"""Dump records to a JSON file repo: one file per table, an array of rows.

The inverse of load. Link tables are not dumped separately — links live
inside their parent rows (e.g. author_ids) and db.put rebuilds the link tables.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from pkit.config import as_path
from pkit.meta import config, db
from pkit.meta.models import stored_models

__all__ = ["dump"]


def dump(
    db_path: str | Path | None = None,
    repo_path: str | Path | None = None,
) -> int:
    """Write every record table to <repo>/<table>.json as a JSON array.

    Returns the number of records written. Empty tables are written as [].
    Rows are sorted by id, so unchanged data produces byte-identical files
    (clean git diffs).
    """
    cfg = config.load()
    repo = as_path(repo_path or cfg.repo_path)
    db_path = db_path or cfg.db_path
    count = 0
    repo.mkdir(parents=True, exist_ok=True)
    with db.session(db_path) as conn:
        for cls in stored_models():
            rows = [asdict(r) for r in db.find(conn, cls)]
            rows.sort(key=lambda row: row["id"])
            file = repo / f"{cls.table}.json"
            tmp = file.with_name(file.name + ".tmp")
            tmp.write_text(
                json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            tmp.replace(file)
            count += len(rows)
    return count
