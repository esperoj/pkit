"""load() edge cases through the public pipeline: sources, repos, errors.

A tiny FakeSource speaks the Source protocol with dict items, so the import
loop's contract (missing and unsupported items, link cycles, redirects,
unshapable children) is exercised end to end. Malformed Wikidata responses
cover the client's wire handling the same way — only the transport is fake.
"""

import json

import pytest
from wikidata_entities import _item_stmt, _str_stmt, entity, text_entity

from pkit.meta import load
from pkit.meta.db import MetaDB
from pkit.meta.models import Person, Text
from pkit.meta.source import LinkRef, Plan
from pkit.meta.wikidata import API_URL, WikidataSource

# --- the fake source ----------------------------------------------------------


class FakeSource:
    """Source protocol on plain dicts: fetch is a table lookup, plans are data."""

    name = "fake"

    def __init__(self, items: dict, plans: tuple[Plan, ...]) -> None:
        self.items = items
        self.plans = plans

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def match(self, url: str):
        return url.removeprefix("fake:") if url.startswith("fake:") else None

    def fetch(self, key, *, expected=None, lang="auto"):
        return self.items.get(key)


def fake_plans(*, person_plan: bool = True, link_model=Person) -> tuple[Plan, ...]:
    """A Text plan whose author links can point at anything."""
    text = Plan(
        model=Text,
        when=lambda item: item.get("shape") == "text",
        key=lambda item: item.get("id", ""),
        fields={
            "title": lambda item: item.get("title", ""),
            "extra": lambda item: item.get("extra", {}),
        },
        links={"author_ids": LinkRef(lambda item: item.get("authors", []), model=link_model)},
    )
    plans: tuple[Plan, ...] = (text,)
    if person_plan:
        person = Plan(
            model=Person,
            when=lambda item: item.get("shape") == "person",
            key=lambda item: item.get("id", ""),
            fields={"name": lambda item: item.get("name", "")},
        )
        plans = (text, person)
    return plans


@pytest.fixture
def fake_source(monkeypatch):
    def _install(items: dict, plans: tuple[Plan, ...] | None = None) -> FakeSource:
        source = FakeSource(items, plans if plans is not None else fake_plans())
        monkeypatch.setattr(load, "SOURCES", (lambda: source,))
        return source

    return _install


def _text_by_source_id(db_path, key: str) -> Text:
    with MetaDB(db_path) as db:
        record = db.get(f"fake:{key}", by="identifiers")
    assert isinstance(record, Text)
    return record  # narrowed by the assert above


# --- root item failures ---------------------------------------------------------


def test_load_missing_item_raises(fake_source, db_path):
    fake_source({})

    with pytest.raises(ValueError, match="fake item 'gone' not found"):
        load.load("fake:gone", db_path=db_path)


def test_load_unsupported_item_raises(fake_source, db_path):
    fake_source({"Q1": {"shape": "universe"}})

    with pytest.raises(ValueError, match="unsupported fake item 'Q1'"):
        load.load("fake:Q1", db_path=db_path)


def test_load_unsupported_wikidata_item_raises(wikidata, db_path):
    wikidata.enqueue(json={"entities": {"Q1": {"id": "Q1", "labels": {"en": {"value": "thing"}}}}})

    with pytest.raises(ValueError, match="unsupported wikidata item 'Q1'"):
        load.load("wikidata:Q1", db_path=db_path)


# --- link edge cases --------------------------------------------------------------


def test_load_missing_link_child_is_skipped(fake_source, db_path):
    fake_source({"Q1": {"shape": "text", "id": "Q1", "title": "T", "authors": ["ghost"]}})

    assert load.load("fake:Q1", db_path=db_path) == 1

    assert _text_by_source_id(db_path, "Q1").author_ids == []


def test_load_unshapable_link_child_is_skipped(fake_source, db_path):
    """No Person plan exists: the author link resolves to nothing, not a crash."""
    items = {
        "Q1": {"shape": "text", "id": "Q1", "title": "T", "authors": ["Q2"]},
        "Q2": {"shape": "alien", "id": "Q2"},
    }
    fake_source(items, fake_plans(person_plan=False))

    assert load.load("fake:Q1", db_path=db_path) == 1

    assert _text_by_source_id(db_path, "Q1").author_ids == []


