"""Wayback backend: CDX listing, temporary staging, SPN2 capture.

Wayback stores snapshots, not paths. A location is a full capture URL with a
timestamp, so the same URL may hold several different contents over time.
Staging (rclone "pkit-archive:") is transport only and is never registered.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Self

from pkit.archive.cas import Entry, Hashes
from pkit.archive.errors import ArchiveError, InputError, TransientError
from pkit.archive.rclone import RcloneBackend
from pkit.archive.remote import RemoteFile, Status, split_wayback, wayback_location
from pkit.lib.http import HttpClient, HttpError
from pkit.wayback import client as wayback_client

__all__ = ["STAGE_REMOTE", "WaybackBackend"]

STAGE_REMOTE = "pkit-archive:"
SAVE_OPTIONS = wayback_client.SaveOptions(
    capture_all=False,
    capture_outlinks=False,
    email_result=False,
    force_get=True,
    skip_first_archive=False,
)


def _sha1(digest: str) -> str:
    """CDX digests are base32 SHA1; convert to the hex form we store."""
    try:
        return base64.b32decode(digest.upper()).hex()
    except (binascii.Error, ValueError):
        return ""


class WaybackBackend:
    """One staging URL prefix served by a public base URL."""

    def __init__(
        self,
        prefix: str,
        *,
        base_url: str = "",
        stage: str = STAGE_REMOTE,
        client: wayback_client.WaybackClient | None = None,
        http: HttpClient | None = None,
    ) -> None:
        self.prefix = prefix.strip("/")
        self.base_url = base_url.rstrip("/")
        target = f"{stage.rstrip('/')}/{self.prefix}" if self.prefix else stage
        self.stage = RcloneBackend(target)
        self._client = client if client is not None else wayback_client.WaybackClient()
        self._http = http if http is not None else HttpClient()
        self._owned = (client is None, http is None)

    def close(self) -> None:
        """Release only the clients this backend created."""
        if self._owned[0]:
            self._client.close()
        if self._owned[1]:
            self._http.close()
        self.stage.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def url_for(self, relpath: str) -> str:
        if not self.base_url:
            raise InputError("wayback_url is not configured ([archive] wayback_url)")
        parts = [self.base_url, self.prefix, relpath]
        return "/".join(part.strip("/") for part in parts if part)

    def _captures(self, url: str, match_type: str) -> list[wayback_client.CdxCapture]:
        try:
            return self._client.cdx(url, match_type=match_type)
        except (wayback_client.RateLimitError, wayback_client.JobTimeoutError) as exc:
            raise TransientError(str(exc)) from exc
        except wayback_client.WaybackError as exc:
            raise ArchiveError(str(exc)) from exc

    def list(self) -> list[RemoteFile]:
        """One CDX prefix query: every 200 capture under the staging URL."""
        url = self.url_for("") + "/"
        oldest: dict[tuple[str, str], tuple[str, str]] = {}
        for capture in self._captures(url, "prefix"):
            if capture.statuscode != "200" or not capture.digest:
                continue
            sha1 = _sha1(capture.digest)
            if not sha1:
                continue
            key = (sha1, capture.original)
            current = oldest.get(key)
            if current is None or capture.timestamp < current[0]:
                oldest[key] = (capture.timestamp, capture.original)
        out = []
        for (sha1, original), (timestamp, url) in oldest.items():
            location = wayback_location(f"{wayback_client.BASE_URL}/web/{timestamp}id_/{url}")
            out.append(RemoteFile(location=location, hashes=(f"sha1:{sha1}",)))
        return out

    def copy(self, root: Path, entries: Sequence[Entry]) -> Iterator[RemoteFile]:
        """Stage and capture each missing file, yielding as each one lands."""
        for entry in entries:
            self.stage.put(entry.path, entry.relpath)
            try:
                result = self._client.save_url(self.url_for(entry.relpath), opts=SAVE_OPTIONS)
            except (wayback_client.RateLimitError, wayback_client.JobTimeoutError) as exc:
                raise TransientError(str(exc)) from exc
            except wayback_client.WaybackError as exc:
                raise ArchiveError(str(exc)) from exc
            yield RemoteFile(
                location=wayback_location(result.archive_url),
                hashes=entry.hashes.tokens,
            )

    def verify(self, location: str, want: Hashes) -> Status:
        """Compare the CDX digest of one capture. Binary payloads are not rewritten."""
        timestamp, original = split_wayback(location)
        rows = [row for row in self._captures(original, "exact") if row.timestamp == timestamp]
        if not rows:
            return "unknown"
        for row in rows:
            if row.statuscode != "200":
                return "mismatch"
            sha1 = _sha1(row.digest)
            if sha1 and f"sha1:{sha1}" in want.tokens:
                return "ok"
        return "mismatch"

    def fetch(self, location: str, dest: Path) -> None:
        """Download the raw capture payload."""
        url = location.removeprefix("wayback:")
        try:
            self._http.download(url, dest)
        except HttpError as exc:
            if exc.status_code is None or exc.status_code >= 500:
                raise TransientError(str(exc)) from exc
            raise ArchiveError(str(exc)) from exc
