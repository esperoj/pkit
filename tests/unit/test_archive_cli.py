from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence

import pytest

from pkit import archive
from pkit.archive.cas import cas_path, hash_file
from pkit.archive.cli import main
from pkit.archive.rclone import RcloneBackend
from pkit.lib.cli import EXIT_ERROR, EXIT_OK, EXIT_TEMPFAIL, EXIT_USAGE
from pkit.meta.cli import main as meta_main

PAYLOAD = b"pandora"


class FakeRclone:
    """Stand-in for shell.run: answers lsjson queries from canned rows."""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], *, timeout=None):
        self.calls.append(list(args))
        out = json.dumps(self.rows) if "lsjson" in args else ""
        return subprocess.CompletedProcess(list(args), 0, out, "")


def _row(hashes, path="v1.cbz"):
    return {"Path": path, "Name": path, "Size": len(PAYLOAD), "IsDir": False, "Hashes": hashes}


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".git").mkdir()
    tree = tmp_path / "manga"
    tree.mkdir()
    (tree / "v1.cbz").write_bytes(PAYLOAD)
    return tmp_path


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "records.db"
    monkeypatch.setenv("PKIT_META_DB_PATH", str(path))
    return path


@pytest.fixture
def added(run_cli, repo, db_path):
    """Tree ingested via the CLI; returns its Hashes."""
    code, _, err = run_cli(main, ["add", str(repo / "manga")])
    assert code == EXIT_OK, err
    return hash_file(repo / "manga" / "v1.cbz")


def test_no_args_is_help(capsys):
    assert main([]) == EXIT_USAGE
    assert "add" in capsys.readouterr().out


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == EXIT_OK
    out = capsys.readouterr().out
    for command in ("add", "fix", "copy", "match", "verify", "drop", "get", "forget"):
        assert command in out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == EXIT_OK
    assert "pkit archive" in capsys.readouterr().out


def test_add_plain_and_json(run_cli, repo, db_path, added):
    code, out, err = run_cli(main, ["add", str(repo / "manga")])

    assert code == EXIT_OK, err
    assert out.strip() == "0 added, 0 relinked, 0 skipped"

    code, out, err = run_cli(main, ["add", str(repo / "manga"), "--json"])
    assert code == EXIT_OK, err
    assert json.loads(out) == {"added": 0, "relinked": 0, "skipped": 0}


def test_add_without_git_is_usage_error(run_cli, tmp_path, db_path):
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    (lonely / "f").write_bytes(b"x")

    code, out, err = run_cli(main, ["add", str(lonely)])

    assert code == EXIT_USAGE
    assert "no .git directory" in err
    assert out == ""


def test_add_maps_transient_to_tempfail(run_cli, repo, db_path, monkeypatch):
    def explode(path):
        raise archive.TransientError("queue is full")

    monkeypatch.setattr("pkit.archive.add", explode)

    code, out, err = run_cli(main, ["add", str(repo / "manga")])

    assert code == EXIT_TEMPFAIL
    assert "queue is full" in err
    assert out == ""


def test_unknown_command_is_usage_error(run_cli):
    code, out, _err = run_cli(main, ["bogus"])

    assert code == EXIT_USAGE
    assert out == ""


def test_fix_reports_relinked(run_cli, repo, db_path, added):
    item = repo / "manga" / "v1.cbz"
    named = os.path.basename(os.readlink(item))
    item.unlink()
    item.symlink_to(f"/old/cas/{named[:2]}/{named}")

    code, out, err = run_cli(main, ["fix", str(repo / "manga")])

    assert code == EXIT_OK, err
    assert "1 relinked" in out
    assert "missing" not in out


def test_fix_reports_missing_objects(run_cli, repo, db_path, added):
    item = repo / "manga" / "v1.cbz"
    named = os.path.basename(os.readlink(item))
    cas_path(repo / ".git" / "pkit-archive", named).unlink()

    code, out, err = run_cli(main, ["fix", str(repo / "manga")])

    assert code == EXIT_OK, err
    assert f"missing {item}" in out


