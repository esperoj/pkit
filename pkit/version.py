"""Lazy version resolution for pkit.

The single source of truth for the package version is the [project].version
field in pyproject.toml. The version string is never hardcoded in Python
modules and never parsed at import time: call get_version() only where the
version is actually needed, such as CLI --version output.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_PACKAGE_NAME = "pkit"
_PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return the package version, resolving it lazily on first use."""
    return _resolve_version(_PYPROJECT_PATH)


def _resolve_version(pyproject_path: Path) -> str:
    """Resolve the version from a source checkout or installed metadata.

    Source checkouts (including editable installs) parse pyproject.toml so
    the reported version always matches the working tree. Installed wheels
    fall back to the distribution metadata, which is generated from the
    same pyproject.toml at build time.
    """
    if pyproject_path.is_file():
        with pyproject_path.open("rb") as handle:
            data = tomllib.load(handle)
        return str(data["project"]["version"])
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError as exc:
        message = (
            f"Cannot determine {_PACKAGE_NAME} version: {pyproject_path} "
            "was not found and the package is not installed."
        )
        raise RuntimeError(message) from exc
