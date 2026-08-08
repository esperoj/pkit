"""wayback-machine CLI.

This is a Click group so future commands can be added cleanly:

    wayback save URL
    wayback cdx URL
    wayback digest URL

This CLI is also mountable inside a larger BusyBox-style CLI:

    perma-tools wayback save URL
"""

from __future__ import annotations

import dataclasses
from typing import Any

import click

from .. import __version__
from ..common.cli_helpers import (
    EXIT_ERROR,
    EXIT_TEMPFAIL,
    EXIT_USAGE,
    emit_result,
    fail,
    read_stdin_payload,
)
from .client import (
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


_OPTION_FIELDS = {field.name for field in dataclasses.fields(SaveOptions)}
_FALSE_STRINGS = {"", "0", "false", "no", "off"}
_DEFAULTS = SaveOptions()


def _as_bool(value: Any) -> bool:
    """Coerce JSON option values into booleans."""
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_STRINGS
    return bool(value)


def _coerce_options(payload: dict[str, Any]) -> dict[str, bool]:
    """Extract known SaveOptions fields from a JSON payload."""
    return {key: _as_bool(value) for key, value in payload.items() if key in _OPTION_FIELDS}


def _save_options_from_params(
    params: dict[str, Any],
    payload: dict[str, Any],
) -> SaveOptions:
    """Build SaveOptions from CLI flags and optional stdin JSON payload.

    JSON payload values override CLI flag values, matching the original behavior.
    """
    values = {name: params[name] for name in _OPTION_FIELDS}
    values.update(_coerce_options(payload))
    return SaveOptions(**values)


def _exit_code_for_error(exc: Exception) -> int:
    """Map SDK exceptions to stable CLI exit codes.

    Exit-code contract:
      0  success
      1  unknown / unexpected failure
      2  usage/input/auth/config error, user can fix
      75 temporary failure, retry later
    """
    if isinstance(exc, (InputError, AuthError)):
        return EXIT_USAGE

    if isinstance(exc, (RateLimitError, JobTimeoutError)):
        return EXIT_TEMPFAIL

    return EXIT_ERROR


@click.group(
    name="wayback",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=__version__)
@click.option(
    "--key",
    "--api-key",
    "api_key",
    envvar=ENV_ACCESS_KEY,
    help=f"Internet Archive access key. [{ENV_ACCESS_KEY}]",
)
@click.option(
    "--secret",
    "--api-secret",
    "api_secret",
    envvar=ENV_SECRET_KEY,
    help=f"Internet Archive secret key. [{ENV_SECRET_KEY}]",
)
@click.option(
    "--proxy",
    "proxy_prefix",
    help="Optional proxy prefix placed before Wayback Machine URLs.",
)
@click.option(
    "--lock-file",
    type=click.Path(dir_okay=False),
    default=DEFAULT_LOCK_FILE,
    show_default=True,
    help="Lock file used to serialize SPN2 submissions. Empty string disables locking.",
)
@click.option(
    "--timeout",
    type=float,
    default=120.0,
    show_default=True,
    help="HTTP timeout in seconds.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    api_key: str | None,
    api_secret: str | None,
    proxy_prefix: str | None,
    lock_file: str,
    timeout: float,
) -> None:
    """Wayback Machine commands."""
    ctx.ensure_object(dict)

    client = WaybackClient(
        api_key=api_key,
        api_secret=api_secret,
        proxy_prefix=proxy_prefix,
        lock_file=lock_file,
        timeout=timeout,
    )

    # Namespace the client so it does not collide with other tools in a
    # BusyBox-style super CLI.
    ctx.obj["wayback.client"] = client
    ctx.call_on_close(client.close)


@cli.command(name="save")
@click.argument("url", required=False)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output JSON. If URL is omitted or '-', read JSON from stdin.",
)
@click.option(
    "--capture-all",
    is_flag=True,
    default=_DEFAULTS.capture_all,
    help="Capture all embedded page resources.",
)
@click.option(
    "--capture-outlinks",
    is_flag=True,
    default=_DEFAULTS.capture_outlinks,
    help="Capture outgoing links.",
)
@click.option(
    "--email/--no-email",
    "email_result",
    default=_DEFAULTS.email_result,
    show_default=True,
    help="Send an email notification when the job completes.",
)
@click.option(
    "--force-get/--no-force-get",
    "force_get",
    default=_DEFAULTS.force_get,
    show_default=True,
    help="Force a GET request instead of HEAD.",
)
@click.option(
    "--skip-first/--no-skip-first",
    "skip_first_archive",
    default=_DEFAULTS.skip_first_archive,
    show_default=True,
    help="Skip archiving if the URL was recently saved.",
)
@click.pass_context
def save(
    ctx: click.Context,
    url: str | None,
    json_output: bool,
    capture_all: bool,
    capture_outlinks: bool,
    email_result: bool,
    force_get: bool,
    skip_first_archive: bool,
) -> None:
    """Save one URL to the Wayback Machine using SPN2."""
    client: WaybackClient = ctx.obj["wayback.client"]

    if url == "-":
        url = None

    payload: dict[str, Any] = {}

    # Unix convention:
    #   - If URL is provided, stdin is ignored.
    #   - If URL is omitted or "-", read from stdin.
    if url is None:
        url, payload = read_stdin_payload(json_output, primary_key="url")

    if not url:
        fail("No target URL provided.", exit_code=EXIT_USAGE)

    opts = _save_options_from_params(ctx.params, payload)

<<<<<<< HEAD
    result = client.save_url(
        url,
        opts=opts,
    )

    if result.error:
        fail(
            result.error,
            result=result,
=======
    try:
        result = client.save_url(url, opts=opts)
    except WaybackError as exc:
        fail(str(exc), exit_code=_exit_code_for_error(exc))
    except Exception as exc:
        fail(str(exc), exit_code=EXIT_ERROR)
    else:
        emit_result(
            result,
>>>>>>> 4708c76 (add tests)
            json_output=json_output,
            plain=lambda r: r.archive_url,
        )


# Future commands can be added here:
#
# @cli.command(name="cdx")
# @click.argument("url", required=True)
# def cdx(url: str) -> None:
#     ...
#
# @cli.command(name="digest")
# @click.argument("url", required=True)
# def digest(url: str) -> None:
#     ...
