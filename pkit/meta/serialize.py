"""Record <-> JSON boundary: mappings in, typed records out."""

import json
from dataclasses import fields
from typing import Any

from pkit.meta.models import RECORD_TYPES, Record

__all__ = ["from_dict", "from_json"]


def from_dict(data: dict[str, Any]) -> Record:
    record_type = data.get("type")
    if not isinstance(record_type, str) or not record_type:
        raise ValueError("record data must contain a string 'type'")
    cls = RECORD_TYPES.get(record_type)
    if cls is None:
        raise ValueError(f"unknown record type: {record_type}")
    allowed = {f.name for f in fields(cls)}
    # unknown keys ignored, so old code tolerates future payloads
    return cls(**{k: v for k, v in data.items() if k in allowed})


def from_json(payload: str | bytes) -> Record:
    return from_dict(json.loads(payload))
