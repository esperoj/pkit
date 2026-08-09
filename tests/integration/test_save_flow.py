from __future__ import annotations

import json
import re

import pytest

from pkit.common.cli_helpers import EXIT_ERROR, EXIT_TEMPFAIL, EXIT_USAGE
from pkit.wayback.cli import cli
from pkit.wayback.client import (
    ENV_ACCESS_KEY,
    ENV_SECRET_KEY,
    AuthError,
    WaybackClient,
)

BASE_URL = "https://web.archive.org"
USER_STATUS_RE = re.compile(r"https://web\.archive\.org/save/status/user\?.*")


def save_cmd(*args: str) -> list[str]:
    return ["--lock-file", "", "save", *args]


def test_cli_save_success(requests_mock, runner, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(f"{BASE_URL}/save", json={"job_id": "job1"})
    requests_mock.get(
        f"{BASE_URL}/save/status/job1",
        json={
            "status": "success",
            "timestamp": "123",
            "original_url": "http://example.com",
        },
    )

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == f"{BASE_URL}/web/123id_/http://example.com"


def test_cli_save_json_success(requests_mock, runner, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(f"{BASE_URL}/save", json={"job_id": "job1"})
    requests_mock.get(
        f"{BASE_URL}/save/status/job1",
        json={
            "status": "success",
            "timestamp": "123",
            "original_url": "http://example.com",
        },
    )

    result = runner.invoke(cli, save_cmd("--json", "http://example.com"))

    assert result.exit_code == 0, result.stderr

    payload = json.loads(result.stdout)

    assert payload == {
        "url": "http://example.com",
        "archive_url": f"{BASE_URL}/web/123id_/http://example.com",
        "job_id": "job1",
    }


def test_cli_auth_error(requests_mock, runner, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(
        f"{BASE_URL}/save",
        status_code=401,
        text="Unauthorized",
    )

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == EXIT_USAGE
    assert "HTTP 401: Unauthorized" in result.stderr
    assert result.stdout == ""


def test_cli_rate_limit_error(requests_mock, runner, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(
        f"{BASE_URL}/save",
        status_code=429,
        text="rate limit",
    )

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == EXIT_TEMPFAIL
    assert "HTTP 429: rate limit" in result.stderr
    assert result.stdout == ""


def test_cli_server_error(requests_mock, runner, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(
        f"{BASE_URL}/save",
        status_code=500,
        text="upstream exploded",
    )

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == EXIT_ERROR
    assert "HTTP 500: upstream exploded" in result.stderr
    assert result.stdout == ""


def test_cli_missing_job_id(requests_mock, runner, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(f"{BASE_URL}/save", json={})

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == EXIT_ERROR
    assert "Missing job_id" in result.stderr
    assert result.stdout == ""


def test_cli_job_failure(requests_mock, runner, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(f"{BASE_URL}/save", json={"job_id": "job1"})
    requests_mock.get(
        f"{BASE_URL}/save/status/job1",
        json={
            "status": "error",
            "error": "bad",
        },
    )

    result = runner.invoke(cli, save_cmd("http://example.com"))

    assert result.exit_code == EXIT_ERROR
    assert "SPN2 job job1 failed" in result.stderr
    assert result.stdout == ""


def test_cli_json_error_stdout_empty(requests_mock, runner, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(
        f"{BASE_URL}/save",
        status_code=500,
        text="upstream exploded",
    )

    result = runner.invoke(cli, save_cmd("--json", "http://example.com"))

    assert result.exit_code == EXIT_ERROR
    assert result.stdout == ""
    assert "HTTP 500: upstream exploded" in result.stderr


def test_cli_proxy_prefix_is_used(requests_mock, runner, disable_sleep):
    proxy = "http://proxy.local/"

    proxy_user_status_re = re.compile(
        r"http://proxy\.local/https://web\.archive\.org/save/status/user\?.*"
    )

    requests_mock.get(proxy_user_status_re, json={"available": 1})
    requests_mock.post(
        f"{proxy}https://web.archive.org/save",
        json={"job_id": "jobp"},
    )
    requests_mock.get(
        f"{proxy}https://web.archive.org/save/status/jobp",
        json={
            "status": "success",
            "timestamp": "9",
            "original_url": "http://proxy.example",
        },
    )

    result = runner.invoke(
        cli,
        [
            "--proxy",
            proxy,
            "--lock-file",
            "",
            "save",
            "http://proxy.example",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == f"{BASE_URL}/web/9id_/http://proxy.example"


def test_sdk_save_success(requests_mock, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(f"{BASE_URL}/save", json={"job_id": "job1"})
    requests_mock.get(
        f"{BASE_URL}/save/status/job1",
        json={
            "status": "success",
            "timestamp": "123",
            "original_url": "http://example.com",
        },
    )

    with WaybackClient(
        api_key="k",
        api_secret="s",
        lock_file="",
        timeout=0.1,
    ) as client:
        result = client.save_url("http://example.com")

    assert result.job_id == "job1"
    assert result.archive_url == f"{BASE_URL}/web/123id_/http://example.com"


def test_sdk_auth_error(requests_mock, disable_sleep, monkeypatch):
    monkeypatch.delenv(ENV_ACCESS_KEY, raising=False)
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)

    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(
        f"{BASE_URL}/save",
        status_code=401,
        text="Unauthorized",
    )

    with WaybackClient(lock_file="", timeout=0.1) as client, pytest.raises(AuthError):
        client.save_url("http://example.com")
