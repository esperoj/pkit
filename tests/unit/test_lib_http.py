from __future__ import annotations

import pytest
import requests

from pkit.lib.http import HttpClient, HttpError

URL = "https://example.com/thing"


def test_request_returns_response(requests_mock):
    requests_mock.get(URL, json={"a": 1})

    with HttpClient() as http:
        response = http.request("GET", URL)

    assert response.status_code == 200
    assert response.json() == {"a": 1}


def test_request_sends_headers_and_params(requests_mock):
    requests_mock.get(URL, json={})

    with HttpClient(headers={"Accept": "application/json"}) as http:
        http.request("GET", URL, params={"q": "x"})

    sent = requests_mock.last_request
    assert sent.headers["Accept"] == "application/json"
    assert sent.qs == {"q": ["x"]}


def test_request_maps_http_error_with_body(requests_mock):
    requests_mock.get(URL, status_code=404, text="nope")

    with HttpClient() as http, pytest.raises(HttpError) as exc:
        http.request("GET", URL)

    assert exc.value.status_code == 404
    assert exc.value.body == "nope"
    assert f"HTTP 404: {URL}" in str(exc.value)


def test_request_http_error_without_response(monkeypatch):
    def raise_http_error(method, url, **kwargs):
        raise requests.HTTPError("boom", response=None)

    http = HttpClient()
    monkeypatch.setattr(http._session, "request", raise_http_error)

    with pytest.raises(HttpError) as exc:
        http.request("GET", URL)

    assert exc.value.status_code is None
    assert "boom" in str(exc.value)
    http.close()


def test_request_maps_transport_error(requests_mock):
    requests_mock.get(URL, exc=requests.exceptions.ConnectTimeout)

    with HttpClient() as http, pytest.raises(HttpError) as exc:
        http.request("GET", URL)

    assert exc.value.status_code is None


def test_request_timeout_override_is_accepted(requests_mock):
    requests_mock.get(URL, json={})

    with HttpClient(timeout=5.0) as http:
        http.request("GET", URL, timeout=0.5)

    assert requests_mock.last_request.timeout == 0.5


def test_download_writes_file(requests_mock, tmp_path):
    requests_mock.get(URL, content=b"bytes")

    dest = tmp_path / "obj" / "file.bin"
    with HttpClient() as http:
        assert http.download(URL, dest) == dest

    assert dest.read_bytes() == b"bytes"


def test_download_maps_http_error(requests_mock, tmp_path):
    requests_mock.get(URL, status_code=500, text="exploded")

    with HttpClient() as http, pytest.raises(HttpError) as exc:
        http.download(URL, tmp_path / "gone.bin")

    assert exc.value.status_code == 500


def test_context_manager_closes_session(monkeypatch):
    called = []
    with HttpClient() as http:
        monkeypatch.setattr(http._session, "close", lambda: called.append(True))
    assert called == [True]
