"""SQLite plumbing: connections with pkit conventions, migrations, Database.

One connect() helper, one migration runner, and a Database base class that
owns connect + migrate + commit/rollback + close, so a tool layer only adds
its own typed operations and scripts get the same machinery for free:

    from pkit.lib import sql, sqlite

    with sqlite.Database("x.db", migrations_dir="migrations") as db:
        db.conn.execute(sql.upsert("things", ["id", "name"]), ("t1", "one"))

Migrations are .sql files applied in name order and recorded in a
_migrations table. The pending check is read-only, so an up-to-date
database takes no write lock on connect (gnu parallel can hammer
`pkit archive verify`).
"""

import sqlite3
from pathlib import Path
from typing import Self
from urllib.parse import quote as url_quote

__all__ = ["CONNECT_TIMEOUT", "Database", "connect", "migrate", "pending_migrations"]

CONNECT_TIMEOUT = 30.0
BUSY_TIMEOUT_MS = 30000


def connect(
    path: str | Path, *, readonly: bool = False, timeout: float = CONNECT_TIMEOUT
) -> sqlite3.Connection:
    """Open one sqlite database with the pkit conventions.

    Rows come back as sqlite3.Row, foreign keys stay OFF (import order is
    free), and a busy timeout rides out short write locks. readonly=True
    opens mode=ro: no DDL, no migrations, no write lock on connect.
    """
    target = Path(path).expanduser()
    if readonly:
        conn = sqlite3.connect(f"file:{url_quote(str(target))}?mode=ro", uri=True, timeout=timeout)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def pending_migrations(conn: sqlite3.Connection, migrations_dir: str | Path | None) -> list[Path]:
    """Unapplied .sql files, checked without taking any write lock."""
    if migrations_dir is None:
        return []
    directory = Path(migrations_dir).expanduser()
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.sql"))
    if not files:
        return []
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = '_migrations'"
    ).fetchone()
    if row is None:
        return files
    applied = {row[0] for row in conn.execute("SELECT name FROM _migrations")}
    return [file for file in files if file.name not in applied]


def migrate(conn: sqlite3.Connection, migrations_dir: str | Path | None) -> list[str]:
    """Apply pending migrations, returning the names applied ([] when current).

    WAL journaling is enabled best-effort (read-only or locked databases
    keep their current mode). Each file runs inside BEGIN IMMEDIATE with its
    _migrations bookkeeping inserted first; the primary key re-checks the
    applied set inside the transaction, so concurrent migrators are safe —
    the loser aborts on the duplicate insert before any schema statement
    runs, and a body never executes twice. Migration files must not contain
    BEGIN/COMMIT and must end statements with ';'.
    """
    pending = pending_migrations(conn, migrations_dir)
    if not pending:
        return []
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "name TEXT PRIMARY KEY CHECK (name <> ''), "
        "applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
    )
    newly: list[str] = []
    for file_path in pending:
        name = file_path.name.replace("'", "''")
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                + f"INSERT INTO _migrations(name) VALUES ('{name}');\n"
                + file_path.read_text(encoding="utf-8")
                + "\nCOMMIT;"
            )
        except sqlite3.IntegrityError:
            # A concurrent migrator recorded it while we waited on the
            # write lock; our whole re-run rolled back with the insert.
            conn.rollback()
            continue
        newly.append(file_path.name)
    return newly


class Database:
    """One sqlite connection owning migrations, commit/rollback, close.

    Context-manager contract: commit on success, roll back on error, always
    close. commit() stays public for callers that need intermediate
    durability inside one long session (archive.add commits per file).
    applied_migrations records what connecting migrated ([] when the
    database is current or read-only), so callers like `pkit meta migrate`
    can report it.

    readonly=True opens an existing non-empty database read-only (mode=ro,
    no DDL, no migrations); a missing or empty file is still created once,
    so callers behave sensibly on a fresh machine.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        migrations_dir: str | Path | None = None,
        readonly: bool = False,
    ) -> None:
        target = Path(path).expanduser()
        self.migrations_dir = (
            Path(migrations_dir).expanduser() if migrations_dir is not None else None
        )
        self.readonly = readonly and target.is_file() and target.stat().st_size > 0
        self.conn = connect(target, readonly=self.readonly)
        self.applied_migrations: list[str] = []
        if not self.readonly and pending_migrations(self.conn, self.migrations_dir):
            self.applied_migrations = migrate(self.conn, self.migrations_dir)

    def commit(self) -> None:
        """Persist writes so far; the context exit commits the remainder."""
        self.conn.commit()

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