def test_load_expected_model_wins_over_item_shape(fake_source, db_path):
    """An author is imported as a Person even when its item looks like a text."""
    items = {
        "Q1": {"shape": "text", "id": "Q1", "title": "T", "authors": ["Q2"]},
        "Q2": {"shape": "text", "id": "Q2", "title": "Not a person"},
    }
    fake_source(items)

    assert load.load("fake:Q1", db_path=db_path) == 2

    with MetaDB(db_path) as db:
        author = db.get("fake:Q2", by="identifiers")
    assert isinstance(author, Person)
    assert author.name == ""


def test_load_link_keys_empty_and_duplicate_entries(fake_source, db_path):
    items = {
        "Q1": {"shape": "text", "id": "Q1", "title": "T", "authors": ["", "Q2", "Q2"]},
        "Q2": {"shape": "person", "id": "P1", "name": "Ada"},
    }
    fake_source(items)

    assert load.load("fake:Q1", db_path=db_path) == 2

    assert _text_by_source_id(db_path, "Q1").author_ids == [_person_id(db_path, "P1")]


def _person_id(db_path, canonical: str) -> str:
    with MetaDB(db_path) as db:
        person = db.get(f"fake:{canonical}", by="identifiers")
    assert isinstance(person, Person)
    return person.id


def test_load_self_referencing_link_uses_the_provisional_id(fake_source, db_path):
    """An item that is its own author closes the cycle with its own id."""
    fake_source({"Q1": {"shape": "text", "id": "Q1", "title": "T", "authors": ["Q1"]}})

    assert load.load("fake:Q1", db_path=db_path) == 1

    text = _text_by_source_id(db_path, "Q1")
    assert text.author_ids == [text.id]


def test_load_link_redirect_into_the_item_being_imported(fake_source, db_path):
    """A child whose canonical id is the parent in progress resolves to it."""
    items = {
        "Q1": {"shape": "text", "id": "Q1", "title": "T", "authors": ["Q2"]},
        "Q2": {"shape": "person", "id": "Q1", "name": "Loop"},  # redirects to Q1
    }
    fake_source(items)

    assert load.load("fake:Q1", db_path=db_path) == 1

    text = _text_by_source_id(db_path, "Q1")
    assert text.author_ids == [text.id]


def test_load_redirect_dedupes_authors_within_one_import(fake_source, db_path):
    """Two author keys with one canonical id import one Person."""
    items = {
        "Q1": {"shape": "text", "id": "Q1", "title": "T", "authors": ["Q2", "Q3"]},
        "Q2": {"shape": "person", "id": "P1", "name": "Ada"},
        "Q3": {"shape": "person", "id": "P1", "name": "Ada (redirect)"},
    }
    fake_source(items)

    assert load.load("fake:Q1", db_path=db_path) == 2

    assert _text_by_source_id(db_path, "Q1").author_ids == [_person_id(db_path, "P1")]


def test_load_redirect_to_existing_record_reuses_it(fake_source, db_path):
    items = {
        "Q1": {"shape": "text", "id": "Q1", "title": "First", "authors": ["Q2"]},
        "Q2": {"shape": "person", "id": "P1", "name": "Ada"},
    }
    fake_source(items)
    load.load("fake:Q1", db_path=db_path)
    first_person = _person_id(db_path, "P1")

    items["Q4"] = {"shape": "text", "id": "Q4", "title": "Second", "authors": ["Q3"]}
    items["Q3"] = {"shape": "person", "id": "P1", "name": "Ada (redirect)"}
    assert load.load("fake:Q4", db_path=db_path) == 1  # person already in the db

    assert _text_by_source_id(db_path, "Q4").author_ids == [first_person]


