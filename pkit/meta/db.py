"""Record storage: one class over one sqlite connection.

MetaDB layers typed record methods on pkit.lib.sqlite.Database, which owns
connect + migrate + commit/rollback + close, so no caller threads a raw
connection through its code. It is a context manager:

    with MetaDB(path) as db:
        db.put(Text(id="t1", title="..."))
        record = db.get("t1")

Input rule: lenient at the edge (Record or mapping in, str | Path | None for
paths), strict inside, one output type. Here `str` always means a record id —
never a JSON payload. SQL fragments come from pkit.lib.sql, so quoting,
placeholders and sorts behave like every other pkit database.
"""

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from pkit.lib import sql
from pkit.lib.sqlite import Database
from pkit.meta import config as meta_config
from pkit.meta.models import RECORD_TYPES, Record, new_id, stored_models, type_name
from pkit.meta.serialize import from_dict, from_json

__all__ = ["TIMESTAMP_COLUMNS", "MetaDB"]

TIMESTAMP_COLUMNS = ("created_at", "updated_at")


def _sortable(cls: type[Record]) -> tuple[str, ...]:
    """Every column find() may sort by: id, slug, timestamps, scalar fields."""
    return ("id", "slug", *TIMESTAMP_COLUMNS, *cls.scalar_fields)


def _resolve_type(record_type: str | type[Record] | None) -> type[Record] | None:
    """A type name or Record class to its registered class, validated."""
    if isinstance(record_type, type):
        if not issubclass(record_type, Record):
            raise TypeError(f"not a Record class: {record_type!r}")
        record_type = type_name(record_type)
    if record_type is None:
        return None
    if record_type not in RECORD_TYPES:
        known = ", ".join(sorted(RECORD_TYPES)) or "(none)"
        raise ValueError(f"unknown record type {record_type!r}; known types: {known}")
    return RECORD_TYPES[record_type]


def _check_model(cls: type[Record], record_type: str | type[Record] | None) -> None:
    """Raise when an explicit record_type disagrees with a resolved model."""
    hint = _resolve_type(record_type)
    if hint is not None and hint is not cls:
        raise ValueError(f"record type {type_name(hint)!r} does not match {type_name(cls)!r}")


def _field_kind(cls: type[Record], name: str) -> str | None:
    """How one field is queried: "scalar", "array" (JSON list), "link", or None.

    id and slug are plain columns; dict JSON fields (extra) and unknown
    names are not queryable.
    """
    if name in ("id", "slug") or name in cls.scalar_fields:
        return "scalar"
    if name in cls.array_fields:
        return "array"
    if name in {link.key for link in cls.links}:
        return "link"
    return None


def _condition(cls: type[Record], name: str, items: list[Any]) -> str:
    """SQL condition matching one field against materialized items."""
    kind = _field_kind(cls, name)
    if kind == "scalar":
        return sql.equals(name, items)
    if kind == "array":
        return sql.json_contains(cls.table, name, items)
    link = next(link for link in cls.links if link.key == name)
    return (
        f"EXISTS (SELECT 1 FROM {sql.quote(link.table)} "
        f"WHERE {sql.quote(link.parent_column)} = {sql.quote(cls.table)}.id "
        f"AND {sql.quote(link.child_column)} IN ({sql.placeholders(items)}))"
    )


def _items(value: Any) -> list[Any] | None:
    """Materialized filter values, or None when the value is absent or empty.

    A single value stays a one-item list; a collection keeps its order minus
    duplicates. None means absent (probes skip it, find rejects it); a
    mapping is never a filter value and always raises.
    """
    if isinstance(value, Mapping):
        # a mapping is a wrong filter value shape, not a wrong argument type
        raise ValueError(f"not a filter value: {value!r}")  # noqa: TRY004 - tested contract
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        return [value] if value else None
    if isinstance(value, Iterable):
        return list(dict.fromkeys(value)) or None
    return [value]


