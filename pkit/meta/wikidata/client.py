"""WikidataSource: fetch entities via wbgetentities, batch-resolve labels."""

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse

from pkit.lib import Closeable
from pkit.lib.http import HttpClient
from pkit.meta.models import Person, Record
from pkit.meta.source import Plan, select_plan
from pkit.meta.wikidata.extract import (
    LANG_QIDS,
    PRIMARY_LANGS,
    EntityView,
    entity_lang_code,
    extract_item_ids,
    monolingual_map,
    normalize_lang,
    text_map,
)
from pkit.meta.wikidata.plans import PLANS
from pkit.version import get_version

__all__ = ["API_URL", "WikidataSource"]

API_URL = "https://www.wikidata.org/w/api.php"
_BATCH_SIZE = 50

_QID_RE = re.compile(r"^Q[1-9]\d*$")
_QID_PATH_RE = re.compile(r"(?:^|/)(Q[1-9]\d*)(?:\.json)?$", re.IGNORECASE)


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


class WikidataSource(Closeable):
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
        lbls = text_map(entity.get("labels"))
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
            self._labels[qid] = text_map(entity.get("labels"))

    def _resolve_lang_codes(self, qids: Sequence[str]) -> list[str]:
        unknown = [qid for qid in qids if qid not in LANG_QIDS and qid not in self._lang_codes]
        if unknown:
            entities = self._wbgetentities(unknown, props="labels|claims")
            for qid in unknown:
                entity = entities.get(qid)
                if isinstance(entity, dict) and "missing" not in entity:
                    lbls = text_map(entity.get("labels"))
                    if lbls:
                        self._labels[qid] = lbls
                    self._lang_codes[qid] = entity_lang_code(entity)
                else:
                    self._lang_codes[qid] = ""
        codes: list[str] = []
        for qid in qids:
            code = LANG_QIDS.get(qid) or self._lang_codes.get(qid, "")
            if code and code not in codes:
                codes.append(code)
        return codes

    def _detect_orig_lang(self, entity: dict[str, Any], *, is_person: bool) -> str:
        prop = "P103" if is_person else "P407"
        qids = extract_item_ids(entity, prop)
        if qids:
            codes = self._resolve_lang_codes(qids)
            return codes[0] if len(codes) == 1 else ""
        text_prop = "P1559" if is_person else "P1476"
        monolingual = monolingual_map(entity, text_prop)
        codes = list(
            dict.fromkeys(normalize_lang(tag) for tag in monolingual if normalize_lang(tag))
        )
        return codes[0] if len(codes) == 1 else ""

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
        selected = select_plan(self.plans, probe, expected)
        if selected is None:
            return probe

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
