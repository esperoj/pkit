"""The meta load URL pipeline: CLI -> source -> sqlite, with only the wire faked."""

import uuid

import pytest

from pkit.lib.cli import EXIT_OK, EXIT_TEMPFAIL
from pkit.meta import load
from pkit.meta.cli import main as meta_main
from pkit.meta.db import MetaDB
from pkit.meta.models import Person, Software, Text
from pkit.meta.wikidata import API_URL, WikidataSource


def _item_stmt(prop: str, qid: str) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {
                "type": "wikibase-entityid",
                "value": {"entity-type": "item", "id": qid},
            },
        },
        "rank": "normal",
    }


def _str_stmt(prop: str, value: str) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {"type": "string", "value": value},
        },
        "rank": "normal",
    }


def _time_stmt(prop: str, iso_time: str, precision: int = 11) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {
                "type": "time",
                "value": {"time": iso_time, "precision": precision},
            },
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
            "P31": [_item_stmt("P31", "Q5")],
            "P569": [_time_stmt("P569", "+1952-03-11T00:00:00Z")],
            "P214": [_str_stmt("P214", "113230702")],
        },
    }


def text_entity() -> dict:
    """A minimal Text entity referencing Q42 as author."""
    return {
        "id": "Q187655",
        "labels": {"en": {"value": "The Hitchhiker's Guide to the Galaxy"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q571")],
            "P7937": [_item_stmt("P7937", "Q8261")],
            "P50": [_item_stmt("P50", "Q42")],
        },
    }


def game_entity() -> dict:
    """A Q82050-shaped video game entity."""
    return {
        "id": "Q82050",
        "labels": {"en": {"value": "Tales of the Abyss"}},
        "descriptions": {"en": {"value": "2005 video game"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q7889")],
            "P179": [_item_stmt("P179", "Q1193867")],
            "P178": [_item_stmt("P178", "Q1781791")],
            "P123": [_item_stmt("P123", "Q309996")],
            "P400": [_item_stmt("P400", "Q10680"), _item_stmt("P400", "Q203597")],
            "P136": [_item_stmt("P136", "Q744038")],
            "P577": [_time_stmt("P577", "+2005-12-15T00:00:00Z")],
            "P5794": [_str_stmt("P5794", "tales-of-the-abyss")],
            "P11688": [_str_stmt("P11688", "27617")],
            "P5290": [_str_stmt("P5290", "25771")],
        },
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


def test_load_text_skips_already_imported_author(wikidata, db_path):
    wikidata.enqueue(json={"entities": {"Q42": entity()}})
    wikidata.enqueue(json={"entities": {"Q187655": text_entity()}})

    assert load.load("wikidata:Q42", db_path=db_path) == 1
    assert load.load("wikidata:Q187655", db_path=db_path) == 1
    assert len(wikidata.requests) == 2  # Q42 was not fetched a second time

    with MetaDB(db_path) as meta:
        author = meta.find(Person, identifier="wikidata:Q42")[0]
        text = meta.find(Text, identifier="wikidata:Q187655")[0]
    assert isinstance(text, Text)
    assert text.kind == "novel"
    assert text.author_ids == [author.id]


def test_load_imports_wikidata_game_without_child_records(wikidata, db_path):
    wikidata.enqueue(json={"entities": {"Q82050": game_entity()}})
    wikidata.enqueue(
        json={
            "entities": {
                "Q10680": {"id": "Q10680", "labels": {"en": {"value": "PlayStation 2"}}},
                "Q1193867": {"id": "Q1193867", "labels": {"en": {"value": "Tales"}}},
                "Q1781791": {"id": "Q1781791", "labels": {"en": {"value": "Namco Tales Studio"}}},
                "Q203597": {"id": "Q203597", "labels": {"en": {"value": "Nintendo 3DS"}}},
                "Q309996": {"id": "Q309996", "labels": {"en": {"value": "Namco"}}},
                "Q744038": {
                    "id": "Q744038",
                    "labels": {"en": {"value": "role-playing video game"}},
                },
            }
        }
    )

    assert load.load("wikidata:Q82050", db_path=db_path) == 1
    assert len(wikidata.requests) == 2  # 1 entity fetch + 1 batched label warm

    with MetaDB(db_path) as meta:
        assert meta.find(Person) == []
        matches = meta.find(Software, identifier="wikidata:Q82050")
    assert len(matches) == 1
    game = matches[0]
    assert isinstance(game, Software)
    assert game.title == "Tales of the Abyss"
    assert game.kind == "game"
    assert game.publication_date == "2005-12-15"
    assert game.description == "2005 video game"
    assert game.series == ["Tales"]
    assert game.developers == ["Namco Tales Studio"]
    assert game.publishers == ["Namco"]
    assert game.platforms == ["PlayStation 2", "Nintendo 3DS"]
    assert game.genres == ["role-playing video game"]
    assert "igdb:tales-of-the-abyss" in game.identifiers
    assert "mobygames:27617" in game.identifiers
    assert "redump:25771" in game.identifiers


def test_load_imports_wikidata_software_and_prefers_game_over_text(wikidata, db_path):
    app = {
        "id": "Q1234",
        "labels": {"en": {"value": "Example Editor"}},
        "descriptions": {"en": {"value": "text editor"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q166142")],
            "P1324": [_str_stmt("P1324", "https://git.example.com/editor")],
            "P4655": [_str_stmt("P4655", "org.example.Editor")],
        },
    }
    vn = {
        "id": "Q5678",
        "labels": {"en": {"value": "Example VN"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q689445"), _item_stmt("P31", "Q7725634")],
            "P3180": [_str_stmt("P3180", "v42")],
        },
    }
    wikidata.enqueue(json={"entities": {"Q1234": app}})
    wikidata.enqueue(json={"entities": {"Q5678": vn}})

    assert load.load("wikidata:Q1234", db_path=db_path) == 1
    assert load.load("wikidata:Q5678", db_path=db_path) == 1

    with MetaDB(db_path) as meta:
        sw = meta.find(Software, identifier="wikidata:Q1234")[0]
        vn_rec = meta.find(Software, identifier="wikidata:Q5678")[0]
        assert meta.find(Text) == []
    assert isinstance(sw, Software)
    assert sw.kind == "software"
    assert sw.website == "https://git.example.com/editor"
    assert "flathub:org.example.Editor" in sw.identifiers
    assert isinstance(vn_rec, Software)
    assert vn_rec.kind == "game"
    assert "vndb:v42" in vn_rec.identifiers


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
