"""Shared CLI helpers extracted from cli.py.

Provides ``get_db()`` and ``refresh_summary()`` so that both the main
``cli.py`` and future ``cli_commands/*.py`` subpackages can access them
without circular imports.
"""

from __future__ import annotations

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


def get_db() -> FiligreeDB:
    """Discover the project anchor and return an initialized FiligreeDB.

    Uses :func:`find_filigree_anchor` so legacy installs (no ``.filigree.conf``
    yet) still open without requiring write access — the conf is only created
    by explicit init/install paths, not by discovery.
    """
    try:
        project_root, conf_path = find_filigree_anchor()
    except ProjectNotInitialisedError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    if conf_path is not None:
        return FiligreeDB.from_conf(conf_path)
    return FiligreeDB.from_filigree_dir(project_root / FILIGREE_DIR_NAME)


def refresh_summary(db: FiligreeDB) -> None:
    """Regenerate context.md after mutations."""
    try:
        filigree_dir = find_filigree_root()
        write_summary(db, filigree_dir / SUMMARY_FILENAME)
    except FileNotFoundError:
        pass  # No .filigree/ dir — skip summary
