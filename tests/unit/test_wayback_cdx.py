from __future__ import annotations

import pytest

from pkit.wayback.client import BASE_URL, InputError, WaybackClient


def test_cdx_parses_rows_and_skips_header(requests_mock):
    requests_mock.get(
        f"{BASE_URL}/cdx/search/cdx",
        json=[
            ["original", "timestamp", "digest", "statuscode", "mimetype"],
            ["https://host/a", "20260101000000", "ABC", "200", "application/pdf"],
        ],
    )
    with WaybackClient(lock_file="", timeout=0.1) as client:
        rows = client.cdx("https://host/", match_type="prefix")
    assert len(rows) == 1
    assert rows[0].timestamp == "20260101000000"
    assert rows[0].statuscode == "200"


def test_cdx_empty_body_is_empty_list(requests_mock):
    requests_mock.get(f"{BASE_URL}/cdx/search/cdx", text="")
    with WaybackClient(lock_file="", timeout=0.1) as client:
        assert client.cdx("https://host/a") == []


def test_cdx_requires_url():
    with WaybackClient(lock_file="", timeout=0.1) as client, pytest.raises(InputError):
        client.cdx("")
