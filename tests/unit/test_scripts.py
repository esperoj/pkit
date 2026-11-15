"""Smoke tests for the scripts/ helpers.

They cover shell syntax, argument validation and the clean fallbacks when
optional external binaries (fzf, age, parallel, zstd) are missing — the
same paths CI takes. Full interactive flows are exercised by hand; see
scripts/README.org.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
BASH = shutil.which("bash") or "/bin/bash"

ALL_SCRIPTS = ("meta-diff-fzf", "archive-pack", "archive-verify-sweep")


def run_script(script: str, *args: str, path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [BASH, str(SCRIPTS / script), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize("script", ALL_SCRIPTS)
def test_script_is_executable_and_syntax_valid(script):
    assert SCRIPTS.joinpath(script).stat().st_mode & 0o111

    result = subprocess.run(
        [BASH, "-n", str(SCRIPTS / script)], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr


def restricted_path(tmp_path: Path, *tools: str) -> str:
    """A PATH directory exposing only `tools`, so missing ones stay missing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in tools:
        found = shutil.which(tool)
        if found is not None:
            bin_dir.joinpath(tool).symlink_to(found)
    return str(bin_dir)


def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "records"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    return repo


def commit(repo: Path, table: str, rows: list[dict]) -> None:
    repo.joinpath(f"{table}.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestMetaDiffFzf:
    def test_missing_fzf_fails_cleanly(self, tmp_path):
        repo = git_repo(tmp_path)
        commit(repo, "persons", [{"id": "p1", "type": "person", "name": "Jun"}])
        repo.joinpath("persons.json").write_text(
            json.dumps([{"id": "p1", "type": "person", "name": "Jun M."}]) + "\n",
            encoding="utf-8",
        )

        result = run_script(
            "meta-diff-fzf", str(repo), path=restricted_path(tmp_path, "git", "python3")
        )

        assert result.returncode == 2
        assert "missing required tool: fzf" in result.stderr
        assert result.stdout == ""

    def test_no_changes_exits_one(self, tmp_path):
        repo = git_repo(tmp_path)
        commit(repo, "persons", [{"id": "p1", "type": "person", "name": "Jun"}])

        result = run_script(
            "meta-diff-fzf", str(repo), path=restricted_path(tmp_path, "git", "python3")
        )

        assert result.returncode == 1
        assert "no record changes" in result.stderr

    def test_missing_repo_directory_fails_cleanly(self, tmp_path):
        result = run_script(
            "meta-diff-fzf",
            str(tmp_path / "nope"),
            path=restricted_path(tmp_path, "git", "python3"),
        )

        assert result.returncode == 2
        assert "no such repo directory" in result.stderr

    def test_preview_prints_record_diff(self, tmp_path):
        repo = git_repo(tmp_path)
        commit(repo, "persons", [{"id": "p1", "type": "person", "name": "Jun"}])
        repo.joinpath("persons.json").write_text(
            json.dumps([{"id": "p1", "type": "person", "name": "Jun M."}]) + "\n",
            encoding="utf-8",
        )

        result = run_script(
            "meta-diff-fzf",
            "--preview",
            "persons",
            "p1",
            str(repo),
            path=restricted_path(tmp_path, "git", "python3"),
        )

        assert result.returncode == 0, result.stderr
        assert '-  "name": "Jun"' in result.stdout
        assert '+  "name": "Jun M."' in result.stdout
        assert "HEAD persons.json p1" in result.stdout


class TestArchivePack:
    def test_missing_path_fails_cleanly(self, tmp_path):
        result = run_script("archive-pack", str(tmp_path / "nope"))

        assert result.returncode == 2
        assert "no such file or directory" in result.stderr

    def test_unknown_option_fails_cleanly(self, tmp_path):
        result = run_script("archive-pack", "--bogus", str(tmp_path))

        assert result.returncode == 2
        assert "unknown option" in result.stderr

    @pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
    def test_pack_is_deterministic(self, tmp_path):
        source = tmp_path / "book"
        source.mkdir()
        (source / "b.txt").write_text("two\n")
        (source / "a.txt").write_text("one\n")

        first = tmp_path / "one.tar.zst"
        second = tmp_path / "two.tar.zst"
        assert run_script("archive-pack", "-o", str(first), str(source)).returncode == 0
        assert run_script("archive-pack", "-o", str(second), str(source)).returncode == 0

        assert first.read_bytes() == second.read_bytes()

    @pytest.mark.skipif(shutil.which("zstd") is not None, reason="zstd installed")
    def test_missing_zstd_fails_cleanly(self, tmp_path):
        source = tmp_path / "book"
        source.mkdir()
        (source / "a.txt").write_text("one\n")

        result = run_script("archive-pack", str(source))

        assert result.returncode == 2
        assert "missing required tool: zstd" in result.stderr


class TestArchiveVerifySweep:
    def test_invalid_shard_fails_cleanly(self, tmp_path):
        for shard in ("5/3", "abc", "0/4", "1/0", "/4"):
            result = run_script("archive-verify-sweep", "--shard", shard, str(tmp_path))
            assert result.returncode == 2, shard
            assert "invalid shard" in result.stderr

    def test_unknown_option_fails_cleanly(self, tmp_path):
        result = run_script("archive-verify-sweep", "--bogus", str(tmp_path))

        assert result.returncode == 2
        assert "unknown option" in result.stderr

    def test_missing_tree_fails_cleanly(self, tmp_path):
        result = run_script("archive-verify-sweep", str(tmp_path / "nope"))

        assert result.returncode == 2
        assert "no such directory" in result.stderr

    def test_no_cas_links_exits_one(self, tmp_path):
        empty = tmp_path / "tree"
        empty.mkdir()

        result = run_script(
            "archive-verify-sweep",
            str(empty),
            path=restricted_path(tmp_path, "find", "sed", "grep", "sort", "awk"),
        )

        assert result.returncode == 1
        assert "no CAS-linked files" in result.stderr

    def test_missing_parallel_fails_cleanly(self, tmp_path):
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "obj").symlink_to("../cas/" + "a" * 64)

        result = run_script(
            "archive-verify-sweep",
            str(tree),
            path=restricted_path(tmp_path, "find", "sed", "grep", "sort", "awk"),
        )

        assert result.returncode == 2
        assert "missing required tool: GNU parallel" in result.stderr
