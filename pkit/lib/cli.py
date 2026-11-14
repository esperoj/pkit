"""Shared CLI plumbing: exit codes, stdin, output, one main-loop.

This module is intentionally generic. Tool-specific option coercion belongs
in the tool's own cli module. Only stdlib argparse is used here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

__all__ = [
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_TEMPFAIL",
    "EXIT_USAGE",
    "emit_result",
    "fail",
    "read_stdin_payload",
    "run_tool",
]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_TEMPFAIL = 75


def read_stdin_payload(
    json_mode: bool,
    primary_key: str = "url",
) -> tuple[str | None, dict[str, Any]]:
    """Read stdin in a Unix-friendly way.

    Plain mode:
        Reads one line of text. Useful for:

            echo https://example.com | tool save

    JSON mode:
        Attempts to parse stdin as JSON.

        If stdin is a JSON object, returns:

            (payload.get(primary_key), payload)

        If stdin is a JSON scalar (str/number), returns:

            (str(payload), {})

        If stdin is invalid JSON, treats the first line as plain text.
    """
    if sys.stdin.isatty():
        return None, {}

    raw = sys.stdin.read().strip()
    if not raw:
        return None, {}

    if not json_mode:
        first_line = raw.splitlines()[0].strip()
        return first_line or None, {}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        first_line = raw.splitlines()[0].strip()
        return first_line or None, {}

    if isinstance(payload, dict):
        primary = payload.get(primary_key)
        return (str(primary) if primary is not None else None), payload

    if payload is None or isinstance(payload, (bool, list)):
        return None, {}

    return str(payload), {}


def emit_result(
    result: Any,
    *,
    json_output: bool,
    plain: Callable[[Any], str | None],
) -> None:
    """Emit a result in either JSON or plain Unix output.

    For plain output, the `plain` callback should return the single string
    that should be printed to stdout.
    """
    if json_output:
        payload = (
            asdict(result) if is_dataclass(result) and not isinstance(result, type) else result
        )
        print(json.dumps(payload, indent=2))
        return

    value = plain(result)
    if value is not None:
        print(value)


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
