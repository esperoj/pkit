"""Wayback Machine CLI (argparse, stdlib only).

    pkit wayback save URL          # save one URL via SPN2
    pkit-wayback save URL          # same tool, standalone entry
    python -m pkit.wayback save URL

Tool options come before the command; save options after it:

    pkit wayback --key K --secret S save --json URL
"""

import argparse
import dataclasses
from collections.abc import Sequence
from typing import Any

from pkit.lib.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_TEMPFAIL,
    EXIT_USAGE,
    emit_result,
    fail,
    read_stdin_payload,
    run_tool,
)
from pkit.version import get_version
from pkit.wayback.client import (
    DEFAULT_HOST,
    DEFAULT_LOCK_FILE,
    ENV_ACCESS_KEY,
    ENV_SECRET_KEY,
    AuthError,
    InputError,
    JobTimeoutError,
    RateLimitError,
    SaveOptions,
    WaybackClient,
    WaybackError,
)

__all__ = ["DESCRIPTION", "main"]

DESCRIPTION = "Save URLs to the Wayback Machine (SPN2)."

_OPTION_FIELDS = {field.name for field in dataclasses.fields(SaveOptions)}
_FALSE_STRINGS = {"", "0", "false", "no", "off"}
_DEFAULTS = SaveOptions()


def _as_bool(value: Any) -> bool:
    """Coerce JSON option values into booleans."""
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_STRINGS
    return bool(value)


def _save_options(args: argparse.Namespace, payload: dict[str, Any]) -> SaveOptions:
    """Build SaveOptions from CLI flags; stdin JSON payload values win."""
    values = {name: getattr(args, name) for name in _OPTION_FIELDS}
    values.update({key: _as_bool(value) for key, value in payload.items() if key in _OPTION_FIELDS})
    return SaveOptions(**values)


def _exit_code_for_error(exc: Exception) -> int:
    """Map SDK exceptions to stable CLI exit codes.

    Exit-code contract:

        0   success
        1   unknown / unexpected failure
        2   usage/input/auth/config error, user can fix
        75  temporary failure, retry later
    """
    if isinstance(exc, (InputError, AuthError)):
        return EXIT_USAGE
    if isinstance(exc, (RateLimitError, JobTimeoutError)):
        return EXIT_TEMPFAIL
    return EXIT_ERROR


def _add_flag(parser: argparse.ArgumentParser, name: str, help_text: str) -> None:
    """Add one SaveOptions flag with its --no- counterpart (stdlib pattern)."""
    parser.add_argument(
        f"--{name.replace('_', '-')}",
        dest=name,
        action=argparse.BooleanOptionalAction,
        default=getattr(_DEFAULTS, name),
        help=help_text,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pkit wayback", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_version()}")
    parser.add_argument(
        "--key",
        "--api-key",
        dest="api_key",
        default=None,
        help=f"Internet Archive access key (env {ENV_ACCESS_KEY}).",
    )
    parser.add_argument(
        "--secret",
        "--api-secret",
        dest="api_secret",
        default=None,
        help=f"Internet Archive secret key (env {ENV_SECRET_KEY}).",
    )
    parser.add_argument(
        "--proxy",
        dest="proxy_prefix",
        default=None,
        help="Proxy prefix placed before canonical Wayback URLs (disables the mirror host).",
    )
    parser.add_argument(
        "--host",
        dest="host",
        default=None,
        help=(
            "Wayback mirror host for web.archive.org URLs "
            f"(env PKIT_WAYBACK_HOST, [wayback] host; default {DEFAULT_HOST}; "
            "'web.archive.org' or '' disables the rewrite)."
        ),
    )
    parser.add_argument(
        "--lock-file",
        dest="lock_file",
        default=DEFAULT_LOCK_FILE,
        help=f"Lock file serializing SPN2 submissions; '' disables (default: {DEFAULT_LOCK_FILE}).",
    )
    parser.add_argument(
        "--timeout",
        dest="timeout",
        type=float,
        default=120.0,
        help="HTTP timeout in seconds (default: 120).",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    save = sub.add_parser("save", help="Save one URL to the Wayback Machine using SPN2.")
    save.add_argument("url", nargs="?", default=None, help="Target URL; omit or '-' to read stdin.")
    save.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output JSON. If URL is omitted or '-', read JSON from stdin.",
    )
    _add_flag(save, "capture_all", "Capture all embedded page resources.")
    _add_flag(save, "capture_outlinks", "Capture outgoing links.")
    _add_flag(save, "email_result", "Send an email notification when the job completes.")
    _add_flag(save, "force_get", "Force a GET request instead of HEAD.")
    _add_flag(save, "skip_first_archive", "Skip archiving if the URL was recently saved.")
    save.set_defaults(func=_save)
    return parser


def _save(args: argparse.Namespace) -> int:
    url = None if args.url == "-" else args.url
    payload: dict[str, Any] = {}

    # Unix convention:
    #   - If URL is provided, stdin is ignored.
    #   - If URL is omitted or "-", read from stdin.
    if url is None:
        url, payload = read_stdin_payload(args.json_output, primary_key="url")

    if not url:
        return fail("No target URL provided.", exit_code=EXIT_USAGE)

    opts = _save_options(args, payload)
    with WaybackClient(
        api_key=args.api_key,
        api_secret=args.api_secret,
        proxy_prefix=args.proxy_prefix,
        host=args.host,
        lock_file=args.lock_file,
        timeout=args.timeout,
    ) as client:
        try:
            result = client.save_url(url, opts=opts)
        except WaybackError as exc:
            return fail(str(exc), exit_code=_exit_code_for_error(exc))
    emit_result(result, json_output=args.json_output, plain=lambda r: r.archive_url)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    return run_tool(_build_parser(), argv)
