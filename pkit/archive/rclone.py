"""rclone backend: any rclone remote, addressed as "name:path".

The adapter half of the rclone story: pkit.lib.rclone runs the binary and
reads its exit codes; this module turns rows into RemoteFiles with hash
tokens, verifies them against a Hashes set, and copies Entry manifests
under meaningful names. rclone itself owns authentication, retries and
transfer decisions; pkit only lists, copies and checks hashes.
"""

import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import NoReturn

from pkit.archive.cas import Entry, Hashes
from pkit.archive.errors import ArchiveError, InputError, TransientError
from pkit.archive.remote import RemoteFile, Status
from pkit.lib import Closeable, rclone, shell

__all__ = ["RcloneBackend", "map_rclone_error"]

_ALGOS = {"MD5": "md5", "SHA1": "sha1", "SHA256": "sha256"}


def map_rclone_error(exc: BaseException) -> NoReturn:
    """Raise the archive error standing in for one lib rclone failure."""
    if isinstance(exc, shell.CommandNotFound):
        raise InputError(str(exc)) from exc
    if isinstance(exc, rclone.TransientError):
        raise TransientError(str(exc)) from exc
    raise ArchiveError(str(exc)) from exc


def _tokens(row: dict) -> tuple[str, ...]:
    hashes = row.get("Hashes") or {}
    out = []
    for key, value in hashes.items():
        algo = _ALGOS.get(str(key).upper().replace("-", ""))
        if algo and value:
            out.append(f"{algo}:{str(value).lower()}")
    return tuple(sorted(out))


class RcloneBackend(Closeable):
    """One rclone destination prefix."""

    def __init__(self, prefix: str, *, timeout: float | None = None) -> None:
        if ":" not in prefix:
            raise InputError(f"not an rclone remote: {prefix!r}")
        self.prefix = prefix.rstrip("/")
        self.timeout = timeout

    def location(self, relpath: str) -> str:
        relpath = relpath.lstrip("/")
        if not relpath:
            return self.prefix
        sep = "" if self.prefix.endswith(":") else "/"
        return f"{self.prefix}{sep}{relpath}"

    def _call(self, func, *args, **kwargs):
        """One lib rclone call, with archive's error mapping."""
        try:
            return func(*args, **kwargs)
        except (rclone.RcloneError, shell.ShellError) as exc:
            map_rclone_error(exc)

    # Sequence, not list: the method named `list` below shadows the builtin
    # inside the class body, so `list[...]` would not resolve as a type here.
    def list(self) -> Sequence[RemoteFile]:
        """One recursive listing with hashes for the whole prefix."""
        out = []
        for row in self._call(rclone.lsjson, self.prefix, timeout=self.timeout):
            relpath = str(row.get("Path") or row.get("Name") or "")
            out.append(RemoteFile(location=self.location(relpath), hashes=_tokens(row)))
        return out

    def put(self, source: Path, relpath: str) -> None:
        """Upload one file under a meaningful name (follows symlinks)."""
        self._call(rclone.copyto, source, self.location(relpath), timeout=self.timeout)

    def copy(self, root: Path, entries: Sequence[Entry]) -> Iterator[RemoteFile]:
        """Transfer missing entries by relative path, then report what landed."""
        if not entries:
            return iter(())
        base = root if root.is_dir() and not root.is_symlink() else root.parent
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "files.txt"
            manifest.write_text(
                "\n".join(entry.relpath for entry in entries) + "\n",
                encoding="utf-8",
            )
            self._call(rclone.copy_files, base, self.prefix, manifest, timeout=self.timeout)
        return iter(self.list())

    def verify(self, location: str, want: Hashes) -> Status:
        """Check one object's hashes. Never prunes; the caller decides."""
        row = self._call(rclone.statjson, location, timeout=self.timeout)
        if row is None or row.get("IsDir"):
            return "missing"
        tokens = _tokens(row)
        if not tokens:
            return "unknown"
        return "ok" if set(tokens) & set(want.identifiers) else "mismatch"

    def fetch(self, location: str, dest: Path, *, progress=None) -> None:
        """Download one object to a local path.

        progress is accepted for backend-protocol symmetry and unused:
        rclone is silent on success and per-object fetches are short.
        """
        self._call(rclone.copyto, location, dest, timeout=self.timeout)
