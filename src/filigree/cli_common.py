"""Shared CLI helpers extracted from cli.py.

Provides ``get_db()`` and ``refresh_summary()`` so that both the main
``cli.py`` and future ``cli_commands/*.py`` subpackages can access them
without circular imports.
"""

from __future__ import annotations

import sys

import click

from filigree.core import (
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
    find_filigree_root,
    read_config,
)
from filigree.summary import write_summary


def get_db() -> FiligreeDB:
    """Discover .filigree/ and return an initialized FiligreeDB."""
    try:
        filigree_dir = find_filigree_root()
    except FileNotFoundError:
        click.echo(f"No {FILIGREE_DIR_NAME}/ found. Run 'filigree init' first.", err=True)
        sys.exit(1)
    config = read_config(filigree_dir)
    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=config.get("prefix", "filigree"))
    db.initialize()
    return db


def refresh_summary(db: FiligreeDB) -> None:
    """Regenerate context.md after mutations."""
    try:
        filigree_dir = find_filigree_root()
        write_summary(db, filigree_dir / SUMMARY_FILENAME)
    except FileNotFoundError:
        pass  # No .filigree/ dir — skip summary
