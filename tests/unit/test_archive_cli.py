import io
import json
import os
import sys
from dataclasses import replace

import pytest

from pkit import archive
from pkit.archive.cas import cas_path, hash_file
from pkit.archive.cli import main
from pkit.archive.db import ArchiveDB
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


def test_verify_rejects_unresolvable_input(run_cli, db_path):
    code, out, err = run_cli(main, ["verify", "not-a-hash-and-not-a-file"])

    assert code == EXIT_USAGE
    assert "cannot resolve a content hash" in err
    assert out == ""


def test_verify_json(run_cli, repo, db_path, added):
    code, out, err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz"), "--json"])

    assert code == EXIT_OK, err
    payload = json.loads(out)
    assert payload == [
        {"record_id": added.record_id, "checks": [{"location": "local", "status": "ok"}]}
    ]


def test_verify_tree_tags_lines_with_record_ids(run_cli, repo, db_path, added):
    (repo / "manga" / "v2.cbz").write_bytes(b"pandora2")
    run_cli(main, ["add", str(repo / "manga")])

    code, out, err = run_cli(main, ["verify", str(repo / "manga")])

    assert code == EXIT_OK, err
    lines = out.strip().splitlines()
    assert len(lines) == 2
    assert all(line.endswith("\tlocal\tok") for line in lines)
    other = hash_file(repo / "manga" / "v2.cbz").record_id
    assert {line.split("\t")[0] for line in lines} == {added.record_id, other}


def test_verify_tree_without_links_is_the_sweep_alert(run_cli, tmp_path, db_path):
    (tmp_path / ".git").mkdir()
    empty = tmp_path / "plain"
    empty.mkdir()
    (empty / "note.txt").write_text("not a cas link")

    code, out, err = run_cli(main, ["verify", str(empty)])

    assert code == EXIT_ERROR, err
    assert out == ""


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


def test_verify_remote_filter_matching_nothing_prints_no_report(run_cli, repo, db_path, added):
    with MetaDB(db_path) as meta:
        record = meta.get(added.record_id)
        assert isinstance(record, File)
        meta.put(replace(record, locations=["ia:x/v1.cbz"]))

    code, out, err = run_cli(main, ["verify", str(repo / "manga" / "v1.cbz"), "other:y"])

    assert code == EXIT_ERROR, err  # nothing verified: the sweep alert
    assert out == ""  # empty report: no data lines


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


def test_drop_tree_drops_every_link(run_cli, repo, db_path, install_backend):
    (repo / "manga" / "v2.cbz").write_bytes(b"pandora2")
    run_cli(main, ["add", str(repo / "manga")])
    install_backend(remote="ia:x")
    run_cli(main, ["copy", str(repo / "manga"), "ia:x"])

    code, out, err = run_cli(main, ["drop", str(repo / "manga")])

    assert code == EXIT_OK, err
    assert len(out.strip().splitlines()) == 2
    assert all("dropped sha256:" in line for line in out.strip().splitlines())
    assert not any(obj.is_file() for obj in (repo / ".git" / "pkit-archive").rglob("*"))


