"""Run external commands: list args, captured text output, typed failures.

Borrows the ergonomics of sh/execa (capture and check by default) without
their magic: no shell strings, no attribute DSL, no piping language.
"""

import subprocess
from collections.abc import Sequence

__all__ = ["CommandNotFound", "ShellError", "run"]

_STDERR_TAIL = 5


class ShellError(Exception):
    """A command exited non-zero or timed out."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class CommandNotFound(ShellError):
    """The executable is not installed or not on PATH."""


def run(args: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and return its captured output.

    Raises CommandNotFound when the binary is missing, ShellError on non-zero
    exit or timeout. The stderr tail is included in the message so callers can
    surface a useful diagnostic without re-reading the process object.
    """
    if not args:
        raise ShellError("no command given")
    try:
        return subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CommandNotFound(f"command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ShellError(f"command timed out: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        tail = " ".join(stderr.strip().splitlines()[-_STDERR_TAIL:])
        raise ShellError(
            f"{args[0]} exited {exc.returncode}: {tail}",
            returncode=exc.returncode,
            stderr=stderr,
        ) from exc
