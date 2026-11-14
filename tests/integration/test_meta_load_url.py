"""The meta load URL pipeline: CLI -> source -> sqlite, with only the wire faked."""

import pytest

from pkit.lib.cli import EXIT_TEMPFAIL
from pkit.meta import load
from pkit.meta.cli import main as meta_main
from pkit.meta.db import MetaDB
from pkit.meta.models import Person
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


def entity() -> dict:
    """A minimal human (Q5) entity the Person plan can map."""
    return {
        "id": "Q42",
        "labels": {"en": {"value": "Douglas Adams"}},
        "descriptions": {"en": {"value": "writer"}},
        "claims": {"P31": [QID_STATEMENT], "P569": [BIRTH_STATEMENT]},
    }


@pytest.fixture
def wikidata(fake_http, monkeypatch):
    """Point pkit.meta.load's sources at a WikidataSource with a fake transport."""
    monkeypatch.setattr("pkit.meta.load.SOURCES", (lambda: WikidataSource(http=fake_http),))
    return fake_http


def test_load_imports_wikidata_person(wikidata, db_path):
    wikidata.on(API_URL, json={"entities": {"Q42": entity()}})

    count = load.load("wikidata:Q42", db_path=db_path)

    assert count == 1
    with MetaDB(db_path) as meta:
        record = meta.get("wikidata:Q42")
    assert isinstance(record, Person)
    assert record.name == "Douglas Adams"
    assert record.birth_date == "1952-03-11"
    # the one wbgetentities request carried the key and asked for JSON
    request = wikidata.requests[0]
    assert request["method"] == "GET"
    assert request["url"] == API_URL
    assert request["params"]["ids"] == "Q42"
    assert request["params"]["format"] == "json"


def test_load_is_idempotent(wikidata, db_path):
    wikidata.on(API_URL, json={"entities": {"Q42": entity()}})

    assert load.load("https://www.wikidata.org/wiki/Q42", db_path=db_path) == 1
    assert load.load("wikidata:Q42", db_path=db_path) == 1

    with MetaDB(db_path) as meta:
        assert len(meta.find(Person)) == 1


def test_cli_load_http_error_maps_to_tempfail(run_cli, wikidata, db_path):
    wikidata.on(API_URL, status=500, text="upstream exploded")

    code, out, err = run_cli(meta_main, ["load", "wikidata:Q42"])

    assert code == EXIT_TEMPFAIL
    assert "HTTP 500" in err
    assert out == ""


def test_load_rejects_unsupported_url(db_path):
    with pytest.raises(ValueError, match="unsupported source URL"):
        load.load("https://example.com/x", db_path=db_path)
