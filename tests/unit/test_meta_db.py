import json
import re
import sqlite3
from pathlib import Path

import pytest

from pkit.meta import dump, load
from pkit.meta.db import MetaDB
from pkit.meta.models import File, Person, Software, Text

ID = "sha256:" + "a" * 64


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        yield meta


def test_put_and_get_round_trip(db):
    db.put(
        Text(
            id="t1",
            title="Pandora Hearts",
            kind="manga",
            series=["Pandora Hearts"],
            genres=["dark fantasy"],
            author_ids=["p1"],
        )
    )

    record = db.get("t1")

    assert isinstance(record, Text)
    assert record.title == "Pandora Hearts"
    assert record.kind == "manga"
    assert record.series == ["Pandora Hearts"]
    assert record.genres == ["dark fantasy"]
    assert record.author_ids == ["p1"]


def test_software_round_trip(db):
    db.put(
        Software(
            id="s1",
            title="Tales of the Abyss",
            kind="game",
            series=["Tales"],
            developers=["Namco Tales Studio"],
            publishers=["Namco"],
            platforms=["PlayStation 2", "Nintendo 3DS"],
            genres=["role-playing video game"],
            identifiers=["wikidata:Q82050", "igdb:tales-of-the-abyss"],
        )
    )

    record = db.get("s1")

    assert isinstance(record, Software)
    assert record.title == "Tales of the Abyss"
    assert record.kind == "game"
    assert record.series == ["Tales"]
    assert record.developers == ["Namco Tales Studio"]
    assert record.platforms == ["PlayStation 2", "Nintendo 3DS"]
    assert [r.id for r in db.find(Software, identifier="wikidata:Q82050")] == ["s1"]


def test_put_accepts_mapping(db):
    db.put({"id": "p1", "type": "person", "name": "Jun"})

    record = db.get("p1")

    assert isinstance(record, Person)
    assert record.name == "Jun"


def test_put_fills_id_and_slug(db):
    stored = db.put(Person(name="X"))

    assert stored.id
    assert stored.slug == stored.id
    assert db.get(stored.id) == stored


def test_get_missing_is_none(db):
    assert db.get("nope") is None


def test_get_or_create(db):
    first = db.get_or_create(Person(id="p1", name="Jun"))
    again = db.get_or_create({"id": "p1", "type": "person", "name": "Other"})

    assert first == again
    assert db.get("p1").name == "Jun"


def test_find_filters_and_paginates(db):
    db.put(Text(id="t1", title="one"))
    db.put(Text(id="t2", title="two"))
    db.put(Person(id="p1", name="Jun"))
    db.put(Software(id="s1", title="app"))

    assert {record.id for record in db.find(Text)} == {"t1", "t2"}
    assert [record.id for record in db.find("person")] == ["p1"]
    assert [record.id for record in db.find(Software)] == ["s1"]
    assert len(db.find()) == 4
    assert len(db.find(Text, limit=1)) == 1
    assert len(db.find(limit=2, offset=1)) == 2


def test_find_by_identifier(db):
    db.put(Person(id="p1", name="Douglas", identifiers=["wikidata:Q42", "viaf:113"]))
    db.put(Text(id="t1", title="Guide", identifiers=["isbn13:9780", "wikidata:Q187655"]))
    db.put(Software(id="s1", title="Abyss", identifiers=["wikidata:Q82050", "igdb:abyss"]))
    db.put(File(id=ID, size=3, identifiers=[ID, "sha1:bbb"]))

    assert [r.id for r in db.find(identifier="wikidata:Q42")] == ["p1"]
    assert [r.id for r in db.find(Text, identifier=["isbn13:9780", "missing"])] == ["t1"]
    assert [r.id for r in db.find(Software, identifier="igdb:abyss")] == ["s1"]
    assert [r.id for r in db.find(identifier="sha1:bbb")] == [ID]
    assert db.find(identifier="missing") == []
    assert db.find(identifier="") == []
    assert db.find(identifier=[]) == []


