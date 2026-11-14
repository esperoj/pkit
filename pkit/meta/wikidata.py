"""Wikidata source and declarative Book/Person import plans."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import parse_qs, urlparse

from pkit.lib.http import HttpClient
from pkit.meta.models import Book, Person, Record
from pkit.meta.source import LinkRef, Plan, first
from pkit.version import get_version

__all__ = ["API_URL", "BOOK_CLASSES", "PLANS", "EntityView", "WikidataSource"]

API_URL = "https://www.wikidata.org/w/api.php"
_BATCH_SIZE = 50
_LANGS = ("en", "en-gb", "en-us")
_QID_RE = re.compile(r"^Q[1-9]\d*$")
_QID_PATH_RE = re.compile(r"(?:^|/)(Q[1-9]\d*)(?:\.json)?$", re.IGNORECASE)

BOOK_CLASSES = (
    "Q571",       # book
    "Q7725634",   # literary work
    "Q47461344",  # written work
    "Q3331189",   # version, edition or translation
    "Q8261",      # novel
    "Q49084",     # short story
    "Q25379",     # play
    "Q277759",    # book series
    "Q1667921",   # novel series
    "Q21198342",  # manga series
    "Q725377",    # light novel
    "Q53731850",  # light novel series
    "Q1760610",   # comic book
    "Q2831984",   # comic book album
    "Q1004",      # comics
)


@dataclass(frozen=True)
class EntityView:
    """Fetched Wikidata entity paired with a label cache.

    When missing is provided, label() records uncached QIDs so callers can
    batch-fetch them in one request before re-evaluating the plan.
    """

    raw: dict[str, Any]
    labels: dict[str, str]
    missing: set[str] | None = None

    def label(self, qid: str) -> str:
        if qid in self.labels:
            return self.labels[qid]
        if self.missing is not None:
            self.missing.add(qid)
        return ""


def _parse_qid(url: str) -> str | None:
    if url.startswith("wikidata:"):
        qid = url.removeprefix("wikidata:").strip().upper()
        return qid if _QID_RE.match(qid) else None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    if host != "wikidata.org" and not host.endswith(".wikidata.org"):
        return None
    ids = parse_qs(parsed.query).get("ids")
    if ids:
        qid = ids[0].strip().upper()
        return qid if _QID_RE.match(qid) else None
    match = _QID_PATH_RE.search(parsed.path.rstrip("/"))
    return match.group(1).upper() if match else None


def _pick_text(mapping: Any, *, lang: str | None = None) -> str:
    if not isinstance(mapping, dict) or not mapping:
        return ""
    if lang is not None:
        entry = mapping.get(lang)
        return str(entry.get("value") or "") if isinstance(entry, dict) else ""
    for code in _LANGS:
        entry = mapping.get(code)
        if isinstance(entry, dict) and entry.get("value"):
            return str(entry["value"])
    for entry in mapping.values():
        if isinstance(entry, dict) and entry.get("value"):
            return str(entry["value"])
    return ""


def _format_time(value: dict[str, Any]) -> str:
    raw = str(value.get("time") or "")
    raw_prec = value.get("precision")
    precision = 11 if raw_prec is None else int(raw_prec)
    date_part = raw.split("T", 1)[0]
    sign = "-" if date_part.startswith("-") else ""
    parts = date_part.lstrip("+-").split("-")
    if len(parts) != 3:
        return raw
    year, month, day = parts
    if precision <= 9 or month == "00":
        return f"{sign}{year}"
    if precision == 10 or day == "00":
        return f"{sign}{year}-{month}"
    return f"{sign}{year}-{month}-{day}"


def _statements(entity: dict[str, Any], prop: str) -> list[dict[str, Any]]:
    claims_map = entity.get("claims")
    if not isinstance(claims_map, dict):
        return []
    rows = claims_map.get(prop)
    if not isinstance(rows, list):
        return []
    active = [row for row in rows if isinstance(row, dict) and row.get("rank") != "deprecated"]
    preferred = [row for row in active if row.get("rank") == "preferred"]
    return preferred or active


def _datavalue(statement: dict[str, Any]) -> tuple[str, Any] | None:
    snak = statement.get("mainsnak")
    if not isinstance(snak, dict) or snak.get("snaktype") != "value":
        return None
    dv = snak.get("datavalue")
    if not isinstance(dv, dict):
        return None
    return str(dv.get("type") or ""), dv.get("value")


def _qid(statement: dict[str, Any]) -> str | None:
    parsed = _datavalue(statement)
    if parsed is None:
        return None
    dtype, value = parsed
    if dtype == "wikibase-entityid" and isinstance(value, dict):
        if value.get("entity-type", "item") == "item" and value.get("id"):
            return str(value["id"])
    return None


def _decode_value(view: EntityView, dtype: str, value: Any, *, lang: str | None = None) -> str:
    if dtype == "wikibase-entityid" and isinstance(value, dict):
        if value.get("entity-type", "item") == "item" and value.get("id"):
            return view.label(str(value["id"]))
        return ""
    if dtype == "monolingualtext" and isinstance(value, dict):
        if lang is not None and value.get("language") != lang:
            return ""
        return str(value.get("text") or "")
    if dtype == "time" and isinstance(value, dict):
        return _format_time(value)
    if dtype == "quantity" and isinstance(value, dict):
        return str(value.get("amount") or "").lstrip("+")
    if dtype == "string" and value is not None:
        return str(value)
    return ""


def label(lang: str | None = None) -> Callable[[EntityView], str]:
    """Extract an entity's label, defaulting to English with fallback."""
    return lambda view: _pick_text(view.raw.get("labels"), lang=lang)


