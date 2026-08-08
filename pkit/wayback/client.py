"""Wayback Machine SDK client.

This module is library-only. It should not depend on Click or CLI concerns.
Success returns structured dataclasses; failure raises typed exceptions.
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any

import requests


BASE_URL = "https://web.archive.org"
DEFAULT_LOCK_FILE = "/tmp/spn2_submit.lock"
ENV_ACCESS_KEY = "INTERNET_ARCHIVE_ACCESS_KEY"
ENV_SECRET_KEY = "INTERNET_ARCHIVE_SECRET_KEY"

QUEUE_WAIT_TIMEOUT = 300.0
JOB_POLL_TIMEOUT = 180.0
QUEUE_MIN_AVAILABLE = 1
QUEUE_POLL_INTERVAL = 10.0
JOB_POLL_INTERVAL = 15.0
SUBMIT_SETTLE_TIME = 1.0


class WaybackError(Exception):
    """Base exception for Wayback Machine SDK errors."""


class InputError(WaybackError):
    """Raised when the caller provides invalid input."""


class AuthError(WaybackError):
    """Raised when authentication or authorization fails."""


class RateLimitError(WaybackError):
    """Raised when the remote service rate-limits the request."""


class JobTimeoutError(WaybackError):
    """Raised when job polling or queue waiting times out."""


class JobFailedError(WaybackError):
    """Raised when an SPN2 save job fails on the server."""


@dataclass(frozen=True)
class SaveOptions:
    """Configuration options for SPN2 save requests."""

    capture_all: bool = False
    capture_outlinks: bool = False
    email_result: bool = True
    force_get: bool = True
    skip_first_archive: bool = True

    def to_form_dict(self) -> dict[str, int]:
        """Convert boolean flags to SPN2's integer 0/1 form values."""
        return {key: int(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class SaveResult:
    """Successful result of a single URL save operation."""

    url: str
    archive_url: str
    job_id: str


class WaybackClient:
    """Client for Wayback Machine APIs.

    This client is intentionally synchronous and simple. It can later grow
    additional methods such as:

    - cdx_snapshots()
    - latest_snapshot()
    - digest()

    while reusing authentication, proxy configuration, timeout, and session.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        proxy_prefix: str | None = None,
        lock_file: str | None = DEFAULT_LOCK_FILE,
        timeout: float | None = 120.0,
    ) -> None:
        self.api_key = os.getenv(ENV_ACCESS_KEY) if api_key is None else api_key
        self.api_secret = os.getenv(ENV_SECRET_KEY) if api_secret is None else api_secret
        self.proxy_prefix = proxy_prefix or ""
        self.lock_file = DEFAULT_LOCK_FILE if lock_file is None else lock_file
        self.timeout = timeout

        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

        if self.api_key and self.api_secret:
            self._session.headers["Authorization"] = f"LOW {self.api_key}:{self.api_secret}"

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "WaybackClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _endpoint(self, path: str) -> str:
        """Build a full endpoint URL, applying the optional proxy prefix."""
        return f"{self.proxy_prefix}{BASE_URL}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Perform an HTTP request and parse JSON when possible."""
        url = self._endpoint(path)
        request_timeout = self.timeout if timeout is None else timeout

        try:
            response = self._session.request(
                method,
                url,
                data=data,
                timeout=request_timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            response = exc.response

            if response is None:
                raise WaybackError(str(exc)) from exc

            message = f"HTTP {response.status_code}: {response.text}"

            if response.status_code in {401, 403}:
                raise AuthError(message) from exc

            if response.status_code == 429:
                raise RateLimitError(message) from exc

            raise WaybackError(message) from exc

        except requests.RequestException as exc:
            raise WaybackError(str(exc)) from exc

        try:
            payload = response.json()
        except ValueError:
            return {"raw_response": response.text}

        return payload if isinstance(payload, dict) else {"data": payload}

    @contextmanager
    def _locked(self):
        """Acquire an exclusive process-level lock for submission."""
        if not self.lock_file:
            yield
            return

        with open(self.lock_file, "a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _wait_for_availability(self) -> None:
        """Wait until the SPN2 queue appears to have enough capacity."""
        deadline = time.monotonic() + QUEUE_WAIT_TIMEOUT

        while True:
            if time.monotonic() >= deadline:
                raise JobTimeoutError("Timeout waiting for SPN2 queue availability.")

            try:
                payload = self._request("GET", f"/save/status/user?_t={int(time.time())}")
                available = payload.get("available", 0)

                try:
                    available = int(available)
                except (TypeError, ValueError):
                    available = 0

                if available >= QUEUE_MIN_AVAILABLE:
                    return
            except WaybackError:
                # If the availability endpoint is unavailable or unauthorized,
                # proceed and let the actual save endpoint return the real error.
                return

            time.sleep(QUEUE_POLL_INTERVAL)

    def _submit_save_job(self, url: str, opts: SaveOptions) -> str:
        """Submit an SPN2 save job under the submission lock."""
        with self._locked():
            self._wait_for_availability()

            payload = {"url": url, **opts.to_form_dict()}
            response = self._request("POST", "/save", data=payload)

            job_id = response.get("job_id")
            if not job_id:
                raise WaybackError(f"Missing job_id in SPN2 response: {response}")

            # Small settle delay preserved from the original implementation.
            time.sleep(SUBMIT_SETTLE_TIME)

            return str(job_id)

    def _poll_save_job(self, url: str, job_id: str) -> str:
        """Poll an SPN2 save job until success, failure, or timeout."""
        deadline = time.monotonic() + JOB_POLL_TIMEOUT

        while True:
            if time.monotonic() >= deadline:
                raise JobTimeoutError(f"Timeout polling job {job_id}.")

            response = self._request("GET", f"/save/status/{job_id}")
            status = response.get("status")

            if status == "success":
                timestamp = response.get("timestamp", "")
                original_url = response.get("original_url", url)
                return f"{BASE_URL}/web/{timestamp}id_/{original_url}"

            if status == "pending":
                time.sleep(JOB_POLL_INTERVAL)
                continue

            detail = response.get("error") or response.get("message") or status or "unknown status"
            raise JobFailedError(f"SPN2 job {job_id} failed: {detail!r}")

    def save_url(
        self,
        url: str,
        *,
        opts: SaveOptions | None = None,
    ) -> SaveResult:
        """Save one URL using the Wayback Machine SPN2 API."""
        opts = opts or SaveOptions()

        if not url:
<<<<<<< HEAD
            result.error = "No target URL provided."
            return result
=======
            raise InputError("No target URL provided.")
>>>>>>> 4708c76 (add tests)

        job_id = self._submit_save_job(url, opts)
        archive_url = self._poll_save_job(url, job_id)

        return SaveResult(
            url=url,
            archive_url=archive_url,
            job_id=job_id,
        )


# Future methods can live here and reuse the same session/auth/proxy:
#
# def cdx_snapshots(self, url: str) -> list[CdxSnapshot]:
#     ...
#
# def latest_snapshot(self, url: str) -> CdxSnapshot | None:
#     ...
#
# def digest(self, url: str) -> DigestResult:
#     ...
