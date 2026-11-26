"""Personal archive manager: CAS + symlinks locally, a location log in meta.

Three layers, three truths:
  git         names, directory layout, history (symlinks are committed)
  filesystem  local content presence (.git/pkit-archive objects)
  meta db     content identity (hashes, first tree name) + remote locations

Every command takes a tree path and works on the CAS links under it; a
single file, a CAS path or a "sha256:<hex>" id addresses one record.
Library only: results are dataclasses, failures raise typed exceptions.
"""

import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

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
from pkit.archive.db import ArchiveDB, hashes_of
from pkit.archive.errors import ArchiveError, InputError, TransientError
from pkit.archive.rclone import RcloneBackend, map_rclone_error
from pkit.archive.remote import WAYBACK, Status, selects
from pkit.archive.wayback import STAGE_REMOTE, WaybackBackend
from pkit.lib import Progress, config, shell
from pkit.lib.rclone import RcloneError, list_paths
from pkit.lib.rclone import delete as rclone_delete
from pkit.meta.models import File

__all__ = [
    "AddResult",
    "ArchiveError",
    "Check",
    "CleanupResult",
    "CopyResult",
    "DropResult",
    "FixResult",
    "ForgetResult",
    "GetResult",
    "InputError",
    "MatchResult",
    "TransientError",
    "VerifyResult",
    "add",
    "cleanup",
    "copy",
    "drop",
    "fix",
    "forget",
    "get",
    "match",
    "remove_location",
    "verify",
    "verify_wayback",
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


@dataclass(frozen=True)
class CleanupResult:
    """What a stage cleanup found and did."""

    remote: str
    age: str
    eligible: int
    dry_run: bool


def _wayback_url(config_path: str | Path | None) -> str:
    data = config.load(config_path)
    return config.resolve(data, "archive", {"wayback_url": ""})["wayback_url"]


def _backend(remote: str, wayback_url: str) -> RcloneBackend | WaybackBackend:
    if remote.startswith(WAYBACK):
        return WaybackBackend(remote.removeprefix(WAYBACK), base_url=wayback_url)
    return RcloneBackend(remote)


def _record_ids(value: str | Path) -> tuple[str, ...]:
    """Resolve an input to record ids; a tree yields one per CAS link.

    A hash id, a CAS object path or a single link is one id. A tree with
    no CAS links resolves to nothing; an unresolvable input raises.
    """
    digest = str(value).removeprefix("sha256:")
    if is_hash(digest):
        return (f"sha256:{digest}",)
    path = abs_path(value)
    named = link_hash(path) or (path.name if is_hash(path.name) else None)
    if named:
        return (f"sha256:{named}",)
    if path.is_dir() and not path.is_symlink():
        found = {named for item in walk_files(path) if (named := link_hash(item)) is not None}
        return tuple(f"sha256:{item}" for item in sorted(found))
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
    with ArchiveDB(db_path) as db:
        for item in walk_files(root):
            named = link_hash(item)
            if item.is_symlink():
                obj = cas_path(cas, named) if named else None
                if obj is None or not obj.exists():
                    skipped += 1
                    continue
                stored = db.get(f"sha256:{named}")
                stale = not isinstance(stored, File) or not (
                    stored.sha256 and stored.sha1 and stored.md5
                )
                if stale:
                    db.file_record(hash_file(obj), obj.stat().st_size, item.name)
                    db.commit()
                if make_link(item, obj):
                    relinked += 1
                continue
            size = item.stat().st_size
            hashes = hash_file(item)
            obj = ingest(item, cas, hashes)
            make_link(item, obj)
            db.file_record(hashes, size, item.name)
            db.commit()
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
) -> tuple[DropResult, ...]:
    """Delete local objects after proving one remote copy still exists.

    A tree path drops every CAS link under it; the first refusal wins and
    already-dropped objects stay dropped. The symlinks are left dangling:
    they remember the name and the hash.
    """
    cas = find_cas(abs_path(path), cas_dir)
    results: list[DropResult] = []
    with ArchiveDB(db_path) as db:
        for record_id in _record_ids(path):
            obj = cas_path(cas, record_id.removeprefix("sha256:"))
            if not obj.exists():
                raise InputError(f"no local copy of {record_id}")
            record = db.load_file(record_id)
            if not record.locations:
                raise ArchiveError(f"{record_id} has no remote copy; refusing to drop")
            hashes = hashes_of(record)
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
                    results.append(DropResult(record_id=record_id, location=location))
                    break
            else:
                if transient is not None:
                    raise transient
                if not checked and last_error is not None:
                    raise last_error
                raise ArchiveError(f"no remote copy of {record_id} verified; refusing to drop")
    return tuple(results)


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
    with ArchiveDB(db_path) as db:
        for item in walk_files(root):
            named = link_hash(item)
            if named is None:
                continue
            if cas_path(cas, named).exists():
                kept.append(str(item))
                continue
            record_id = f"sha256:{named}"
            if db.delete(record_id):
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
    with ArchiveDB(db_path) as db:
        index = db.hash_index()
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
                    db.register(record_id, [item.location])
                    matched += 1
                db.commit()
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
    with ArchiveDB(db_path) as db:
        entries: list[Entry] = []
        for item in walk_files(root):
            named = link_hash(item)
            if named is None or not cas_path(cas, named).exists():
                continue
            record = db.load_file(f"sha256:{named}")
            entries.append(
                Entry(
                    path=item,
                    relpath=Path(os.path.relpath(item, base)).as_posix(),
                    size=record.size,
                    hashes=hashes_of(record),
                )
            )
        registered: set[str] = set()
        with _backend(remote, url) as backend:
            present = {token for item in backend.list() for token in item.hashes}
            missing = [entry for entry in entries if not set(entry.hashes.identifiers) & present]
            wanted = {
                token: entry.hashes.record_id
                for entry in missing
                for token in entry.hashes.identifiers
            }
            for item in backend.copy(root, missing):
                ids = {wanted[token] for token in item.hashes if token in wanted}
                for record_id in sorted(ids):
                    db.register(record_id, [item.location])
                    registered.add(item.location)
                if ids:
                    db.commit()
    return CopyResult(
        copied=len(missing),
        present=len(entries) - len(missing),
        locations=tuple(sorted(registered)),
    )


