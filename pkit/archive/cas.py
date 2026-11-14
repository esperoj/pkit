"""Content-addressed store: hashing, object paths, symlinks, permissions.

The filesystem is the local truth. A tree entry is a relative symlink whose
target basename is the sha256 of its content, so names, directory levels and
even the CAS root can change without losing identity.
"""

import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pkit.archive.errors import InputError

__all__ = [
    "CAS_DIRNAME",
    "OBJECT_MODE",
    "Entry",
    "Hashes",
    "abs_path",
    "cas_path",
    "find_cas",
    "hash_file",
    "ingest",
    "is_hash",
    "link_hash",
    "make_link",
    "set_readonly",
    "walk_files",
]

CAS_DIRNAME = "pkit-archive"
OBJECT_MODE = 0o444
CHUNK_SIZE = 1024 * 1024
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Hashes:
    """One content identity. sha256 names the object; sha1/md5 match remotes."""

    sha256: str
    sha1: str = ""
    md5: str = ""

    @property
    def record_id(self) -> str:
        return f"sha256:{self.sha256}"

    @property
    def identifiers(self) -> tuple[str, ...]:
        pairs = (("sha1", self.sha1), ("md5", self.md5))
        return tuple(f"{algo}:{value}" for algo, value in pairs if value)

    @property
    def tokens(self) -> tuple[str, ...]:
        return (self.record_id, *self.identifiers)


@dataclass(frozen=True)
class Entry:
    """One local file offered to a backend."""

    path: Path
    relpath: str
    size: int
    hashes: Hashes


def is_hash(value: str) -> bool:
    """True when a string is a bare sha256 hex digest."""
    return bool(_HASH_RE.match(value))


def abs_path(value: str | Path) -> Path:
    """Absolute, normalized path that does not dereference symlinks."""
    return Path(os.path.abspath(Path(value).expanduser()))


def find_cas(path: Path, cas_dir: str | Path | None = None) -> Path:
    """Locate <repo>/.git/pkit-archive by walking up from path.

    Only a .git directory is supported; worktrees/submodules use a .git file
    and must pass cas_dir explicitly.
    """
    if cas_dir is not None:
        return abs_path(cas_dir)
    start = path if path.is_dir() and not path.is_symlink() else path.parent
    for parent in (start, *start.parents):
        git = parent / ".git"
        if git.is_dir():
            return git / CAS_DIRNAME
        if git.exists():
            raise InputError(f"{git} is not a directory; pass cas_dir explicitly")
    raise InputError(f"no .git directory above {path}; pass cas_dir explicitly")


def cas_path(cas_dir: Path, sha256: str) -> Path:
    """Object path with a two-character fanout (git/annex convention)."""
    return cas_dir / sha256[:2] / sha256


def hash_file(path: Path) -> Hashes:
    """Hash a file once, computing sha256, sha1 and md5 in a single pass."""
    digests = {name: hashlib.new(name) for name in ("sha256", "sha1", "md5")}
    with Path(path).open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            for digest in digests.values():
                digest.update(chunk)
    return Hashes(**{name: digest.hexdigest() for name, digest in digests.items()})


def link_hash(path: Path) -> str | None:
    """The sha256 named by a CAS symlink, or None for anything else."""
    if not path.is_symlink():
        return None
    name = os.path.basename(os.readlink(path))
    return name if is_hash(name) else None


def make_link(link: Path, obj: Path) -> bool:
    """Point link at obj using a relative target. True when it changed."""
    target = os.path.relpath(obj, link.parent)
    if link.is_symlink():
        if os.readlink(link) == target:
            return False
        link.unlink()
    elif link.exists():
        raise InputError(f"{link} exists and is not a symlink")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return True


def set_readonly(obj: Path) -> bool:
    """Re-assert the read-only object mode. True when it changed."""
    if obj.stat().st_mode & 0o777 == OBJECT_MODE:
        return False
    obj.chmod(OBJECT_MODE)
    return True


def ingest(source: Path, cas_dir: Path, hashes: Hashes) -> Path:
    """Move content into the CAS and lock it. Existing objects are deduped."""
    obj = cas_path(cas_dir, hashes.sha256)
    if source == obj:
        return obj
    if obj.exists():
        source.unlink()
        return obj
    obj.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, obj)
    except OSError:
        # Cross-device fallback: stage inside obj.parent so obj is replaced atomically.
        fd, tmp_name = tempfile.mkstemp(dir=obj.parent, prefix=f".{obj.name}.")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            shutil.copy2(source, tmp)
            tmp.chmod(OBJECT_MODE)
            os.replace(tmp, obj)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        source.unlink()
    obj.chmod(OBJECT_MODE)
    return obj


def walk_files(root: Path) -> Iterator[Path]:
    """Yield regular files and symlinks without following symlinks and without .git."""
    if root.is_symlink() or root.is_file():
        yield root
        return
    if not root.is_dir():
        raise InputError(f"not a file or directory: {root}")
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for name in sorted(filenames):
            item = Path(dirpath) / name
            if item.is_symlink() or item.is_file():
                yield item
