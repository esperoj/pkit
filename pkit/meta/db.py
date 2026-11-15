"""Record storage: one class over one sqlite connection.

MetaDB owns connect + migrate + commit/rollback + close, so no caller threads
a raw connection through its code. It is a context manager:

    with MetaDB(path) as db:
        db.put(Text(id="t1", title="..."))
        record = db.get("t1")

Input rule: lenient at the edge (Record or mapping in, str | Path | None for
paths), strict inside, one output type. Here `str` always means a record id —
never a JSON payload.

Migrations are checked read-only on connect and applied only when pending,
so an up-to-date database takes no write lock (gnu parallel can hammer
`pkit archive verify`). readonly=True opens the file read-only and never
migrates; migrate() is public for `pkit meta migrate`.
"""

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Self
from urllib.parse import quote

from pkit.meta import config as meta_config
from pkit.meta.models import RECORD_TYPES, Record, new_id, stored_models, type_name
from pkit.meta.serialize import from_dict, from_json

__all__ = ["MetaDB"]

CONNECT_TIMEOUT = 30.0
BUSY_TIMEOUT_MS = 30000

_SORTS: dict[str, str] = {
    "id": "id ASC",
    "-id": "id DESC",
    "created": "created_at ASC, id ASC",
    "-created": "created_at DESC, id DESC",
    "updated": "updated_at ASC, id ASC",
    "-updated": "updated_at DESC, id DESC",
}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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


def _placeholders(items: list[Any]) -> str:
    return ", ".join("?" * len(items))


def _condition(cls: type[Record], name: str, items: list[Any]) -> str:
    """SQL condition matching one field against materialized items."""
    table = _q(cls.table)
    kind = _field_kind(cls, name)
    if kind == "scalar":
        column = _q(name)
        return f"{column} = ?" if len(items) == 1 else f"{column} IN ({_placeholders(items)})"
    if kind == "array":
        column = _q(name)
        return (
            f"EXISTS (SELECT 1 FROM json_each({table}.{column}) AS _e "
            f"WHERE _e.value IN ({_placeholders(items)}))"
        )
    link = next(link for link in cls.links if link.key == name)
    return (
        f"EXISTS (SELECT 1 FROM {_q(link.table)} "
        f"WHERE {_q(link.parent_column)} = {table}.id "
        f"AND {_q(link.child_column)} IN ({_placeholders(items)}))"
    )


def _flatten(value: Any) -> list[Any]:
    """Materialized filter items for one where value.

    A single value stays a one-item list; a collection keeps its order minus
    duplicates. None and mappings are rejected: they are never filter values.
    """
    if value is None or isinstance(value, Mapping):
        raise ValueError(f"not a filter value: {value!r}")
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return [value]
    return list(dict.fromkeys(value))


