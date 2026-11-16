import json
import os
import re
import sqlite3
from pathlib import Path
from typing import ClassVar, Literal

import pytest

from pkit.meta import dump, load
from pkit.meta.db import MetaDB
from pkit.meta.models import (
    File,
    Note,
    Person,
    Record,
    Software,
    Sound,
    Text,
    Video,
    record_model,
)

SHA256 = "a" * 64
SHA1 = "b" * 40
MD5 = "c" * 32
ID = f"sha256:{SHA256}"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        yield meta


# One generated round-trip per model: every field survives put -> get, and a
# where-clause finds the row back. New model = one table row, not one test.
ROUND_TRIPS = [
    (
        Text,
        {
            "title": "Pandora Hearts",
            "kind": "manga",
            "series": ["Pandora Hearts"],
            "genres": ["dark fantasy"],
            "author_ids": ["p1"],
        },
        {"kind": "manga", "author_ids": "p1"},
    ),
    (
        Software,
        {
            "title": "Tales of the Abyss",
            "kind": "game",
            "series": ["Tales"],
            "developers": ["Namco Tales Studio"],
            "platforms": ["PlayStation 2", "Nintendo 3DS"],
            "identifiers": ["wikidata:Q82050", "igdb:tales-of-the-abyss"],
        },
        {"identifiers": "igdb:tales-of-the-abyss"},
    ),
    (
        Video,
        {
            "title": "Ghost in the Shell",
            "kind": "movie",
            "duration": 4800,
            "directors": ["Mamoru Oshii"],
            "genres": ["science fiction"],
            "identifiers": ["imdb:tt0000001"],
        },
        {"kind": "movie", "duration": 4800},
    ),
    (
        Sound,
        {
            "title": "Unravel",
            "kind": "song",
            "duration": 240,
            "performers": ["TK from Ling tosite sigure"],
            "identifiers": ["isrc:XX0000000000"],
        },
        {"identifiers": "isrc:XX0000000000"},
    ),
    (
        Person,
        {
            "name": "Jun",
            "gender": "male",
            "occupations": ["writer"],
            "identifiers": ["wikidata:Q42"],
        },
        {"name": "Jun"},
    ),
]


@pytest.mark.parametrize("cls,values,where", ROUND_TRIPS, ids=lambda v: getattr(v, "__name__", ""))
def test_round_trip(db, cls, values, where):
    db.put(cls(id="x1", **values))

    record = db.get("x1")

    assert isinstance(record, cls)
    for name, value in values.items():
        assert getattr(record, name) == value
    assert [r.id for r in db.find(cls, where=where)] == ["x1"]


def test_note_round_trip_and_subject_filters(db):
    """Reviews, reading progress, and play logs are one model over any type."""
    db.put(Text(id="t1", title="Pandora Hearts", kind="manga"))
    db.put(Software(id="s1", title="Tales of the Abyss", kind="game"))
    db.put(
        Note(
            id="n1",
            title="Reread",
            kind="review",
            body="Still great.",
            rating="9/10",
            subject_ids=["t1"],
        )
    )
    db.put(
        Note(
            id="n2",
            kind="reading",
            start_date="2024-01-01",
            end_date="2024-03-01",
            subject_ids=["t1"],
        )
    )
    db.put(Note(id="n3", kind="play", duration=5400, subject_ids=["s1", "t1"]))

    stored = db.get("n1")
    assert isinstance(stored, Note)
    assert stored.body == "Still great."
    assert stored.rating == "9/10"
    assert db.get("n2").start_date == "2024-01-01"
    play = db.get("n3")
    assert play.duration == 5400

    assert {r.id for r in db.find(Note, where={"subject_ids": "t1"})} == {"n1", "n2", "n3"}
    assert [r.id for r in db.find(Note, where={"subject_ids": "s1"})] == ["n3"]
    assert [r.id for r in db.find(Note, where={"kind": "review"})] == ["n1"]
    assert db.find(Note, where={"subject_ids": "ghost"}) == []


