"""Fixtures for template system tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from tests._db_factory import make_db


@pytest.fixture
def incident_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + planning + incident packs enabled."""
    d = make_db(tmp_path, packs=["core", "planning", "incident"])
    yield d
    d.close()
