"""Declarative source contracts and combinators for importing records.

A Source turns a URL into fetched items and a sequence of Plans. Each Plan
maps one fetched item onto a Record subclass (scalar/JSON fields plus LinkRefs
to child items) without per-type import boilerplate.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pkit.meta.models import Record

__all__ = ["LinkRef", "Plan", "Source", "first", "select_plan"]


def first(*extractors: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Return the first truthy extracted value, or ''."""

    def _extract(item: Any) -> Any:
        for extractor in extractors:
            value = extractor(item)
            if value:
                return value
        return ""

    return _extract


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


def select_plan(
    plans: Sequence[Plan],
    item: Any,
    expected: type[Record] | None = None,
) -> Plan | None:
    """The plan for one item: first when() match, or the expected model's plan.

    With expected set, a matching plan of that model wins over other matches;
    when nothing matches at all the expected model's plan is still returned
    (the caller asked for that shape), else None.
    """
    matched = [plan for plan in plans if plan.when(item)]
    if expected is not None:
        for plan in (*matched, *plans):
            if plan.model is expected:
                return plan
    return matched[0] if matched else None


class Source(Protocol):
    """Remote metadata source consumed by pkit.meta.load.

    Construction must be side-effect free (match() runs on a bare
    instance); fetch() opens whatever resources it needs and close()
    releases them.
    """

    name: str
    plans: Sequence[Plan]

    def match(self, url: str) -> str | None:
        """Return the source-native key for url, or None if not handled."""
        ...

    def fetch(
        self,
        key: str,
        *,
        expected: type[Record] | None = None,
        lang: str = "auto",
    ) -> Any:
        """Fetch one item by key, or return None if missing."""
        ...

    def close(self) -> None: ...
