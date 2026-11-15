from pathlib import Path
from urllib.parse import parse_qs

import pytest
import requests

from pkit.lib.http import HttpClient, HttpError


def write_config(tmp_path: Path, body: str, monkeypatch) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(body, encoding="utf-8")
    monkeypatch.setenv("PKIT_CONFIG", str(config_file))


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


# --- URL rewriting: proxy_prefix -------------------------------------------


def test_resolve_url_inserts_slash_after_bare_prefix():
    http = HttpClient(proxy_prefix="https://proxy.example.com")

    assert http.resolve_url("https://example.com/a") == (
        "https://proxy.example.com/https://example.com/a"
    )


@pytest.mark.parametrize("suffix", ["/", "?", "="])
def test_resolve_url_keeps_separator_prefixes(suffix):
    http = HttpClient(proxy_prefix=f"https://proxy.example.com{suffix}")

    assert http.resolve_url("https://example.com/a") == (
        f"https://proxy.example.com{suffix}https://example.com/a"
    )


def test_resolve_url_prefix_is_idempotent():
    http = HttpClient(proxy_prefix="https://proxy.example.com")

    once = http.resolve_url("https://example.com/a")

    assert http.resolve_url(once) == once


def test_request_routes_through_proxy_prefix(http_server):
    http_server.route("GET", "/https://example.com/thing", text="ok")

    with HttpClient(proxy_prefix=http_server.url) as http:
        response = http.request("GET", "https://example.com/thing")

    assert response.text == "ok"
    assert http_server.requests[-1]["path"] == "/https://example.com/thing"


def test_download_routes_through_proxy_prefix(http_server, tmp_path):
    http_server.route("GET", "/https://example.com/thing", text="bytes")
    dest = tmp_path / "obj.bin"

    with HttpClient(proxy_prefix=http_server.url) as http:
        assert http.download("https://example.com/thing", dest) == dest

    assert dest.read_bytes() == b"bytes"


# --- URL rewriting: host_map ------------------------------------------------


def test_resolve_url_host_map_rewrites_netloc():
    http = HttpClient(host_map={"Example.COM": "mirror.example:8443"})

    assert http.resolve_url("https://example.com/a?q=1#frag") == (
        "https://mirror.example:8443/a?q=1#frag"
    )


def test_resolve_url_host_map_leaves_other_hosts_alone():
    http = HttpClient(host_map={"example.com": "mirror.example"})

    assert http.resolve_url("https://other.com/a") == "https://other.com/a"


def test_resolve_url_no_double_proxy_on_host_map_match():
    http = HttpClient(
        proxy_prefix="https://proxy.example.com/", host_map={"example.com": "mirror.example"}
    )

    assert http.resolve_url("https://example.com/a") == "https://mirror.example/a"
    assert http.resolve_url("https://other.com/a") == (
        "https://proxy.example.com/https://other.com/a"
    )


def test_resolve_url_plain_client_is_identity():
    with HttpClient() as http:
        assert http.resolve_url("https://example.com/a") == "https://example.com/a"


def test_request_routes_through_host_map(http_server):
    http_server.route("GET", "/thing", text="ok")

    with HttpClient(host_map={"example.com": f"127.0.0.1:{http_server.port}"}) as http:
        response = http.request("GET", "http://example.com/thing")

    assert response.text == "ok"
    assert http_server.requests[-1]["path"] == "/thing"


# --- URL rewriting: [http] config -------------------------------------------


def test_http_config_loads_prefix_and_hosts(tmp_path, monkeypatch):
    write_config(
        tmp_path,
        '[http]\nproxy_prefix = "https://p.example/"\n\n[http.hosts]\n'
        '"example.com" = "mirror.example"\n',
        monkeypatch,
    )

    with HttpClient() as http:
        assert http.proxy_prefix == "https://p.example/"
        assert http.host_map == {"example.com": "mirror.example"}
        assert http.resolve_url("https://example.com/a") == "https://mirror.example/a"
        assert http.resolve_url("https://other.com/a") == ("https://p.example/https://other.com/a")


def test_http_config_env_overrides_toml(tmp_path, monkeypatch):
    write_config(tmp_path, '[http]\nproxy_prefix = "https://p.example/"\n', monkeypatch)
    monkeypatch.setenv("PKIT_HTTP_PROXY_PREFIX", "https://env.example/")

    with HttpClient() as http:
        assert http.proxy_prefix == "https://env.example/"


def test_explicit_host_map_merges_over_config(tmp_path, monkeypatch):
    write_config(
        tmp_path,
        '[http.hosts]\n"example.com" = "mirror.example"\n"other.com" = "other-mirror"\n',
        monkeypatch,
    )

    with HttpClient(host_map={"example.com": "caller.example"}) as http:
        assert http.host_map == {
            "example.com": "caller.example",
            "other.com": "other-mirror",
        }