def description(lang: str | None = None) -> Callable[[EntityView], str]:
    """Extract an entity's description, defaulting to English with fallback."""
    return lambda view: _pick_text(view.raw.get("descriptions"), lang=lang)


def claims(prop: str, *, lang: str | None = None) -> Callable[[EntityView], list[str]]:
    """Extract all decoded string values for a property (respecting ranks)."""

    def _extract(view: EntityView) -> list[str]:
        out: list[str] = []
        for stmt in _statements(view.raw, prop):
            parsed = _datavalue(stmt)
            if parsed is None:
                continue
            val = _decode_value(view, parsed[0], parsed[1], lang=lang)
            if val and val not in out:
                out.append(val)
        return out

    return _extract


def claim(prop: str, *, lang: str | None = None) -> Callable[[EntityView], str]:
    """Extract the first decoded string value for a property."""
    many = claims(prop, lang=lang)

    def _extract(view: EntityView) -> str:
        values = many(view)
        return values[0] if values else ""

    return _extract


def item_ids(prop: str) -> Callable[[EntityView], list[str]]:
    """Extract referenced Wikidata QIDs for a property (for links or P31 checks)."""

    def _extract(view: EntityView) -> list[str]:
        out: list[str] = []
        for stmt in _statements(view.raw, prop):
            qid = _qid(stmt)
            if qid and qid not in out:
                out.append(qid)
        return out

    return _extract


def ext_ids(**prop_prefixes: str) -> Callable[[EntityView], list[str]]:
    """Extract external identifiers formatted as '<prefix>:<value>'."""

    def _extract(view: EntityView) -> list[str]:
        out: list[str] = []
        for prop, prefix in prop_prefixes.items():
            for stmt in _statements(view.raw, prop):
                parsed = _datavalue(stmt)
                if parsed is None:
                    continue
                dtype, value = parsed
                if dtype == "string" and value:
                    token = f"{prefix}:{value}"
                    if token not in out:
                        out.append(token)
        return out

    return _extract


def has_class(*qids: str) -> Callable[[EntityView], bool]:
    """True when P31 (instance of) contains any of the given QIDs."""
    wanted = set(qids)
    return lambda view: bool(wanted & set(item_ids("P31")(view)))


def has_claim(*props: str) -> Callable[[EntityView], bool]:
    """True when any listed property has at least one concrete claim value."""

    def _match(view: EntityView) -> bool:
        return any(
            _datavalue(stmt) is not None
            for prop in props
            for stmt in _statements(view.raw, prop)
        )

    return _match


