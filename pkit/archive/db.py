"""File records: the only place archive code touches the database.

ArchiveDB extends MetaDB with archive's File-record operations, so pkit.meta.db
and pkit.archive.db stay symmetric. Backends never see the DB. put() overwrites
whole rows, so every write here is a read-merge-write to preserve previously
recorded locations.
"""

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

from pkit.archive.cas import Hashes
from pkit.archive.errors import InputError
from pkit.archive.remote import merge_locations
from pkit.meta.db import MetaDB
from pkit.meta.models import File

__all__ = ["ArchiveDB", "hashes_of"]


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

    def file_record(self, hashes: Hashes, size: int) -> File:
        """Create or merge the content row for these hashes."""
        stored = self.get(hashes.record_id)
        if stored is None:
            created = File(id=hashes.record_id, size=size, identifiers=list(hashes.identifiers))
            return cast(File, self.put(created))
        if not isinstance(stored, File):
            raise InputError(f"{hashes.record_id} is not a file record")
        identifiers = sorted(set(stored.identifiers) | set(hashes.identifiers))
        new_size = stored.size or size
        if identifiers == stored.identifiers and new_size == stored.size:
            return stored
        return cast(File, self.put(replace(stored, identifiers=identifiers, size=new_size)))

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
        for record in self.find(File):
            index[record.id] = record.id
            for token in record.identifiers:
                index[token] = record.id
        return index


def hashes_of(record: File) -> Hashes:
    """Rebuild the hash set from the id and its alternate identifiers."""
    values = {"sha1": "", "md5": ""}
    for token in record.identifiers:
        algo, _, value = token.partition(":")
        if algo in values and value:
            values[algo] = value
    return Hashes(sha256=record.id.removeprefix("sha256:"), **values)
