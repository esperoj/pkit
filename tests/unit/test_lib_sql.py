"""pkit.lib.sql: pure SQL builders for any table."""

import pytest

from pkit.lib import sql


def test_quote_wraps_identifier():
    assert sql.quote("name") == '"name"'


def test_quote_doubles_embedded_quotes():
    assert sql.quote('we"ird') == '"we""ird"'


def test_placeholders_one_per_value():
    assert sql.placeholders([]) == ""
    assert sql.placeholders(["a"]) == "?"
    assert sql.placeholders(["a", "b", "c"]) == "?, ?, ?"


def test_equals_one_value_compares():
    assert sql.equals("name", ["Jun"]) == '"name" = ?'


def test_equals_many_values_becomes_in():
    assert sql.equals("name", ["Jun", "Mai"]) == '"name" IN (?, ?)'


def test_json_contains_checks_array_membership():
    statement = sql.json_contains("texts", "genres", ["manga", "novel"])

    assert statement == (
        'EXISTS (SELECT 1 FROM json_each("texts"."genres") AS _e WHERE _e.value IN (?, ?))'
    )


def test_parse_sort_plain_column_is_ascending():
    assert sql.parse_sort("title", ["id", "title"]) == ("title", False)


def test_parse_sort_dash_prefix_is_descending():
    assert sql.parse_sort("-updated_at", ["id", "updated_at"]) == ("updated_at", True)


def test_parse_sort_rejects_unknown_column():
    with pytest.raises(ValueError, match="unknown sort 'bogus'; known columns: id, title"):
        sql.parse_sort("bogus", ["id", "title"])


def test_parse_sort_lists_no_columns_as_none():
    with pytest.raises(ValueError, match="known columns: \\(none\\)"):
        sql.parse_sort("id", [])


def test_upsert_updates_every_column_but_the_key():
    statement = sql.upsert("things", ["id", "name"], key="id")

    assert statement == (
        'INSERT INTO "things" ("id", "name") VALUES (?, ?) '
        'ON CONFLICT("id") DO UPDATE SET "name" = excluded."name"'
    )


def test_upsert_supports_a_custom_key():
    statement = sql.upsert("things", ["name", "slug"], key="slug")

    assert 'ON CONFLICT("slug") DO UPDATE SET "name" = excluded."name"' in statement


def test_upsert_with_only_the_key_conflicts_into_nothing():
    assert sql.upsert("things", ["id"]) == (
        'INSERT INTO "things" ("id") VALUES (?) ON CONFLICT("id") DO NOTHING'
    )
