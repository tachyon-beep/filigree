"""CLI for the filigree issue tracker.

Convention-based: discovers .filigree/ by walking up from cwd.
Commands are defined in cli_commands/ subpackage modules.
"""

from __future__ import annotations

import click

from filigree import __version__
from filigree.cli_commands import admin, issues, meta, planning, server, workflow
from filigree.validation import sanitize_actor


@click.group()
@click.version_option(version=__version__, prog_name="filigree")
@click.option("--actor", default="cli", help="Actor identity for audit trail (default: cli)")
@click.pass_context
def cli(ctx: click.Context, actor: str) -> None:
    """Filigree — agent-native issue tracker."""
    ctx.ensure_object(dict)
    cleaned, err = sanitize_actor(actor)
    if err:
        raise click.BadParameter(err, param_hint="'--actor'")
    ctx.obj["actor"] = cleaned


# Register domain command modules
for _mod in (issues, planning, meta, workflow, admin, server):
    _mod.register(cli)


if __name__ == "__main__":
    cli()
