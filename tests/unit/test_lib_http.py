from urllib.parse import parse_qs

import pytest
import requests

from pkit.lib.http import HttpClient, HttpError


def test_request_returns_json(http_server):
    http_server.route("GET", "/thing", json={"a": 1})

    with HttpClient() as http:
        response = http.request("GET", f"{http_server.url}/thing")

    assert response.status_code == 200
    assert response.json() == {"a": 1}


def test_request_sends_headers_and_params(http_server):
    http_server.route("GET", "/thing", json={})

    with HttpClient(headers={"Accept": "application/json"}) as http:
        http.request("GET", f"{http_server.url}/thing", params={"q": "x"})

    sent = http_server.requests[-1]
    assert sent["headers"]["Accept"] == "application/json"
    assert sent["path"] == "/thing?q=x"


def test_request_posts_form_data(http_server):
    http_server.route("POST", "/submit", json={"ok": True})

    with HttpClient() as http:
        response = http.request(
            "POST", f"{http_server.url}/submit", data={"url": "http://x", "flag": 1}
        )

    assert response.json() == {"ok": True}
    sent = http_server.requests[-1]
    assert parse_qs(sent["body"]) == {"url": ["http://x"], "flag": ["1"]}


def test_request_maps_http_error_with_body(http_server):
    http_server.route("GET", "/thing", status=404, text="nope")

    with HttpClient() as http, pytest.raises(HttpError) as exc:
        http.request("GET", f"{http_server.url}/thing")

    assert exc.value.status_code == 404
    assert exc.value.body == "nope"
    assert f"HTTP 404: {http_server.url}/thing" in str(exc.value)


def test_unrouted_request_is_404(http_server):
    with HttpClient() as http, pytest.raises(HttpError) as exc:
        http.request("GET", f"{http_server.url}/nowhere")

    assert exc.value.status_code == 404


def test_request_http_error_without_response(monkeypatch):
    def raise_http_error(method, url, **kwargs):
        raise requests.HTTPError("boom", response=None)

    with HttpClient() as http:
        monkeypatch.setattr(http._session, "request", raise_http_error)
        with pytest.raises(HttpError) as exc:
            http.request("GET", "https://example.com/thing")

    assert exc.value.status_code is None
    assert "boom" in str(exc.value)


def test_request_maps_transport_error(monkeypatch):
    def raise_timeout(method, url, **kwargs):
        raise requests.exceptions.ConnectTimeout("timed out")

    with HttpClient() as http:
        monkeypatch.setattr(http._session, "request", raise_timeout)
        with pytest.raises(HttpError) as exc:
            http.request("GET", "https://example.com/thing")

    assert exc.value.status_code is None


def test_request_timeout_override_is_forwarded(http_server, monkeypatch):
    http_server.route("GET", "/thing", json={})
    sent: dict[str, object] = {}

    with HttpClient(timeout=5.0) as http:
        real_request = http._session.request

        def spy(method, url, **kwargs):
            sent.update(kwargs)
            return real_request(method, url, **kwargs)

        monkeypatch.setattr(http._session, "request", spy)
        http.request("GET", f"{http_server.url}/thing", timeout=0.5)

    assert sent["timeout"] == 0.5


def test_download_writes_file(http_server, tmp_path):
    http_server.route("GET", "/thing", text="bytes")

    dest = tmp_path / "obj" / "file.bin"
    with HttpClient() as http:
        assert http.download(f"{http_server.url}/thing", dest) == dest

    assert dest.read_bytes() == b"bytes"


def test_download_maps_http_error(http_server, tmp_path):
    http_server.route("GET", "/thing", status=500, text="exploded")

    with HttpClient() as http, pytest.raises(HttpError) as exc:
        http.download(f"{http_server.url}/thing", tmp_path / "gone.bin")

    assert exc.value.status_code == 500
    assert not (tmp_path / "gone.bin").exists()


def test_context_manager_closes_session(monkeypatch):
    called = []
    with HttpClient() as http:
        monkeypatch.setattr(http._session, "close", lambda: called.append(True))
    assert called == [True]
