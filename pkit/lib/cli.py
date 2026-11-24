"""Shared CLI plumbing: exit codes, output, one main-loop.

This module is intentionally generic. Tool-specific option coercion belongs
in the tool's own cli module. Only stdlib argparse is used here.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

__all__ = [
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_TEMPFAIL",
    "EXIT_USAGE",
    "emit",
    "fail",
    "read_stdin_line",
    "run_tool",
]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_TEMPFAIL = 75


def read_stdin_line() -> str | None:
    """The first non-empty stdin line, or None on a TTY or empty stdin.

    Unix-friendly: `echo https://example.com | tool save` works without
    flags, and an interactive shell never blocks.
    """
    if sys.stdin.isatty():
        return None
    for line in sys.stdin.read().splitlines():
        line = line.strip()
        if line:
            return line
    return None


def _jsonable(result: Any) -> Any:
    """A dataclass — or a sequence of them — as JSON-ready data."""
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, (list, tuple)):
        return [_jsonable(item) for item in result]
    return result


def emit(result: Any, text: str | None, *, json_output: bool) -> None:
    """Emit a result in either JSON or plain Unix output.

    JSON prints the dataclass (or sequence of dataclasses); plain prints
    the caller-formatted text, or nothing when there is none (an empty
    verify report stays silent). stdout is data only.
    """
    if json_output:
        print(json.dumps(_jsonable(result), indent=2))
    elif text:
        print(text)


def fail(
    message: str,
    *,
    exit_code: int = EXIT_ERROR,
) -> int:
    """Print an error to stderr and return the exit code.

    Unix convention:
      - stdout is for data;
      - stderr is for diagnostics;
      - exit code is for scripting.

    Returning the code (instead of raising SystemExit) keeps `main()`
    composable: `python -m pkit.x`, console scripts and tests all call it.
    """
    print(f"Error: {message}", file=sys.stderr)
    return exit_code


def run_tool(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None = None,
) -> int:
    """The one main-loop shared by every pkit tool CLI.

    No arguments prints help and returns 2 (usage). argparse handles --help,
    --version and usage errors itself (exit 0 / 2). Handlers set with
    `set_defaults(func=...)` catch their own typed exceptions; anything
    unexpected is reported once here as a short diagnostic with exit 1.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        parser.print_help()
        return EXIT_USAGE
    namespace = parser.parse_args(args)
    try:
        return namespace.func(namespace)
    except Exception as exc:  # noqa: BLE001 - contract: unexpected failure, exit 1
        return fail(str(exc))
