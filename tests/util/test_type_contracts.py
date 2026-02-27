"""Contract tests for TypedDict shapes vs actual runtime return values."""

from __future__ import annotations

import ast
from collections.abc import Generator
from pathlib import Path
from typing import get_type_hints

import pytest

from filigree.core import FileRecord, FiligreeDB
from filigree.types.core import FileRecordDict, IssueDict, ScanFindingDict
from tests._db_factory import make_db

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """Fresh FiligreeDB for each test."""
    d = make_db(tmp_path)
    yield d
    d.close()


# ---------------------------------------------------------------------------
# 1. Runtime shape tests — key-set + value-type checks
# ---------------------------------------------------------------------------


class TestIssueDictShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        result = issue.to_dict()
        hints = get_type_hints(IssueDict)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task", priority=1, labels=["a"])
        result = issue.to_dict()
        assert isinstance(result["id"], str)
        assert isinstance(result["title"], str)
        assert isinstance(result["priority"], int)
        assert isinstance(result["is_ready"], bool)
        assert isinstance(result["labels"], list)
        assert isinstance(result["blocks"], list)
        assert isinstance(result["blocked_by"], list)
        assert isinstance(result["children"], list)
        assert isinstance(result["fields"], dict)


class TestFileRecordDictShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        row = db.conn.execute("SELECT * FROM file_records LIMIT 1").fetchone()
        fr = FileRecord(
            id=row["id"],
            path=row["path"],
            language=row["language"] or "",
            file_type=row["file_type"] or "",
        )
        result = fr.to_dict()
        hints = get_type_hints(FileRecordDict)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        row = db.conn.execute("SELECT * FROM file_records LIMIT 1").fetchone()
        fr = FileRecord(
            id=row["id"],
            path=row["path"],
            language=row["language"] or "",
            file_type=row["file_type"] or "",
        )
        result = fr.to_dict()
        assert isinstance(result["id"], str)
        assert isinstance(result["path"], str)
        assert isinstance(result["metadata"], dict)


class TestScanFindingDictShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        findings = db.get_findings_paginated(file_id=file_id, limit=1)
        result = findings["results"][0]
        hints = get_type_hints(ScanFindingDict)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high", "line_start": 1}],
        )
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        findings = db.get_findings_paginated(file_id=file_id, limit=1)
        result = findings["results"][0]
        assert isinstance(result["id"], str)
        assert isinstance(result["severity"], str)
        assert isinstance(result["seen_count"], int)


# ---------------------------------------------------------------------------
# 2. Import constraint test — AST-based
# ---------------------------------------------------------------------------

TYPES_DIR = Path(__file__).resolve().parents[2] / "src" / "filigree" / "types"
FORBIDDEN_MODULES = {"filigree.core", "filigree.db_base"}
FORBIDDEN_PREFIXES = ("filigree.db_",)


def _get_imports_from_file(filepath: Path) -> list[str]:
    tree = ast.parse(filepath.read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


@pytest.mark.parametrize(
    "py_file",
    sorted(TYPES_DIR.glob("*.py")),
    ids=lambda p: p.name,
)
def test_types_module_import_constraint(py_file: Path) -> None:
    imports = _get_imports_from_file(py_file)
    for mod in imports:
        assert mod not in FORBIDDEN_MODULES, f"{py_file.name} imports {mod}"
        for prefix in FORBIDDEN_PREFIXES:
            assert not mod.startswith(prefix), f"{py_file.name} imports {mod}"


# ---------------------------------------------------------------------------
# 3. Dashboard JSON key contract test
# ---------------------------------------------------------------------------

DASHBOARD_ISSUE_KEYS = {
    "id",
    "title",
    "type",
    "status",
    "status_category",
    "priority",
    "assignee",
    "blocked_by",
    "blocks",
    "updated_at",
    "created_at",
    "is_ready",
    "children",
    "labels",
    "description",
    "notes",
}


def test_issue_dict_keys_cover_dashboard_contract() -> None:
    hints = get_type_hints(IssueDict)
    missing = DASHBOARD_ISSUE_KEYS - set(hints.keys())
    assert not missing, f"IssueDict missing dashboard keys: {missing}"
