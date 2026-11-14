"""BusyBox dispatcher and the three entry-point modes of each tool."""

import runpy
import sys
import tomllib
from pathlib import Path

import pytest

from pkit.cli import main as pkit_main
from pkit.lib.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE
from pkit.version import get_version

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_version_matches_pyproject() -> None:
    assert get_version() == _pyproject_version()


def test_wayback_exports() -> None:
    from pkit import wayback

    for name in wayback.__all__:
        assert hasattr(wayback, name)


def test_help_lists_every_tool(capsys) -> None:
    assert pkit_main(["--help"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "archive" in out
    assert "meta" in out
    assert "wayback" in out


def test_no_args_is_help(capsys) -> None:
    assert pkit_main([]) == EXIT_USAGE
    out = capsys.readouterr().out
    assert "usage: pkit" in out
    assert "wayback" in out


def test_version(capsys) -> None:
    assert pkit_main(["--version"]) == EXIT_OK
    assert get_version() in capsys.readouterr().out


def test_unknown_command_is_usage_error(capsys) -> None:
    assert pkit_main(["bogus"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "unknown command" in err
    assert "bogus" in err


def test_dispatches_to_tool_main(monkeypatch, capsys) -> None:
    seen = []

    def fake_main(argv):
        seen.append(list(argv))
        return EXIT_ERROR

    monkeypatch.setattr("pkit.wayback.cli.main", fake_main)
    assert pkit_main(["wayback", "--lock-file", "", "save", "u"]) == EXIT_ERROR
    assert seen == [["--lock-file", "", "save", "u"]]


def test_wayback_help_through_dispatcher(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        pkit_main(["wayback", "--help"])

    assert exc.value.code == EXIT_OK
    assert "save" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("module", "args"),
    [
        ("pkit", ["--version"]),
        ("pkit.meta", []),
        ("pkit.wayback", []),
        ("pkit.archive", []),
    ],
)
def test_python_dash_m_entry_points(module: str, args: list[str]) -> None:
    """`python -m pkit[.tool]` runs the tool CLI through __main__.py."""
    with pytest.raises(SystemExit) as exc, pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", [module, *args])
        runpy.run_module(module, run_name="__main__")

    expected = EXIT_OK if args else EXIT_USAGE
    assert exc.value.code == expected
