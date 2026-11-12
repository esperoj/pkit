from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, Literal, dataclass_transform
from uuid import uuid4

__all__ = ["RECORD_TYPES", "Book", "Link", "Person", "Record", "new_id", "record_model"]


def new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class Link:
    """A many-to-many link stored in its own SQL table."""

    key: str
    table: str
    parent_column: str
    child_column: str
    child_table: str


RECORD_TYPES: dict[str, type[Record]] = {}


@dataclass_transform(kw_only_default=True, frozen_default=True, field_specifiers=(field,))
def record_model(cls):
    """Freeze the class, derive its column layout, register its type.

    list/dict defaults -> JSON text columns, link keys -> own table, rest -> scalar.
    """
    cls = dataclass(cls, frozen=True, slots=True, kw_only=True)
    skip = {"id", "type", "slug"} | {link.key for link in cls.links}
    fs = [f for f in fields(cls) if f.name not in skip]
    cls.json_fields = tuple(f.name for f in fs if f.default_factory in (list, dict))
    cls.scalar_fields = tuple(f.name for f in fs if f.name not in cls.json_fields)
    if cls.type:
        RECORD_TYPES[cls.type] = cls
    return cls


@record_model
class Record:
    """One metadata item."""

    table: ClassVar[str] = ""
    links: ClassVar[tuple[Link, ...]] = ()
    scalar_fields: ClassVar[tuple[str, ...]] = ()
    json_fields: ClassVar[tuple[str, ...]] = ()

    id: str = field(default_factory=new_id)
    type: str = ""
    slug: str = ""
    identifiers: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@record_model
class Book(Record):
    type: Literal[str] = "book"
    table: ClassVar[str] = "books"
    links: ClassVar[tuple[Link, ...]] = (
        Link("author_ids", "book_authors", "book_id", "person_id", "persons"),
    )

    title: str = ""
    publication_date: str = ""
    lang: str = ""
    description: str = ""
    author_ids: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)


@record_model
class Person(Record):
    type: Literal[str] = "person"
    table: ClassVar[str] = "persons"

    name: str = ""
    gender: str = ""
    birth_date: str = ""
    death_date: str = ""
    country: str = ""
    occupations: list[str] = field(default_factory=list)
