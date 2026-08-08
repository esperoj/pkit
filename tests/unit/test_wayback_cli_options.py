from __future__ import annotations

import dataclasses

import pytest

from pkit.wayback.cli import _as_bool, _coerce_options, _save_options_from_params
from pkit.wayback.client import SaveOptions


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", False),
        ("0", False),
        ("false", False),
        ("False", False),
        ("NO", False),
        (" off ", False),
        ("1", True),
        ("true", True),
        ("yes", True),
        (0, False),
        (1, True),
        (None, False),
        ([], False),
        (["x"], True),
    ],
)
def test_as_bool(raw, expected):
    assert _as_bool(raw) is expected


def test_coerce_options_filters_unknown_fields():
    payload = {
        "capture_all": "true",
        "email_result": "0",
        "bogus": True,
    }

    assert _coerce_options(payload) == {
        "capture_all": True,
        "email_result": False,
    }


def test_save_options_from_params_payload_overrides_cli():
    params = {field.name: False for field in dataclasses.fields(SaveOptions)}
    payload = {
        "capture_all": "1",
        "unknown": True,
    }

    opts = _save_options_from_params(params, payload)

    assert opts == SaveOptions(
        capture_all=True,
        capture_outlinks=False,
        email_result=False,
        force_get=False,
        skip_first_archive=False,
    )
