"""rclone backend: any rclone remote, addressed as "name:path".

rclone owns authentication, retries and transfer decisions. pkit only lists,
copies and checks hashes.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Self

from pkit.archive.cas import Entry, Hashes
from pkit.archive.errors import ArchiveError, InputError, TransientError
from pkit.archive.remote import RemoteFile, Status
from pkit.lib import shell

__all__ = ["RcloneBackend"]

RCLONE = "rclone"
_ALGOS = {"MD5": "md5", "SHA1": "sha1", "SHA256": "sha256"}
_NOT_FOUND = (3, 4)
_TEMPFAIL = 5


def _tokens(row: dict) -> tuple[str, ...]:
    hashes = row.get("Hashes") or {}
    out = []
    for key, value in hashes.items():
        algo = _ALGOS.get(str(key).upper().replace("-", ""))
        if algo and value:
            out.append(f"{algo}:{str(value).lower()}")
    return tuple(sorted(out))


class RcloneBackend:
    """One rclone destination prefix."""

    def __init__(self, prefix: str, *, timeout: float | None = None) -> None:
        if ":" not in prefix:
            raise InputError(f"not an rclone remote: {prefix!r}")
        self.prefix = prefix.rstrip("/")
        self.timeout = timeout

    def close(self) -> None:
        """Nothing to release; kept so both backends share a shape."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def location(self, relpath: str) -> str:
        relpath = relpath.lstrip("/")
        if not relpath:
            return self.prefix
        sep = "" if self.prefix.endswith(":") else "/"
        return f"{self.prefix}{sep}{relpath}"

    def _raise(self, exc: shell.ShellError) -> None:
        if isinstance(exc, shell.CommandNotFound):
            raise InputError(str(exc)) from exc
        if exc.returncode == _TEMPFAIL:
            raise TransientError(str(exc)) from exc
        raise ArchiveError(str(exc)) from exc

    def _run(self, args: Sequence[str]) -> str:
        try:
            return shell.run([RCLONE, *args], timeout=self.timeout).stdout
        except shell.ShellError as exc:
            self._raise(exc)
            raise AssertionError("unreachable")

    def _try_run(self, args: Sequence[str]) -> str | None:
        try:
            return shell.run([RCLONE, *args], timeout=self.timeout).stdout
        except shell.ShellError as exc:
            if exc.returncode in _NOT_FOUND:
                return None
            self._raise(exc)
            raise AssertionError("unreachable")

    # Sequence, not list: the method named `list` below shadows the builtin
    # inside the class body, so `list[...]` would not resolve as a type here.
    def _rows(self, raw: str) -> Sequence[dict]:
        try:
            data = json.loads(raw or "[]")
        except ValueError as exc:
            raise ArchiveError("rclone lsjson returned invalid JSON") from exc
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    def list(self) -> Sequence[RemoteFile]:
        """One recursive listing with hashes for the whole prefix."""
        raw = self._run(["lsjson", "--recursive", "--files-only", "--hash", self.prefix])
        out = []
        for row in self._rows(raw):
            relpath = str(row.get("Path") or row.get("Name") or "")
            out.append(RemoteFile(location=self.location(relpath), hashes=_tokens(row)))
        return out

    def put(self, source: Path, relpath: str) -> None:
        """Upload one file under a meaningful name (follows symlinks)."""
        self._run(["copyto", "--copy-links", str(source), self.location(relpath)])

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
            self._run(
                [
                    "copy",
                    "--copy-links",
                    "--checksum",
                    "--files-from-raw",
                    str(manifest),
                    str(base),
                    self.prefix,
                ]
            )
        return iter(self.list())

    def verify(self, location: str, want: Hashes) -> Status:
        """Check one object's hashes. Never prunes; the caller decides."""
        raw = self._try_run(["lsjson", "--stat", "--hash", location])
        if raw is None:
            return "missing"
        rows = self._rows(raw)
        if not rows or rows[0].get("IsDir"):
            return "missing"
        tokens = _tokens(rows[0])
        if not tokens:
            return "unknown"
        return "ok" if set(tokens) & set(want.tokens) else "mismatch"

    def fetch(self, location: str, dest: Path) -> None:
        """Download one object to a local path."""
        self._run(["copyto", location, str(dest)])
