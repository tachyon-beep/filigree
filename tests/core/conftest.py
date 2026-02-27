"""Fixtures for core DB tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from tests._db_factory import make_db


# Duplicated from tests/workflows/conftest.py — keep in sync
@pytest.fixture
def release_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    d = make_db(tmp_path, packs=["core", "planning", "release"])
    yield d
    d.close()
