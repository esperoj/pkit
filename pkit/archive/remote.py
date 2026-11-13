"""Remote location vocabulary: strings, hashes and merge rules. No I/O.

A location is an opaque, self-describing string:
  ia:item/vol1.cbz
  wayback:https://web.archive.org/web/<ts>id_/https://host/folder/vol1.cbz
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pkit.archive.errors import InputError

__all__ = [
    "WAYBACK",
    "RemoteFile",
    "Status",
    "archive_url",
    "is_wayback",
    "merge_locations",
    "selects",
    "split_wayback",
    "wayback_location",
]

WAYBACK = "wayback:"

Status = Literal["ok", "mismatch", "missing", "unknown"]


@dataclass(frozen=True)
class RemoteFile:
    """One remote copy: where it is and which hashes it reports."""

    location: str
    hashes: tuple[str, ...]


def is_wayback(location: str) -> bool:
    return location.startswith(WAYBACK)


def wayback_location(url: str) -> str:
    return f"{WAYBACK}{url}"


def archive_url(location: str) -> str:
    """The fetchable capture URL inside a stored wayback location."""
    return location[len(WAYBACK) :]


def split_wayback(location: str) -> tuple[str, str]:
    """Return (timestamp, original url) from a wayback capture location."""
    head, marker, original = archive_url(location).partition("id_/")
    if not marker or not original:
        raise InputError(f"not a wayback capture url: {location}")
    return head.rsplit("/", 1)[-1], original


def _key(location: str) -> tuple[str, str]:
    if is_wayback(location):
        try:
            return ("wayback", split_wayback(location)[1])
        except InputError:
            return ("raw", location)
    return ("raw", location)


def _rank(location: str) -> str:
    if is_wayback(location):
        try:
            return split_wayback(location)[0]
        except InputError:
            return ""
    return ""


def merge_locations(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    """Union locations, keeping the oldest capture per (content, URL).

    Wayback timestamps sort lexicographically, so the oldest capture wins and
    re-matching stays idempotent (byte-identical record dumps).
    """
    chosen: dict[tuple[str, str], str] = {}
    for location in [*existing, *incoming]:
        key = _key(location)
        current = chosen.get(key)
        if current is None or _rank(location) < _rank(current):
            chosen[key] = location
    return sorted(chosen.values())


def selects(location: str, wanted: str, wayback_url: str = "") -> bool:
    """True when a stored location falls under a user-supplied remote spec."""
    if location == wanted or location.startswith(wanted):
        return True
    if is_wayback(location) and wanted.startswith(WAYBACK) and wayback_url:
        prefix = wanted[len(WAYBACK) :].lstrip("/")
        base = wayback_url.rstrip("/")
        try:
            original = split_wayback(location)[1]
        except InputError:
            return False
        return original.startswith(f"{base}/{prefix}")
    return False