def _by_fields(by: str | Iterable[str] | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if by is None:
        return default
    if isinstance(by, str):
        return (by,)
    return tuple(by)


def _probe_model(
    data: Mapping[str, Any], record_type: str | type[Record] | None
) -> type[Record] | None:
    """The model named by a mapping probe's "type", or the record_type hint."""
    name = data.get("type")
    if name is None:
        return _resolve_type(record_type)
    if not isinstance(name, str) or name not in RECORD_TYPES:
        known = ", ".join(sorted(RECORD_TYPES)) or "(none)"
        raise ValueError(f"unknown record type {name!r}; known types: {known}")
    cls = RECORD_TYPES[name]
    _check_model(cls, record_type)
    return cls


def _prepare(record: Record | Mapping[str, Any]) -> Record:
    """Coerce a mapping or Record and ensure non-empty id and slug."""
    parsed = record if isinstance(record, Record) else from_dict(dict(record))
    if not parsed.id:
        parsed = replace(parsed, id=new_id())
    if not parsed.slug:
        parsed = replace(parsed, slug=parsed.id)
    return parsed


def _payload_sql(cls: type[Record]) -> str:
    """SQL expression returning one row as a JSON payload, links included."""
    table = sql.quote(cls.table)
    parts = ["'id'", "id", "'type'", f"'{type_name(cls)}'", "'slug'", "slug"]
    parts += [x for n in cls.scalar_fields for x in (f"'{n}'", sql.quote(n))]
    parts += [x for n in cls.json_fields for x in (f"'{n}'", f"json({sql.quote(n)})")]
    for link in cls.links:
        sub = (
            f"SELECT json_group_array({sql.quote(link.child_column)}) FROM {sql.quote(link.table)} "
            f"WHERE {sql.quote(link.parent_column)} = {table}.id"
        )
        parts += [f"'{link.key}'", f"json(COALESCE(({sub}), '[]'))"]
    return "json_object(" + ", ".join(parts) + ")"


def _values(record: Record, cls: type[Record]) -> list[Any]:
    values: list[Any] = [record.id, record.slug]
    for name in cls.scalar_fields:
        v = getattr(record, name)
        values.append("" if v is None else v)
    for name in cls.json_fields:
        v = getattr(record, name)
        if v is None:
            v = {} if name == "extra" else []
        values.append(json.dumps(v, ensure_ascii=False, default=str))
    return values


class MetaDB(Database):
    """The record database: sqlite tables behind typed record methods.

    The database path and migrations dir default to [meta] db_path /
    migrations_dir (and their $PKIT_META_* overrides); everything else —
    connect, migrations, commit/rollback, close — is the shared
    pkit.lib.sqlite.Database contract.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        migrations_dir: str | Path | None = None,
        readonly: bool = False,
        auto_migrate: bool = True,
    ) -> None:
        cfg = meta_config.load()
        super().__init__(
            path if path is not None else cfg.db_path,
            migrations_dir=migrations_dir if migrations_dir is not None else cfg.migrations_dir,
            readonly=readonly,
            auto_migrate=auto_migrate,
        )

    def put(self, record: Record | Mapping[str, Any]) -> Record:
        """Insert or update by id, then re-sync link rows.

        Accepts a Record or a mapping (parsed via from_dict); always returns Record.
        """
        record = _prepare(record)
        cls = type(record)
        cols = ["id", "slug", *cls.scalar_fields, *cls.json_fields]
        self.conn.execute(sql.upsert(cls.table, cols), _values(record, cls))
        for link in cls.links:
            self.conn.execute(
                f"DELETE FROM {sql.quote(link.table)} WHERE {sql.quote(link.parent_column)} = ?",
                (record.id,),
            )
            self.conn.executemany(
                f"INSERT OR IGNORE INTO {sql.quote(link.table)} "
                f"({sql.quote(link.parent_column)}, {sql.quote(link.child_column)}) VALUES (?, ?)",
                [
                    (record.id, c)
                    for c in (getattr(record, link.key) or [])
                    if isinstance(c, str) and c
                ],
            )
        return record

    def get_or_create(self, record: Record | Mapping[str, Any]) -> Record:
        """Return the stored record if its id exists in this type's table, else insert.

        One query; own-table semantics preserved (a cross-type id collision still
        inserts, and validate() will flag it).
        """
        record = _prepare(record)
        cls = type(record)
        row = self.conn.execute(
            f"SELECT {_payload_sql(cls)} AS payload FROM {sql.quote(cls.table)} WHERE id = ?",
            (record.id,),
        ).fetchone()
        return from_json(row["payload"]) if row is not None else self.put(record)

    def _lookup(
        self,
        target: str | Record | Mapping[str, Any],
        *,
        record_type: str | type[Record] | None = None,
        by: str | Iterable[str] | None = None,
        payload: bool = False,
    ) -> sqlite3.Row | None:
        """First row matching any non-empty probe field, or None.

        One LIMIT 1 query per field (and per candidate model when no model is
        pinned), so indexed columns and json_each scans never share one OR
        clause. Empty values are skipped; the first hit wins.
        """
        if isinstance(target, Record):
            cls = type(target)
            _check_model(cls, record_type)
            fields = _by_fields(by, cls.unique)
            values: dict[str, Any] = {name: getattr(target, name) for name in fields}
        elif isinstance(target, Mapping):
            data = dict(target)
            cls = _probe_model(data, record_type)
            # typed probes match on the model's unique fields; untyped ones
            # probe their own keys against the hinted (or all stored) models
            if cls is not None and "type" in data:
                default_fields = cls.unique
            else:
                default_fields = tuple(data.keys())
            fields = _by_fields(by, default_fields)
            values = {name: data.get(name) for name in fields}
        else:
            if not target:
                return None
            cls = _resolve_type(record_type)
            fields = _by_fields(by, ("id",))
            values = {name: target for name in fields}

        if cls is not None:
            for name in fields:
                if _field_kind(cls, name) is None:
                    raise ValueError(f"not a queryable field on {type_name(cls)}: {name!r}")
        else:
            for name in fields:
                if not any(_field_kind(model, name) is not None for model in stored_models()):
                    raise ValueError(f"no stored record type has a queryable field {name!r}")

        for name in fields:
            items = _items(values[name])
            if items is None:
                continue
            models = (
                (cls,)
                if cls is not None
                else tuple(
                    model for model in stored_models() if _field_kind(model, name) is not None
                )
            )
            for model in models:
                expression = f"{_payload_sql(model)} AS payload" if payload else "1"
                row = self.conn.execute(
                    f"SELECT {expression} FROM {sql.quote(model.table)} "
                    f"WHERE {_condition(model, name, items)} LIMIT 1",
                    items,
                ).fetchone()
                if row is not None:
                    return row
        return None

    def exists(
        self,
        target: str | Record | Mapping[str, Any],
        *,
        record_type: str | type[Record] | None = None,
        by: str | Iterable[str] | None = None,
    ) -> bool:
        """Cheap presence check: no payload build, no JSON parse.

        A str is probed by id unless by names other fields; Record and
        mapping probes use their model's unique fields, skipping empty ones.
        """
        return self._lookup(target, record_type=record_type, by=by) is not None

    def get(
        self,
        target: str | Record | Mapping[str, Any],
        *,
        record_type: str | type[Record] | None = None,
        by: str | Iterable[str] | None = None,
    ) -> Record | None:
        """Fetch one record by probe, or None.

        Matching follows exists(): a str is probed by id unless by names
        other fields, Record and mapping probes use their model's unique
        fields (empty values skipped). Probing a mapping with "id": ""
        instead of a random UUID is the safe way to match on the other
        fields only.
        """
        row = self._lookup(target, record_type=record_type, by=by, payload=True)
        return from_json(row["payload"]) if row is not None else None

    def find(
        self,
        record_type: str | type[Record] | None = None,
        *,
        where: Mapping[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "-updated_at",
    ) -> list[Record]:
        """All records of a type (or all types), newest first by default.

        record_type accepts a type name ("text") or the class (Text). where
        ANDs field filters: scalar fields compare by equality (a collection
        becomes IN), array fields (identifiers, genres, ...) match by
        membership, and link fields (author_ids) match through the link
        table; an empty collection matches nothing. sort names any sortable
        column — "id", "slug", "created_at", "updated_at" or any scalar
        field — with a "-" prefix for descending order (default
        "-updated_at"); every sort but id tie-breaks on id. With
        record_type None, only stored models carrying every where key and
        the sort column are searched. Unknown types, sorts and where
        fields raise ValueError listing known values.
        """
        cls = _resolve_type(record_type)
        if cls is not None:
            column, descending = sql.parse_sort(sort, _sortable(cls))
            models = (cls,) if cls.table else ()
        else:
            every = dict.fromkeys(
                ("id", "slug", *TIMESTAMP_COLUMNS)
                + tuple(field for model in stored_models() for field in model.scalar_fields)
            )
            column, descending = sql.parse_sort(sort, tuple(every))
            models = tuple(model for model in stored_models() if column in _sortable(model))
        if not models:
            return []
        order_columns = ("id",) if column == "id" else (column, "id")
        direction = "DESC" if descending else "ASC"
        filters: dict[str, list[Any]] = {}
        if where:
            for key in where:
                if cls is not None:
                    if _field_kind(cls, key) is None:
                        raise ValueError(f"not a queryable field on {type_name(cls)}: {key!r}")
                elif not any(_field_kind(model, key) is not None for model in models):
                    raise ValueError(f"no stored record type has a queryable field {key!r}")
            for key, value in where.items():
                if value is None:
                    raise ValueError(f"not a filter value: {value!r}")
                items = _items(value)
                if not items:
                    return []
                filters[key] = items
            if cls is None:
                models = tuple(
                    model
                    for model in models
                    if all(_field_kind(model, key) is not None for key in filters)
                )
                if not models:
                    return []
        selects: list[str] = []
        params: list[Any] = []
        for model in models:
            table = sql.quote(model.table)
            clauses = [_condition(model, key, items) for key, items in filters.items()]
            where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            payload = _payload_sql(model)
            selects.append(
                f"SELECT {payload} AS payload, "
                f"{', '.join(sql.quote(c) for c in order_columns)} FROM {table}{where_sql}"
            )
            params.extend(item for items in filters.values() for item in items)
        union = " UNION ALL ".join(selects)
        order_sql = ", ".join(f"{sql.quote(c)} {direction}" for c in order_columns)
        sql_text = f"SELECT payload FROM ({union}) ORDER BY {order_sql}"
        if limit is not None or offset:
            sql_text += " LIMIT ? OFFSET ?"
            params.extend([limit if limit is not None else -1, offset])
        return [from_json(row["payload"]) for row in self.conn.execute(sql_text, params)]

    def delete(self, record_id: str) -> bool:
        """Hard-delete, plus every link row referencing the id."""
        found = False
        for cls in stored_models():
            cur = self.conn.execute(
                f"DELETE FROM {sql.quote(cls.table)} WHERE id = ?", (record_id,)
            )
            found |= bool(cur.rowcount)
            for link in cls.links:
                self.conn.execute(
                    f"DELETE FROM {sql.quote(link.table)} "
                    f"WHERE {sql.quote(link.parent_column)} = ? OR {sql.quote(link.child_column)} = ?",
                    (record_id, record_id),
                )
        return found

    def validate(self) -> list[str]:
        """Orphan links, duplicate ids, duplicate identifiers, duplicate sha256s."""
        issues: list[str] = []
        models = stored_models()
        for cls in models:
            for link in cls.links:
                for col, ref in (
                    (link.parent_column, cls.table),
                    (link.child_column, link.child_table),
                ):
                    rows = self.conn.execute(
                        f"SELECT {sql.quote(col)} FROM {sql.quote(link.table)} "
                        f"WHERE NOT EXISTS (SELECT 1 FROM {sql.quote(ref)} "
                        f"WHERE id = {sql.quote(link.table)}.{sql.quote(col)})"
                    )
                    issues.extend(f"{link.table}.{col} {row[0]!r} does not exist" for row in rows)
        seen: dict[str, str] = {}
        for cls in models:
            for row in self.conn.execute(f"SELECT id FROM {sql.quote(cls.table)}"):
                rid = row["id"]
                if rid in seen:
                    issues.append(f"duplicate record id {rid!r} in {seen[rid]!r} and {cls.table!r}")
                else:
                    seen[rid] = cls.table
        for cls in models:
            table = sql.quote(cls.table)
            rows = self.conn.execute(
                f"SELECT _id.value FROM {table}, json_each({table}.identifiers) AS _id "
                "WHERE _id.value <> '' "
                f"GROUP BY _id.value HAVING count(DISTINCT {table}.id) > 1"
            )
            issues.extend(f"duplicate identifier {row[0]!r} in {cls.table!r}" for row in rows)
        rows = self.conn.execute(
            "SELECT sha256 FROM files WHERE sha256 <> '' GROUP BY sha256 HAVING count(*) > 1"
        )
        issues.extend(f"duplicate sha256 {row[0]!r} in 'files'" for row in rows)
        return issues
