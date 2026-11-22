"""The archive lifecycle end to end: add, copy, verify, drop, get.

One in-memory remote; sqlite, the CAS, hashing and the CLI are all real.
"""

import json
import os

import pytest

from pkit import archive
from pkit.archive.cas import cas_path, hash_file
from pkit.archive.cli import main as archive_main
from pkit.archive.errors import ArchiveError, InputError
from pkit.lib.cli import EXIT_ERROR, EXIT_OK
from pkit.meta.db import MetaDB
from pkit.meta.models import File

PAYLOAD = b"pandora"


def test_lifecycle_through_the_sdk(repo, db_path, install_backend):
    backend = install_backend(remote="ia:ph")
    tree = repo / "manga"
    item = tree / "v1.cbz"
    hashes = hash_file(item)
    obj = cas_path(repo / ".git" / "pkit-archive", hashes.sha256)

    assert archive.add(tree, db_path=db_path).added == 1

    copied = archive.copy(tree, "ia:ph", db_path=db_path)
    assert copied.copied == 1
    assert copied.locations == ("ia:ph/v1.cbz",)
    assert backend.store["ia:ph/v1.cbz"] == PAYLOAD

    (verified,) = archive.verify(item, db_path=db_path)
    assert [(check.location, check.status) for check in verified.checks] == [
        ("local", "ok"),
        ("ia:ph/v1.cbz", "ok"),
    ]

    (dropped,) = archive.drop(item, db_path=db_path)
    assert dropped.location == "ia:ph/v1.cbz"
    assert not obj.exists()
    assert item.is_symlink() and not os.path.exists(item)  # the name survives, dangling

    (missing,) = archive.verify(item, db_path=db_path)
    assert missing.ok is False  # local is missing now

    (restored,) = archive.get(item, db_path=db_path)
    assert restored.location == "ia:ph/v1.cbz"
    assert obj.read_bytes() == PAYLOAD
    assert item.read_bytes() == PAYLOAD

    assert archive.forget(tree, db_path=db_path).kept == (str(item),)  # object is back


def test_lifecycle_through_the_cli(run_cli, repo, db_path, install_backend):
    install_backend(remote="ia:ph")
    tree = repo / "manga"
    item = tree / "v1.cbz"
    obj = cas_path(repo / ".git" / "pkit-archive", hash_file(item).sha256)

    code, out, err = run_cli(archive_main, ["add", str(tree)])
    assert code == EXIT_OK, err
    assert "1 added" in out

    code, out, err = run_cli(archive_main, ["copy", str(tree), "ia:ph", "--json"])
    assert code == EXIT_OK, err
    assert json.loads(out)["copied"] == 1

    code, out, err = run_cli(archive_main, ["verify", str(item)])
    assert code == EXIT_OK, err
    assert "local\tok" in out
    assert "ia:ph/v1.cbz\tok" in out

    code, out, err = run_cli(archive_main, ["drop", str(item)])
    assert code == EXIT_OK, err
    assert "dropped" in out
    assert not obj.exists()

    code, out, err = run_cli(archive_main, ["verify", str(item)])
    assert code == EXIT_ERROR
    assert "local\tmissing" in out

    code, out, err = run_cli(archive_main, ["get", str(item)])
    assert code == EXIT_OK, err
    assert obj.read_bytes() == PAYLOAD
    assert item.read_bytes() == PAYLOAD


def test_corrupted_remote_never_enters_the_cas(repo, db_path, install_backend):
    backend = install_backend(remote="ia:x")
    tree = repo / "manga"
    item = tree / "v1.cbz"
    archive.add(tree, db_path=db_path)
    archive.copy(tree, "ia:x", db_path=db_path)
    obj = cas_path(repo / ".git" / "pkit-archive", hash_file(item).sha256)
    obj.unlink()
    backend.store["ia:x/v1.cbz"] = b"corrupted"

    with pytest.raises(ArchiveError, match="could be fetched and verified"):
        archive.get(item, db_path=db_path)

    assert not obj.exists()
    assert not (repo / ".git" / "pkit-archive" / obj.name[:2] / obj.name).exists()


def test_drop_reports_transient_and_permanent_verify_failures(repo, db_path, install_backend):
    from pkit.archive.errors import TransientError

    backend = install_backend(remote="ia:x")
    tree = repo / "manga"
    item = tree / "v1.cbz"
    archive.add(tree, db_path=db_path)
    archive.copy(tree, "ia:x", db_path=db_path)

    backend.error = TransientError("hiccup")
    with pytest.raises(TransientError, match="hiccup"):
        archive.drop(item, db_path=db_path)

    backend.error = ArchiveError("gone for good")
    with pytest.raises(ArchiveError, match="gone for good"):
        archive.drop(item, db_path=db_path)


def test_get_reports_transient_fetch_failure(repo, db_path, install_backend):
    from pkit.archive.errors import TransientError

    backend = install_backend(remote="ia:x")
    tree = repo / "manga"
    item = tree / "v1.cbz"
    hashes = hash_file(item)
    archive.add(tree, db_path=db_path)
    archive.copy(tree, "ia:x", db_path=db_path)
    cas_path(repo / ".git" / "pkit-archive", hashes.sha256).unlink()
    backend.error = TransientError("wire cut")

    with pytest.raises(TransientError, match="wire cut"):
        archive.get(item, db_path=db_path)