def test_find_newest_first(db):
    db.put(Text(id="t1", title="one"))
    db.put(Text(id="t2", title="two"))
    db.conn.execute("UPDATE texts SET updated_at = '2000-01-01T00:00:00Z' WHERE id = 't1'")

    assert [record.id for record in db.find(Text)] == ["t2", "t1"]


def test_find_rejects_unknown_type(db):
    with pytest.raises(ValueError, match="unknown record type"):
        db.find("bogus")


def test_find_rejects_non_record_class(db):
    with pytest.raises(TypeError, match="not a Record class"):
        db.find(str)


def test_exists(db):
    db.put(Person(id="p1", name="Jun"))

    assert db.exists("p1")
    assert not db.exists("nope")


def test_delete_removes_rows_and_links(db):
    db.put(Person(id="p1", name="Jun"))
    db.put(Text(id="t1", title="x", author_ids=["p1"]))

    assert db.delete("t1") is True
    assert db.delete("t1") is False
    assert db.get("t1") is None
    assert db.validate() == []


def test_validate_reports_orphans_and_duplicates(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        meta.put(Text(id="t1", title="x", author_ids=["ghost"]))
        meta.put(Person(id="p1", name="A", identifiers=["wikidata:Q42"]))
        meta.put(Person(id="p2", name="B", identifiers=["wikidata:Q42"]))
        meta.conn.execute("INSERT INTO text_authors(text_id, person_id) VALUES ('gone', 'p1')")
        meta.conn.execute("INSERT INTO persons(id, slug, name) VALUES ('t1', 't1-slug', 'same id')")

        issues = meta.validate()

    assert "text_authors.person_id 'ghost' does not exist" in issues
    assert "text_authors.text_id 'gone' does not exist" in issues
    assert "duplicate record id 't1' in 'texts' and 'persons'" in issues
    assert "duplicate identifier 'wikidata:Q42' in 'persons'" in issues


def test_context_exit_rolls_back_on_error(tmp_path):
    path = tmp_path / "records.db"
    with pytest.raises(RuntimeError), MetaDB(path) as meta:
        meta.put(Person(id="p1", name="Jun"))
        raise RuntimeError("boom")

    with MetaDB(path) as meta:
        assert meta.get("p1") is None


def test_commit_persists_inside_session(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        meta.put(Person(id="p1", name="Jun"))
        meta.commit()

        with MetaDB(path) as other:
            assert other.get("p1") is not None


def test_migrations_apply_once(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path):
        pass
    with MetaDB(path) as meta:
        count = meta.conn.execute("SELECT count(*) FROM _migrations").fetchone()[0]

    assert count == 5


def test_migrations_create_expected_indexes(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        indexes = {
            row[0]
            for row in meta.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }

    assert {"files_created", "persons_created", "texts_created", "software_created"} <= indexes


def test_path_defaults_to_config(tmp_path, monkeypatch):
    path = tmp_path / "env.db"
    monkeypatch.setenv("PKIT_META_DB_PATH", str(path))

    with MetaDB() as meta:
        meta.put(Person(id="p1", name="Jun"))

    with MetaDB() as meta:
        assert meta.get("p1") is not None


def test_file_id_must_be_content_addressed(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.put(File(id="not-a-hash"))


def test_put_accepts_hash_id(db):
    stored = db.put(File(id=ID, size=3, identifiers=[ID, "sha1:b", "md5:c"]))

    assert stored.slug == ID
    assert db.get(ID) == stored


def test_dump_load_round_trip(tmp_path):
    db_file = tmp_path / "a.db"
    repo = tmp_path / "repo"
    with MetaDB(db_file) as meta:
        meta.put(File(id=ID, size=1, locations=["ia:item/a"]))
        meta.put(Software(id="s1", title="Abyss", kind="game", series=["Tales"]))

    assert dump.dump(repo, db_path=db_file) == 2

    other = tmp_path / "b.db"
    assert load.load(repo, db_path=other) == 2
    with MetaDB(other) as meta:
        file_rec = meta.get(ID)
        sw_rec = meta.get("s1")
    assert isinstance(file_rec, File)
    assert file_rec.locations == ["ia:item/a"]
    assert isinstance(sw_rec, Software)
    assert sw_rec.series == ["Tales"]


# --- find(sort=...) ----------------------------------------------------------


def test_find_sort_modes(db):
    db.put(Text(id="t2", title="two"))
    db.put(Text(id="t1", title="one"))
    db.conn.execute(
        "UPDATE texts SET created_at = '2001-01-01T00:00:00Z', updated_at = '2002-01-01T00:00:00Z'"
        " WHERE id = 't1'"
    )
    db.conn.execute(
        "UPDATE texts SET created_at = '2000-01-01T00:00:00Z', updated_at = '2003-01-01T00:00:00Z'"
        " WHERE id = 't2'"
    )

    assert [r.id for r in db.find(Text, sort="id")] == ["t1", "t2"]
    assert [r.id for r in db.find(Text, sort="-id")] == ["t2", "t1"]
    assert [r.id for r in db.find(Text, sort="created")] == ["t2", "t1"]
    assert [r.id for r in db.find(Text, sort="-created")] == ["t1", "t2"]
    assert [r.id for r in db.find(Text, sort="updated")] == ["t1", "t2"]
    assert [r.id for r in db.find(Text, sort="-updated")] == ["t2", "t1"]
    assert [r.id for r in db.find(Text)] == ["t2", "t1"]  # default: -updated


def test_find_sort_tie_breaks_on_id(db):
    db.put(Text(id="t2", title="two"))
    db.put(Text(id="t1", title="one"))
    db.conn.execute("UPDATE texts SET created_at = '2000-01-01T00:00:00Z'")

    assert [r.id for r in db.find(Text, sort="created")] == ["t1", "t2"]
    assert [r.id for r in db.find(Text, sort="-created")] == ["t2", "t1"]


def test_find_sort_works_across_types(db):
    db.put(Text(id="t1", title="one"))
    db.put(Person(id="p1", name="Jun"))

    assert [r.id for r in db.find(sort="id")] == ["p1", "t1"]
    assert [r.id for r in db.find(sort="-id")] == ["t1", "p1"]


def test_find_sort_with_identifier_filter(db):
    db.put(Person(id="p1", name="A", identifiers=["wikidata:Q1"]))
    db.put(Person(id="p2", name="B", identifiers=["wikidata:Q2"]))

    assert [
        r.id for r in db.find(Person, sort="id", identifier=["wikidata:Q1", "wikidata:Q2"])
    ] == [
        "p1",
        "p2",
    ]


def test_find_rejects_unknown_sort(db):
    with pytest.raises(ValueError, match="unknown sort"):
        db.find(Text, sort="bogus")


# --- migrate() and zero-write connections ------------------------------------


def test_migrate_method_applies_and_reports(tmp_path):
    path = tmp_path / "records.db"

    with MetaDB(path, auto_migrate=False) as db:
        applied = db.migrate()

    assert applied == [
        "0001_file.sql",
        "0002_person.sql",
        "0003_text.sql",
        "0004_software.sql",
        "0005_created_indexes.sql",
    ]
    with MetaDB(path, auto_migrate=False) as db:
        assert db.migrate() == []


def test_migrate_reports_only_new_files(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path):
        pass

    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "0000_baseline.sql").write_text(
        "CREATE TABLE IF NOT EXISTS baseline (id TEXT);\n", encoding="utf-8"
    )

    with MetaDB(path, auto_migrate=False, migrations_dir=extra) as db:
        assert db.migrate() == ["0000_baseline.sql"]


def test_migrate_skips_concurrently_applied(tmp_path, monkeypatch):
    """A second migrator that raced ahead must not crash or double-apply."""
    path = tmp_path / "records.db"
    real_read_text = Path.read_text
    state = {"injected": False, "busy": False}

    def racing_read_text(self, *args, **kwargs):
        if self.name == "0001_file.sql" and not state["injected"] and not state["busy"]:
            state["busy"] = True
            try:
                with MetaDB(path, auto_migrate=False) as other:
                    other.migrate()
                state["injected"] = True
            finally:
                state["busy"] = False
        return real_read_text(self, *args, **kwargs)

    with MetaDB(path, auto_migrate=False) as db:
        monkeypatch.setattr(Path, "read_text", racing_read_text)
        applied = db.migrate()

    assert applied == []
    with MetaDB(path, readonly=True) as db:
        count = db.conn.execute("SELECT count(*) FROM _migrations").fetchone()[0]
    assert count == 5


def test_connect_executes_zero_writes_when_current(tmp_path, monkeypatch):
    path = tmp_path / "records.db"
    with MetaDB(path):
        pass

    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    with MetaDB(path) as db:
        assert db.get("nope") is None
        assert db.find(Person) == []

    writes = [
        statement
        for statement in statements
        if re.match(
            r"\s*(BEGIN|COMMIT|ROLLBACK|CREATE|INSERT|UPDATE|DELETE|DROP|ALTER)\b",
            statement,
            re.IGNORECASE,
        )
    ]
    assert writes == []
    assert any("sqlite_master" in statement for statement in statements)


def test_readonly_opens_read_only(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as db:
        db.put(Person(id="p1", name="Jun"))

    with MetaDB(path, readonly=True) as ro:
        assert ro.get("p1") is not None
        assert ro.readonly is True
        with pytest.raises(sqlite3.OperationalError):
            ro.put(Person(id="p2", name="Mai"))


def test_readonly_never_migrates(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path):
        pass
    # A new pending migration must not be applied through a readonly handle.
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "0006_extra.sql").write_text(
        "CREATE TABLE IF NOT EXISTS extra (id TEXT);\n", encoding="utf-8"
    )

    with MetaDB(path, readonly=True, migrations_dir=extra) as ro:
        table = ro.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'extra'"
        ).fetchone()
    assert table is None


def test_readonly_missing_file_creates_schema(tmp_path):
    path = tmp_path / "records.db"

    with MetaDB(path, readonly=True) as db:
        assert db.find(Person) == []
        assert db.validate() == []

    assert path.exists()
    with MetaDB(path, readonly=True) as db:
        assert db.readonly is True  # created once, read-only afterwards


def test_readonly_zero_writes_while_writer_holds_transaction(tmp_path, monkeypatch):
    path = tmp_path / "records.db"
    with MetaDB(path) as db:
        db.put(Person(id="p1", name="Jun"))

    writer = sqlite3.connect(str(path))
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO persons(id, slug, name) VALUES ('p9', 'p9', 'Busy')")

    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    try:
        monkeypatch.setattr(sqlite3, "connect", traced_connect)
        with MetaDB(path, readonly=True) as ro:
            # Committed data is readable while the writer holds the lock.
            assert ro.get("p1") is not None
    finally:
        writer.rollback()
        writer.close()

    writes = [
        statement
        for statement in statements
        if re.match(
            r"\s*(BEGIN|COMMIT|ROLLBACK|CREATE|INSERT|UPDATE|DELETE|DROP|ALTER)\b",
            statement,
            re.IGNORECASE,
        )
    ]
    assert writes == []


def test_dump_orders_by_id_in_sqlite(tmp_path):
    db_file = tmp_path / "a.db"
    repo = tmp_path / "repo"
    with MetaDB(db_file) as meta:
        meta.put(Software(id="s2", title="B", kind="game"))
        meta.put(Software(id="s1", title="A", kind="game"))

    assert dump.dump(repo, db_path=db_file) == 2

    rows = json.loads((repo / "software.json").read_text())
    assert [row["id"] for row in rows] == ["s1", "s2"]


def test_migrate_with_missing_directory_returns_empty(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as db:
        assert db.migrate(tmp_path / "no-such-dir") == []