def test_find_where_narrows_across_new_and_old_models(db):
    db.put(Video(id="v1", title="Abyss", genres=["science fiction"]))
    db.put(Sound(id="a1", title="Abyss", genres=["science fiction"]))
    db.put(Text(id="t1", title="Abyss"))

    # genres narrows to the models carrying it; title to all three
    assert {r.id for r in db.find(where={"genres": "science fiction"})} == {"v1", "a1"}
    assert {r.id for r in db.find(where={"title": "Abyss"})} == {"t1", "v1", "a1"}


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


def test_find_where_scalar_equality(db):
    db.put(Person(id="p1", name="Jun", country="JP"))
    db.put(Person(id="p2", name="Mai", country="VN"))

    assert [r.id for r in db.find(Person, where={"name": "Jun"})] == ["p1"]
    assert [r.id for r in db.find(Person, where={"country": "VN"})] == ["p2"]
    assert db.find(Person, where={"name": "nope"}) == []


def test_find_where_scalar_collection_in(db):
    db.put(Person(id="p1", name="Jun"))
    db.put(Person(id="p2", name="Mai"))
    db.put(Person(id="p3", name="Ken"))

    assert {r.id for r in db.find(Person, where={"name": ["Jun", "Ken"]})} == {"p1", "p3"}
    assert {r.id for r in db.find(Person, where={"name": ("Mai", "Mai")})} == {"p2"}
    assert db.find(Person, where={"name": []}) == []


def test_find_where_array_inclusion(db):
    db.put(Person(id="p1", name="Jun", identifiers=["wikidata:Q42", "viaf:113"]))
    db.put(Text(id="t1", title="Guide", genres=["dark fantasy"], identifiers=["isbn13:9780"]))
    db.put(Software(id="s1", title="Abyss", platforms=["Linux"], identifiers=["igdb:abyss"]))
    db.put(File(id=ID, size=3, locations=["ia:x"]))

    assert [r.id for r in db.find(where={"identifiers": "wikidata:Q42"})] == ["p1"]
    assert [r.id for r in db.find(Text, where={"identifiers": ["isbn13:9780", "nope"]})] == ["t1"]
    assert [r.id for r in db.find(where={"genres": "dark fantasy"})] == ["t1"]
    assert [r.id for r in db.find(where={"platforms": ["Linux", "Plan 9"]})] == ["s1"]
    assert [r.id for r in db.find(File, where={"locations": "ia:x"})] == [ID]
    assert db.find(where={"identifiers": []}) == []
    assert db.find(where={"identifiers": "missing"}) == []


def test_find_where_link_filter(db):
    db.put(Person(id="p1", name="Jun"))
    db.put(Person(id="p2", name="Mai"))
    db.put(Text(id="t1", title="one", author_ids=["p1"]))
    db.put(Text(id="t2", title="two", author_ids=["p2", "p1"]))
    db.put(Text(id="t3", title="three"))

    assert {r.id for r in db.find(Text, where={"author_ids": "p1"})} == {"t1", "t2"}
    assert {r.id for r in db.find(Text, where={"author_ids": ["p2"]})} == {"t2"}
    assert db.find(Text, where={"author_ids": []}) == []
    assert db.find(Text, where={"author_ids": "ghost"}) == []


def test_find_where_and_combination(db):
    db.put(Person(id="p1", name="Jun"))
    db.put(Text(id="t1", title="one", kind="manga", genres=["dark fantasy"], author_ids=["p1"]))
    db.put(Text(id="t2", title="two", kind="manga", genres=["comedy"]))
    db.put(Text(id="t3", title="three", kind="novel", genres=["dark fantasy"]))

    assert {r.id for r in db.find(Text, where={"kind": "manga", "genres": "dark fantasy"})} == {
        "t1"
    }
    assert {r.id for r in db.find(Text, where={"kind": "manga", "author_ids": "p1"})} == {"t1"}
    assert db.find(Text, where={"kind": "novel", "author_ids": "p1"}) == []


def test_find_where_narrows_to_models_with_all_keys(db):
    db.put(Person(id="p1", name="Jun"))
    db.put(Software(id="s1", title="app", platforms=["Linux"]))
    db.put(Text(id="t1", title="book"))

    assert [r.id for r in db.find(where={"name": "Jun"})] == ["p1"]
    assert [r.id for r in db.find(where={"platforms": "Linux"})] == ["s1"]
    assert [r.id for r in db.find(where={"title": "app"})] == ["s1"]
    # title lives on Text and Software; platforms only on Software.
    assert [r.id for r in db.find(where={"title": "app", "platforms": "Linux"})] == ["s1"]
    assert db.find(where={"title": "app", "name": "Jun"}) == []  # no model has both
    with pytest.raises(ValueError, match="no stored record type has a queryable field"):
        db.find(where={"bogus": "x"})


