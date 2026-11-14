"""Archive SDK exceptions.

TransientError is separate because verify must never treat a network hiccup
as proof that a remote copy is gone.
"""

__all__ = ["ArchiveError", "InputError", "TransientError"]


class ArchiveError(Exception):
    """Base archive error."""


class InputError(ArchiveError):
    """Invalid input or missing local prerequisite; the caller can fix it."""


class TransientError(ArchiveError):
    """Temporary failure; retrying later is safe."""
