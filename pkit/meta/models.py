from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, Literal, dataclass_transform
from uuid import uuid4

__all__ = [
    "RECORD_TYPES",
    "File",
    "Link",
    "Person",
    "Record",
    "Software",
    "Text",
    "new_id",
    "record_model",
    "stored_models",
    "type_name",
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


def type_name(cls: type[Record]) -> str:
    """The type name of a record class ("file"), or "" when unregistered.

    The name is the default of `type: Literal["..."] = "..."`. Reading the
    field default is required because slots=True turns `cls.type` into a slot
    descriptor, not the value.
    """
    default = cls.__dataclass_fields__["type"].default
    return default if isinstance(default, str) else ""


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
    The type name is resolved by type_name(); see its docstring for why the
    field default is read instead of cls.type.
    """
    cls = dataclass(cls, frozen=True, slots=True, kw_only=True)
    skip = {"id", "type", "slug"} | {link.key for link in cls.links}
    fs = [f for f in fields(cls) if f.name not in skip]
    cls.json_fields = tuple(f.name for f in fs if f.default_factory in (list, dict))
    cls.scalar_fields = tuple(f.name for f in fs if f.name not in cls.json_fields)
    name = type_name(cls)
    if name:
        RECORD_TYPES[name] = cls
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
class Text(Record):
    type: Literal["text"] = "text"
    table: ClassVar[str] = "texts"
    links: ClassVar[tuple[Link, ...]] = (
        Link("author_ids", "text_authors", "text_id", "person_id", "persons"),
    )

    title: str = ""
    kind: str = ""
    publication_date: str = ""
    lang: str = ""
    description: str = ""
    series: list[str] = field(default_factory=list)
    author_ids: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)


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
class Software(Record):
    type: Literal["software"] = "software"
    table: ClassVar[str] = "software"

    title: str = ""
    kind: str = ""
    publication_date: str = ""
    description: str = ""
    website: str = ""
    series: list[str] = field(default_factory=list)
    developers: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    licenses: list[str] = field(default_factory=list)


@record_model
class File(Record):
    """One content-addressed file.

    id is "sha256:<hex>" so re-adding the same bytes is the same row across
    machines. identifiers carry all content hashes ("sha256:...", "sha1:...",
    "md5:...") so every record type stores all lookup tokens uniformly.
    locations are remote copies only — local presence lives in the filesystem.
    """

    type: Literal["file"] = "file"
    table: ClassVar[str] = "files"

    size: int = 0
    locations: list[str] = field(default_factory=list)
