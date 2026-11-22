"""Smoke tests for the scripts/ helpers.

Cover shell syntax, argument validation, clean fallbacks when optional
binaries (fzf, age, parallel, zstd, split) are missing, and archive-pack's
contract: top-folder-only relative members, real mtimes/modes/owner names,
symlinks, hardlinks, sparse holes, xattrs, verification on by default,
zstd -19 + checksum, 256M chunks named .part-001 upward, small results left
whole. Interactive flows: see scripts/README.org.
"""

import json
import os
import pwd
import random
import shutil
import stat
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
NS = 1_234_567_890_123_456_789
BOOK_MEMBERS = ["book/", "book/a.txt", "book/b.txt"]

# Records age's argv, then fakes a ciphertext so encryption plumbing is testable.
AGE_STUB = """
printf '%s\\n' "$@" > "$AGE_STUB_LOG"
prev="" out=""
for arg in "$@"; do
  if [ "$prev" = "-o" ]; then out=$arg; fi
  prev=$arg
done
printf 'stub-ciphertext\\n' > "$out"
"""


def run_script(script, *args, path=None, cwd=None, env=None):
    run_env = dict(os.environ)
    if path is not None:
        run_env["PATH"] = path
    if env:
        run_env.update(env)
    return subprocess.run(
        [BASH, str(SCRIPTS / script), *args],
        check=False, capture_output=True, text=True, env=run_env,
        cwd=None if cwd is None else str(cwd),
    )


