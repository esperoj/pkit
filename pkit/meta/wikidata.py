"""Wikidata source and declarative Person/Software/Text import plans."""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self
from urllib.parse import parse_qs, urlparse

from pkit.lib.http import HttpClient
from pkit.meta.models import Person, Record, Software, Text
from pkit.meta.source import LinkRef, Plan, first
from pkit.version import get_version

__all__ = [
    "API_URL",
    "GAME_CLASSES",
    "PERSON_PLAN",
    "PLANS",
    "PRIMARY_LANGS",
    "SOFTWARE_CLASSES",
    "SOFTWARE_PLAN",
    "TEXT_CLASSES",
    "TEXT_PLAN",
    "EntityView",
    "WikidataSource",
]

API_URL = "https://www.wikidata.org/w/api.php"
_BATCH_SIZE = 50
PRIMARY_LANGS = ("en", "vi", "zh")
_LANG_TAGS: dict[str, tuple[str, ...]] = {
    "zh": (
        "zh-hans",
        "zh-cn",
        "zh",
        "zh-sg",
        "zh-hant",
        "zh-tw",
        "zh-hk",
        "zh-mo",
        "zh-classical",
        "lzh",
        "yue",
        "zh-yue",
    ),
    "en": ("en", "en-gb", "en-us"),
    "vi": ("vi",),
}
_ZH_ALIASES = frozenset({*_LANG_TAGS["zh"], "cmn", "wuu"})
_LANG_QIDS: dict[str, str] = {
    "Q1860": "en",  # English
    "Q9199": "vi",  # Vietnamese
    "Q7850": "zh",  # Chinese
    "Q9192": "zh",  # Mandarin Chinese
    "Q727694": "zh",  # Standard Chinese
    "Q37041": "zh",  # Classical Chinese
    "Q13414913": "zh",  # Simplified Chinese
    "Q18130932": "zh",  # Traditional Chinese
    "Q9186": "zh",  # Cantonese
    "Q5287": "ja",  # Japanese
    "Q9176": "ko",  # Korean
    "Q150": "fr",  # French
    "Q188": "de",  # German
    "Q1321": "es",  # Spanish
    "Q652": "it",  # Italian
    "Q5146": "pt",  # Portuguese
    "Q7737": "ru",  # Russian
    "Q13955": "ar",  # Arabic
    "Q397": "la",  # Latin
    "Q7411": "nl",  # Dutch
    "Q809": "pl",  # Polish
    "Q9217": "th",  # Thai
    "Q9240": "id",  # Indonesian
    "Q9027": "sv",  # Swedish
    "Q9043": "no",  # Norwegian
    "Q1568": "hi",  # Hindi
    "Q11059": "sa",  # Sanskrit
}

_QID_RE = re.compile(r"^Q[1-9]\d*$")
_QID_PATH_RE = re.compile(r"(?:^|/)(Q[1-9]\d*)(?:\.json)?$", re.IGNORECASE)

GAME_CLASSES = (
    "Q7889",  # video game
    "Q7058673",  # video game series
    "Q689445",  # visual novel
    "Q209163",  # expansion add-on
    "Q865493",  # video game mod
)

SOFTWARE_CLASSES = (
    "Q7397",  # software
    "Q166142",  # application software
    "Q341",  # free software
    "Q506883",  # free and open-source software
    "Q1130645",  # open-source software
    "Q218616",  # proprietary software
    "Q188860",  # software library
    "Q9135",  # operating system
    "Q189210",  # web application
)

TEXT_CLASSES = (
    "Q571",  # book
    "Q7725634",  # literary work
    "Q47461344",  # written work
    "Q3331189",  # version, edition or translation
    "Q8261",  # novel
    "Q49084",  # short story
    "Q25379",  # play
    "Q277759",  # book series
    "Q1667921",  # novel series
    "Q8274",  # manga
    "Q21198342",  # manga series
    "Q747381",  # light novel
    "Q53731850",  # light novel series
    "Q104213567",  # light novel series
    "Q725377",  # graphic novel
    "Q1760610",  # comic book
    "Q2831984",  # comic book album
    "Q1004",  # comics
    "Q191067",  # article
    "Q13442814",  # scholarly article
    "Q5633421",  # scientific journal
    "Q737498",  # academic journal
    "Q1057179",  # user guide / manual
    "Q1413406",  # technical documentation
)