def test_explicit_empty_host_map_disables_config_hosts(tmp_path, monkeypatch):
    write_config(tmp_path, '[http.hosts]\n"example.com" = "mirror.example"\n', monkeypatch)

    with HttpClient(host_map={}) as http:
        assert http.host_map == {}
        assert http.resolve_url("https://example.com/a") == "https://example.com/a"


def test_explicit_empty_prefix_disables_config_prefix(tmp_path, monkeypatch):
    write_config(tmp_path, '[http]\nproxy_prefix = "https://p.example/"\n', monkeypatch)

    with HttpClient(proxy_prefix="") as http:
        assert http.proxy_prefix == ""
        assert http.resolve_url("https://example.com/a") == "https://example.com/a"


# --- Manual redirect following (only when rewriting is configured) ----------


def test_request_follows_relative_redirect_through_proxy(http_server):
    http_server.route("GET", "/https://example.com/red", status=302, headers={"Location": "/thing"})
    http_server.route("GET", "/thing", json={"ok": True})

    with HttpClient(proxy_prefix=f"{http_server.url}/") as http:
        response = http.request("GET", "https://example.com/red")

    assert response.json() == {"ok": True}
    assert http_server.requests[-1]["path"] == "/thing"


def test_request_follows_absolute_redirect_through_proxy(http_server):
    http_server.route(
        "GET", "/https://a.com/red", status=302, headers={"Location": "https://b.com/thing"}
    )
    http_server.route("GET", "/https://b.com/thing", text="ok")

    with HttpClient(proxy_prefix=http_server.url) as http:
        response = http.request("GET", "https://a.com/red")

    assert response.text == "ok"
    assert http_server.requests[-1]["path"] == "/https://b.com/thing"


def test_request_redirect_303_switches_post_to_get(http_server):
    http_server.route(
        "POST", "/https://example.com/red", status=303, headers={"Location": "/thing"}
    )
    http_server.route("GET", "/thing", json={"ok": True})

    with HttpClient(proxy_prefix=http_server.url) as http:
        response = http.request("POST", "https://example.com/red", data={"a": 1})

    assert response.json() == {"ok": True}
    final = http_server.requests[-1]
    assert final["method"] == "GET"
    assert final["body"] == ""


def test_request_redirect_307_keeps_post_method_and_body(http_server):
    http_server.route(
        "POST", "/https://example.com/red", status=307, headers={"Location": "/thing"}
    )
    http_server.route("POST", "/thing", json={"ok": True})

    with HttpClient(proxy_prefix=http_server.url) as http:
        response = http.request("POST", "https://example.com/red", data={"a": "1"})

    assert response.json() == {"ok": True}
    final = http_server.requests[-1]
    assert final["method"] == "POST"
    assert parse_qs(final["body"]) == {"a": ["1"]}


def test_request_redirect_too_many_hops_raises(http_server):
    http_server.route("GET", "/https://example.com/loop", status=302, headers={"Location": "/loop"})
    http_server.route("GET", "/loop", status=302, headers={"Location": "/loop"})

    with HttpClient(proxy_prefix=http_server.url) as http, pytest.raises(HttpError) as exc:
        http.request("GET", "https://example.com/loop")

    assert exc.value.status_code is None
    assert "too many redirects" in str(exc.value)


def test_request_redirect_keeps_authorization_on_mapped_host(http_server):
    http_server.route("GET", "/thing", status=302, headers={"Location": "http://example.com/other"})
    http_server.route("GET", "/other", text="ok")

    with HttpClient(
        host_map={"example.com": f"127.0.0.1:{http_server.port}"},
        headers={"Authorization": "LOW key:secret"},
    ) as http:
        http.request("GET", "http://example.com/thing")

    # The canonical Location maps back to the mirror: same host, auth kept.
    assert http_server.requests[-1]["headers"]["Authorization"] == "LOW key:secret"


def test_request_redirect_keeps_authorization_across_configured_mirrors(http_server):
    """Two host_map keys sharing one server: the second mirror is trusted."""
    http_server.route("GET", "/thing", status=302, headers={"Location": "/other"})
    http_server.route("GET", "/other", text="ok")

    with HttpClient(
        host_map={
            "example.com": f"127.0.0.1:{http_server.port}",
            "mirror.example.com": f"localhost:{http_server.port}",
        },
        headers={"Authorization": "LOW key:secret"},
    ) as http:
        response = http.request("GET", "http://example.com/thing", allow_redirects=False)

    assert response.text == "ok"
    assert http_server.requests[-1]["headers"]["Authorization"] == "LOW key:secret"


def test_request_redirect_keeps_authorization_on_proxy_host(http_server):
    """A mirror hop that lands on the proxy stays on trusted ground.

    The first URL is host-mapped (never proxied); the redirect target is not
    mapped, so it is routed through the proxy — whose host is trusted.
    """
    proxied = f"/http://127.0.0.1:{http_server.port}/other"
    http_server.route("GET", "/thing", status=302, headers={"Location": "/other"})
    http_server.route("GET", proxied, text="ok")

    with HttpClient(
        host_map={"example.com": f"127.0.0.1:{http_server.port}"},
        proxy_prefix=f"http://localhost:{http_server.port}",
        headers={"Authorization": "LOW key:secret"},
    ) as http:
        response = http.request("GET", "http://example.com/thing")

    assert response.text == "ok"
    assert http_server.requests[-1]["headers"]["Authorization"] == "LOW key:secret"


