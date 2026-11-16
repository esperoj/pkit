"""One HTTP transport, one timeout, one error type.

Callers map HttpError onto their own taxonomy; this module stays generic.
URL rewriting lives here — a proxy prefix or a host map — so every caller
(wayback, archive, meta imports) shares one implementation.

The transport is the standard library (urllib.request): pkit has zero
runtime dependencies. Two simplifications follow from that choice: bodies
are identity-encoded only (no gzip — a few more bytes on the wire, no
decompression layer to own), and connections are not pooled (request
volume makes reuse irrelevant). Redirects are always followed by the loop
in request(), rewriting configured or not, so one code path replays
301/302/303 as GET, keeps method and body on 307/308, and applies one
Authorization trust policy on every hop.

proxy_prefix and host_map default to the [http] config section:
proxy_prefix / PKIT_HTTP_PROXY_PREFIX and the [http.hosts] table. An
explicitly passed value overrides the config; an empty one disables it.
"""

import email.message
import http.client
import json
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from pkit.lib import CHUNK_SIZE, Closeable, config
from pkit.version import get_version

__all__ = ["HttpClient", "HttpError", "Response"]

MAX_REDIRECTS = 10
_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
# Characters quote() may leave alone: the RFC 3986 reserved set plus "%"
# (so existing escapes are never doubled). Everything non-ASCII — and the
# few ASCII characters that are genuinely illegal in a URL — gets escaped.
_WIRE_SAFE = "!#$%&'()*+,/:;=?@[%]"


