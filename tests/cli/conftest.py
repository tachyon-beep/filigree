"""Fixtures for CLI interface tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.cli import cli


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
