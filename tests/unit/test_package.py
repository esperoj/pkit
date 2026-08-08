import pkit
from pkit.cli import cli as pkit_cli
from pkit.common.cli_helpers import EXIT_USAGE


def test_version() -> None:
    assert pkit.__version__ == "0.2.0"


def test_wayback_exports() -> None:
    from pkit import wayback

    for name in wayback.__all__:
        assert hasattr(wayback, name)


def test_pkit_help_lists_wayback(runner) -> None:
    result = runner.invoke(pkit_cli, ["--help"])

    assert result.exit_code == 0
    assert "wayback" in result.stdout


def test_pkit_no_args_is_help(runner) -> None:
    result = runner.invoke(pkit_cli, [])

    assert result.exit_code == EXIT_USAGE
    assert "wayback" in (result.stdout + result.stderr)


def test_pkit_can_mount_wayback_help(runner) -> None:
    result = runner.invoke(pkit_cli, ["wayback", "--help"])

    assert result.exit_code == 0
    assert "save" in result.stdout
