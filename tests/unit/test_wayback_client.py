from __future__ import annotations

import pytest

from pkit.lib.http import HttpError
from pkit.wayback.client import (
    BASE_URL,
    DEFAULT_LOCK_FILE,
    ENV_ACCESS_KEY,
    ENV_SECRET_KEY,
    AuthError,
    CdxCapture,
    InputError,
    JobFailedError,
    JobTimeoutError,
    RateLimitError,
    SaveOptions,
    SaveResult,
    WaybackClient,
    WaybackError,
)


def make_client(http) -> WaybackClient:
    return WaybackClient(api_key="k", api_secret="s", lock_file="", timeout=0.1, http=http)


def test_init_uses_env_credentials(http_server, monkeypatch):
    monkeypatch.setenv(ENV_ACCESS_KEY, "env-key")
    monkeypatch.setenv(ENV_SECRET_KEY, "env-secret")
    http_server.route("GET", f"/{BASE_URL}/thing", json={"a": 1})

    with WaybackClient(lock_file="", proxy_prefix=f"{http_server.url}/") as client:
        client._request("GET", "/thing")

    assert http_server.requests[-1]["headers"]["Authorization"] == "LOW env-key:env-secret"


def test_init_explicit_credentials_override_env(http_server, monkeypatch):
    monkeypatch.setenv(ENV_ACCESS_KEY, "old-key")
    monkeypatch.setenv(ENV_SECRET_KEY, "old-secret")
    http_server.route("GET", f"/{BASE_URL}/thing", json={"a": 1})

    with WaybackClient(
        api_key="new-key", api_secret="new-secret", lock_file="", proxy_prefix=f"{http_server.url}/"
    ) as client:
        client._request("GET", "/thing")

    assert http_server.requests[-1]["headers"]["Authorization"] == "LOW new-key:new-secret"


def test_init_no_auth_header_when_credentials_missing(http_server, monkeypatch):
    monkeypatch.delenv(ENV_ACCESS_KEY, raising=False)
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    http_server.route("GET", f"/{BASE_URL}/thing", json={"a": 1})

    with WaybackClient(lock_file="", proxy_prefix=f"{http_server.url}/") as client:
        client._request("GET", "/thing")

    assert "Authorization" not in http_server.requests[-1]["headers"]


def test_lock_file_none_uses_default(fake_http):
    client = WaybackClient(lock_file=None, http=fake_http)
    assert client.lock_file == DEFAULT_LOCK_FILE
    client.close()


def test_endpoint_applies_proxy(fake_http):
    client = WaybackClient(proxy_prefix="http://proxy/", lock_file="", http=fake_http)
    assert client._endpoint("/save") == "http://proxy/https://web.archive.org/save"
    client.close()


def test_close_closes_owned_http(monkeypatch):
    called = []
    with WaybackClient(lock_file="") as client:
        monkeypatch.setattr(client._http, "close", lambda: called.append(True))
        client.close()
        assert called == [True]


def test_context_manager_closes_owned_http(monkeypatch):
    called = []
    with WaybackClient(lock_file="") as client:
        monkeypatch.setattr(client._http, "close", lambda: called.append(True))
    assert called == [True]


def test_close_keeps_injected_http(fake_http, monkeypatch):
    """An injected transport belongs to the caller; the client must not close it."""
    called = []
    monkeypatch.setattr(fake_http, "close", lambda: called.append(True))
    client = make_client(fake_http)
    client.close()
    assert called == []


def test_request_returns_json_dict(fake_http):
    fake_http.on(f"{BASE_URL}/thing", json={"a": 1})
    with make_client(fake_http) as client:
        assert client._request("GET", "/thing") == {"a": 1}


def test_request_wraps_json_list(fake_http):
    fake_http.on(f"{BASE_URL}/thing", json=[1, 2])
    with make_client(fake_http) as client:
        assert client._request("GET", "/thing") == {"data": [1, 2]}


def test_request_returns_raw_text_when_not_json(fake_http):
    fake_http.on(f"{BASE_URL}/thing", text="hello")
    with make_client(fake_http) as client:
        assert client._request("GET", "/thing") == {"raw_response": "hello"}


def test_request_maps_transport_error(fake_http):
    fake_http.on(f"{BASE_URL}/thing", error=HttpError("connection reset"))
    with make_client(fake_http) as client, pytest.raises(WaybackError, match="connection reset"):
        client._request("GET", "/thing")


@pytest.mark.parametrize(
    ("status_code", "expected_exception"),
    [
        (401, AuthError),
        (403, AuthError),
        (429, RateLimitError),
        (500, WaybackError),
    ],
)
def test_request_http_status_exceptions(fake_http, status_code, expected_exception):
    fake_http.on(f"{BASE_URL}/thing", status=status_code, text="nope")
    with make_client(fake_http) as client, pytest.raises(expected_exception):
        client._request("GET", "/thing")


def test_locked_disabled(client):
    with client._locked():
        pass


def test_locked_creates_file(tmp_path, fake_http):
    lock = tmp_path / "lock"
    client = WaybackClient(lock_file=str(lock), http=fake_http)
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


def test_poll_save_job_success_without_timestamp(client, monkeypatch):
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path: {"status": "success", "timestamp": ""},
    )

    with pytest.raises(JobFailedError, match="without timestamp"):
        client._poll_save_job("http://example.com", "job1")


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


def test_save_url_full_flow_over_http(fake_http, disable_sleep):
    fake_http.on(f"{BASE_URL}/save/status/user", json={"available": 1})
    fake_http.on(f"{BASE_URL}/save", json={"job_id": "job1"})
    fake_http.on(
        f"{BASE_URL}/save/status/job1",
        json={"status": "success", "timestamp": "123", "original_url": "http://example.com"},
    )

    with make_client(fake_http) as client:
        result = client.save_url("http://example.com")

    assert result == SaveResult(
        url="http://example.com",
        archive_url=f"{BASE_URL}/web/123id_/http://example.com",
        job_id="job1",
    )
    post = next(request for request in fake_http.requests if request["method"] == "POST")
    assert post["url"] == f"{BASE_URL}/save"


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


def test_cdx_parses_rows_and_skips_header(fake_http):
    fake_http.on(
        f"{BASE_URL}/cdx/search/cdx",
        json=[
            ["original", "timestamp", "digest", "statuscode", "mimetype"],
            ["https://host/a", "20260101000000", "ABC", "200", "application/pdf"],
        ],
    )
    with make_client(fake_http) as client:
        rows = client.cdx("https://host/", match_type="prefix")
    assert rows == [
        CdxCapture(
            original="https://host/a",
            timestamp="20260101000000",
            digest="ABC",
            statuscode="200",
            mimetype="application/pdf",
        )
    ]


def test_cdx_empty_body_is_empty_list(fake_http):
    fake_http.on(f"{BASE_URL}/cdx/search/cdx", text="")
    with make_client(fake_http) as client:
        assert client.cdx("https://host/a") == []


def test_cdx_requires_url(fake_http):
    with make_client(fake_http) as client, pytest.raises(InputError):
        client.cdx("")


def test_cdx_sends_query(fake_http):
    fake_http.on(f"{BASE_URL}/cdx/search/cdx", json=[])
    with make_client(fake_http) as client:
        client.cdx("https://host/a", match_type="prefix", limit=5)
    url = fake_http.requests[0]["url"]
    assert "matchType=prefix" in url
    assert "limit=5" in url
    assert "url=https%3A%2F%2Fhost%2Fa" in url
