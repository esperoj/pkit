"""config.resolve: one precedence rule, casts inferred from the default type."""

import pytest

from pkit import config


@pytest.mark.parametrize(
    ("default", "env", "file_value", "expected"),
    [
        (True, "no", True, False),  # bool cast, as_bool false spellings
        (True, "yes", False, True),
        (7, "42", 1, 42),  # int cast
        (1.5, "2", 0.5, 2.0),  # float cast
        ("fallback", "", "from-file", "from-file"),  # empty env counts as unset
        ("fallback", None, None, "fallback"),  # str default, no cast
    ],
)
def test_resolve_casts_and_precedence(monkeypatch, default, env, file_value, expected):
    monkeypatch.setenv("PKIT_TEST_SECTION_VALUE", env) if env is not None else monkeypatch.delenv(
        "PKIT_TEST_SECTION_VALUE", raising=False
    )

    resolved = config.resolve(
        {"test_section": {"value": file_value}}, "test_section", {"value": default}
    )

    assert resolved["value"] == expected


def test_as_bool_spells_true_and_false():
    assert config.as_bool(" ON ")
    assert not config.as_bool("off")
