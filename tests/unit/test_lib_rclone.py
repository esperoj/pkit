"""Tests for pkit.lib.rclone: exit-code mapping, listing, delete, paths."""

import json
from pathlib import Path

import pytest
from tests.conftest import FakeRclone

from pkit.lib import rclone
from pkit.lib.shell import CommandNotFound, ShellError

ROWS = [{"Path": "dir/f.txt", "IsDir": False, "Hashes": {"MD5": "b" * 32}}]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRclone:
    fake = FakeRclone()
    monkeypatch.setattr(rclone.shell, "run", fake)
    return fake


@pytest.mark.parametrize(
    ("code", "exc_class"),
    [
        (3, rclone.NotFoundError),
        (4, rclone.NotFoundError),
        (5, rclone.TransientError),
        (1, rclone.RcloneError),
        (8, rclone.RcloneError),
    ],
)
def test_run_maps_exit_codes(fake: FakeRclone, code: int, exc_class):
    fake.enqueue(ShellError(f"rclone exited {code}", returncode=code))

    with pytest.raises(exc_class):
        rclone.run(["lsjson", "ia:x"])


def test_run_returns_stdout(fake: FakeRclone):
    fake.enqueue("raw output")

    assert rclone.run(["lsjson", "ia:x"]) == "raw output"
    assert fake.calls == [["rclone", "lsjson", "ia:x"]]


def test_run_passes_command_not_found_through(monkeypatch: pytest.MonkeyPatch):
    def raise_not_found(args, *, timeout=None):
        raise CommandNotFound("command not found: rclone")

    monkeypatch.setattr(rclone.shell, "run", raise_not_found)

    with pytest.raises(CommandNotFound):
        rclone.run(["lsjson", "ia:x"])


def test_try_run_returns_none_on_not_found(fake: FakeRclone):
    fake.enqueue(ShellError("rclone exited 3", returncode=3))

    assert rclone.try_run(["lsjson", "--stat", "ia:x/missing"]) is None


def test_try_run_returns_stdout_when_found(fake: FakeRclone):
    fake.enqueue("{}")

    assert rclone.try_run(["lsjson", "--stat", "ia:x/f"]) == "{}"


def test_try_run_raises_other_codes(fake: FakeRclone):
    fake.enqueue(ShellError("rclone exited 1", returncode=1))

    with pytest.raises(rclone.RcloneError):
        rclone.try_run(["lsjson", "ia:x"])


def test_lsjson_parses_rows(fake: FakeRclone):
    fake.rows = ROWS

    assert rclone.lsjson("ia:x") == ROWS
    assert fake.calls == [["rclone", "lsjson", "--recursive", "--files-only", "--hash", "ia:x"]]


def test_lsjson_rejects_invalid_json(fake: FakeRclone):
    fake.enqueue("not-json{")

    with pytest.raises(rclone.RcloneError, match="invalid JSON"):
        rclone.lsjson("ia:x")


def test_statjson_missing_is_none(fake: FakeRclone):
    fake.enqueue(ShellError("rclone exited 4", returncode=4))

    assert rclone.statjson("ia:x/missing") is None


def test_statjson_returns_the_row(fake: FakeRclone):
    row = {"IsDir": False, "Hashes": {"MD5": "b" * 32}}
    fake.enqueue(json.dumps(row))

    assert rclone.statjson("ia:x/f") == row
    assert fake.calls == [["rclone", "lsjson", "--stat", "--hash", "ia:x/f"]]


def test_copyto_adds_copy_links(fake: FakeRclone, tmp_path: Path):
    source = tmp_path / "f"
    source.write_bytes(b"data")

    rclone.copyto(source, "ia:x/dir/f")

    assert fake.calls == [["rclone", "copyto", "--copy-links", str(source), "ia:x/dir/f"]]


def test_copy_files_builds_the_manifest_call(fake: FakeRclone, tmp_path: Path):
    manifest = tmp_path / "files.txt"
    manifest.write_text("f\n")

    rclone.copy_files(tmp_path, "ia:x", manifest)

    assert fake.calls == [
        [
            "rclone",
            "copy",
            "--copy-links",
            "--checksum",
            "--files-from-raw",
            str(manifest),
            str(tmp_path),
            "ia:x",
        ]
    ]


def test_list_paths_splits_lines(fake: FakeRclone):
    fake.enqueue("a.txt\nsub/b.txt\n\n")

    assert rclone.list_paths("ia:x") == ["a.txt", "sub/b.txt"]
    assert fake.calls == [["rclone", "lsf", "--recursive", "--files-only", "ia:x"]]


def test_list_paths_with_min_age(fake: FakeRclone):
    fake.enqueue("a.txt\n")

    rclone.list_paths("pkit-archive:", min_age="35d")

    assert fake.calls == [
        ["rclone", "lsf", "--recursive", "--files-only", "--min-age=35d", "pkit-archive:"]
    ]


def test_delete_with_min_age_and_dry_run(fake: FakeRclone):
    rclone.delete("pkit-archive:", min_age="35d", dry_run=True)

    assert fake.calls == [["rclone", "delete", "--dry-run", "--min-age=35d", "pkit-archive:"]]


def test_delete_without_options_is_bare(fake: FakeRclone):
    rclone.delete("ia:x")

    assert fake.calls == [["rclone", "delete", "ia:x"]]
