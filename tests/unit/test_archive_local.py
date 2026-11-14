import os

import pytest

from pkit import archive
from pkit.archive.cas import cas_path, hash_file
from pkit.archive.errors import ArchiveError, InputError
from pkit.meta.db import MetaDB
from pkit.meta.models import File

PAYLOAD = b"pandora"


def test_add_moves_content_and_links(repo, db_path):
    tree = repo / "manga"
    item = tree / "v1.cbz"
    hashes = hash_file(item)

    result = archive.add(tree, db_path=db_path)

    obj = cas_path(repo / ".git" / "pkit-archive", hashes.sha256)
    assert result.added == 1
    assert obj.read_bytes() == PAYLOAD
    assert obj.stat().st_mode & 0o777 == 0o444
    assert item.is_symlink()
    assert not os.path.isabs(os.readlink(item))
    with MetaDB(db_path) as meta:
        record = meta.get(hashes.record_id)
    assert isinstance(record, File)
    assert record.size == 7
    assert f"sha1:{hashes.sha1}" in record.identifiers
    assert f"md5:{hashes.md5}" in record.identifiers
    assert record.locations == []


def test_add_is_idempotent_and_dedupes(repo, db_path):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)
    (tree / "copy.cbz").write_bytes(PAYLOAD)

    result = archive.add(tree, db_path=db_path)

    assert result.added == 1
    assert archive.add(tree, db_path=db_path).added == 0
    cas = repo / ".git" / "pkit-archive"
    objects = [item for item in cas.rglob("*") if item.is_file()]
    assert objects == [cas_path(cas, hash_file(tree / "v1.cbz").sha256)]


def test_add_skips_foreign_symlinks(repo, db_path):
    tree = repo / "manga"
    outside = repo / "outside.bin"
    outside.write_bytes(b"elsewhere")
    (tree / "foreign").symlink_to(outside)

    result = archive.add(tree, db_path=db_path)

    assert result.skipped == 1
    assert outside.read_bytes() == b"elsewhere"


def test_add_relinks_stale_targets(repo, db_path):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)
    item = tree / "v1.cbz"
    named = os.path.basename(os.readlink(item))
    item.unlink()
    item.symlink_to(f"/old/machine/cas/{named[:2]}/{named}")

    result = archive.add(tree, db_path=db_path)

    assert result.relinked == 1
    assert not os.path.isabs(os.readlink(item))
    assert item.read_bytes() == PAYLOAD


def test_add_reregisters_missing_record(repo, db_path):
    tree = repo / "manga"
    hashes = hash_file(tree / "v1.cbz")
    archive.add(tree, db_path=db_path)
    with MetaDB(db_path) as meta:
        meta.delete(hashes.record_id)

    result = archive.add(tree, db_path=db_path)

    assert result == archive.AddResult(added=0, relinked=0, skipped=0)
    with MetaDB(db_path) as meta:
        assert meta.get(hashes.record_id) is not None


def test_add_refuses_cas_directory(repo, db_path):
    with pytest.raises(InputError, match="inside the CAS directory"):
        archive.add(repo / ".git" / "pkit-archive", db_path=db_path)


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
    assert item.read_bytes() == PAYLOAD


def test_fix_reports_missing_and_repairs_permissions(repo, db_path):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)
    item = tree / "v1.cbz"
    named = os.path.basename(os.readlink(item))
    obj = cas_path(repo / ".git" / "pkit-archive", named)
    obj.chmod(0o644)
    (tree / "gone.cbz").symlink_to(f"../.git/pkit-archive/{'0' * 64}")

    result = archive.fix(tree)

    assert result.relinked == 0
    assert result.permissions == 1
    assert obj.stat().st_mode & 0o777 == 0o444
    assert result.missing == (str(tree / "gone.cbz"),)


def test_drop_refuses_without_remote(repo, db_path):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)

    with pytest.raises(ArchiveError, match="refusing to drop"):
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
