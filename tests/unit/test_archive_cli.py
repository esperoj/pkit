from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest

from pkit import archive
from pkit.archive.cas import cas_path, hash_file
from pkit.archive.cli import main
from pkit.archive.rclone import RcloneBackend
from pkit.archive.remote import RemoteFile
from pkit.lib.cli import EXIT_ERROR, EXIT_OK, EXIT_TEMPFAIL, EXIT_USAGE
from pkit.meta.cli import main as meta_main
from pkit.meta.db import MetaDB
from pkit.meta.models import File

PAYLOAD = b"pandora"


def _row(hashes, path="v1.cbz"):
    return {"Path": path, "Name": path, "Size": len(PAYLOAD), "IsDir": False, "Hashes": hashes}


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


def test_match_registers_locations(run_cli, repo, db_path, added, rclone):
    rclone.rows = [_row({"SHA-1": added.sha1})]

    code, out, err = run_cli(main, ["match", "ia:x"])

    assert code == EXIT_OK, err
    assert out.strip() == "1 matched, 0 unknown, 0 without hashes"


def test_copy_uploads_missing(run_cli, repo, db_path, added, rclone):
    code, out, err = run_cli(main, ["copy", str(repo / "manga"), "ia:x"])

    assert code == EXIT_OK, err
    assert out.startswith("1 copied, 0 already present")
    assert any(call[1] == "copy" for call in rclone.calls)


def test_copy_skips_present(run_cli, repo, db_path, added, rclone):
    rclone.rows = [_row({"MD5": added.md5})]

    code, out, err = run_cli(main, ["copy", str(repo / "manga"), "ia:x"])

    assert code == EXIT_OK, err
    assert out.startswith("0 copied, 1 already present")


def test_verify_local_ok(run_cli, repo, db_path, added):
    code, out, err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK, err
    assert out.strip() == "local\tok"


def test_verify_json(run_cli, repo, db_path, added):
    code, out, err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz"), "--json"])

    assert code == EXIT_OK, err
    payload = json.loads(out)
    assert payload["checks"] == [{"location": "local", "status": "ok"}]


def test_verify_remote_ok(run_cli, repo, db_path, added, rclone):
    rclone.rows = [_row({"SHA-1": added.sha1})]
    run_cli(main, ["match", "ia:x"])

    code, out, err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK, err
    assert "ia:x/v1.cbz\tok" in out


def test_verify_mismatch_fails(run_cli, repo, db_path, added, rclone):
    with MetaDB(db_path) as meta:
        record = meta.get(added.record_id)
        assert isinstance(record, File)
        meta.put(replace(record, locations=["ia:x/v1.cbz"]))
    rclone.rows = [_row({"SHA-1": "0" * 40})]

    code, out, _err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_ERROR
    assert "ia:x/v1.cbz\tmismatch" in out


def test_drop_without_remote_fails(run_cli, repo, db_path, added):
    code, out, err = run_cli(main, ["drop", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_ERROR
    assert "refusing to drop" in err
    assert out == ""


def test_drop_success_via_cli(run_cli, repo, db_path, added, install_backend):
    install_backend(remote="ia:x", files=[RemoteFile("ia:x/v1.cbz", (f"sha1:{added.sha1}",))])
    run_cli(main, ["match", "ia:x"])

    code, out, err = run_cli(main, ["drop", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK, err
    assert out.strip() == f"dropped {added.record_id} (verified at ia:x/v1.cbz)"
    assert not cas_path(repo / ".git" / "pkit-archive", added.sha256).exists()


def test_get_restores_from_remote(run_cli, repo, db_path, added, rclone, monkeypatch):
    obj = cas_path(repo / ".git" / "pkit-archive", added.sha256)
    obj.unlink()

    def fake_fetch(self, location, dest):
        dest.write_bytes(PAYLOAD)

    monkeypatch.setattr(RcloneBackend, "fetch", fake_fetch)
    rclone.rows = [_row({"SHA-1": added.sha1})]
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


def test_remove_location_updates_record(run_cli, repo, db_path, added, rclone):
    rclone.rows = [_row({"SHA-1": added.sha1})]
    run_cli(main, ["match", "ia:x"])

    code, out, err = run_cli(
        main, ["remove-location", str(repo / "manga" / "v1.cbz"), "ia:x/v1.cbz"]
    )

    assert code == EXIT_OK, err
    assert out.strip() == f"0 locations remain for {added.record_id}"


def test_meta_reads_what_archive_writes(run_cli, repo, db_path, added, tmp_path):
    """`pkit archive add` writes records `pkit meta dump` can read."""
    code, out, err = run_cli(meta_main, ["dump", str(tmp_path / "repo")])

    assert code == EXIT_OK, err
    assert out.strip() == "1"
