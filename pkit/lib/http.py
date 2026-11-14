"""One HTTP session, one timeout, one error type.

Callers map HttpError onto their own taxonomy; this module stays generic.
"""

from pathlib import Path
from typing import Any, Self

import requests

__all__ = ["HttpClient", "HttpError"]

CHUNK_SIZE = 1024 * 1024


class HttpError(Exception):
    """HTTP failure. status_code is None when no response arrived.

    body carries the response text so callers can surface a useful
    diagnostic ("HTTP 401: Unauthorized") without re-fetching.
    """

    def __init__(self, message: str, *, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class HttpClient:
    """Closeable requests wrapper with a default timeout and typed errors."""

    def __init__(
        self,
        *,
        timeout: float | None = 120.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.timeout = timeout
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

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Perform one request and raise HttpError on transport/HTTP failure.

        A `timeout` in kwargs overrides the session default for this call.
        """
        kwargs.setdefault("timeout", self.timeout)
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

    def download(self, url: str, dest: str | Path) -> Path:
        """Stream a URL to a file. The caller owns the destination path."""
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        response = self.request("GET", url, stream=True)
        try:
            with path.open("wb") as handle:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if chunk:
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
