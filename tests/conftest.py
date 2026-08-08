from __future__ import annotations

import inspect
import sys

import pytest
from click.testing import CliRunner

from pkit.wayback.client import WaybackClient


class FakeStdin:
    """Minimal fake stdin for direct helper tests."""

    def __init__(self, text: str = "", *, is_tty: bool = False) -> None:
        self._text = text
        self._is_tty = is_tty

    def read(self) -> str:
        return self._text

    def isatty(self) -> bool:
        return self._is_tty


def _cli_runner_kwargs() -> dict[str, bool]:
    """Return CliRunner kwargs for stdout/stderr separation across Click versions.

    Older Click versions accept:

        CliRunner(mix_stderr=False)

    Newer Click versions removed `mix_stderr` and separate stdout/stderr by
    default.
    """
    params = inspect.signature(CliRunner.__init__).parameters

    if "mix_stderr" in params:
        return {"mix_stderr": False}

    return {}


@pytest.fixture
def fake_stdin(monkeypatch):
    """Patch sys.stdin for helper-level tests."""

    def _set(text: str, *, is_tty: bool = False) -> None:
        monkeypatch.setattr(sys, "stdin", FakeStdin(text, is_tty=is_tty))

    return _set


@pytest.fixture
def runner() -> CliRunner:
    """Click CLI runner with separated stdout/stderr."""
    return CliRunner(**_cli_runner_kwargs())


@pytest.fixture
def client() -> WaybackClient:
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
