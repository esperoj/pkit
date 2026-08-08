"""Shared helpers for building Unix-friendly Click CLIs.

This module is intentionally generic. It should not contain Wayback-specific
logic. Wayback-specific option coercion belongs in the Wayback CLI module.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, NoReturn

import click


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

        If stdin is a JSON scalar, returns:

            (str(payload), {})

        If stdin is invalid JSON, treats it as plain text.
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
        return raw, {}

    if isinstance(payload, dict):
        primary = payload.get(primary_key)
        return (str(primary) if primary is not None else None), payload

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
        click.echo(json.dumps(payload, indent=2))
        return

    value = plain(result)
    if value is not None:
        click.echo(value)


def fail(
    message: str,
    *,
    exit_code: int = EXIT_ERROR,
) -> NoReturn:
    """Print an error to stderr and exit non-zero.

    Unix convention:
      - stdout is for data;
      - stderr is for diagnostics;
      - exit code is for scripting.
    """
    click.echo(f"Error: {message}", err=True)
    raise SystemExit(exit_code)
