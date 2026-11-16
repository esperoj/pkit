"""Generic config: TOML file + environment, one precedence rule for all of pkit."""

import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_CONFIG_PATH", "as_bool", "as_path", "load", "pick", "resolve"]

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "pkit" / "config.toml"


def load(path: str | Path | None = None) -> dict[str, Any]:
    """Explicit path > $PKIT_CONFIG > ~/.config/pkit/config.toml. Missing file = {}."""
    path = Path(path or os.environ.get("PKIT_CONFIG") or DEFAULT_CONFIG_PATH).expanduser()
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def pick(
    env_key: str,
    file_value: Any = None,
    default: Any = None,
    cast: Callable[[Any], Any] | None = None,
) -> Any:
    """Environment > file > default. Empty strings count as unset."""
    value = os.environ.get(env_key) or file_value
    if value is None or value == "":
        value = default
    return cast(value) if cast is not None and value is not None else value


def _cast_for(default: Any) -> Callable[[Any], Any] | None:
    """Infer the cast from the default's type (click/environs convention).

    bool is checked before int (bool subclasses int); Path uses isinstance so
    PosixPath / WindowsPath defaults work too.
    """
    if isinstance(default, bool):
        return as_bool
    if isinstance(default, Path):
        return as_path
    if isinstance(default, int):
        return int
    if isinstance(default, float):
        return float
    return None  # str (and anything else): use the value as-is


def resolve[T](data: dict[str, Any], section: str, defaults: dict[str, T]) -> dict[str, T]:
    """Resolve a whole section: PKIT_<SECTION>_<KEY> > [section] key > default.

    The cast is inferred per key from the default's type, so one section can
    mix Path, bool, int, float and str.
    """
    values = data.get(section, {})
    return {
        key: pick(
            f"PKIT_{section.upper()}_{key.upper()}",
            values.get(key),
            default,
            _cast_for(default),
        )
        for key, default in defaults.items()
    }


def as_path(value: Any) -> Path:
    return Path(str(value)).expanduser()


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
