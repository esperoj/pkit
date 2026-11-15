"""Shared Wikidata entity builders for integration tests.

Minimal statement factories plus the canonical fixture entities used by the
load-URL pipeline tests. QIDs in the Q900xxx range are invented; real QIDs
(Q42, Q82050, ...) keep the shapes of real items without asserting live data.
"""

import json


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


def _mono_stmt(prop: str, lang: str, text: str) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {"type": "monolingualtext", "value": {"language": lang, "text": text}},
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


def _quantity_stmt(prop: str, amount: str) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {"type": "quantity", "value": {"amount": amount, "unit": "1"}},
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


def film_entity() -> dict:
    """A film-shaped (P31 Q11424) entity; QIDs are test fixtures, not real items."""
    return {
        "id": "Q900001",
        "labels": {"en": {"value": "Ghost in the Shell"}},
        "descriptions": {"en": {"value": "1995 animated film"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q11424")],
            "P577": [_time_stmt("P577", "+1995-11-18T00:00:00Z")],
            "P407": [_item_stmt("P407", "Q5287")],
            "P57": [_item_stmt("P57", "Q900010")],
            "P272": [_item_stmt("P272", "Q900011")],
            "P136": [_item_stmt("P136", "Q900012")],
            "P2047": [_quantity_stmt("P2047", "+4800")],
            "P345": [_str_stmt("P345", "tt0000001")],
        },
    }


def song_entity() -> dict:
    """A song-shaped (P31 Q7366) entity; QIDs are test fixtures, not real items."""
    return {
        "id": "Q900002",
        "labels": {"en": {"value": "Unravel"}},
        "descriptions": {"en": {"value": "2014 song"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q7366")],
            "P577": [_time_stmt("P577", "+2014-07-23T00:00:00Z")],
            "P175": [_item_stmt("P175", "Q900020")],
            "P264": [_item_stmt("P264", "Q900021")],
            "P136": [_item_stmt("P136", "Q900022")],
            "P2047": [_quantity_stmt("P2047", "+240")],
            "P436": [_str_stmt("P436", "0b0c1b1a-2d3e-4f5a-6b7c-8d9e0f1a2b3c")],
            "P1243": [_str_stmt("P1243", "XX0000000000")],
        },
    }


def film_labels() -> str:
    """One batched label warm covering the film entity's referenced QIDs."""
    return json.dumps(
        {
            "entities": {
                "Q5287": {"id": "Q5287", "labels": {"en": {"value": "Japanese"}}},
                "Q900010": {"id": "Q900010", "labels": {"en": {"value": "Mamoru Oshii"}}},
                "Q900011": {"id": "Q900011", "labels": {"en": {"value": "Production I.G"}}},
                "Q900012": {"id": "Q900012", "labels": {"en": {"value": "science fiction"}}},
            }
        }
    )
