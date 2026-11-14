"""Declarative source contracts and combinators for importing records.

A Source turns a URL into fetched items and a sequence of Plans. Each Plan
maps one fetched item onto a Record subclass (scalar/JSON fields plus LinkRefs
to child items) without per-type import boilerplate.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, Self

from pkit.meta.models import Record

__all__ = ["LinkRef", "Plan", "Source", "const", "first"]


def first(*extractors: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Return the first truthy extracted value, or ''."""

    def _extract(item: Any) -> Any:
        for extractor in extractors:
            value = extractor(item)
            if value:
                return value
        return ""

    return _extract


def const(value: Any) -> Callable[[Any], Any]:
    """Return a constant value regardless of the input item."""
    return lambda _item: value


@dataclass(frozen=True)
class LinkRef:
    """Declarative reference from a link field to child remote item keys."""

    extract: Callable[[Any], Sequence[str]]
    model: type[Record] | None = None


@dataclass(frozen=True)
class Plan:
    """Declarative rule mapping one fetched item onto a Record model."""

    model: type[Record]
    when: Callable[[Any], bool]
    fields: Mapping[str, Callable[[Any], Any]]
    links: Mapping[str, LinkRef] = field(default_factory=dict)
    key: Callable[[Any], Any] | None = None


class Source(Protocol):
    """Remote metadata source consumed by pkit.meta.load."""

    name: str
    plans: Sequence[Plan]

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None: ...

    def match(self, url: str) -> str | None:
        """Return the source-native key for url, or None if not handled."""
        ...

    def fetch(self, key: str, *, expected: type[Record] | None = None) -> Any:
        """Fetch one item by key, or return None if missing."""
        ...
