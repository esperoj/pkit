"""Tests for pkit.lib.aria2c: command construction, progress parsing, errors.

The aria2c binary is faked at the process boundary (subprocess.Popen), so
no aria2c is needed — same rule as the rclone fake: only the binaries pkit
does not own are faked.
"""

import io
from collections.abc import Callable
from pathlib import Path

import pytest

from pkit.lib import Progress, aria2c
from pkit.lib.shell import CommandNotFound, ShellError

SUMMARY = "[#2080b3 12MiB/20MiB(60%) CN:4 DL:2.5MiB ETA:3s]"


class FakeAria2c:
    """subprocess.Popen stand-in with a scripted stdout and exit code."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self.stdout = io.BytesIO(stdout)
        self.returncode = returncode
        self.args: list[str] | None = None
        self.killed = False

    def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeAria2c]:
    """One programmed FakeAria2c per launch: stdout, exit code, seen args."""

    def _launch(stdout: bytes = b"", returncode: int = 0) -> FakeAria2c:
        process = FakeAria2c(stdout, returncode)

        def fake_popen(args, **kwargs):
            process.args = list(args)
            return process

        monkeypatch.setattr(aria2c.subprocess, "Popen", fake_popen)
        return process

    return _launch


@pytest.mark.parametrize(
    "line",
    [
        SUMMARY,
        "[#a 30MiB/100MiB(30%) CN:2 DL:5MiB ETA:14s]",
        "[#x 0B/0B CN:1 DL:0B ETA:0s]",
        "[#gid 1.2GiB/2.0GiB(60%) CN:8 DL:5.5MiB ETA:1m20s]",
        "[#gid 512KiB/1.0MiB(50%) CN:1 DL:1KiB]",  # no ETA yet
        "[#gid 0B/0B CN:1 DL:0B]",  # size unknown: total becomes None
    ],
)
def test_parse_progress_accepts_summary_lines(line: str):
    assert aria2c.parse_progress(line) is not None


@pytest.mark.parametrize(
    "line",
    ["(OK): download completed.", "header junk", "", "[#abc no-sizes CN:1]"],
)
def test_parse_progress_skips_other_lines(line: str):
    assert aria2c.parse_progress(line) is None


def test_parse_progress_values():
    sample = aria2c.parse_progress(SUMMARY)

    assert sample == Progress(received=12 * 1024**2, total=20 * 1024**2, rate=2.5 * 1024**2, eta=3)


def test_parse_progress_eta_forms():
    minutes = aria2c.parse_progress("[#g 1MiB/2MiB(50%) CN:1 DL:1MiB ETA:1m20s]")
    hours = aria2c.parse_progress("[#g 1MiB/2MiB(50%) CN:1 DL:1MiB ETA:2h4m1s]")

    assert minutes is not None and minutes.eta == 80
    assert hours is not None and hours.eta == 7441


def test_available_false_when_not_on_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aria2c, "which", lambda name: None)

    assert aria2c.available() is False


def test_available_true_when_on_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(aria2c, "which", lambda name: f"/bin/{name}")

    assert aria2c.available() is True


def test_download_builds_the_command(tmp_path: Path, popen):
    process = popen()

    aria2c.download(
        "https://example.com/thing.bin",
        tmp_path / "out" / "thing.bin",
        headers={"Accept": "application/json"},
        user_agent="pkit/9",
        timeout=30.0,
    )

    args = process.args
    assert args[0] == "aria2c"
    assert "--continue=true" in args
    assert "--allow-overwrite=true" in args
    assert "--auto-file-renaming=false" in args
    assert f"--dir={tmp_path / 'out'}" in args
    assert "--out=thing.bin" in args
    assert "--header=Accept: application/json" in args
    assert "--user-agent=pkit/9" in args
    assert "--timeout=30" in args
    assert args[-1] == "https://example.com/thing.bin"
    assert (tmp_path / "out").is_dir()


def test_download_adds_checksum_and_connections(tmp_path: Path, popen):
    process = popen()

    aria2c.download(
        "https://example.com/f",
        tmp_path / "f",
        checksum="a" * 64,
        connections=4,
    )

    assert f"--checksum=sha-256={'a' * 64}" in process.args
    assert "--split=4" in process.args
    assert "--max-connection-per-server=4" in process.args


def test_download_reports_progress_from_summary_lines(tmp_path: Path, popen):
    popen((SUMMARY + "\r" + SUMMARY + "\r").encode())

    samples: list[Progress] = []
    aria2c.download("https://example.com/f", tmp_path / "f", progress=samples.append)

    assert len(samples) == 2
    assert samples[0].received == 12 * 1024**2


def test_download_without_progress_still_drains_stdout(tmp_path: Path, popen):
    popen(((SUMMARY + "\r") * 100).encode())

    assert aria2c.download("https://example.com/f", tmp_path / "f") == tmp_path / "f"


def test_download_maps_nonzero_exit_to_shell_error(tmp_path: Path, popen):
    popen(b"", returncode=1)

    with pytest.raises(ShellError) as exc:
        aria2c.download("https://example.com/f", tmp_path / "f")

    assert exc.value.returncode == 1
    assert "aria2c exited 1" in str(exc.value)


def test_download_propagates_command_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def raise_not_found(args, **kwargs):
        raise FileNotFoundError("No such file or directory: 'aria2c'")

    monkeypatch.setattr(aria2c.subprocess, "Popen", raise_not_found)

    with pytest.raises(CommandNotFound):
        aria2c.download("https://example.com/f", tmp_path / "f")


def test_parse_progress_unknown_eta_stays_none():
    sample = aria2c.parse_progress("[#g 1MiB/2MiB(50%) CN:1 DL:1MiB ETA:soon]")

    assert sample is not None and sample.eta is None


def test_download_without_trailing_separator_reports_final_buffer(tmp_path: Path, popen):
    popen(SUMMARY.encode())  # no \r or \n at the end

    samples: list[Progress] = []
    aria2c.download("https://example.com/f", tmp_path / "f", progress=samples.append)

    assert len(samples) == 1
    assert samples[0].received == 12 * 1024**2


def test_download_kills_the_process_when_the_callback_raises(tmp_path: Path, popen):
    process = popen(SUMMARY.encode())

    def explode(progress: Progress) -> None:
        raise RuntimeError("callback blew up")

    with pytest.raises(RuntimeError, match="callback blew up"):
        aria2c.download("https://example.com/f", tmp_path / "f", progress=explode)

    assert process.killed


def test_download_without_timeout_omits_the_flag(tmp_path: Path, popen):
    process = popen()

    aria2c.download("https://example.com/f", tmp_path / "f", timeout=None)

    assert not any(arg.startswith("--timeout") for arg in process.args)


def test_download_skips_unparsable_fragments(tmp_path: Path, popen):
    # junk between separators is skipped; a trailing fragment is too.
    popen(("junk\r" + SUMMARY + "\r" + "not-a-line").encode())

    samples: list[Progress] = []
    aria2c.download("https://example.com/f", tmp_path / "f", progress=samples.append)

    assert samples == [
        Progress(received=12 * 1024**2, total=20 * 1024**2, rate=2.5 * 1024**2, eta=3)
    ]
