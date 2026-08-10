"""Package-level behavior: version source and BusyBox CLI mounting."""

from __future__ import annotations

import runpy
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from pkit.cli import cli as pkit_cli
from pkit.common.cli_helpers import EXIT_USAGE
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


def test_pkit_help_lists_wayback(runner) -> None:
    result = runner.invoke(pkit_cli, ["--help"])
    assert result.exit_code == 0
    assert "wayback" in result.stdout


def test_pkit_version(runner) -> None:
    result = runner.invoke(pkit_cli, ["--version"])
    assert result.exit_code == 0
    assert get_version() in result.stdout


def test_pkit_no_args_is_help(runner) -> None:
    result = runner.invoke(pkit_cli, [])
    assert result.exit_code == EXIT_USAGE
    assert "wayback" in (result.stdout + result.stderr)


def test_pkit_can_mount_wayback_help(runner) -> None:
    result = runner.invoke(pkit_cli, ["wayback", "--help"])
    assert result.exit_code == 0
    assert "save" in result.stdout


def test_pkit_module_main_entrypoint() -> None:
    """`python -m pkit.cli` runs the CLI through the __main__ guard."""
    # Remove from sys.modules to force a clean execution by runpy.
    # This avoids the "found in sys.modules" RuntimeWarning and ensures
    # coverage.py traces the top-level `if __name__ == "__main__":` block.
    sys.modules.pop("pkit.cli", None)

    with patch.object(sys, "argv", ["pkit", "--version"]):
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("pkit.cli", run_name="__main__")
        assert exc.value.code == 0
