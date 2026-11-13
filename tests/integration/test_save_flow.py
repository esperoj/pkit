from __future__ import annotations

import json
import re

import pytest

from pkit.lib.cli import EXIT_ERROR, EXIT_TEMPFAIL, EXIT_USAGE
from pkit.wayback.cli import main
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


def test_cli_save_success(run_cli, requests_mock, disable_sleep):
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

    code, out, err = run_cli(main, save_cmd("http://example.com"))

    assert code == 0, err
    assert out.strip() == f"{BASE_URL}/web/123id_/http://example.com"


def test_cli_save_json_success(run_cli, requests_mock, disable_sleep):
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

    code, out, err = run_cli(main, save_cmd("--json", "http://example.com"))

    assert code == 0, err
    payload = json.loads(out)
    assert payload == {
        "url": "http://example.com",
        "archive_url": f"{BASE_URL}/web/123id_/http://example.com",
        "job_id": "job1",
    }


def test_cli_auth_error(run_cli, requests_mock, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(
        f"{BASE_URL}/save",
        status_code=401,
        text="Unauthorized",
    )

    code, out, err = run_cli(main, save_cmd("http://example.com"))

    assert code == EXIT_USAGE
    assert "HTTP 401: Unauthorized" in err
    assert out == ""


def test_cli_rate_limit_error(run_cli, requests_mock, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(
        f"{BASE_URL}/save",
        status_code=429,
        text="rate limit",
    )

    code, out, err = run_cli(main, save_cmd("http://example.com"))

    assert code == EXIT_TEMPFAIL
    assert "HTTP 429: rate limit" in err
    assert out == ""


def test_cli_server_error(run_cli, requests_mock, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(
        f"{BASE_URL}/save",
        status_code=500,
        text="upstream exploded",
    )

    code, out, err = run_cli(main, save_cmd("http://example.com"))

    assert code == EXIT_ERROR
    assert "HTTP 500: upstream exploded" in err
    assert out == ""


def test_cli_missing_job_id(run_cli, requests_mock, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(f"{BASE_URL}/save", json={})

    code, out, err = run_cli(main, save_cmd("http://example.com"))

    assert code == EXIT_ERROR
    assert "Missing job_id" in err
    assert out == ""


def test_cli_job_failure(run_cli, requests_mock, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(f"{BASE_URL}/save", json={"job_id": "job1"})
    requests_mock.get(
        f"{BASE_URL}/save/status/job1",
        json={
            "status": "error",
            "error": "bad",
        },
    )

    code, out, err = run_cli(main, save_cmd("http://example.com"))

    assert code == EXIT_ERROR
    assert "SPN2 job job1 failed" in err
    assert out == ""


def test_cli_json_error_stdout_empty(run_cli, requests_mock, disable_sleep):
    requests_mock.get(USER_STATUS_RE, json={"available": 1})
    requests_mock.post(
        f"{BASE_URL}/save",
        status_code=500,
        text="upstream exploded",
    )

    code, out, err = run_cli(main, save_cmd("--json", "http://example.com"))

    assert code == EXIT_ERROR
    assert out == ""
    assert "HTTP 500: upstream exploded" in err


def test_cli_proxy_prefix_is_used(run_cli, requests_mock, disable_sleep):
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

    code, out, err = run_cli(
        main,
        [
            "--proxy",
            proxy,
            "--lock-file",
            "",
            "save",
            "http://proxy.example",
        ],
    )

    assert code == 0, err
    assert out.strip() == f"{BASE_URL}/web/9id_/http://proxy.example"


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
