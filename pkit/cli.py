"""pkit: BusyBox-style CLI for multiple personal tools."""

from __future__ import annotations

import click

from .version import get_version
from .wayback.cli import cli as wayback_cli


@click.group(
    name="pkit",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=get_version())
def cli() -> None:
    """pkit: collection of archival and preservation utilities."""


# Register subcommands at module level so they are available to Click's
# help formatter before the group callback is ever executed.
cli.add_command(wayback_cli, name="wayback")


if __name__ == "__main__":
    cli()
