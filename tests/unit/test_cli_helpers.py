from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from pkit.common.cli_helpers import (
    EXIT_ERROR,
    EXIT_USAGE,
    emit_result,
    fail,
    read_stdin_payload,
)


@dataclass
class Dummy:
    ok: bool
    message: str


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


def test_fail_default_exit_code(capsys):
    with pytest.raises(SystemExit) as exc:
        fail("bad input")

    assert exc.value.code == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: bad input" in captured.err


def test_fail_usage_exit_code(capsys):
    with pytest.raises(SystemExit) as exc:
        fail("bad input", exit_code=EXIT_USAGE)

    assert exc.value.code == EXIT_USAGE

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: bad input" in captured.err
