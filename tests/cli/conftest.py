"""Fixtures for CLI interface tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.cli import cli
from tests._seeds import SeededProject


@pytest.fixture
def cli_in_project(tmp_path: Path, cli_runner: CliRunner) -> Generator[tuple[CliRunner, Path], None, None]:
    """Initialize a filigree project in tmp_path and return (runner, project_root)."""
    original_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    result = cli_runner.invoke(cli, ["init", "--prefix", "test"])
    assert result.exit_code == 0
    yield cli_runner, tmp_path
    os.chdir(original_cwd)


def _extract_id(create_output: str) -> str:
    """Extract issue ID from 'Created test-abc123: Title' output."""
    return create_output.split(":")[0].replace("Created ", "").strip()


# initialized_project and _fresh_project live in tests/conftest.py so they are
# visible to both tests/cli/ and tests/mcp/. Pytest resolves them from the root
# conftest; no local definition needed here.


@pytest.fixture
def initialized_project_with_bug(initialized_project: Path) -> SeededProject:
    from filigree.cli_common import get_db

    original = os.getcwd()
    os.chdir(str(initialized_project))
    try:
        with get_db() as db:
            bug = db.create_issue("Test bug", type="bug", priority=2)
        return SeededProject(path=initialized_project, bug_id=bug.id)
    finally:
        os.chdir(original)


@pytest.fixture
def initialized_project_with_bugs(initialized_project: Path) -> SeededProject:
    from filigree.cli_common import get_db

    original = os.getcwd()
    os.chdir(str(initialized_project))
    try:
        ids: list[str] = []
        with get_db() as db:
            for i in range(3):
                ids.append(db.create_issue(f"Bug {i}", type="bug", priority=2).id)
        return SeededProject(path=initialized_project, bug_ids=ids)
    finally:
        os.chdir(original)


@pytest.fixture
def initialized_project_with_observation(initialized_project: Path) -> SeededProject:
    from filigree.cli_common import get_db

    original = os.getcwd()
    os.chdir(str(initialized_project))
    try:
        with get_db() as db:
            rec = db.create_observation("note", actor="test")
        return SeededProject(path=initialized_project, obs_id=rec["id"])
    finally:
        os.chdir(original)


@pytest.fixture
def initialized_project_with_many_obs(initialized_project: Path) -> SeededProject:
    from filigree.cli_common import get_db

    original = os.getcwd()
    os.chdir(str(initialized_project))
    try:
        ids: list[str] = []
        with get_db() as db:
            for i in range(3):
                ids.append(db.create_observation(f"note {i}", actor="test")["id"])
        return SeededProject(path=initialized_project, obs_ids=ids)
    finally:
        os.chdir(original)
