"""File records: the only place archive code touches the database.

ArchiveDB extends MetaDB with archive's File-record operations, so pkit.meta.db
and pkit.archive.db stay symmetric. Backends never see the DB. put() overwrites
whole rows, so every write here is a read-merge-write to preserve previously
recorded locations. Hash tokens ("sha256:<hex>") are archive's own convention:
pkit.meta.db stays unaware of them, this module maps them to File's columns.
"""

import time
from collections.abc import Iterable
from dataclasses import replace
from typing import cast

from pkit.archive.cas import Hashes
from pkit.archive.errors import InputError
from pkit.archive.remote import WAYBACK, merge_locations, split_wayback
from pkit.meta.db import MetaDB
from pkit.meta.models import File

__all__ = ["ArchiveDB", "hashes_of"]

HASH_TOKEN_PREFIXES = ("sha256:", "sha1:", "md5:")


class ArchiveDB(MetaDB):
    """MetaDB plus the File-record operations archive needs."""

    def load_file(self, record_id: str) -> File:
        """Fetch a File row or raise InputError pointing at the missing step."""
        record = self.get(record_id)
        if record is None:
            raise InputError(f"no file record for {record_id}; run add first")
        if not isinstance(record, File):
            raise InputError(f"{record_id} is not a file record")
        return record

    def file_record(self, hashes: Hashes, size: int, name: str = "") -> File:
        """Create or merge the content row for these hashes.

        name is the tree name the content first arrived under; an already
        recorded name wins, so re-adds and re-registration stay no-ops.
        """
        # The canonical row is keyed by sha256; a row recorded under another
        # hash (repo data carrying only sha1/md5) is still the same content.
        stored = cast(File, self.get(hashes.record_id))
        if stored is None:
            for field in ("sha256", "sha1", "md5"):
                if value := getattr(hashes, field):
                    stored = cast(File, self.find_one(File, where={field: value}))
                    if stored is not None:
                        break
        if stored is None:
            created = File(
                id=hashes.record_id,
                name=name,
                size=size,
                sha256=hashes.sha256,
                sha1=hashes.sha1,
                md5=hashes.md5,
            )
            return cast(File, self.put(created))
        identifiers = [
            token for token in stored.identifiers if not token.startswith(HASH_TOKEN_PREFIXES)
        ]
        merged = replace(
            stored,
            identifiers=identifiers,
            sha256=hashes.sha256 or stored.sha256,
            sha1=hashes.sha1 or stored.sha1,
            md5=hashes.md5 or stored.md5,
            size=stored.size or size,
            name=stored.name or name,
        )
        if merged == stored:
            return stored
        return cast(File, self.put(merged))

    def register(self, record_id: str, locations: Iterable[str]) -> File:
        """Record remote copies against known content."""
        stored = self.load_file(record_id)
        return cast(
            File, self.put(replace(stored, locations=merge_locations(stored.locations, locations)))
        )

    def remove_location(self, record_id: str, location: str) -> File:
        """Forget one remote copy. The manual counterpart to a verify report."""
        stored = self.load_file(record_id)
        kept = [item for item in stored.locations if item != location]
        return cast(File, self.put(replace(stored, locations=kept)))

    def hash_index(self) -> dict[str, str]:
        """Every known hash token mapped to its record id (one pass, O(1) lookup)."""
        index: dict[str, str] = {}
        for row in self.conn.execute("SELECT id, sha256, sha1, md5 FROM files"):
            for algo in ("sha256", "sha1", "md5"):
                value = row[algo]
                if value:
                    index[f"{algo}:{value}"] = row["id"]
        return index

    def wayback_stale(self, days: float) -> list[tuple[str, tuple[str, ...]]]:
        """(record id, locations) whose wayback captures predate the cutoff.

        json_each narrows the scan to wayback locations; the timestamp
        comparison stays in Python — a capture URL is not a column, and
        yyyymmddhhmmss timestamps already sort lexicographically. A
        malformed location is skipped, not fatal: register only wrote good
        ones, so a bad one is imported data worth seeing in a report later.
        """
        cutoff = time.strftime("%Y%m%d%H%M%S", time.gmtime(time.time() - days * 86400))
        found: dict[str, list[str]] = {}
        rows = self.conn.execute(
            "SELECT files.id, _loc.value AS location "
            "FROM files, json_each(files.locations) AS _loc WHERE _loc.value LIKE ?",
            (f"{WAYBACK}%",),
        )
        for row in rows:
            try:
                timestamp, _ = split_wayback(row["location"])
            except InputError:
                continue
            if timestamp < cutoff:
                found.setdefault(row["id"], []).append(row["location"])
        return [(record_id, tuple(locations)) for record_id, locations in found.items()]


def hashes_of(record: File) -> Hashes:
    """Rebuild the hash set from the record's hash columns (id as fallback)."""
    return Hashes(
        sha256=record.sha256 or record.id.removeprefix("sha256:"),
        sha1=record.sha1,
        md5=record.md5,
    )
