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

from pkit.lib.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_TEMPFAIL,
    EXIT_USAGE,
    emit,
    fail,
    read_stdin_line,
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
_DEFAULTS = SaveOptions()


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
        help="Output JSON.",
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
    # If URL is provided, stdin is ignored; omitted or '-', read one line.
    if url is None:
        url = read_stdin_line()

    if not url:
        return fail("No target URL provided.", exit_code=EXIT_USAGE)

    opts = SaveOptions(**{name: getattr(args, name) for name in _OPTION_FIELDS})
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
    emit(result, result.archive_url, json_output=args.json_output)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    return run_tool(_build_parser(), argv)
