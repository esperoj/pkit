from __future__ import annotations

import sys
from collections.abc import Callable, Generator

import pytest

from pkit.wayback.client import WaybackClient


class FakeStdin:
    """Minimal fake stdin for direct CLI tests."""

    def __init__(self, text: str = "", *, is_tty: bool = False) -> None:
        self._text = text
        self._is_tty = is_tty

    def read(self) -> str:
        return self._text

    def isatty(self) -> bool:
        return self._is_tty


@pytest.fixture
def fake_stdin(monkeypatch):
    """Patch sys.stdin for helper-level tests."""

    def _set(text: str, *, is_tty: bool = False) -> None:
        monkeypatch.setattr(sys, "stdin", FakeStdin(text, is_tty=is_tty))

    return _set


@pytest.fixture
def run_cli(monkeypatch, capsys, fake_stdin):
    """Call a CLI main() with faked stdin; return (exit_code, stdout, stderr).

    SystemExit (argparse --help/--version/usage errors) is folded into the
    returned code, mirroring what a console script would exit with.
    """

    def _run(
        main: Callable[..., int],
        args: list[str],
        *,
        input: str = "",
    ) -> tuple[int, str, str]:
        fake_stdin(input)
        try:
            code = main(list(args))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def client() -> Generator[WaybackClient]:
    """SDK client with lock disabled for unit tests."""
    c = WaybackClient(
        api_key="test-key",
        api_secret="test-secret",
        lock_file="",
        timeout=0.1,
    )
    yield c
    c.close()


@pytest.fixture
def disable_sleep(monkeypatch):
    """Disable real sleeps in the Wayback client."""
    monkeypatch.setattr("pkit.wayback.client.time.sleep", lambda _seconds: None)


@pytest.fixture
def patch_save_url(monkeypatch):
    """Patch WaybackClient.save_url for CLI unit tests.

    Usage:
        calls = patch_save_url(lambda url, opts: SaveResult(...))
        calls = patch_save_url(lambda url, opts: (_ for _ in ()).throw(SomeError()))
    """

    def _patch(result_factory):
        calls = []

        def fake_save_url(self, url, *, opts=None):
            calls.append({"url": url, "opts": opts})
            return result_factory(url, opts)

        monkeypatch.setattr(
            "pkit.wayback.cli.WaybackClient.save_url",
            fake_save_url,
        )
        return calls

    return _patch
