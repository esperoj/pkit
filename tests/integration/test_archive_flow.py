"""The archive lifecycle end to end: add, copy, verify, drop, get.

One in-memory remote; sqlite, the CAS, hashing and the CLI are all real.
"""

from __future__ import annotations

import json
import os

import pytest

from pkit import archive
from pkit.archive.cas import cas_path, hash_file
from pkit.archive.cli import main as archive_main
from pkit.archive.errors import ArchiveError
from pkit.lib.cli import EXIT_ERROR, EXIT_OK

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

    verified = archive.verify(item, db_path=db_path)
    assert [(check.location, check.status) for check in verified.checks] == [
        ("local", "ok"),
        ("ia:ph/v1.cbz", "ok"),
    ]

    dropped = archive.drop(item, db_path=db_path)
    assert dropped.location == "ia:ph/v1.cbz"
    assert not obj.exists()
    assert item.is_symlink() and not os.path.exists(item)  # the name survives, dangling

    assert archive.verify(item, db_path=db_path).ok is False  # local is missing now

    restored = archive.get(item, db_path=db_path)
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
