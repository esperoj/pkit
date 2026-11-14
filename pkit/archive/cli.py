"""archive CLI: the thin shell over the pkit.archive SDK.

    pkit archive add PATH...              # ingest: CAS, symlinks, records
    pkit archive fix PATH                 # re-point links, re-assert perms
    pkit archive copy PATH REMOTE         # transfer missing, register
    pkit archive match REMOTE             # register known content, no transfer
    pkit archive verify FILE [REMOTE...] # report local + remote integrity
    pkit archive drop PATH               # delete local copy once a remote is proven
    pkit archive get FILE [REMOTE]       # restore a verified copy
    pkit archive forget PATH              # delete records for dangling links
    pkit archive remove-location FILE LOCATION

Paths and remotes come from the tree; the database and CAS root come from
config ([meta] db_path, [archive] wayback_url) and their env overrides.
"""

import argparse
from collections.abc import Callable, Sequence
from typing import Any

from pkit import archive
from pkit.archive.errors import ArchiveError, InputError, TransientError
from pkit.lib.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_TEMPFAIL,
    EXIT_USAGE,
    emit_result,
    fail,
    run_tool,
)
from pkit.version import get_version

__all__ = ["DESCRIPTION", "main"]

DESCRIPTION = "Personal archive manager: content-addressed store, symlinks, location log."


def _exit_code(exc: Exception) -> int:
    """Map archive exceptions to stable CLI exit codes."""
    if isinstance(exc, (InputError, OSError, ValueError)):
        return EXIT_USAGE
    if isinstance(exc, TransientError):
        return EXIT_TEMPFAIL
    return EXIT_ERROR


def _run(
    func: Callable[..., Any],
    *,
    args: argparse.Namespace,
    plain: Callable[[Any], str | None],
    **kwargs: Any,
) -> int:
    """Call one archive SDK function, print its result, map failures."""
    try:
        result = func(**kwargs)
    except (ArchiveError, OSError, ValueError) as exc:
        return fail(str(exc), exit_code=_exit_code(exc))
    emit_result(result, json_output=args.json_output, plain=plain)
    return EXIT_OK


