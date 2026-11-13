"""Record storage.

Every function takes a connection first, so this module can later be split into
sql / db / read / write / check without changing a single call site.

Input rule: lenient at the edge (Record or mapping in, str | Path | None for
paths), strict inside, one output type. In this module `str` always means a
record id — never a JSON payload.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from pkit.meta import config as meta_config
from pkit.meta.models import RECORD_TYPES, Record, new_id, stored_models
from pkit.meta.serialize import from_dict, from_json

__all__ = [
    "connect",
    "delete",
    "exists",
    "find",
    "get",
    "get_or_create",
    "migrate",
    "put",
    "session",
    "validate",
]


# --- sql helpers (future: pkit/meta/sql.py) ----------------------------------


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _models() -> tuple[type[Record], ...]:
    return stored_models()


def _coerce(record: Record | Mapping[str, Any]) -> Record:
    """One parse path for lenient input: mappings go through from_dict."""
    return record if isinstance(record, Record) else from_dict(dict(record))


def _normalize(record: Record) -> Record:
    record = record if record.id else replace(record, id=new_id())
    return record if record.slug else replace(record, slug=record.id)


def _payload_sql(cls: type[Record]) -> str:
    """SQL expression returning one row as a JSON payload, links included."""
    table = _q(cls.table)
    parts = ["'id'", "id", "'type'", f"'{cls.type}'", "'slug'", "slug"]
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


# --- connection (future: pkit/meta/db.py) ------------------------------------


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Raw connection; the caller owns it. Foreign keys OFF so import order is free."""
    path = Path(path if path is not None else meta_config.load().db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


@contextmanager
def session(
    path: str | Path | None = None, *, migrations_dir: str | Path | None = None
) -> Generator[sqlite3.Connection]:
    """The one entry point: connect, migrate, commit (or roll back), close."""
    conn = connect(path)
    try:
        migrate(conn, migrations_dir)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection, directory: str | Path | None = None) -> None:
    """Apply each .sql once, atomically with its _migrations row.

    Files must not contain BEGIN/COMMIT and must end statements with ';'.
    """
    directory = Path(
        directory if directory is not None else meta_config.load().migrations_dir
    ).expanduser()
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


# --- write (future: pkit/meta/write.py) --------------------------------------


def put(conn: sqlite3.Connection, record: Record | Mapping[str, Any]) -> Record:
    """Insert or update by id, then re-sync link rows.

    Accepts a Record or a mapping (parsed via from_dict); always returns Record.
    """
    record = _normalize(_coerce(record))
    cls = type(record)
    cols = ["id", "slug", *cls.scalar_fields, *cls.json_fields]
    conn.execute(
        f"INSERT INTO {_q(cls.table)} ({', '.join(map(_q, cols))}) "
        f"VALUES ({', '.join('?' * len(cols))}) ON CONFLICT(id) DO UPDATE SET "
        + ", ".join(f"{_q(c)} = excluded.{_q(c)}" for c in cols if c != "id"),
        _values(record, cls),
    )
    for link in cls.links:
        conn.execute(
            f"DELETE FROM {_q(link.table)} WHERE {_q(link.parent_column)} = ?",
            (record.id,),
        )
        conn.executemany(
            f"INSERT OR IGNORE INTO {_q(link.table)} "
            f"({_q(link.parent_column)}, {_q(link.child_column)}) VALUES (?, ?)",
            [(record.id, c) for c in (getattr(record, link.key) or []) if isinstance(c, str) and c],
        )
    return record


def get_or_create(conn: sqlite3.Connection, record: Record | Mapping[str, Any]) -> Record:
    """Return the stored record if its id exists in this type's table, else insert.

    One query; own-table semantics preserved (a cross-type id collision still
    inserts, and validate() will flag it).
    """
    record = _normalize(_coerce(record))
    cls = type(record)
    row = conn.execute(
        f"SELECT {_payload_sql(cls)} AS payload FROM {_q(cls.table)} WHERE id = ?",
        (record.id,),
    ).fetchone()
    return from_json(row["payload"]) if row is not None else put(conn, record)


