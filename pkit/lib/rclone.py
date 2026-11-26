"""General rclone CLI wrapper: invocation, exit-code reading, listings.

rclone owns authentication, retries and transfer decisions; this module owns
what every caller would otherwise duplicate — list-args invocation with no
shell, the exit-code reading (3/4 nothing found, 5 retry later, anything
else failed), and lsjson parsing. Reusable by pkit.archive's backend,
scripts and any other project:

    from pkit.lib import rclone

    rows = rclone.lsjson("ia:bucket/prefix")
    rclone.delete("pkit-archive:", min_age="35d")

Errors follow the lib convention — callers map these onto their own
taxonomy. shell.CommandNotFound (rclone not installed) passes through
untouched, like any other missing binary.
"""

import json
from collections.abc import Sequence
from pathlib import Path

from pkit.lib import shell

__all__ = [
    "RCLONE",
    "NotFoundError",
    "RcloneError",
    "TransientError",
    "copy_files",
    "copyto",
    "delete",
    "list_paths",
    "lsjson",
    "run",
    "statjson",
]

RCLONE = "rclone"
_NOT_FOUND = (3, 4)
_TRANSIENT = (5,)


class RcloneError(Exception):
    """rclone exited non-zero, other than not-found or transient."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class NotFoundError(RcloneError):
    """rclone found no such file or directory (exit 3 or 4)."""


class TransientError(RcloneError):
    """A retry-able rclone failure (exit 5): retry later."""


def _map(exc: shell.ShellError) -> RcloneError:
    """One shell failure onto the rclone error it means."""
    if exc.returncode in _NOT_FOUND:
        return NotFoundError(str(exc), returncode=exc.returncode, stderr=exc.stderr)
    if exc.returncode in _TRANSIENT:
        return TransientError(str(exc), returncode=exc.returncode, stderr=exc.stderr)
    return RcloneError(str(exc), returncode=exc.returncode, stderr=exc.stderr)


def run(args: Sequence[str], *, timeout: float | None = None) -> str:
    """Run one rclone command with list args; return stdout, raise on failure.

    shell.CommandNotFound (rclone not installed) passes through untouched,
    like any other missing binary; callers treat it as a user-fixable setup
    problem.
    """
    try:
        return shell.run([RCLONE, *args], timeout=timeout).stdout
    except shell.CommandNotFound:
        raise
    except shell.ShellError as exc:
        raise _map(exc) from exc


def try_run(args: Sequence[str], *, timeout: float | None = None) -> str | None:
    """Run one rclone command; None when rclone reports nothing found."""
    try:
        return run(args, timeout=timeout)
    except NotFoundError:
        return None


def _rows(raw: str) -> list[dict]:
    """Parse lsjson output: one row, a list of rows, or nothing useful."""
    try:
        data = json.loads(raw or "[]")
    except ValueError as exc:
        raise RcloneError("rclone lsjson returned invalid JSON") from exc
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def lsjson(prefix: str, *, timeout: float | None = None) -> list[dict]:
    """One recursive file listing with hashes for a whole prefix."""
    return _rows(run(["lsjson", "--recursive", "--files-only", "--hash", prefix], timeout=timeout))


def statjson(location: str, *, timeout: float | None = None) -> dict | None:
    """One object's stat row with hashes; None when nothing is there."""
    raw = try_run(["lsjson", "--stat", "--hash", location], timeout=timeout)
    if raw is None:
        return None
    rows = _rows(raw)
    return rows[0] if rows else None


def copyto(source: str | Path, dest: str, *, timeout: float | None = None) -> None:
    """Copy one file (following symlinks, rclone --copy-links)."""
    run(["copyto", "--copy-links", str(source), dest], timeout=timeout)


def copy_files(
    root: str | Path, prefix: str, manifest: str | Path, *, timeout: float | None = None
) -> None:
    """Copy a manifest of relative paths from root into prefix, checksummed."""
    run(
        [
            "copy",
            "--copy-links",
            "--checksum",
            "--files-from-raw",
            str(manifest),
            str(root),
            prefix,
        ],
        timeout=timeout,
    )


def list_paths(prefix: str, *, min_age: str = "", timeout: float | None = None) -> list[str]:
    """Relative file paths under a prefix, optionally older than min_age."""
    args = ["lsf", "--recursive", "--files-only"]
    if min_age:
        args.append(f"--min-age={min_age}")
    args.append(prefix)
    return [line for line in run(args, timeout=timeout).splitlines() if line]


def delete(
    prefix: str, *, min_age: str = "", dry_run: bool = False, timeout: float | None = None
) -> None:
    """Delete files under a prefix, optionally only older than min_age."""
    args = ["delete"]
    if dry_run:
        args.append("--dry-run")
    if min_age:
        args.append(f"--min-age={min_age}")
    args.append(prefix)
    run(args, timeout=timeout)
