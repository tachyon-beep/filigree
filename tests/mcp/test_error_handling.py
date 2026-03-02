"""MCP error handling regression tests.

Covers: filigree-d6c3f6 (comment/label error handling)
"""

from __future__ import annotations

from filigree.core import FiligreeDB
from filigree.mcp_server import call_tool
from tests.mcp._helpers import _parse


class TestMCPCommentErrors:
    """MCP add_comment and get_comments on missing issues."""

    async def test_add_comment_missing_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "add_comment",
            {"issue_id": "nonexistent-xyz", "text": "Hello"},
        )
        data = _parse(result)
        assert data["code"] == "not_found"
        assert "nonexistent-xyz" in data["error"]

    async def test_get_comments_missing_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "get_comments",
            {"issue_id": "nonexistent-xyz"},
        )
        data = _parse(result)
        assert data["code"] == "not_found"
        assert "nonexistent-xyz" in data["error"]

    async def test_add_comment_still_works(self, mcp_db: FiligreeDB) -> None:
        """Verify normal add_comment still works after the fix."""
        issue = mcp_db.create_issue("Commentable")
        result = await call_tool(
            "add_comment",
            {"issue_id": issue.id, "text": "Hello"},
        )
        data = _parse(result)
        assert data["status"] == "ok"


class TestMCPLabelErrors:
    """MCP add_label and remove_label on missing issues."""

    async def test_add_label_missing_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "add_label",
            {"issue_id": "nonexistent-xyz", "label": "bug"},
        )
        data = _parse(result)
        assert data["code"] == "not_found"
        assert "nonexistent-xyz" in data["error"]

    async def test_remove_label_missing_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "remove_label",
            {"issue_id": "nonexistent-xyz", "label": "bug"},
        )
        data = _parse(result)
        assert data["code"] == "not_found"
        assert "nonexistent-xyz" in data["error"]

    async def test_add_label_still_works(self, mcp_db: FiligreeDB) -> None:
        """Verify normal add_label still works after the fix."""
        issue = mcp_db.create_issue("Labelable")
        result = await call_tool(
            "add_label",
            {"issue_id": issue.id, "label": "urgent"},
        )
        data = _parse(result)
        assert data["status"] == "added"
