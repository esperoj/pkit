import json

import pytest

from pkit.lib.cli import EXIT_ERROR, EXIT_TEMPFAIL, EXIT_USAGE
from pkit.version import get_version
from pkit.wayback.cli import main
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


def test_wayback_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    assert "save" in capsys.readouterr().out


def test_wayback_no_args_is_help(capsys):
    assert main([]) == EXIT_USAGE
    assert "save" in capsys.readouterr().out


def test_wayback_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    assert get_version() in capsys.readouterr().out


def test_save_plain_success(run_cli, patch_save_url):
    calls = patch_save_url(success_result)
    code, out, err = run_cli(main, save_cmd("http://example.com"))

    assert code == 0, err
    assert out.strip() == "http://archive"
    assert calls[0]["url"] == "http://example.com"
    opts = calls[0]["opts"]
    assert opts.capture_all is False
    assert opts.capture_outlinks is False
    assert opts.email_result is True
    assert opts.force_get is True
    assert opts.skip_first_archive is False


def test_save_json_success(run_cli, patch_save_url):
    patch_save_url(success_result)
    code, out, err = run_cli(main, save_cmd("--json", "http://example.com"))

    assert code == 0, err
    payload = json.loads(out)
    assert payload == {
        "url": "http://example.com",
        "archive_url": "http://archive",
        "job_id": "job1",
    }


def test_save_flags_override_defaults(run_cli, patch_save_url):
    calls = patch_save_url(success_result)
    args = save_cmd(
        "--capture-all",
        "--capture-outlinks",
        "--no-email",
        "--no-force-get",
        "--no-skip-first",
        "http://example.com",
    )
    code, _out, err = run_cli(main, args)

    assert code == 0, err
    opts = calls[0]["opts"]
    assert opts.capture_all is True
    assert opts.capture_outlinks is True
    assert opts.email_result is False
    assert opts.force_get is False
    assert opts.skip_first_archive is False


def test_save_no_url_plain(run_cli, patch_save_url):
    calls = patch_save_url(success_result)
    code, out, err = run_cli(main, save_cmd(), input="")

    assert code == EXIT_USAGE
    assert "No target URL provided." in err
    assert out == ""
    assert calls == []


def test_save_reads_plain_stdin_first_line(run_cli, patch_save_url):
    calls = patch_save_url(success_result)
    code, _out, err = run_cli(main, save_cmd(), input="http://stdin\nhttp://ignored\n")

    assert code == 0, err
    assert calls[0]["url"] == "http://stdin"


def test_save_reads_json_payload_url_from_stdin(run_cli, patch_save_url):
    # --json affects output only; stdin stays one plain URL per line
    calls = patch_save_url(success_result)
    code, out, err = run_cli(main, save_cmd("--json"), input='{"url": "http://json"}\n')

    assert code == 0, err
    assert calls[0]["url"] == '{"url": "http://json"}'
    output = json.loads(out)
    assert output["url"] == '{"url": "http://json"}'


def test_save_dash_reads_stdin(run_cli, patch_save_url):
    calls = patch_save_url(success_result)
    code, _out, err = run_cli(main, save_cmd("-"), input="http://dash\n")

    assert code == 0, err
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
    run_cli,
    patch_save_url,
    exc_class,
    message,
    expected_exit_code,
):
    def raise_error(url, opts):
        raise exc_class(message)

    patch_save_url(raise_error)
    code, out, err = run_cli(main, save_cmd("http://example.com"))

    assert code == expected_exit_code
    assert message in err
    assert out == ""


def test_cli_json_errors_go_to_stderr_not_stdout(run_cli, patch_save_url):
    def raise_error(url, opts):
        raise WaybackError("boom")

    patch_save_url(raise_error)
    code, out, err = run_cli(main, save_cmd("--json", "http://example.com"))

    assert code == EXIT_ERROR
    assert out == ""
    assert "boom" in err


def test_cli_closes_client(run_cli, patch_save_url, monkeypatch):
    patch_save_url(success_result)
    closed = []
    original_close = WaybackClient.close

    def spy_close(self):
        closed.append(True)
        original_close(self)

    monkeypatch.setattr("pkit.wayback.cli.WaybackClient.close", spy_close)
    code, _out, err = run_cli(main, save_cmd("http://example.com"))

    assert code == 0, err
    assert closed == [True]


def test_cli_host_flag_reaches_client(run_cli, patch_save_url, monkeypatch):
    """--host flows into WaybackClient(host=...); the mirror is off only when asked."""
    captured: dict[str, object] = {}

    class SpyClient(WaybackClient):
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr("pkit.wayback.cli.WaybackClient", SpyClient)
    patch_save_url(success_result)
    code, _out, err = run_cli(main, ["--host", "mirror.example", *save_cmd("http://example.com")])

    assert code == 0, err
    assert captured["host"] == "mirror.example"


def test_cli_help_lists_host(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])

    out = capsys.readouterr().out
    assert "--host" in out
    assert "PKIT_WAYBACK_HOST" in out
    assert "--proxy" in out
