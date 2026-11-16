"""pkit.lib.sqlite: connections, migrations, and the Database base class."""

import sqlite3

import pytest

from pkit.lib import sql, sqlite


@pytest.fixture
def migrations(tmp_path):
    """Two tiny migrations; names give the apply order."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_first.sql").write_text(
        "CREATE TABLE things (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '');\n",
        encoding="utf-8",
    )
    (directory / "0002_second.sql").write_text(
        "CREATE TABLE entries (id TEXT PRIMARY KEY, body TEXT NOT NULL DEFAULT '');\n",
        encoding="utf-8",
    )
    return directory


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_connect_sets_row_factory_and_pragmas(tmp_path):
    conn = sqlite.connect(tmp_path / "plain.db")
    try:
        assert isinstance(conn.execute("SELECT 1 AS one").fetchone(), sqlite3.Row)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == sqlite.BUSY_TIMEOUT_MS
    finally:
        conn.close()


def test_connect_creates_parent_directories(tmp_path):
    sqlite.connect(tmp_path / "deep" / "nested" / "db.sqlite").close()

    assert (tmp_path / "deep" / "nested" / "db.sqlite").is_file()


def test_database_applies_migrations_once(tmp_path, migrations):
    db_file = tmp_path / "db.sqlite"

    with sqlite.Database(db_file, migrations_dir=migrations) as db:
        assert not db.readonly
        assert "things" in _tables(db.conn)
        assert "entries" in _tables(db.conn)
        # auto_migrate only checks; the explicit call reports nothing new
        assert db.migrate() == []

    with sqlite.Database(db_file, migrations_dir=migrations, auto_migrate=False) as db:
        assert db.migrate() == []


def test_migrate_returns_applied_names(tmp_path, migrations):
    db = sqlite.Database(tmp_path / "db.sqlite", migrations_dir=migrations, auto_migrate=False)
    try:
        assert db.migrate() == ["0001_first.sql", "0002_second.sql"]
    finally:
        db.close()


def test_migrate_accepts_an_explicit_directory(tmp_path, migrations):
    with sqlite.Database(tmp_path / "db.sqlite", auto_migrate=False) as db:
        assert db.migrate(migrations) == ["0001_first.sql", "0002_second.sql"]


def test_auto_migrate_false_leaves_schema_empty(tmp_path, migrations):
    with sqlite.Database(
        tmp_path / "db.sqlite", migrations_dir=migrations, auto_migrate=False
    ) as db:
        assert _tables(db.conn) == set()


def test_database_without_migrations_dir(tmp_path):
    with sqlite.Database(tmp_path / "bare.db") as db:
        assert _tables(db.conn) == set()
        assert db.migrate() == []


@pytest.mark.parametrize("kind", ["missing-dir", "empty-dir"], ids=["missing", "empty"])
def test_migrations_directory_without_sql_files(tmp_path, kind):
    """A missing or empty migrations dir connects fine and builds no tables."""
    directory = tmp_path / kind
    if kind == "empty-dir":
        directory.mkdir()

    with sqlite.Database(tmp_path / "db.sqlite", migrations_dir=directory) as db:
        assert _tables(db.conn) == set()


def test_pending_migrations_before_and_after_apply(tmp_path, migrations):
    conn = sqlite.connect(tmp_path / "db.sqlite")
    try:
        assert [file.name for file in sqlite.pending_migrations(conn, migrations)] == [
            "0001_first.sql",
            "0002_second.sql",
        ]
        assert sqlite.migrate(conn, migrations) == ["0001_first.sql", "0002_second.sql"]
        assert sqlite.pending_migrations(conn, migrations) == []
        assert sqlite.migrate(conn, migrations) == []
    finally:
        conn.close()


def test_pending_migrations_without_directory_is_empty(tmp_path):
    conn = sqlite.connect(tmp_path / "db.sqlite")
    try:
        assert sqlite.pending_migrations(conn, None) == []
    finally:
        conn.close()


def test_readonly_opens_existing_database_read_only(tmp_path, migrations):
    db_file = tmp_path / "db.sqlite"
    with sqlite.Database(db_file, migrations_dir=migrations):
        pass

    with sqlite.Database(db_file, migrations_dir=migrations, readonly=True) as ro:
        assert ro.readonly
        assert "things" in _tables(ro.conn)
        with pytest.raises(sqlite3.OperationalError):
            ro.conn.execute("INSERT INTO things(id) VALUES ('x')")


def test_readonly_missing_file_creates_the_schema(tmp_path, migrations):
    db_file = tmp_path / "fresh.db"

    with sqlite.Database(db_file, migrations_dir=migrations, readonly=True) as db:
        assert not db.readonly  # nothing to read yet: created once
        assert "things" in _tables(db.conn)

    with sqlite.Database(db_file, migrations_dir=migrations, readonly=True) as ro:
        assert ro.readonly


def test_readonly_skips_new_pending_migrations(tmp_path, migrations):
    db_file = tmp_path / "db.sqlite"
    with sqlite.Database(db_file, migrations_dir=migrations):
        pass
    (migrations / "0003_third.sql").write_text(
        "CREATE TABLE late (id TEXT PRIMARY KEY);\n", encoding="utf-8"
    )

    with sqlite.Database(db_file, migrations_dir=migrations, readonly=True) as ro:
        assert "late" not in _tables(ro.conn)


def test_migrate_enables_wal_best_effort(tmp_path, migrations):
    with sqlite.Database(tmp_path / "db.sqlite", migrations_dir=migrations) as db:
        mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode == "wal"


def test_context_exit_commits_on_success(tmp_path, migrations):
    db_file = tmp_path / "db.sqlite"
    with sqlite.Database(db_file, migrations_dir=migrations) as db:
        db.conn.execute(sql.upsert("things", ["id", "name"]), ("t1", "one"))

    with sqlite.Database(db_file, migrations_dir=migrations, readonly=True) as ro:
        assert ro.conn.execute("SELECT name FROM things WHERE id = 't1'").fetchone()[0] == "one"


def test_context_exit_rolls_back_on_error(tmp_path, migrations):
    db_file = tmp_path / "db.sqlite"
    with pytest.raises(RuntimeError), sqlite.Database(db_file, migrations_dir=migrations) as db:
        db.conn.execute(sql.upsert("things", ["id", "name"]), ("t1", "one"))
        raise RuntimeError("boom")

    with sqlite.Database(db_file, migrations_dir=migrations, readonly=True) as ro:
        assert ro.conn.execute("SELECT count(*) FROM things").fetchone()[0] == 0


def test_commit_persists_inside_the_session(tmp_path, migrations):
    db_file = tmp_path / "db.sqlite"
    with sqlite.Database(db_file, migrations_dir=migrations) as db:
        db.conn.execute(sql.upsert("things", ["id", "name"]), ("t1", "one"))
        db.commit()
        crash = sqlite.Database(db_file, readonly=True)
        assert crash.conn.execute("SELECT count(*) FROM things").fetchone()[0] == 1
        crash.close()


def test_close_without_context_manager_persists_commits(tmp_path, migrations):
    db_file = tmp_path / "db.sqlite"
    db = sqlite.Database(db_file, migrations_dir=migrations)
    try:
        db.conn.execute(sql.upsert("things", ["id", "name"]), ("t1", "one"))
        db.commit()
    finally:
        db.close()

    with sqlite.Database(db_file, readonly=True) as ro:
        assert ro.conn.execute("SELECT count(*) FROM things").fetchone()[0] == 1


def test_sql_helpers_drive_a_plain_database(tmp_path, migrations):
    """The scripting story: sql builders over a bare Database, no meta layer."""
    (migrations / "0003_search.sql").write_text(
        "CREATE TABLE search (thing_id TEXT PRIMARY KEY, tokens TEXT NOT NULL DEFAULT '[]');\n",
        encoding="utf-8",
    )

    with sqlite.Database(tmp_path / "db.sqlite", migrations_dir=migrations) as db:
        for thing_id, name in (("t1", "one"), ("t2", "two")):
            db.conn.execute(sql.upsert("things", ["id", "name"]), (thing_id, name))
        db.conn.execute(
            "INSERT INTO search(thing_id, tokens) VALUES ('t1', ?)", ('["one", "uno"]',)
        )

        wanted = db.conn.execute(
            f"SELECT name FROM things WHERE {sql.equals('id', ['t1'])}", ["t1"]
        ).fetchone()
        matched = db.conn.execute(
            f"SELECT thing_id FROM search WHERE {sql.json_contains('search', 'tokens', ['uno'])}",
            ["uno"],
        ).fetchall()
        rows = db.conn.execute("SELECT name FROM things ORDER BY name DESC").fetchall()

    assert wanted["name"] == "one"
    assert [row["thing_id"] for row in matched] == ["t1"]
    assert [row["name"] for row in rows] == ["two", "one"]