class HttpError(Exception):
    """HTTP failure. status_code is None when no response arrived.

    body carries the response text so callers can surface a useful
    diagnostic ("HTTP 401: Unauthorized") without re-fetching.
    """

    def __init__(self, message: str, *, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _http_config() -> tuple[str, dict[str, str]]:
    """Load the [http] section: proxy_prefix plus the [http.hosts] table."""
    data = config.load()
    section = data.get("http")
    section = section if isinstance(section, dict) else {}
    prefix = str(config.pick("PKIT_HTTP_PROXY_PREFIX", section.get("proxy_prefix"), ""))
    hosts = section.get("hosts")
    hosts = hosts if isinstance(hosts, dict) else {}
    return prefix, {str(key).lower(): str(value) for key, value in hosts.items()}


class _NoRedirect(HTTPRedirectHandler):
    """Keep 3xx out of urllib's own redirect machinery.

    Redirects are followed by HttpClient itself (rewriting each hop and
    applying its auth policy), so the handler declines them: the response
    then arrives as an HTTPError, which is response-like.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Response:
    """One HTTP response: status, headers, and a readable body.

    Wraps whatever urllib produced: an http.client.HTTPResponse for a 2xx,
    and a urllib.error.HTTPError for any other status (it is response-like:
    .code/.headers/.read()), so callers see one shape. headers.get is
    case-insensitive; json() raises ValueError on non-JSON and empty
    bodies; a body that ends before Content-Length raises IncompleteRead
    instead of passing as a silent short read.
    """

    def __init__(self, status_code: int, headers: email.message.Message, body: IO[bytes]) -> None:
        self.status_code = status_code
        self.headers = headers
        self._body = body
        length = headers.get("Content-Length") or ""
        self._length = int(length) if length.isdigit() else None
        self._received = 0
        self._content: bytes | None = None

    @property
    def content(self) -> bytes:
        """The full body, cached after the first read."""
        if self._content is None:
            self._content = self.read()
        return self._content

    @property
    def text(self) -> str:
        """The body decoded as the declared charset (utf-8 by default)."""
        charset = self.headers.get_content_charset() or "utf-8"
        return self.content.decode(charset, "replace")

    def json(self) -> Any:
        """Parse the body as JSON; ValueError when it is not JSON (or empty)."""
        return json.loads(self.text)

    def read(self, size: int = -1) -> bytes:
        """Read up to size bytes of the body (all of it when size is negative)."""
        data = self._body.read() if size < 0 else self._body.read(size)
        self._received += len(data)
        if not data and self._length is not None and self._received < self._length:
            # http.client returns a short read silently when the connection
            # dies mid-body; a download must not finish quietly truncated.
            raise http.client.IncompleteRead(data, self._length - self._received)
        return data

    def close(self) -> None:
        """Release the connection; the body is unusable afterwards."""
        self._body.close()


def _with_params(url: str, params: Mapping[str, Any] | None) -> str:
    """Append encoded params to a URL's query string (? or & as needed)."""
    if not params:
        return url
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


def _encode_url(url: str) -> str:
    """Percent-encode one URL for the wire.

    urllib sends the request line as ASCII: non-ASCII bytes in the path
    or query are UTF-8 percent-escaped (existing % escapes are kept), and
    a non-ASCII host is IDNA-encoded. ASCII URLs pass through unchanged.
    """
    parts = urlsplit(url)
    netloc = parts.netloc
    host = parts.hostname
    if host and not host.isascii():
        netloc = netloc.replace(host, host.encode("idna").decode("ascii"))
    path = quote(parts.path, safe=_WIRE_SAFE)
    query = quote(parts.query, safe=_WIRE_SAFE)
    return urlunsplit((parts.scheme, netloc, path, query, parts.fragment))


class HttpClient(Closeable):
    """Closeable stdlib transport with a default timeout and typed errors.

    resolve_url rewrites every outgoing URL: a host_map match lands directly
    on the configured mirror (the proxy prefix is never stacked on top of
    it); every other URL is prefixed with proxy_prefix. Redirects are
    always followed manually so each hop is rewritten and one auth policy
    applies everywhere.
    """

    def __init__(
        self,
        *,
        timeout: float | None = 120.0,
        headers: dict[str, str] | None = None,
        proxy_prefix: str | None = None,
        host_map: Mapping[str, str] | None = None,
    ) -> None:
        cfg_prefix, cfg_hosts = _http_config()
        if proxy_prefix is None:
            proxy_prefix = cfg_prefix
        if host_map is None:
            hosts: dict[str, str] = cfg_hosts
        elif host_map:
            hosts = {**cfg_hosts, **dict(host_map)}  # caller entries win on conflicts
        else:
            hosts = {}
        self.timeout = timeout
        self.proxy_prefix = proxy_prefix
        self.host_map = {str(key).lower(): str(value) for key, value in hosts.items()}
        self._headers: dict[str, str] = {"User-Agent": f"pkit/{get_version()}"}
        if headers:
            self._headers.update(headers)
        # Default handlers (env proxies, verified TLS) plus no-redirect.
        self._opener: OpenerDirector = build_opener(_NoRedirect())

    def close(self) -> None:
        """No-op: each request owns its connection, released with its body."""

    def resolve_url(self, url: str) -> str:
        """Rewrite one URL: a host_map match first, then the proxy prefix.

        A host_map match already lands on a configured mirror, so the proxy
        prefix is never applied on top of it. A URL that already carries the
        prefix (as after a relative redirect) passes through unchanged, so
        the rewrite stays idempotent.
        """
        if self.host_map:
            parts = urlsplit(url)
            host = parts.hostname
            if host and host in self.host_map:
                return urlunsplit(
                    (parts.scheme, self.host_map[host], parts.path, parts.query, parts.fragment)
                )
        if self.proxy_prefix:
            prefix = self.proxy_prefix
            if not prefix.endswith(("/", "?", "=")):
                prefix += "/"
            if not url.startswith(prefix):
                return prefix + url
        return url

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Response:
        """Perform one request and raise HttpError on transport/HTTP failure.

        A `timeout` overrides the client default for this call. The URL is
        rewritten through resolve_url and redirects are followed manually:
        301/302/303 become GET without a body, 307/308 keep the method and
        body, and Authorization is dropped when a redirect leaves the
        trusted hosts.
        """
        request_timeout = self.timeout if timeout is None else timeout
        current = self.resolve_url(_with_params(url, params))
        body = urlencode(data).encode() if data else None
        hop_headers = dict(self._headers)
        if headers:
            hop_headers.update(headers)
        hop_method, hop_body = method, body
        for _ in range(MAX_REDIRECTS + 1):
            response = self._send(
                hop_method, current, headers=hop_headers, body=hop_body, timeout=request_timeout
            )
            location = response.headers.get("Location")
            if response.status_code not in _REDIRECT_STATUSES or not location:
                if response.status_code >= 400:
                    raise HttpError(
                        f"HTTP {response.status_code}: {url}",
                        status_code=response.status_code,
                        body=response.text,
                    )
                return response
            # Intermediate bodies are never read; only the final one is.
            response.close()
            target = self.resolve_url(urljoin(current, location))
            if response.status_code in (301, 302, 303):
                hop_method, hop_body = "GET", None
            hop_headers = self._hop_headers(hop_headers, current, target)
            current = target
        raise HttpError(f"too many redirects (max {MAX_REDIRECTS}) following {url}")

    def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float | None,
    ) -> Response:
        """One request, no redirects: the single line all network traffic crosses."""
        request = Request(_encode_url(url), data=body, headers=headers, method=method)
        try:
            raw = self._opener.open(request, timeout=timeout)
        except HTTPError as exc:
            # HTTPError is response-like: .code/.headers/.read() carry the
            # 3xx/4xx/5xx response that did arrive.
            raw = exc
        except (URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise HttpError(str(exc)) from exc
        return Response(raw.code, raw.headers, raw)

    def _hop_headers(self, headers: dict[str, str], current: str, target: str) -> dict[str, str]:
        """Headers for the next hop: Authorization is dropped when a redirect
        leaves the trusted hosts.

        Trusted hosts are the host_map entries (source hosts and mirrors)
        and the proxy host itself: everything routed through the proxy
        stays on the proxy.
        """
        current_host = urlsplit(current).hostname
        target_host = urlsplit(target).hostname
        if not target_host or target_host == current_host:
            return headers
        trusted = set(self.host_map)
        trusted.update(urlsplit(value).hostname or "" for value in self.host_map.values())
        if self.proxy_prefix:
            proxy_host = urlsplit(self.proxy_prefix).hostname
            if proxy_host:
                trusted.add(proxy_host)
        if target_host in trusted:
            return headers
        hop = dict(headers)
        hop.pop("Authorization", None)
        return hop

    def download(self, url: str, dest: str | Path) -> Path:
        """Stream a URL to a file. The caller owns the destination path."""
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        response = self.request("GET", url)
        try:
            with path.open("wb") as handle:
                # Empty chunks are a no-op write; no guard needed.
                while chunk := response.read(CHUNK_SIZE):
                    handle.write(chunk)
        except (OSError, http.client.HTTPException) as exc:
            path.unlink(missing_ok=True)
            raise HttpError(str(exc)) from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            response.close()
        return path
