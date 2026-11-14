"""Personal archive manager: CAS + symlinks locally, a location log in meta.

Three layers, three truths:
  git         names, directory layout, history (symlinks are committed)
  filesystem  local content presence (.git/pkit-archive objects)
  meta db     content identity plus remote locations (dump/load syncs machines)

Library only: results are dataclasses, failures raise typed exceptions.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pkit import config
from pkit.archive import records
from pkit.archive.cas import (
    Entry,
    abs_path,
    cas_path,
    find_cas,
    hash_file,
    ingest,
    is_hash,
    link_hash,
    make_link,
    set_readonly,
    walk_files,
)
from pkit.archive.errors import ArchiveError, InputError, TransientError
from pkit.archive.rclone import RcloneBackend
from pkit.archive.remote import WAYBACK, RemoteFile, Status, selects
from pkit.archive.wayback import WaybackBackend
from pkit.meta import db
from pkit.meta.models import File

__all__ = [
    "AddResult",
    "ArchiveError",
    "Check",
    "CopyResult",
    "DropResult",
    "FixResult",
    "ForgetResult",
    "GetResult",
    "InputError",
    "MatchResult",
    "RemoteFile",
    "TransientError",
    "VerifyResult",
    "add",
    "copy",
    "drop",
    "fix",
    "forget",
    "get",
    "match",
    "remove_location",
    "verify",
]


@dataclass(frozen=True)
class AddResult:
    added: int
    relinked: int
    skipped: int


@dataclass(frozen=True)
class FixResult:
    relinked: int
    permissions: int
    missing: tuple[str, ...]


@dataclass(frozen=True)
class DropResult:
    record_id: str
    location: str


@dataclass(frozen=True)
class ForgetResult:
    forgotten: tuple[str, ...]
    kept: tuple[str, ...]


@dataclass(frozen=True)
class MatchResult:
    matched: int
    unknown: int
    no_hash: int


@dataclass(frozen=True)
class CopyResult:
    copied: int
    present: int
    locations: tuple[str, ...]


@dataclass(frozen=True)
class Check:
    location: str
    status: Status


@dataclass(frozen=True)
class VerifyResult:
    record_id: str
    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.status == "ok" for check in self.checks)


@dataclass(frozen=True)
class GetResult:
    record_id: str
    location: str
    path: str


def _wayback_url(config_path: str | Path | None) -> str:
    data = config.load(config_path)
    return config.resolve(data, "archive", {"wayback_url": ""})["wayback_url"]


def _backend(remote: str, wayback_url: str) -> RcloneBackend | WaybackBackend:
    if remote.startswith(WAYBACK):
        return WaybackBackend(remote.removeprefix(WAYBACK), base_url=wayback_url)
    return RcloneBackend(remote)


def _record_id(value: str | Path) -> str:
    digest = str(value).removeprefix("sha256:")
    if is_hash(digest):
        return f"sha256:{digest}"
    path = abs_path(value)
    named = link_hash(path) or (path.name if is_hash(path.name) else None)
    if named:
        return f"sha256:{named}"
    raise InputError(f"cannot resolve a content hash from {value}")


def add(
    path: str | Path, *, db_path: str | Path | None = None, cas_dir: str | Path | None = None
) -> AddResult:
    """Move content into the CAS, leave relative symlinks, register content.

    Foreign symlinks are never followed. Already-linked content is only
    re-registered if missing from the database (and re-pointed if the CAS moved).
    """
    root = abs_path(path)
    cas = find_cas(root, cas_dir)
    if root == cas or cas in root.parents:
        raise InputError(f"cannot add files inside the CAS directory: {root}")
    added = relinked = skipped = 0
    with db.session(db_path) as conn:
        for item in walk_files(root):
            named = link_hash(item)
            if item.is_symlink():
                obj = cas_path(cas, named) if named else None
                if obj is None or not obj.exists():
                    skipped += 1
                    continue
                stored = db.get(conn, f"sha256:{named}")
                if not isinstance(stored, File) or not stored.identifiers:
                    records.file_record(conn, hash_file(obj), obj.stat().st_size)
                    conn.commit()
                if make_link(item, obj):
                    relinked += 1
                continue
            size = item.stat().st_size
            hashes = hash_file(item)
            obj = ingest(item, cas, hashes)
            make_link(item, obj)
            records.file_record(conn, hashes, size)
            conn.commit()
            added += 1
    return AddResult(added=added, relinked=relinked, skipped=skipped)


def fix(path: str | Path, *, cas_dir: str | Path | None = None) -> FixResult:
    """Re-point CAS symlinks at the current root and re-assert object perms.

    Pure filesystem: no database, no network. Dangling links are reported.
    """
    root = abs_path(path)
    cas = find_cas(root, cas_dir)
    relinked = permissions = 0
    missing: list[str] = []
    for item in walk_files(root):
        named = link_hash(item)
        if named is None:
            continue
        obj = cas_path(cas, named)
        if not obj.exists():
            missing.append(str(item))
            continue
        if make_link(item, obj):
            relinked += 1
        if set_readonly(obj):
            permissions += 1
    return FixResult(relinked=relinked, permissions=permissions, missing=tuple(missing))


def drop(
    path: str | Path,
    *,
    db_path: str | Path | None = None,
    cas_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> DropResult:
    """Delete the local object after proving one remote copy still exists.

    The symlink is left dangling: it remembers the name and the hash.
    """
    target = abs_path(path)
    cas = find_cas(target, cas_dir)
    record_id = _record_id(path)
    obj = cas_path(cas, record_id.removeprefix("sha256:"))
    if not obj.exists():
        raise InputError(f"no local copy of {record_id}")
    with db.session(db_path) as conn:
        record = records.load_file(conn, record_id)
        if not record.locations:
            raise ArchiveError(f"{record_id} has no remote copy; refusing to drop")
        hashes = records.hashes_of(record)
        has_wayback = any(loc.startswith(WAYBACK) for loc in record.locations)
        url = _wayback_url(config_path) if has_wayback else ""
        transient: TransientError | None = None
        last_error: ArchiveError | None = None
        checked = False
        for location in record.locations:
            try:
                with _backend(location, url) as backend:
                    status = backend.verify(location, hashes)
            except TransientError as exc:
                transient = exc
                continue
            except ArchiveError as exc:
                last_error = exc
                continue
            checked = True
            if status == "ok":
                obj.unlink()
                return DropResult(record_id=record_id, location=location)
    if transient is not None:
        raise transient
    if not checked and last_error is not None:
        raise last_error
    raise ArchiveError(f"no remote copy of {record_id} verified; refusing to drop")


def forget(
    path: str | Path,
    *,
    db_path: str | Path | None = None,
    cas_dir: str | Path | None = None,
) -> ForgetResult:
    """Delete records for dangling links. Refuses while the object exists."""
    root = abs_path(path)
    cas = find_cas(root, cas_dir)
    forgotten: set[str] = set()
    kept: list[str] = []
    with db.session(db_path) as conn:
        for item in walk_files(root):
            named = link_hash(item)
            if named is None:
                continue
            if cas_path(cas, named).exists():
                kept.append(str(item))
                continue
            record_id = f"sha256:{named}"
            if db.delete(conn, record_id):
                forgotten.add(record_id)
    return ForgetResult(forgotten=tuple(sorted(forgotten)), kept=tuple(kept))


def match(
    remote: str,
    *,
    db_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> MatchResult:
    """Register remote copies of known content. One listing, no transfer."""
    url = _wayback_url(config_path) if remote.startswith(WAYBACK) else ""
    with db.session(db_path) as conn:
        index = records.hash_index(conn)
        matched = unknown = no_hash = 0
        with _backend(remote, url) as backend:
            for item in backend.list():
                if not item.hashes:
                    no_hash += 1
                    continue
                ids = {index[token] for token in item.hashes if token in index}
                if not ids:
                    unknown += 1
                    continue
                for record_id in sorted(ids):
                    records.register(conn, record_id, [item.location])
                    matched += 1
                conn.commit()
    return MatchResult(matched=matched, unknown=unknown, no_hash=no_hash)


def copy(
    path: str | Path,
    remote: str,
    *,
    db_path: str | Path | None = None,
    cas_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> CopyResult:
    """Check the destination, transfer only what is missing, register results.

    Content is uploaded under its meaningful tree names, never hash names.
    Run add first: copy operates on CAS links whose object is present.
    """
    root = abs_path(path)
    cas = find_cas(root, cas_dir)
    base = root if root.is_dir() and not root.is_symlink() else root.parent
    url = _wayback_url(config_path) if remote.startswith(WAYBACK) else ""
    with db.session(db_path) as conn:
        entries: list[Entry] = []
        for item in walk_files(root):
            named = link_hash(item)
            if named is None or not cas_path(cas, named).exists():
                continue
            record = records.load_file(conn, f"sha256:{named}")
            entries.append(
                Entry(
                    path=item,
                    relpath=Path(os.path.relpath(item, base)).as_posix(),
                    size=record.size,
                    hashes=records.hashes_of(record),
                )
            )
        registered: set[str] = set()
        with _backend(remote, url) as backend:
            present = {token for item in backend.list() for token in item.hashes}
            missing = [entry for entry in entries if not set(entry.hashes.tokens) & present]
            wanted = {
                token: entry.hashes.record_id for entry in missing for token in entry.hashes.tokens
            }
            for item in backend.copy(root, missing):
                ids = {wanted[token] for token in item.hashes if token in wanted}
                for record_id in sorted(ids):
                    records.register(conn, record_id, [item.location])
                    registered.add(item.location)
                if ids:
                    conn.commit()
    return CopyResult(
        copied=len(missing),
        present=len(entries) - len(missing),
        locations=tuple(sorted(registered)),
    )


def verify(
    file: str | Path,
    remotes: Sequence[str] = (),
    *,
    db_path: str | Path | None = None,
    cas_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> VerifyResult:
    """Check content against local and remote copies. Reports, never prunes.

    Works with no local file: the hash comes from a dangling link, a CAS
    object name or the record id. "local" is a pseudo-location.
    """
    record_id = _record_id(file)
    try:
        cas = find_cas(abs_path(file), cas_dir)
    except InputError:
        cas = None
    url = _wayback_url(config_path)
    checks: list[Check] = []
    with db.session(db_path) as conn:
        record = records.load_file(conn, record_id)
        hashes = records.hashes_of(record)
        if not remotes or "local" in remotes:
            if cas is None:
                local_status: Status = "unknown"
            else:
                obj = cas_path(cas, hashes.sha256)
                if not obj.exists():
                    local_status = "missing"
                else:
                    local_status = "ok" if hash_file(obj).sha256 == hashes.sha256 else "mismatch"
            checks.append(Check(location="local", status=local_status))
        wanted = [item for item in remotes if item != "local"]
        for location in record.locations:
            if remotes and not any(selects(location, item, url) for item in wanted):
                continue
            with _backend(location, url) as backend:
                checks.append(Check(location=location, status=backend.verify(location, hashes)))
    return VerifyResult(record_id=record_id, checks=tuple(checks))


def get(
    file: str | Path,
    remote: str | None = None,
    *,
    db_path: str | Path | None = None,
    cas_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> GetResult:
    """Restore a local object from a remote copy, verifying the bytes.

    The DB resolves addresses; the download itself is the proof. A mismatch
    never reaches the CAS.
    """
    target = abs_path(file)
    cas = find_cas(target, cas_dir)
    record_id = _record_id(file)
    is_tree_path = (
        not is_hash(str(file).removeprefix("sha256:"))
        and cas not in target.parents
        and target != cas
    )
    url = _wayback_url(config_path)
    with db.session(db_path) as conn:
        record = records.load_file(conn, record_id)
        hashes = records.hashes_of(record)
        obj = cas_path(cas, hashes.sha256)
        if obj.exists():
            if is_tree_path and (not target.exists() or target.is_symlink()):
                make_link(target, obj)
            return GetResult(record_id=record_id, location="local", path=str(obj))
        candidates = [
            location
            for location in record.locations
            if remote is None or selects(location, remote, url)
        ]
        if not candidates and remote is not None:
            with _backend(remote, url) as backend:
                candidates = [
                    item.location
                    for item in backend.list()
                    if set(item.hashes) & set(hashes.tokens)
                ]
        transient: TransientError | None = None
        for location in candidates:
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / hashes.sha256
                try:
                    with _backend(location, url) as backend:
                        backend.fetch(location, dest)
                except TransientError as exc:
                    transient = exc
                    continue
                except ArchiveError:
                    continue
                if not dest.exists() or hash_file(dest).sha256 != hashes.sha256:
                    continue
                ingest(dest, cas, hashes)
            records.register(conn, record_id, [location])
            conn.commit()
            if is_tree_path and (not target.exists() or target.is_symlink()):
                make_link(target, obj)
            return GetResult(record_id=record_id, location=location, path=str(obj))
    if transient is not None:
        raise transient
    raise ArchiveError(f"no remote copy of {record_id} could be fetched and verified")


def remove_location(
    file: str | Path,
    location: str,
    *,
    db_path: str | Path | None = None,
) -> File:
    """Drop one recorded remote copy after a verify report."""
    record_id = _record_id(file)
    with db.session(db_path) as conn:
        return records.remove_location(conn, record_id, location)
