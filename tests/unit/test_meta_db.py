"""MetaDB: put/get/find/find_one/delete/validate over the record tables.

One generated round-trip per model covers every field; focused tests cover
the query contract (filters, sorts, deduplication probes) and the database
lifecycle (migrations, readonly, zero-write connects).
"""

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import ClassVar, Literal

import pytest

from pkit.lib import sqlite as lib_sqlite
from pkit.meta import dump, load
from pkit.meta.db import MetaDB
from pkit.meta.models import (
    File,
    Person,
    Record,
    Software,
    Sound,
    Text,
    Video,
    record_model,
)
from pkit.meta.serialize import from_dict, from_json

SHA256 = "a" * 64
SHA1 = "b" * 40
MD5 = "c" * 32
ID = f"sha256:{SHA256}"
MIGRATIONS = [
    "0001_files.sql",
    "0002_persons.sql",
    "0003_texts.sql",
    "0004_text_authors.sql",
    "0005_software.sql",
    "0006_videos.sql",
    "0007_sounds.sql",
]


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


# --- put / get ---------------------------------------------------------------


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


def test_put_mapping_null_json_fields_stored_as_empty(db):
    db.put({"id": "p1", "type": "person", "extra": None, "occupations": None})

    record = db.get("p1")

    assert isinstance(record, Person)
    assert record.extra == {}
    assert record.occupations == []


def test_put_file_with_missing_id_fails_the_check(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.put({"type": "file", "size": 3})


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


def test_get_missing_is_none(db):
    assert db.get("nope") is None
    assert db.get("") is None


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


# --- find --------------------------------------------------------------------


def test_find_filters_and_paginates(db):
    db.put(Text(id="t1", title="one"))
    db.put(Text(id="t2", title="two"))
    db.put(Person(id="p1", name="Jun"))

    assert {record.id for record in db.find(Text)} == {"t1", "t2"}
    assert [record.id for record in db.find("person")] == ["p1"]
    assert len(db.find(Text, limit=1)) == 1
    assert len(db.find(Text, limit=2, offset=1)) == 1


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

    assert [r.id for r in db.find(Person, where={"identifiers": "wikidata:Q42"})] == ["p1"]
    assert [r.id for r in db.find(Text, where={"identifiers": ["isbn13:9780", "nope"]})] == ["t1"]
    assert [r.id for r in db.find(Text, where={"genres": "dark fantasy"})] == ["t1"]
    assert [r.id for r in db.find(Software, where={"platforms": ["Linux", "Plan 9"]})] == ["s1"]
    assert [r.id for r in db.find(File, where={"locations": "ia:x"})] == [ID]
    assert db.find(Person, where={"identifiers": []}) == []
    assert db.find(Person, where={"identifiers": "missing"}) == []


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


def test_find_on_tableless_registered_model_is_empty(db):
    """A registered model without a table is skipped, not an error."""

    @record_model
    class Tableless(Record):
        type: Literal["tableless"] = "tableless"

    assert db.find(Tableless) == []
    assert db.find("tableless") == []


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


def test_find_rejects_sort_the_model_lacks(db):
    with pytest.raises(ValueError, match="unknown sort"):
        db.find(Person, sort="title")
    # a column some other type carries is still unknown to this type
    with pytest.raises(ValueError, match="unknown sort"):
        db.find(Person, sort="duration")


# --- find_one: the deduplication probe ----------------------------------------


def test_find_one_by_identifiers(db):
    db.put(Person(id="p1", name="Jun", identifiers=["wikidata:Q42", "viaf:113"]))

    record = db.find_one(Person, where={"identifiers": "wikidata:Q42"})
    assert isinstance(record, Person)
    assert record.id == "p1"
    assert db.find_one(Person, where={"identifiers": "viaf:999"}) is None


def test_find_one_by_hash_fields(db):
    db.put(File(id=ID, size=3, sha256=SHA256, sha1=SHA1, md5=MD5))

    for field, value in (("sha256", SHA256), ("sha1", SHA1), ("md5", MD5)):
        record = db.find_one(File, where={field: value})
        assert isinstance(record, File)
        assert record.id == ID
    assert db.find_one(File, where={"sha1": "0" * 40}) is None


def test_find_one_identifier_tokens_match_any(db):
    db.put(Software(id="s1", title="Abyss", identifiers=["igdb:abyss"]))

    record = db.find_one(Software, where={"identifiers": ["wikidata:Q1", "igdb:abyss"]})
    assert record is not None
    assert record.id == "s1"


def test_find_one_without_model_searches_every_table(db):
    db.put(Person(id="p1", name="Jun", identifiers=["wikidata:Q42"]))
    db.put(File(id=ID, size=3, sha256=SHA256, sha1=SHA1, md5=MD5))

    assert db.find_one(where={"identifiers": "wikidata:Q42"}).id == "p1"
    assert db.find_one(where={"sha1": SHA1}).id == ID
    assert db.find_one(where={"identifiers": "nope"}) is None
    # only models carrying every key are probed
    assert db.find_one(where={"name": "Jun"}).id == "p1"
    with pytest.raises(ValueError, match="no stored record type has a queryable field"):
        db.find_one(where={"bogus": "x"})


def test_find_one_empty_collection_matches_nothing(db):
    db.put(Person(id="p1", name="Jun"))

    assert db.find_one(Person, where={"identifiers": []}) is None


def test_find_where_scalar_single_value_wraps_to_one_item_list(db):
    db.put(File(id=ID, size=3, sha256=SHA256))

    assert [r.id for r in db.find(File, where={"size": 3})] == [ID]
    assert db.find(File, where={"size": 4}) == []


def test_find_one_on_tableless_model_is_none(db):
    @record_model
    class Tableless(Record):
        type: Literal["tableless"] = "tableless"

    assert db.find_one(Tableless, where={"identifiers": "x"}) is None


# --- delete / validate -------------------------------------------------------


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


# --- lifecycle: context, commit, close ----------------------------------------


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


def test_path_defaults_to_config(tmp_path, monkeypatch):
    path = tmp_path / "env.db"
    monkeypatch.setenv("PKIT_META_DB_PATH", str(path))

    with MetaDB() as meta:
        meta.put(Person(id="p1", name="Jun"))

    with MetaDB() as meta:
        assert meta.get("p1") is not None


# --- migrations ----------------------------------------------------------------


def test_migrations_apply_once(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path):
        pass
    with MetaDB(path) as meta:
        names = [row[0] for row in meta.conn.execute("SELECT name FROM _migrations")]

    assert names == MIGRATIONS


def test_migrate_reports_each_applied_name(tmp_path):
    path = tmp_path / "records.db"
    with lib_sqlite.connect(path) as conn:
        applied = lib_sqlite.migrate(conn, "pkit/meta/migrations")

    assert applied == MIGRATIONS
    with lib_sqlite.connect(path) as conn:
        assert lib_sqlite.migrate(conn, "pkit/meta/migrations") == []


def test_migrate_reports_only_new_files(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path):
        pass

    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "0000_baseline.sql").write_text(
        "CREATE TABLE IF NOT EXISTS baseline (id TEXT);\n", encoding="utf-8"
    )

    with lib_sqlite.connect(path) as conn:
        assert lib_sqlite.migrate(conn, extra) == ["0000_baseline.sql"]