def test_get_restores_from_remote(run_cli, repo, db_path, added, rclone, monkeypatch):
    obj = cas_path(repo / ".git" / "pkit-archive", added.sha256)
    obj.unlink()

    def fake_fetch(self, location, dest, *, progress=None):
        dest.write_bytes(PAYLOAD)

    monkeypatch.setattr(RcloneBackend, "fetch", fake_fetch)
    rclone.rows = [_row({"SHA-1": added.sha1})]
    run_cli(main, ["match", "ia:x"])

    code, out, err = run_cli(main, ["get", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK, err
    assert out.strip() == str(obj)
    assert obj.read_bytes() == PAYLOAD


def test_get_tree_restores_every_link(run_cli, repo, db_path, added, install_backend):
    (repo / "manga" / "v2.cbz").write_bytes(b"pandora2")
    run_cli(main, ["add", str(repo / "manga")])
    install_backend(remote="ia:x")
    run_cli(main, ["copy", str(repo / "manga"), "ia:x"])
    cas = repo / ".git" / "pkit-archive"
    for item in (repo / "manga").iterdir():
        cas_path(cas, os.path.basename(os.readlink(item))).unlink()

    code, out, err = run_cli(main, ["get", str(repo / "manga")])

    assert code == EXIT_OK, err
    lines = out.strip().splitlines()
    assert len(lines) == 2
    assert all(line.startswith(str(cas)) for line in lines)
    assert (repo / "manga" / "v1.cbz").read_bytes() == PAYLOAD
    assert (repo / "manga" / "v2.cbz").read_bytes() == b"pandora2"


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


# --- get --quiet and the progress wiring ---------------------------------------


class TtyStderr(io.StringIO):
    """A stderr that claims to be a terminal, so progress reporting turns on."""

    def isatty(self) -> bool:
        return True


def test_get_passes_progress_when_stderr_is_a_tty(
    run_cli, repo, db_path, added, install_backend, monkeypatch
):
    backend = install_backend(files=[RemoteFile(location="fake:v1.cbz", hashes=added.identifiers)])
    backend.store["fake:v1.cbz"] = PAYLOAD
    with ArchiveDB(db_path) as db:
        db.register(f"sha256:{added.sha256}", ["fake:v1.cbz"])
    (repo / ".git" / "pkit-archive" / added.sha256[:2] / added.sha256).unlink()
    tty = TtyStderr()
    monkeypatch.setattr(sys, "stderr", tty)

    code, _out, _err = run_cli(main, ["get", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK, tty.getvalue()
    assert backend.progress is not None


def test_get_quiet_disables_progress(run_cli, repo, db_path, added, install_backend, monkeypatch):
    backend = install_backend(files=[RemoteFile(location="fake:v1.cbz", hashes=added.identifiers)])
    backend.store["fake:v1.cbz"] = PAYLOAD
    with ArchiveDB(db_path) as db:
        db.register(f"sha256:{added.sha256}", ["fake:v1.cbz"])
    (repo / ".git" / "pkit-archive" / added.sha256[:2] / added.sha256).unlink()
    tty = TtyStderr()
    monkeypatch.setattr(sys, "stderr", tty)

    code, _out, _err = run_cli(main, ["get", "--quiet", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK, tty.getvalue()
    assert backend.progress is None


def test_get_no_progress_when_stderr_is_a_pipe(run_cli, repo, db_path, added, install_backend):
    backend = install_backend(files=[RemoteFile(location="fake:v1.cbz", hashes=added.identifiers)])
    backend.store["fake:v1.cbz"] = PAYLOAD
    with ArchiveDB(db_path) as db:
        db.register(f"sha256:{added.sha256}", ["fake:v1.cbz"])
    (repo / ".git" / "pkit-archive" / added.sha256[:2] / added.sha256).unlink()

    code, _out, _err = run_cli(main, ["get", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_OK
    assert backend.progress is None


def test_verify_wayback_maps_transient_to_tempfail(run_cli, monkeypatch):
    def raise_transient(days, **kwargs):
        raise archive.TransientError("wire cut")

    monkeypatch.setattr(archive, "verify_wayback", raise_transient)

    code, out, err = run_cli(main, ["verify-wayback"])

    assert code == EXIT_TEMPFAIL
    assert "wire cut" in err
    assert out == ""


def test_get_maps_archive_error_to_exit_code(run_cli, repo, monkeypatch):
    def raise_archive_error(*args, **kwargs):
        raise archive.ArchiveError("no remote copy")

    monkeypatch.setattr(archive, "get", raise_archive_error)

    code, out, err = run_cli(main, ["get", str(repo / "manga" / "v1.cbz")])

    assert code == EXIT_ERROR
    assert "no remote copy" in err
    assert out == ""
