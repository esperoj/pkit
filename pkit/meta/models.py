from dataclasses import dataclass, field
from typing import Any, dataclass_transform


@dataclass_transform(kw_only_default=True, frozen_default=True)
def entity_model(cls):
    return dataclass(cls, frozen=True, slots=True, kw_only=True)

@entity_model
class Entity:
    id: str
    slug: str
    extra: dict[str, Any] = field(default_factory=dict)

# example: https://www.wikidata.org/wiki/Q501514
@entity_model
class Book(Entity):
    title: str
    publication_date: str | None = None
    author_ids: list[str] = field(default_factory=list)
    publisher_ids: list[str] = field(default_factory=list)

@entity_model
class Person(Entity):
    name: str
