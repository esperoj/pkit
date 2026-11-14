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


def wayback_location(url: str) -> str:
    return f"{WAYBACK}{url}"


def split_wayback(location: str) -> tuple[str, str]:
    """Return (timestamp, original url) from a wayback capture location."""
    head, marker, original = location.removeprefix(WAYBACK).partition("id_/")
    timestamp = head.rsplit("/", 1)[-1]
    if not marker or not original or not timestamp:
        raise InputError(f"not a wayback capture url: {location}")
    return timestamp, original


def _capture_key(location: str) -> tuple[tuple[str, str], str]:
    if location.startswith(WAYBACK):
        try:
            timestamp, original = split_wayback(location)
            return ("wayback", original), timestamp
        except InputError:
            pass
    return ("raw", location), ""


def merge_locations(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    """Union locations, keeping the oldest capture per (content, URL).

    Wayback timestamps sort lexicographically, so the oldest capture wins and
    re-matching stays idempotent (byte-identical record dumps).
    """
    chosen: dict[tuple[str, str], tuple[str, str]] = {}
    for location in (*existing, *incoming):
        key, rank = _capture_key(location)
        current = chosen.get(key)
        if current is None or rank < current[0]:
            chosen[key] = (rank, location)
    return sorted(location for _, location in chosen.values())


def _prefix_match(value: str, prefix: str) -> bool:
    if prefix.endswith((":", "/")):
        return value.startswith(prefix)
    return value == prefix or value.startswith(f"{prefix}/")


def selects(location: str, wanted: str, wayback_url: str = "") -> bool:
    """True when a stored location falls under a user-supplied remote spec."""
    if _prefix_match(location, wanted):
        return True
    if location.startswith(WAYBACK) and wanted.startswith(WAYBACK) and wayback_url:
        prefix = wanted.removeprefix(WAYBACK).lstrip("/")
        base = wayback_url.rstrip("/")
        target = f"{base}/{prefix}" if prefix else f"{base}/"
        try:
            _, original = split_wayback(location)
        except InputError:
            return False
        return _prefix_match(original, target)
    return False
