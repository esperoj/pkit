from __future__ import annotations

import os

import pytest

from pkit import archive
from pkit.archive.cas import cas_path, hash_file
from pkit.archive.errors import ArchiveError, InputError
from pkit.meta import db
from pkit.meta.models import File


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".git").mkdir()
    tree = tmp_path / "manga"
    tree.mkdir()
    (tree / "v1.cbz").write_bytes(b"pandora")
    return tmp_path


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "records.db"


def test_add_moves_content_and_links(repo, db_path):
    tree = repo / "manga"
    item = tree / "v1.cbz"
    hashes = hash_file(item)

    result = archive.add(tree, db_path=db_path)

    obj = cas_path(repo / ".git" / "pkit-archive", hashes.sha256)
    assert result.added == 1
    assert obj.read_bytes() == b"pandora"
    assert obj.stat().st_mode & 0o777 == 0o444
    assert item.is_symlink()
    assert not os.path.isabs(os.readlink(item))
    with db.session(db_path) as conn:
        record = db.get(conn, hashes.record_id)
    assert isinstance(record, File)
    assert record.size == 7
    assert f"sha1:{hashes.sha1}" in record.identifiers
    assert record.locations == []


def test_add_is_idempotent_and_dedupes(repo, db_path):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)
    (tree / "copy.cbz").write_bytes(b"pandora")
    result = archive.add(tree, db_path=db_path)
    assert result.added == 1
    assert archive.add(tree, db_path=db_path).added == 0
    cas = repo / ".git" / "pkit-archive"
    assert len(list(cas.rglob("*" * 1))) == 2  # one fanout dir + one object


def test_add_skips_foreign_symlinks(repo, db_path):
    tree = repo / "manga"
    outside = repo / "outside.bin"
    outside.write_bytes(b"elsewhere")
    (tree / "foreign").symlink_to(outside)
    result = archive.add(tree, db_path=db_path)
    assert result.skipped == 1
    assert outside.read_bytes() == b"elsewhere"


def test_fix_repoints_links(repo, db_path):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)
    item = tree / "v1.cbz"
    named = os.path.basename(os.readlink(item))
    item.unlink()
    item.symlink_to(f"/old/machine/cas/{named[:2]}/{named}")

    result = archive.fix(tree)

    assert result.relinked == 1
    assert not os.path.isabs(os.readlink(item))
    assert item.read_bytes() == b"pandora"


def test_drop_refuses_without_remote(repo, db_path):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)
    with pytest.raises(ArchiveError):
        archive.drop(tree / "v1.cbz", db_path=db_path)


def test_forget_guards_present_objects(repo, db_path):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)
    item = tree / "v1.cbz"
    assert archive.forget(tree, db_path=db_path).kept == (str(item),)

    named = os.path.basename(os.readlink(item))
    cas_path(repo / ".git" / "pkit-archive", named).unlink()
    result = archive.forget(tree, db_path=db_path)
    assert result.forgotten == (f"sha256:{named}",)


def test_find_cas_requires_git_or_override(tmp_path):
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    (lonely / "f").write_bytes(b"x")
    with pytest.raises(InputError):
        archive.add(lonely, db_path=tmp_path / "r.db")
    result = archive.add(lonely, db_path=tmp_path / "r.db", cas_dir=tmp_path / "cas")
    assert result.added == 1
