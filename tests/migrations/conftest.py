"""Migration tests work with bd-* IDs from the beads source database.

Override the shared ``db`` fixture so the test DB's prefix matches the
beads source prefix. This avoids tripping the v2.0 prefix-mismatch guard
on legitimate post-migration writes (e.g. ``test_rerun_does_not_overwrite_parent_id``
which re-parents a migrated issue).

In production, users running ``filigree migrate-from-beads`` are expected
to ``filigree init --prefix bd`` (or whatever matches their source IDs)
beforehand.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from tests._db_factory import make_db


@pytest.fixture
def db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """Migration-test DB with prefix='bd' to match beads source IDs."""
    d = make_db(tmp_path, prefix="bd")
    yield d
    d.close()
