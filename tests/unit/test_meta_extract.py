"""Extractor contracts: the public combinators of extract.py.

Plan authors compose claim/claims/kind_of/... — that composition is the
public surface under test. Cases are tables of (entity, extractor,
expected), so each branch (ranks, datavalue types, time precisions,
language fallbacks, malformed claims) is one table row, not one test.
"""

import pytest

from pkit.meta.wikidata.extract import (
    EntityView,
    claim,
    claims,
    ext_ids,
    has_claim,
    has_class,
    item_ids,
    kind_of,
    person_name,
    work_title,
)


def item_stmt(prop: str, qid: str, *, rank: str = "normal", entity_type: str = "item") -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {
                "type": "wikibase-entityid",
                "value": {"entity-type": entity_type, "id": qid},
            },
        },
        "rank": rank,
    }


def str_stmt(prop: str, value: str, *, rank: str = "normal") -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {"type": "string", "value": value},
        },
        "rank": rank,
    }


def mono_stmt(prop: str, lang: str, text: str) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {"type": "monolingualtext", "value": {"language": lang, "text": text}},
        },
        "rank": "normal",
    }


def time_stmt(prop: str, iso: str, precision: int = 11) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {"type": "time", "value": {"time": iso, "precision": precision}},
        },
        "rank": "normal",
    }


def view(raw: dict, labels: dict | None = None, **kwargs) -> EntityView:
    return EntityView(raw, labels or {}, **kwargs)


# --- claims: ranks and datavalue types ---------------------------------------


def test_claims_drop_deprecated_and_prefer_preferred():
    raw = {
        "claims": {
            "P50": [
                item_stmt("P50", "Q1", rank="deprecated"),
                item_stmt("P50", "Q2", rank="normal"),
            ]
        }
    }
    assert claims("P50")(view(raw, {"Q2": {"en": "Second"}})) == ["Second"]

    raw["claims"]["P50"].append(item_stmt("P50", "Q3", rank="preferred"))
    assert claims("P50")(view(raw, {"Q2": {"en": "Second"}, "Q3": {"en": "Third"}})) == ["Third"]


def test_claims_decode_every_datavalue_type():
    raw = {
        "claims": {
            "P999": [
                {"mainsnak": {"snaktype": "novalue"}},  # no datavalue: skipped
                {"mainsnak": {"snaktype": "value", "property": "P999"}},  # datavalue missing
                item_stmt("P999", "P31", entity_type="property"),  # non-item: no label
                str_stmt("P999", "plain"),
                time_stmt("P999", "+2001-02-03T00:00:00Z"),
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P999",
                        "datavalue": {"type": "quantity", "value": {"amount": "+7"}},
                    },
                    "rank": "normal",
                },
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P999",
                        "datavalue": {"type": "globecoordinate", "value": {"longitude": 1}},
                    },
                    "rank": "normal",
                },
                mono_stmt("P999", "en", "mono"),
            ]
        }
    }

    assert claims("P999")(view(raw)) == ["plain", "2001-02-03", "7", "mono"]


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_claims_lang_filter_keeps_only_that_language(lang):
    raw = {
        "claims": {
            "P1476": [
                mono_stmt("P1476", "en", "English"),
                mono_stmt("P1476", "fr", "Français"),
                mono_stmt("P1476", "", "no language"),  # empty language: dropped
            ]
        }
    }

    assert claims("P1476", lang=lang)(view(raw)) == ["English" if lang == "en" else "Français"]


def test_work_title_skips_malformed_monolinguals():
    raw = {
        "claims": {
            "P1476": [
                {"mainsnak": {"snaktype": "novalue"}},  # no datavalue
                str_stmt("P1476", "string, not monolingual"),
                mono_stmt("P1476", "", "empty language"),
                mono_stmt("P1476", "en", ""),  # empty text
                mono_stmt("P1476", "en", "Real Title"),
            ]
        },
        "labels": {"en": {"value": "Fallback"}},
    }

    assert work_title()(view(raw)) == "Real Title"