def test_find_where_rejects_unknown_and_dict_fields(db):
    with pytest.raises(ValueError, match="not a queryable field on person"):
        db.find(Person, where={"bogus": "x"})
    with pytest.raises(ValueError, match="not a queryable field on person"):
        db.find(Person, where={"extra": {"k": "v"}})
    # field validation wins over value handling: an empty collection
    # elsewhere in the same where cannot mask an unknown field
    with pytest.raises(ValueError, match="not a queryable field on person"):
        db.find(Person, where={"name": [], "bogus": "x"})


def test_find_where_rejects_none_and_mapping_values(db):
    db.put(Person(id="p1", name="Jun"))
    with pytest.raises(ValueError, match="not a filter value"):
        db.find(Person, where={"name": None})
    with pytest.raises(ValueError, match="not a filter value"):
        db.find(Person, where={"identifiers": {"wikidata:Q42": 1}})


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


def test_exists_by_id(db):
    db.put(Person(id="p1", name="Jun"))

    assert db.exists("p1")
    assert not db.exists("nope")
    assert not db.exists("")


def test_get_by_id_default(db):
    db.put(Person(id="p1", name="Jun"))

    record = db.get("p1")
    assert isinstance(record, Person)
    assert record.name == "Jun"
    assert db.get("nope") is None
    assert db.get("") is None


def test_get_and_exists_by_identifiers(db):
    db.put(Person(id="p1", name="Jun", identifiers=["wikidata:Q42", "viaf:113"]))

    assert db.exists("wikidata:Q42", by="identifiers")
    record = db.get("wikidata:Q42", by="identifiers")
    assert isinstance(record, Person)
    assert record.id == "p1"
    assert not db.exists("viaf:999", by="identifiers")
    assert db.get("viaf:999", by="identifiers") is None


def test_get_and_exists_by_hash_fields(db):
    db.put(File(id=ID, size=3, sha256=SHA256, sha1=SHA1, md5=MD5))

    for field, value in (("sha256", SHA256), ("sha1", SHA1), ("md5", MD5)):
        assert db.exists(value, by=field)
        record = db.get(value, by=field)
        assert isinstance(record, File)
        assert record.id == ID
    assert not db.exists("0" * 40, by="sha1")


def test_get_by_field_tuple(db):
    db.put(File(id=ID, size=3, sha256=SHA256, sha1=SHA1, md5=MD5))

    record = db.get(MD5, by=("sha1", "md5"))  # sha1 misses, md5 hits
    assert isinstance(record, File)
    assert record.id == ID
    assert db.get("0" * 40, by=("sha1", "md5")) is None


def test_get_record_probe_uses_unique_fields(db):
    db.put(Person(id="p1", name="Jun", identifiers=["wikidata:Q42"]))
    db.put(File(id=ID, size=3, sha256=SHA256, sha1=SHA1, md5=MD5))

    record = db.get(Person(id="", identifiers=["wikidata:Q42"]))  # id skipped as empty
    assert isinstance(record, Person)
    assert record.id == "p1"
    file_probe = db.get(File(sha1=SHA1))  # id and sha256 skipped as empty
    assert isinstance(file_probe, File)
    assert file_probe.id == ID
    assert db.exists(File(md5=MD5))
    assert not db.exists(File(sha1="0" * 40))


def test_get_record_probe_with_explicit_by(db):
    db.put(Person(id="p1", name="Jun"))

    record = db.get(Person(id="", name="Jun"), by="name")
    assert isinstance(record, Person)
    assert record.id == "p1"
    assert db.exists(Person(id="", name="Jun"), by="name")


def test_get_record_probe_rejects_mismatched_record_type(db):
    db.put(Person(id="p1", name="Jun"))
    with pytest.raises(ValueError, match="does not match"):
        db.get(Person(id="p1"), record_type="text")
    with pytest.raises(ValueError, match="does not match"):
        db.get(Person(id="p1"), record_type=Text)


