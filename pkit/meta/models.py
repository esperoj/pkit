from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, dataclass_transform
from uuid import uuid4

__all__ = [
    "RECORD_TYPES",
    "Book",
    "Link",
    "Person",
    "Record",
    "new_id",
    "record_model",
]


def new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class Link:
    """
    Describes a many-to-many link stored in a separate SQL table.
    """

    key: str
    table: str
    parent_column: str
    child_column: str
    child_table: str


# Populated by @record_model.
# Maps record type string -> Python class.
RECORD_TYPES: dict[str, type[Record]] = {}


@dataclass_transform(kw_only_default=True, frozen_default=True)
def record_model(cls):
    """
    Mark a class as a record model.

    Also computes persistence metadata:

        cls.scalar_fields
        cls.json_fields

    Scalar fields are normal table columns.
    JSON fields are stored as JSON text columns.
    Link fields are excluded from table columns and handled separately.
    """
    cls = dataclass(cls, frozen=True, slots=True, kw_only=True)

    link_keys = {link.key for link in getattr(cls, "links", ())}

    scalar_fields: list[str] = []
    json_fields: list[str] = []

    for f in fields(cls):
        if f.name in {"id", "type", "slug"}:
            continue

        if f.name in link_keys:
            continue

        # Fields whose default factory is list/dict are stored as JSON.
        # Other fields are stored as scalar SQL values.
        if f.default_factory in (list, dict):
            json_fields.append(f.name)
        else:
            scalar_fields.append(f.name)

    cls.scalar_fields = tuple(scalar_fields)
    cls.json_fields = tuple(json_fields)

    record_type = getattr(cls, "type", "")
    if isinstance(record_type, str) and record_type:
        RECORD_TYPES[record_type] = cls

    return cls


@record_model
class Record:
    """
    Base record.

    A record is one metadata item.
    """

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
    type: str = "book"
    table: ClassVar[str] = "books"
    links: ClassVar[tuple[Link, ...]] = (
        Link(
            key="author_ids",
            table="book_authors",
            parent_column="book_id",
            child_column="person_id",
            child_table="persons",
        ),
    )

    title: str = ""
    publication_date: str = ""
    lang: str = ""
    description: str = ""

    author_ids: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)


@record_model
class Person(Record):
    type: str = "person"
    table: ClassVar[str] = "persons"
    links: ClassVar[tuple[Link, ...]] = ()

    name: str = ""
    gender: str = ""
    birth_date: str = ""
    death_date: str = ""
    country: str = ""

    occupations: list[str] = field(default_factory=list)
