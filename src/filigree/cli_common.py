"""Shared CLI helpers extracted from cli.py.

Provides ``get_db()`` and ``refresh_summary()`` so that both the main
``cli.py`` and future ``cli_commands/*.py`` subpackages can access them
without circular imports.
"""

from __future__ import annotations

import json as json_mod
import logging
import sqlite3
import sys

import click

from filigree.core import (
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
    ProjectNotInitialisedError,
    find_filigree_anchor,
    find_filigree_root,
)
from filigree.summary import write_summary
from filigree.types.api import ErrorCode, SchemaVersionMismatchError

logger = logging.getLogger(__name__)


def _wants_json() -> bool:
    """Return True when the active CLI invocation passed ``--json``.

    The root group stashes the literal argv list in ``ctx.meta`` from
    ``_FiligreeGroup.parse_args`` so shared startup helpers can honour the
    2.0 flat envelope contract before the subcommand callback runs.

    Tokens after Click's ``--`` option terminator are positional values,
    not flags — e.g. ``filigree create -- --json`` makes the issue title
    literally ``"--json"``, with no real JSON-mode flag. We slice at the
    first ``--`` so a positional that happens to spell ``--json`` does
    not flip startup errors into the JSON envelope. (filigree-df988a37fc)
    """
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return False
    raw_args = ctx.find_root().meta.get("filigree_raw_args", [])
    end = raw_args.index("--") if "--" in raw_args else len(raw_args)
    return "--json" in raw_args[:end]


def _emit_startup_failure(exc: Exception, code: ErrorCode, *, human_prefix: str = "") -> None:
    """Render a ``get_db`` failure as JSON envelope (--json) or plain stderr."""
    if _wants_json():
        click.echo(json_mod.dumps({"error": str(exc), "code": code}))
    else:
        click.echo(f"{human_prefix}{exc}" if human_prefix else str(exc), err=True)


def get_db() -> FiligreeDB:
    """Discover the project anchor and return an initialized FiligreeDB.

    Uses :func:`find_filigree_anchor` so legacy installs (no ``.filigree.conf``
    yet) still open without requiring write access — the conf is only created
    by explicit init/install paths, not by discovery.

    Surfaces corrupt-conf / unreadable-DB / schema-mismatch failures as clean
    ``ClickException``-style exits (stderr + exit 1), or — when the active
    invocation passed ``--json`` — as the 2.0 flat envelope on stdout, rather
    than letting raw ValueError / OSError / sqlite3.Error / TypeError /
    KeyError tracebacks escape from every command. ``TypeError`` and
    ``KeyError`` cover malformed-but-JSON-valid configs (e.g. non-string
    ``db``, non-list ``enabled_packs``, missing required keys) — see GH PR
    #33 review. ``SchemaVersionMismatchError`` is a ``ValueError`` subclass
    and so must be caught before the broader ``ValueError`` arm to map to
    its own ``SCHEMA_MISMATCH`` code.
    """
    try:
        project_root, conf_path = find_filigree_anchor()
    except ProjectNotInitialisedError as exc:
        _emit_startup_failure(exc, ErrorCode.NOT_INITIALIZED)
        sys.exit(1)
    try:
        if conf_path is not None:
            return FiligreeDB.from_conf(conf_path)
        return FiligreeDB.from_filigree_dir(project_root / FILIGREE_DIR_NAME)
    except SchemaVersionMismatchError as exc:
        _emit_startup_failure(exc, ErrorCode.SCHEMA_MISMATCH, human_prefix="Error opening project database: ")
        sys.exit(1)
    except (OSError, sqlite3.Error) as exc:
        _emit_startup_failure(exc, ErrorCode.IO, human_prefix="Error opening project database: ")
        sys.exit(1)
    except (ValueError, TypeError, KeyError) as exc:
        _emit_startup_failure(exc, ErrorCode.VALIDATION, human_prefix="Error opening project database: ")
        sys.exit(1)


def refresh_summary(db: FiligreeDB) -> None:
    """Regenerate context.md after mutations.

    Best-effort: the mutation has already committed by the time we're called,
    so a summary-write failure (disk full, permission, missing dir) must not
    turn a successful command into a non-zero exit. Log and continue.
    """
    try:
        filigree_dir = find_filigree_root()
        write_summary(db, filigree_dir / SUMMARY_FILENAME)
    except FileNotFoundError:
        pass  # No .filigree/ dir — skip summary
    except OSError as exc:
        logger.warning("Failed to refresh context.md summary: %s", exc)
    except Exception:
        logger.warning("Unexpected error refreshing context.md summary", exc_info=True)
