"""Shared CLI plumbing: exit codes, output, one main-loop.

This module is intentionally generic. Tool-specific option coercion belongs
in the tool's own cli module. Only stdlib argparse is used here.
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from pkit.lib import Progress

__all__ = [
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_TEMPFAIL",
    "EXIT_USAGE",
    "emit",
    "end_progress",
    "fail",
    "format_progress",
    "progress_reporter",
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


def _size(value: float) -> str:
    """Bytes in the KiB/MiB family: "512B", "1.5KiB", "20MiB"."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.1f}".rstrip("0").rstrip(".") + unit
        value /= 1024
    return f"{value:.1f}".rstrip("0").rstrip(".") + "TiB"


def _duration(seconds: float) -> str:
    """Seconds as "3s", "1m20s", "2h4m1s" — aria2c's own vocabulary."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def format_progress(progress: Progress) -> str:
    """One compact progress line: "12.3MiB/20MiB 61% 2.5MiB/s ETA:3s"."""
    total = _size(progress.total) if progress.total is not None else "?"
    percent = f"{100 * progress.received / progress.total:.0f}%" if progress.total else "?"
    line = f"{_size(progress.received)}/{total} {percent} {_size(progress.rate)}/s"
    if progress.eta is not None:
        line += f" ETA:{_duration(progress.eta)}"
    return line


def progress_reporter(quiet: bool) -> Callable[[Progress], None] | None:
    """The CLI-side progress callback, or None when it would be noise.

    One carriage-returned line per sample on stderr — no bar to redraw, no
    progress at all under --quiet or when stderr is not a terminal (logs and
    pipes stay clean).
    """
    if quiet or not sys.stderr.isatty():
        return None

    def report(progress: Progress) -> None:
        sys.stderr.write(format_progress(progress) + "\r")
        sys.stderr.flush()

    return report


def end_progress(reporter: Callable[[Progress], None] | None) -> None:
    """Finish the progress line after a transfer (or do nothing)."""
    if reporter is not None:
        sys.stderr.write("\n")
        sys.stderr.flush()


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