def test_load_refresh_skips_the_database_shortcut(fake_source, db_path):
    items = {
        "Q1": {"shape": "text", "id": "Q1", "title": "First", "authors": ["Q2"]},
        "Q2": {"shape": "person", "id": "P1", "name": "Ada"},
    }
    fake_source(items)
    load.load("fake:Q1", db_path=db_path)

    items["Q3"] = {"shape": "person", "id": "P1", "name": "Ada (redirect)"}
    items["Q1"]["authors"] = ["Q3"]
    assert load.load("fake:Q1", db_path=db_path, refresh=True) == 2

    text = _text_by_source_id(db_path, "Q1")
    assert text.title == "First"
    assert text.author_ids == [_person_id(db_path, "P1")]


def test_load_refresh_with_null_i18n_entry_drops_it(fake_source, db_path):
    """Null i18n values (bad source data) vanish instead of crashing the merge."""
    item = {
        "shape": "text",
        "id": "Q1",
        "title": "T",
        "extra": {"i18n": {"vi": None, "en": {"title": "T"}}},
    }
    fake_source({"Q1": item})

    load.load("fake:Q1", db_path=db_path)
    assert load.load("fake:Q1", db_path=db_path, refresh=True) == 1

    assert _text_by_source_id(db_path, "Q1").extra == {"i18n": {"en": {"title": "T"}}}


# --- local JSON repo edges ---------------------------------------------------------


def test_load_repo_single_file_with_single_object(tmp_path, db_path):
    one = tmp_path / "one.json"
    one.write_text(json.dumps({"id": "t1", "type": "text", "title": "Solo"}), encoding="utf-8")
    assert load.load(one, db_path=db_path) == 1

    many = tmp_path / "many.json"
    many.write_text(json.dumps([{"id": "p1", "type": "person", "name": "N"}]), encoding="utf-8")
    assert load.load(many, db_path=db_path) == 1


def test_load_missing_repo_raises(tmp_path, db_path):
    with pytest.raises(FileNotFoundError, match="no record repo"):
        load.load(tmp_path / "nowhere", db_path=db_path)


def test_load_without_source_reads_configured_repo(tmp_path, monkeypatch, db_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "texts.json").write_text(
        json.dumps([{"id": "t1", "type": "text", "title": "From repo path"}]), encoding="utf-8"
    )
    monkeypatch.setenv("PKIT_META_REPO_PATH", str(repo))

    assert load.load(db_path=db_path) == 1

    with MetaDB(db_path) as db:
        record = db.get("t1")
    assert isinstance(record, Text)
    assert record.title == "From repo path"


# --- refresh merge edge --------------------------------------------------------------


def test_refresh_replaces_non_mapping_i18n_entry(wikidata, db_path):
    """A corrupt string i18n entry gives way to the fetched mapping."""
    with MetaDB(db_path) as db:
        db.put(Person(name="Old", identifiers=["wikidata:Q42"], extra={"i18n": {"vi": "junk"}}))

    wikidata.on(
        API_URL,
        json={
            "entities": {
                "Q42": {
                    **entity(),
                    "labels": {
                        "en": {"value": "Douglas Adams"},
                        "vi": {"value": "Đát-lát A-đam"},
                    },
                }
            }
        },
    )

    assert load.load("wikidata:Q42", db_path=db_path, refresh=True) == 1

    with MetaDB(db_path) as db:
        person = db.find(Person, where={"identifiers": "wikidata:Q42"})[0]
    assert person.extra["i18n"]["vi"] == {"name": "Đát-lát A-đam"}


# --- wikidata wire edge cases ----------------------------------------------------------


def test_wikidata_match_url_forms():
    src = WikidataSource()

    assert src.match("wikidata:q42") == "Q42"
    assert src.match("https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q42") == "Q42"
    assert src.match("https://m.wikidata.org/wiki/Q100") == "Q100"
    assert src.match("https://example.com/Q1") is None


def test_wikidata_source_owns_and_closes_its_client(monkeypatch):
    lifecycle = []

    class OwnedClient:
        def __init__(self, **kwargs):
            lifecycle.append("created")

        def request(self, method, url, **kwargs):
            lifecycle.append("request")
            return type("R", (), {"json": lambda self: {"entities": {}}})()

        def close(self):
            lifecycle.append("closed")

    monkeypatch.setattr("pkit.meta.wikidata.client.HttpClient", OwnedClient)

    with WikidataSource() as src:
        assert src.fetch("Q42") is None  # missing entity: client created, used, closed

    assert lifecycle == ["created", "request", "closed"]


