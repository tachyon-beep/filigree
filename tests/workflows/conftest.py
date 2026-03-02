"""Fixtures for end-to-end workflow scenario tests.

Pack-specific FiligreeDB fixtures used by tests in this directory.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from tests._db_factory import make_db


@pytest.fixture
def db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + risk + spike packs enabled."""
    d = make_db(tmp_path, packs=["core", "risk", "spike"])
    yield d
    d.close()


@pytest.fixture
def req_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + requirements packs enabled."""
    d = make_db(tmp_path, packs=["core", "requirements"])
    yield d
    d.close()


@pytest.fixture
def roadmap_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + planning + roadmap packs enabled."""
    d = make_db(tmp_path, packs=["core", "planning", "roadmap"])
    yield d
    d.close()


@pytest.fixture
def incident_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + incident packs enabled.

    Intentionally omits the planning pack — workflow e2e tests only exercise
    incident, postmortem, and task types (task is in core). Adding planning
    would mask issues where incident workflows inadvertently depend on
    planning-pack types.
    """
    d = make_db(tmp_path, packs=["core", "incident"])
    yield d
    d.close()


@pytest.fixture
def debt_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with core + debt packs enabled."""
    d = make_db(tmp_path, packs=["core", "debt"])
    yield d
    d.close()
