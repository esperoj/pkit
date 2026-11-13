"""pkit: BusyBox-style CLI for personal tools.

    pkit COMMAND [ARGS...]     # run one tool
    pkit --help                # list tools

Each tool also runs standalone, three ways:

    pkit-meta | python -m pkit.meta     (same for wayback and archive)
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from pkit.archive import cli as archive_cli
from pkit.lib.cli import EXIT_OK, EXIT_USAGE, fail
from pkit.meta import cli as meta_cli
from pkit.version import get_version
from pkit.wayback import cli as wayback_cli

__all__ = ["main"]

_TOOLS = (
    ("archive", archive_cli),
    ("meta", meta_cli),
    ("wayback", wayback_cli),
)


def _print_usage() -> None:
    print("usage: pkit [--version] COMMAND [ARGS...]")
    print()
    print("commands:")
    for name, module in _TOOLS:
        print(f"  {name:<9} {module.DESCRIPTION}")
    print()
    print("Each tool also runs standalone: pkit-<name> or python -m pkit.<name>.")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch `pkit COMMAND ARGS...` to one tool CLI."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _print_usage()
        return EXIT_USAGE
    head = args[0]
    if head in {"-h", "--help"}:
        _print_usage()
        return EXIT_OK
    if head == "--version":
        print(f"pkit {get_version()}")
        return EXIT_OK
    for name, module in _TOOLS:
        if head == name:
            return module.main(args[1:])
    return fail(f"unknown command {head!r}; see 'pkit --help'", exit_code=EXIT_USAGE)
