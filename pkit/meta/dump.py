"""Dump the record database to a JSON repo (one array file per table).

Rows are sorted by id, so unchanged data produces byte-identical files
(clean git diffs). Link tables are not dumped separately — links live
inside their parent rows.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from pkit.config import as_path
from pkit.meta import config
from pkit.meta.db import MetaDB
from pkit.meta.models import stored_models

__all__ = ["dump"]


def dump(
    repo_path: str | Path | None = None,
    *,
    db_path: str | Path | None = None,
) -> int:
    """Write every record table to <repo>/<table>.json as a JSON array.

    Returns the number of records written. Empty tables are written as [].
    """
    cfg = config.load()
    repo = as_path(repo_path or cfg.repo_path)
    target_db = db_path or cfg.db_path
    count = 0
    repo.mkdir(parents=True, exist_ok=True)
    with MetaDB(target_db) as db:
        for cls in stored_models():
            rows = [asdict(record) for record in db.find(cls)]
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
