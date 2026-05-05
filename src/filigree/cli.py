"""CLI for the filigree issue tracker.

Convention-based: discovers .filigree/ by walking up from cwd.
Commands are defined in cli_commands/ subpackage modules.
"""

from __future__ import annotations

import json as json_mod

import click

from filigree import __version__
from filigree.cli_commands import admin, files, issues, meta, observations, planning, scanners, server, workflow
from filigree.cli_common import _wants_json
from filigree.types.api import ErrorCode
from filigree.validation import sanitize_actor


class _FiligreeGroup(click.Group):
    """Click Group that stashes the raw invocation args for downstream use.

    Stage 2B task 2b.3b: the group-level ``--actor`` callback needs to
    detect whether the caller also passed ``--json`` on the subcommand
    so a validation failure can surface as the 2.0 flat envelope rather
    than Click's stderr usage error. By group-callback time,
    ``ctx.args``/``ctx.protected_args`` are empty and ``sys.argv`` is
    untouched by ``CliRunner``; the only reliable way to see the raw
    invocation is to capture it during ``parse_args`` (which runs
    before the callback) and stash it in ``ctx.meta``.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        ctx.meta["filigree_raw_args"] = list(args)
        return super().parse_args(ctx, args)


@click.group(cls=_FiligreeGroup)
@click.version_option(version=__version__, prog_name="filigree")
@click.option("--actor", default="cli", help="Actor identity for audit trail (default: cli)")
@click.pass_context
def cli(ctx: click.Context, actor: str) -> None:
    """Filigree — agent-native issue tracker."""
    ctx.ensure_object(dict)
    cleaned, err = sanitize_actor(actor)
    if err:
        # Stage 2B task 2b.3b: when the caller is running a subcommand
        # with ``--json``, emit the 2.0 envelope instead of Click's
        # stderr usage error. Detection delegates to ``_wants_json``
        # (cli_common) so both the group-level actor check and the
        # ``get_db()`` startup-failure path agree on what counts as a
        # JSON-mode invocation — including ignoring tokens after Click's
        # ``--`` option terminator. (filigree-df988a37fc)
        if _wants_json():
            click.echo(json_mod.dumps({"error": err, "code": ErrorCode.VALIDATION}))
            ctx.exit(1)
        raise click.BadParameter(err, param_hint="'--actor'")
    ctx.obj["actor"] = cleaned


# Register domain command modules
for _mod in (issues, planning, meta, workflow, admin, server, observations, files, scanners):
    _mod.register(cli)


if __name__ == "__main__":
    cli()