def test_wikidata_invalid_json_raises_type_error(wikidata, db_path):
    wikidata.on(API_URL, json=[1, 2])

    with pytest.raises(TypeError, match="invalid JSON"):
        load.load("wikidata:Q42", db_path=db_path)


def test_wikidata_api_error_raises_value_error(wikidata, db_path):
    wikidata.on(API_URL, json={"error": {"info": "unrecognized parameter"}})

    with pytest.raises(ValueError, match="Wikidata API error: unrecognized parameter"):
        load.load("wikidata:Q42", db_path=db_path)


@pytest.mark.parametrize(
    "entities",
    [{}, {"Q42": {"id": "Q42", "missing": ""}}, "junk"],
    ids=["empty-answer", "missing-marker", "junk-answer"],
)
def test_wikidata_missing_item_raises(wikidata, db_path, entities):
    wikidata.on(API_URL, json={"entities": entities})

    with pytest.raises(ValueError, match="wikidata item 'Q42' not found"):
        load.load("wikidata:Q42", db_path=db_path)


def test_wikidata_source_caches_fetches(wikidata):
    """A second fetch of the same key never hits the wire again."""
    wikidata.on(API_URL, json={"entities": {"Q42": entity()}})
    wikidata.enqueue(json={"entities": {"Q42": entity()}})
    wikidata.enqueue(json={"entities": {}})

    with WikidataSource(http=wikidata) as src:
        first = src.fetch("Q42")
        second = src.fetch("Q42")  # cache hit
        missing = src.fetch("Q99")  # answered once, marker cached
        again = src.fetch("Q99")  # still cached

    assert first is not None and second is not None
    assert missing is None and again is None
    assert len(wikidata.requests) == 2  # Q42 once, Q99 once


def test_wikidata_redirect_answer_uses_the_canonical_id(wikidata, db_path):
    book = {
        "id": "Q100",
        "labels": {"en": {"value": "Redirected"}},
        "claims": {"P31": [_item_stmt("P31", "Q571")]},
    }
    wikidata.on(API_URL, json={"entities": {"Q100": book}})  # asked Q99, answered Q100

    assert load.load("wikidata:Q99", db_path=db_path) == 1

    with MetaDB(db_path) as db:
        record = db.get("wikidata:Q100", by="identifiers")
    assert isinstance(record, Text)
    assert record.title == "Redirected"


def test_wikidata_person_without_q5_class_still_imports(wikidata, db_path):
    """An author entity with no P31 at all: the expected model forces the plan."""
    author = {"id": "Q42", "labels": {"en": {"value": "Douglas Adams"}}}
    wikidata.enqueue(json={"entities": {"Q187655": text_entity()}})  # P50 -> Q42
    wikidata.enqueue(json={"entities": {"Q42": author}})

    assert load.load("wikidata:Q187655", db_path=db_path) == 2

    with MetaDB(db_path) as db:
        person = db.find(Person, where={"identifiers": "wikidata:Q42"})[0]
    assert isinstance(person, Person)
    assert person.name == "Douglas Adams"


def test_wikidata_labelless_entity_imports_with_empty_fields(wikidata, db_path):
    book = {"id": "Q300", "claims": {"P31": [_item_stmt("P31", "Q571")]}}
    wikidata.on(API_URL, json={"entities": {"Q300": book}})

    assert load.load("wikidata:Q300", db_path=db_path) == 1

    record = _wikidata_record(db_path, "Q300", Text)
    assert record.title == ""
    assert record.extra == {}


def _wikidata_record(db_path, key: str, cls):
    with MetaDB(db_path) as db:
        record = db.get(f"wikidata:{key}", by="identifiers")
    assert isinstance(record, cls)
    return record


