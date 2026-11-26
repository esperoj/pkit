"""archive CLI: the thin shell over the pkit.archive SDK.

    pkit archive add PATH...              # ingest: CAS, symlinks, records
    pkit archive fix PATH                 # re-point links, re-assert perms
    pkit archive copy PATH REMOTE         # transfer missing, register
    pkit archive match REMOTE             # register known content, no transfer
    pkit archive verify PATH [REMOTE...]  # report local + remote integrity
    pkit archive verify-wayback          # re-verify stale wayback captures
    pkit archive drop PATH                # delete local copy once a remote is proven
    pkit archive get PATH [REMOTE]        # restore a verified copy
    pkit archive forget PATH              # delete records for dangling links
    pkit archive remove-location PATH LOCATION
    pkit archive cleanup                  # delete old staged files

Every PATH is a tree (worked per CAS link under it) or a single target: a
file, a CAS path, or a 'sha256:<hex>' id. The database and CAS root come
from config ([meta] db_path, [archive] wayback_url) and their env overrides.
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
    emit,
    end_progress,
    fail,
    progress_reporter,
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
    json_output: bool,
    plain: Callable[[Any], str | None],
    **kwargs: Any,
) -> int:
    """Call one archive SDK function, print its result, map failures."""
    try:
        result = func(**kwargs)
    except (ArchiveError, OSError, ValueError) as exc:
        return fail(str(exc), exit_code=_exit_code(exc))
    emit(result, plain(result), json_output=json_output)
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
    verify.add_argument("path", help="Tree, file, CAS path or 'sha256:<hex>' id.")
    verify.add_argument(
        "remotes",
        nargs="*",
        help="Limit checks to these remotes; 'local' works (default: all).",
    )
    verify.set_defaults(func=_verify)

    wayback_verify = sub.add_parser(
        "verify-wayback",
        parents=[_json_parent()],
        help="Re-verify wayback captures older than the grace window (default 32 days).",
    )
    wayback_verify.add_argument(
        "--days",
        type=int,
        default=32,
        help="Re-verify captures whose timestamp is older than this many days (default: 32).",
    )
    wayback_verify.set_defaults(func=_verify_wayback)

    drop = sub.add_parser(
        "drop",
        parents=[_json_parent()],
        help="Delete the local object after proving one remote copy.",
    )
    drop.add_argument("path", help="Tree, file, CAS path or 'sha256:<hex>' id.")
    drop.set_defaults(func=_drop)

    get = sub.add_parser(
        "get", parents=[_json_parent()], help="Restore a local object from a remote copy, verified."
    )
    get.add_argument("path", help="Tree, file, CAS path or 'sha256:<hex>' id.")
    get.add_argument("remote", nargs="?", default=None, help="Preferred remote (default: any).")
    get.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the download progress line (stderr).",
    )
    get.set_defaults(func=_get)

    forget = sub.add_parser(
        "forget", parents=[_json_parent()], help="Delete records for dangling links."
    )
    forget.add_argument("path", help="Tree whose dangling links to forget.")
    forget.set_defaults(func=_forget)

    remove = sub.add_parser(
        "remove-location", parents=[_json_parent()], help="Forget one recorded remote copy."
    )
    remove.add_argument("path", help="Tree, file, CAS path or 'sha256:<hex>' id.")
    remove.add_argument("location", help="Recorded location to drop.")
    remove.set_defaults(func=_remove_location)

    cleanup = sub.add_parser(
        "cleanup",
        parents=[_json_parent()],
        help="Delete staged files older than the safety window (default 35 days).",
    )
    cleanup.add_argument(
        "--age",
        default="35d",
        help="rclone duration; only staged files older than this are deleted (default: 35d).",
    )
    cleanup.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted, delete nothing.",
    )
    cleanup.set_defaults(func=_cleanup)
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
    emit(
        summary,
        f"{summary.added} added, {summary.relinked} relinked, {summary.skipped} skipped",
        json_output=args.json_output,
    )
    return EXIT_OK


def _plain_fix(result: archive.FixResult) -> str | None:
    text = f"{result.relinked} relinked, {result.permissions} permissions fixed"
    if result.missing:
        text += "\n" + "\n".join(f"missing {path}" for path in result.missing)
    return text


def _fix(args: argparse.Namespace) -> int:
    return _run(archive.fix, json_output=args.json_output, plain=_plain_fix, path=args.path)


def _plain_copy(result: archive.CopyResult) -> str | None:
    lines = [f"{result.copied} copied, {result.present} already present"]
    lines.extend(result.locations)
    return "\n".join(lines)


def _copy(args: argparse.Namespace) -> int:
    return _run(
        archive.copy,
        json_output=args.json_output,
        plain=_plain_copy,
        path=args.path,
        remote=args.remote,
    )


def _match(args: argparse.Namespace) -> int:
    return _run(
        archive.match,
        json_output=args.json_output,
        plain=lambda r: f"{r.matched} matched, {r.unknown} unknown, {r.no_hash} without hashes",
        remote=args.remote,
    )


def _plain_verify(results: tuple[archive.VerifyResult, ...]) -> str | None:
    # one record keeps the sweep's bare "location<TAB>status" line; a tree
    # prefixes the record id so every line stays attributable
    tagged = len(results) > 1
    return (
        "\n".join(
            (f"{result.record_id}\t" if tagged else "") + f"{check.location}\t{check.status}"
            for result in results
            for check in result.checks
        )
        or None
    )


def _verify(args: argparse.Namespace) -> int:
    try:
        results = archive.verify(args.path, args.remotes)
    except (ArchiveError, OSError, ValueError) as exc:
        return fail(str(exc), exit_code=_exit_code(exc))
    emit(results, _plain_verify(results), json_output=args.json_output)
    return _verify_exit(results)


def _verify_wayback(args: argparse.Namespace) -> int:
    try:
        results = archive.verify_wayback(args.days)
    except (ArchiveError, OSError, ValueError) as exc:
        return fail(str(exc), exit_code=_exit_code(exc))
    emit(results, _plain_verify(results), json_output=args.json_output)
    # No stale captures is the healthy month (unlike a tree verify, where an
    # empty report is the alert a sweep looks for); any failed check is not.
    return EXIT_OK if all(result.ok for result in results) else EXIT_ERROR


def _verify_exit(results: tuple[archive.VerifyResult, ...]) -> int:
    """A report is success only when every check passed; an empty report (a
    filter that matches nothing) is the alert a sweep looks for."""
    return EXIT_OK if results and all(result.ok for result in results) else EXIT_ERROR


def _drop(args: argparse.Namespace) -> int:
    return _run(
        archive.drop,
        json_output=args.json_output,
        path=args.path,
        plain=lambda results: (
            "\n".join(
                f"dropped {result.record_id} (verified at {result.location})" for result in results
            )
            or None
        ),
    )


def _get(args: argparse.Namespace) -> int:
    progress = progress_reporter(args.quiet)
    try:
        result = archive.get(args.path, args.remote, progress=progress)
    except (ArchiveError, OSError, ValueError) as exc:
        return fail(str(exc), exit_code=_exit_code(exc))
    end_progress(progress)
    emit(result, "\n".join(item.path for item in result) or None, json_output=args.json_output)
    return EXIT_OK


def _forget(args: argparse.Namespace) -> int:
    return _run(
        archive.forget,
        json_output=args.json_output,
        path=args.path,
        plain=lambda r: f"{len(r.forgotten)} forgotten, {len(r.kept)} kept",
    )


def _remove_location(args: argparse.Namespace) -> int:
    return _run(
        archive.remove_location,
        json_output=args.json_output,
        path=args.path,
        location=args.location,
        plain=lambda results: (
            "\n".join(
                f"{len(record.locations)} locations remain for {record.id}" for record in results
            )
            or None
        ),
    )


def _plain_cleanup(result: archive.CleanupResult) -> str:
    verb = "would delete" if result.dry_run else "deleted"
    return f"{result.eligible} staged files older than {result.age} on {result.remote.rstrip(':')} — {verb}"


def _cleanup(args: argparse.Namespace) -> int:
    return _run(
        archive.cleanup,
        json_output=args.json_output,
        age=args.age,
        dry_run=args.dry_run,
        plain=_plain_cleanup,
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run_tool(_build_parser(), argv)