_GAME_ID_PROPS = (
    "P1733",  # Steam application ID
    "P5794",  # IGDB game ID
    "P2725",  # GOG application ID
    "P11688",  # MobyGames game ID
    "P3180",  # Visual Novel Database ID
    "P6337",  # PCGamingWiki ID
    "P4769",  # GameFAQs game ID
    "P5290",  # Redump ID
    "P7597",  # Lutris game ID
)

_SOFTWARE_PROPS = (
    "P306",  # operating system
    "P1324",  # source code repository URL
    "P4655",  # Flathub ID
    "P3454",  # Arch Linux package
    "P3442",  # Debian stable package
    "P3597",  # F-Droid package
    "P4763",  # crates.io ID
)

_TEXT_ID_PROPS = ("P212", "P957", "P236", "P356")  # ISBN-13, ISBN-10, ISSN, DOI

_SOFTWARE_KINDS = {
    "Q188860": "library",
    "Q9135": "os",
}

_TEXT_KINDS = {
    "Q8274": "manga",
    "Q21198342": "manga",
    "Q747381": "light novel",
    "Q53731850": "light novel",
    "Q104213567": "light novel",
    "Q1004": "comic",
    "Q1760610": "comic",
    "Q2831984": "comic",
    "Q725377": "comic",
    "Q25379": "play",
    "Q49084": "short story",
    "Q8261": "novel",
    "Q1667921": "novel",
    "Q13442814": "article",
    "Q191067": "article",
    "Q5633421": "journal",
    "Q737498": "journal",
    "Q1057179": "manual",
    "Q1413406": "manual",
    "Q571": "book",
    "Q277759": "book",
    "Q3331189": "book",
}


def _normalize_lang(code: str) -> str:
    tag = code.strip().lower().replace("_", "-")
    if not tag:
        return ""
    if tag in _ZH_ALIASES or tag.startswith("zh-"):
        return "zh"
    if tag == "en" or tag.startswith("en-"):
        return "en"
    if tag == "vi" or tag.startswith("vi-"):
        return "vi"
    return tag


def _tags_for(lang: str, available: Iterable[str]) -> list[str]:
    if lang in _LANG_TAGS:
        return list(_LANG_TAGS[lang])
    tags = [lang]
    base, sep, _ = lang.partition("-")
    if sep and base:
        tags.append(base)
    prefix = f"{base or lang}-"
    tags.extend(sorted(key for key in available if key.startswith(prefix) and key not in tags))
    return tags


def _lang_matches(raw_code: str, wanted: str) -> bool:
    code = raw_code.strip().lower().replace("_", "-")
    if not code:
        return False
    if wanted in _LANG_TAGS:
        return code in _LANG_TAGS[wanted] or code.startswith(f"{wanted}-")
    target = wanted.lower()
    return code == target or code.startswith(f"{target}-")


def _text_map(mapping: Any) -> dict[str, str]:
    if not isinstance(mapping, dict):
        return {}
    out: dict[str, str] = {}
    for code, entry in mapping.items():
        if isinstance(code, str) and isinstance(entry, dict) and entry.get("value"):
            out[code.lower()] = str(entry["value"])
    return out


def _exact_from_layers(layers: Sequence[Mapping[str, str]], lang: str) -> str:
    available = {key for layer in layers for key in layer}
    for tag in _tags_for(lang, available):
        for layer in layers:
            value = layer.get(tag)
            if value:
                return value
    return ""


def _pick_from_layers(
    layers: Sequence[Mapping[str, str]],
    *,
    lang: str | None = None,
    fallback: bool = True,
    orig_lang: str = "",
) -> str:
    if lang is not None and not fallback:
        return _exact_from_layers(layers, lang)
    order: list[str] = []
    for cand in (lang, *PRIMARY_LANGS, orig_lang):
        if cand and cand not in order:
            order.append(cand)
    for cand in order:
        hit = _exact_from_layers(layers, cand)
        if hit:
            return hit
    for layer in layers:
        for value in layer.values():
            if value:
                return value
    return ""


