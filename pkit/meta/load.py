"""Load records into the database from a local JSON repo or a source URL.

Local paths read *.json files into sqlite. Source URLs dispatch to a matching
Source and execute its declarative Plans, recursively importing LinkRefs with
cycle protection and identifier-based deduplication.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any

from pkit.config import as_path
from pkit.meta import config
from pkit.meta.db import MetaDB
from pkit.meta.models import Record, new_id, type_name
from pkit.meta.serialize import from_dict
from pkit.meta.source import Plan, Source
from pkit.meta.wikidata import WikidataSource

__all__ = ["SOURCES", "VALID_LANGS", "load"]

SOURCES: tuple[Callable[[], Source], ...] = (WikidataSource,)
VALID_LANGS = ("auto", "en", "vi", "zh")


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _resolve_lang(lang: str | None, default: str) -> str:
    value = (default if lang is None else lang).strip().lower()
    if value not in VALID_LANGS:
        choices = ", ".join(VALID_LANGS)
        raise ValueError(f"unsupported language {value!r}; choose from {choices}")
    return value


def _match_source(value: str) -> tuple[Callable[[], Source], str] | None:
    for factory in SOURCES:
        with factory() as src:
            key = src.match(value)
            if key is not None:
                return factory, key
    return None


def _load_repo(path: Path, *, db_path: str | Path | None) -> int:
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(path.rglob("*.json"))
    else:
        raise FileNotFoundError(f"no record repo at {path}")
    count = 0
    with MetaDB(db_path) as db:
        for file in files:
            rows = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows:
                db.put(row)
                count += 1
    return count


def _select_plan(
    plans: Sequence[Plan],
    item: Any,
    expected: type[Record] | None = None,
) -> Plan | None:
    matched = [plan for plan in plans if plan.when(item)]
    if expected is not None:
        for plan in matched:
            if plan.model is expected:
                return plan
        for plan in plans:
            if plan.model is expected:
                return plan
    return matched[0] if matched else None


def _dedupe(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _merge_extra(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged = {**existing, **incoming}
    prev_i18n = existing.get("i18n")
    curr_i18n = incoming.get("i18n")
    if isinstance(prev_i18n, Mapping) and isinstance(curr_i18n, Mapping):
        i18n: dict[str, Any] = {}
        for lang_code in sorted({*prev_i18n, *curr_i18n}):
            prev_entry = prev_i18n.get(lang_code)
            curr_entry = curr_i18n.get(lang_code)
            if isinstance(prev_entry, Mapping) and isinstance(curr_entry, Mapping):
                i18n[lang_code] = {**prev_entry, **curr_entry}
            elif curr_entry is not None:
                i18n[lang_code] = curr_entry
            elif prev_entry is not None:
                i18n[lang_code] = prev_entry
        merged["i18n"] = i18n
    return merged


def _merge_record(
    model: type[Record],
    record_id: str,
    canonical_id: str,
    existing: Record | None,
    values: dict[str, Any],
) -> Record:
    payload: dict[str, Any] = {
        "id": record_id,
        "type": type_name(model),
        "slug": existing.slug if existing is not None else record_id,
    }
    existing_ids = existing.identifiers if existing is not None else ()
    incoming_ids = values.get("identifiers") or ()
    payload["identifiers"] = sorted({*existing_ids, canonical_id, *incoming_ids} - {""})

    existing_extra = existing.extra if existing is not None else {}
    incoming_extra = values.get("extra") or {}
    payload["extra"] = _merge_extra(existing_extra, incoming_extra)

    link_keys = {link.key for link in model.links}
    list_fields = {f.name for f in fields(model) if f.default_factory is list} | link_keys
    dict_fields = {f.name for f in fields(model) if f.default_factory is dict}

    for name in (*model.json_fields, *link_keys):
        if name in {"identifiers", "extra"}:
            continue
        prev = getattr(existing, name) if existing is not None else None
        curr = values.get(name)
        if name in list_fields:
            payload[name] = _dedupe([*(prev or ()), *(curr or ())])
        elif name in dict_fields:
            payload[name] = {**(prev or {}), **(curr or {})}

    for name in model.scalar_fields:
        prev = getattr(existing, name) if existing is not None else None
        curr = values.get(name)
        if curr not in (None, ""):
            payload[name] = curr
        elif prev is not None:
            payload[name] = prev

    return from_dict(payload)


def _load_source(
    factory: Callable[[], Source],
    root_key: str,
    *,
    db_path: str | Path | None,
    refresh: bool = False,
    lang: str = "auto",
) -> int:
    resolved: dict[str, str] = {}
    provisional: dict[str, str] = {}
    visiting: set[str] = set()
    imported = 0

    with factory() as src, MetaDB(db_path) as db:

        def _import_key(key: str, *, expected: type[Record] | None = None) -> str | None:
            nonlocal imported
            if key in resolved:
                return resolved[key]
            if key in visiting:
                return provisional.get(key)
            if not refresh:
                existing_rows = db.find(expected, identifier=f"{src.name}:{key}")
                if existing_rows:
                    resolved[key] = existing_rows[0].id
                    return existing_rows[0].id
            visiting.add(key)

            item = src.fetch(key, expected=expected, lang=lang)
            if item is None:
                visiting.discard(key)
                if expected is None:
                    raise ValueError(f"{src.name} item {key!r} not found")
                return None

            plan = _select_plan(src.plans, item, expected)
            if plan is None:
                visiting.discard(key)
                if expected is None:
                    raise ValueError(f"unsupported {src.name} item {key!r}")
                return None

            canon_key = str(plan.key(item) if plan.key is not None else key) or key
            if canon_key in resolved:
                visiting.discard(key)
                resolved[key] = resolved[canon_key]
                return resolved[canon_key]
            if canon_key != key and canon_key in visiting:
                visiting.discard(key)
                return provisional.get(canon_key)
            if not refresh and canon_key != key:
                existing_rows = db.find(plan.model, identifier=f"{src.name}:{canon_key}")
                if existing_rows:
                    visiting.discard(key)
                    resolved[key] = existing_rows[0].id
                    resolved[canon_key] = existing_rows[0].id
                    return existing_rows[0].id
            visiting.add(canon_key)

            values = {name: extractor(item) for name, extractor in plan.fields.items()}
            canonical_id = f"{src.name}:{canon_key}"
            tokens = [canonical_id, *(values.get("identifiers") or ())]
            matches = db.find(plan.model, identifier=tokens)
            existing = matches[0] if matches else None
            record_id = existing.id if existing is not None else new_id()
            provisional[key] = record_id
            provisional[canon_key] = record_id

            for link_field, ref in plan.links.items():
                child_ids: list[str] = []
                for child_key in ref.extract(item) or ():
                    if not child_key:
                        continue
                    child_id = _import_key(str(child_key), expected=ref.model)
                    if child_id and child_id not in child_ids:
                        child_ids.append(child_id)
                values[link_field] = child_ids

            record = _merge_record(plan.model, record_id, canonical_id, existing, values)
            saved = db.put(record)

            visiting.discard(key)
            visiting.discard(canon_key)
            resolved[key] = saved.id
            resolved[canon_key] = saved.id
            imported += 1
            return saved.id

        _import_key(root_key)
    return imported


def load(
    source: str | Path | None = None,
    *,
    db_path: str | Path | None = None,
    refresh: bool = False,
    lang: str | None = None,
) -> int:
    """Load records from a local JSON repo or a supported source URL.

    When source is omitted, loads from [meta] repo_path. Existing records from
    source URLs are skipped unless refresh=True. lang selects the main display
    language (auto|en|vi|zh), defaulting to [meta] lang / $PKIT_META_LANG.
    Returns the number of records written to the database.
    """
    cfg = config.load()
    target_lang = _resolve_lang(lang, cfg.lang)
    target_db = db_path or cfg.db_path
    if source is None:
        return _load_repo(as_path(cfg.repo_path), db_path=target_db)
    if isinstance(source, str):
        matched = _match_source(source)
        if matched is not None:
            factory, key = matched
            return _load_source(factory, key, db_path=target_db, refresh=refresh, lang=target_lang)
        if _looks_like_url(source):
            raise ValueError(f"unsupported source URL: {source}")
    return _load_repo(as_path(source), db_path=target_db)
