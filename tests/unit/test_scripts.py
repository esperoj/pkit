"""Smoke tests for the scripts/ helpers.

They cover shell syntax, argument validation, the clean fallbacks when
optional external binaries (fzf, age, parallel, zstd, split) are missing,
and the archive-pack defaults: zstd -19 on all cores with a content
checksum, 256M chunks named .part-001 upward, and byte-for-byte
reproducible output. Full interactive flows are exercised by hand; see
scripts/README.org.
"""

import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
BASH = shutil.which("bash") or "/bin/bash"
ALL_SCRIPTS = ("meta-diff-fzf", "archive-pack", "archive-verify-sweep")

needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
needs_age = pytest.mark.skipif(
    shutil.which("age") is None or shutil.which("age-keygen") is None,
    reason="age / age-keygen not installed",
)

# Records the argv age was called with, then fakes a ciphertext so the
# split/encrypt plumbing can be tested without real keys.
AGE_STUB = """
printf '%s\\n' "$@" > "$AGE_STUB_LOG"
prev=""
out=""
for arg in "$@"; do
  if [ "$prev" = "-o" ]; then out=$arg; fi
  prev=$arg
done
printf 'stub-ciphertext\\n' > "$out"
"""


def run_script(
    script: str,
    *args: str,
    path: str | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    run_env = dict(os.environ)
    if path is not None:
        run_env["PATH"] = path
    if env:
        run_env.update(env)
    return subprocess.run(
        [BASH, str(SCRIPTS / script), *args],
        check=False,
        capture_output=True,
        text=True,
        env=run_env,
        cwd=None if cwd is None else str(cwd),
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


def stub_tool(tmp_path: Path, name: str, body: str) -> Path:
    """An executable fake in the restricted PATH (absolute bash shebang)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    tool = bin_dir / name
    tool.write_text(f"#!{BASH}\n{body}", encoding="utf-8")
    tool.chmod(0o755)
    return tool


def sample_tree(tmp_path: Path) -> Path:
    source = tmp_path / "book"
    source.mkdir()
    (source / "b.txt").write_text("two\n", encoding="utf-8")
    (source / "a.txt").write_text("one\n", encoding="utf-8")
    return source


def noisy_tree(tmp_path: Path, size: int = 200 * 1024) -> Path:
    """Compressible but not trivial, so levels and chunking both matter."""
    rng = random.Random(0)
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
    source = tmp_path / "noisy"
    source.mkdir()
    text = []
    while sum(len(line) + 1 for line in text) < size:
        text.append(" ".join(rng.choice(words) for _ in range(12)))
    (source / "words.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    return source


def unpack(data: bytes, dest: Path, identity: Path | None = None) -> list[str]:
    """chunk bytes -> (age) -> zstd -> tar, returning extracted file names."""
    if identity is not None:
        data = subprocess.run(
            ["age", "-d", "-i", str(identity)],
            input=data,
            capture_output=True,
            check=True,
        ).stdout
    tar_bytes = subprocess.run(
        ["zstd", "-d", "--stdout"], input=data, capture_output=True, check=True
    ).stdout
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["tar", "-xf", "-", "-C", str(dest)],
        input=tar_bytes,
        capture_output=True,
        check=True,
    )
    return sorted(
        p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()
    )


def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "records"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"], check=True
    )
    return repo


def commit(repo: Path, table: str, rows: list[dict]) -> None:
    repo.joinpath(f"{table}.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8"
    )
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


class TestArchivePackArguments:
    def test_missing_path_fails_cleanly(self, tmp_path):
        result = run_script("archive-pack", str(tmp_path / "nope"))
        assert result.returncode == 2
        assert "no such file or directory" in result.stderr

    def test_no_path_prints_usage(self, tmp_path):
        result = run_script("archive-pack")
        assert result.returncode == 2
        assert "Usage:" in result.stderr

    def test_unknown_option_fails_cleanly(self, tmp_path):
        result = run_script("archive-pack", "--bogus", str(tmp_path))
        assert result.returncode == 2
        assert "unknown option" in result.stderr

    def test_second_path_fails_cleanly(self, tmp_path):
        result = run_script("archive-pack", str(tmp_path), str(tmp_path))
        assert result.returncode == 2
        assert "unexpected argument" in result.stderr

    @pytest.mark.parametrize("flag", ["-o", "-r", "-s", "-l", "-j"])
    def test_option_without_value_fails_cleanly(self, tmp_path, flag):
        result = run_script("archive-pack", flag)
        assert result.returncode == 2
        assert "needs a value" in result.stderr

    @pytest.mark.parametrize("size", ["abc", "0", "-5", "12X", "M", ""])
    def test_invalid_split_size_fails_cleanly(self, tmp_path, size):
        source = sample_tree(tmp_path)
        result = run_script("archive-pack", "-s", size, str(source))
        assert result.returncode == 2, size
        assert "invalid split size" in result.stderr

    @pytest.mark.parametrize("level", ["0", "20", "abc", "-1", "19.5"])
    def test_invalid_level_fails_cleanly(self, tmp_path, level):
        source = sample_tree(tmp_path)
        result = run_script("archive-pack", "-l", level, str(source))
        assert result.returncode == 2, level
        assert "invalid level" in result.stderr

    @pytest.mark.parametrize("threads", ["abc", "-1", "10000"])
    def test_invalid_threads_fails_cleanly(self, tmp_path, threads):
        source = sample_tree(tmp_path)
        result = run_script("archive-pack", "-j", threads, str(source))
        assert result.returncode == 2, threads
        assert "invalid threads" in result.stderr

    def test_recipient_and_passphrase_are_exclusive(self, tmp_path):
        source = sample_tree(tmp_path)
        result = run_script(
            "archive-pack", "-r", "age1example", "-p", str(source)
        )
        assert result.returncode == 2
        assert "cannot combine" in result.stderr

    def test_help_documents_defaults(self, tmp_path):
        result = run_script("archive-pack", "--help")
        assert result.returncode == 0
        assert "256M" in result.stdout
        assert "part-001" in result.stdout
        assert "default: 19" in result.stdout
        assert "0 = all cores" in result.stdout
        assert "--no-split" in result.stdout
        assert "--threads 1" in result.stdout


class TestArchivePackDependencies:
    def test_missing_tar_fails_cleanly(self, tmp_path):
        source = sample_tree(tmp_path)
        result = run_script("archive-pack", str(source), path=restricted_path(tmp_path))
        assert result.returncode == 2
        assert "missing required tool: tar" in result.stderr

    @pytest.mark.skipif(shutil.which("zstd") is not None, reason="zstd installed")
    def test_missing_zstd_fails_cleanly(self, tmp_path):
        source = sample_tree(tmp_path)
        result = run_script("archive-pack", str(source))
        assert result.returncode == 2
        assert "missing required tool: zstd" in result.stderr

    def test_missing_split_fails_cleanly(self, tmp_path):
        source = sample_tree(tmp_path)
        result = run_script(
            "archive-pack",
            str(source),
            path=restricted_path(tmp_path, "tar", "zstd"),
        )
        assert result.returncode == 2
        assert "missing required tool: split" in result.stderr

    @pytest.mark.parametrize("flags", [["-r", "age1example"], ["-p"]])
    def test_missing_age_fails_cleanly(self, tmp_path, flags):
        source = sample_tree(tmp_path)
        result = run_script(
            "archive-pack",
            *flags,
            str(source),
            path=restricted_path(tmp_path, "tar", "zstd", "split"),
        )
        assert result.returncode == 2
        assert "missing required tool: age" in result.stderr


@needs_zstd
class TestArchivePackOutput:
    @pytest.mark.parametrize("threads", ["0", "1"])
    def test_pack_is_deterministic(self, tmp_path, threads):
        source = sample_tree(tmp_path)
        first = tmp_path / "one.tar.zst"
        second = tmp_path / "two.tar.zst"
        for out in (first, second):
            result = run_script(
                "archive-pack", "--no-split", "-j", threads, "-o", str(out), str(source)
            )
            assert result.returncode == 0, result.stderr
        assert first.read_bytes() == second.read_bytes()

    def test_chunked_pack_is_deterministic(self, tmp_path):
        source = noisy_tree(tmp_path)
        first = tmp_path / "one.tar.zst"
        second = tmp_path / "two.tar.zst"
        for out in (first, second):
            result = run_script("archive-pack", "-s", "4K", "-o", str(out), str(source))
            assert result.returncode == 0, result.stderr
        one = sorted(tmp_path.glob("one.tar.zst.part-*"))
        two = sorted(tmp_path.glob("two.tar.zst.part-*"))
        assert len(one) > 1
        assert [p.read_bytes() for p in one] == [p.read_bytes() for p in two]

    def test_default_splits_with_numeric_suffixes(self, tmp_path):
        source = sample_tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        result = run_script("archive-pack", "-o", str(out), str(source))
        assert result.returncode == 0, result.stderr
        # One small chunk by default; the whole file is gone, no aa/ab names.
        assert sorted(p.name for p in tmp_path.glob("out.tar.zst*")) == [
            "out.tar.zst.part-001"
        ]
        assert not out.exists()
        assert "part-001" in result.stdout

    def test_default_output_name_uses_basename(self, tmp_path):
        source = sample_tree(tmp_path)
        work = tmp_path / "work"
        work.mkdir()
        result = run_script("archive-pack", str(source), cwd=work)
        assert result.returncode == 0, result.stderr
        assert (work / "book.tar.zst.part-001").exists()

    def test_no_split_keeps_one_whole_file(self, tmp_path):
        source = sample_tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        result = run_script("archive-pack", "--no-split", "-o", str(out), str(source))
        assert result.returncode == 0, result.stderr
        assert out.is_file()
        assert list(tmp_path.glob("out.tar.zst.part-*")) == []

    def test_chunks_are_numbered_and_reassemble_exactly(self, tmp_path):
        source = tmp_path / "book"
        source.mkdir()
        (source / "random.bin").write_bytes(os.urandom(20 * 1024))
        whole = tmp_path / "whole.tar.zst"
        chunked = tmp_path / "chunked.tar.zst"
        assert (
            run_script(
                "archive-pack", "--no-split", "-o", str(whole), str(source)
            ).returncode
            == 0
        )
        assert (
            run_script("archive-pack", "-s", "4K", "-o", str(chunked), str(source)).returncode
            == 0
        )
        parts = sorted(tmp_path.glob("chunked.tar.zst.part-*"))
        assert len(parts) > 1
        assert [p.name for p in parts] == [
            f"chunked.tar.zst.part-{i:03d}" for i in range(1, len(parts) + 1)
        ]
        assert b"".join(p.read_bytes() for p in parts) == whole.read_bytes()
        assert not chunked.exists()

    def test_chunks_round_trip(self, tmp_path):
        source = tmp_path / "book"
        source.mkdir()
        payload = os.urandom(12 * 1024)
        (source / "random.bin").write_bytes(payload)
        (source / "a.txt").write_text("one\n", encoding="utf-8")
        out = tmp_path / "out.tar.zst"
        assert run_script("archive-pack", "-s", "4K", "-o", str(out), str(source)).returncode == 0
        parts = sorted(tmp_path.glob("out.tar.zst.part-*"))
        assert len(parts) > 1
        dest = tmp_path / "restored"
        assert unpack(b"".join(p.read_bytes() for p in parts), dest) == [
            "book/a.txt",
            "book/random.bin",
        ]
        assert (dest / "book" / "random.bin").read_bytes() == payload

    def test_frame_carries_content_checksum(self, tmp_path):
        source = sample_tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        assert run_script("archive-pack", "--no-split", "-o", str(out), str(source)).returncode == 0
        data = out.read_bytes()
        assert data[:4] == b"\x28\xb5\x2f\xfd"  # zstd magic
        assert data[4] & 0x04, "expected content-checksum flag in frame header"
        assert subprocess.run(["zstd", "--test", str(out)], capture_output=True).returncode == 0

    def test_level_flag_is_honoured(self, tmp_path):
        source = noisy_tree(tmp_path)
        fast = tmp_path / "fast.tar.zst"
        best = tmp_path / "best.tar.zst"
        assert run_script("archive-pack", "-S", "-l", "1", "-o", str(fast), str(source)).returncode == 0
        assert run_script("archive-pack", "-S", "-o", str(best), str(source)).returncode == 0
        assert best.read_bytes() != fast.read_bytes()
        assert best.stat().st_size <= fast.stat().st_size

    def test_single_file_and_trailing_slash(self, tmp_path):
        note = tmp_path / "note.txt"
        note.write_text("hello\n", encoding="utf-8")
        out = tmp_path / "note.tar.zst"
        assert run_script("archive-pack", "-S", "-o", str(out), str(note)).returncode == 0
        assert unpack(out.read_bytes(), tmp_path / "one") == ["note.txt"]

        source = sample_tree(tmp_path)
        out2 = tmp_path / "dir.tar.zst"
        assert (
            run_script("archive-pack", "-S", "-o", str(out2), str(source) + "/").returncode
            == 0
        )
        assert unpack(out2.read_bytes(), tmp_path / "two") == ["book/a.txt", "book/b.txt"]

    def test_refuses_to_overwrite_whole_output(self, tmp_path):
        source = sample_tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        out.write_bytes(b"keep me")
        result = run_script("archive-pack", "--no-split", "-o", str(out), str(source))
        assert result.returncode == 2
        assert "refusing to overwrite" in result.stderr
        assert out.read_bytes() == b"keep me"

    def test_refuses_to_overwrite_existing_chunk(self, tmp_path):
        source = sample_tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        chunk = tmp_path / "out.tar.zst.part-001"
        chunk.write_bytes(b"keep me")
        result = run_script("archive-pack", "-o", str(out), str(source))
        assert result.returncode == 2
        assert "refusing to overwrite" in result.stderr
        assert chunk.read_bytes() == b"keep me"
        assert not out.exists()

    def test_failure_leaves_no_partial_output(self, tmp_path):
        source = sample_tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        path = restricted_path(tmp_path, "tar", "rm")
        stub_tool(tmp_path, "zstd", "exit 1\n")
        result = run_script("archive-pack", "-o", str(out), str(source), path=path)
        assert result.returncode != 0
        assert "removed partial output" in result.stderr
        assert list(tmp_path.glob("out.tar.zst*")) == []


@needs_zstd
class TestArchivePackEncryption:
    @pytest.mark.parametrize(
        "flags,expected",
        [
            (["-p"], ["-p"]),
            (["-r", "age1aaa", "-r", "age1bbb"], ["-r", "age1aaa", "-r", "age1bbb"]),
        ],
    )
    def test_age_flags_and_temp_cleanup(self, tmp_path, flags, expected):
        source = sample_tree(tmp_path)
        out = tmp_path / "out.tar.zst.age"
        log = tmp_path / "age-args.txt"
        path = restricted_path(tmp_path, "tar", "zstd", "split", "rm")
        stub_tool(tmp_path, "age", AGE_STUB)
        result = run_script(
            "archive-pack",
            *flags,
            "-o",
            str(out),
            str(source),
            path=path,
            env={"AGE_STUB_LOG": str(log)},
        )
        assert result.returncode == 0, result.stderr
        logged = log.read_text(encoding="utf-8").splitlines()
        assert logged[: len(expected)] == expected
        assert "-R" not in logged  # age -R is recipients-file, not passphrase
        assert (tmp_path / "out.tar.zst.age.part-001").exists()
        assert not (tmp_path / "out.tar.zst").exists()  # plaintext temp removed

    @needs_age
    def test_encrypted_chunks_round_trip(self, tmp_path):
        identity = tmp_path / "key.txt"
        generated = subprocess.run(
            ["age-keygen"], capture_output=True, text=True, check=True
        )
        identity.write_text(generated.stdout, encoding="utf-8")
        recipient = next(
            line.split(":", 1)[1].strip()
            for line in generated.stdout.splitlines()
            if line.startswith("# public key:")
        )
        source = noisy_tree(tmp_path)
        out = tmp_path / "out.tar.zst.age"
        result = run_script(
            "archive-pack", "-r", recipient, "-s", "4K", "-o", str(out), str(source)
        )
        assert result.returncode == 0, result.stderr
        parts = sorted(tmp_path.glob("out.tar.zst.age.part-*"))
        assert len(parts) > 1
        assert not (tmp_path / "out.tar.zst").exists()
        files = unpack(
            b"".join(p.read_bytes() for p in parts),
            tmp_path / "restored",
            identity=identity,
        )
        assert files == ["noisy/words.txt"]


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