@pytest.mark.parametrize("claims_map", ["junk", {"P31": "junk"}, {"P31": [42]}])
def test_malformed_claims_containers_are_skipped(claims_map):
    assert claims("P31")(view({"claims": claims_map})) == []


def test_claim_without_values_is_empty():
    assert claim("P569")(view({})) == ""


# --- time precisions ----------------------------------------------------------


@pytest.mark.parametrize(
    ("iso", "precision", "expected"),
    [
        ("+1952-03-11T00:00:00Z", 11, "1952-03-11"),
        ("+1952-03-11T00:00:00Z", 10, "1952-03"),  # month precision
        ("+1952-03-11T00:00:00Z", 9, "1952"),  # year precision
        ("+1952-03-00T00:00:00Z", 11, "1952-03"),  # day 00
        ("-0044-03-15T00:00:00Z", 11, "-0044-03-15"),  # BC year keeps its sign
        ("junk", 11, "junk"),  # no date parts: stays raw
    ],
)
def test_time_precision_formats(iso, precision, expected):
    raw = {"claims": {"P577": [time_stmt("P577", iso, precision)]}}

    assert claim("P577")(view(raw)) == expected


# --- item references: item_ids, has_class, has_claim, kind_of ------------------


def test_item_ids_skip_non_item_entities():
    raw = {
        "claims": {
            "P31": [
                {"mainsnak": {"snaktype": "novalue"}},  # no value at all
                item_stmt("P31", "Q5"),
                item_stmt("P31", "P31", entity_type="property"),
            ]
        }
    }

    assert item_ids("P31")(view(raw)) == ["Q5"]
    assert has_class("Q5")(view(raw))
    assert not has_class("P31")(view(raw))
    assert has_claim("P31")(view(raw))  # any concrete claim value
    assert not has_claim("P31")(view({"claims": {"P31": [{"mainsnak": {"snaktype": "novalue"}}]}}))


def test_kind_of_skips_unmapped_qids():
    raw = {"claims": {"P31": [item_stmt("P31", "Q999"), item_stmt("P31", "Q11424")]}}

    assert kind_of({"Q11424": "movie"}, default="video")(view(raw)) == "movie"
    assert kind_of({})(view(raw)) == ""  # nothing maps: default stays ""


# --- external identifiers ------------------------------------------------------


def test_ext_ids_skip_non_strings_and_duplicates():
    raw = {
        "claims": {
            "P345": [
                {"mainsnak": {"snaktype": "novalue"}},
                str_stmt("P345", "tt1"),
                str_stmt("P345", "tt1"),
                item_stmt("P345", "Q1"),
            ]
        }
    }

    assert ext_ids(P345="imdb")(view(raw)) == ["imdb:tt1"]


# --- label layers and language fallbacks ---------------------------------------


def test_name_layers_skip_malformed_entries():
    raw = {"labels": {"en": "raw-string", "fr": {"value": ""}, "de": {"value": "ok"}}}

    assert person_name()(EntityView(raw, {}, lang="de")) == "ok"


def test_name_falls_back_to_any_available_language():
    raw = {"labels": {"fr": {"value": "Seulement français"}}}

    assert person_name()(view(raw)) == "Seulement français"


def test_name_matches_regional_language_tags():
    # language codes are lowercase on the wire (client normalizes requests)
    both = {"labels": {"fr": {"value": "français"}, "fr-ca": {"value": "canadien"}}}
    base_only = {"labels": {"fr": {"value": "français"}}}

    assert person_name()(view(both, lang="fr-ca")) == "canadien"
    assert person_name()(view(base_only, lang="fr-ca")) == "français"  # base tag fallback


def test_label_of_uncached_qid():
    assert EntityView({}, {}).label("Q1") == ""  # no collector: just empty

    missing: set[str] = set()
    EntityView({}, {}, missing).label("Q1")

    assert missing == {"Q1"}  # collector records it for batch warming


def test_label_with_cached_empty_value_falls_through():
    assert EntityView({}, {"Q1": {"en": ""}}).label("Q1") == ""
