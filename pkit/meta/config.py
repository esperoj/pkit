"""Meta settings: PKIT_META_* env > TOML [meta] > defaults under the core data dir."""

from dataclasses import dataclass
from pathlib import Path

from pkit import config

__all__ = ["MetaConfig", "load"]

DATA_DIR = Path.home() / ".local" / "share" / "pkit"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


@dataclass(frozen=True)
class MetaConfig:
    db_path: Path
    repo_path: Path
    migrations_dir: Path
    lang: str = "auto"


def load(path: str | Path | None = None) -> MetaConfig:
    data = config.load(path)
    data_dir = config.resolve(data, "core", {"data_dir": DATA_DIR})["data_dir"]
    paths = config.resolve(
        data,
        "meta",
        {
            "db_path": data_dir / "records.db",
            "repo_path": data_dir / "records",
            "migrations_dir": MIGRATIONS_DIR,
        },
    )
    lang = str(config.resolve(data, "meta", {"lang": "auto"})["lang"])
    return MetaConfig(
        db_path=paths["db_path"],
        repo_path=paths["repo_path"],
        migrations_dir=paths["migrations_dir"],
        lang=lang,
    )
