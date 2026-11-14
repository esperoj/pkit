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


def _mono_stmt(prop: str, lang: str, text: str) -> dict:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {
                "type": "monolingualtext",
                "value": {"language": lang, "text": text},
            },
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
    assert record.extra == {"i18n": {"en": {"name": "Douglas Adams", "description": "writer"}}}
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


def test_auto_chinese_work_uses_simplified_main_and_english_taxonomy(wikidata, db_path):
    book = {
        "id": "Q1001",
        "labels": {
            "en": {"value": "The Three-Body Problem"},
            "vi": {"value": "Tam thể"},
            "zh-hant": {"value": "三體"},
            "zh-hans": {"value": "三体"},
        },
        "descriptions": {
            "en": {"value": "science fiction novel"},
            "vi": {"value": "tiểu thuyết khoa học viễn tưởng"},
            "zh-hans": {"value": "刘慈欣创作的长篇科幻小说"},
            "zh-hant": {"value": "劉慈欣創作的長篇科幻小說"},
        },
        "claims": {
            "P31": [_item_stmt("P31", "Q8261")],
            "P407": [_item_stmt("P407", "Q7850")],
            "P1476": [
                _mono_stmt("P1476", "zh-hant", "三體"),
                _mono_stmt("P1476", "zh-hans", "三体"),
            ],
            "P123": [_item_stmt("P123", "Q2001")],
            "P136": [_item_stmt("P136", "Q2002")],
            "P50": [_item_stmt("P50", "Q2003")],
        },
    }
    author = {
        "id": "Q2003",
        "labels": {
            "en": {"value": "Liu Cixin"},
            "vi": {"value": "Lưu Từ Hân"},
            "zh-hant": {"value": "劉慈欣"},
            "zh-hans": {"value": "刘慈欣"},
        },
        "descriptions": {"en": {"value": "Chinese writer"}, "zh": {"value": "中国科幻作家"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q5")],
            "P103": [_item_stmt("P103", "Q7850")],
            "P27": [_item_stmt("P27", "Q2004")],
            "P106": [_item_stmt("P106", "Q2005")],
        },
    }
    wikidata.enqueue(json={"entities": {"Q1001": book}})
    wikidata.enqueue(
        json={
            "entities": {
                "Q2001": {
                    "id": "Q2001",
                    "labels": {
                        "en": {"value": "Chongqing Press"},
                        "zh-hans": {"value": "重庆出版社"},
                        "zh-hant": {"value": "重慶出版社"},
                    },
                },
                "Q2002": {
                    "id": "Q2002",
                    "labels": {
                        "en": {"value": "science fiction"},
                        "zh-hans": {"value": "科幻小说"},
                    },
                },
                "Q7850": {
                    "id": "Q7850",
                    "labels": {"en": {"value": "Chinese"}, "zh-hans": {"value": "汉语"}},
                },
            }
        }
    )
    wikidata.enqueue(json={"entities": {"Q2003": author}})
    wikidata.enqueue(
        json={
            "entities": {
                "Q2004": {
                    "id": "Q2004",
                    "labels": {"en": {"value": "China"}, "zh-hans": {"value": "中国"}},
                },
                "Q2005": {
                    "id": "Q2005",
                    "labels": {"en": {"value": "writer"}, "zh-hans": {"value": "作家"}},
                },
            }
        }
    )

    assert load.load("wikidata:Q1001", db_path=db_path) == 2

    with MetaDB(db_path) as meta:
        text = meta.find(Text, identifier="wikidata:Q1001")[0]
        person = meta.find(Person, identifier="wikidata:Q2003")[0]

    assert isinstance(text, Text)
    assert text.title == "三体"
    assert text.description == "刘慈欣创作的长篇科幻小说"
    assert text.publishers == ["重庆出版社"]
    assert text.lang == "Chinese"
    assert text.genres == ["science fiction"]
    assert text.extra["i18n"] == {
        "en": {"title": "The Three-Body Problem", "description": "science fiction novel"},
        "vi": {"title": "Tam thể", "description": "tiểu thuyết khoa học viễn tưởng"},
        "zh": {"title": "三体", "description": "刘慈欣创作的长篇科幻小说"},
    }

    assert isinstance(person, Person)
    assert person.name == "刘慈欣"
    assert person.country == "China"
    assert person.occupations == ["writer"]
    assert person.extra["i18n"] == {
        "en": {"name": "Liu Cixin", "description": "Chinese writer"},
        "vi": {"name": "Lưu Từ Hân"},
        "zh": {"name": "刘慈欣", "description": "中国科幻作家"},
    }


def test_auto_vietnamese_work_and_person_p1559_fallback(wikidata, db_path):
    book = {
        "id": "Q3001",
        "labels": {"en": {"value": "Dumb Luck"}, "vi": {"value": "Số đỏ"}},
        "descriptions": {"vi": {"value": "tiểu thuyết của Vũ Trọng Phụng"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q8261")],
            "P407": [_item_stmt("P407", "Q9199")],
            "P50": [_item_stmt("P50", "Q3002")],
        },
    }
    author = {
        "id": "Q3002",
        "labels": {"en": {"value": "Vu Trong Phung"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q5")],
            "P1559": [_mono_stmt("P1559", "vi", "Vũ Trọng Phụng")],
        },
    }
    wikidata.enqueue(json={"entities": {"Q3001": book}})
    wikidata.enqueue(
        json={"entities": {"Q9199": {"id": "Q9199", "labels": {"en": {"value": "Vietnamese"}}}}}
    )
    wikidata.enqueue(json={"entities": {"Q3002": author}})

    assert load.load("wikidata:Q3001", db_path=db_path) == 2

    with MetaDB(db_path) as meta:
        text = meta.find(Text, identifier="wikidata:Q3001")[0]
        person = meta.find(Person, identifier="wikidata:Q3002")[0]

    assert isinstance(text, Text)
    assert text.title == "Số đỏ"
    assert text.description == "tiểu thuyết của Vũ Trọng Phụng"
    assert text.lang == "Vietnamese"
    assert set(text.extra["i18n"]) == {"en", "vi"}

    assert isinstance(person, Person)
    assert person.name == "Vũ Trọng Phụng"
    assert person.extra["i18n"] == {
        "en": {"name": "Vu Trong Phung"},
        "vi": {"name": "Vũ Trọng Phụng"},
    }


def test_auto_other_original_language_keeps_at_most_four_langs_in_extra(wikidata, db_path):
    manga = {
        "id": "Q4001",
        "labels": {
            "en": {"value": "Pandora Hearts"},
            "ja": {"value": "パンドラハーツ"},
            "vi": {"value": "Pandora Hearts (vi)"},
            "zh-hans": {"value": "潘朵拉之心"},
            "fr": {"value": "Pandora Hearts (fr)"},
        },
        "descriptions": {"ja": {"value": "日本の漫画"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q8274")],
            "P407": [_item_stmt("P407", "Q5287")],
        },
    }
    wikidata.enqueue(json={"entities": {"Q4001": manga}})
    wikidata.enqueue(
        json={"entities": {"Q5287": {"id": "Q5287", "labels": {"en": {"value": "Japanese"}}}}}
    )

    assert load.load("wikidata:Q4001", db_path=db_path) == 1

    with MetaDB(db_path) as meta:
        text = meta.find(Text, identifier="wikidata:Q4001")[0]

    assert isinstance(text, Text)
    assert text.title == "Pandora Hearts"
    assert text.lang == "Japanese"
    assert list(text.extra["i18n"]) == ["en", "ja", "vi", "zh"]
    assert text.extra["i18n"]["ja"] == {"title": "パンドラハーツ", "description": "日本の漫画"}


def test_auto_resolves_unknown_language_qid_via_p424(wikidata, db_path):
    book = {
        "id": "Q4501",
        "labels": {"en": {"value": "R.U.R."}, "cs": {"value": "R.U.R."}},
        "descriptions": {"cs": {"value": "divadelní hra"}},
        "claims": {
            "P31": [_item_stmt("P31", "Q25379")],
            "P407": [_item_stmt("P407", "Q9056")],
        },
    }
    czech = {
        "id": "Q9056",
        "labels": {"en": {"value": "Czech"}},
        "claims": {"P424": [_str_stmt("P424", "cs")]},
    }
    wikidata.enqueue(json={"entities": {"Q4501": book}})
    wikidata.enqueue(json={"entities": {"Q9056": czech}})

    assert load.load("wikidata:Q4501", db_path=db_path) == 1
    assert len(wikidata.requests) == 2  # Q9056 label cached alongside P424

    with MetaDB(db_path) as meta:
        text = meta.find(Text, identifier="wikidata:Q4501")[0]

    assert isinstance(text, Text)
    assert text.lang == "Czech"
    assert list(text.extra["i18n"]) == ["cs", "en"]


def test_explicit_lang_overrides_auto_and_config(run_cli, wikidata, db_path, tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[meta]\nlang = "vi"\n', encoding="utf-8")
    monkeypatch.setenv("PKIT_CONFIG", str(cfg_file))

    sw = {
        "id": "Q5001",
        "labels": {
            "en": {"value": "Sample App"},
            "vi": {"value": "Ứng dụng mẫu"},
            "zh-hans": {"value": "示例软件"},
            "zh-hant": {"value": "示例軟體"},
        },
        "claims": {
            "P31": [_item_stmt("P31", "Q166142")],
            "P407": [_item_stmt("P407", "Q1860"), _item_stmt("P407", "Q7850")],
            "P178": [_item_stmt("P178", "Q5002")],
            "P306": [_item_stmt("P306", "Q5003")],
        },
    }
    wikidata.enqueue(json={"entities": {"Q5001": sw}})
    wikidata.enqueue(
        json={
            "entities": {
                "Q5002": {
                    "id": "Q5002",
                    "labels": {"en": {"value": "Acme"}, "zh-hans": {"value": "甲公司"}},
                },
                "Q5003": {
                    "id": "Q5003",
                    "labels": {"en": {"value": "Linux"}, "zh-hans": {"value": "Linux内核"}},
                },
            }
        }
    )

    # CLI --lang zh overrides [meta] lang = "vi"
    code, out, err = run_cli(meta_main, ["load", "--lang", "zh", "wikidata:Q5001"])
    assert code == EXIT_OK, err
    assert out.strip() == "1"

    with MetaDB(db_path) as meta:
        record = meta.find(Software, identifier="wikidata:Q5001")[0]
    assert isinstance(record, Software)
    assert record.title == "示例软件"
    assert record.developers == ["甲公司"]
    assert record.platforms == ["Linux"]
    assert set(record.extra["i18n"]) == {"en", "vi", "zh"}


def test_refresh_merges_i18n_and_preserves_manual_edits(wikidata, db_path):
    with MetaDB(db_path) as meta:
        meta.put(
            Person(
                name="Old",
                identifiers=["wikidata:Q42"],
                extra={
                    "notes": "custom",
                    "i18n": {
                        "vi": {"name": "Đát-lát A-đam"},
                        "zh": {"name": "道格拉斯"},
                    },
                },
            )
        )
    wikidata.on(API_URL, json={"entities": {"Q42": entity(name="Douglas Adams")}})

    assert load.load("wikidata:Q42", db_path=db_path, refresh=True) == 1

    with MetaDB(db_path) as meta:
        person = meta.find(Person, identifier="wikidata:Q42")[0]

    assert person.extra == {
        "notes": "custom",
        "i18n": {
            "en": {"name": "Douglas Adams", "description": "writer"},
            "vi": {"name": "Đát-lát A-đam"},
            "zh": {"name": "道格拉斯"},
        },
    }


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
