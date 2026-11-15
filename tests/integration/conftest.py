"""Fixtures shared by the meta integration tests."""

import pytest

from pkit.meta.wikidata import WikidataSource


@pytest.fixture
def wikidata(fake_http, monkeypatch):
    """Point pkit.meta.load's sources at a WikidataSource with a fake transport."""
    monkeypatch.setattr("pkit.meta.load.SOURCES", (lambda: WikidataSource(http=fake_http),))
    return fake_http