def test_request_redirect_with_hostless_proxy_prefix_drops_cross_host_auth(http_server):
    """A proxy prefix without a hostname cannot vouch for cross-host hops.

    The first URL is host-mapped (never proxied); the redirect target is a
    second configured key, so it resolves onto a different mirror host. The
    hostless prefix contributes no trusted host, so Authorization is gone.
    """
    http_server.route(
        "GET", "/thing", status=302, headers={"Location": "http://mirror.example.com/other"}
    )
    http_server.route("GET", "/other", text="ok")

    with HttpClient(
        host_map={
            "example.com": f"127.0.0.1:{http_server.port}",
            "mirror.example.com": f"localhost:{http_server.port}",
        },
        proxy_prefix="opaque-prefix",
        headers={"Authorization": "LOW key:secret"},
    ) as http:
        response = http.request("GET", "http://example.com/thing")

    assert response.text == "ok"
    assert "Authorization" not in http_server.requests[-1]["headers"]


def test_request_redirect_drops_authorization_cross_host(http_server, monkeypatch):
    http_server.route("GET", "/thing", status=302, headers={"Location": "https://evil.com/x"})

    with HttpClient(
        host_map={"example.com": f"127.0.0.1:{http_server.port}"},
        headers={"Authorization": "LOW key:secret"},
    ) as http:
        real_request = http._session.request
        calls: list[dict[str, object]] = []

        def spy(method, url, **kwargs):
            calls.append({"url": url, "headers": kwargs.get("headers")})
            if len(calls) == 1:
                return real_request(method, url, **kwargs)
            response = requests.Response()
            response.status_code = 200
            response._content = b"ok"
            response.headers["Content-Type"] = "text/plain"
            return response

        monkeypatch.setattr(http._session, "request", spy)
        response = http.request("GET", "http://example.com/thing")

    assert response.text == "ok"
    # The second hop leaves the trusted hosts: Authorization must be None,
    # which requests removes from the merged headers.
    assert calls[1]["headers"] == {"Authorization": None}


def test_request_without_rewriting_uses_session_redirects(http_server, monkeypatch):
    http_server.route("GET", "/thing", text="ok")
    sent: dict[str, object] = {}

    with HttpClient() as http:
        real_request = http._session.request

        def spy(method, url, **kwargs):
            sent.update(kwargs)
            return real_request(method, url, **kwargs)

        monkeypatch.setattr(http._session, "request", spy)
        http.request("GET", f"{http_server.url}/thing")

    assert "allow_redirects" not in sent


def test_request_maps_transport_error_while_rewriting():
    # Port 1 on localhost is never listening: connection refused, no response.
    with HttpClient(proxy_prefix="http://127.0.0.1:1") as http, pytest.raises(HttpError) as exc:
        http.request("GET", "https://example.com/thing")

    assert exc.value.status_code is None


def test_request_maps_http_error_while_rewriting(http_server):
    http_server.route("GET", "/https://example.com/thing", status=500, text="exploded")

    with HttpClient(proxy_prefix=http_server.url) as http, pytest.raises(HttpError) as exc:
        http.request("GET", "https://example.com/thing")

    assert exc.value.status_code == 500
    assert exc.value.body == "exploded"


def test_request_redirect_without_location_returns_response(http_server):
    http_server.route("GET", "/https://example.com/thing", status=302, text="stuck")

    with HttpClient(proxy_prefix=http_server.url) as http:
        response = http.request("GET", "https://example.com/thing")

    assert response.status_code == 302
    assert response.text == "stuck"


def test_download_cleans_up_when_the_stream_dies(http_server, tmp_path):
    """A body shorter than its Content-Length aborts and leaves no file."""
    http_server.route(
        "GET",
        "/truncated",
        text="only a bit",
        headers={"Content-Length": "4096", "Connection": "close"},
    )

    with HttpClient(timeout=5) as http, pytest.raises(HttpError):
        http.download(f"{http_server.url}/truncated", tmp_path / "partial.bin")

    assert not (tmp_path / "partial.bin").exists()


def test_download_cleans_up_on_unexpected_errors(http_server, monkeypatch, tmp_path):
    """Failures beyond HTTP (KeyboardInterrupt, bugs) still leave no partial file."""

    def broken(self, chunk_size):
        yield b"bytes"
        raise RuntimeError("boom")

    monkeypatch.setattr(requests.Response, "iter_content", broken)
    http_server.route("GET", "/thing", text="bytes")

    with HttpClient() as http, pytest.raises(RuntimeError, match="boom"):
        http.download(f"{http_server.url}/thing", tmp_path / "partial.bin")

    assert not (tmp_path / "partial.bin").exists()
