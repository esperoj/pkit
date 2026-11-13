"""File record helpers: the only place archive code touches the database.

Backends never see the DB. put() overwrites whole rows, so every write here
is a read-merge-write to preserve previously recorded locations.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import replace
from typing import cast

from pkit.archive.cas import Hashes
from pkit.archive.errors import InputError
from pkit.archive.remote import merge_locations
from pkit.meta import db
from pkit.meta.models import File

__all__ = ["file_record", "hash_index", "hashes_of", "load_file", "register", "remove_location"]


def load_file(conn: sqlite3.Connection, record_id: str) -> File:
    """Fetch a File row or raise InputError pointing at the missing step."""
    record = db.get(conn, record_id)
    if record is None:
        raise InputError(f"no file record for {record_id}; run add first")
    if not isinstance(record, File):
        raise InputError(f"{record_id} is not a file record")
    return record


def file_record(conn: sqlite3.Connection, hashes: Hashes, size: int) -> File:
    """Create or merge the content row for these hashes."""
    stored = db.get(conn, hashes.record_id)
    if stored is None:
        created = File(id=hashes.record_id, size=size, identifiers=list(hashes.identifiers))
        return cast(File, db.put(conn, created))
    if not isinstance(stored, File):
        raise InputError(f"{hashes.record_id} is not a file record")
    identifiers = sorted(set(stored.identifiers) | set(hashes.identifiers))
    return cast(
        File, db.put(conn, replace(stored, identifiers=identifiers, size=stored.size or size))
    )


def register(conn: sqlite3.Connection, record_id: str, locations: Iterable[str]) -> File:
    """Record remote copies against known content."""
    stored = load_file(conn, record_id)
    return cast(
        File, db.put(conn, replace(stored, locations=merge_locations(stored.locations, locations)))
    )


def remove_location(conn: sqlite3.Connection, record_id: str, location: str) -> File:
    """Forget one remote copy. The manual counterpart to a verify report."""
    stored = load_file(conn, record_id)
    kept = [item for item in stored.locations if item != location]
    return cast(File, db.put(conn, replace(stored, locations=kept)))


def hashes_of(record: File) -> Hashes:
    """Rebuild the hash set from the id and its alternate identifiers."""
    values = {"sha1": "", "md5": ""}
    for token in record.identifiers:
        algo, _, value = token.partition(":")
        if algo in values and value:
            values[algo] = value
    return Hashes(sha256=record.id.removeprefix("sha256:"), **values)


def hash_index(conn: sqlite3.Connection) -> dict[str, str]:
    """Every known hash token mapped to its record id (one pass, O(1) lookup)."""
    index: dict[str, str] = {}
    for record in db.find(conn, File):
        index[record.id] = record.id
        for token in record.identifiers:
            index[token] = record.id
    return index