def verify(
    path: str | Path,
    remotes: Sequence[str] = (),
    *,
    db_path: str | Path | None = None,
    cas_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> tuple[VerifyResult, ...]:
    """Check content against local and remote copies. Reports, never prunes.

    A tree path checks every CAS link under it, one result per record.
    Works with no local file: the hash comes from a dangling link, a CAS
    object name or the record id. "local" is a pseudo-location.
    """
    record_ids = _record_ids(path)
    try:
        cas = find_cas(abs_path(path), cas_dir)
    except InputError:
        cas = None
    url = _wayback_url(config_path)
    results: list[VerifyResult] = []
    # Read-only connection: sweeps run verify in parallel and must not
    # contend for the SQLite write lock on connect.
    with ArchiveDB(db_path, readonly=True) as db:
        for record_id in record_ids:
            record = db.load_file(record_id)
            hashes = hashes_of(record)
            checks: list[Check] = []
            if not remotes or "local" in remotes:
                if cas is None:
                    local_status: Status = "unknown"
                else:
                    obj = cas_path(cas, hashes.sha256)
                    if not obj.exists():
                        local_status = "missing"
                    else:
                        local_status = (
                            "ok" if hash_file(obj).sha256 == hashes.sha256 else "mismatch"
                        )
                checks.append(Check(location="local", status=local_status))
            wanted = [item for item in remotes if item != "local"]
            for location in record.locations:
                if remotes and not any(selects(location, item, url) for item in wanted):
                    continue
                with _backend(location, url) as backend:
                    checks.append(Check(location=location, status=backend.verify(location, hashes)))
            results.append(VerifyResult(record_id=record_id, checks=tuple(checks)))
    return tuple(results)


def get(
    path: str | Path,
    remote: str | None = None,
    *,
    db_path: str | Path | None = None,
    cas_dir: str | Path | None = None,
    config_path: str | Path | None = None,
    progress: Callable[[Progress], None] | None = None,
) -> tuple[GetResult, ...]:
    """Restore local objects from remote copies, verifying the bytes.

    A tree path restores every CAS link under it and re-points the links.
    The DB resolves addresses; the download itself is the proof. A mismatch
    never reaches the CAS. progress, when given, is forwarded to the
    backend's fetch (wayback/http downloads report it; rclone fetches are
    short and stay silent).
    """
    target = abs_path(path)
    cas = find_cas(target, cas_dir)
    is_tree = target.is_dir() and not target.is_symlink()
    if is_tree:
        wanted = [
            (item, f"sha256:{named}")
            for item in walk_files(target)
            if (named := link_hash(item)) is not None
        ]
    else:
        wanted = [(target, *_record_ids(path))]
    # ids and CAS paths have no tree link to repair; tree links and single
    # tree paths are re-pointed at the restored object
    linkable = is_tree or (
        not is_hash(str(path).removeprefix("sha256:"))
        and cas not in target.parents
        and target != cas
    )
    url = _wayback_url(config_path)
    results: list[GetResult] = []
    with ArchiveDB(db_path) as db:
        for item, record_id in wanted:
            record = db.load_file(record_id)
            hashes = hashes_of(record)
            obj = cas_path(cas, hashes.sha256)
            if obj.exists():
                if linkable and (not item.exists() or item.is_symlink()):
                    make_link(item, obj)
                results.append(GetResult(record_id=record_id, location="local", path=str(obj)))
                continue
            candidates = [
                location
                for location in record.locations
                if remote is None or selects(location, remote, url)
            ]
            if not candidates and remote is not None:
                with _backend(remote, url) as backend:
                    candidates = [
                        entry.location
                        for entry in backend.list()
                        if set(entry.hashes) & set(hashes.identifiers)
                    ]
            transient: TransientError | None = None
            for location in candidates:
                with tempfile.TemporaryDirectory() as tmp:
                    dest = Path(tmp) / hashes.sha256
                    try:
                        with _backend(location, url) as backend:
                            backend.fetch(location, dest, progress=progress)
                    except TransientError as exc:
                        transient = exc
                        continue
                    except ArchiveError:
                        continue
                    if not dest.exists() or hash_file(dest).sha256 != hashes.sha256:
                        continue
                    ingest(dest, cas, hashes)
                db.register(record_id, [location])
                db.commit()
                if linkable and (not item.exists() or item.is_symlink()):
                    make_link(item, obj)
                results.append(GetResult(record_id=record_id, location=location, path=str(obj)))
                break
            else:
                if transient is not None:
                    raise transient
                raise ArchiveError(f"no remote copy of {record_id} could be fetched and verified")
    return tuple(results)


def remove_location(
    path: str | Path,
    location: str,
    *,
    db_path: str | Path | None = None,
) -> tuple[File, ...]:
    """Drop one recorded remote copy after a verify report."""
    with ArchiveDB(db_path) as db:
        return tuple(db.remove_location(record_id, location) for record_id in _record_ids(path))


def verify_wayback(
    days: float = 32,
    *,
    db_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> tuple[VerifyResult, ...]:
    """Re-verify wayback captures older than the grace window (32 days).

    The Internet Archive indexes captures with a delay, so a fresh capture
    is weak evidence; after about a month, re-check. One result per record,
    one check per stale capture — fresh captures are left for the next run.
    Reports, never prunes; `verify` semantics, driven from the database
    instead of a tree.
    """
    url = _wayback_url(config_path)
    results: list[VerifyResult] = []
    # Read-only connection, like verify: this runs while other tools work.
    with ArchiveDB(db_path, readonly=True) as db:
        for record_id, locations in db.wayback_stale(days):
            hashes = hashes_of(db.load_file(record_id))
            checks: list[Check] = []
            for location in locations:
                with _backend(location, url) as backend:
                    status = backend.verify(location, hashes)
                checks.append(Check(location=location, status=status))
            results.append(VerifyResult(record_id=record_id, checks=tuple(checks)))
    return tuple(results)


def cleanup(
    age: str = "35d",
    *,
    dry_run: bool = False,
    remote: str = STAGE_REMOTE,
) -> CleanupResult:
    """Delete staged files older than the safety window (35 days).

    Wayback is unreliable for weeks after a capture, so staging is kept as
    a safety copy until re-verification (see verify_wayback) has had time
    to run. rclone does the age filtering; nothing younger is touched.
    """
    try:
        paths = list_paths(remote, min_age=age)
        rclone_delete(remote, min_age=age, dry_run=dry_run)
    except (RcloneError, shell.ShellError) as exc:
        map_rclone_error(exc)
    return CleanupResult(remote=remote, age=age, eligible=len(paths), dry_run=dry_run)
