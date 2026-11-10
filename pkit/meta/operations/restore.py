from __future__ import annotations

from pathlib import Path

from pkit.meta import config as meta_config
from pkit.meta import records
from pkit.meta.serialize import from_json

__all__ = [
    "restore",
]


def restore(
    db_path: str | Path | None = None,
    repo_path: str | Path | None = None,
) -> int:
    """
    Restore records from JSON files in the records repo.

    The repo may contain JSON files directly or in subfolders:

        records/
          books/
            some-book.json
          persons/
            some-person.json

    Missing foreign references are allowed.
    Use records.validate() later to inspect unresolved links.
    """
    cfg = meta_config.load()

    db_path = db_path or cfg.db_path
    repo_path = repo_path or cfg.repo_path
    repo_path = Path(repo_path).expanduser()

    if not repo_path.is_dir():
        return 0

    count = 0

    with records.open(db_path) as rcs:
        for file_path in sorted(repo_path.rglob("*.json")):
            record = from_json(file_path.read_bytes())
            rcs.put(record)
            count += 1

    return count