def test_get_mapping_probe_with_type(db):
    db.put(Person(id="p1", name="Jun", identifiers=["wikidata:Q42"]))

    record = db.get({"type": "person", "identifiers": ["wikidata:Q42"]})
    assert isinstance(record, Person)
    assert record.id == "p1"
    assert db.exists({"type": "person", "identifiers": "wikidata:Q42"})
    assert db.get({"type": "person", "id": "", "slug": ""}) is None  # empties skipped
    assert db.exists({"type": "person", "identifiers": "missing"}) is False


def test_get_mapping_probe_without_type(db):
    db.put(Person(id="p1", name="Jun"))
    db.put(File(id=ID, size=1, sha256=SHA256, sha1=SHA1, md5=MD5))

    record = db.get({"name": "Jun"})
    assert isinstance(record, Person)
    assert record.id == "p1"
    record = db.get({"sha1": SHA1})
    assert isinstance(record, File)
    assert record.id == ID
    assert db.get({"name": "nope"}) is None
    assert db.exists({"sha256": SHA256})
    # a record_type hint narrows the probe to that model's table
    record = db.get({"sha1": SHA1}, record_type="file")
    assert isinstance(record, File)
    with pytest.raises(ValueError, match="not a queryable field on file"):
        db.get({"name": "Jun"}, record_type="file")


def test_get_mapping_probe_with_unknown_type(db):
    with pytest.raises(ValueError, match="unknown record type"):
        db.get({"type": "bogus"})
    with pytest.raises(ValueError, match="does not match"):
        db.get({"type": "person"}, record_type="text")


def test_get_and_exists_reject_bad_by_fields(db):
    db.put(Person(id="p1", name="Jun"))
    with pytest.raises(ValueError, match="not a queryable field on person"):
        db.get("p1", record_type="person", by="sha1")
    with pytest.raises(ValueError, match="not a queryable field on person"):
        db.exists(Person(name="Jun"), by="extra")
    with pytest.raises(ValueError, match="no stored record type has a queryable field"):
        db.get("p1", by="bogus")
    with pytest.raises(ValueError, match="no stored record type has a queryable field"):
        db.exists({"bogus": "x"})


def test_get_probes_skip_empty_fields(db):
    db.put(File(id=ID, size=3, sha256=SHA256, sha1=SHA1, md5=MD5))

    record = db.get(File(sha256="", sha1=SHA1))  # sha256 skipped, sha1 matches
    assert isinstance(record, File)
    assert record.id == ID
    assert db.get(File(sha256="", sha1="", md5="")) is None


def test_get_probes_stored_models_in_order_until_first_match(db):
    db.put(Text(id="t1", title="x"))
    db.put(Person(id="p1", name="Jun"))

    statements: list[str] = []
    db.conn.set_trace_callback(statements.append)
    try:
        assert db.get("p1") is not None
    finally:
        db.conn.set_trace_callback(None)
    # texts, videos, sounds are probed and miss, persons hits; nothing after
    assert len(statements) == 4


def test_get_short_circuits_on_the_id_probe(db):
    db.put(File(id=ID, size=3, sha256=SHA256, sha1=SHA1, md5=MD5))

    statements: list[str] = []
    db.conn.set_trace_callback(statements.append)
    try:
        record = db.get(File(id=ID, sha256=SHA256, sha1=SHA1, md5=MD5))
    finally:
        db.conn.set_trace_callback(None)
    assert isinstance(record, File)
    # the id probe hit: sha256/sha1/md5 probes never ran
    assert len(statements) == 1
    assert 'WHERE "id" = ' in statements[0]


def test_get_mapping_probe_rejects_mapping_values(db):
    db.put(Person(id="p1", name="Jun"))
    with pytest.raises(ValueError, match="not a filter value"):
        db.get({"type": "person", "identifiers": {"wikidata:Q42": 1}})


def test_get_by_scalar_int_field(db):
    db.put(File(id=ID, size=3, sha256=SHA256, sha1=SHA1, md5=MD5))

    assert db.exists(File(id="", size=3), by="size")
    record = db.get(File(id="", size=3), by="size")
    assert isinstance(record, File)
    assert record.id == ID