def restricted_path(tmp_path: Path, *tools: str) -> str:
    """A PATH directory exposing only `tools`, so missing ones stay missing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in tools:
        found = shutil.which(tool)
        if found is not None:
            bin_dir.joinpath(tool).symlink_to(found)
    return str(bin_dir)


def stub_tool(tmp_path: Path, name: str, body: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    tool = bin_dir / name
    tool.write_text(f"#!{BASH}\n{body}", encoding="utf-8")
    tool.chmod(0o755)


def tar_supports(flag: str) -> bool:
    return subprocess.run(
        ["tar", flag, "-cf", os.devnull, "-T", os.devnull], capture_output=True
    ).returncode == 0


def tree(tmp_path: Path) -> Path:
    """Tiny dir: fits one chunk, so it must stay a whole file."""
    d = tmp_path / "book"
    d.mkdir()
    (d / "b.txt").write_text("two\n", encoding="utf-8")
    (d / "a.txt").write_text("one\n", encoding="utf-8")
    return d


def rich_tree(tmp_path: Path) -> Path:
    """Odd mtime, non-default modes, hardlink and symlink."""
    d = tmp_path / "book"
    d.mkdir()
    f = d / "a.txt"
    f.write_text("one\n", encoding="utf-8")
    f.chmod(0o640)
    os.utime(f, ns=(NS, NS))
    os.link(f, d / "hard.txt")
    (d / "link.txt").symlink_to("a.txt")
    sub = d / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("two\n", encoding="utf-8")
    sub.chmod(0o751)
    return d


def big_tree(tmp_path: Path) -> Path:
    """Incompressible bulk (forces 4K chunks) plus compressible text (levels)."""
    d = tmp_path / "book"
    d.mkdir()
    (d / "random.bin").write_bytes(os.urandom(20 * 1024))
    rng = random.Random(0)
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    lines = [" ".join(rng.choice(words) for _ in range(12)) for _ in range(4000)]
    (d / "words.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def decode(data: bytes, identity: Path | None = None) -> bytes:
    if identity is not None:
        data = subprocess.run(
            ["age", "-d", "-i", str(identity)], input=data, capture_output=True, check=True
        ).stdout
    return subprocess.run(
        ["zstd", "-d", "--stdout"], input=data, capture_output=True, check=True
    ).stdout


def members(tar_bytes: bytes) -> list[str]:
    return subprocess.run(
        ["tar", "-tf", "-"], input=tar_bytes, capture_output=True, check=True
    ).stdout.decode().splitlines()


def unpack(data: bytes, dest: Path, identity: Path | None = None, extra=()) -> list[str]:
    """chunk bytes -> (age) -> zstd -> tar -xp; returns extracted entry names."""
    tar_bytes = decode(data, identity)
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["tar", "-xpf", "-", *extra, "-C", str(dest)],
        input=tar_bytes, capture_output=True, check=True,
    )
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if not p.is_dir())


def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "records"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    return repo


def commit(repo: Path, table: str, rows: list[dict]) -> None:
    repo.joinpath(f"{table}.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)


@pytest.mark.parametrize("script", ALL_SCRIPTS)
def test_script_is_executable_and_syntax_valid(script):
    assert SCRIPTS.joinpath(script).stat().st_mode & 0o111
    result = subprocess.run([BASH, "-n", str(SCRIPTS / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestMetaDiffFzf:
    def test_missing_fzf_fails_cleanly(self, tmp_path):
        repo = git_repo(tmp_path)
        commit(repo, "persons", [{"id": "p1", "type": "person", "name": "Jun"}])
        repo.joinpath("persons.json").write_text(
            json.dumps([{"id": "p1", "type": "person", "name": "Jun M."}]) + "\n", encoding="utf-8"
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
            "meta-diff-fzf", str(tmp_path / "nope"),
            path=restricted_path(tmp_path, "git", "python3"),
        )
        assert result.returncode == 2
        assert "no such repo directory" in result.stderr

    def test_preview_prints_record_diff(self, tmp_path):
        repo = git_repo(tmp_path)
        commit(repo, "persons", [{"id": "p1", "type": "person", "name": "Jun"}])
        repo.joinpath("persons.json").write_text(
            json.dumps([{"id": "p1", "type": "person", "name": "Jun M."}]) + "\n", encoding="utf-8"
        )
        result = run_script(
            "meta-diff-fzf", "--preview", "persons", "p1", str(repo),
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

    def test_no_path_prints_usage(self):
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
    def test_option_without_value_fails_cleanly(self, flag):
        result = run_script("archive-pack", flag)
        assert result.returncode == 2
        assert "needs a value" in result.stderr

    @pytest.mark.parametrize("size", ["abc", "0", "-5", "12X", "M"])
    def test_invalid_split_size(self, tmp_path, size):
        result = run_script("archive-pack", "-s", size, str(tree(tmp_path)))
        assert result.returncode == 2, size
        assert "invalid split size" in result.stderr

    @pytest.mark.parametrize("level", ["0", "20", "abc", "-1", "19.5"])
    def test_invalid_level(self, tmp_path, level):
        result = run_script("archive-pack", "-l", level, str(tree(tmp_path)))
        assert result.returncode == 2, level
        assert "invalid level" in result.stderr

    @pytest.mark.parametrize("threads", ["abc", "-1", "10000"])
    def test_invalid_threads(self, tmp_path, threads):
        result = run_script("archive-pack", "-j", threads, str(tree(tmp_path)))
        assert result.returncode == 2, threads
        assert "invalid threads" in result.stderr

    def test_recipient_and_passphrase_are_exclusive(self, tmp_path):
        result = run_script("archive-pack", "-r", "age1x", "-p", str(tree(tmp_path)))
        assert result.returncode == 2
        assert "cannot combine" in result.stderr

    def test_missing_output_directory(self, tmp_path):
        result = run_script(
            "archive-pack", "-o", str(tmp_path / "nope" / "out.tar.zst"), str(tree(tmp_path))
        )
        assert result.returncode == 2
        assert "no such output directory" in result.stderr

    def test_help_documents_defaults_and_restore(self):
        result = run_script("archive-pack", "--help")
        assert result.returncode == 0
        for text in ("256M", "part-001", "default: 19", "0 = all cores", "--no-split",
                     "--no-verify", "top folder", "--same-owner", "numeric ids", "--xattrs"):
            assert text in result.stdout, text


class TestArchivePackDependencies:
    def test_missing_tar_fails_cleanly(self, tmp_path):
        result = run_script("archive-pack", str(tree(tmp_path)), path=restricted_path(tmp_path))
        assert result.returncode == 2
        assert "missing required tool: tar" in result.stderr

    @pytest.mark.skipif(shutil.which("zstd") is not None, reason="zstd installed")
    def test_missing_zstd_fails_cleanly(self, tmp_path):
        result = run_script("archive-pack", str(tree(tmp_path)))
        assert result.returncode == 2
        assert "missing required tool: zstd" in result.stderr

    def test_missing_split_fails_cleanly(self, tmp_path):
        result = run_script(
            "archive-pack", str(tree(tmp_path)), path=restricted_path(tmp_path, "tar", "zstd")
        )
        assert result.returncode == 2
        assert "missing required tool: split" in result.stderr

    @pytest.mark.parametrize("flags", [["-r", "age1x"], ["-p"]])
    def test_missing_age_fails_cleanly(self, tmp_path, flags):
        result = run_script(
            "archive-pack", *flags, str(tree(tmp_path)),
            path=restricted_path(tmp_path, "tar", "zstd", "split"),
        )
        assert result.returncode == 2
        assert "missing required tool: age" in result.stderr

    def test_failure_leaves_no_partial_output(self, tmp_path):
        out = tmp_path / "out.tar.zst"
        path = restricted_path(tmp_path, "tar", "split", "rm", "mv")
        stub_tool(tmp_path, "zstd", "exit 1\n")
        result = run_script("archive-pack", "-o", str(out), str(tree(tmp_path)), path=path)
        assert result.returncode != 0
        assert "removed partial output" in result.stderr
        assert list(tmp_path.glob("out.tar.zst*")) == []


@needs_zstd
class TestArchivePackPaths:
    @pytest.mark.parametrize("arg", ["book", "./book", "book/", "ABS"])
    def test_stores_top_folder_only_relative(self, tmp_path, arg):
        source = tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        target = str(source) if arg == "ABS" else arg
        result = run_script("archive-pack", "-o", str(out), target, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        names = members(decode(out.read_bytes()))
        assert names == BOOK_MEMBERS
        assert all(not n.startswith("/") and ".." not in n for n in names)

    def test_dot_packs_real_folder_name(self, tmp_path):
        source = tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        result = run_script("archive-pack", "-o", str(out), ".", cwd=source)
        assert result.returncode == 0, result.stderr
        assert members(decode(out.read_bytes())) == BOOK_MEMBERS

    def test_default_output_name_uses_basename(self, tmp_path):
        source = tree(tmp_path)
        work = tmp_path / "work"
        work.mkdir()
        assert run_script("archive-pack", str(source) + "/", cwd=work).returncode == 0
        assert (work / "book.tar.zst").is_file()

    def test_refuses_output_inside_packed_tree(self, tmp_path):
        source = tree(tmp_path)
        inside = source / "out.tar.zst"
        result = run_script("archive-pack", "-o", str(inside), str(source))
        assert result.returncode == 2
        assert "inside the packed tree" in result.stderr
        assert not inside.exists()

    def test_single_file_member_has_no_directory_prefix(self, tmp_path):
        note = tmp_path / "note.txt"
        note.write_text("hello\n", encoding="utf-8")
        out = tmp_path / "note.tar.zst"
        assert run_script("archive-pack", "-o", str(out), str(note)).returncode == 0
        assert members(decode(out.read_bytes())) == ["note.txt"]
        assert unpack(out.read_bytes(), tmp_path / "restored") == ["note.txt"]


@needs_zstd
class TestArchivePackVerify:
    @pytest.mark.parametrize("flags", [[], ["-v"], ["--verify"]])
    def test_verification_is_on_by_default(self, tmp_path, flags):
        out = tmp_path / "out.tar.zst"
        result = run_script("archive-pack", *flags, "-o", str(out), str(rich_tree(tmp_path)))
        assert result.returncode == 0, result.stderr
        assert "verified" in result.stdout
        assert out.is_file()

    def test_no_verify_skips_comparison(self, tmp_path):
        out = tmp_path / "out.tar.zst"
        result = run_script("archive-pack", "-V", "-o", str(out), str(tree(tmp_path)))
        assert result.returncode == 0, result.stderr
        assert "verified" not in result.stdout

    def test_verify_failure_removes_output(self, tmp_path):
        real_tar = shutil.which("tar")
        out = tmp_path / "out.tar.zst"
        path = restricted_path(tmp_path, "zstd", "split", "rm", "mv")
        stub_tool(
            tmp_path, "tar",
            'for a in "$@"; do if [ "$a" = "--compare" ]; then exit 1; fi; done\n'
            f'exec {real_tar} "$@"\n',
        )
        result = run_script("archive-pack", "-o", str(out), str(tree(tmp_path)), path=path)
        assert result.returncode == 2
        assert "verification failed" in result.stderr
        assert list(tmp_path.glob("out.tar.zst*")) == []


@needs_zstd
class TestArchivePackPreservation:
    def test_mtime_modes_and_links_survive(self, tmp_path):
        source = rich_tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        assert run_script("archive-pack", "-o", str(out), str(source)).returncode == 0
        dest = tmp_path / "restored"
        assert unpack(out.read_bytes(), dest) == [
            "book/a.txt", "book/hard.txt", "book/link.txt", "book/sub/b.txt"
        ]
        restored = dest / "book" / "a.txt"
        assert restored.stat().st_mtime_ns == NS
        assert stat.S_IMODE(restored.stat().st_mode) == 0o640
        assert stat.S_IMODE((dest / "book" / "sub").stat().st_mode) == 0o751
        assert os.readlink(dest / "book" / "link.txt") == "a.txt"
        hard = (dest / "book" / "hard.txt").stat()
        assert hard.st_ino == restored.stat().st_ino
        assert hard.st_nlink == 2
        assert restored.read_text(encoding="utf-8") == "one\n"

    def test_owner_names_are_recorded_not_stripped(self, tmp_path):
        try:
            user = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            pytest.skip("current uid has no passwd entry")
        out = tmp_path / "out.tar.zst"
        assert run_script("archive-pack", "-o", str(out), str(tree(tmp_path))).returncode == 0
        listing = subprocess.run(
            ["tar", "-tvf", "-"], input=decode(out.read_bytes()), capture_output=True, check=True
        ).stdout.decode()
        assert user in listing

    def test_xattrs_survive(self, tmp_path):
        if not tar_supports("--xattrs"):
            pytest.skip("tar built without xattr support")
        source = tree(tmp_path)
        try:
            os.setxattr(source / "a.txt", "user.pkit", b"keep")
        except OSError as exc:
            pytest.skip(f"filesystem rejects user xattrs: {exc}")
        out = tmp_path / "out.tar.zst"
        assert run_script("archive-pack", "-o", str(out), str(source)).returncode == 0
        dest = tmp_path / "restored"
        unpack(out.read_bytes(), dest, extra=("--xattrs", "--xattrs-include=*"))
        assert os.getxattr(dest / "book" / "a.txt", "user.pkit") == b"keep"

    def test_sparse_holes_survive(self, tmp_path):
        source = tmp_path / "sparse"
        source.mkdir()
        hole = source / "hole.bin"
        size = 4 * 1024 * 1024
        with hole.open("wb") as fh:
            fh.truncate(size)
            fh.seek(size - 4)
            fh.write(b"tail")
        if hole.stat().st_blocks * 512 >= size:
            pytest.skip("filesystem did not create a hole")
        out = tmp_path / "out.tar.zst"
        assert run_script("archive-pack", "-o", str(out), str(source)).returncode == 0
        dest = tmp_path / "restored"
        unpack(out.read_bytes(), dest)
        restored = dest / "sparse" / "hole.bin"
        assert restored.stat().st_size == size
        assert restored.read_bytes()[-4:] == b"tail"
        assert restored.stat().st_blocks * 512 < size


@needs_zstd
class TestArchivePackOutput:
    @pytest.mark.parametrize("flags", [[], ["-S"]])
    def test_small_result_stays_whole(self, tmp_path, flags):
        out = tmp_path / "out.tar.zst"
        result = run_script("archive-pack", *flags, "-o", str(out), str(tree(tmp_path)))
        assert result.returncode == 0, result.stderr
        assert out.is_file()
        assert list(tmp_path.glob("out.tar.zst.part-*")) == []
        assert str(out) in result.stdout

    def test_chunks_numbered_and_reassemble(self, tmp_path):
        source = big_tree(tmp_path)
        out = tmp_path / "out.tar.zst"
        result = run_script("archive-pack", "-s", "4K", "-o", str(out), str(source))
        assert result.returncode == 0, result.stderr
        parts = sorted(tmp_path.glob("out.tar.zst.part-*"))
        assert len(parts) > 1
        assert [p.name for p in parts] == [
            f"out.tar.zst.part-{i:03d}" for i in range(1, len(parts) + 1)
        ]
        assert "part-001" in result.stdout
        assert not out.exists()
        dest = tmp_path / "restored"
        assert unpack(b"".join(p.read_bytes() for p in parts), dest) == [
            "book/random.bin", "book/words.txt"
        ]
        assert (dest / "book" / "random.bin").read_bytes() == (source / "random.bin").read_bytes()

    def test_frame_carries_content_checksum(self, tmp_path):
        out = tmp_path / "out.tar.zst"
        assert run_script("archive-pack", "-o", str(out), str(tree(tmp_path))).returncode == 0
        data = out.read_bytes()
        assert data[:4] == b"\x28\xb5\x2f\xfd"          # zstd magic
        assert data[4] & 0x04                            # content-checksum flag
        assert subprocess.run(["zstd", "--test", str(out)], capture_output=True).returncode == 0

    def test_level_flag_is_honoured(self, tmp_path):
        source = big_tree(tmp_path)
        fast, best = tmp_path / "fast.tar.zst", tmp_path / "best.tar.zst"
        assert run_script("archive-pack", "-l", "1", "-o", str(fast), str(source)).returncode == 0
        assert run_script("archive-pack", "-o", str(best), str(source)).returncode == 0
        assert best.stat().st_size < fast.stat().st_size

    def test_refuses_to_overwrite_whole_output(self, tmp_path):
        out = tmp_path / "out.tar.zst"
        out.write_bytes(b"keep me")
        result = run_script("archive-pack", "-o", str(out), str(tree(tmp_path)))
        assert result.returncode == 2
        assert "refusing to overwrite" in result.stderr
        assert out.read_bytes() == b"keep me"

    def test_refuses_to_overwrite_existing_chunk(self, tmp_path):
        chunk = tmp_path / "out.tar.zst.part-001"
        chunk.write_bytes(b"keep me")
        result = run_script("archive-pack", "-o", str(tmp_path / "out.tar.zst"), str(tree(tmp_path)))
        assert result.returncode == 2
        assert "refusing to overwrite" in result.stderr
        assert chunk.read_bytes() == b"keep me"
        assert list(tmp_path.glob("out.tar.zst*")) == [chunk]


@needs_zstd
class TestArchivePackEncryption:
    @pytest.mark.parametrize(
        "flags,expected",
        [(["-p"], ["-p"]), (["-r", "age1aaa", "-r", "age1bbb"], ["-r", "age1aaa", "-r", "age1bbb"])],
    )
    def test_age_flags_and_temp_cleanup(self, tmp_path, flags, expected):
        out, log = tmp_path / "out.tar.zst.age", tmp_path / "age-args.txt"
        path = restricted_path(tmp_path, "tar", "zstd", "split", "rm", "mv")
        stub_tool(tmp_path, "age", AGE_STUB)
        result = run_script(
            "archive-pack", *flags, "-o", str(out), str(tree(tmp_path)),
            path=path, env={"AGE_STUB_LOG": str(log)},
        )
        assert result.returncode == 0, result.stderr
        logged = log.read_text(encoding="utf-8").splitlines()
        assert logged[: len(expected)] == expected
        assert "-R" not in logged                        # age -R is recipients-file
        assert out.is_file()
        assert not (tmp_path / "out.tar.zst").exists()    # plaintext temp removed

    @needs_age
    def test_encrypted_chunks_round_trip(self, tmp_path):
        identity = tmp_path / "key.txt"
        generated = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True)
        identity.write_text(generated.stdout, encoding="utf-8")
        recipient = next(
            line.split(":", 1)[1].strip()
            for line in generated.stdout.splitlines()
            if line.startswith("# public key:")
        )
        source = big_tree(tmp_path)
        out = tmp_path / "out.tar.zst.age"
        result = run_script(
            "archive-pack", "-r", recipient, "-s", "4K", "-o", str(out), str(source)
        )
        assert result.returncode == 0, result.stderr
        parts = sorted(tmp_path.glob("out.tar.zst.age.part-*"))
        assert len(parts) > 1
        assert not out.exists()
        assert not (tmp_path / "out.tar.zst").exists()
        dest = tmp_path / "restored"
        assert unpack(
            b"".join(p.read_bytes() for p in parts), dest, identity=identity
        ) == ["book/random.bin", "book/words.txt"]
        assert (dest / "book" / "random.bin").read_bytes() == (source / "random.bin").read_bytes()


class TestArchiveVerifySweep:
    @pytest.mark.parametrize("shard", ["5/3", "abc", "0/4", "1/0", "/4"])
    def test_invalid_shard_fails_cleanly(self, tmp_path, shard):
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
            "archive-verify-sweep", str(empty),
            path=restricted_path(tmp_path, "find", "sed", "grep", "sort", "awk"),
        )
        assert result.returncode == 1
        assert "no CAS-linked files" in result.stderr

    def test_missing_parallel_fails_cleanly(self, tmp_path):
        tree_dir = tmp_path / "tree"
        tree_dir.mkdir()
        (tree_dir / "obj").symlink_to("../cas/" + "a" * 64)
        result = run_script(
            "archive-verify-sweep", str(tree_dir),
            path=restricted_path(tmp_path, "find", "sed", "grep", "sort", "awk"),
        )
        assert result.returncode == 2
        assert "missing required tool: GNU parallel" in result.stderr
