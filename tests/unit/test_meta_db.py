import sqlite3

import pytest

from pkit.meta import dump, load
from pkit.meta.db import MetaDB
from pkit.meta.models import Book, File, Person

ID = "sha256:" + "a" * 64


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        yield meta


def test_put_and_get_round_trip(db):
    db.put(Book(id="b1", title="Pandora Hearts", author_ids=["p1"]))

    record = db.get("b1")

    assert isinstance(record, Book)
    assert record.title == "Pandora Hearts"
    assert record.author_ids == ["p1"]


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
    db.put(Book(id="b1", title="one"))
    db.put(Book(id="b2", title="two"))
    db.put(Person(id="p1", name="Jun"))

    assert {record.id for record in db.find(Book)} == {"b1", "b2"}
    assert [record.id for record in db.find("person")] == ["p1"]
    assert len(db.find()) == 3
    assert len(db.find(Book, limit=1)) == 1
    assert len(db.find(limit=2, offset=1)) == 2


def test_find_by_identifier(db):
    db.put(Person(id="p1", name="Douglas", identifiers=["wikidata:Q42", "viaf:113"]))
    db.put(Book(id="b1", title="Guide", identifiers=["isbn13:9780", "wikidata:Q187655"]))
    db.put(File(id=ID, size=3, identifiers=[ID, "sha1:bbb"]))

    assert [r.id for r in db.find(identifier="wikidata:Q42")] == ["p1"]
    assert [r.id for r in db.find(Book, identifier=["isbn13:9780", "missing"])] == ["b1"]
    assert [r.id for r in db.find(identifier="sha1:bbb")] == [ID]
    assert db.find(identifier="missing") == []
    assert db.find(identifier="") == []
    assert db.find(identifier=[]) == []


def test_find_newest_first(db):
    db.put(Book(id="b1", title="one"))
    db.put(Book(id="b2", title="two"))
    db.conn.execute("UPDATE books SET updated_at = '2000-01-01T00:00:00Z' WHERE id = 'b1'")

    assert [record.id for record in db.find(Book)] == ["b2", "b1"]


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
    db.put(Book(id="b1", title="x", author_ids=["p1"]))

    assert db.delete("b1") is True
    assert db.delete("b1") is False
    assert db.get("b1") is None
    assert db.validate() == []


def test_validate_reports_orphans_and_duplicates(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        meta.put(Book(id="b1", title="x", author_ids=["ghost"]))
        meta.put(Person(id="p1", name="A", identifiers=["wikidata:Q42"]))
        meta.put(Person(id="p2", name="B", identifiers=["wikidata:Q42"]))
        meta.conn.execute("INSERT INTO book_authors(book_id, person_id) VALUES ('gone', 'p1')")
        meta.conn.execute("INSERT INTO persons(id, slug, name) VALUES ('b1', 'b1-slug', 'same id')")

        issues = meta.validate()

    assert "book_authors.person_id 'ghost' does not exist" in issues
    assert "book_authors.book_id 'gone' does not exist" in issues
    assert "duplicate record id 'b1' in 'books' and 'persons'" in issues
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

    assert count == 3


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

    assert dump.dump(repo, db_path=db_file) == 1

    other = tmp_path / "b.db"
    assert load.load(repo, db_path=other) == 1
    with MetaDB(other) as meta:
        record = meta.get(ID)
    assert isinstance(record, File)
    assert record.locations == ["ia:item/a"]
