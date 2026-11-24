"""version: the distribution metadata lookup."""

from pkit.version import get_version


def test_get_version_returns_metadata_version(monkeypatch) -> None:
    monkeypatch.setattr("pkit.version.version", lambda name: "9.9.9")
    assert get_version() == "9.9.9"


def test_get_version_unknown_when_not_installed(monkeypatch) -> None:
    from importlib.metadata import PackageNotFoundError

    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr("pkit.version.version", missing)
    assert get_version() == "unknown"