def test_match_registers_locations(run_cli, repo, db_path, added, monkeypatch):
    monkeypatch.setattr(
        "pkit.archive.rclone.shell.run",
        FakeRclone([_row({"SHA-1": added.sha1})]),
    )

    code, out, err = run_cli(main, ["match", "ia:x"])

    assert code == EXIT_OK, err
    assert out.strip() == "1 matched, 0 unknown, 0 without hashes"


def test_copy_uploads_missing(run_cli, repo, db_path, added, monkeypatch):
    monkeypatch.setattr("pkit.archive.rclone.shell.run", FakeRclone([]))
    monkeypatch.setattr(RcloneBackend, "list", lambda self: [])

    code, out, err = run_cli(main, ["copy", str(repo / "manga"), "ia:x"])

    assert code == EXIT_OK, err
    assert out.startswith("1 copied, 0 already present")


def test_verify_local_ok(run_cli, repo, db_path, added):
    code, out, err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK, err
    assert out.strip() == "local\tok"


def test_verify_json(run_cli, repo, db_path, added):
    code, out, err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz"), "--json"])

    assert code == EXIT_OK, err
    payload = json.loads(out)
    assert payload["checks"] == [{"location": "local", "status": "ok"}]


def test_verify_remote_ok(run_cli, repo, db_path, added, monkeypatch):
    monkeypatch.setattr(
        "pkit.archive.rclone.shell.run",
        FakeRclone([_row({"SHA-1": added.sha1})]),
    )
    run_cli(main, ["match", "ia:x"])

    code, out, err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK, err
    assert "ia:x/v1.cbz\tok" in out


def test_verify_mismatch_fails(run_cli, repo, db_path, added, monkeypatch):
    from dataclasses import replace

    from pkit.meta import db

    with db.session(db_path) as conn:
        record = db.get(conn, added.record_id)
        assert record is not None
        db.put(conn, replace(record, locations=["ia:x/v1.cbz"]))
    monkeypatch.setattr(
        "pkit.archive.rclone.shell.run",
        FakeRclone([_row({"SHA-1": "0" * 40})]),
    )

    code, out, _err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_ERROR
    assert "ia:x/v1.cbz\tmismatch" in out


def test_drop_without_remote_fails(run_cli, repo, db_path, added):
    code, out, err = run_cli(main, ["drop", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_ERROR
    assert "refusing to drop" in err
    assert out == ""


def test_get_restores_from_remote(run_cli, repo, db_path, added, monkeypatch):
    obj = cas_path(repo / ".git" / "pkit-archive", added.sha256)
    obj.unlink()

    def fake_fetch(self, location, dest):
        dest.write_bytes(PAYLOAD)

    monkeypatch.setattr(RcloneBackend, "fetch", fake_fetch)
    monkeypatch.setattr(
        "pkit.archive.rclone.shell.run",
        FakeRclone([_row({"SHA-1": added.sha1})]),
    )
    run_cli(main, ["match", "ia:x"])

    code, out, err = run_cli(main, ["get", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK, err
    assert out.strip() == str(obj)
    assert obj.read_bytes() == PAYLOAD


def test_forget_reports_counts(run_cli, repo, db_path, added):
    item = repo / "manga" / "v1.cbz"
    named = os.path.basename(os.readlink(item))
    cas_path(repo / ".git" / "pkit-archive", named).unlink()

    code, out, err = run_cli(main, ["forget", str(repo / "manga")])

    assert code == EXIT_OK, err
    assert out.strip() == "1 forgotten, 0 kept"


def test_remove_location_updates_record(run_cli, repo, db_path, added, monkeypatch):
    monkeypatch.setattr(
        "pkit.archive.rclone.shell.run",
        FakeRclone([_row({"SHA-1": added.sha1})]),
    )
    run_cli(main, ["match", "ia:x"])

    code, out, err = run_cli(
        main, ["remove-location", str(repo / "manga" / "v1.cbz"), "ia:x/v1.cbz"]
    )

    assert code == EXIT_OK, err
    assert out.strip() == f"0 locations remain for sha256:{added.sha256}"


def test_meta_reads_what_archive_writes(run_cli, repo, db_path, added, tmp_path):
    """`pkit archive add` writes records `pkit meta dump` can read."""
    code, out, err = run_cli(meta_main, ["dump", str(tmp_path / "repo")])

    assert code == EXIT_OK, err
    assert out.strip() == "1"
