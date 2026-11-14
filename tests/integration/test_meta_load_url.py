"""The meta load URL pipeline: CLI -> source -> sqlite, with only the wire faked."""

import uuid

import pytest

from pkit.lib.cli import EXIT_OK, EXIT_TEMPFAIL
from pkit.meta import load
from pkit.meta.cli import main as meta_main
from pkit.meta.db import MetaDB
from pkit.meta.models import Book, Person
from pkit.meta.wikidata import API_URL, WikidataSource

QID_STATEMENT = {
    "mainsnak": {
        "snaktype": "value",
        "property": "P31",
        "datavalue": {"type": "wikibase-entityid", "value": {"entity-type": "item", "id": "Q5"}},
    },
    "rank": "normal",
}
BIRTH_STATEMENT = {
    "mainsnak": {
        "snaktype": "value",
        "property": "P569",
        "datavalue": {
            "type": "time",
            "value": {"time": "+1952-03-11T00:00:00Z", "precision": 11},
        },
    },
    "rank": "normal",
}
VIAF_STATEMENT = {
    "mainsnak": {
        "snaktype": "value",
        "property": "P214",
        "datavalue": {"type": "string", "value": "113230702"},
    },
    "rank": "normal",
}
BOOK_TYPE_STATEMENT = {
    "mainsnak": {
        "snaktype": "value",
        "property": "P31",
        "datavalue": {"type": "wikibase-entityid", "value": {"entity-type": "item", "id": "Q571"}},
    },
    "rank": "normal",
}
AUTHOR_STATEMENT = {
    "mainsnak": {
        "snaktype": "value",
        "property": "P50",
        "datavalue": {"type": "wikibase-entityid", "value": {"entity-type": "item", "id": "Q42"}},
    },
    "rank": "normal",
}


def entity(*, name: str = "Douglas Adams") -> dict:
    """A minimal human (Q5) entity the Person plan can map."""
    return {
        "id": "Q42",
        "labels": {"en": {"value": name}},
        "descriptions": {"en": {"value": "writer"}},
        "claims": {
            "P31": [QID_STATEMENT],
            "P569": [BIRTH_STATEMENT],
            "P214": [VIAF_STATEMENT],
        },
    }


def book_entity() -> dict:
    """A minimal Book entity referencing Q42 as author."""
    return {
        "id": "Q187655",
        "labels": {"en": {"value": "The Hitchhiker's Guide to the Galaxy"}},
        "claims": {"P31": [BOOK_TYPE_STATEMENT], "P50": [AUTHOR_STATEMENT]},
    }


@pytest.fixture
def wikidata(fake_http, monkeypatch):
    """Point pkit.meta.load's sources at a WikidataSource with a fake transport."""
    monkeypatch.setattr("pkit.meta.load.SOURCES", (lambda: WikidataSource(http=fake_http),))
    return fake_http


def test_load_imports_wikidata_person_with_uuid_and_identifier(wikidata, db_path):
    wikidata.on(API_URL, json={"entities": {"Q42": entity()}})

    count = load.load("wikidata:Q42", db_path=db_path)

    assert count == 1
    with MetaDB(db_path) as meta:
        assert meta.get("wikidata:Q42") is None
        matches = meta.find(Person, identifier="wikidata:Q42")
    assert len(matches) == 1
    record = matches[0]
    assert isinstance(record, Person)
    assert uuid.UUID(record.id).version == 4
    assert "wikidata:Q42" in record.identifiers
    assert "viaf:113230702" in record.identifiers
    assert record.name == "Douglas Adams"
    assert record.birth_date == "1952-03-11"
    request = wikidata.requests[0]
    assert request["method"] == "GET"
    assert request["url"] == API_URL
    assert request["params"]["ids"] == "Q42"
    assert request["params"]["format"] == "json"


