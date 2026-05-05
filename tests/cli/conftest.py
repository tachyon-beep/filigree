"""Fixtures for CLI interface tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.cli import cli
from tests._seeds import SeededProject, seed_bugs, seed_file, seed_finding, seed_observations, seed_open_bug


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
            bug_id = seed_open_bug(db)
        return SeededProject(path=initialized_project, bug_id=bug_id)
    finally:
        os.chdir(original)


@pytest.fixture
def initialized_project_with_bugs(initialized_project: Path) -> SeededProject:
    from filigree.cli_common import get_db

    original = os.getcwd()
    os.chdir(str(initialized_project))
    try:
        with get_db() as db:
            ids = seed_bugs(db, count=3)
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
            # Single-obs scenario uses the canonical "note" text for parity
            # with the prior inline fixture — tests assert on the text.
            obs_id = db.create_observation("note", actor="test")["id"]
        return SeededProject(path=initialized_project, obs_id=obs_id)
    finally:
        os.chdir(original)


@pytest.fixture
def initialized_project_with_many_obs(initialized_project: Path) -> SeededProject:
    from filigree.cli_common import get_db

    original = os.getcwd()
    os.chdir(str(initialized_project))
    try:
        with get_db() as db:
            ids = seed_observations(db, count=3)
        return SeededProject(path=initialized_project, obs_ids=ids)
    finally:
        os.chdir(original)


@pytest.fixture
def initialized_project_with_file(initialized_project: Path) -> SeededProject:
    from filigree.cli_common import get_db

    original = os.getcwd()
    os.chdir(str(initialized_project))
    try:
        with get_db() as db:
            fid = seed_file(db)
        return SeededProject(path=initialized_project, file_id=fid)
    finally:
        os.chdir(original)


@pytest.fixture
def initialized_project_with_finding(initialized_project: Path) -> SeededProject:
    from filigree.cli_common import get_db

    original = os.getcwd()
    os.chdir(str(initialized_project))
    try:
        with get_db() as db:
            fid = seed_file(db)
            finding_id = seed_finding(db, file_id=fid)
        return SeededProject(path=initialized_project, file_id=fid, finding_id=finding_id)
    finally:
        os.chdir(original)


@pytest.fixture
def initialized_project_with_many_findings(initialized_project: Path) -> SeededProject:
    from filigree.cli_common import get_db

    original = os.getcwd()
    os.chdir(str(initialized_project))
    try:
        with get_db() as db:
            fid = seed_file(db)
            finding_ids = [seed_finding(db, file_id=fid, rule_id=f"rule-{i}", message=f"Finding {i}") for i in range(3)]
        return SeededProject(path=initialized_project, file_id=fid, finding_ids=finding_ids)
    finally:
        os.chdir(original)
