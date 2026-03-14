"""Tests for dashboard API response & validation bug cluster.

Covers:
- search endpoint returns page count instead of total count
- scan ingest returns inverted status codes (202 for no-findings, 200 for findings)
- findings endpoint passes unvalidated severity/status through cast()
- add_dependency endpoint accepts empty depends_on
- MCP add_comment missing _refresh_summary call
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import filigree.dashboard as dash_module
from filigree.core import FiligreeDB
from filigree.dashboard import create_app
from tests._db_factory import make_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bug_db(tmp_path: Path) -> FiligreeDB:
    """FiligreeDB for bug cluster tests."""
    return make_db(tmp_path, check_same_thread=False)


@pytest.fixture
async def client(bug_db: FiligreeDB) -> AsyncClient:
    dash_module._db = bug_db
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._db = None


# ---------------------------------------------------------------------------
# Bug 1: search total is page count, not actual total
# ---------------------------------------------------------------------------


class TestSearchTotalCount:
    async def test_search_total_reflects_full_count_not_page_size(self, bug_db: FiligreeDB, client: AsyncClient) -> None:
        """Create more issues than the limit, verify total > len(results)."""
        # Create 5 issues with "widget" in the title
        for i in range(5):
            bug_db.create_issue(f"Widget component {i}")

        # Search with limit=2 — total should be 5, not 2
        resp = await client.get("/api/search", params={"q": "widget", "limit": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 2
        assert data["total"] >= 5, f"total should reflect full match count, got {data['total']}"


# ---------------------------------------------------------------------------
# Bug 2: scan ingest returns inverted status codes
# ---------------------------------------------------------------------------


class TestScanIngestStatusCode:
    async def test_scan_with_findings_returns_200(self, bug_db: FiligreeDB, client: AsyncClient) -> None:
        """Scan ingest with findings should return 200 (processed)."""
        payload = {
            "scan_source": "test-scanner",
            "findings": [
                {
                    "path": "src/foo.py",
                    "rule_id": "R001",
                    "message": "Test finding",
                    "severity": "warning",
                }
            ],
        }
        resp = await client.post("/api/v1/scan-results", json=payload)
        assert resp.status_code == 200, f"Findings present: expected 200, got {resp.status_code}"

    async def test_scan_without_findings_returns_200(self, bug_db: FiligreeDB, client: AsyncClient) -> None:
        """Scan ingest with no findings should also return 200 (synchronous operation completed)."""
        payload = {"scan_source": "test-scanner", "findings": []}
        resp = await client.post("/api/v1/scan-results", json=payload)
        # 202 is wrong here — the operation completed synchronously
        assert resp.status_code == 200, f"No findings: expected 200, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Bug 3: findings endpoint accepts invalid severity/status without validation
# ---------------------------------------------------------------------------


class TestFindingsEnumValidation:
    async def test_invalid_severity_returns_400(self, bug_db: FiligreeDB, client: AsyncClient) -> None:
        """Passing an invalid severity value should return 400, not silently return empty."""
        # Register a file first
        file_rec = bug_db.register_file("src/test.py")
        file_id = file_rec.id

        resp = await client.get(f"/api/files/{file_id}/findings", params={"severity": "banana"})
        assert resp.status_code == 400, f"Invalid severity should be rejected, got {resp.status_code}"

    async def test_invalid_finding_status_returns_400(self, bug_db: FiligreeDB, client: AsyncClient) -> None:
        """Passing an invalid finding status should return 400, not silently return empty."""
        file_rec = bug_db.register_file("src/test.py")
        file_id = file_rec.id

        resp = await client.get(f"/api/files/{file_id}/findings", params={"status": "banana"})
        assert resp.status_code == 400, f"Invalid status should be rejected, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Bug 4: add_dependency accepts empty depends_on
# ---------------------------------------------------------------------------


class TestAddDependencyValidation:
    async def test_empty_depends_on_returns_400(self, bug_db: FiligreeDB, client: AsyncClient) -> None:
        """Empty depends_on should return a clear 400 error, not a confusing KeyError."""
        issue = bug_db.create_issue("Test issue")
        resp = await client.post(
            f"/api/issue/{issue.id}/dependencies",
            json={"depends_on": ""},
        )
        assert resp.status_code == 400, f"Empty depends_on should be 400, got {resp.status_code}"

    async def test_missing_depends_on_returns_400(self, bug_db: FiligreeDB, client: AsyncClient) -> None:
        """Missing depends_on key should return 400."""
        issue = bug_db.create_issue("Test issue")
        resp = await client.post(
            f"/api/issue/{issue.id}/dependencies",
            json={},
        )
        assert resp.status_code == 400, f"Missing depends_on should be 400, got {resp.status_code}"