def test_get_finds_copies_not_recorded_as_locations(repo, db_path, install_backend):
    """A remote spec searches the backend listing when no location matches."""
    from pkit.archive.remote import RemoteFile

    backend = install_backend(remote="ia:x")
    tree = repo / "manga"
    item = tree / "v1.cbz"
    hashes = hash_file(item)
    archive.add(tree, db_path=db_path)  # copied nowhere yet
    cas_path(repo / ".git" / "pkit-archive", hashes.sha256).unlink()
    backend.files.append(RemoteFile("ia:x/elsewhere.cbz", hashes.identifiers))
    backend.store["ia:x/elsewhere.cbz"] = PAYLOAD

    (result,) = archive.get(item, remote="ia:x", db_path=db_path)

    assert result.location == "ia:x/elsewhere.cbz"
    assert item.read_bytes() == PAYLOAD


def test_verify_without_local_cas_reports_unknown(db_path, tmp_path, monkeypatch):
    """No CAS directory above cwd: the local check is unknown, not missing."""
    sha256 = "a" * 64
    with MetaDB(db_path) as meta:
        meta.put(File(id=f"sha256:{sha256}", size=3, sha256=sha256))
    monkeypatch.chdir(tmp_path)  # tmp_path has no .git above it

    (result,) = archive.verify(f"sha256:{sha256}", db_path=db_path, remotes=())

    assert [(check.location, check.status) for check in result.checks] == [("local", "unknown")]


def test_verify_rejects_unresolvable_input(db_path):
    with pytest.raises(InputError, match="cannot resolve a content hash"):
        archive.verify("not-a-hash-and-not-a-file", db_path=db_path)


def test_local_commands_skip_plain_files(tmp_path, db_path, install_backend):
    """Files that are not CAS links are ignored by copy, relink, and forget."""

    install_backend(remote="ia:x")
    (tmp_path / ".git").mkdir()
    tree = tmp_path / "plain"
    tree.mkdir()
    (tree / "regular.txt").write_text("not a cas link")

    assert archive.copy(tree, "ia:x", db_path=db_path).copied == 0
    assert archive.fix(tree).relinked == 0 and archive.fix(tree).missing == ()
    assert archive.forget(tree, db_path=db_path).kept == ()


def test_get_reports_permanent_fetch_failure(repo, db_path, install_backend):
    backend = install_backend(remote="ia:x")
    tree = repo / "manga"
    item = tree / "v1.cbz"
    hashes = hash_file(item)
    archive.add(tree, db_path=db_path)
    archive.copy(tree, "ia:x", db_path=db_path)
    cas_path(repo / ".git" / "pkit-archive", hashes.sha256).unlink()
    backend.error = ArchiveError("remote says no")

    with pytest.raises(ArchiveError, match="no remote copy"):
        archive.get(item, db_path=db_path)


def test_get_by_record_id_never_touches_a_tree(repo, db_path, install_backend):
    from pkit.archive.remote import RemoteFile

    backend = install_backend(remote="ia:x")
    tree = repo / "manga"
    cas_dir = repo / ".git" / "pkit-archive"
    hashes = hash_file(tree / "v1.cbz")
    archive.add(tree, db_path=db_path)
    obj = cas_path(cas_dir, hashes.sha256)

    # Local copy present: returned as-is, no link juggling. A record id has
    # no tree of its own, so the CAS directory is passed explicitly.
    assert archive.get(hashes.record_id, db_path=db_path, cas_dir=cas_dir)[0].location == "local"

    obj.unlink()
    backend.files.append(RemoteFile("ia:x/elsewhere.cbz", hashes.identifiers))
    backend.store["ia:x/elsewhere.cbz"] = PAYLOAD

    (restored,) = archive.get(hashes.record_id, remote="ia:x", db_path=db_path, cas_dir=cas_dir)

    assert restored.location == "ia:x/elsewhere.cbz"
    assert obj.read_bytes() == PAYLOAD


def test_forget_skips_dangling_links_to_unrecorded_content(tmp_path, db_path):
    (tmp_path / ".git").mkdir()
    tree = tmp_path / "t"
    tree.mkdir()
    sha256 = "d" * 64
    (tree / "dead.cbz").symlink_to(f"../.git/pkit-archive/{sha256[:2]}/{sha256}")

    assert archive.forget(tree, db_path=db_path) == archive.ForgetResult(forgotten=(), kept=())


def test_copy_ignores_landed_files_that_match_nothing(repo, db_path, install_backend):
    from pkit.archive.remote import RemoteFile

    hashes = hash_file(repo / "manga" / "v1.cbz")
    backend = install_backend(remote="ia:x", files=[RemoteFile("ia:x/v1.cbz", hashes.identifiers)])
    # The listing already reports the file, so nothing is missing; the odd
    # file the backend reports back matches no known record and is ignored.
    backend.copy = lambda root, entries: iter([RemoteFile("ia:x/unrelated", ("md5:" + "0" * 32,))])
    tree = repo / "manga"
    archive.add(tree, db_path=db_path)

    result = archive.copy(tree, "ia:x", db_path=db_path)

    assert result.copied == 0
    assert result.locations == ()
