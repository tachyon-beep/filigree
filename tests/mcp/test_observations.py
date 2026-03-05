"""MCP tool tests for observation tools."""
from __future__ import annotations

from filigree.core import FiligreeDB
from filigree.mcp_server import call_tool
from tests.mcp._helpers import _parse


class TestObserveTool:
    async def test_observe_creates_observation(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {"summary": "Something looks wrong"})
        data = _parse(result)
        assert data["id"].startswith("mcp-")
        assert data["summary"] == "Something looks wrong"
        assert data["priority"] == 3

    async def test_observe_with_all_fields(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {
            "summary": "Null deref risk",
            "detail": "result.data used without check",
            "file_path": "src/core.py",
            "line": 42,
            "priority": 1,
            "actor": "claude",
        })
        data = _parse(result)
        assert data["summary"] == "Null deref risk"
        assert data["priority"] == 1

    async def test_observe_empty_summary_fails(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {"summary": ""})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_observe_priority_zero(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {"summary": "critical", "priority": 0})
        data = _parse(result)
        assert data["priority"] == 0

    async def test_observe_priority_four(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {"summary": "backlog", "priority": 4})
        data = _parse(result)
        assert data["priority"] == 4

    async def test_observe_with_source_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("observe", {
            "summary": "side note",
            "source_issue_id": "mcp-abc123",
        })
        data = _parse(result)
        assert data["source_issue_id"] == "mcp-abc123"


class TestListObservationsTool:
    async def test_list_empty(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("list_observations", {})
        data = _parse(result)
        assert data["observations"] == []
        assert data["stats"]["count"] == 0

    async def test_list_returns_observations(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_observation("First")
        mcp_db.create_observation("Second")
        result = await call_tool("list_observations", {})
        data = _parse(result)
        assert len(data["observations"]) == 2

    async def test_list_with_file_path_filter(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_observation("api bug", file_path="src/api/routes.py")
        mcp_db.create_observation("core bug", file_path="src/core.py")
        result = await call_tool("list_observations", {"file_path": "src/api"})
        data = _parse(result)
        assert len(data["observations"]) == 1
        assert data["observations"][0]["summary"] == "api bug"

    async def test_list_with_limit(self, mcp_db: FiligreeDB) -> None:
        for i in range(5):
            mcp_db.create_observation(f"Obs {i}")
        result = await call_tool("list_observations", {"limit": 2})
        data = _parse(result)
        assert len(data["observations"]) == 2

    async def test_list_with_file_id_filter(self, mcp_db: FiligreeDB) -> None:
        obs = mcp_db.create_observation("api bug", file_path="src/api.py")
        mcp_db.create_observation("other bug", file_path="src/other.py")
        result = await call_tool("list_observations", {"file_id": obs["file_id"]})
        data = _parse(result)
        assert len(data["observations"]) == 1
        assert data["observations"][0]["summary"] == "api bug"


class TestDismissObservationTool:
    async def test_dismiss(self, mcp_db: FiligreeDB) -> None:
        obs = mcp_db.create_observation("To dismiss")
        result = await call_tool("dismiss_observation", {"id": obs["id"]})
        data = _parse(result)
        assert data["status"] == "dismissed"

    async def test_dismiss_with_reason(self, mcp_db: FiligreeDB) -> None:
        obs = mcp_db.create_observation("Not a bug")
        result = await call_tool("dismiss_observation", {
            "id": obs["id"],
            "reason": "false positive",
            "actor": "tester",
        })
        _parse(result)
        row = mcp_db.conn.execute(
            "SELECT reason FROM dismissed_observations WHERE obs_id = ?", (obs["id"],)
        ).fetchone()
        assert row["reason"] == "false positive"

    async def test_dismiss_nonexistent_fails(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("dismiss_observation", {"id": "nope-123"})
        data = _parse(result)
        assert data["code"] == "not_found"


class TestBatchDismissTool:
    async def test_batch_dismiss(self, mcp_db: FiligreeDB) -> None:
        o1 = mcp_db.create_observation("One")
        o2 = mcp_db.create_observation("Two")
        mcp_db.create_observation("Three")
        result = await call_tool("batch_dismiss_observations", {
            "ids": [o1["id"], o2["id"]],
        })
        data = _parse(result)
        remaining = mcp_db.list_observations()
        assert len(remaining) == 1
        assert remaining[0]["summary"] == "Three"
        assert data.get("dismissed") == 2

    async def test_batch_dismiss_empty_list(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("batch_dismiss_observations", {"ids": []})
        data = _parse(result)
        assert data.get("dismissed", 0) == 0

    async def test_batch_dismiss_invalid_ids(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("batch_dismiss_observations", {"ids": ["nope-1", "nope-2"]})
        data = _parse(result)
        assert data.get("dismissed", 0) == 0


class TestPromoteObservationTool:
    async def test_promote(self, mcp_db: FiligreeDB) -> None:
        obs = mcp_db.create_observation(
            "Null pointer risk",
            detail="result.data used without check",
            file_path="src/api.py",
            priority=2,
        )
        result = await call_tool("promote_observation", {
            "id": obs["id"],
            "type": "bug",
        })
        data = _parse(result)
        assert "issue" in data
        assert data["issue"]["title"] == "Null pointer risk"
        assert mcp_db.list_observations() == []

    async def test_promote_nonexistent_fails(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("promote_observation", {"id": "nope-123"})
        data = _parse(result)
        assert data["code"] == "not_found"