# --- read (future: pkit/meta/read.py) ----------------------------------------


def get(conn: sqlite3.Connection, record_id: str) -> Record | None:
    """Fetch one record by id across all known types."""
    for cls in _models():
        row = conn.execute(
            f"SELECT {_payload_sql(cls)} AS payload FROM {_q(cls.table)} WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is not None:
            return from_json(row["payload"])
    return None


def find(
    conn: sqlite3.Connection,
    record_type: str | type[Record] | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Record]:
    """All records of a type (or all types), newest first.

    record_type accepts a type name ("note") or the class (Note).
    Unknown types raise ValueError listing the known types.
    """
    if isinstance(record_type, type):
        if not issubclass(record_type, Record):
            raise TypeError(f"not a Record class: {record_type!r}")
        record_type = record_type.type
    if record_type is not None and record_type not in RECORD_TYPES:
        known = ", ".join(sorted(RECORD_TYPES)) or "(none)"
        raise ValueError(f"unknown record type {record_type!r}; known types: {known}")
    models = (RECORD_TYPES[record_type],) if record_type is not None else _models()
    models = tuple(cls for cls in models if cls.table)
    if not models:
        return []
    union = " UNION ALL ".join(
        f"SELECT {_payload_sql(cls)} AS payload, updated_at FROM {_q(cls.table)}" for cls in models
    )
    sql = f"SELECT payload FROM ({union}) ORDER BY updated_at DESC"
    params: list[Any] = []
    if limit is not None or offset:
        sql += " LIMIT ? OFFSET ?"
        params = [limit if limit is not None else -1, offset]
    return [from_json(row["payload"]) for row in conn.execute(sql, params)]


def exists(conn: sqlite3.Connection, record_id: str) -> bool:
    """Cheap presence check: no payload build, no JSON parse."""
    for cls in _models():
        row = conn.execute(
            f"SELECT 1 FROM {_q(cls.table)} WHERE id = ? LIMIT 1", (record_id,)
        ).fetchone()
        if row is not None:
            return True
    return False


# --- delete / check (future: pkit/meta/check.py) -----------------------------


def delete(conn: sqlite3.Connection, record_id: str) -> bool:
    """Hard-delete, plus every link row referencing the id."""
    found = False
    for cls in _models():
        cur = conn.execute(f"DELETE FROM {_q(cls.table)} WHERE id = ?", (record_id,))
        found |= bool(cur.rowcount)
        for link in cls.links:
            conn.execute(
                f"DELETE FROM {_q(link.table)} "
                f"WHERE {_q(link.parent_column)} = ? OR {_q(link.child_column)} = ?",
                (record_id, record_id),
            )
    return found


def validate(conn: sqlite3.Connection) -> list[str]:
    """Orphan link parents/children, and globally duplicate record ids."""
    issues: list[str] = []

    def orphans(table: str, column: str, ref: str) -> list[str]:
        return [
            row[0]
            for row in conn.execute(
                f"SELECT {_q(column)} FROM {_q(table)} "
                f"WHERE {_q(column)} NOT IN (SELECT id FROM {_q(ref)})"
            )
        ]

    for cls in _models():
        for link in cls.links:
            for col, ref in (
                (link.parent_column, cls.table),
                (link.child_column, link.child_table),
            ):
                issues += [
                    f"{link.table}.{col} {v!r} does not exist"
                    for v in orphans(link.table, col, ref)
                ]
    seen: dict[str, str] = {}
    for cls in _models():
        for row in conn.execute(f"SELECT id FROM {_q(cls.table)}"):
            rid = row["id"]
            if rid in seen:
                issues.append(f"duplicate record id {rid!r} in {seen[rid]!r} and {cls.table!r}")
            else:
                seen[rid] = cls.table
    return issues