def _json_parent() -> argparse.ArgumentParser:
    """Shared flag so every subcommand accepts --json after the command."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output the result as JSON.",
    )
    return parent


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkit archive", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_version()}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    add = sub.add_parser(
        "add", parents=[_json_parent()], help="Move content into the CAS, leave symlinks, register."
    )
    add.add_argument("paths", nargs="+", help="Tree or file to ingest.")
    add.set_defaults(func=_add)

    fix = sub.add_parser(
        "fix", parents=[_json_parent()], help="Re-point CAS symlinks at the current root."
    )
    fix.add_argument("path", help="Tree to repair.")
    fix.set_defaults(func=_fix)

    copy = sub.add_parser(
        "copy",
        parents=[_json_parent()],
        help="Transfer missing content to a remote, then register.",
    )
    copy.add_argument("path", help="Tree whose CAS links hold the content.")
    copy.add_argument("remote", help="rclone remote or 'wayback:' prefix.")
    copy.set_defaults(func=_copy)

    match = sub.add_parser(
        "match",
        parents=[_json_parent()],
        help="Register remote copies of known content (no transfer).",
    )
    match.add_argument("remote", help="rclone remote or 'wayback:' prefix.")
    match.set_defaults(func=_match)

    verify = sub.add_parser(
        "verify", parents=[_json_parent()], help="Check content against local and remote copies."
    )
    verify.add_argument("file", help="Tree path, CAS path or 'sha256:<hex>' id.")
    verify.add_argument(
        "remotes",
        nargs="*",
        help="Limit checks to these remotes; 'local' works (default: all).",
    )
    verify.set_defaults(func=_verify)

    drop = sub.add_parser(
        "drop",
        parents=[_json_parent()],
        help="Delete the local object after proving one remote copy.",
    )
    drop.add_argument("path", help="Tree path, CAS path or 'sha256:<hex>' id.")
    drop.set_defaults(func=_drop)

    get = sub.add_parser(
        "get", parents=[_json_parent()], help="Restore a local object from a remote copy, verified."
    )
    get.add_argument("file", help="Tree path, CAS path or 'sha256:<hex>' id.")
    get.add_argument("remote", nargs="?", default=None, help="Preferred remote (default: any).")
    get.set_defaults(func=_get)

    forget = sub.add_parser(
        "forget", parents=[_json_parent()], help="Delete records for dangling links."
    )
    forget.add_argument("path", help="Tree whose dangling links to forget.")
    forget.set_defaults(func=_forget)

    remove = sub.add_parser(
        "remove-location", parents=[_json_parent()], help="Forget one recorded remote copy."
    )
    remove.add_argument("file", help="Tree path, CAS path or 'sha256:<hex>' id.")
    remove.add_argument("location", help="Recorded location to drop.")
    remove.set_defaults(func=_remove_location)
    return parser


def _add(args: argparse.Namespace) -> int:
    added = relinked = skipped = 0
    try:
        for path in args.paths:
            result = archive.add(path)
            added += result.added
            relinked += result.relinked
            skipped += result.skipped
    except (ArchiveError, OSError, ValueError) as exc:
        return fail(str(exc), exit_code=_exit_code(exc))
    summary = archive.AddResult(added, relinked, skipped)
    emit_result(
        summary,
        json_output=args.json_output,
        plain=lambda r: f"{r.added} added, {r.relinked} relinked, {r.skipped} skipped",
    )
    return EXIT_OK


def _plain_fix(result: archive.FixResult) -> str:
    text = f"{result.relinked} relinked, {result.permissions} permissions fixed"
    if result.missing:
        text += "\n" + "\n".join(f"missing {path}" for path in result.missing)
    return text


def _fix(args: argparse.Namespace) -> int:
    return _run(archive.fix, args=args, path=args.path, plain=_plain_fix)


def _plain_copy(result: archive.CopyResult) -> str:
    lines = [f"{result.copied} copied, {result.present} already present"]
    lines.extend(result.locations)
    return "\n".join(lines)


def _copy(args: argparse.Namespace) -> int:
    return _run(archive.copy, args=args, path=args.path, remote=args.remote, plain=_plain_copy)


def _match(args: argparse.Namespace) -> int:
    return _run(
        archive.match,
        args=args,
        remote=args.remote,
        plain=lambda r: f"{r.matched} matched, {r.unknown} unknown, {r.no_hash} without hashes",
    )


def _verify(args: argparse.Namespace) -> int:
    try:
        result = archive.verify(args.file, args.remotes)
    except (ArchiveError, OSError, ValueError) as exc:
        return fail(str(exc), exit_code=_exit_code(exc))
    if args.json_output:
        emit_result(result, json_output=True, plain=lambda r: None)
    else:
        for check in result.checks:
            print(f"{check.location}\t{check.status}")
    # A report is success only when every check passed; a mismatch or a
    # missing copy is the alert a sweep looks for.
    return EXIT_OK if result.ok else EXIT_ERROR


def _drop(args: argparse.Namespace) -> int:
    return _run(
        archive.drop,
        args=args,
        path=args.path,
        plain=lambda r: f"dropped {r.record_id} (verified at {r.location})",
    )


def _get(args: argparse.Namespace) -> int:
    return _run(
        archive.get,
        args=args,
        file=args.file,
        remote=args.remote,
        plain=lambda r: r.path,
    )


def _forget(args: argparse.Namespace) -> int:
    return _run(
        archive.forget,
        args=args,
        path=args.path,
        plain=lambda r: f"{len(r.forgotten)} forgotten, {len(r.kept)} kept",
    )


def _remove_location(args: argparse.Namespace) -> int:
    return _run(
        archive.remove_location,
        args=args,
        file=args.file,
        location=args.location,
        plain=lambda r: f"{len(r.locations)} locations remain for {r.id}",
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run_tool(_build_parser(), argv)
