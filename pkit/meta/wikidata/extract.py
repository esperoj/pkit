"""Language handling, entity parsing, and extractor combinators.

Everything here turns one fetched Wikidata entity into plain field values
(strings, string lists, extra dicts) — no model or plan knowledge.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PRIMARY_LANGS",
    "EntityView",
    "claim",
    "claims",
    "description",
    "ext_ids",
    "has_claim",
    "has_class",
    "i18n_extra",
    "item_ids",
    "kind_of",
    "person_name",
    "work_title",
]

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
    lang: str,
    fallback: bool = True,
    orig_lang: str = "",
) -> str:
    if not fallback:
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

    def label(self, qid: str, *, lang: str | None = None) -> str:
        """One referenced item's label in lang (default: the view language)."""
        if qid in self.labels:
            return _pick_from_layers((self.labels[qid],), lang=lang or self.lang)
        if self.missing is not None:
            self.missing.add(qid)
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


def description() -> Callable[[EntityView], str]:
    """Extract an entity's description in the view language."""
    return lambda view: _pick_from_layers(
        (_text_map(view.raw.get("descriptions")),),
        lang=view.lang,
        orig_lang=view.orig_lang,
    )


def work_title() -> Callable[[EntityView], str]:
    """Extract a work's title from P1476 and labels, respecting script priority."""

    def _extract(view: EntityView) -> str:
        return _pick_from_layers(
            (_monolingual_map(view.raw, "P1476"), _text_map(view.raw.get("labels"))),
            lang=view.lang,
            orig_lang=view.orig_lang,
        )

    return _extract


def person_name() -> Callable[[EntityView], str]:
    """Extract a person's name from labels and P1559 (name in native language)."""

    def _extract(view: EntityView) -> str:
        return _pick_from_layers(
            (_text_map(view.raw.get("labels")), _monolingual_map(view.raw, "P1559")),
            lang=view.lang,
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
