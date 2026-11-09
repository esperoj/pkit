from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pkit import config

__all__ = [
    "MetaConfig",
    "load",
]


@dataclass(frozen=True)
class MetaConfig:
    db_path: Path
    repo_path: Path
    migrations_dir: Path


def load(path: str | Path | None = None) -> MetaConfig:
    """
    Load meta config.

    Supported environment variables:

        PKIT_CONFIG
        PKIT_CORE_DATA_DIR
        PKIT_META_DB_PATH
        PKIT_META_REPO_PATH
        PKIT_META_MIGRATIONS_DIR
    """
    data = config.load_toml(path)

    core = data.get("core", {})
    meta = data.get("meta", {})

    data_dir = config.get(
        "PKIT_CORE_DATA_DIR",
        file_value=core.get("data_dir"),
        default=Path.home() / ".local" / "share" / "pkit",
        cast=config.path,
    )

    db_path = config.get(
        "PKIT_META_DB_PATH",
        file_value=meta.get("db_path"),
        default=data_dir / "records.db",
        cast=config.path,
    )

    repo_path = config.get(
        "PKIT_META_REPO_PATH",
        file_value=meta.get("repo_path"),
        default=data_dir / "records",
        cast=config.path,
    )

    migrations_dir = config.get(
        "PKIT_META_MIGRATIONS_DIR",
        file_value=meta.get("migrations_dir"),
        default=Path(__file__).resolve().parent / "migrations",
        cast=config.path,
    )

    return MetaConfig(
        db_path=db_path,
        repo_path=repo_path,
        migrations_dir=migrations_dir,
    )