@dataclass(frozen=True)
class EntityView:
    """Fetched Wikidata entity paired with a label cache and target language.

    When missing is provided, label() records uncached QIDs so callers can
    batch-fetch them in one request before re-evaluating the plan.
    """

    raw: dict[str, Any]
    labels: Mapping[str, Mapping[str, str]]
    missing: set[str] | None = None
    lang: str = "en"
    orig_lang: str = ""

    def label(self, qid: str, *, lang: str | None = None, fallback: bool = True) -> str:
        if qid in self.labels:
            return _pick_from_layers(
                (self.labels[qid],),
                lang=lang or self.lang,
                fallback=fallback,
            )
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
    if (
        dtype == "wikibase-entityid"
        and isinstance(value, dict)
        and value.get("entity-type", "item") == "item"
        and value.get("id")
    ):
        return str(value["id"])
    return None


def _extract_item_ids(entity: dict[str, Any], prop: str) -> list[str]:
    out: list[str] = []
    for stmt in _statements(entity, prop):
        qid = _qid(stmt)
        if qid and qid not in out:
            out.append(qid)
    return out


def _monolingual_map(entity: dict[str, Any], prop: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for stmt in _statements(entity, prop):
        parsed = _datavalue(stmt)
        if parsed is None:
            continue
        dtype, value = parsed
        if dtype == "monolingualtext" and isinstance(value, dict):
            code = str(value.get("language") or "").strip().lower().replace("_", "-")
            text = str(value.get("text") or "")
            if code and text:
                out.setdefault(code, text)
    return out


def _entity_lang_code(entity: dict[str, Any]) -> str:
    for prop in ("P424", "P305", "P218"):
        for stmt in _statements(entity, prop):
            parsed = _datavalue(stmt)
            if parsed is None:
                continue
            dtype, value = parsed
            if dtype == "string" and value:
                normalized = _normalize_lang(str(value))
                if normalized:
                    return normalized
    return ""


def _decode_value(
    view: EntityView,
    dtype: str,
    value: Any,
    *,
    item_lang: str | None = None,
    mono_lang: str | None = None,
) -> str:
    if dtype == "wikibase-entityid" and isinstance(value, dict):
        if value.get("entity-type", "item") == "item" and value.get("id"):
            return view.label(str(value["id"]), lang=item_lang or view.lang)
        return ""
    if dtype == "monolingualtext" and isinstance(value, dict):
        if mono_lang is not None and not _lang_matches(str(value.get("language") or ""), mono_lang):
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
    """Extract an entity's label, defaulting to the view language."""
    return lambda view: _pick_from_layers(
        (_text_map(view.raw.get("labels")),),
        lang=lang or view.lang,
        orig_lang=view.orig_lang,
    )


def description(lang: str | None = None) -> Callable[[EntityView], str]:
    """Extract an entity's description, defaulting to the view language."""
    return lambda view: _pick_from_layers(
        (_text_map(view.raw.get("descriptions")),),
        lang=lang or view.lang,
        orig_lang=view.orig_lang,
    )


def work_title(lang: str | None = None) -> Callable[[EntityView], str]:
    """Extract a work's title from P1476 and labels, respecting script priority."""

    def _extract(view: EntityView) -> str:
        return _pick_from_layers(
            (_monolingual_map(view.raw, "P1476"), _text_map(view.raw.get("labels"))),
            lang=lang or view.lang,
            orig_lang=view.orig_lang,
        )

    return _extract


def person_name(lang: str | None = None) -> Callable[[EntityView], str]:
    """Extract a person's name from labels and P1559 (name in native language)."""

    def _extract(view: EntityView) -> str:
        return _pick_from_layers(
            (_text_map(view.raw.get("labels")), _monolingual_map(view.raw, "P1559")),
            lang=lang or view.lang,
            orig_lang=view.orig_lang,
        )

    return _extract


def _tracked_langs(orig_lang: str) -> tuple[str, ...]:
    langs = list(PRIMARY_LANGS)
    if orig_lang and orig_lang not in PRIMARY_LANGS:
        langs.append(orig_lang)
    return tuple(sorted(langs))


def i18n_extra(*, person: bool = False) -> Callable[[EntityView], dict[str, Any]]:
    """Build extra['i18n'] for en, vi, zh, plus orig_lang if outside those three."""
    text_key = "name" if person else "title"

    def _extract(view: EntityView) -> dict[str, Any]:
        labels = _text_map(view.raw.get("labels"))
        descriptions = _text_map(view.raw.get("descriptions"))
        if person:
            text_layers: tuple[Mapping[str, str], ...] = (
                labels,
                _monolingual_map(view.raw, "P1559"),
            )
        else:
            text_layers = (_monolingual_map(view.raw, "P1476"), labels)
        i18n: dict[str, dict[str, str]] = {}
        for code in _tracked_langs(view.orig_lang):
            entry: dict[str, str] = {}
            text = _pick_from_layers(text_layers, lang=code, fallback=False)
            if text:
                entry[text_key] = text
            desc = _pick_from_layers((descriptions,), lang=code, fallback=False)
            if desc:
                entry["description"] = desc
            if entry:
                i18n[code] = entry
        return {"i18n": i18n} if i18n else {}

    return _extract


def claims(*props: str, lang: str | None = None) -> Callable[[EntityView], list[str]]:
    """Extract all decoded string values across properties (respecting ranks)."""

    def _extract(view: EntityView) -> list[str]:
        target_lang = lang or view.lang
        out: list[str] = []
        for prop in props:
            for stmt in _statements(view.raw, prop):
                parsed = _datavalue(stmt)
                if parsed is None:
                    continue
                val = _decode_value(
                    view,
                    parsed[0],
                    parsed[1],
                    item_lang=target_lang,
                    mono_lang=lang,
                )
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
    return lambda view: _extract_item_ids(view.raw, prop)


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
            _datavalue(stmt) is not None for prop in props for stmt in _statements(view.raw, prop)
        )

    return _match