def test_load_skips_existing_unless_refresh(wikidata, db_path):
    wikidata.enqueue(json={"entities": {"Q42": entity(name="Douglas Adams")}})
    wikidata.enqueue(json={"entities": {"Q42": entity(name="Douglas Noel Adams")}})

    assert load.load("https://www.wikidata.org/wiki/Q42", db_path=db_path) == 1
    assert len(wikidata.requests) == 1
    with MetaDB(db_path) as meta:
        first = meta.find(Person, identifier="wikidata:Q42")[0]

    # Second load skips network and preserves the existing record.
    assert load.load("wikidata:Q42", db_path=db_path) == 0
    assert len(wikidata.requests) == 1

    # refresh=True re-fetches, updates fields, and keeps the same UUID.
    assert load.load("wikidata:Q42", db_path=db_path, refresh=True) == 1
    assert len(wikidata.requests) == 2
    with MetaDB(db_path) as meta:
        rows = meta.find(Person, identifier="wikidata:Q42")
    assert len(rows) == 1
    assert rows[0].id == first.id
    assert isinstance(rows[0], Person)
    assert rows[0].name == "Douglas Noel Adams"


def test_load_book_skips_already_imported_author(wikidata, db_path):
    wikidata.enqueue(json={"entities": {"Q42": entity()}})
    wikidata.enqueue(json={"entities": {"Q187655": book_entity()}})

    assert load.load("wikidata:Q42", db_path=db_path) == 1
    assert load.load("wikidata:Q187655", db_path=db_path) == 1
    assert len(wikidata.requests) == 2  # Q42 was not fetched a second time

    with MetaDB(db_path) as meta:
        author = meta.find(Person, identifier="wikidata:Q42")[0]
        book = meta.find(Book, identifier="wikidata:Q187655")[0]
    assert isinstance(book, Book)
    assert book.author_ids == [author.id]


def test_load_merges_existing_record_matched_by_other_identifier(wikidata, db_path):
    with MetaDB(db_path) as meta:
        manual = meta.put(Person(name="D. Adams", identifiers=["viaf:113230702"]))
    wikidata.on(API_URL, json={"entities": {"Q42": entity()}})

    assert load.load("wikidata:Q42", db_path=db_path) == 1
    with MetaDB(db_path) as meta:
        rows = meta.find(Person)
    assert len(rows) == 1
    assert rows[0].id == manual.id
    assert rows[0].identifiers == ["viaf:113230702", "wikidata:Q42"]


def test_load_skips_redirect_to_existing_record(wikidata, db_path):
    wikidata.enqueue(json={"entities": {"Q42": entity()}})
    wikidata.enqueue(json={"entities": {"Q42": entity(name="Changed")}})

    assert load.load("wikidata:Q42", db_path=db_path) == 1
    assert load.load("wikidata:Q99", db_path=db_path) == 0
    with MetaDB(db_path) as meta:
        person = meta.find(Person, identifier="wikidata:Q42")[0]
    assert isinstance(person, Person)
    assert person.name == "Douglas Adams"


def test_cli_load_refresh_flag(run_cli, wikidata, db_path):
    wikidata.enqueue(json={"entities": {"Q42": entity(name="First")}})
    wikidata.enqueue(json={"entities": {"Q42": entity(name="Updated")}})

    code, out, err = run_cli(meta_main, ["load", "wikidata:Q42"])
    assert code == EXIT_OK, err
    assert out.strip() == "1"

    code, out, err = run_cli(meta_main, ["load", "--refresh", "wikidata:Q42"])
    assert code == EXIT_OK, err
    assert out.strip() == "1"
    with MetaDB(db_path) as meta:
        person = meta.find(Person, identifier="wikidata:Q42")[0]
    assert isinstance(person, Person)
    assert person.name == "Updated"


def test_cli_load_http_error_maps_to_tempfail(run_cli, wikidata, db_path):
    wikidata.on(API_URL, status=500, text="upstream exploded")

    code, out, err = run_cli(meta_main, ["load", "wikidata:Q42"])

    assert code == EXIT_TEMPFAIL
    assert "HTTP 500" in err
    assert out == ""


def test_load_rejects_unordered_url(db_path):
    with pytest.raises(ValueError, match="unsupported source URL"):
        load.load("https://example.com/x", db_path=db_path)
