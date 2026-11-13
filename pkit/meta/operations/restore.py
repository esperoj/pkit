"""Restore records from a JSON file repo into the database."""

from __future__ import annotations

import json
from pathlib import Path

from pkit.config import as_path
from pkit.meta import config, db

__all__ = ["restore"]


def restore(
    db_path: str | Path | None = None,
    repo_path: str | Path | None = None,
) -> int:
    """Restore every *.json file under the repo (one record or a list per file).

    Returns the number of records restored. Raises FileNotFoundError if the
    repo directory does not exist.
    """
    repo = as_path(repo_path or config.load().repo_path)
    if not repo.is_dir():
        raise FileNotFoundError(f"no record repo at {repo}")
    count = 0
    with db.session(db_path) as conn:
        for file in sorted(repo.rglob("*.json")):
            rows = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows:
                db.put(conn, row)
                count += 1
    return count