def _probe_items(value: Any) -> list[Any] | None:
    """Materialized probe items, or None when the value is empty (skip)."""
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        return [value] if value else None
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        items = list(dict.fromkeys(value))
        return items or None
    if not isinstance(value, Mapping):
        return [value]
    raise ValueError(f"not a filter value: {value!r}")


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
    table = _q(cls.table)
    parts = ["'id'", "id", "'type'", f"'{type_name(cls)}'", "'slug'", "slug"]
    parts += [x for n in cls.scalar_fields for x in (f"'{n}'", _q(n))]
    parts += [x for n in cls.json_fields for x in (f"'{n}'", f"json({_q(n)})")]
    for link in cls.links:
        sub = (
            f"SELECT json_group_array({_q(link.child_column)}) FROM {_q(link.table)} "
            f"WHERE {_q(link.parent_column)} = {table}.id"
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


class MetaDB:
    """The record database: sqlite tables behind typed record methods.

    Context-manager contract: commit on success, roll back on error, always
    close. `commit()` stays public for callers that need intermediate
    durability inside one long session (archive.add commits per file).
    Foreign keys are OFF so import order is free.

    readonly=True opens an existing database read-only (mode=ro, no DDL, no
    migrations). A missing or empty file still creates the schema once, so
    dump/validate behave sensibly on a fresh machine. auto_migrate=False
    skips the automatic check for callers that run migrate() themselves.
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
        target = Path(path if path is not None else cfg.db_path).expanduser()
        self._migrations_dir = Path(
            migrations_dir if migrations_dir is not None else cfg.migrations_dir
        ).expanduser()
        self.readonly = readonly and target.is_file() and target.stat().st_size > 0
        if self.readonly:
            self.conn = sqlite3.connect(
                f"file:{quote(str(target))}?mode=ro", uri=True, timeout=CONNECT_TIMEOUT
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(target), timeout=CONNECT_TIMEOUT)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        if not self.readonly and auto_migrate and self._pending_migrations(self._migrations_dir):
            self.migrate(self._migrations_dir)

    def _pending_migrations(self, directory: Path) -> list[Path]:
        """Unapplied .sql files, checked without taking any write lock."""
        if not directory.is_dir():
            return []
        files = sorted(directory.glob("*.sql"))
        if not files:
            return []
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = '_migrations'"
        ).fetchone()
        if row is None:
            return files
        applied = {row[0] for row in self.conn.execute("SELECT name FROM _migrations")}
        return [file for file in files if file.name not in applied]

    def migrate(self, migrations_dir: str | Path | None = None) -> list[str]:
        """Apply pending migrations, returning the names applied ([] when current).

        WAL journaling is enabled best-effort (read-only or locked databases
        keep their current mode). Each file runs inside BEGIN IMMEDIATE with
        its _migrations bookkeeping; the primary key re-checks the applied
        set inside the transaction, so concurrent migrators are safe — the
        loser's re-run rolls back whole on the duplicate insert.
        Migration files must not contain BEGIN/COMMIT and must end statements
        with ';'.
        """
        directory = Path(
            migrations_dir if migrations_dir is not None else self._migrations_dir
        ).expanduser()
        if not directory.is_dir():
            return []
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "name TEXT PRIMARY KEY CHECK (name <> ''), "
            "applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
        )
        applied = {row[0] for row in self.conn.execute("SELECT name FROM _migrations")}
        newly: list[str] = []
        for file_path in sorted(directory.glob("*.sql")):
            if file_path.name in applied:
                continue
            # Schema change and bookkeeping row commit together: no crash
            # window where a migration is applied but unrecorded.
            name = file_path.name.replace("'", "''")
            try:
                self.conn.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + file_path.read_text(encoding="utf-8")
                    + f"\nINSERT INTO _migrations(name) VALUES ('{name}');\nCOMMIT;"
                )
            except sqlite3.IntegrityError:
                # A concurrent migrator recorded it while we waited on the
                # write lock; our whole re-run rolled back with the insert.
                self.conn.rollback()
                continue
            applied.add(file_path.name)
            newly.append(file_path.name)
        return newly

    def close(self) -> None:
        """Close the connection."""
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()

    def commit(self) -> None:
        """Persist writes so far; the context exit commits the remainder."""
        self.conn.commit()

    def put(self, record: Record | Mapping[str, Any]) -> Record:
        """Insert or update by id, then re-sync link rows.

        Accepts a Record or a mapping (parsed via from_dict); always returns Record.
        """
        record = _prepare(record)
        cls = type(record)
        cols = ["id", "slug", *cls.scalar_fields, *cls.json_fields]
        self.conn.execute(
            f"INSERT INTO {_q(cls.table)} ({', '.join(map(_q, cols))}) "
            f"VALUES ({', '.join('?' * len(cols))}) ON CONFLICT(id) DO UPDATE SET "
            + ", ".join(f"{_q(c)} = excluded.{_q(c)}" for c in cols if c != "id"),
            _values(record, cls),
        )
        for link in cls.links:
            self.conn.execute(
                f"DELETE FROM {_q(link.table)} WHERE {_q(link.parent_column)} = ?",
                (record.id,),
            )
            self.conn.executemany(
                f"INSERT OR IGNORE INTO {_q(link.table)} "
                f"({_q(link.parent_column)}, {_q(link.child_column)}) VALUES (?, ?)",
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
            f"SELECT {_payload_sql(cls)} AS payload FROM {_q(cls.table)} WHERE id = ?",
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
            items = _probe_items(values[name])
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
                    f"SELECT {expression} FROM {_q(model.table)} "
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
        sort: str = "-updated",
    ) -> list[Record]:
        """All records of a type (or all types), newest first by default.

        record_type accepts a type name ("text") or the class (Text). where
        ANDs field filters: scalar fields compare by equality (a collection
        becomes IN), array fields (identifiers, genres, ...) match by
        membership, and link fields (author_ids) match through the link
        table; an empty collection matches nothing. With record_type None,
        only stored models carrying every where key are searched. sort picks
        the order: "id", "-id", "created", "-created", "updated" or
        "-updated" (default); timestamp sorts tie-break on id. Unknown
        types, sorts and where fields raise ValueError listing known values.
        """
        if sort not in _SORTS:
            known = ", ".join(_SORTS)
            raise ValueError(f"unknown sort {sort!r}; known sorts: {known}")
        cls = _resolve_type(record_type)
        models = (cls,) if cls is not None else stored_models()
        models = tuple(model for model in models if model.table)
        if not models:
            return []
        filters: dict[str, list[Any]] = {}
        if where:
            for name in where:
                if cls is not None:
                    if _field_kind(cls, name) is None:
                        raise ValueError(f"not a queryable field on {type_name(cls)}: {name!r}")
                elif not any(_field_kind(model, name) is not None for model in models):
                    raise ValueError(f"no stored record type has a queryable field {name!r}")
            for name, value in where.items():
                items = _flatten(value)
                if not items:
                    return []
                filters[name] = items
            if cls is None:
                models = tuple(
                    model
                    for model in models
                    if all(_field_kind(model, name) is not None for name in filters)
                )
                if not models:
                    return []
        selects: list[str] = []
        params: list[Any] = []
        for model in models:
            table = _q(model.table)
            clauses = [_condition(model, name, items) for name, items in filters.items()]
            where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            payload = _payload_sql(model)
            selects.append(
                f"SELECT {payload} AS payload, id, created_at, updated_at FROM {table}{where_sql}"
            )
            params.extend(item for items in filters.values() for item in items)
        union = " UNION ALL ".join(selects)
        sql = f"SELECT payload FROM ({union}) ORDER BY {_SORTS[sort]}"
        if limit is not None or offset:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit if limit is not None else -1, offset])
        return [from_json(row["payload"]) for row in self.conn.execute(sql, params)]

    def delete(self, record_id: str) -> bool:
        """Hard-delete, plus every link row referencing the id."""
        found = False
        for cls in stored_models():
            cur = self.conn.execute(f"DELETE FROM {_q(cls.table)} WHERE id = ?", (record_id,))
            found |= bool(cur.rowcount)
            for link in cls.links:
                self.conn.execute(
                    f"DELETE FROM {_q(link.table)} "
                    f"WHERE {_q(link.parent_column)} = ? OR {_q(link.child_column)} = ?",
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
                        f"SELECT {_q(col)} FROM {_q(link.table)} "
                        f"WHERE NOT EXISTS (SELECT 1 FROM {_q(ref)} WHERE id = {_q(link.table)}.{_q(col)})"
                    )
                    issues.extend(f"{link.table}.{col} {row[0]!r} does not exist" for row in rows)
        seen: dict[str, str] = {}
        for cls in models:
            for row in self.conn.execute(f"SELECT id FROM {_q(cls.table)}"):
                rid = row["id"]
                if rid in seen:
                    issues.append(f"duplicate record id {rid!r} in {seen[rid]!r} and {cls.table!r}")
                else:
                    seen[rid] = cls.table
        for cls in models:
            table = _q(cls.table)
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
