from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pkit.meta.models import Entity
from pkit.meta.serialize import from_json, to_json

SCHEMA_PATH = Path(__file__).resolve().with_name("schema.sql")


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
def session(path: str | Path = "library.db") -> Generator[sqlite3.Connection]:
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
    Create real tables, view, and triggers.
    """
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


def _now(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]


def create_entity(conn: sqlite3.Connection, entity: Entity) -> Entity:
    """
    Insert a new entity through the entities view.
    """
    conn.execute(
        """
        INSERT INTO entities (id, type, slug, payload)
        VALUES (?, ?, ?, ?)
        """,
        (
            entity.id,
            entity.type,
            entity.slug,
            to_json(entity),
        ),
    )
    return entity


def save_entity(conn: sqlite3.Connection, entity: Entity) -> Entity:
    """
    Insert or update an entity by id.

    SQLite upsert through a writable view is not as nice as upsert on a real
    table, so this does a simple existence check first.
    """
    exists = conn.execute(
        "SELECT 1 FROM entities WHERE id = ?",
        (entity.id,),
    ).fetchone()

    if exists is None:
        return create_entity(conn, entity)

    return update_entity(conn, entity)


def update_entity(conn: sqlite3.Connection, entity: Entity) -> Entity:
    """
    Update an existing entity through the entities view.
    """
    exists = conn.execute(
        "SELECT 1 FROM entities WHERE id = ?",
        (entity.id,),
    ).fetchone()

    if exists is None:
        raise LookupError(f"entity {entity.id!r} not found")

    conn.execute(
        """
        UPDATE entities
        SET
            type = ?,
            slug = ?,
            payload = ?
        WHERE id = ?
        """,
        (
            entity.type,
            entity.slug,
            to_json(entity),
            entity.id,
        ),
    )

    return entity


def get_entity(
    conn: sqlite3.Connection,
    entity_id: str,
    include_archived: bool = False,
) -> Entity | None:
    """
    Fetch one entity by id.

    By default archived entities are hidden.
    """
    sql = "SELECT payload FROM entities WHERE id = ?"

    if not include_archived:
        sql += " AND archived_at = ''"

    row = conn.execute(sql, (entity_id,)).fetchone()
    if row is None:
        return None

    return from_json(row["payload"])


def list_entities(
    conn: sqlite3.Connection,
    entity_type: str | None = None,
    include_archived: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> list[Entity]:
    """
    List entities.

    By default archived entities are hidden.
    """
    sql = "SELECT payload FROM entities"
    where: list[str] = []
    params: list[Any] = []

    if not include_archived:
        where.append("archived_at = ''")

    if entity_type is not None:
        where.append("type = ?")
        params.append(entity_type)

    if where:
        sql += " WHERE " + " AND ".join(where)

    sql += " ORDER BY updated_at DESC"

    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    return [from_json(row["payload"]) for row in rows]


def archive_entity(conn: sqlite3.Connection, entity_id: str) -> bool:
    """
    Soft-delete an entity by marking it archived.

    This is the normal replacement for delete.
    """
    exists = conn.execute(
        """
        SELECT 1
        FROM entities
        WHERE id = ? AND archived_at = ''
        """,
        (entity_id,),
    ).fetchone()

    if exists is None:
        return False

    now = _now(conn)

    conn.execute(
        """
        UPDATE entities
        SET archived_at = ?
        WHERE id = ?
        """,
        (now, entity_id),
    )

    return True


def unarchive_entity(conn: sqlite3.Connection, entity_id: str) -> bool:
    """
    Restore an archived entity.
    """
    exists = conn.execute(
        """
        SELECT 1
        FROM entities
        WHERE id = ? AND archived_at <> ''
        """,
        (entity_id,),
    ).fetchone()

    if exists is None:
        return False

    conn.execute(
        """
        UPDATE entities
        SET archived_at = ''
        WHERE id = ?
        """,
        (entity_id,),
    )

    return True


def purge_entity(conn: sqlite3.Connection, entity_id: str) -> bool:
    """
    Dangerous hard delete.

    This is intentionally not the normal delete path.
    It only deletes entities that are already archived.
    """
    exists = conn.execute(
        """
        SELECT 1
        FROM entities
        WHERE id = ? AND archived_at <> ''
        """,
        (entity_id,),
    ).fetchone()

    if exists is None:
        return False

    conn.execute(
        """
        DELETE FROM entities
        WHERE id = ?
        """,
        (entity_id,),
    )

    return True
