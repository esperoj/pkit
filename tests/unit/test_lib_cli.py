"""lib.cli: exit codes, stdin line, emit, progress, and the one main-loop."""

import argparse
import io
import json
import sys
from dataclasses import dataclass

import pytest

from pkit.lib import Progress
from pkit.lib.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    emit,
    end_progress,
    fail,
    format_progress,
    progress_reporter,
    read_stdin_line,
    run_tool,
)


@dataclass
class Dummy:
    ok: bool
    message: str


def _parser_with_command(handler) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tool")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("cmd")
    cmd.set_defaults(func=handler)
    return parser


def test_read_stdin_line_tty(fake_stdin):
    fake_stdin("ignored", is_tty=True)

    assert read_stdin_line() is None


def test_read_stdin_line_first_non_empty(fake_stdin):
    fake_stdin("\n   \nhttp://one\nhttp://two\n")

    assert read_stdin_line() == "http://one"


def test_read_stdin_line_blank(fake_stdin):
    fake_stdin("   \n")

    assert read_stdin_line() is None


def test_emit_json_dataclass(capsys):
    emit(Dummy(True, "done"), "ignored", json_output=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "message": "done"}


def test_emit_json_non_dataclass(capsys):
    emit({"a": 1}, "ignored", json_output=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"a": 1}


def test_emit_json_sequence_of_dataclasses(capsys):
    emit([Dummy(True, "a"), Dummy(False, "b")], "ignored", json_output=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"ok": True, "message": "a"}, {"ok": False, "message": "b"}]


def test_emit_plain(capsys):
    emit(Dummy(True, "done"), "done", json_output=False)

    assert capsys.readouterr().out == "done\n"


def test_emit_plain_no_text_prints_nothing(capsys):
    emit(Dummy(True, "done"), None, json_output=False)
    emit(Dummy(True, "done"), "", json_output=False)

    assert capsys.readouterr().out == ""


def test_fail_returns_exit_code(capsys):
    assert fail("bad input") == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: bad input" in captured.err


def test_fail_usage_exit_code(capsys):
    assert fail("bad input", exit_code=EXIT_USAGE) == EXIT_USAGE

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: bad input" in captured.err


def test_run_tool_no_args_prints_help_and_returns_usage(capsys):
    parser = _parser_with_command(lambda args: 0)

    assert run_tool(parser, []) == EXIT_USAGE
    assert "usage: tool" in capsys.readouterr().out


def test_run_tool_dispatches(capsys):
    parser = _parser_with_command(lambda args: EXIT_OK)

    assert run_tool(parser, ["cmd"]) == EXIT_OK


def test_run_tool_maps_unexpected_errors_to_one(capsys):
    def handler(args):
        raise RuntimeError("boom")

    parser = _parser_with_command(handler)

    assert run_tool(parser, ["cmd"]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: boom" in captured.err


def test_run_tool_uses_sys_argv_by_default(capsys, monkeypatch):
    parser = _parser_with_command(lambda args: EXIT_OK)
    monkeypatch.setattr(sys, "argv", ["pkit-tool", "cmd"])

    assert run_tool(parser) == EXIT_OK


def test_run_tool_usage_error_exits_two():
    parser = _parser_with_command(lambda args: EXIT_OK)

    with pytest.raises(SystemExit) as exc:
        run_tool(parser, ["nope"])

    assert exc.value.code == EXIT_USAGE


# --- progress formatting (shared by every download-facing CLI) ---------------


def test_format_progress_compact_line():
    assert format_progress(Progress(12 * 1024**2, 20 * 1024**2, 2.5 * 1024**2, 3)) == (
        "12MiB/20MiB 60% 2.5MiB/s ETA:3s"
    )


def test_format_progress_unknown_total_and_rate():
    assert format_progress(Progress(512)) == "512B/? ? 0B/s"


def test_format_progress_minute_eta():
    assert format_progress(Progress(1 * 1024**2, 2 * 1024**2, 1 * 1024**2, 80)).endswith(
        "ETA:1m20s"
    )


def test_format_progress_hour_eta():
    assert format_progress(Progress(1, 2, 1, 7441)).endswith("ETA:2h4m1s")


def test_progress_reporter_off_when_quiet(monkeypatch):
    tty = io.StringIO()
    tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stderr", tty)

    assert progress_reporter(quiet=True) is None


def test_progress_reporter_off_when_stderr_is_not_a_tty(monkeypatch):
    pipe = io.StringIO()
    monkeypatch.setattr(sys, "stderr", pipe)

    assert progress_reporter(quiet=False) is None


def test_progress_reporter_writes_stderr_lines(monkeypatch):
    tty = io.StringIO()
    tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stderr", tty)

    reporter = progress_reporter(quiet=False)
    assert reporter is not None
    reporter(Progress(512, 1024, 256, 2))
    end_progress(reporter)

    text = tty.getvalue()
    assert text.startswith("512B/1KiB 50% 256B/s ETA:2s\r")
    assert text.endswith("\n")


def test_end_progress_without_reporter_is_a_no_op():
    end_progress(None)


def test_format_progress_terabyte_scale():
    assert format_progress(Progress(2 * 1024**4)).startswith("2TiB")
