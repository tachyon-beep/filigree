"""Tests for file records and scan findings features."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import filigree.dashboard as dash_module
from filigree.core import FiligreeDB
from filigree.dashboard import create_app
from filigree.registry import ProjectManager, Registry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> FiligreeDB:
    """Fresh FiligreeDB for file/finding tests."""
    d = FiligreeDB(tmp_path / "filigree.db", prefix="test")
    d.initialize()
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestFileSchema:
    """Verify file/finding tables are created."""

    def test_file_records_table_exists(self, db: FiligreeDB) -> None:
        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_records'").fetchone()
        assert row is not None

    def test_scan_findings_table_exists(self, db: FiligreeDB) -> None:
        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_findings'").fetchone()
        assert row is not None

    def test_file_associations_table_exists(self, db: FiligreeDB) -> None:
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='file_associations'"
        ).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# FileRecord CRUD tests
# ---------------------------------------------------------------------------


class TestRegisterFile:
    """Tests for registering and retrieving file records."""

    def test_register_new_file(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py", language="python")
        assert f.path == "src/main.py"
        assert f.language == "python"
        assert f.id.startswith("test-f-")

    def test_register_duplicate_path_returns_existing(self, db: FiligreeDB) -> None:
        f1 = db.register_file("src/main.py")
        f2 = db.register_file("src/main.py")
        assert f1.id == f2.id

    def test_register_updates_language(self, db: FiligreeDB) -> None:
        db.register_file("src/main.py", language="")
        f2 = db.register_file("src/main.py", language="python")
        assert f2.language == "python"

    def test_get_file_by_id(self, db: FiligreeDB) -> None:
        created = db.register_file("src/main.py", language="python")
        fetched = db.get_file(created.id)
        assert fetched.path == "src/main.py"
        assert fetched.language == "python"

    def test_get_file_not_found(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError, match="test-f-nope"):
            db.get_file("test-f-nope")

    def test_get_file_by_path(self, db: FiligreeDB) -> None:
        created = db.register_file("src/main.py")
        fetched = db.get_file_by_path("src/main.py")
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_file_by_path_not_found(self, db: FiligreeDB) -> None:
        result = db.get_file_by_path("nonexistent.py")
        assert result is None


class TestListFiles:
    """Tests for listing file records."""

    def test_list_empty(self, db: FiligreeDB) -> None:
        files = db.list_files()
        assert files == []

    def test_list_returns_all(self, db: FiligreeDB) -> None:
        db.register_file("a.py")
        db.register_file("b.py")
        files = db.list_files()
        assert len(files) == 2

    def test_list_with_limit(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.register_file(f"file{i}.py")
        files = db.list_files(limit=3)
        assert len(files) == 3

    def test_list_with_offset(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.register_file(f"file{i}.py")
        files = db.list_files(offset=3)
        assert len(files) == 2

    def test_list_with_language_filter(self, db: FiligreeDB) -> None:
        db.register_file("a.py", language="python")
        db.register_file("b.js", language="javascript")
        files = db.list_files(language="python")
        assert len(files) == 1
        assert files[0].language == "python"

    def test_list_with_path_prefix(self, db: FiligreeDB) -> None:
        db.register_file("src/core/a.py")
        db.register_file("src/core/b.py")
        db.register_file("tests/test_a.py")
        files = db.list_files(path_prefix="src/core/")
        assert len(files) == 2

    def test_list_sorted_by_path(self, db: FiligreeDB) -> None:
        db.register_file("z.py")
        db.register_file("a.py")
        files = db.list_files(sort="path")
        assert files[0].path == "a.py"
        assert files[1].path == "z.py"


# ---------------------------------------------------------------------------
# Scan findings tests
# ---------------------------------------------------------------------------


class TestProcessScanResults:
    """Tests for ingesting scan findings."""

    def test_ingest_creates_file_and_findings(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(
            scan_source="ruff",
            findings=[
                {
                    "path": "src/main.py",
                    "rule_id": "E501",
                    "severity": "low",
                    "message": "Line too long",
                    "line_start": 10,
                    "line_end": 10,
                },
            ],
        )
        assert result["files_created"] >= 1
        assert result["findings_created"] >= 1

    def test_ingest_upserts_existing_finding(self, db: FiligreeDB) -> None:
        finding = {
            "path": "src/main.py",
            "rule_id": "E501",
            "severity": "low",
            "message": "Line too long",
            "line_start": 10,
        }
        db.process_scan_results(scan_source="ruff", findings=[finding])
        result = db.process_scan_results(scan_source="ruff", findings=[finding])
        # Second ingest should update, not create
        assert result["findings_created"] == 0
        assert result["findings_updated"] >= 1

    def test_ingest_with_language(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {
                    "path": "src/main.py",
                    "language": "python",
                    "rule_id": "E501",
                    "severity": "low",
                    "message": "Line too long",
                },
            ],
        )
        f = db.get_file_by_path("src/main.py")
        assert f is not None
        assert f.language == "python"

    def test_ingest_validates_severity(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="severity"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[
                    {
                        "path": "src/main.py",
                        "rule_id": "E501",
                        "severity": "extreme",
                        "message": "Bad",
                    },
                ],
            )

    def test_ingest_empty_findings(self, db: FiligreeDB) -> None:
        result = db.process_scan_results(scan_source="ruff", findings=[])
        assert result["files_created"] == 0
        assert result["findings_created"] == 0

    def test_ingest_finding_missing_path(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="path"):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"severity": "low", "message": "No path key"}],
            )

    def test_ingest_finding_is_string(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="dict"):
            db.process_scan_results(scan_source="ruff", findings=["not-a-dict"])

    def test_ingest_finding_is_number(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="dict"):
            db.process_scan_results(scan_source="ruff", findings=[42])


class TestGetFindings:
    """Tests for retrieving findings for a file."""

    def test_get_findings_for_file(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "Too long"},
                {"path": "a.py", "rule_id": "E502", "severity": "high", "message": "Bad import"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        assert len(findings) == 2

    def test_get_findings_with_severity_filter(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "Low"},
                {"path": "a.py", "rule_id": "E502", "severity": "high", "message": "High"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id, severity="high")
        assert len(findings) == 1
        assert findings[0].severity == "high"

    def test_get_findings_with_status_filter(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "A"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id, status="open")
        assert len(findings) == 1
        findings = db.get_findings(f.id, status="fixed")
        assert len(findings) == 0

    def test_get_findings_pagination(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": f"E{i}", "severity": "low", "message": f"msg{i}"} for i in range(10)],
        )
        f = db.get_file_by_path("a.py")
        page1 = db.get_findings(f.id, limit=5)
        assert len(page1) == 5
        page2 = db.get_findings(f.id, limit=5, offset=5)
        assert len(page2) == 5


# ---------------------------------------------------------------------------
# File association tests
# ---------------------------------------------------------------------------


class TestFileAssociations:
    """Tests for linking files to issues."""

    def test_add_association(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py")
        issue = db.create_issue("Fix bug")
        db.add_file_association(f.id, issue.id, "bug_in")
        assocs = db.get_file_associations(f.id)
        assert len(assocs) == 1
        assert assocs[0]["issue_id"] == issue.id
        assert assocs[0]["assoc_type"] == "bug_in"

    def test_add_duplicate_association_is_idempotent(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py")
        issue = db.create_issue("Fix bug")
        db.add_file_association(f.id, issue.id, "bug_in")
        db.add_file_association(f.id, issue.id, "bug_in")
        assocs = db.get_file_associations(f.id)
        assert len(assocs) == 1

    def test_invalid_assoc_type(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py")
        issue = db.create_issue("Fix bug")
        with pytest.raises(ValueError, match="assoc_type"):
            db.add_file_association(f.id, issue.id, "invalid_type")

    def test_multiple_association_types(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py")
        issue = db.create_issue("Fix bug")
        db.add_file_association(f.id, issue.id, "bug_in")
        db.add_file_association(f.id, issue.id, "task_for")
        assocs = db.get_file_associations(f.id)
        assert len(assocs) == 2


# ---------------------------------------------------------------------------
# Bidirectional navigation tests (issue → files/findings)
# ---------------------------------------------------------------------------


class TestIssueFiles:
    """Tests for getting files associated with an issue."""

    def test_get_issue_files(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        f = db.register_file("src/main.py")
        db.add_file_association(f.id, issue.id, "bug_in")
        files = db.get_issue_files(issue.id)
        assert len(files) == 1
        assert files[0]["file_id"] == f.id
        assert files[0]["assoc_type"] == "bug_in"

    def test_get_issue_files_empty(self, db: FiligreeDB) -> None:
        issue = db.create_issue("No files")
        files = db.get_issue_files(issue.id)
        assert files == []

    def test_get_issue_files_includes_file_path(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        f = db.register_file("src/main.py", language="python")
        db.add_file_association(f.id, issue.id, "bug_in")
        files = db.get_issue_files(issue.id)
        assert files[0]["file_path"] == "src/main.py"
        assert files[0]["file_language"] == "python"


class TestIssueFindings:
    """Tests for getting scan findings linked to an issue."""

    def test_get_issue_findings_via_association(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        f = db.register_file("src/main.py")
        db.add_file_association(f.id, issue.id, "scan_finding")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "src/main.py", "rule_id": "E501", "severity": "low", "message": "Too long"}],
        )
        findings = db.get_issue_findings(issue.id)
        assert len(findings) >= 1

    def test_get_issue_findings_via_direct_link(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "src/main.py", "rule_id": "E501", "severity": "low", "message": "Too long"}],
        )
        f = db.get_file_by_path("src/main.py")
        # Directly link a finding to the issue
        finding = db.get_findings(f.id)[0]
        db.conn.execute("UPDATE scan_findings SET issue_id = ? WHERE id = ?", (issue.id, finding.id))
        db.conn.commit()
        findings = db.get_issue_findings(issue.id)
        assert len(findings) >= 1

    def test_get_issue_findings_empty(self, db: FiligreeDB) -> None:
        issue = db.create_issue("No findings")
        findings = db.get_issue_findings(issue.id)
        assert findings == []


class TestFileDetailCore:
    """Tests for get_file_findings_summary() and get_file_detail()."""

    def test_summary_empty_file(self, db: FiligreeDB) -> None:
        f = db.register_file("empty.py")
        summary = db.get_file_findings_summary(f.id)
        assert summary["total_findings"] == 0
        assert summary["open_findings"] == 0
        for sev in ("critical", "high", "medium", "low", "info"):
            assert summary[sev] == 0

    def test_summary_counts_by_severity(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "S101", "severity": "critical", "message": "Assert"},
                {"path": "a.py", "rule_id": "E501", "severity": "high", "message": "Long"},
                {"path": "a.py", "rule_id": "E302", "severity": "high", "message": "Space"},
                {"path": "a.py", "rule_id": "W291", "severity": "low", "message": "Trail"},
            ],
        )
        f = db.get_file_by_path("a.py")
        summary = db.get_file_findings_summary(f.id)
        assert summary["total_findings"] == 4
        assert summary["open_findings"] == 4
        assert summary["critical"] == 1
        assert summary["high"] == 2
        assert summary["low"] == 1

    def test_summary_excludes_fixed_and_false_positive(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "high", "message": "Long"},
                {"path": "a.py", "rule_id": "E302", "severity": "medium", "message": "Space"},
            ],
        )
        f = db.get_file_by_path("a.py")
        # Mark one as fixed
        findings = db.get_findings(f.id)
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (findings[0].id,))
        db.conn.commit()
        summary = db.get_file_findings_summary(f.id)
        assert summary["total_findings"] == 2  # total includes all
        assert summary["open_findings"] == 1  # open excludes fixed

    def test_get_file_detail_structure(self, db: FiligreeDB) -> None:
        f = db.register_file("src/main.py", language="python")
        detail = db.get_file_detail(f.id)
        assert set(detail.keys()) == {"file", "associations", "recent_findings", "summary"}
        assert detail["file"]["path"] == "src/main.py"
        assert detail["associations"] == []
        assert detail["recent_findings"] == []
        assert detail["summary"]["total_findings"] == 0

    def test_get_file_detail_with_data(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Fix bug")
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "src/main.py", "rule_id": "E501", "severity": "high", "message": "Long"},
            ],
        )
        f = db.get_file_by_path("src/main.py")
        db.add_file_association(f.id, issue.id, "bug_in")
        detail = db.get_file_detail(f.id)
        assert len(detail["associations"]) == 1
        assert detail["associations"][0]["issue_title"] == "Fix bug"
        assert len(detail["recent_findings"]) == 1
        assert detail["recent_findings"][0]["severity"] == "high"
        assert detail["summary"]["high"] == 1

    def test_get_file_detail_raises_for_missing(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db.get_file_detail("nonexistent")

    def test_recent_findings_capped_at_10(self, db: FiligreeDB) -> None:
        findings = [
            {"path": "big.py", "rule_id": f"E{i:03d}", "severity": "low", "message": f"Finding {i}"} for i in range(15)
        ]
        db.process_scan_results(scan_source="ruff", findings=findings)
        f = db.get_file_by_path("big.py")
        detail = db.get_file_detail(f.id)
        assert len(detail["recent_findings"]) == 10
        assert detail["summary"]["total_findings"] == 15


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


class TestFileMigration:
    """Verify v1→v2 migration adds file tables to existing databases."""

    def test_migration_creates_tables(self, tmp_path: Path) -> None:
        # Create a v1 database
        d = FiligreeDB(tmp_path / "filigree.db", prefix="test")
        d.initialize()
        # Should be at v2 now (fresh DB gets latest schema)
        assert d.get_schema_version() == 2
        d.close()

    def test_migration_from_v1(self, tmp_path: Path) -> None:
        """Simulate an existing v1 database that needs migration."""
        import sqlite3

        db_path = tmp_path / "filigree.db"
        conn = sqlite3.connect(str(db_path))
        # Manually create only v1 tables (without file tables)
        from filigree.core import SCHEMA_V1_SQL

        conn.executescript(SCHEMA_V1_SQL)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

        # Opening with FiligreeDB should run migration
        d = FiligreeDB(db_path, prefix="test")
        d.initialize()
        assert d.get_schema_version() == 2
        # File tables should now exist
        row = d.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_records'").fetchone()
        assert row is not None
        d.close()


# ---------------------------------------------------------------------------
# Dashboard API endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def api_db(tmp_path: Path) -> FiligreeDB:
    """Fresh DB for dashboard API tests with check_same_thread=False."""
    d = FiligreeDB(tmp_path / "filigree.db", prefix="test", check_same_thread=False)
    d.initialize()
    yield d
    d.close()


@pytest.fixture
async def client(api_db: FiligreeDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    """Test client wired to the api_db fixture."""
    reg_dir = tmp_path / ".filigree-registry"
    monkeypatch.setattr("filigree.registry.REGISTRY_DIR", reg_dir)
    monkeypatch.setattr("filigree.registry.REGISTRY_FILE", reg_dir / "registry.json")
    monkeypatch.setattr("filigree.registry.REGISTRY_LOCK", reg_dir / "registry.lock")

    registry = Registry()
    pm = ProjectManager(registry)
    pm._connections["test"] = api_db
    pm._paths["test"] = Path("/fake/.filigree")

    dash_module._project_manager = pm
    dash_module._default_project_key = "test"

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._project_manager = None
    dash_module._default_project_key = ""


class TestFileEndpoints:
    """Tests for file API endpoints."""

    async def test_list_files_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total"] == 0

    async def test_list_files_with_data(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        api_db.register_file("src/main.py", language="python")
        api_db.register_file("src/utils.py", language="python")
        resp = await client.get("/api/files")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 2

    async def test_list_files_with_language_filter(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        api_db.register_file("a.py", language="python")
        api_db.register_file("b.js", language="javascript")
        resp = await client.get("/api/files?language=python")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["language"] == "python"

    async def test_get_file_detail_structure(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        f = api_db.register_file("src/main.py", language="python")
        resp = await client.get(f"/api/files/{f.id}")
        assert resp.status_code == 200
        data = resp.json()
        # Top-level keys are separated data layers
        assert set(data.keys()) == {"file", "associations", "recent_findings", "summary"}
        assert data["file"]["path"] == "src/main.py"
        assert data["file"]["language"] == "python"
        assert data["associations"] == []
        assert data["recent_findings"] == []
        assert data["summary"]["total_findings"] == 0
        assert data["summary"]["open_findings"] == 0

    async def test_get_file_detail_with_findings(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        api_db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "x.py", "rule_id": "E501", "severity": "high", "message": "Line too long"},
                {"path": "x.py", "rule_id": "E302", "severity": "low", "message": "Spacing"},
            ],
        )
        f = api_db.get_file_by_path("x.py")
        resp = await client.get(f"/api/files/{f.id}")
        data = resp.json()
        assert data["summary"]["total_findings"] == 2
        assert data["summary"]["open_findings"] == 2
        assert data["summary"]["high"] == 1
        assert data["summary"]["low"] == 1
        assert data["summary"]["critical"] == 0
        assert len(data["recent_findings"]) == 2

    async def test_get_file_detail_with_associations(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        f = api_db.register_file("src/main.py")
        issue = api_db.create_issue("Fix the bug")
        api_db.add_file_association(f.id, issue.id, "bug_in")
        resp = await client.get(f"/api/files/{f.id}")
        data = resp.json()
        assert len(data["associations"]) == 1
        assert data["associations"][0]["issue_id"] == issue.id
        assert data["associations"][0]["assoc_type"] == "bug_in"
        assert data["associations"][0]["issue_title"] == "Fix the bug"

    async def test_get_file_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/test-f-nope")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "FILE_NOT_FOUND"

    async def test_get_file_findings(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        api_db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "Too long"},
            ],
        )
        f = api_db.get_file_by_path("a.py")
        resp = await client.get(f"/api/files/{f.id}/findings")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["rule_id"] == "E501"

    async def test_post_scan_results(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/scan-results",
            json={
                "scan_source": "ruff",
                "findings": [
                    {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "Too long"},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["findings_created"] == 1

    async def test_post_scan_results_invalid_severity(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/scan-results",
            json={
                "scan_source": "ruff",
                "findings": [
                    {"path": "a.py", "rule_id": "E501", "severity": "extreme", "message": "Bad"},
                ],
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_post_file_association(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        f = api_db.register_file("src/main.py")
        issue = api_db.create_issue("Fix bug")
        resp = await client.post(
            f"/api/files/{f.id}/associations",
            json={"issue_id": issue.id, "assoc_type": "bug_in"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

    async def test_post_file_association_invalid_type(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        f = api_db.register_file("src/main.py")
        issue = api_db.create_issue("Fix bug")
        resp = await client.post(
            f"/api/files/{f.id}/associations",
            json={"issue_id": issue.id, "assoc_type": "invalid"},
        )
        assert resp.status_code == 400

    async def test_schema_endpoint_statuses_updated(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/_schema")
        data = resp.json()
        # All endpoints should now be live
        for ep in data["endpoints"]:
            assert ep["status"] == "live", f"{ep['path']} should be live"


class TestHotspots:
    """Tests for the hotspots (triage prioritization) feature."""

    def test_hotspots_empty(self, db: FiligreeDB) -> None:
        result = db.get_file_hotspots()
        assert result == []

    def test_hotspots_ranks_by_severity_score(self, db: FiligreeDB) -> None:
        # File with critical findings should rank higher
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "critical.py", "rule_id": "S001", "severity": "critical", "message": "Security"},
                {"path": "low.py", "rule_id": "E501", "severity": "low", "message": "Style"},
                {"path": "low.py", "rule_id": "E502", "severity": "low", "message": "Style 2"},
                {"path": "low.py", "rule_id": "E503", "severity": "low", "message": "Style 3"},
            ],
        )
        result = db.get_file_hotspots()
        assert len(result) == 2
        # critical=10 > 3*low=3
        assert result[0]["file"]["path"] == "critical.py"
        assert result[0]["score"] > result[1]["score"]

    def test_hotspots_with_limit(self, db: FiligreeDB) -> None:
        for i in range(10):
            db.process_scan_results(
                scan_source="ruff",
                findings=[{"path": f"file{i}.py", "rule_id": "E501", "severity": "low", "message": "msg"}],
            )
        result = db.get_file_hotspots(limit=5)
        assert len(result) == 5

    def test_hotspots_includes_breakdown(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "S001", "severity": "critical", "message": "Critical"},
                {"path": "a.py", "rule_id": "S002", "severity": "high", "message": "High"},
                {"path": "a.py", "rule_id": "E501", "severity": "low", "message": "Low"},
            ],
        )
        result = db.get_file_hotspots()
        assert result[0]["findings_breakdown"]["critical"] == 1
        assert result[0]["findings_breakdown"]["high"] == 1
        assert result[0]["findings_breakdown"]["low"] == 1

    def test_hotspots_only_counts_open_findings(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "E501", "severity": "high", "message": "High"},
            ],
        )
        f = db.get_file_by_path("a.py")
        findings = db.get_findings(f.id)
        # Mark finding as fixed
        db.conn.execute("UPDATE scan_findings SET status = 'fixed' WHERE id = ?", (findings[0].id,))
        db.conn.commit()
        result = db.get_file_hotspots()
        assert result == []


class TestPaginationMetadata:
    """Tests for paginated response format."""

    def test_list_files_returns_total(self, db: FiligreeDB) -> None:
        for i in range(10):
            db.register_file(f"file{i}.py")
        result = db.list_files_paginated(limit=3)
        assert result["total"] == 10
        assert result["limit"] == 3
        assert result["offset"] == 0
        assert result["has_more"] is True
        assert len(result["results"]) == 3

    def test_list_files_last_page(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.register_file(f"file{i}.py")
        result = db.list_files_paginated(limit=3, offset=3)
        assert result["total"] == 5
        assert result["has_more"] is False
        assert len(result["results"]) == 2

    def test_list_files_empty(self, db: FiligreeDB) -> None:
        result = db.list_files_paginated()
        assert result["total"] == 0
        assert result["has_more"] is False
        assert result["results"] == []

    def test_get_findings_returns_total(self, db: FiligreeDB) -> None:
        db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": f"E{i}", "severity": "low", "message": f"msg{i}"} for i in range(10)],
        )
        f = db.get_file_by_path("a.py")
        result = db.get_findings_paginated(f.id, limit=5)
        assert result["total"] == 10
        assert result["has_more"] is True
        assert len(result["results"]) == 5

    def test_list_files_with_filter_and_total(self, db: FiligreeDB) -> None:
        for i in range(5):
            db.register_file(f"src/file{i}.py", language="python")
        for i in range(3):
            db.register_file(f"lib/file{i}.js", language="javascript")
        result = db.list_files_paginated(language="python")
        assert result["total"] == 5
        assert len(result["results"]) == 5


class TestBidirectionalEndpoints:
    """Tests for issue→files and issue→findings endpoints."""

    async def test_issue_files_endpoint(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        issue = api_db.create_issue("Fix bug")
        f = api_db.register_file("src/main.py")
        api_db.add_file_association(f.id, issue.id, "bug_in")
        resp = await client.get(f"/api/issue/{issue.id}/files")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["file_path"] == "src/main.py"

    async def test_issue_findings_endpoint(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        issue = api_db.create_issue("Fix bug")
        api_db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": "E501", "severity": "low", "message": "Too long"}],
        )
        f = api_db.get_file_by_path("a.py")
        api_db.add_file_association(f.id, issue.id, "scan_finding")
        resp = await client.get(f"/api/issue/{issue.id}/findings")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    async def test_issue_files_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/issue/test-nope/files")
        assert resp.status_code == 404


class TestHotspotsEndpoint:
    """Tests for the hotspots API endpoint."""

    async def test_hotspots_endpoint(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        api_db.process_scan_results(
            scan_source="ruff",
            findings=[
                {"path": "a.py", "rule_id": "S001", "severity": "critical", "message": "Critical"},
                {"path": "b.py", "rule_id": "E501", "severity": "low", "message": "Low"},
            ],
        )
        resp = await client.get("/api/files/hotspots")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["score"] > data[1]["score"]

    async def test_hotspots_with_limit(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        for i in range(5):
            api_db.process_scan_results(
                scan_source="ruff",
                findings=[{"path": f"file{i}.py", "rule_id": "E501", "severity": "low", "message": "msg"}],
            )
        resp = await client.get("/api/files/hotspots?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3


class TestPaginatedEndpoints:
    """Tests for paginated API response format."""

    async def test_list_files_paginated_response(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        for i in range(5):
            api_db.register_file(f"file{i}.py")
        resp = await client.get("/api/files?limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert data["limit"] == 3
        assert data["offset"] == 0
        assert data["has_more"] is True
        assert len(data["results"]) == 3

    async def test_file_findings_paginated_response(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        api_db.process_scan_results(
            scan_source="ruff",
            findings=[{"path": "a.py", "rule_id": f"E{i}", "severity": "low", "message": f"msg{i}"} for i in range(5)],
        )
        f = api_db.get_file_by_path("a.py")
        resp = await client.get(f"/api/files/{f.id}/findings?limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert data["has_more"] is True
        assert len(data["results"]) == 3


class TestInputValidation400s:
    """Malformed client input must return 400 with structured error, never 500."""

    # -- P2a: scan body must be a JSON object --------------------------------

    async def test_scan_body_is_list(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/scan-results",
            content="[]",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_scan_body_is_string(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/scan-results",
            content='"hello"',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_association_body_is_list(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        f = api_db.register_file("x.py")
        resp = await client.post(
            f"/api/files/{f.id}/associations",
            content="[]",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # -- P2b: malformed finding entries --------------------------------------

    async def test_scan_finding_missing_path(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/scan-results",
            json={"scan_source": "ruff", "findings": [{"severity": "low"}]},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert "path" in resp.json()["error"]["message"].lower()

    async def test_scan_finding_is_string(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/scan-results",
            json={"scan_source": "ruff", "findings": ["not-a-dict"]},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_scan_finding_is_number(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/scan-results",
            json={"scan_source": "ruff", "findings": [42]},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # -- P2c: pagination query params ----------------------------------------

    async def test_files_limit_not_int(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files?limit=abc")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_files_offset_not_int(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files?offset=xyz")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_findings_limit_not_int(self, client: AsyncClient, api_db: FiligreeDB) -> None:
        f = api_db.register_file("x.py")
        resp = await client.get(f"/api/files/{f.id}/findings?limit=nope")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_hotspots_limit_not_int(self, client: AsyncClient) -> None:
        resp = await client.get("/api/files/hotspots?limit=bad")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
