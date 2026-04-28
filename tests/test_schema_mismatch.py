"""Tests for ``filigree doctor`` schema-mismatch (v+1) handling.

When a project's DB schema is newer than the installed filigree (a
"forward" mismatch), ``filigree doctor`` must exit with code 3 — distinct
from the generic exit 1 used for other failed checks — and surface the
shared guidance text from
:mod:`filigree.install_support.version_marker`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, FiligreeDB, write_config
from filigree.db_schema import CURRENT_SCHEMA_VERSION
from filigree.install_support.doctor import run_doctor


@pytest.fixture
def v_plus_one_project(tmp_path: Path) -> Path:
    """Create a filigree project, then forcibly bump its DB schema to v+1.

    Returns the project root (parent of ``.filigree/``). Opening this DB
    via :class:`FiligreeDB.from_filigree_dir` would raise
    ``SchemaVersionMismatchError`` by design — the bump uses raw sqlite3
    on a closed connection to simulate the "newer filigree wrote this DB"
    condition for ``filigree doctor`` to detect.
    """
    filigree_dir = tmp_path / FILIGREE_DIR_NAME
    filigree_dir.mkdir()
    write_config(filigree_dir, {"prefix": "proj", "version": 1})

    db_path = filigree_dir / DB_FILENAME
    d = FiligreeDB(db_path, prefix="proj")
    d.initialize()
    d.close()

    # Forcibly bump user_version past the installed schema version.
    bumped = CURRENT_SCHEMA_VERSION + 1
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA user_version = {bumped}")
        conn.commit()
    finally:
        conn.close()

    return tmp_path


def test_doctor_cli_exits_3_on_forward_mismatch(
    v_plus_one_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``filigree doctor`` must exit 3 (not 1) and emit guidance text."""
    from filigree.cli_commands.admin import doctor

    monkeypatch.chdir(v_plus_one_project)
    runner = CliRunner()
    result = runner.invoke(doctor, [])

    assert result.exit_code == 3, f"expected exit 3 for forward schema mismatch, got {result.exit_code}\noutput:\n{result.output}"
    assert "Downgrade is not supported" in result.output


def test_run_doctor_sets_schema_mismatch_forward_code(
    v_plus_one_project: Path,
) -> None:
    """Structural contract: the v+1 CheckResult carries the typed code.

    Guards against a future refactor renaming the code string and
    silently regressing exit-code wiring (the CLI test only checks
    exit 3, which is downstream of this field).
    """
    results = run_doctor(project_root=v_plus_one_project)
    schema_results = [r for r in results if r.name == "Schema version"]
    assert schema_results, f"no Schema version check found in results: {results}"
    schema_result = schema_results[0]
    assert schema_result.passed is False
    assert schema_result.code == "schema_mismatch_forward"
