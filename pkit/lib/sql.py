"""SQL generation: quoting, placeholders, conditions, sorts, upserts.

Pure string builders over identifiers and values — no driver, no connection —
so any script can assemble parameterized SQL for its own tables.
Identifiers are always quoted; values never enter the SQL text, they ride
behind ? placeholders.
"""

from collections.abc import Sequence
from typing import Any

__all__ = ["equals", "json_contains", "parse_sort", "placeholders", "quote", "upsert"]


def quote(name: str) -> str:
    """One quoted SQL identifier; embedded quotes are doubled."""
    return '"' + name.replace('"', '""') + '"'


def placeholders(values: Sequence[Any]) -> str:
    """One ? per value, comma-separated ("?" / "?, ?" / ...)."""
    return ", ".join("?" * len(values))


def equals(column: str, values: Sequence[Any]) -> str:
    """Equality against one value, IN against many."""
    if len(values) == 1:
        return f"{quote(column)} = ?"
    return f"{quote(column)} IN ({placeholders(values)})"


def json_contains(table: str, column: str, values: Sequence[Any]) -> str:
    """Membership in a JSON array column of one table."""
    return (
        f"EXISTS (SELECT 1 FROM json_each({quote(table)}.{quote(column)}) AS _e "
        f"WHERE _e.value IN ({placeholders(values)}))"
    )


def parse_sort(sort: str, columns: Sequence[str]) -> tuple[str, bool]:
    """Split a sort spec into (column, descending); unknown columns raise.

    Sort specs are plain column names with an optional "-" prefix for
    descending order ("title", "-title"). columns lists every accepted
    column name, in the order the error message should show them.
    """
    descending, name = sort.startswith("-"), sort.removeprefix("-")
    if name not in columns:
        known = ", ".join(columns) or "(none)"
        raise ValueError(f"unknown sort {sort!r}; known columns: {known}")
    return name, descending


def upsert(table: str, columns: Sequence[str], *, key: str = "id") -> str:
    """INSERT ... ON CONFLICT(key) DO UPDATE for the columns.

    The key column is inserted but never updated; every other column takes
    the excluded (incoming) row's value. A column list that is only the key
    upserts nothing, so the conflict is a no-op (DO NOTHING).
    """
    quoted = [quote(column) for column in columns]
    sets = ", ".join(f"{q} = excluded.{q}" for q, column in zip(quoted, columns) if column != key)
    conflict = f"UPDATE SET {sets}" if sets else "NOTHING"
    return (
        f"INSERT INTO {quote(table)} ({', '.join(quoted)}) "
        f"VALUES ({placeholders(columns)}) ON CONFLICT({quote(key)}) DO {conflict}"
    )