PLANS: tuple[Plan, ...] = (
    Plan(
        model=Person,
        when=has_class("Q5"),
        key=lambda view: str(view.raw.get("id") or ""),
        fields={
            "name": label(),
            "gender": claim("P21"),
            "birth_date": claim("P569"),
            "death_date": claim("P570"),
            "country": claim("P27"),
            "occupations": claims("P106"),
            "identifiers": ext_ids(
                P214="viaf",
                P213="isni",
                P227="gnd",
                P244="lccn",
                P496="orcid",
                P648="openlibrary",
            ),
        },
    ),
    Plan(
        model=Book,
        when=lambda view: has_class(*BOOK_CLASSES)(view) or has_claim("P212", "P957")(view),
        key=lambda view: str(view.raw.get("id") or ""),
        fields={
            "title": first(claim("P1476", lang="en"), label("en"), claim("P1476"), label()),
            "publication_date": first(claim("P577"), claim("P571")),
            "lang": claim("P407"),
            "description": description(),
            "publishers": claims("P123"),
            "identifiers": ext_ids(
                P212="isbn13",
                P957="isbn10",
                P214="viaf",
                P244="lccn",
                P648="openlibrary",
                P1085="librarything",
                P2969="goodreads",
            ),
        },
        links={
            "author_ids": LinkRef(item_ids("P50"), model=Person),
        },
    ),
)


class WikidataSource:
    """Fetch Wikidata items via wbgetentities and batch-resolve referenced labels."""

    name: str = "wikidata"
    plans: Sequence[Plan] = PLANS

    def __init__(self, *, http: HttpClient | None = None, timeout: float | None = 30.0) -> None:
        self._http = http
        self._owns_http = http is None
        self._timeout = timeout
        self._entities: dict[str, dict[str, Any]] = {}
        self._labels: dict[str, str] = {}

    def _client(self) -> HttpClient:
        if self._http is None:
            self._http = HttpClient(
                timeout=self._timeout,
                headers={
                    "Accept": "application/json",
                    "User-Agent": f"pkit/{get_version()} (https://github.com/esperoj/pkit)",
                },
            )
        return self._http

    def close(self) -> None:
        if self._owns_http and self._http is not None:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def match(self, url: str) -> str | None:
        return _parse_qid(url)

    def _wbgetentities(self, ids: Sequence[str], *, props: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        client = self._client()
        for start in range(0, len(ids), _BATCH_SIZE):
            batch = ids[start : start + _BATCH_SIZE]
            resp = client.request(
                "GET",
                API_URL,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": props,
                    "redirects": "yes",
                    "format": "json",
                },
            )
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("Wikidata returned invalid JSON")
            if "error" in data:
                info = data["error"].get("info") or data["error"].get("code") or "API error"
                raise ValueError(f"Wikidata API error: {info}")
            entities = data.get("entities") or {}
            if isinstance(entities, dict):
                result.update(entities)
        return result

    def _get_entity(self, qid: str) -> dict[str, Any] | None:
        if qid in self._entities:
            entity = self._entities[qid]
            return None if "missing" in entity else entity
        entities = self._wbgetentities([qid], props="labels|descriptions|claims")
        entity = entities.get(qid)
        if entity is None and len(entities) == 1:
            entity = next(iter(entities.values()))
        if not isinstance(entity, dict) or "missing" in entity:
            self._entities[qid] = {"id": qid, "missing": ""}
            return None
        canon_id = str(entity.get("id") or qid)
        self._entities[qid] = entity
        self._entities[canon_id] = entity
        lbl = _pick_text(entity.get("labels"))
        if lbl:
            self._labels.setdefault(canon_id, lbl)
        return entity

    def _warm_labels(self, qids: Sequence[str]) -> None:
        needed = [qid for qid in qids if qid not in self._labels]
        if not needed:
            return
        entities = self._wbgetentities(needed, props="labels")
        for qid in needed:
            entity = entities.get(qid) or {}
            self._labels[qid] = _pick_text(entity.get("labels"))

    def fetch(self, key: str, *, expected: type[Record] | None = None) -> EntityView | None:
        entity = self._get_entity(key)
        if entity is None:
            return None
        probe = EntityView(entity, self._labels)
        matching = [plan for plan in self.plans if plan.when(probe)]
        if not matching and expected is not None:
            matching = [plan for plan in self.plans if plan.model is expected]
        if matching:
            missing: set[str] = set()
            collector = EntityView(entity, self._labels, missing)
            for plan in matching:
                for extractor in plan.fields.values():
                    extractor(collector)
            self._warm_labels(sorted(missing))
        return EntityView(entity, self._labels)
