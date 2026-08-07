"""pkit: BusyBox-style CLI for multiple personal tools."""

from __future__ import annotations

import click

from . import __version__
from .wayback.cli import cli as wayback_cli


@click.group(
    name="pkit",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=__version__)
def cli() -> None:
    """pkit: collection of archival and preservation utilities."""


# Mount the Wayback Machine CLI as:
#
#   pkit wayback ...
#
# The same Wayback group can also be exposed standalone as:
#
#   wayback ...
#
cli.add_command(wayback_cli, name="wayback")


if __name__ == "__main__":
    cli()
