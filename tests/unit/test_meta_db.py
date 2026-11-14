import sqlite3

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

    assert count == 4


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
