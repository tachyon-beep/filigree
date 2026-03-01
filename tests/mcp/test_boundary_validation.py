"""MCP boundary validation tests for priority and actor."""

from __future__ import annotations

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
        result = await call_tool("close_issue", {"id": issue.id, "actor": "\ufeff"})
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

    async def test_reopen_issue_control_char_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        mcp_db.close_issue(issue.id, reason="done")
        result = await call_tool("reopen_issue", {"id": issue.id, "actor": "\x00evil"})
        data = _parse(result)
        assert data["code"] == "validation_error"
        assert "control" in data["error"].lower()

    async def test_reopen_issue_empty_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        mcp_db.close_issue(issue.id, reason="done")
        result = await call_tool("reopen_issue", {"id": issue.id, "actor": ""})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_release_claim_control_char_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        mcp_db.claim_issue(issue.id, assignee="agent-1")
        result = await call_tool("release_claim", {"id": issue.id, "actor": "\nbad"})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_release_claim_empty_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        mcp_db.claim_issue(issue.id, assignee="agent-1")
        result = await call_tool("release_claim", {"id": issue.id, "actor": ""})
        data = _parse(result)
        assert data["code"] == "validation_error"


class TestMCPPriorityValidation:
    """Priority range validation in MCP issue handlers."""

    async def test_create_issue_priority_too_high(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("create_issue", {"title": "Bad", "priority": 5})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_create_issue_priority_too_low(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("create_issue", {"title": "Bad", "priority": -1})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_create_issue_priority_boundary_0(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("create_issue", {"title": "Low bound", "priority": 0})
        data = _parse(result)
        assert "error" not in data
        assert data["priority"] == 0

    async def test_create_issue_priority_boundary_4(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("create_issue", {"title": "High bound", "priority": 4})
        data = _parse(result)
        assert "error" not in data
        assert data["priority"] == 4

    async def test_update_issue_priority_out_of_range(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        result = await call_tool("update_issue", {"id": issue.id, "priority": 99})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_list_issues_priority_filter_out_of_range(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("list_issues", {"priority": -1})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_batch_update_priority_out_of_range(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Target")
        result = await call_tool("batch_update", {"ids": [issue.id], "priority": 5})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_claim_next_priority_min_out_of_range(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("claim_next", {"assignee": "bot", "priority_min": -1})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_claim_next_priority_max_out_of_range(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("claim_next", {"assignee": "bot", "priority_max": 5})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_create_issue_priority_bool_true(self, mcp_db: FiligreeDB) -> None:
        """bool is a subclass of int — True should be rejected, not silently become 1."""
        result = await call_tool("create_issue", {"title": "Bad", "priority": True})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_create_issue_priority_bool_false(self, mcp_db: FiligreeDB) -> None:
        """bool is a subclass of int — False should be rejected, not silently become 0."""
        result = await call_tool("create_issue", {"title": "Bad", "priority": False})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_create_plan_milestone_priority_out_of_range(self, mcp_db: FiligreeDB) -> None:
        """Invalid priority on milestone should be rejected at boundary."""
        result = await call_tool(
            "create_plan",
            {
                "milestone": {"title": "Bad", "priority": 99},
                "phases": [{"title": "P1", "steps": [{"title": "S1"}]}],
            },
        )
        data = _parse(result)
        assert data["code"] == "validation_error"
        assert "milestone.priority" in data["error"]

    async def test_create_plan_phase_priority_out_of_range(self, mcp_db: FiligreeDB) -> None:
        """Invalid priority on a phase should be rejected at boundary."""
        result = await call_tool(
            "create_plan",
            {
                "milestone": {"title": "M"},
                "phases": [{"title": "P1", "priority": -1, "steps": [{"title": "S1"}]}],
            },
        )
        data = _parse(result)
        assert data["code"] == "validation_error"
        assert "phases[0].priority" in data["error"]

    async def test_create_plan_step_priority_out_of_range(self, mcp_db: FiligreeDB) -> None:
        """Invalid priority on a step should be rejected at boundary."""
        result = await call_tool(
            "create_plan",
            {
                "milestone": {"title": "M"},
                "phases": [{"title": "P1", "steps": [{"title": "S1", "priority": 5}]}],
            },
        )
        data = _parse(result)
        assert data["code"] == "validation_error"
        assert "phases[0].steps[0].priority" in data["error"]

    async def test_create_plan_step_priority_bool(self, mcp_db: FiligreeDB) -> None:
        """Bool priority on a nested step should be rejected."""
        result = await call_tool(
            "create_plan",
            {
                "milestone": {"title": "M"},
                "phases": [{"title": "P1", "steps": [{"title": "S1", "priority": True}]}],
            },
        )
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_create_plan_valid_priorities(self, mcp_db: FiligreeDB) -> None:
        """Valid priorities (0-4) should succeed."""
        result = await call_tool(
            "create_plan",
            {
                "milestone": {"title": "M", "priority": 0},
                "phases": [{"title": "P1", "priority": 4, "steps": [{"title": "S1", "priority": 2}]}],
            },
        )
        data = _parse(result)
        assert "error" not in data

    async def test_update_issue_priority_none_allowed(self, mcp_db: FiligreeDB) -> None:
        """Not providing priority should be fine (optional)."""
        issue = mcp_db.create_issue("Target")
        result = await call_tool("update_issue", {"id": issue.id, "title": "New"})
        data = _parse(result)
        assert "error" not in data
        assert data.get("title") == "New"


class TestMCPStringValidation:
    """_validate_str boundary tests for MCP file-tool filter fields."""

    async def test_list_files_language_non_string(self, mcp_db: FiligreeDB) -> None:
        """Non-string language filter should be rejected."""
        result = await call_tool("list_files", {"language": 123})
        data = _parse(result)
        assert data["code"] == "validation_error"
        assert "string" in data["error"]

    async def test_list_files_path_prefix_non_string(self, mcp_db: FiligreeDB) -> None:
        """Non-string path_prefix filter should be rejected."""
        result = await call_tool("list_files", {"path_prefix": True})
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_list_files_scan_source_non_string(self, mcp_db: FiligreeDB) -> None:
        """Non-string scan_source filter should be rejected."""
        result = await call_tool("list_files", {"scan_source": ["test"]})
        data = _parse(result)
        assert data["code"] == "validation_error"
