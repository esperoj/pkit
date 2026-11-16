"""Shared, tool-agnostic helpers: cli, config, http, shell, sql, sqlite.

The only shared home in pkit: nothing here knows a tool, and tools map
these helpers onto their own types. Scripts can build on it directly —

    from pkit.lib import sql, sqlite

    with sqlite.Database("x.db", migrations_dir="migrations") as db:
        db.conn.execute(sql.upsert("t", ["id", "name"]), (1, "Jun"))
"""

from typing import Self

__all__ = ["CHUNK_SIZE", "Closeable"]

CHUNK_SIZE = 1024 * 1024  # one streaming unit, shared by hashing and downloads


class Closeable:
    """close() plus the with-statement boilerplate; subclasses override close()."""

    def close(self) -> None:
        """Release resources; the default releases nothing."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