@pytest.mark.parametrize(
    ("lang_claims", "expected_orig"),
    [
        ({"P305": [_str_stmt("P305", "es")]}, "es"),  # IETF tag via P305
        ({"P218": [_str_stmt("P218", "pt")]}, "pt"),  # ISO code via P218
        (
            {
                "P424": [
                    _item_stmt("P424", "Q1"),  # item-typed: not a language
                    {"mainsnak": {"snaktype": "novalue"}},  # no value
                    _str_stmt("P424", " "),  # blank: no language either
                ],
                "P305": [_str_stmt("P305", "")],  # empty: skipped
            },
            "",
        ),
        ({"P424": [_str_stmt("P424", "zh-Hant")]}, "zh"),  # folds into a primary lang
        ({"P424": [_str_stmt("P424", "vi-VN")]}, "vi"),  # regional variant
        ({"P424": [_str_stmt("P424", "fr-CA")]}, "fr-ca"),  # unknown regional stays
    ],
    ids=["p305", "p218", "junk", "zh-hant", "vi-regional", "fr-regional"],
)
def test_wikidata_unknown_lang_qid_resolution(wikidata, db_path, lang_claims, expected_orig):
    """P407 pointing at an unknown QID: the lang entity is fetched and decoded."""
    labels = {"en": {"value": "Book"}}
    if expected_orig:
        labels[expected_orig] = {"value": "Titre"}
    book = {
        "id": "Q500",
        "labels": labels,
        "claims": {"P31": [_item_stmt("P31", "Q571")], "P407": [_item_stmt("P407", "Q9001")]},
    }
    lang_entity = {"id": "Q9001", "labels": {"en": {"value": "Whatever"}}, "claims": lang_claims}
    wikidata.enqueue(json={"entities": {"Q500": book}})
    wikidata.enqueue(json={"entities": {"Q9001": lang_entity}})

    load.load("wikidata:Q500", db_path=db_path)

    record = _wikidata_record(db_path, "Q500", Text)
    assert isinstance(record, Text)
    if expected_orig:
        assert record.extra["i18n"][expected_orig] == {"title": "Titre"}
    else:
        assert list(record.extra["i18n"]) == ["en"]


def test_wikidata_lang_entity_missing_leaves_orig_empty(wikidata, db_path):
    book = {
        "id": "Q501",
        "labels": {"en": {"value": "Book"}},
        "claims": {"P31": [_item_stmt("P31", "Q571")], "P407": [_item_stmt("P407", "Q9002")]},
    }
    wikidata.enqueue(json={"entities": {"Q501": book}})
    wikidata.enqueue(json={"entities": {"Q9002": {"id": "Q9002", "missing": ""}}})
    # the missing Q9002 is label-warmed once more (the lang field asks for it)
    wikidata.enqueue(json={"entities": {}})

    load.load("wikidata:Q501", db_path=db_path)

    record = _wikidata_record(db_path, "Q501", Text)
    assert list(record.extra["i18n"]) == ["en"]


def test_wikidata_two_lang_qids_one_language(wikidata, db_path):
    """Two P407 QIDs that decode to the same code still pick one language."""
    book = {
        "id": "Q502",
        "labels": {"en": {"value": "Book"}, "fr": {"value": "Livre"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q571")],
            "P407": [_item_stmt("P407", "Q1860"), _item_stmt("P407", "Q9003")],
        },
    }
    english_variant = {"id": "Q9003", "claims": {"P424": [_str_stmt("P424", "en-GB")]}}
    wikidata.enqueue(json={"entities": {"Q502": book}})
    wikidata.enqueue(json={"entities": {"Q9003": english_variant}})
    # both P407 QIDs are label-warmed in one final batch
    wikidata.enqueue(
        json={
            "entities": {
                "Q1860": {"id": "Q1860", "labels": {"en": {"value": "English"}}},
                "Q9003": {"id": "Q9003", "labels": {"en": {"value": "British English"}}},
            }
        }
    )

    load.load("wikidata:Q502", db_path=db_path)

    record = _wikidata_record(db_path, "Q502", Text)
    assert record.lang == "English"
    assert list(record.extra["i18n"]) == ["en"]
    assert record.extra["i18n"]["en"] == {"title": "Book"}
