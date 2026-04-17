"""Shared CLI helpers extracted from cli.py.

Provides ``get_db()`` and ``refresh_summary()`` so that both the main
``cli.py`` and future ``cli_commands/*.py`` subpackages can access them
without circular imports.
"""

from __future__ import annotations

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

logger = logging.getLogger(__name__)


def get_db() -> FiligreeDB:
    """Discover the project anchor and return an initialized FiligreeDB.

    Uses :func:`find_filigree_anchor` so legacy installs (no ``.filigree.conf``
    yet) still open without requiring write access — the conf is only created
    by explicit init/install paths, not by discovery.

    Surfaces corrupt-conf / unreadable-DB / schema-mismatch failures as clean
    ``ClickException``-style exits (stderr + exit 1) rather than letting raw
    ValueError / OSError / sqlite3.Error tracebacks escape from every command.
    """
    try:
        project_root, conf_path = find_filigree_anchor()
    except ProjectNotInitialisedError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    try:
        if conf_path is not None:
            return FiligreeDB.from_conf(conf_path)
        return FiligreeDB.from_filigree_dir(project_root / FILIGREE_DIR_NAME)
    except (ValueError, OSError, sqlite3.Error) as exc:
        click.echo(f"Error opening project database: {exc}", err=True)
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
