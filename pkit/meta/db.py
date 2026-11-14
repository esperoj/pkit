"""Record storage: one class over one sqlite connection.

MetaDB owns connect + migrate + commit/rollback + close, so no caller threads
a raw connection through its code. It is a context manager:

    with MetaDB(path) as db:
        db.put(Book(id="b1", title="..."))
        record = db.get("b1")

Input rule: lenient at the edge (Record or mapping in, str | Path | None for
paths), strict inside, one output type. Here `str` always means a record id —
never a JSON payload.
"""

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Self

from pkit.meta import config as meta_config
from pkit.meta.models import RECORD_TYPES, Record, new_id, stored_models, type_name
from pkit.meta.serialize import from_dict, from_json

__all__ = ["MetaDB"]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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


def _migrate(conn: sqlite3.Connection, directory: str | Path) -> None:
    """Apply each .sql once, atomically with its _migrations row.

    Files must not contain BEGIN/COMMIT and must end statements with ';'.
    """
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        return
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "name TEXT PRIMARY KEY CHECK (name <> ''), "
        "applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
    )
    conn.commit()
    applied = {row[0] for row in conn.execute("SELECT name FROM _migrations")}
    for file_path in sorted(directory.glob("*.sql")):
        if file_path.name in applied:
            continue
        # Schema change and bookkeeping row commit together: no crash window
        # where a migration is applied but unrecorded.
        name = file_path.name.replace("'", "''")
        conn.executescript(
            "BEGIN;\n"
            + file_path.read_text(encoding="utf-8")
            + f"\nINSERT INTO _migrations(name) VALUES ('{name}');\nCOMMIT;"
        )


class MetaDB:
    """The record database: sqlite tables behind typed record methods.

    Context-manager contract: commit on success, roll back on error, always
    close. `commit()` stays public for callers that need intermediate
    durability inside one long session (archive.add commits per file).
    Foreign keys are OFF so import order is free.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        migrations_dir: str | Path | None = None,
    ) -> None:
        cfg = meta_config.load()
        target = Path(path if path is not None else cfg.db_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(target))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = OFF")
        _migrate(self.conn, migrations_dir if migrations_dir is not None else cfg.migrations_dir)

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

    def get(self, record_id: str) -> Record | None:
        """Fetch one record by id across all known types."""
        for cls in stored_models():
            row = self.conn.execute(
                f"SELECT {_payload_sql(cls)} AS payload FROM {_q(cls.table)} WHERE id = ?",
                (record_id,),
            ).fetchone()
            if row is not None:
                return from_json(row["payload"])
        return None

    def find(
        self,
        record_type: str | type[Record] | None = None,
        *,
        identifier: str | Iterable[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Record]:
        """All records of a type (or all types), newest first.

        record_type accepts a type name ("note") or the class (Note).
        identifier filters to records matching a token (or any token in an
        iterable). Unknown types raise ValueError listing the known types.
        """
        if isinstance(record_type, type):
            if not issubclass(record_type, Record):
                raise TypeError(f"not a Record class: {record_type!r}")
            record_type = type_name(record_type)
        if record_type is not None and record_type not in RECORD_TYPES:
            known = ", ".join(sorted(RECORD_TYPES)) or "(none)"
            raise ValueError(f"unknown record type {record_type!r}; known types: {known}")
        models = (RECORD_TYPES[record_type],) if record_type is not None else stored_models()
        models = tuple(cls for cls in models if cls.table)
        if not models:
            return []
        per_select: tuple[str, ...] = ()
        if identifier is not None:
            raw: Iterable[str] = (identifier,) if isinstance(identifier, str) else identifier
            per_select = tuple(dict.fromkeys(tok for tok in raw if tok))
            if not per_select:
                return []
        placeholders = ", ".join("?" * len(per_select)) if per_select else ""
        selects: list[str] = []
        for cls in models:
            table = _q(cls.table)
            where = (
                f" WHERE EXISTS (SELECT 1 FROM json_each({table}.identifiers) AS _id "
                f"WHERE _id.value IN ({placeholders}))"
                if placeholders
                else ""
            )
            payload = _payload_sql(cls)
            selects.append(f"SELECT {payload} AS payload, updated_at FROM {table}{where}")
        union = " UNION ALL ".join(selects)
        sql = f"SELECT payload FROM ({union}) ORDER BY updated_at DESC"
        params: list[Any] = list(per_select) * len(models)
        if limit is not None or offset:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit if limit is not None else -1, offset])
        return [from_json(row["payload"]) for row in self.conn.execute(sql, params)]

    def exists(self, record_id: str) -> bool:
        """Cheap presence check: no payload build, no JSON parse."""
        for cls in stored_models():
            row = self.conn.execute(
                f"SELECT 1 FROM {_q(cls.table)} WHERE id = ? LIMIT 1", (record_id,)
            ).fetchone()
            if row is not None:
                return True
        return False

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
        """Orphan link parents/children, duplicate ids, and duplicate identifiers."""
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
        return issues
