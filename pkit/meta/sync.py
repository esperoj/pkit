"""Record sync: dump the database to a JSON repo, load it back.

One file per table, one JSON array per file. Link tables are not synced
separately — links live inside their parent rows and db.put rebuilds them.
Rows are sorted by id, so unchanged data produces byte-identical files
(clean git diffs). Commit the repo; copy it; load it on the other machine.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from pkit.config import as_path
from pkit.meta import config, db
from pkit.meta.models import stored_models

__all__ = ["dump", "load"]


def dump(
    db_path: str | Path | None = None,
    repo_path: str | Path | None = None,
) -> int:
    """Write every record table to <repo>/<table>.json as a JSON array.

    Returns the number of records written. Empty tables are written as [].
    """
    cfg = config.load()
    repo = as_path(repo_path or cfg.repo_path)
    db_path = db_path or cfg.db_path
    count = 0
    repo.mkdir(parents=True, exist_ok=True)
    with db.session(db_path) as conn:
        for cls in stored_models():
            rows = [asdict(record) for record in db.find(conn, cls)]
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


def load(
    db_path: str | Path | None = None,
    repo_path: str | Path | None = None,
) -> int:
    """Load every *.json file under the repo (one record or a list per file).

    Returns the number of records loaded. Raises FileNotFoundError if the
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
