"""meta CLI: sync the record database with a JSON repo, and check it.

pkit meta dump [REPO]      # database -> <repo>/<table>.json
pkit meta load [REPO]      # JSON repo -> database
pkit meta validate         # orphan link rows, duplicate ids
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from pkit.lib.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    fail,
    run_tool,
)
from pkit.meta import db, sync
from pkit.version import get_version

__all__ = ["DESCRIPTION", "main"]

DESCRIPTION = "Record database: JSON repo sync and integrity checks."

_REPO_HELP = "repo directory (default: [meta] repo_path)"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkit meta", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_version()}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    dump = sub.add_parser("dump", help="Write every record table to <repo>/<table>.json.")
    dump.add_argument("repo", nargs="?", default=None, help=_REPO_HELP)
    dump.set_defaults(func=_dump)

    load = sub.add_parser("load", help="Load records from a JSON repo into the database.")
    load.add_argument("repo", nargs="?", default=None, help=_REPO_HELP)
    load.set_defaults(func=_load)

    validate = sub.add_parser("validate", help="Report orphan link rows and duplicate ids.")
    validate.set_defaults(func=_validate)
    return parser


def _dump(args: argparse.Namespace) -> int:
    try:
        count = sync.dump(repo_path=args.repo)
    except (OSError, ValueError) as exc:
        return fail(str(exc), exit_code=EXIT_USAGE)
    print(count)
    return EXIT_OK


def _load(args: argparse.Namespace) -> int:
    try:
        count = sync.load(repo_path=args.repo)
    except (OSError, ValueError) as exc:
        return fail(str(exc), exit_code=EXIT_USAGE)
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