def kind_of(
    mapping: Mapping[str, str],
    *,
    props: Sequence[str] = ("P31",),
    default: str = "",
) -> Callable[[EntityView], str]:
    """Map the first matching QID across properties to a coarse kind label."""

    def _extract(view: EntityView) -> str:
        for prop in props:
            for qid in item_ids(prop)(view):
                if qid in mapping:
                    return mapping[qid]
        return default

    return _extract


def _software_kind(view: EntityView) -> str:
    if has_class(*GAME_CLASSES)(view) or has_claim(*_GAME_ID_PROPS)(view):
        return "game"
    return kind_of(_SOFTWARE_KINDS, default="software")(view)


PERSON_PLAN = Plan(
    model=Person,
    when=has_class("Q5"),
    key=lambda view: str(view.raw.get("id") or ""),
    fields={
        "name": person_name(),
        "gender": claim("P21", lang="en"),
        "birth_date": claim("P569"),
        "death_date": claim("P570"),
        "country": claim("P27", lang="en"),
        "occupations": claims("P106", lang="en"),
        "identifiers": ext_ids(
            P214="viaf",
            P213="isni",
            P227="gnd",
            P244="lccn",
            P496="orcid",
            P648="openlibrary",
        ),
        "extra": i18n_extra(person=True),
    },
)

SOFTWARE_PLAN = Plan(
    model=Software,
    when=lambda view: (
        has_class(*GAME_CLASSES, *SOFTWARE_CLASSES)(view)
        or has_claim(*_GAME_ID_PROPS, *_SOFTWARE_PROPS)(view)
    ),
    key=lambda view: str(view.raw.get("id") or ""),
    fields={
        "title": work_title(),
        "kind": _software_kind,
        "publication_date": first(claim("P577"), claim("P571")),
        "description": description(),
        "website": first(claim("P856"), claim("P1324")),
        "series": claims("P179"),
        "developers": claims("P178"),
        "publishers": claims("P123"),
        "platforms": claims("P400", "P306", lang="en"),
        "genres": claims("P136", lang="en"),
        "licenses": claims("P275", lang="en"),
        "identifiers": ext_ids(
            P1733="steam",
            P5794="igdb",
            P2725="gog",
            P11688="mobygames",
            P3180="vndb",
            P6337="pcgamingwiki",
            P2816="hltb",
            P7597="lutris",
            P4769="gamefaqs",
            P5290="redump",
            P6278="epic",
            P4655="flathub",
            P3454="arch",
            P3442="debian",
            P3597="fdroid",
            P3418="gplay",
            P4763="crates",
            P2209="sourceforge",
        ),
        "extra": i18n_extra(),
    },
)

