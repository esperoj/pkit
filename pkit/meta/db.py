from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pkit.meta.models import Entity
from pkit.meta.serialize import from_json, to_json

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: str | Path = "library.db") -> sqlite3.Connection:
    """
    Open a SQLite connection with sane defaults.

    Foreign keys must be enabled for every connection. If you use Django,
    make sure Django's SQLite connection also has foreign key checks enabled.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(path: str | Path = "library.db") -> Iterator[sqlite3.Connection]:
    """
    Simple transaction scope.

    Usage:
        with db.session("library.db") as conn:
            db.setup(conn)
            db.create_entity(conn, some_entity)
    """
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def setup(conn: sqlite3.Connection) -> None:
    """
    Load and run schema.sql.

    This creates tables if missing and replaces triggers.
    It does not drop your main data tables.
    """
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


def reset_sync_depth(conn: sqlite3.Connection) -> None:
    """
    Emergency reset for the trigger sync guard.

    Normally not needed because trigger statements roll back cleanly.
    """
    conn.execute("UPDATE sync_control SET depth = 0 WHERE id = 1")


def create_entity(conn: sqlite3.Connection, entity: Entity) -> Entity:
    """
    Insert a new entity. Fails if the entity id already exists.
    """
    conn.execute(
        """
        INSERT INTO entities (id, type, slug, payload)
        VALUES (?, ?, ?, ?)
        """,
        (entity.id, entity.type, entity.slug, to_json(entity)),
    )
    return entity


def save_entity(conn: sqlite3.Connection, entity: Entity) -> Entity:
    """
    Insert or update an entity by id.

    If you want the smallest possible CRUD surface, you can use only:
        save_entity()
        get_entity()
        list_entities()
        delete_entity()
    """
    conn.execute(
        """
        INSERT INTO entities (id, type, slug, payload)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            type = excluded.type,
            slug = excluded.slug,
            payload = excluded.payload,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (entity.id, entity.type, entity.slug, to_json(entity)),
    )
    return entity


def update_entity(conn: sqlite3.Connection, entity: Entity) -> Entity:
    """
    Update an existing entity. Fails if the entity id does not exist.
    """
    cur = conn.execute(
        """
        UPDATE entities
        SET
            type = ?,
            slug = ?,
            payload = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (entity.type, entity.slug, to_json(entity), entity.id),
    )

    if cur.rowcount == 0:
        raise LookupError(f"entity {entity.id!r} not found")

    return entity


def get_entity(conn: sqlite3.Connection, entity_id: str) -> Entity | None:
    """
    Fetch one entity by id and convert its payload back into a model.
    """
    row = conn.execute(
        """
        SELECT payload
        FROM entities
        WHERE id = ?
        """,
        (entity_id,),
    ).fetchone()

    if row is None:
        return None

    return from_json(row["payload"])


def list_entities(
    conn: sqlite3.Connection,
    entity_type: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Entity]:
    """
    List entities, optionally filtered by type.
    """
    sql = "SELECT payload FROM entities"
    params: list[Any] = []

    if entity_type is not None:
        sql += " WHERE type = ?"
        params.append(entity_type)

    sql += " ORDER BY updated_at DESC"

    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    return [from_json(row["payload"]) for row in rows]


def delete_entity(conn: sqlite3.Connection, entity_id: str) -> bool:
    """
    Delete an entity by id.

    Returns True if an entity row was deleted.
    """
    cur = conn.execute(
        """
        DELETE FROM entities
        WHERE id = ?
        """,
        (entity_id,),
    )
    return cur.rowcount > 0
