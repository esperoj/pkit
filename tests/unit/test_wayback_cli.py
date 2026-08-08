from __future__ import annotations

import json

import pytest

from pkit.common.cli_helpers import EXIT_ERROR, EXIT_TEMPFAIL, EXIT_USAGE
from pkit.wayback.cli import cli
from pkit.wayback.client import (
    AuthError,
    InputError,
    JobFailedError,
    JobTimeoutError,
    RateLimitError,
    SaveResult,
    WaybackClient,
    WaybackError,
)


def save_cmd(*args: str) -> list[str]:
    return ["--lock-file", "", "save", *args]


def success_result(url: str, opts=None) -> SaveResult:
    return SaveResult(
        url=url,
        archive_url="http://archive",
        job_id="job1",
    )


def test_wayback_help(runner):
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "save" in result.stdout


def test_wayback_no_args_is_help(runner):
    result = runner.invoke(cli, [])

    assert result.exit_code == EXIT_USAGE
    assert "save" in (result.stdout + result.stderr)


def test_wayback_version(runner):
    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert "0.2.0" in result.stdout


def test_save_plain_success(runner, patch_save_url):
    calls = patch_save_url(success_result)

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "http://archive"

    assert calls[0]["url"] == "http://example.com"

    opts = calls[0]["opts"]
    assert opts.capture_all is False
    assert opts.capture_outlinks is False
    assert opts.email_result is True
    assert opts.force_get is True
    assert opts.skip_first_archive is True


def test_save_json_success(runner, patch_save_url):
    patch_save_url(success_result)

    result = runner.invoke(cli, save_cmd("--json", "http://example.com"))

    assert result.exit_code == 0, result.stderr

    payload = json.loads(result.stdout)

    assert payload == {
        "url": "http://example.com",
        "archive_url": "http://archive",
        "job_id": "job1",
    }


def test_save_flags_override_defaults(runner, patch_save_url):
    calls = patch_save_url(success_result)

    args = save_cmd(
        "--capture-all",
        "--capture-outlinks",
        "--no-email",
        "--no-force-get",
        "--no-skip-first",
        "http://example.com",
    )

    result = runner.invoke(cli, args)

    assert result.exit_code == 0, result.stderr

    opts = calls[0]["opts"]
    assert opts.capture_all is True
    assert opts.capture_outlinks is True
    assert opts.email_result is False
    assert opts.force_get is False
    assert opts.skip_first_archive is False


def test_save_no_url_plain(runner, patch_save_url):
    calls = patch_save_url(success_result)

    result = runner.invoke(cli, save_cmd(), input="")

    assert result.exit_code == EXIT_USAGE
    assert "No target URL provided." in result.stderr
    assert result.stdout == ""
    assert calls == []


def test_save_reads_plain_stdin_first_line(runner, patch_save_url):
    calls = patch_save_url(success_result)

    result = runner.invoke(
        cli,
        save_cmd(),
        input="http://stdin\nhttp://ignored\n",
    )

    assert result.exit_code == 0, result.stderr
    assert calls[0]["url"] == "http://stdin"


def test_save_reads_json_object_from_stdin(runner, patch_save_url):
    calls = patch_save_url(success_result)

    payload = {
        "url": "http://json",
        "capture_all": "1",
        "email_result": "off",
        "bogus": True,
    }

    result = runner.invoke(
        cli,
        save_cmd("--json"),
        input=json.dumps(payload),
    )

    assert result.exit_code == 0, result.stderr
    assert calls[0]["url"] == "http://json"

    opts = calls[0]["opts"]
    assert opts.capture_all is True
    assert opts.email_result is False

    output = json.loads(result.stdout)
    assert output["url"] == "http://json"


def test_save_reads_json_scalar_from_stdin(runner, patch_save_url):
    calls = patch_save_url(success_result)

    result = runner.invoke(
        cli,
        save_cmd("--json"),
        input='"http://scalar"',
    )

    assert result.exit_code == 0, result.stderr
    assert calls[0]["url"] == "http://scalar"


def test_save_invalid_json_stdin_is_treated_as_url(runner, patch_save_url):
    calls = patch_save_url(success_result)

    result = runner.invoke(
        cli,
        save_cmd("--json"),
        input="http://raw",
    )

    assert result.exit_code == 0, result.stderr
    assert calls[0]["url"] == "http://raw"


def test_save_dash_reads_stdin(runner, patch_save_url):
    calls = patch_save_url(success_result)

    result = runner.invoke(
        cli,
        save_cmd("-"),
        input="http://dash\n",
    )

    assert result.exit_code == 0, result.stderr
    assert calls[0]["url"] == "http://dash"


@pytest.mark.parametrize(
    ("exc_class", "message", "expected_exit_code"),
    [
        (InputError, "No target URL provided.", EXIT_USAGE),
        (AuthError, "HTTP 401: Unauthorized", EXIT_USAGE),
        (RateLimitError, "HTTP 429: rate limit", EXIT_TEMPFAIL),
        (JobTimeoutError, "Timeout polling job abc.", EXIT_TEMPFAIL),
        (JobFailedError, "SPN2 job abc failed: 'blocked'", EXIT_ERROR),
        (WaybackError, "HTTP 500: exploded", EXIT_ERROR),
    ],
)
def test_cli_error_exit_codes(
    runner,
    patch_save_url,
    exc_class,
    message,
    expected_exit_code,
):
    def raise_error(url, opts):
        raise exc_class(message)

    patch_save_url(raise_error)

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == expected_exit_code
    assert message in result.stderr
    assert result.stdout == ""


def test_cli_unexpected_exception_exit_code(runner, patch_save_url):
    def raise_error(url, opts):
        raise RuntimeError("wat")

    patch_save_url(raise_error)

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == EXIT_ERROR
    assert "wat" in result.stderr
    assert result.stdout == ""


def test_cli_json_errors_go_to_stderr_not_stdout(runner, patch_save_url):
    def raise_error(url, opts):
        raise WaybackError("boom")

    patch_save_url(raise_error)

    result = runner.invoke(cli, save_cmd("--json", "http://example.com"))

    assert result.exit_code == EXIT_ERROR
    assert result.stdout == ""
    assert "boom" in result.stderr


def test_cli_closes_client(runner, patch_save_url, monkeypatch):
    patch_save_url(success_result)

    closed = []
    original_close = WaybackClient.close

    def spy_close(self):
        closed.append(True)
        original_close(self)

    monkeypatch.setattr("pkit.wayback.cli.WaybackClient.close", spy_close)

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == 0, result.stderr
    assert closed == [True]
