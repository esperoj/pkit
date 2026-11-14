"""meta CLI: dump, load, and check the record database.

pkit meta dump [REPO]      # database -> <repo>/<table>.json
pkit meta load [SOURCE]    # JSON repo or source URL -> database
pkit meta validate         # orphan link rows, duplicate ids
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from pkit.lib.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_TEMPFAIL,
    EXIT_USAGE,
    fail,
    run_tool,
)
from pkit.lib.http import HttpError
from pkit.meta import db, dump, load
from pkit.version import get_version

__all__ = ["DESCRIPTION", "main"]

DESCRIPTION = "Record database: JSON repo dump/load, URL import, and integrity checks."

_REPO_HELP = "repo directory (default: [meta] repo_path)"
_SOURCE_HELP = "JSON repo path or source URL (default: [meta] repo_path)"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkit meta", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_version()}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    dump_cmd = sub.add_parser("dump", help="Write every record table to <repo>/<table>.json.")
    dump_cmd.add_argument("repo", nargs="?", default=None, help=_REPO_HELP)
    dump_cmd.set_defaults(func=_dump)

    load_cmd = sub.add_parser(
        "load", help="Load records from a JSON repo or source URL into the database."
    )
    load_cmd.add_argument("source", nargs="?", default=None, help=_SOURCE_HELP)
    load_cmd.set_defaults(func=_load)

    validate_cmd = sub.add_parser("validate", help="Report orphan link rows and duplicate ids.")
    validate_cmd.set_defaults(func=_validate)
    return parser


def _dump(args: argparse.Namespace) -> int:
    try:
        count = dump.dump(repo_path=args.repo)
    except (OSError, ValueError) as exc:
        return fail(str(exc), exit_code=EXIT_USAGE)
    print(count)
    return EXIT_OK


def _load(args: argparse.Namespace) -> int:
    try:
        count = load.load(args.source)
    except (OSError, ValueError) as exc:
        return fail(str(exc), exit_code=EXIT_USAGE)
    except HttpError as exc:
        code = (
            EXIT_TEMPFAIL
            if exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500
            else EXIT_ERROR
        )
        return fail(str(exc), exit_code=code)
    print(count)
    return EXIT_OK


def _validate(_: argparse.Namespace) -> int:
    with db.session(None) as conn:
        issues = db.validate(conn)
    for issue in issues:
        print(issue)
    return EXIT_OK if not issues else EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    return run_tool(_build_parser(), argv)
