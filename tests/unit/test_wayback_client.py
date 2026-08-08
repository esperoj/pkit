from __future__ import annotations

import dataclasses

import pytest
import requests

from pkit.wayback.client import (
    BASE_URL,
    DEFAULT_LOCK_FILE,
    ENV_ACCESS_KEY,
    ENV_SECRET_KEY,
    AuthError,
    InputError,
    JobFailedError,
    JobTimeoutError,
    RateLimitError,
    SaveOptions,
    SaveResult,
    WaybackClient,
    WaybackError,
)


def test_init_uses_env_credentials(monkeypatch):
    monkeypatch.setenv(ENV_ACCESS_KEY, "env-key")
    monkeypatch.setenv(ENV_SECRET_KEY, "env-secret")

    client = WaybackClient(lock_file="")

    assert client._session.headers["Authorization"] == "LOW env-key:env-secret"
    client.close()


def test_init_explicit_credentials_override_env(monkeypatch):
    monkeypatch.setenv(ENV_ACCESS_KEY, "old-key")
    monkeypatch.setenv(ENV_SECRET_KEY, "old-secret")

    client = WaybackClient(
        api_key="new-key",
        api_secret="new-secret",
        lock_file="",
    )

    assert client._session.headers["Authorization"] == "LOW new-key:new-secret"
    client.close()


def test_init_no_auth_header_when_credentials_missing(monkeypatch):
    monkeypatch.delenv(ENV_ACCESS_KEY, raising=False)
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)

    client = WaybackClient(lock_file="")

    assert "Authorization" not in client._session.headers
    client.close()


def test_lock_file_none_uses_default():
    client = WaybackClient(lock_file=None)

    assert client.lock_file == DEFAULT_LOCK_FILE
    client.close()


def test_endpoint_applies_proxy():
    client = WaybackClient(proxy_prefix="http://proxy/", lock_file="")

    assert client._endpoint("/save") == "http://proxy/https://web.archive.org/save"

    client.close()


def test_close_closes_session(monkeypatch):
    client = WaybackClient(lock_file="")
    called = []

    monkeypatch.setattr(client._session, "close", lambda: called.append(True))

    client.close()

    assert called == [True]


def test_context_manager_closes_session(monkeypatch):
    called = []

    with WaybackClient(lock_file="") as client:
        monkeypatch.setattr(client._session, "close", lambda: called.append(True))

    assert called == [True]


