from __future__ import annotations

from dataclasses import asdict, fields
from json import dumps, loads
from typing import Any

from pkit.meta.models import Book, Entity, Person

MODEL_REGISTRY: dict[str, type[Entity]] = {
    "book": Book,
    "person": Person,
}


def to_dict(model: Entity) -> dict[str, Any]:
    """
    Convert an Entity model into a plain JSON-compatible dict.
    """
    return asdict(model)


def to_json(model: Entity) -> str:
    """
    Convert an Entity model into a JSON payload string.
    """
    return dumps(to_dict(model), indent=2, ensure_ascii=False, sort_keys=True)


def from_dict(data: dict[str, Any]) -> Entity:
    """
    Convert a JSON-compatible dict back into the correct Entity model.
    """
    data = dict(data)

    entity_type = data.get("type")
    if not isinstance(entity_type, str) or not entity_type:
        raise ValueError("entity data must contain a string 'type'")

    cls = MODEL_REGISTRY.get(entity_type)
    if cls is None:
        raise ValueError(f"unknown entity type: {entity_type}")

    # Ignore unknown keys so old code can tolerate future payload additions.
    allowed = {f.name for f in fields(cls)}
    kwargs = {key: value for key, value in data.items() if key in allowed}

    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ValueError(f"invalid data for entity type '{entity_type}'") from exc


def from_json(payload: str | bytes) -> Entity:
    """
    Convert a JSON payload string/bytes back into the correct Entity model.
    """
    return from_dict(loads(payload))
