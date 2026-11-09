from __future__ import annotations

from dataclasses import asdict, fields
from json import dumps, loads
from typing import Any

from pkit.meta.models import RECORD_TYPES, Record

__all__ = [
    "MODEL_REGISTRY",
    "from_dict",
    "from_json",
    "to_dict",
    "to_json",
]


# Compatibility alias.
MODEL_REGISTRY = RECORD_TYPES


def to_dict(model: Record) -> dict[str, Any]:
    """
    Convert a Record model into a plain JSON-compatible dict.
    """
    return asdict(model)


def to_json(model: Record) -> str:
    """
    Convert a Record model into a JSON payload string.
    """
    return dumps(
        to_dict(model),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )


def from_dict(data: dict[str, Any]) -> Record:
    """
    Convert a JSON-compatible dict back into the correct Record model.
    """
    data = dict(data)

    record_type = data.get("type")
    if not isinstance(record_type, str) or not record_type:
        raise ValueError("record data must contain a string 'type'")

    cls = RECORD_TYPES.get(record_type)
    if cls is None:
        raise ValueError(f"unknown record type: {record_type}")

    # Ignore unknown keys so old code can tolerate future payload additions.
    allowed = {f.name for f in fields(cls)}
    kwargs = {key: value for key, value in data.items() if key in allowed}

    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ValueError(f"invalid data for record type '{record_type}'") from exc


def from_json(payload: str | bytes) -> Record:
    """
    Convert a JSON payload string/bytes back into the correct Record model.
    """
    return from_dict(loads(payload))
