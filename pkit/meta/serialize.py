from dataclasses import asdict, fields
from json import dumps, loads
from typing import Any

from pkit.meta.models import RECORD_TYPES, Record

__all__ = ["from_dict", "from_json", "to_dict", "to_json"]


def to_dict(model: Record) -> dict[str, Any]:
    return asdict(model)


def to_json(model: Record) -> str:
    return dumps(to_dict(model), indent=2, ensure_ascii=False, sort_keys=True)


def from_dict(data: dict[str, Any]) -> Record:
    record_type = data.get("type")
    if not isinstance(record_type, str) or not record_type:
        raise ValueError("record data must contain a string 'type'")
    cls = RECORD_TYPES.get(record_type)
    if cls is None:
        raise ValueError(f"unknown record type: {record_type}")
    allowed = {f.name for f in fields(cls)}
    try:  # unknown keys ignored, so old code tolerates future payloads
        return cls(**{k: v for k, v in data.items() if k in allowed})
    except TypeError as exc:
        raise ValueError(f"invalid data for record type '{record_type}'") from exc


def from_json(payload: str | bytes) -> Record:
    return from_dict(loads(payload))