def test_put_file_with_missing_id_fails_the_check(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.put({"type": "file", "size": 3})


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


def test_validate_reports_duplicate_sha256_but_not_sha1_or_md5(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        meta.put(File(id=ID, size=1, sha256=SHA256))
        meta.put(File(id=f"sha256:{'d' * 64}", size=1, sha256=SHA256, sha1=SHA1))
        meta.put(File(id=f"sha256:{'e' * 64}", size=1, sha1=SHA1, md5=MD5))

        issues = meta.validate()

    assert f"duplicate sha256 '{SHA256}' in 'files'" in issues
    assert not any("sha1" in issue for issue in issues)
    assert not any("md5" in issue for issue in issues)


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

    assert count == 7


def test_migrations_create_expected_indexes(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        indexes = {
            row[0]
            for row in meta.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }

    assert {
        "files_created",
        "persons_created",
        "texts_created",
        "software_created",
        "videos_created",
        "sounds_created",
        "notes_created",
        "files_sha256",
        "files_sha1",
        "files_md5",
    } <= indexes


def test_files_table_has_hash_columns(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        columns = {row[1] for row in meta.conn.execute("PRAGMA table_info(files)")}

    assert {"sha256", "sha1", "md5"} <= columns


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
    stored = db.put(File(id=ID, size=3, sha256=SHA256, sha1=SHA1, md5=MD5))

    assert stored.slug == ID
    record = db.get(ID)
    assert isinstance(record, File)
    assert record == stored
    assert record.sha256 == SHA256
    assert record.sha1 == SHA1
    assert record.md5 == MD5
    assert record.identifiers == []


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
    assert [r.id for r in db.find(Text, sort="created_at")] == ["t2", "t1"]
    assert [r.id for r in db.find(Text, sort="-created_at")] == ["t1", "t2"]
    assert [r.id for r in db.find(Text, sort="updated_at")] == ["t1", "t2"]
    assert [r.id for r in db.find(Text, sort="-updated_at")] == ["t2", "t1"]
    assert [r.id for r in db.find(Text)] == ["t2", "t1"]  # default: -updated_at


def test_find_sort_tie_breaks_on_id(db):
    db.put(Text(id="t2", title="two"))
    db.put(Text(id="t1", title="one"))
    db.conn.execute("UPDATE texts SET created_at = '2000-01-01T00:00:00Z'")

    assert [r.id for r in db.find(Text, sort="created_at")] == ["t1", "t2"]
    assert [r.id for r in db.find(Text, sort="-created_at")] == ["t2", "t1"]


def test_find_sort_works_across_types(db):
    db.put(Text(id="t1", title="one"))
    db.put(Person(id="p1", name="Jun"))

    assert [r.id for r in db.find(sort="id")] == ["p1", "t1"]
    assert [r.id for r in db.find(sort="-id")] == ["t1", "p1"]


def test_find_sort_with_where_filter(db):
    db.put(Person(id="p1", name="A", identifiers=["wikidata:Q1"]))
    db.put(Person(id="p2", name="B", identifiers=["wikidata:Q2"]))

    assert [
        r.id
        for r in db.find(Person, sort="id", where={"identifiers": ["wikidata:Q1", "wikidata:Q2"]})
    ] == [
        "p1",
        "p2",
    ]


def test_find_sort_lists_known_columns_on_error(db):
    with pytest.raises(ValueError, match="known columns: id, slug"):
        db.find(Text, sort="bogus")


def test_find_sorts_by_scalar_field(db):
    db.put(Text(id="t1", title="beta"))
    db.put(Text(id="t2", title="alpha"))

    assert [r.title for r in db.find(Text, sort="title")] == ["alpha", "beta"]
    assert [r.title for r in db.find(Text, sort="-title")] == ["beta", "alpha"]


def test_find_sorts_by_scalar_number(db):
    db.put(Video(id="v2", duration=4800))
    db.put(Video(id="v1", duration=240))

    assert [r.id for r in db.find(Video, sort="duration")] == ["v1", "v2"]
    assert [r.id for r in db.find(Video, sort="-duration")] == ["v2", "v1"]


def test_find_scalar_sort_tie_breaks_on_id(db):
    db.put(Text(id="t2", title="same"))
    db.put(Text(id="t1", title="same"))

    assert [r.id for r in db.find(Text, sort="title")] == ["t1", "t2"]
    assert [r.id for r in db.find(Text, sort="-title")] == ["t2", "t1"]


def test_find_sort_narrows_to_models_carrying_the_column(db):
    db.put(Text(id="t1", title="beta"))
    db.put(Person(id="p1", name="Jun"))

    assert [r.id for r in db.find(sort="title")] == ["t1"]
    assert [r.id for r in db.find(sort="name")] == ["p1"]


def test_find_rejects_sort_the_model_lacks(db):
    with pytest.raises(ValueError, match="unknown sort"):
        db.find(Person, sort="title")
    with pytest.raises(ValueError, match="unknown sort"):
        db.find(sort="bogus")
    # a column some other type carries is still unknown to this type
    with pytest.raises(ValueError, match="unknown sort"):
        db.find(Person, sort="duration")


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
        "0005_video.sql",
        "0006_sound.sql",
        "0007_note.sql",
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
    assert count == 7


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


@pytest.mark.parametrize("migrations", ["missing-dir", "empty-dir"], ids=["missing", "empty"])
def test_connect_skips_migration_without_sql_files(tmp_path, migrations):
    """A missing or empty migrations dir connects fine and builds no tables."""
    directory = tmp_path / migrations
    if migrations == "empty-dir":
        directory.mkdir()

    with MetaDB(tmp_path / "records.db", migrations_dir=directory) as db:
        tables = db.conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]

    assert tables == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_migrate_on_unwritable_database_fails_cleanly(tmp_path):
    """The WAL pragma failure is tolerated; the first real write is not."""
    path = tmp_path / "records.db"
    path.write_text("", encoding="utf-8")
    path.chmod(0o444)
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "0007_extra.sql").write_text(
        "CREATE TABLE IF NOT EXISTS extra (id TEXT);\n", encoding="utf-8"
    )

    with (
        MetaDB(path, auto_migrate=False, migrations_dir=extra) as db,
        pytest.raises(sqlite3.OperationalError),
    ):
        db.migrate()


def test_close_without_context_manager_persists_commits(tmp_path):
    path = tmp_path / "records.db"
    db = MetaDB(path)
    try:
        db.put(Person(id="p1", name="Jun"))
        db.commit()
    finally:
        db.close()

    with MetaDB(path) as meta:
        assert meta.get("p1") is not None


# --- model layout: unique / array_fields -------------------------------------


def test_put_mapping_null_json_fields_stored_as_empty(db):
    db.put({"id": "n1", "type": "note", "extra": None, "subject_ids": None})

    record = db.get("n1")

    assert isinstance(record, Note)
    assert record.extra == {}
    assert record.subject_ids == []


def test_find_on_tableless_registered_model_is_empty(db):
    """A registered model without a table is skipped, not an error."""

    @record_model
    class Tableless(Record):
        type: Literal["tableless"] = "tableless"

    assert db.find(Tableless) == []
    assert db.find("tableless") == []


def test_model_derives_unique_and_array_fields():
    assert Person.unique == ("id", "slug", "identifiers")
    assert File.unique == ("id", "slug", "sha256", "sha1", "md5")
    assert "identifiers" in Person.array_fields
    assert "occupations" in Person.array_fields
    assert "extra" in Person.json_fields
    assert "extra" not in Person.array_fields
    assert File.array_fields == ("identifiers", "locations")
    assert File.scalar_fields == ("size", "sha256", "sha1", "md5")


def test_record_model_rejects_unqueryable_unique():
    with pytest.raises(ValueError, match="is not queryable"):

        @record_model
        class BadUnique(Record):
            unique: ClassVar[tuple[str, ...]] = ("extra",)

    with pytest.raises(ValueError, match="is not queryable"):

        @record_model
        class MissingUnique(Record):
            unique: ClassVar[tuple[str, ...]] = ("nope",)


def test_file_probe_instances_get_empty_ids():
    assert File().id == ""
    assert File(sha1=SHA1).id == ""