def test_save_result_is_frozen():
    result = SaveResult(
        url="http://example.com",
        archive_url="http://archive",
        job_id="job1",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.url = "changed"


def test_request_returns_json_dict(requests_mock, client):
    requests_mock.get(f"{BASE_URL}/thing", json={"a": 1})

    assert client._request("GET", "/thing") == {"a": 1}


def test_request_wraps_json_list(requests_mock, client):
    requests_mock.get(f"{BASE_URL}/thing", json=[1, 2])

    assert client._request("GET", "/thing") == {"data": [1, 2]}


def test_request_returns_raw_text_when_not_json(requests_mock, client):
    requests_mock.get(f"{BASE_URL}/thing", text="hello")

    assert client._request("GET", "/thing") == {"raw_response": "hello"}


@pytest.mark.parametrize(
    ("status_code", "expected_exception"),
    [
        (401, AuthError),
        (403, AuthError),
        (429, RateLimitError),
        (500, WaybackError),
    ],
)
def test_request_http_status_exceptions(
    requests_mock,
    client,
    status_code,
    expected_exception,
):
    requests_mock.get(
        f"{BASE_URL}/thing",
        status_code=status_code,
        text="nope",
    )

    with pytest.raises(expected_exception):
        client._request("GET", "/thing")


def test_request_http_error_without_response(client, monkeypatch):
    def raise_http_error(method, url, **kwargs):
        raise requests.HTTPError("boom", response=None)

    monkeypatch.setattr(client._session, "request", raise_http_error)

    with pytest.raises(WaybackError, match="boom"):
        client._request("GET", "/thing")


def test_request_connection_error(requests_mock, client):
    requests_mock.get(
        f"{BASE_URL}/thing",
        exc=requests.exceptions.ConnectTimeout,
    )

    with pytest.raises(WaybackError):
        client._request("GET", "/thing")


def test_locked_disabled(client):
    with client._locked():
        pass


def test_locked_creates_file(tmp_path):
    lock = tmp_path / "lock"
    client = WaybackClient(lock_file=str(lock))

    with client._locked():
        assert lock.exists()

    client.close()


def test_wait_for_availability_immediate(client, monkeypatch):
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append(path)
        return {"available": "1"}

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr("pkit.wayback.client.time.sleep", lambda seconds: None)

    client._wait_for_availability()

    assert len(calls) == 1
    assert calls[0].startswith("/save/status/user")


def test_wait_for_availability_polls_until_available(client, monkeypatch):
    responses = [
        {"available": 0},
        {"available": 2},
    ]
    sleeps = []

    def fake_request(method, path, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr(
        "pkit.wayback.client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    client._wait_for_availability()

    assert sleeps == [10.0]
    assert responses == []


def test_wait_for_availability_handles_bad_available_value(client, monkeypatch):
    responses = [
        {"available": "bogus"},
        {"available": 1},
    ]
    sleeps = []

    def fake_request(method, path, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr(
        "pkit.wayback.client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    client._wait_for_availability()

    assert sleeps == [10.0]
    assert responses == []


def test_wait_for_availability_ignores_availability_errors(client, monkeypatch):
    def fake_request(method, path, **kwargs):
        raise AuthError("HTTP 401: Unauthorized")

    monkeypatch.setattr(client, "_request", fake_request)

    # Should not raise; actual save endpoint can report the real error.
    client._wait_for_availability()


def test_wait_for_availability_timeout(client, monkeypatch):
    values = iter([0.0, 301.0])
    sleeps = []

    monkeypatch.setattr(
        "pkit.wayback.client.time.monotonic",
        lambda: next(values),
    )
    monkeypatch.setattr(
        "pkit.wayback.client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    with pytest.raises(JobTimeoutError):
        client._wait_for_availability()

    assert sleeps == []


def test_submit_save_job_posts_options(client, monkeypatch):
    posted = []

    monkeypatch.setattr(client, "_wait_for_availability", lambda: None)

    def fake_request(method, path, *, data=None, timeout=None):
        posted.append((method, path, data))
        return {"job_id": "job1"}

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr("pkit.wayback.client.time.sleep", lambda seconds: None)

    opts = SaveOptions(
        capture_all=True,
        capture_outlinks=True,
        email_result=False,
        force_get=False,
        skip_first_archive=False,
    )

    job_id = client._submit_save_job("http://example.com", opts)

    assert job_id == "job1"

    method, path, data = posted[0]

    assert method == "POST"
    assert path == "/save"
    assert data["url"] == "http://example.com"
    assert data["capture_all"] == 1
    assert data["capture_outlinks"] == 1
    assert data["email_result"] == 0
    assert data["force_get"] == 0
    assert data["skip_first_archive"] == 0


def test_submit_save_job_requires_job_id(client, monkeypatch):
    monkeypatch.setattr(client, "_wait_for_availability", lambda: None)
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, *, data=None, timeout=None: {},
    )
    monkeypatch.setattr("pkit.wayback.client.time.sleep", lambda seconds: None)

    with pytest.raises(WaybackError, match="Missing job_id"):
        client._submit_save_job("http://example.com", SaveOptions())


def test_poll_save_job_success(client, monkeypatch):
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path: {
            "status": "success",
            "timestamp": "123",
        },
    )

    url = client._poll_save_job("http://example.com", "job1")

    assert url == f"{BASE_URL}/web/123id_/http://example.com"


def test_poll_save_job_pending_then_success(client, monkeypatch):
    responses = [
        {"status": "pending"},
        {
            "status": "success",
            "timestamp": "1",
            "original_url": "http://y",
        },
    ]
    sleeps = []

    def fake_request(method, path):
        return responses.pop(0)

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr(
        "pkit.wayback.client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    url = client._poll_save_job("http://x", "job1")

    assert url == f"{BASE_URL}/web/1id_/http://y"
    assert sleeps == [15.0]
    assert responses == []


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"status": "error", "error": "bad"}, "bad"),
        ({"status": "error", "message": "worse"}, "worse"),
        ({"status": "weird"}, "weird"),
    ],
)
def test_poll_save_job_failure(client, monkeypatch, payload, match):
    monkeypatch.setattr(client, "_request", lambda method, path: payload)

    with pytest.raises(JobFailedError, match=match):
        client._poll_save_job("http://x", "job1")


def test_poll_save_job_timeout(client, monkeypatch):
    values = iter([0.0, 181.0])

    monkeypatch.setattr(
        "pkit.wayback.client.time.monotonic",
        lambda: next(values),
    )

    with pytest.raises(JobTimeoutError):
        client._poll_save_job("http://x", "job1")


def test_save_url_requires_url(client):
    with pytest.raises(InputError):
        client.save_url("")


def test_save_url_success(client, monkeypatch):
    monkeypatch.setattr(client, "_submit_save_job", lambda url, opts: "job1")
    monkeypatch.setattr(client, "_poll_save_job", lambda url, job_id: "http://arch")

    result = client.save_url("http://x")

    assert result == SaveResult(
        url="http://x",
        archive_url="http://arch",
        job_id="job1",
    )


@pytest.mark.parametrize(
    "exc_class",
    [
        RateLimitError,
        JobTimeoutError,
        JobFailedError,
        WaybackError,
        AuthError,
    ],
)
def test_save_url_propagates_sdk_exceptions(client, monkeypatch, exc_class):
    def raise_submit(url, opts):
        raise exc_class("boom")

    monkeypatch.setattr(client, "_submit_save_job", raise_submit)

    with pytest.raises(exc_class):
        client.save_url("http://x")