TEXT_PLAN = Plan(
    model=Text,
    when=lambda view: has_class(*TEXT_CLASSES)(view) or has_claim(*_TEXT_ID_PROPS)(view),
    key=lambda view: str(view.raw.get("id") or ""),
    fields={
        "title": work_title(),
        "kind": kind_of(_TEXT_KINDS, props=("P7937", "P31"), default="text"),
        "publication_date": first(claim("P577"), claim("P571")),
        "lang": claim("P407", lang="en"),
        "description": description(),
        "series": claims("P179"),
        "publishers": claims("P123"),
        "genres": claims("P136", lang="en"),
        "identifiers": ext_ids(
            P212="isbn13",
            P957="isbn10",
            P236="issn",
            P356="doi",
            P214="viaf",
            P244="lccn",
            P648="openlibrary",
            P1085="librarything",
            P2969="goodreads",
        ),
        "extra": i18n_extra(),
    },
    links={
        "author_ids": LinkRef(item_ids("P50"), model=Person),
    },
)

PLANS: tuple[Plan, ...] = (
    PERSON_PLAN,
    SOFTWARE_PLAN,
    TEXT_PLAN,
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
        self._labels: dict[str, dict[str, str]] = {}
        self._lang_codes: dict[str, str] = {}

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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
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
                raise TypeError("Wikidata returned invalid JSON")
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
        lbls = _text_map(entity.get("labels"))
        if lbls:
            self._labels.setdefault(canon_id, lbls)
        return entity

    def _warm_labels(self, qids: Sequence[str]) -> None:
        needed = [qid for qid in qids if qid not in self._labels]
        if not needed:
            return
        entities = self._wbgetentities(needed, props="labels")
        for qid in needed:
            entity = entities.get(qid) or {}
            self._labels[qid] = _text_map(entity.get("labels"))

    def _resolve_lang_codes(self, qids: Sequence[str]) -> list[str]:
        unknown = [qid for qid in qids if qid not in _LANG_QIDS and qid not in self._lang_codes]
        if unknown:
            entities = self._wbgetentities(unknown, props="labels|claims")
            for qid in unknown:
                entity = entities.get(qid)
                if isinstance(entity, dict) and "missing" not in entity:
                    lbls = _text_map(entity.get("labels"))
                    if lbls:
                        self._labels[qid] = lbls
                    self._lang_codes[qid] = _entity_lang_code(entity)
                else:
                    self._lang_codes[qid] = ""
        codes: list[str] = []
        for qid in qids:
            code = _LANG_QIDS.get(qid) or self._lang_codes.get(qid, "")
            if code and code not in codes:
                codes.append(code)
        return codes

    def _detect_orig_lang(self, entity: dict[str, Any], *, is_person: bool) -> str:
        prop = "P103" if is_person else "P407"
        qids = _extract_item_ids(entity, prop)
        if qids:
            codes = self._resolve_lang_codes(qids)
            return codes[0] if len(codes) == 1 else ""
        text_prop = "P1559" if is_person else "P1476"
        monolingual = _monolingual_map(entity, text_prop)
        codes = list(
            dict.fromkeys(_normalize_lang(tag) for tag in monolingual if _normalize_lang(tag))
        )
        return codes[0] if len(codes) == 1 else ""

    def _select_plan(
        self,
        probe: EntityView,
        expected: type[Record] | None,
    ) -> Plan | None:
        matching = [plan for plan in self.plans if plan.when(probe)]
        if expected is not None:
            for plan in matching:
                if plan.model is expected:
                    return plan
            for plan in self.plans:
                if plan.model is expected:
                    return plan
        return matching[0] if matching else None

    def fetch(
        self,
        key: str,
        *,
        expected: type[Record] | None = None,
        lang: str = "auto",
    ) -> EntityView | None:
        entity = self._get_entity(key)
        if entity is None:
            return None
        probe = EntityView(entity, self._labels)
        selected = self._select_plan(probe, expected)
        if selected is None:
            return EntityView(entity, self._labels)

        orig_lang = self._detect_orig_lang(entity, is_person=selected.model is Person)
        pref = lang.strip().lower() or "auto"
        chosen_lang = (
            pref if pref != "auto" else (orig_lang if orig_lang in PRIMARY_LANGS else "en")
        )
        missing: set[str] = set()
        collector = EntityView(
            entity,
            self._labels,
            missing,
            lang=chosen_lang,
            orig_lang=orig_lang,
        )
        for extractor in selected.fields.values():
            extractor(collector)
        self._warm_labels(sorted(missing))
        return EntityView(entity, self._labels, lang=chosen_lang, orig_lang=orig_lang)
