"""One HTTP session, one timeout, one error type.

Callers map HttpError onto their own taxonomy; this module stays generic.
URL rewriting lives here — a proxy prefix or a host map — so every caller
(wayback, archive, meta imports) shares one implementation.

proxy_prefix and host_map default to the [http] config section:
proxy_prefix / PKIT_HTTP_PROXY_PREFIX and the [http.hosts] table. An
explicitly passed value overrides the config; an empty one disables it.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from pkit import config

__all__ = ["HttpClient", "HttpError"]

CHUNK_SIZE = 1024 * 1024
MAX_REDIRECTS = 10
_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))


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


class HttpClient:
    """Closeable requests wrapper with a default timeout and typed errors.

    resolve_url rewrites every outgoing URL: a host_map match lands directly
    on the configured mirror (the proxy prefix is never stacked on top of
    it); every other URL is prefixed with proxy_prefix. When either is
    active, redirects are followed manually so each hop is rewritten too.
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
        self._session = requests.Session()
        if headers:
            self._session.headers.update(headers)

    def close(self) -> None:
        """Close the underlying session."""
        self._session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

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

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Perform one request and raise HttpError on transport/HTTP failure.

        A `timeout` in kwargs overrides the session default for this call.
        The URL is rewritten through resolve_url; when rewriting is active,
        redirects are followed manually: 301/302/303 become GET without a
        body, 307/308 keep the method and body, and Authorization is
        dropped when a redirect leaves the trusted hosts.
        """
        kwargs.setdefault("timeout", self.timeout)
        if self.proxy_prefix or self.host_map:
            return self._request_rewriting(method, url, **kwargs)
        try:
            response = self._session.request(method, url, **kwargs)
            response.raise_for_status()
        except requests.HTTPError as exc:
            response = exc.response
            if response is None:
                raise HttpError(str(exc)) from exc
            raise HttpError(
                f"HTTP {response.status_code}: {url}",
                status_code=response.status_code,
                body=response.text,
            ) from exc
        except requests.RequestException as exc:
            raise HttpError(str(exc)) from exc
        return response

    def _request_rewriting(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """One request with URL rewriting and manual redirect following."""
        kwargs["allow_redirects"] = False
        hop_method = method
        current = self.resolve_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = self._session.request(hop_method, current, **kwargs)
            except requests.RequestException as exc:
                raise HttpError(str(exc)) from exc
            location = response.headers.get("Location")
            if response.status_code not in _REDIRECT_STATUSES or not location:
                self._raise_http_error(response, url)
                return response
            # Intermediate bodies are never read; only the final one is.
            response.close()
            target = self.resolve_url(urljoin(current, location))
            if response.status_code in (301, 302, 303):
                hop_method = "GET"
                kwargs.pop("data", None)
                kwargs.pop("json", None)
            self._drop_authorization(kwargs, current, target)
            current = target
        raise HttpError(f"too many redirects (max {MAX_REDIRECTS}) following {url}")

    def _raise_http_error(self, response: requests.Response, url: str) -> None:
        """Map a 4xx/5xx final response onto HttpError, like request() does."""
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise HttpError(
                f"HTTP {response.status_code}: {url}",
                status_code=response.status_code,
                body=response.text,
            ) from exc

    def _drop_authorization(self, kwargs: dict[str, Any], current: str, target: str) -> None:
        """Drop Authorization when a redirect leaves the trusted hosts.

        Trusted hosts are the host_map entries (source hosts and mirrors)
        and the proxy host itself: everything routed through the proxy
        stays on the proxy. requests removes headers set to None.
        """
        current_host = urlsplit(current).hostname
        target_host = urlsplit(target).hostname
        if not target_host or target_host == current_host:
            return
        trusted = set(self.host_map)
        trusted.update(urlsplit(value).hostname or "" for value in self.host_map.values())
        if self.proxy_prefix:
            proxy_host = urlsplit(self.proxy_prefix).hostname
            if proxy_host:
                trusted.add(proxy_host)
        if target_host in trusted:
            return
        headers = dict(kwargs.get("headers") or {})
        headers["Authorization"] = None
        kwargs["headers"] = headers

    def download(self, url: str, dest: str | Path) -> Path:
        """Stream a URL to a file. The caller owns the destination path."""
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        response = self.request("GET", url, stream=True)
        try:
            with path.open("wb") as handle:
                # Empty chunks are a no-op write; no guard needed.
                for chunk in response.iter_content(CHUNK_SIZE):
                    handle.write(chunk)
        except requests.RequestException as exc:
            path.unlink(missing_ok=True)
            raise HttpError(str(exc)) from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            response.close()
        return path
