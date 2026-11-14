"""The wayback save flow over a real local HTTP server.

The CLI talks to a genuine http.server through --proxy, so authentication
headers, form bodies and status mapping all cross the wire for real.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from pkit.lib.cli import EXIT_ERROR, EXIT_TEMPFAIL, EXIT_USAGE
from pkit.wayback.cli import main
from pkit.wayback.client import BASE_URL, WaybackClient

# Request path the local server sees behind --proxy (e.g. /https://web.archive.org/save).
WB = f"/{BASE_URL}"


def save_cmd(*args: str) -> list[str]:
    return ["--lock-file", "", "save", *args]


def route_success(server) -> None:
    server.route("GET", f"{WB}/save/status/user", json={"available": 1})
    server.route("POST", f"{WB}/save", json={"job_id": "job1"})
    server.route(
        "GET",
        f"{WB}/save/status/job1",
        json={"status": "success", "timestamp": "123", "original_url": "http://example.com"},
    )


def posted(server) -> dict:
    return next(request for request in server.requests if request["method"] == "POST")


def test_cli_save_success(run_cli, http_server, disable_sleep):
    route_success(http_server)

    code, out, err = run_cli(
        main,
        ["--proxy", f"{http_server.url}/", *save_cmd("http://example.com")],
    )

    assert code == 0, err
    assert out.strip() == f"{BASE_URL}/web/123id_/http://example.com"


def test_cli_save_sends_credentials_and_form(run_cli, http_server, disable_sleep):
    route_success(http_server)

    code, _out, err = run_cli(
        main,
        [
            "--key",
            "cli-key",
            "--secret",
            "cli-secret",
            "--proxy",
            f"{http_server.url}/",
            *save_cmd("--capture-all", "--no-email", "http://example.com"),
        ],
    )

    assert code == 0, err
    post = posted(http_server)
    assert post["headers"]["Authorization"] == "LOW cli-key:cli-secret"
    form = parse_qs(post["body"])
    assert form["url"] == ["http://example.com"]
    assert form["capture_all"] == ["1"]
    assert form["email_result"] == ["0"]
    assert form["force_get"] == ["1"]


def test_cli_save_json_success(run_cli, http_server, disable_sleep):
    route_success(http_server)

    code, out, err = run_cli(
        main,
        ["--proxy", f"{http_server.url}/", *save_cmd("--json", "http://example.com")],
    )

    assert code == 0, err
    assert json.loads(out) == {
        "url": "http://example.com",
        "archive_url": f"{BASE_URL}/web/123id_/http://example.com",
        "job_id": "job1",
    }


def test_cli_auth_error(run_cli, http_server, disable_sleep):
    http_server.route("GET", f"{WB}/save/status/user", json={"available": 1})
    http_server.route("POST", f"{WB}/save", status=401, text="Unauthorized")

    code, out, err = run_cli(
        main,
        ["--proxy", f"{http_server.url}/", *save_cmd("http://example.com")],
    )

    assert code == EXIT_USAGE
    assert "HTTP 401: Unauthorized" in err
    assert out == ""


def test_cli_rate_limit_error(run_cli, http_server, disable_sleep):
    http_server.route("GET", f"{WB}/save/status/user", json={"available": 1})
    http_server.route("POST", f"{WB}/save", status=429, text="rate limit")

    code, out, err = run_cli(
        main,
        ["--proxy", f"{http_server.url}/", *save_cmd("http://example.com")],
    )

    assert code == EXIT_TEMPFAIL
    assert "HTTP 429: rate limit" in err
    assert out == ""


def test_cli_server_error(run_cli, http_server, disable_sleep):
    http_server.route("GET", f"{WB}/save/status/user", json={"available": 1})
    http_server.route("POST", f"{WB}/save", status=500, text="upstream exploded")

    code, out, err = run_cli(
        main,
        ["--proxy", f"{http_server.url}/", *save_cmd("http://example.com")],
    )

    assert code == EXIT_ERROR
    assert "HTTP 500: upstream exploded" in err
    assert out == ""


def test_cli_missing_job_id(run_cli, http_server, disable_sleep):
    http_server.route("GET", f"{WB}/save/status/user", json={"available": 1})
    http_server.route("POST", f"{WB}/save", json={})

    code, out, err = run_cli(
        main,
        ["--proxy", f"{http_server.url}/", *save_cmd("http://example.com")],
    )

    assert code == EXIT_ERROR
    assert "Missing job_id" in err
    assert out == ""


def test_cli_job_failure(run_cli, http_server, disable_sleep):
    http_server.route("GET", f"{WB}/save/status/user", json={"available": 1})
    http_server.route("POST", f"{WB}/save", json={"job_id": "job1"})
    http_server.route("GET", f"{WB}/save/status/job1", json={"status": "error", "error": "bad"})

    code, out, err = run_cli(
        main,
        ["--proxy", f"{http_server.url}/", *save_cmd("http://example.com")],
    )

    assert code == EXIT_ERROR
    assert "SPN2 job job1 failed" in err
    assert out == ""


def test_cli_json_error_stdout_empty(run_cli, http_server, disable_sleep):
    http_server.route("GET", f"{WB}/save/status/user", json={"available": 1})
    http_server.route("POST", f"{WB}/save", status=500, text="upstream exploded")

    code, out, err = run_cli(
        main,
        ["--proxy", f"{http_server.url}/", *save_cmd("--json", "http://example.com")],
    )

    assert code == EXIT_ERROR
    assert out == ""
    assert "HTTP 500: upstream exploded" in err


def test_sdk_save_success(http_server, disable_sleep):
    route_success(http_server)

    with WaybackClient(
        api_key="k",
        api_secret="s",
        lock_file="",
        proxy_prefix=f"{http_server.url}/",
    ) as client:
        result = client.save_url("http://example.com")

    assert result.job_id == "job1"
    assert result.archive_url == f"{BASE_URL}/web/123id_/http://example.com"
    post = posted(http_server)
    assert post["headers"]["Authorization"] == "LOW k:s"
    assert parse_qs(post["body"])["url"] == ["http://example.com"]
