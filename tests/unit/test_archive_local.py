import os

import pytest

from pkit import archive
from pkit.archive.cas import Hashes, cas_path, hash_file
from pkit.archive.db import ArchiveDB, hashes_of
from pkit.archive.errors import ArchiveError, InputError
from pkit.meta.db import MetaDB
from pkit.meta.models import File, Person

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
    assert record.sha256 == hashes.sha256
    assert record.sha1 == hashes.sha1
    assert record.md5 == hashes.md5
    assert record.identifiers == []
    assert record.locations == []


def test_hashes_of_reads_columns_and_falls_back_to_id():
    sha256 = "a" * 64
    full = File(id=f"sha256:{sha256}", sha256=sha256, sha1="b" * 40, md5="c" * 32)
    legacy = File(id=f"sha256:{sha256}")  # columns empty: id is the fallback

    assert hashes_of(full) == Hashes(sha256=sha256, sha1="b" * 40, md5="c" * 32)
    assert hashes_of(legacy) == Hashes(sha256=sha256)


def test_hash_index_maps_all_hash_columns(repo, db_path):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)
    hashes = hash_file(tree / "v1.cbz")

    with ArchiveDB(db_path) as db:
        index = db.hash_index()

    assert index == {
        f"sha256:{hashes.sha256}": hashes.record_id,
        f"sha1:{hashes.sha1}": hashes.record_id,
        f"md5:{hashes.md5}": hashes.record_id,
    }


def test_file_record_upgrades_legacy_hash_tokens(db_path):
    sha256 = "a" * 64
    with ArchiveDB(db_path) as db:
        db.put(File(id=f"sha256:{sha256}", identifiers=[f"sha256:{sha256}", "sha1:b", "md5:c"]))
        upgraded = db.file_record(Hashes(sha256=sha256, sha1="b" * 40, md5="c" * 32), 7)
        again = db.file_record(Hashes(sha256=sha256, sha1="b" * 40, md5="c" * 32), 0)

    assert upgraded.id == f"sha256:{sha256}"
    assert upgraded.identifiers == []  # hash tokens moved out of identifiers
    assert upgraded.sha256 == sha256
    assert upgraded.sha1 == "b" * 40
    assert upgraded.md5 == "c" * 32
    assert upgraded.size == 7
    assert again == upgraded  # the second call is a no-op


def test_load_file_rejects_non_file_record(db_path):
    record_id = "sha256:" + "a" * 64
    with MetaDB(db_path) as meta:
        meta.put(Person(id=record_id, name="not a file"))

    with ArchiveDB(db_path) as db, pytest.raises(InputError, match="is not a file record"):
        db.load_file(record_id)


def test_hash_index_skips_empty_hash_columns(db_path):
    with ArchiveDB(db_path) as db:
        db.put(File(id="sha256:" + "a" * 64))

        assert db.hash_index() == {}


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


def test_add_skips_rehash_of_registered_symlinks(repo, db_path, monkeypatch):
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)
    calls: list[str] = []
    real_hash_file = hash_file

    def traced_hash_file(path):
        calls.append(str(path))
        return real_hash_file(path)

    monkeypatch.setattr("pkit.archive.hash_file", traced_hash_file)

    result = archive.add(tree, db_path=db_path)

    assert result == archive.AddResult(added=0, relinked=0, skipped=0)
    assert calls == []  # a fully recorded object is never re-hashed


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


def test_load_file_rejects_non_file_record_sharing_the_id(db_path):
    with MetaDB(db_path) as meta:
        meta.put(Person(id="p1", name="Ghost"))

    with ArchiveDB(db_path) as db, pytest.raises(InputError, match="is not a file record"):
        db.load_file("p1")