def test_migrate_skips_concurrently_applied(tmp_path, monkeypatch):
    """A second migrator that raced ahead must not crash or double-apply."""
    path = tmp_path / "records.db"
    real_read_text = Path.read_text
    state = {"injected": False, "busy": False}

    def racing_read_text(self, *args, **kwargs):
        if self.name == "0001_files.sql" and not state["injected"] and not state["busy"]:
            state["busy"] = True
            try:
                with lib_sqlite.connect(path) as other:
                    lib_sqlite.migrate(other, "pkit/meta/migrations")
                state["injected"] = True
            finally:
                state["busy"] = False
        return real_read_text(self, *args, **kwargs)

    with lib_sqlite.connect(path) as conn:
        monkeypatch.setattr(Path, "read_text", racing_read_text)
        applied = lib_sqlite.migrate(conn, "pkit/meta/migrations")

    assert applied == []
    with lib_sqlite.connect(path, readonly=True) as conn:
        count = conn.execute("SELECT count(*) FROM _migrations").fetchone()[0]
    assert count == len(MIGRATIONS)


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
        "files_sha256",
        "files_sha1",
        "files_md5",
    } <= indexes


def test_files_table_has_hash_and_name_columns(tmp_path):
    path = tmp_path / "records.db"
    with MetaDB(path) as meta:
        columns = {row[1] for row in meta.conn.execute("PRAGMA table_info(files)")}

    assert {"name", "sha256", "sha1", "md5"} <= columns


def test_migrate_with_missing_directory_returns_empty(tmp_path):
    with lib_sqlite.connect(tmp_path / "records.db") as conn:
        assert lib_sqlite.migrate(conn, tmp_path / "no-such-dir") == []


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
    (extra / "0001_extra.sql").write_text(
        "CREATE TABLE IF NOT EXISTS extra (id TEXT);\n", encoding="utf-8"
    )

    with (
        lib_sqlite.connect(path) as conn,
        pytest.raises(sqlite3.OperationalError),
    ):
        lib_sqlite.migrate(conn, extra)


# --- readonly and zero-write connections ---------------------------------------


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


# --- dump / load round trip -----------------------------------------------------


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


def test_dump_orders_by_id_in_sqlite(tmp_path):
    db_file = tmp_path / "a.db"
    repo = tmp_path / "repo"
    with MetaDB(db_file) as meta:
        meta.put(Software(id="s2", title="B", kind="game"))
        meta.put(Software(id="s1", title="A", kind="game"))

    assert dump.dump(repo, db_path=db_file) == 2

    rows = json.loads((repo / "software.json").read_text())
    assert [row["id"] for row in rows] == ["s1", "s2"]


# --- model layout: unique / array_fields ----------------------------------------


def test_model_derives_unique_and_array_fields():
    assert Person.unique == ("id", "slug", "identifiers")
    assert File.unique == ("id", "slug", "sha256", "sha1", "md5")
    assert "identifiers" in Person.array_fields
    assert "occupations" in Person.array_fields
    assert "extra" in Person.json_fields
    assert "extra" not in Person.array_fields
    assert File.array_fields == ("identifiers", "locations")
    assert File.scalar_fields == ("name", "size", "sha256", "sha1", "md5")


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


# --- serialize: the record <-> JSON boundary ------------------------------------


@pytest.mark.parametrize(
    "payload",
    [{}, {"type": ""}, {"type": 42}, {"type": "alien"}],
    ids=["no-type", "empty-type", "non-str-type", "unknown-type"],
)
def test_from_dict_rejects_bad_type(payload):
    with pytest.raises(ValueError):
        from_dict(payload)


def test_from_dict_ignores_unknown_keys():
    record = from_dict({"type": "video", "id": "v1", "duration": 4800, "future": "field"})

    assert isinstance(record, Video)
    assert record.duration == 4800


def test_from_json_round_trip():
    payload = json.dumps({"type": "person", "id": "p1", "name": "Jun", "occupations": ["writer"]})

    assert from_json(payload) == Person(id="p1", name="Jun", occupations=["writer"])
