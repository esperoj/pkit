from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from pkit.meta import config as meta_config
from pkit.meta.models import RECORD_TYPES, Record, new_id
from pkit.meta.serialize import from_json

__all__ = [
    "Records",
    "connect",
    "delete",
    "ensure",
    "exists",
    "find",
    "get",
    "migrate",
    "open",
    "put",
    "session",
    "validate",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _q(name: str) -> str:
    """
    Quote an SQL identifier.
    """
    return '"' + name.replace('"', '""') + '"'


def _models() -> tuple[type[Record], ...]:
    """
    All registered record models that have a real SQL table.
    """
    return tuple(cls for cls in RECORD_TYPES.values() if getattr(cls, "table", ""))


def _normalize(record: Record) -> Record:
    """
    Make sure the record has an id and slug.
    """
    if not record.id:
        record = replace(record, id=new_id())

    if not record.slug:
        record = replace(record, slug=record.id)

    return record


def _payload_sql(cls: type[Record]) -> str:
    """
    Build a SQL expression that returns a JSON payload for one row.
    """
    table = _q(cls.table)

    parts: list[str] = [
        "'id'",
        "id",
        "'type'",
        f"'{cls.type}'",
        "'slug'",
        "slug",
    ]

    for name in cls.scalar_fields:
        parts.append(f"'{name}'")
        parts.append(_q(name))

    for name in cls.json_fields:
        parts.append(f"'{name}'")
        parts.append(f"json({_q(name)})")

    for link in cls.links:
        subquery = (
            f"SELECT json_group_array({_q(link.child_column)}) "
            f"FROM {_q(link.table)} "
            f"WHERE {_q(link.parent_column)} = {table}.id"
        )

        parts.append(f"'{link.key}'")
        parts.append(f"json(COALESCE(({subquery}), '[]'))")

    return "json_object(" + ", ".join(parts) + ")"


# ---------------------------------------------------------------------------
# Connection / session
# ---------------------------------------------------------------------------


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """
    Open a SQLite connection.

    Foreign-key enforcement is OFF by default so books can be imported
    before their linked persons exist.
    """
    if path is None:
        path = meta_config.load().db_path

    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = OFF")

    return conn


@contextmanager
def session(
    path: str | Path | None = None,
    *,
    migrations_dir: str | Path | None = None,
) -> Generator[sqlite3.Connection]:
    """
    Transaction-scoped connection.

    Applies migrations automatically.
    """
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


@contextmanager
def open(
    path: str | Path | None = None,
    *,
    migrations_dir: str | Path | None = None,
) -> Generator[Records]:
    """
    Transaction-scoped Records object.
    """
    with session(path, migrations_dir=migrations_dir) as conn:
        yield Records(conn)


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def migrate(
    conn: sqlite3.Connection,
    directory: str | Path | None = None,
) -> None:
    """
    Apply migration files.

    Migration files should not contain BEGIN/COMMIT.
    """
    if directory is None:
        directory = meta_config.load().migrations_dir

    directory = Path(directory).expanduser()
    if not directory.is_dir():
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY CHECK (name <> ''),
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    conn.commit()

    applied = {row[0] for row in conn.execute("SELECT name FROM _migrations")}

    for file_path in sorted(directory.glob("*.sql")):
        if file_path.name in applied:
            continue

        sql = file_path.read_text(encoding="utf-8")

        conn.executescript("BEGIN;\n" + sql + "\nCOMMIT;")

        conn.execute(
            "INSERT INTO _migrations(name) VALUES (?)",
            (file_path.name,),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------


def put(conn: sqlite3.Connection, record: Record) -> Record:
    """
    Insert or update a record by id.
    """
    record = _normalize(record)
    cls = type(record)

    columns = ["id", "slug", *cls.scalar_fields, *cls.json_fields]
    values: list[Any] = [record.id, record.slug]

    for name in cls.scalar_fields:
        value = getattr(record, name)
        values.append("" if value is None else value)

    for name in cls.json_fields:
        value = getattr(record, name)
        if value is None:
            value = {} if name == "extra" else []
        values.append(
            json.dumps(
                value,
                ensure_ascii=False,
                default=str,
            )
        )

    column_sql = ", ".join(_q(column) for column in columns)
    placeholder_sql = ", ".join("?" for _ in columns)

    update_sql = ", ".join(
        f"{_q(column)} = excluded.{_q(column)}" for column in columns if column != "id"
    )

    conn.execute(
        f"""
        INSERT INTO {_q(cls.table)} ({column_sql})
        VALUES ({placeholder_sql})
        ON CONFLICT(id) DO UPDATE SET
            {update_sql}
        """,
        values,
    )

    for link in cls.links:
        conn.execute(
            f"""
            DELETE FROM {_q(link.table)}
            WHERE {_q(link.parent_column)} = ?
            """,
            (record.id,),
        )

        child_ids = getattr(record, link.key) or []
        for child_id in child_ids:
            if isinstance(child_id, str) and child_id:
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO {_q(link.table)} (
                        {_q(link.parent_column)},
                        {_q(link.child_column)}
                    )
                    VALUES (?, ?)
                    """,
                    (record.id, child_id),
                )

    return record


def ensure(conn: sqlite3.Connection, record: Record) -> Record:
    """
    Insert if missing. Do not overwrite if present.
    """
    record = _normalize(record)
    cls = type(record)

    row = conn.execute(
        f"""
        SELECT 1
        FROM {_q(cls.table)}
        WHERE id = ?
        """,
        (record.id,),
    ).fetchone()

    if row is not None:
        return get(conn, record.id) or record

    return put(conn, record)


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def get(conn: sqlite3.Connection, record_id: str) -> Record | None:
    """
    Fetch one record by id across all known record types.
    """
    for cls in _models():
        row = conn.execute(
            f"""
            SELECT {_payload_sql(cls)} AS payload
            FROM {_q(cls.table)}
            WHERE id = ?
            """,
            (record_id,),
        ).fetchone()

        if row is not None:
            return from_json(row["payload"])

    return None


def find(
    conn: sqlite3.Connection,
    record_type: str | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Record]:
    """
    Query records.
    """
    if record_type is None:
        models = _models()
    else:
        models = (RECORD_TYPES[record_type],)

    if not models:
        return []

    parts = [
        f"""
        SELECT {_payload_sql(cls)} AS payload, updated_at
        FROM {_q(cls.table)}
        """
        for cls in models
    ]

    sql = f"""
    SELECT payload
    FROM (
        {" UNION ALL ".join(parts)}
    )
    ORDER BY updated_at DESC
    """

    params: list[Any] = []

    if limit is not None or offset:
        sql += " LIMIT ? OFFSET ?"
        params.extend((limit if limit is not None else -1, offset))

    rows = conn.execute(sql, params).fetchall()

    return [from_json(row["payload"]) for row in rows]


def exists(conn: sqlite3.Connection, record_id: str) -> bool:
    return get(conn, record_id) is not None


# ---------------------------------------------------------------------------
# Delete API
# ---------------------------------------------------------------------------


def delete(conn: sqlite3.Connection, record_id: str) -> bool:
    """
    Hard-delete a record by id.

    Also removes link-table rows that reference the deleted id.
    """
    found = False

    for cls in _models():
        cur = conn.execute(
            f"""
            DELETE FROM {_q(cls.table)}
            WHERE id = ?
            """,
            (record_id,),
        )

        if cur.rowcount:
            found = True

    for cls in _models():
        for link in cls.links:
            conn.execute(
                f"""
                DELETE FROM {_q(link.table)}
                WHERE {_q(link.parent_column)} = ?
                   OR {_q(link.child_column)} = ?
                """,
                (record_id, record_id),
            )

    return found


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(conn: sqlite3.Connection) -> list[str]:
    """
    Validate eventual consistency using model metadata.

    Current checks:

    - link-table parent ids exist
    - link-table child ids exist
    - record ids are globally unique across record tables
    """
    issues: list[str] = []

    for cls in _models():
        for link in cls.links:
            link_table = _q(link.table)
            parent_column = _q(link.parent_column)
            child_column = _q(link.child_column)
            parent_table = _q(cls.table)
            child_table = _q(link.child_table)

            rows = conn.execute(
                f"""
                SELECT {parent_column} AS parent_id
                FROM {link_table}
                WHERE {parent_column} NOT IN (
                    SELECT id FROM {parent_table}
                )
                """
            ).fetchall()

            for row in rows:
                issues.append(
                    f"{link.table}.{link.parent_column} {row['parent_id']!r} does not exist"
                )

            rows = conn.execute(
                f"""
                SELECT {child_column} AS child_id
                FROM {link_table}
                WHERE {child_column} NOT IN (
                    SELECT id FROM {child_table}
                )
                """
            ).fetchall()

            for row in rows:
                issues.append(
                    f"{link.table}.{link.child_column} {row['child_id']!r} does not exist"
                )

    seen: dict[str, str] = {}

    for cls in _models():
        rows = conn.execute(
            f"""
            SELECT id
            FROM {_q(cls.table)}
            """
        ).fetchall()

        for row in rows:
            record_id = row["id"]

            if record_id in seen:
                issues.append(
                    f"duplicate record id {record_id!r} in {seen[record_id]!r} and {cls.table!r}"
                )
            else:
                seen[record_id] = cls.table

    return issues


# ---------------------------------------------------------------------------
# Object glue
# ---------------------------------------------------------------------------


class Records:
    """
    Thin object-style wrapper around the record functions.
    """

    __slots__ = ("conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def migrate(self, directory: str | Path | None = None) -> None:
        migrate(self.conn, directory)

    def put(self, record: Record) -> Record:
        return put(self.conn, record)

    def ensure(self, record: Record) -> Record:
        return ensure(self.conn, record)

    def get(self, record_id: str) -> Record | None:
        return get(self.conn, record_id)

    def find(
        self,
        record_type: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Record]:
        return find(
            self.conn,
            record_type,
            limit=limit,
            offset=offset,
        )

    def delete(self, record_id: str) -> bool:
        return delete(self.conn, record_id)

    def exists(self, record_id: str) -> bool:
        return exists(self.conn, record_id)

    def validate(self) -> list[str]:
        return validate(self.conn)
