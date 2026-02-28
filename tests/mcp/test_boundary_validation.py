"""MCP boundary validation tests for priority and actor."""

from __future__ import annotations

from typing import Any

from filigree.core import FiligreeDB
from filigree.mcp_server import call_tool
from tests.mcp.conftest import _parse


class TestMCPActorValidation:
    """Actor validation across MCP handlers."""

    async def test_create_issue_empty_actor(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("create_issue", {"title": "Test", "actor": ""})
        data = _parse(result)
        assert data["code"] == "validation_error"
        assert "empty" in data["error"]

    async def test_create_issue_control_char_actor(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("create_issue", {"title": "Test", "actor": "\x00evil"})
        data = _parse(result)
        assert data["code"] == "validation_error"
        assert "control" in data["error"].lower()

    async def test_create_issue_strips_actor(self, mcp_db: FiligreeDB) -> None:
        """Valid actor with whitespace should succeed (stripped)."""
        result = await call_tool("create_issue", {"title": "Stripped", "actor": "  bot  "})
        data = _parse(result)
        assert "error" not in data
        assert data["title"] == "Stripped"

    async def test_create_issue_default_actor(self, mcp_db: FiligreeDB) -> None:
        """No actor provided — defaults to 'mcp', should succeed."""
        result = await call_tool("create_issue", {"title": "Default Actor"})
        data = _parse(result)
        assert "error" not in data

    async def test_update_issue_empty_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        result = await call_tool("update_issue", {"id": issue.id, "title": "New", "actor": ""})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_close_issue_bom_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        result = await call_tool("close_issue", {"id": issue.id, "actor": "\uFEFF"})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_add_dependency_control_actor(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("A")
        b = mcp_db.create_issue("B")
        result = await call_tool(
            "add_dependency",
            {"from_id": a.id, "to_id": b.id, "actor": "\nbad"},
        )
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_add_comment_empty_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        result = await call_tool(
            "add_comment",
            {"issue_id": issue.id, "text": "hello", "actor": ""},
        )
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_undo_last_long_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        result = await call_tool(
            "undo_last",
            {"id": issue.id, "actor": "a" * 129},
        )
        data = _parse(result)
        assert data["code"] == "validation_error"
        assert "128" in data["error"]
