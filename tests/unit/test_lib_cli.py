import argparse
import json
import sys
from dataclasses import dataclass

import pytest

from pkit.lib.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    emit_result,
    fail,
    read_stdin_payload,
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


def test_read_stdin_payload_tty(fake_stdin):
    fake_stdin("ignored", is_tty=True)

    assert read_stdin_payload(False) == (None, {})


def test_read_stdin_payload_plain_first_line(fake_stdin):
    fake_stdin("http://one\nhttp://two\n")

    assert read_stdin_payload(False) == ("http://one", {})


def test_read_stdin_payload_plain_blank(fake_stdin):
    fake_stdin("   \n")

    assert read_stdin_payload(False) == (None, {})


def test_read_stdin_payload_json_object(fake_stdin):
    fake_stdin('{"url": 7, "capture_all": true}')

    url, payload = read_stdin_payload(True)

    assert url == "7"
    assert payload == {"url": 7, "capture_all": True}


def test_read_stdin_payload_json_object_missing_primary(fake_stdin):
    fake_stdin('{"foo": 1}')

    url, payload = read_stdin_payload(True)

    assert url is None
    assert payload == {"foo": 1}


def test_read_stdin_payload_json_scalar(fake_stdin):
    fake_stdin('"http://example.com"')

    assert read_stdin_payload(True) == ("http://example.com", {})


def test_read_stdin_payload_json_invalid(fake_stdin):
    fake_stdin("http://raw")

    assert read_stdin_payload(True) == ("http://raw", {})


def test_emit_result_json_dataclass(capsys):
    emit_result(
        Dummy(True, "done"),
        json_output=True,
        plain=lambda result: None,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "message": "done"}


def test_emit_result_json_non_dataclass(capsys):
    emit_result(
        {"a": 1},
        json_output=True,
        plain=lambda result: None,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"a": 1}


def test_emit_result_plain(capsys):
    emit_result(
        Dummy(True, "done"),
        json_output=False,
        plain=lambda result: result.message,
    )

    assert capsys.readouterr().out == "done\n"


def test_emit_result_plain_none(capsys):
    emit_result(
        Dummy(True, "done"),
        json_output=False,
        plain=lambda result: None,
    )

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
