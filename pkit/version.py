"""The package version, from the installed distribution metadata.

The single source of truth is the `version` field in pyproject.toml, which
pip copies into the distribution metadata at install time (editable installs
included). get_version() is only called where the version is actually
needed, such as CLI --version output.
"""

from importlib.metadata import PackageNotFoundError, version

_UNKNOWN = "unknown"


def get_version() -> str:
    """Return the package version, or "unknown" for an uninstalled tree."""
    try:
        return version("pkit")
    except PackageNotFoundError:
        return _UNKNOWN
