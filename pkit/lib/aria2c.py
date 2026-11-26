"""General aria2c CLI wrapper: resumable downloads with progress reporting.

aria2c owns connections, chunking, retries and resume; this module owns the
command construction, the summary lines it prints, and the typed failure —
shell.ShellError with aria2c's exit code and stderr tail, exactly like
pkit.lib.shell for any other external command.

Progress is best-effort: a summary line that does not parse is skipped
silently, and a download is only ever proven by the caller's own hash check.
Summary lines are read live (--summary-interval=1) so a progress callback
follows the real transfer, not a guess.

Used through pkit.lib.http's downloader argument; importable directly:

    from pkit.lib import aria2c

    aria2c.download(url, dest, progress=print)
"""

import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from shutil import which

from pkit.lib import Progress
from pkit.lib.shell import CommandNotFound, ShellError

__all__ = ["ARIA2C", "available", "download", "parse_progress"]

ARIA2C = "aria2c"
_STDERR_TAIL = 5

# One size token: "0B", "512KiB", "30MiB", "1.2GiB" (aria2c prints binary units).
_SIZE = r"(\d+(?:\.\d+)?)\s?([KMGT]?i?B)"
# One summary line: "[#gid 12MiB/20MiB(60%) CN:4 DL:2.5MiB ETA:3s]". The
# percent and ETA show only once the total and rate are known.
_SUMMARY_RE = re.compile(
    rf"\[[#\w]*\s+{_SIZE}/{_SIZE}(?:\s*\((\d+)%\))?"  # received/total/percent
    rf"[^\]]*?DL:{_SIZE}"  # rate
    rf"(?:\s+ETA:(\S+))?\]"  # eta, e.g. "3s", "1m20s"
)
_ETA_RE = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(\d+)s\Z")
_POWERS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def available() -> bool:
    """True when the aria2c binary is on PATH."""
    return which(ARIA2C) is not None


def _bytes(value: str, unit: str) -> float:
    return float(value) * _POWERS[unit[0]]


def _seconds(text: str) -> float | None:
    """aria2c ETA forms ("3s", "1m20s", "2h4m1s") as seconds; None otherwise."""
    match = _ETA_RE.fullmatch(text)
    if match is None:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def parse_progress(line: str) -> Progress | None:
    """One summary line as a Progress sample, or None when it is not one.

    Public for tests and for anyone parsing saved aria2c output.
    """
    match = _SUMMARY_RE.search(line)
    if match is None:
        return None
    received_value, received_unit, total_value, total_unit, _percent, rate_value, rate_unit, eta = (
        match.groups()
    )
    received = _bytes(received_value, received_unit)
    total = _bytes(total_value, total_unit)
    seconds = _seconds(eta) if eta else None
    return Progress(
        received=int(received),
        total=int(total) or None,
        rate=_bytes(rate_value, rate_unit),
        eta=seconds,
    )


def download(
    url: str,
    dest: str | Path,
    *,
    headers: Mapping[str, str] | None = None,
    user_agent: str | None = None,
    checksum: str | None = None,
    connections: int = 1,
    timeout: float | None = 60.0,
    progress: Callable[[Progress], None] | None = None,
) -> Path:
    """Download url to dest with aria2c: resumable, checksummable, reported.

    --continue=true makes a rerun resume from the .aria2 control file beside
    dest, so an interrupted download finishes instead of restarting. A failed
    download leaves dest and its control file in place for that retry.
    connections is --split/--max-connection-per-server; 1 (the default) keeps
    the transfer single-connection and polite. timeout is aria2c's own
    per-operation network timeout, passed through; there is deliberately no
    whole-process timeout — a long download is not a failure.

    Raises shell.ShellError (exit code and stderr tail) when aria2c fails.
    """
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        ARIA2C,
        f"--dir={path.parent}",
        f"--out={path.name}",
        "--continue=true",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--console-log-level=warn",
        "--summary-interval=1",
        "--download-result=hide",
    ]
    if user_agent:
        args.append(f"--user-agent={user_agent}")
    for key, value in (headers or {}).items():
        args.append(f"--header={key}: {value}")
    if checksum:
        args.append(f"--checksum=sha-256={checksum}")
    if connections > 1:
        args.append(f"--split={connections}")
        args.append(f"--max-connection-per-server={connections}")
    if timeout is not None:
        args.append(f"--timeout={timeout:g}")
    args.append(url)
    _run(args, progress)
    return path


def _run(args: list[str], progress: Callable[[Progress], None] | None) -> None:
    """Run aria2c, reporting progress, and raise ShellError on failure.

    stderr goes to a file (warn level keeps it tiny): a piped-but-undrained
    stderr could block aria2c mid-download, and stdout must be drained to EOF
    anyway — with --summary-interval=1 it carries more than a pipe's worth
    over a long transfer.
    """
    tail = ""
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errors:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors)
            try:
                _report(process.stdout, progress)
                code = process.wait()
            except BaseException:
                process.kill()
                process.wait()
                raise
            if code != 0:
                errors.seek(0)
                tail = " ".join(errors.read().strip().splitlines()[-_STDERR_TAIL:])
    except FileNotFoundError as exc:
        raise CommandNotFound(f"command not found: {ARIA2C}") from exc
    if code != 0:
        raise ShellError(f"aria2c exited {code}: {tail}", returncode=code)


def _report(stream, progress: Callable[[Progress], None] | None) -> None:
    """Drain stdout, feeding parsed summary lines to the callback.

    Progress lines are separated by \r or \n depending on the terminal state,
    so both split the stream; unparsable fragments are skipped.
    """
    assert stream is not None  # stdout=PIPE is always a real pipe here
    buffer = ""
    while True:
        chunk = stream.read(4096)
        if not chunk:
            break
        if progress is None:
            continue
        buffer += chunk.decode("utf-8", "replace")
        parts = re.split(r"[\r\n]", buffer)
        buffer = parts.pop()
        for part in parts:
            sample = parse_progress(part)
            if sample is not None:
                progress(sample)
    if progress is not None:
        sample = parse_progress(buffer)
        if sample is not None:
            progress(sample)
