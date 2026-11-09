from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "as_bool",
    "get",
    "load_toml",
    "path",
]


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "pkit" / "config.toml"


def load_toml(path: str | Path | None = None) -> dict[str, Any]:
    """
    Load TOML config.

    Resolution order for the config file path:

        explicit path argument
          -> PKIT_CONFIG environment variable
          -> ~/.config/pkit/config.toml

    Missing file is not an error.
    """
    if path is None:
        path = os.environ.get("PKIT_CONFIG") or DEFAULT_CONFIG_PATH

    path = Path(path).expanduser()

    if not path.exists():
        return {}

    with path.open("rb") as fh:
        return tomllib.load(fh)


def get(
    env_key: str,
    *,
    file_value: Any = None,
    default: Any = None,
    cast: Callable[[Any], Any] | None = None,
) -> Any:
    """
    Pick a config value.

    Precedence:

        environment variable
          -> TOML file value
          -> default

    Empty environment variables are treated as unset.

    Conversion is explicit:

        config.get(..., cast=config.path)
        config.get(..., cast=config.as_bool)
    """
    value = os.environ.get(env_key)

    if value is None or value == "":
        value = file_value

    if value is None or value == "":
        value = default

    if cast is not None and value is not None:
        return cast(value)

    return value


def path(value: Any) -> Path:
    """
    Explicit Path cast.
    """
    return Path(str(value)).expanduser()


def as_bool(value: Any) -> bool:
    """
    Explicit bool cast.
    """
    return str(value).lower() in {"1", "true", "yes", "on"}
