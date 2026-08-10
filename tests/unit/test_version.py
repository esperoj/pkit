"""Tests for lazy version resolution."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from pkit.version import _PYPROJECT_PATH, _resolve_version, get_version

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def clear_version_cache():
    """Keep get_version() cache state isolated between tests."""
    get_version.cache_clear()
    yield
    get_version.cache_clear()


def _pyproject_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_pyproject_path_points_at_repo_root() -> None:
    assert _PYPROJECT_PATH == REPO_ROOT / "pyproject.toml"


def test_get_version_matches_pyproject() -> None:
    assert get_version() == _pyproject_version()


def test_get_version_is_cached() -> None:
    assert get_version() == get_version()
    info = get_version.cache_info()
    assert info.misses == 1
    assert info.hits == 1


def test_get_version_falls_back_to_metadata_without_pyproject(monkeypatch) -> None:
    monkeypatch.setattr("pkit.version._PYPROJECT_PATH", Path("/nonexistent/pyproject.toml"))
    monkeypatch.setattr("pkit.version.version", lambda name: "9.9.9")
    assert get_version() == "9.9.9"


def test_resolve_prefers_pyproject_over_metadata(monkeypatch) -> None:
    monkeypatch.setattr("pkit.version.version", lambda name: "0.0.0-metadata")
    assert _resolve_version(_PYPROJECT_PATH) == _pyproject_version()


def test_resolve_falls_back_to_installed_metadata(monkeypatch) -> None:
    monkeypatch.setattr("pkit.version.version", lambda name: "9.9.9")
    assert _resolve_version(Path("/nonexistent/pyproject.toml")) == "9.9.9"


def test_resolve_raises_when_version_unresolvable(monkeypatch) -> None:
    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr("pkit.version.version", missing)
    with pytest.raises(RuntimeError, match="Cannot determine pkit version"):
        _resolve_version(Path("/nonexistent/pyproject.toml"))
