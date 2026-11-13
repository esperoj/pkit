from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, Literal, dataclass_transform
from uuid import uuid4

__all__ = [
    "RECORD_TYPES",
    "Book",
    "File",
    "Link",
    "Person",
    "Record",
    "new_id",
    "record_model",
    "stored_models",
]


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


def stored_models() -> tuple[type[Record], ...]:
    """The one discovery point: registered classes that own a SQL table.

    Order = definition order. Link tables are not listed: they are derived
    data, rebuilt by db.put from the parent rows' link keys.
    """
    return tuple(cls for cls in RECORD_TYPES.values() if cls.table)


@dataclass_transform(kw_only_default=True, frozen_default=True, field_specifiers=(field,))
def record_model(cls):
    """Freeze the class, derive its column layout, register its type.

    list/dict defaults -> JSON text columns, link keys -> own table, rest -> scalar.
    The type name is the default of `type: Literal["..."] = "..."`; reading the
    field default (not cls.type) is required because slots=True turns class
    attribute access into a slot descriptor.
    """
    cls = dataclass(cls, frozen=True, slots=True, kw_only=True)
    skip = {"id", "type", "slug"} | {link.key for link in cls.links}
    fs = [f for f in fields(cls) if f.name not in skip]
    cls.json_fields = tuple(f.name for f in fs if f.default_factory in (list, dict))
    cls.scalar_fields = tuple(f.name for f in fs if f.name not in cls.json_fields)
    type_name = cls.__dataclass_fields__["type"].default
    if isinstance(type_name, str) and type_name:
        RECORD_TYPES[type_name] = cls
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
    type: Literal["book"] = "book"
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
    type: Literal["person"] = "person"
    table: ClassVar[str] = "persons"

    name: str = ""
    gender: str = ""
    birth_date: str = ""
    death_date: str = ""
    country: str = ""
    occupations: list[str] = field(default_factory=list)


@record_model
class File(Record):
    """One content-addressed file.

    id is "sha256:<hex>" so re-adding the same bytes is the same row.
    identifiers carry alternate hashes ("sha1:...", "md5:...") used to match
    remotes. locations are remote copies only — local presence lives in the
    filesystem, never here.
    """

    type: Literal["file"] = "file"
    table: ClassVar[str] = "files"

    size: int = 0
    locations: list[str] = field(default_factory=list)
