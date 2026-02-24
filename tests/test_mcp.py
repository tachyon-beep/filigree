"""MCP server contract tests — test all 20 tools via call_tool()."""

from __future__ import annotations

import asyncio
import builtins
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, SUMMARY_FILENAME, FiligreeDB, write_config
from filigree.mcp_server import (
    _MAX_LIST_RESULTS,
    _safe_path,
    _text,
    call_tool,
    create_mcp_app,
    get_workflow_prompt,
    list_resources,
    list_tools,
    read_context,
)


def _parse(result: list[Any]) -> Any:
    """Extract the text content from MCP response and parse as JSON if possible."""
    text = result[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


@pytest.fixture
def mcp_db(tmp_path: Path) -> FiligreeDB:
    """Set up a FiligreeDB and patch the MCP module globals."""
    filigree_dir = tmp_path / FILIGREE_DIR_NAME
    filigree_dir.mkdir()
    write_config(filigree_dir, {"prefix": "mcp", "version": 1})
    (filigree_dir / SUMMARY_FILENAME).write_text("# test\n")

    d = FiligreeDB(filigree_dir / DB_FILENAME, prefix="mcp")
    d.initialize()

    import filigree.mcp_server as mcp_mod

    original_db = mcp_mod.db
    original_dir = mcp_mod._filigree_dir
    mcp_mod.db = d
    mcp_mod._filigree_dir = filigree_dir

    yield d

    mcp_mod.db = original_db
    mcp_mod._filigree_dir = original_dir
    d.close()


class TestCreateAndGet:
    async def test_create_issue(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("create_issue", {"title": "MCP test issue"})
        data = _parse(result)
        assert data["title"] == "MCP test issue"
        assert data["id"].startswith("mcp-")

    async def test_create_issue_full(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "create_issue",
            {
                "title": "Full MCP issue",
                "type": "bug",
                "priority": 1,
                "description": "A bug",
                "notes": "Notes here",
                "fields": {"severity": "critical"},
                "labels": ["urgent"],
            },
        )
        data = _parse(result)
        assert data["type"] == "bug"
        assert data["priority"] == 1
        assert data["fields"]["severity"] == "critical"
        assert "urgent" in data["labels"]

    async def test_get_issue(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Get me")
        result = await call_tool("get_issue", {"id": issue.id})
        data = _parse(result)
        assert data["title"] == "Get me"

    async def test_get_issue_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_issue", {"id": "nonexistent-xyz"})
        data = _parse(result)
        assert data["code"] == "not_found"


class TestRefreshSummaryBestEffort:
    async def test_mutation_succeeds_when_summary_refresh_fails(self, mcp_db: FiligreeDB) -> None:
        """If _refresh_summary() raises, the mutation result should still be returned."""
        with patch("filigree.mcp_server.write_summary", side_effect=OSError("disk full")):
            result = await call_tool("create_issue", {"title": "Should succeed"})
        data = _parse(result)
        # Mutation must succeed — the issue was created in the DB
        assert "id" in data
        assert data["title"] == "Should succeed"
        # Verify it's actually in the DB
        issue = mcp_db.get_issue(data["id"])
        assert issue.title == "Should succeed"


class TestListAndSearch:
    async def test_list_issues(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("List A")
        mcp_db.create_issue("List B")
        result = await call_tool("list_issues", {})
        data = _parse(result)
        assert len(data["issues"]) == 2
        assert data["has_more"] is False

    async def test_list_issues_filter(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("Open one")
        b = mcp_db.create_issue("Close one")
        mcp_db.close_issue(b.id)
        result = await call_tool("list_issues", {"status": "open"})
        data = _parse(result)
        assert len(data["issues"]) == 1

    async def test_search(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("Authentication bug")
        mcp_db.create_issue("Something else")
        result = await call_tool("search_issues", {"query": "auth"})
        data = _parse(result)
        assert len(data["issues"]) == 1
        assert data["issues"][0]["title"] == "Authentication bug"
        assert data["has_more"] is False


class TestListPagination:
    """Pagination cap and has_more for list_issues / search_issues."""

    async def test_list_issues_capped(self, mcp_db: FiligreeDB) -> None:
        """Default cap limits results to _MAX_LIST_RESULTS."""
        for i in range(_MAX_LIST_RESULTS + 5):
            mcp_db.create_issue(f"Issue {i}")
        result = await call_tool("list_issues", {})
        data = _parse(result)
        assert len(data["issues"]) == _MAX_LIST_RESULTS
        assert data["has_more"] is True
        assert data["limit"] == _MAX_LIST_RESULTS
        assert data["offset"] == 0

    async def test_list_issues_no_limit(self, mcp_db: FiligreeDB) -> None:
        """no_limit=true bypasses the cap."""
        for i in range(_MAX_LIST_RESULTS + 5):
            mcp_db.create_issue(f"Issue {i}")
        result = await call_tool("list_issues", {"no_limit": True})
        data = _parse(result)
        assert len(data["issues"]) == _MAX_LIST_RESULTS + 5
        assert data["has_more"] is False

    async def test_list_issues_no_limit_with_explicit_limit_has_more(self, mcp_db: FiligreeDB) -> None:
        """no_limit=true with explicit limit should compute has_more correctly."""
        for i in range(10):
            mcp_db.create_issue(f"Issue {i}")
        result = await call_tool("list_issues", {"no_limit": True, "limit": 5})
        data = _parse(result)
        assert len(data["issues"]) == 5
        assert data["has_more"] is True
        assert data["limit"] == 5

    async def test_list_issues_offset(self, mcp_db: FiligreeDB) -> None:
        """Offset works with the capped limit."""
        for i in range(_MAX_LIST_RESULTS + 10):
            mcp_db.create_issue(f"Issue {i}")
        result = await call_tool("list_issues", {"offset": _MAX_LIST_RESULTS})
        data = _parse(result)
        assert len(data["issues"]) == 10
        assert data["has_more"] is False
        assert data["offset"] == _MAX_LIST_RESULTS

    async def test_list_issues_requested_limit_below_cap(self, mcp_db: FiligreeDB) -> None:
        """Explicit limit below _MAX_LIST_RESULTS is respected."""
        for i in range(10):
            mcp_db.create_issue(f"Issue {i}")
        result = await call_tool("list_issues", {"limit": 3})
        data = _parse(result)
        assert len(data["issues"]) == 3
        assert data["has_more"] is True
        assert data["limit"] == 3

    async def test_search_issues_capped(self, mcp_db: FiligreeDB) -> None:
        """search_issues respects the same cap."""
        for i in range(_MAX_LIST_RESULTS + 5):
            mcp_db.create_issue(f"Bug {i}")
        result = await call_tool("search_issues", {"query": "Bug"})
        data = _parse(result)
        assert len(data["issues"]) == _MAX_LIST_RESULTS
        assert data["has_more"] is True

    async def test_search_issues_no_limit(self, mcp_db: FiligreeDB) -> None:
        """search_issues with no_limit=true bypasses the cap."""
        for i in range(_MAX_LIST_RESULTS + 5):
            mcp_db.create_issue(f"Bug {i}")
        result = await call_tool("search_issues", {"query": "Bug", "no_limit": True})
        data = _parse(result)
        assert len(data["issues"]) == _MAX_LIST_RESULTS + 5
        assert data["has_more"] is False

    async def test_search_issues_no_limit_with_explicit_limit_has_more(self, mcp_db: FiligreeDB) -> None:
        """search_issues no_limit=true with explicit limit computes has_more correctly."""
        for i in range(10):
            mcp_db.create_issue(f"Bug {i}")
        result = await call_tool("search_issues", {"query": "Bug", "no_limit": True, "limit": 5})
        data = _parse(result)
        assert len(data["issues"]) == 5
        assert data["has_more"] is True


class TestUpdateAndClose:
    async def test_update_issue(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Update me")
        result = await call_tool("update_issue", {"id": issue.id, "status": "in_progress"})
        data = _parse(result)
        assert data["status"] == "in_progress"

    async def test_update_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("update_issue", {"id": "nonexistent-xyz", "title": "nope"})
        data = _parse(result)
        assert data["code"] == "not_found"

    async def test_close_issue(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Close me")
        result = await call_tool("close_issue", {"id": issue.id, "reason": "done"})
        data = _parse(result)
        assert data["status"] == "closed"

    async def test_close_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("close_issue", {"id": "nonexistent-xyz"})
        data = _parse(result)
        assert data["code"] == "not_found"


class TestDependencies:
    async def test_add_dependency(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Blocked")
        b = mcp_db.create_issue("Blocker")
        result = await call_tool("add_dependency", {"from_id": a.id, "to_id": b.id})
        data = _parse(result)
        assert data["status"] == "added"
        assert data["from_id"] == a.id

    async def test_add_dependency_cycle(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("A")
        b = mcp_db.create_issue("B")
        mcp_db.add_dependency(a.id, b.id)
        result = await call_tool("add_dependency", {"from_id": b.id, "to_id": a.id})
        data = _parse(result)
        assert data["code"] == "invalid"

    async def test_remove_dependency(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("A")
        b = mcp_db.create_issue("B")
        mcp_db.add_dependency(a.id, b.id)
        result = await call_tool("remove_dependency", {"from_id": a.id, "to_id": b.id})
        data = _parse(result)
        assert data["status"] == "removed"


class TestReadyAndBlocked:
    async def test_get_ready(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("Ready one")
        result = await call_tool("get_ready", {})
        data = _parse(result)
        assert len(data) == 1
        assert data[0]["title"] == "Ready one"

    async def test_get_blocked(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Blocked")
        b = mcp_db.create_issue("Blocker")
        mcp_db.add_dependency(a.id, b.id)
        result = await call_tool("get_blocked", {})
        data = _parse(result)
        assert len(data) == 1
        assert data[0]["id"] == a.id
        assert b.id in data[0]["blocked_by"]


class TestPlan:
    async def test_get_plan(self, mcp_db: FiligreeDB) -> None:
        ms = mcp_db.create_issue("Milestone", type="milestone")
        p = mcp_db.create_issue("Phase 1", type="phase", parent_id=ms.id)
        s = mcp_db.create_issue("Step 1", type="step", parent_id=p.id)
        mcp_db.close_issue(s.id)
        result = await call_tool("get_plan", {"milestone_id": ms.id})
        data = _parse(result)
        assert data["total_steps"] == 1
        assert data["completed_steps"] == 1

    async def test_get_plan_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_plan", {"milestone_id": "nonexistent-xyz"})
        data = _parse(result)
        assert data["code"] == "not_found"


class TestComments:
    async def test_add_comment(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Commentable")
        result = await call_tool("add_comment", {"issue_id": issue.id, "text": "A comment"})
        data = _parse(result)
        assert data["status"] == "ok"
        assert "comment_id" in data

    async def test_get_comments(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("With comments")
        mcp_db.add_comment(issue.id, "First", author="alice")
        mcp_db.add_comment(issue.id, "Second", author="bob")
        result = await call_tool("get_comments", {"issue_id": issue.id})
        data = _parse(result)
        assert len(data) == 2
        assert data[0]["text"] == "First"


class TestTemplateAndSummary:
    async def test_get_template(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_template", {"type": "bug"})
        data = _parse(result)
        assert data["type"] == "bug"
        assert "fields_schema" in data

    async def test_get_template_unknown(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_template", {"type": "nonexistent"})
        data = _parse(result)
        assert data["code"] == "not_found"

    async def test_get_summary(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_summary", {})
        text = _parse(result)
        assert "Project Pulse" in text

    async def test_get_stats(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("A")
        result = await call_tool("get_stats", {})
        data = _parse(result)
        assert "by_status" in data
        assert data["by_status"]["open"] == 1


class TestLabels:
    async def test_add_label(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Labelable")
        result = await call_tool("add_label", {"issue_id": issue.id, "label": "urgent"})
        data = _parse(result)
        assert data["status"] == "added"
        assert data["label"] == "urgent"
        # Verify it was actually added
        updated = mcp_db.get_issue(issue.id)
        assert "urgent" in updated.labels

    async def test_add_label_rejects_reserved_type_name(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Labelable")
        result = await call_tool("add_label", {"issue_id": issue.id, "label": "bug"})
        data = _parse(result)
        assert data["code"] == "validation_error"
        assert "reserved as an issue type" in data["error"]

    async def test_remove_label(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Labelable", labels=["defect", "urgent"])
        result = await call_tool("remove_label", {"issue_id": issue.id, "label": "defect"})
        data = _parse(result)
        assert data["status"] == "removed"
        updated = mcp_db.get_issue(issue.id)
        assert "defect" not in updated.labels
        assert "urgent" in updated.labels


class TestCreatePlan:
    async def test_create_plan_basic(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "create_plan",
            {
                "milestone": {"title": "v1.0 Release"},
                "phases": [
                    {
                        "title": "Phase 1",
                        "steps": [
                            {"title": "Step 1.1"},
                            {"title": "Step 1.2", "deps": [0]},
                        ],
                    },
                    {
                        "title": "Phase 2",
                        "steps": [
                            {"title": "Step 2.1", "deps": ["0.1"]},
                        ],
                    },
                ],
            },
        )
        data = _parse(result)
        assert data["milestone"]["title"] == "v1.0 Release"
        assert data["total_steps"] == 3
        assert len(data["phases"]) == 2
        assert data["phases"][0]["total"] == 2
        assert data["phases"][1]["total"] == 1

    async def test_create_plan_empty_phases(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "create_plan",
            {
                "milestone": {"title": "Empty milestone"},
                "phases": [{"title": "Empty phase"}],
            },
        )
        data = _parse(result)
        assert data["total_steps"] == 0
        assert len(data["phases"]) == 1

    async def test_create_plan_with_descriptions(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "create_plan",
            {
                "milestone": {"title": "Described plan", "description": "A plan with descriptions", "priority": 1},
                "phases": [
                    {
                        "title": "Phase A",
                        "description": "First phase",
                        "steps": [{"title": "Step A.1", "description": "Do something", "priority": 0}],
                    },
                ],
            },
        )
        data = _parse(result)
        assert data["milestone"]["priority"] == 1
        assert data["phases"][0]["steps"][0]["priority"] == 0


class TestBatchClose:
    async def test_batch_close(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Close A")
        b = mcp_db.create_issue("Close B")
        result = await call_tool("batch_close", {"ids": [a.id, b.id], "reason": "done"})
        data = _parse(result)
        assert data["count"] == 2
        assert a.id in data["succeeded"]
        assert b.id in data["succeeded"]
        assert data["failed"] == []

    async def test_batch_close_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("batch_close", {"ids": ["nonexistent-xyz"]})
        data = _parse(result)
        assert data["count"] == 0
        assert len(data["failed"]) == 1
        assert data["failed"][0]["code"] == "not_found"


class TestBatchUpdate:
    async def test_batch_update_status(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Update A")
        b = mcp_db.create_issue("Update B")
        result = await call_tool("batch_update", {"ids": [a.id, b.id], "status": "in_progress"})
        data = _parse(result)
        assert data["count"] == 2
        assert a.id in data["succeeded"]
        assert mcp_db.get_issue(a.id).status == "in_progress"
        assert mcp_db.get_issue(b.id).status == "in_progress"

    async def test_batch_update_priority(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Priority A")
        b = mcp_db.create_issue("Priority B")
        result = await call_tool("batch_update", {"ids": [a.id, b.id], "priority": 0})
        data = _parse(result)
        assert data["count"] == 2
        assert mcp_db.get_issue(a.id).priority == 0

    async def test_batch_update_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("batch_update", {"ids": ["nonexistent-xyz"], "status": "closed"})
        data = _parse(result)
        assert data["count"] == 0
        assert len(data["failed"]) == 1
        assert data["failed"][0]["code"] == "not_found"


class TestBatchAddLabel:
    async def test_batch_add_label(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Label A")
        b = mcp_db.create_issue("Label B")
        result = await call_tool("batch_add_label", {"ids": [a.id, b.id], "label": "security"})
        data = _parse(result)
        assert data["count"] == 2
        assert a.id in data["succeeded"]
        assert b.id in data["succeeded"]
        assert data["failed"] == []

    async def test_batch_add_label_partial_failure(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Label A")
        result = await call_tool("batch_add_label", {"ids": [a.id, "nonexistent-xyz"], "label": "security"})
        data = _parse(result)
        assert data["count"] == 1
        assert a.id in data["succeeded"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["code"] == "not_found"

    async def test_batch_add_label_validation_error(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Label A")
        result = await call_tool("batch_add_label", {"ids": [a.id], "label": "bug"})
        data = _parse(result)
        assert data["count"] == 0
        assert len(data["failed"]) == 1
        assert data["failed"][0]["code"] == "validation_error"


class TestBatchAddComment:
    async def test_batch_add_comment(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Comment A")
        b = mcp_db.create_issue("Comment B")
        result = await call_tool("batch_add_comment", {"ids": [a.id, b.id], "text": "triage complete"})
        data = _parse(result)
        assert data["count"] == 2
        assert a.id in data["succeeded"]
        assert b.id in data["succeeded"]
        assert data["failed"] == []

    async def test_batch_add_comment_partial_failure(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Comment A")
        result = await call_tool("batch_add_comment", {"ids": [a.id, "nonexistent-xyz"], "text": "triage complete"})
        data = _parse(result)
        assert data["count"] == 1
        assert a.id in data["succeeded"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["code"] == "not_found"

    async def test_batch_add_comment_validation_error(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Comment A")
        result = await call_tool("batch_add_comment", {"ids": [a.id], "text": "   "})
        data = _parse(result)
        assert data["count"] == 0
        assert len(data["failed"]) == 1
        assert data["failed"][0]["code"] == "validation_error"


class TestClaimIssue:
    async def test_claim_success(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Claimable")
        result = await call_tool("claim_issue", {"id": issue.id, "assignee": "agent-1"})
        data = _parse(result)
        assert data["status"] == "open"  # status unchanged — claim only sets assignee
        assert data["assignee"] == "agent-1"

    async def test_claim_conflict(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Claimable")
        mcp_db.claim_issue(issue.id, assignee="agent-1")
        result = await call_tool("claim_issue", {"id": issue.id, "assignee": "agent-2"})
        data = _parse(result)
        assert data["code"] == "conflict"

    async def test_claim_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("claim_issue", {"id": "nonexistent-xyz", "assignee": "agent-1"})
        data = _parse(result)
        assert data["code"] == "not_found"


class TestGetChanges:
    async def test_get_changes(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Track me")
        mcp_db.update_issue(issue.id, status="in_progress")
        result = await call_tool("get_changes", {"since": "2000-01-01T00:00:00+00:00"})
        data = _parse(result)
        assert len(data) >= 2  # created + status_changed
        assert any(e["event_type"] == "status_changed" for e in data)

    async def test_get_changes_empty(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_changes", {"since": "2099-01-01T00:00:00+00:00"})
        data = _parse(result)
        assert data == []

    async def test_get_changes_with_limit(self, mcp_db: FiligreeDB) -> None:
        for i in range(5):
            mcp_db.create_issue(f"Issue {i}")
        result = await call_tool("get_changes", {"since": "2000-01-01T00:00:00+00:00", "limit": 2})
        data = _parse(result)
        assert len(data) == 2


class TestActorIdentity:
    async def test_update_with_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Actor test")
        await call_tool("update_issue", {"id": issue.id, "status": "in_progress", "actor": "agent-alpha"})
        events = mcp_db.get_recent_events(limit=5)
        status_event = next(e for e in events if e["event_type"] == "status_changed")
        assert status_event["actor"] == "agent-alpha"

    async def test_close_with_actor(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Actor close")
        await call_tool("close_issue", {"id": issue.id, "actor": "agent-beta"})
        events = mcp_db.get_recent_events(limit=5)
        close_event = next(e for e in events if e["event_type"] == "status_changed" and e["new_value"] == "closed")
        assert close_event["actor"] == "agent-beta"

    async def test_default_actor_is_mcp(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Default actor")
        await call_tool("update_issue", {"id": issue.id, "status": "in_progress"})
        events = mcp_db.get_recent_events(limit=5)
        status_event = next(e for e in events if e["event_type"] == "status_changed")
        assert status_event["actor"] == "mcp"


class TestResource:
    async def test_list_resources(self, mcp_db: FiligreeDB) -> None:
        resources = await list_resources()
        assert len(resources) == 1
        assert resources[0].name == "Project Pulse"
        assert str(resources[0].uri) == "filigree://context"

    async def test_read_context_resource(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("Resource test")
        content = await read_context("filigree://context")
        assert "Project Pulse" in content
        assert "Resource test" in content

    async def test_read_unknown_resource(self, mcp_db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Unknown resource"):
            await read_context("filigree://nonexistent")


class TestPrompt:
    async def test_get_workflow_prompt(self, mcp_db: FiligreeDB) -> None:
        result = await get_workflow_prompt("filigree-workflow", None)
        assert result.description is not None
        assert "workflow" in result.description.lower()
        assert len(result.messages) >= 1
        # First message should contain the workflow text
        assert "Filigree Workflow" in result.messages[0].content.text  # type: ignore[union-attr]

    async def test_workflow_prompt_includes_context(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("Prompt context test")
        result = await get_workflow_prompt("filigree-workflow", None)
        assert len(result.messages) == 2
        # Second message should be the project summary
        assert "Project Pulse" in result.messages[1].content.text  # type: ignore[union-attr]

    async def test_workflow_prompt_excludes_context(self, mcp_db: FiligreeDB) -> None:
        result = await get_workflow_prompt("filigree-workflow", {"include_context": "false"})
        assert len(result.messages) == 1


class TestProactiveContext:
    async def test_close_returns_newly_unblocked(self, mcp_db: FiligreeDB) -> None:
        blocker = mcp_db.create_issue("Blocker")
        blocked = mcp_db.create_issue("Blocked task")
        mcp_db.add_dependency(blocked.id, blocker.id)
        result = await call_tool("close_issue", {"id": blocker.id})
        data = _parse(result)
        assert "newly_unblocked" in data
        assert len(data["newly_unblocked"]) == 1
        assert data["newly_unblocked"][0]["id"] == blocked.id

    async def test_close_no_unblocked_items(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Standalone")
        result = await call_tool("close_issue", {"id": issue.id})
        data = _parse(result)
        assert "newly_unblocked" not in data

    async def test_batch_close_returns_newly_unblocked(self, mcp_db: FiligreeDB) -> None:
        b1 = mcp_db.create_issue("Blocker 1")
        b2 = mcp_db.create_issue("Blocker 2")
        blocked = mcp_db.create_issue("Doubly blocked")
        mcp_db.add_dependency(blocked.id, b1.id)
        mcp_db.add_dependency(blocked.id, b2.id)
        result = await call_tool("batch_close", {"ids": [b1.id, b2.id]})
        data = _parse(result)
        assert "newly_unblocked" in data
        assert any(item["id"] == blocked.id for item in data["newly_unblocked"])


class TestMetrics:
    async def test_get_metrics(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Metric test")
        mcp_db.update_issue(issue.id, status="in_progress")
        mcp_db.close_issue(issue.id)
        result = await call_tool("get_metrics", {})
        data = _parse(result)
        assert "throughput" in data
        assert "avg_cycle_time_hours" in data
        assert data["throughput"] >= 1
        assert data["period_days"] == 30

    async def test_get_metrics_with_days(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_metrics", {"days": 7})
        data = _parse(result)
        assert data["period_days"] == 7


class TestCriticalPathMCP:
    async def test_get_critical_path(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("A")
        b = mcp_db.create_issue("B")
        mcp_db.add_dependency(a.id, b.id)
        result = await call_tool("get_critical_path", {})
        data = _parse(result)
        assert data["length"] == 2
        assert len(data["path"]) == 2

    async def test_get_critical_path_empty(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_critical_path", {})
        data = _parse(result)
        assert data["length"] == 0
        assert data["path"] == []


class TestUnknownTool:
    async def test_unknown_tool(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("nonexistent_tool", {})
        data = _parse(result)
        assert data["code"] == "unknown_tool"


class TestTextHelper:
    def test_text_string(self) -> None:
        result = _text("hello")
        assert result[0].text == "hello"

    def test_text_dict(self) -> None:
        result = _text({"key": "value"})
        assert '"key"' in result[0].text


class TestWorkflowTemplateTools:
    """Tests for Batch 1 — MCP read tools for workflow templates."""

    async def test_list_types(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("list_types", {})
        data = _parse(result)
        assert isinstance(data, list)
        assert len(data) >= 2  # At least task and bug from core pack
        type_names = [t["type"] for t in data]
        assert "task" in type_names
        assert "bug" in type_names
        # Each type has required fields
        for t in data:
            assert "display_name" in t
            assert "pack" in t
            assert "states" in t
            assert "initial_state" in t

    async def test_list_types_sorted(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("list_types", {})
        data = _parse(result)
        type_names = [t["type"] for t in data]
        assert type_names == sorted(type_names)

    async def test_get_type_info_task(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_type_info", {"type": "task"})
        data = _parse(result)
        assert data["type"] == "task"
        assert data["display_name"] == "Task"
        assert len(data["states"]) >= 3  # open, in_progress, closed at minimum
        assert len(data["transitions"]) >= 2
        assert "fields_schema" in data

    async def test_get_type_info_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_type_info", {"type": "nonexistent"})
        data = _parse(result)
        assert data["code"] == "not_found"

    async def test_get_type_info_fields_have_options(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_type_info", {"type": "bug"})
        data = _parse(result)
        severity_field = next((f for f in data["fields_schema"] if f["name"] == "severity"), None)
        assert severity_field is not None
        assert "options" in severity_field

    async def test_list_packs(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("list_packs", {})
        data = _parse(result)
        assert isinstance(data, list)
        assert len(data) >= 2  # core + planning
        pack_names = [p["pack"] for p in data]
        assert "core" in pack_names
        for p in data:
            assert "version" in p
            assert "types" in p
            assert "requires_packs" in p

    async def test_get_valid_transitions(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Transition test", type="task")
        result = await call_tool("get_valid_transitions", {"issue_id": issue.id})
        data = _parse(result)
        assert isinstance(data, list)
        assert len(data) >= 1
        for t in data:
            assert "to" in t
            assert "category" in t
            assert "ready" in t
            assert "missing_fields" in t

    async def test_get_valid_transitions_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_valid_transitions", {"issue_id": "nonexistent-xyz"})
        data = _parse(result)
        assert data["code"] == "not_found"

    async def test_validate_issue(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Validate test", type="task")
        result = await call_tool("validate_issue", {"issue_id": issue.id})
        data = _parse(result)
        assert "valid" in data
        assert "warnings" in data
        assert "errors" in data
        assert data["valid"] is True

    async def test_validate_issue_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("validate_issue", {"issue_id": "nonexistent-xyz"})
        data = _parse(result)
        assert data["code"] == "not_found"

    async def test_get_workflow_guide(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_workflow_guide", {"pack": "core"})
        data = _parse(result)
        assert data["pack"] == "core"
        # Guide content may or may not be present depending on the pack data
        assert "guide" in data

    async def test_get_workflow_guide_not_found(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_workflow_guide", {"pack": "nonexistent"})
        data = _parse(result)
        assert data["code"] == "not_found"

    async def test_explain_state(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("explain_state", {"type": "task", "state": "open"})
        data = _parse(result)
        assert data["state"] == "open"
        assert data["category"] == "open"
        assert data["type"] == "task"
        assert "inbound_transitions" in data
        assert "outbound_transitions" in data
        assert "required_fields" in data

    async def test_explain_state_unknown_type(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("explain_state", {"type": "nonexistent", "state": "open"})
        data = _parse(result)
        assert data["code"] == "not_found"

    async def test_explain_state_unknown_state(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("explain_state", {"type": "task", "state": "nonexistent"})
        data = _parse(result)
        assert data["code"] == "not_found"

    async def test_reload_templates(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("reload_templates", {})
        data = _parse(result)
        assert data["status"] == "ok"
        # Templates should still work after reload
        result2 = await call_tool("list_types", {})
        data2 = _parse(result2)
        assert len(data2) >= 2


class TestMCPMutationEnhancements:
    """Tests for Batch 2 — enhanced error handling and new features."""

    async def test_get_issue_with_transitions(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Test transitions", type="task")
        result = await call_tool("get_issue", {"id": issue.id, "include_transitions": True})
        data = _parse(result)
        assert data["title"] == "Test transitions"
        assert "valid_transitions" in data
        assert isinstance(data["valid_transitions"], list)
        assert len(data["valid_transitions"]) >= 1

    async def test_get_issue_without_transitions(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("No transitions")
        result = await call_tool("get_issue", {"id": issue.id})
        data = _parse(result)
        assert "valid_transitions" not in data

    async def test_list_issues_status_category(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Open one")
        mcp_db.create_issue("WIP one")
        mcp_db.update_issue(mcp_db.list_issues()[-1].id, status="in_progress")
        result = await call_tool("list_issues", {"status_category": "open"})
        data = _parse(result)
        issues = data["issues"]
        # Should return open issues (not in_progress ones)
        statuses = {d["status"] for d in issues}
        assert "in_progress" not in statuses
        assert a.id in [d["id"] for d in issues]

    async def test_update_issue_error_includes_transitions(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Error test", type="bug")
        # Try an invalid status
        result = await call_tool("update_issue", {"id": issue.id, "status": "nonexistent_state"})
        data = _parse(result)
        assert data["code"] == "invalid_transition"
        assert "valid_transitions" in data
        assert "hint" in data

    async def test_claim_next_success(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("Ready task", type="task", priority=1)
        result = await call_tool("claim_next", {"assignee": "agent-1"})
        data = _parse(result)
        assert data["assignee"] == "agent-1"
        assert data["title"] == "Ready task"

    async def test_claim_next_empty(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("claim_next", {"assignee": "agent-1"})
        data = _parse(result)
        assert data["status"] == "empty"

    async def test_claim_next_with_type_filter(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("A task", type="task")
        mcp_db.create_issue("A bug", type="bug")
        result = await call_tool("claim_next", {"assignee": "agent-1", "type": "bug"})
        data = _parse(result)
        assert data["type"] == "bug"

    async def test_claim_next_with_priority_filter(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("Low priority", type="task", priority=4)
        mcp_db.create_issue("High priority", type="task", priority=0)
        result = await call_tool("claim_next", {"assignee": "agent-1", "priority_max": 2})
        data = _parse(result)
        assert data["priority"] <= 2

    async def test_batch_close_partial_failure(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Closeable")
        result = await call_tool("batch_close", {"ids": [a.id, "nonexistent-xyz"]})
        data = _parse(result)
        assert data["count"] == 1
        assert a.id in data["succeeded"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["id"] == "nonexistent-xyz"

    async def test_batch_update_partial_failure(self, mcp_db: FiligreeDB) -> None:
        a = mcp_db.create_issue("Updatable")
        result = await call_tool("batch_update", {"ids": [a.id, "nonexistent-xyz"], "priority": 0})
        data = _parse(result)
        assert data["count"] == 1
        assert a.id in data["succeeded"]
        assert len(data["failed"]) == 1


class TestCoreReloadTemplates:
    """Test core.py reload_templates method."""

    def test_reload_clears_registry(self, mcp_db: FiligreeDB) -> None:
        # Access templates to load them
        _ = mcp_db.templates.list_types()
        assert mcp_db._template_registry is not None
        mcp_db.reload_templates()
        assert mcp_db._template_registry is None
        # Should reload on next access
        types = mcp_db.templates.list_types()
        assert len(types) >= 2


class TestDynamicWorkflowPrompt:
    """Test the dynamic workflow prompt builder."""

    async def test_workflow_prompt_includes_types(self, mcp_db: FiligreeDB) -> None:
        result = await get_workflow_prompt("filigree-workflow", {"include_context": "false"})
        assert len(result.messages) >= 1
        text = result.messages[0].content.text
        assert "task" in text.lower()
        assert "Registered Types" in text or "Key tools" in text

    async def test_build_workflow_text_error_logs_at_error_level(self, mcp_db: FiligreeDB) -> None:
        """Bug filigree-964d67: template registry failures must log at error, not warning."""
        import logging

        import filigree.mcp_server as mcp_mod

        original = mcp_mod._get_db

        def _boom() -> None:
            raise RuntimeError("template registry broken")

        mcp_mod._get_db = _boom
        try:
            logger = logging.getLogger("filigree.mcp_server")
            with pytest.MonkeyPatch.context() as mp:
                logged_errors: list[str] = []
                logged_warnings: list[str] = []
                mp.setattr(logger, "error", lambda msg, *a, **kw: logged_errors.append(msg))
                mp.setattr(logger, "warning", lambda msg, *a, **kw: logged_warnings.append(msg))
                from filigree.mcp_server import _build_workflow_text

                text = _build_workflow_text()
            assert "Dynamic workflow info unavailable" in text
            assert len(logged_errors) == 1
            assert logged_warnings == []
        finally:
            mcp_mod._get_db = original

    async def test_workflow_prompt_fallback_without_db(self) -> None:
        import filigree.mcp_server as mcp_mod

        original_db = mcp_mod.db
        mcp_mod.db = None
        try:
            result = await get_workflow_prompt("filigree-workflow", {"include_context": "false"})
            text = result.messages[0].content.text
            assert "Filigree Workflow" in text
        finally:
            mcp_mod.db = original_db


class TestPromptRuntimeErrorNarrowing:
    """Bug filigree-0458c5: get_workflow_prompt should only silence 'not initialized' RuntimeError."""

    async def test_unexpected_runtime_error_is_logged(self, mcp_db: FiligreeDB) -> None:
        """Non-initialization RuntimeErrors must be logged at error level, not swallowed."""
        import logging

        import filigree.mcp_server as mcp_mod

        original = mcp_mod.generate_summary

        def _boom(_db: object) -> str:
            raise RuntimeError("maximum recursion depth exceeded")

        mcp_mod.generate_summary = _boom
        try:
            logger = logging.getLogger("filigree.mcp_server")
            with pytest.MonkeyPatch.context() as mp:
                logged_errors: list[str] = []
                mp.setattr(logger, "error", lambda msg, *a, **kw: logged_errors.append(msg))
                result = await get_workflow_prompt("filigree-workflow", {"include_context": "true"})
            assert len(result.messages) >= 1  # prompt still returned
            assert any("Unexpected" in e for e in logged_errors)
        finally:
            mcp_mod.generate_summary = original

    async def test_not_initialized_error_is_silenced(self, mcp_db: FiligreeDB) -> None:
        """'not initialized' RuntimeError should be silently ignored (expected at startup)."""
        import logging

        import filigree.mcp_server as mcp_mod

        original = mcp_mod.generate_summary

        def _not_init(_db: object) -> str:
            raise RuntimeError("DB not initialized")

        mcp_mod.generate_summary = _not_init
        try:
            logger = logging.getLogger("filigree.mcp_server")
            with pytest.MonkeyPatch.context() as mp:
                logged_errors: list[str] = []
                mp.setattr(logger, "error", lambda msg, *a, **kw: logged_errors.append(msg))
                result = await get_workflow_prompt("filigree-workflow", {"include_context": "true"})
            assert len(result.messages) == 1  # no context appended
            assert logged_errors == []  # no error logged
        finally:
            mcp_mod.generate_summary = original


class TestInstructionsUpdate:
    """Test that FILIGREE_INSTRUCTIONS includes workflow commands."""

    def test_instructions_include_types(self) -> None:
        from filigree.install import FILIGREE_INSTRUCTIONS

        assert "filigree types" in FILIGREE_INSTRUCTIONS
        assert "filigree type-info" in FILIGREE_INSTRUCTIONS
        assert "filigree transitions" in FILIGREE_INSTRUCTIONS
        assert "filigree packs" in FILIGREE_INSTRUCTIONS
        assert "filigree validate" in FILIGREE_INSTRUCTIONS
        assert "filigree guide" in FILIGREE_INSTRUCTIONS


class TestSafePath:
    """Tests for the _safe_path() path traversal guard."""

    def test_rejects_absolute_path(self, mcp_db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Absolute paths not allowed"):
            _safe_path("/etc/passwd")

    def test_rejects_dotdot_escape(self, mcp_db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Path escapes project directory"):
            _safe_path("../../etc/passwd")

    def test_rejects_dotdot_in_middle(self, mcp_db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Path escapes project directory"):
            _safe_path("subdir/../../etc/passwd")

    def test_allows_valid_relative_path(self, mcp_db: FiligreeDB) -> None:
        result = _safe_path("backup.jsonl")
        assert result.name == "backup.jsonl"

    def test_allows_subdirectory_path(self, mcp_db: FiligreeDB) -> None:
        result = _safe_path("backups/export.jsonl")
        assert result.name == "export.jsonl"
        assert "backups" in str(result)

    def test_rejects_another_absolute_path(self, mcp_db: FiligreeDB) -> None:
        """Absolute paths on any platform should be rejected."""
        with pytest.raises(ValueError, match="Absolute paths not allowed"):
            _safe_path("/var/data/evil.jsonl")

    def test_project_not_initialized(self) -> None:
        """_safe_path fails gracefully when _filigree_dir is None."""
        import filigree.mcp_server as mcp_mod

        original = mcp_mod._filigree_dir
        mcp_mod._filigree_dir = None
        try:
            with pytest.raises(ValueError, match="Project directory not initialized"):
                _safe_path("test.jsonl")
        finally:
            mcp_mod._filigree_dir = original


class TestExportImportPathTraversal:
    """Tests that export_jsonl and import_jsonl MCP tools reject unsafe paths."""

    async def test_export_rejects_absolute_path(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("export_jsonl", {"output_path": "/var/data/evil.jsonl"})
        data = _parse(result)
        assert data["code"] == "invalid_path"
        assert "Absolute paths not allowed" in data["error"]

    async def test_export_rejects_path_traversal(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("export_jsonl", {"output_path": "../../evil.jsonl"})
        data = _parse(result)
        assert data["code"] == "invalid_path"
        assert "escapes project directory" in data["error"]

    async def test_import_rejects_absolute_path(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("import_jsonl", {"input_path": "/etc/passwd"})
        data = _parse(result)
        assert data["code"] == "invalid_path"
        assert "Absolute paths not allowed" in data["error"]

    async def test_import_rejects_path_traversal(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("import_jsonl", {"input_path": "../../../etc/passwd"})
        data = _parse(result)
        assert data["code"] == "invalid_path"
        assert "escapes project directory" in data["error"]

    async def test_export_allows_valid_relative_path(self, mcp_db: FiligreeDB) -> None:
        # Create an issue so there's data to export
        mcp_db.create_issue("Export test")
        result = await call_tool("export_jsonl", {"output_path": "test-export.jsonl"})
        data = _parse(result)
        assert data["status"] == "ok"
        assert data["records"] >= 1

    async def test_export_io_error_returns_structured_error(self, mcp_db: FiligreeDB) -> None:
        """export_jsonl to a nonexistent directory must return error, not crash."""
        result = await call_tool("export_jsonl", {"output_path": "nonexistent-dir/out.jsonl"})
        data = _parse(result)
        assert "error" in data
        assert data["code"] == "io_error"

    async def test_import_malformed_jsonl_returns_parse_error_not_invalid_path(self, mcp_db: FiligreeDB) -> None:
        """import_jsonl with malformed JSONL must not report 'invalid_path'."""
        project_root = mcp_db.db_path.parent.parent
        bad_file = project_root / "bad.jsonl"
        bad_file.write_text("this is not valid json\n")
        result = await call_tool("import_jsonl", {"input_path": "bad.jsonl"})
        data = _parse(result)
        assert "error" in data
        assert data["code"] != "invalid_path"

    async def test_import_jsonl_unexpected_exception_propagates(self, mcp_db: FiligreeDB) -> None:
        """Unexpected exceptions (not ValueError/OSError/sqlite3.Error) must propagate."""
        project_root = mcp_db.db_path.parent.parent
        valid_file = project_root / "valid.jsonl"
        valid_file.write_text("")  # empty is fine for path validation

        with (
            patch.object(mcp_db, "import_jsonl", side_effect=RuntimeError("unexpected internal error")),
            pytest.raises(RuntimeError, match="unexpected internal error"),
        ):
            await call_tool("import_jsonl", {"input_path": "valid.jsonl"})

    async def test_import_jsonl_expected_exceptions_handled_gracefully(self, mcp_db: FiligreeDB) -> None:
        """ValueError, OSError, sqlite3.Error must be caught and return structured error."""
        import sqlite3

        project_root = mcp_db.db_path.parent.parent
        valid_file = project_root / "valid.jsonl"
        valid_file.write_text("")

        for exc_type in (ValueError, OSError, sqlite3.OperationalError):
            with patch.object(mcp_db, "import_jsonl", side_effect=exc_type("test error")):
                result = await call_tool("import_jsonl", {"input_path": "valid.jsonl"})
                data = _parse(result)
                assert "error" in data, f"{exc_type.__name__} must be caught gracefully"
                assert data["code"] == "import_error"


class TestRefreshSummaryLogging:
    """Bug filigree-c13236: _refresh_summary must log even when _logger is None."""

    async def test_refresh_summary_logs_when_logger_is_none(self, mcp_db: FiligreeDB) -> None:
        """When _logger is None, fallback to logging.getLogger(__name__)."""
        import filigree.mcp_server as mcp_mod

        original_logger = mcp_mod._logger
        mcp_mod._logger = None

        try:
            with (
                patch.object(mcp_mod, "write_summary", side_effect=OSError("disk full")),
                patch("logging.getLogger") as mock_get_logger,
            ):
                mock_fallback = MagicMock()
                mock_get_logger.return_value = mock_fallback
                mcp_mod._refresh_summary()
                mock_fallback.warning.assert_called_once()
        finally:
            mcp_mod._logger = original_logger


class TestRefreshSummaryErrorEscalation:
    """Bug filigree-e45c0d: _refresh_summary must log at error for non-OSError exceptions."""

    async def test_db_error_logged_at_error_level(self, mcp_db: FiligreeDB) -> None:
        import sqlite3

        import filigree.mcp_server as mcp_mod

        mock_logger = MagicMock()
        original_logger = mcp_mod._logger
        mcp_mod._logger = mock_logger

        try:
            with patch.object(mcp_mod, "write_summary", side_effect=sqlite3.DatabaseError("database disk image is malformed")):
                mcp_mod._refresh_summary()
                mock_logger.error.assert_called_once()
                assert "malformed" in str(mock_logger.error.call_args) or "context.md" in str(mock_logger.error.call_args)
        finally:
            mcp_mod._logger = original_logger

    async def test_os_error_still_logged_at_warning(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        mock_logger = MagicMock()
        original_logger = mcp_mod._logger
        mcp_mod._logger = mock_logger

        try:
            with patch.object(mcp_mod, "write_summary", side_effect=OSError("disk full")):
                mcp_mod._refresh_summary()
                mock_logger.warning.assert_called_once()
                mock_logger.error.assert_not_called()
        finally:
            mcp_mod._logger = original_logger


class TestMCPTransactionSafety:
    """MCP-level safety net: no dirty transactions survive after failed tool calls."""

    async def test_failed_create_no_dirty_transaction(self, mcp_db: FiligreeDB) -> None:
        """create_issue with invalid deps returns error AND leaves no dirty txn."""
        result = await call_tool(
            "create_issue",
            {"title": "Should fail", "deps": ["nonexistent-dep-id"]},
        )
        data = _parse(result)
        assert "error" in data

        assert not mcp_db.conn.in_transaction, (
            "Dirty transaction left after failed create_issue — next successful commit would flush orphaned writes"
        )

    async def test_failed_update_no_dirty_transaction(self, mcp_db: FiligreeDB) -> None:
        """update_issue with invalid priority returns error AND leaves no dirty txn."""
        create_result = await call_tool("create_issue", {"title": "Valid issue"})
        issue_id = _parse(create_result)["id"]

        result = await call_tool(
            "update_issue",
            {"id": issue_id, "title": "New title", "priority": 99},
        )
        data = _parse(result)
        assert "error" in data

        assert not mcp_db.conn.in_transaction, (
            "Dirty transaction left after failed update_issue — next successful commit would flush orphaned events"
        )

    async def test_unhandled_error_rolls_back_dirty_transaction(self, mcp_db: FiligreeDB) -> None:
        """Safety net: unhandled exception from _dispatch rolls back any dirty txn.

        Simulates a core function that writes to the DB then raises without
        rolling back — the MCP call_tool() safety net must clean up.
        """

        async def _bad_dispatch(name: str, arguments: dict[str, Any], tracker: FiligreeDB) -> list[Any]:
            # Simulate a buggy mutation: INSERT a row, then crash
            tracker.conn.execute(
                "INSERT INTO issues (id, title, type, status, priority, description, "
                "notes, assignee, parent_id, fields, created_at, updated_at) "
                "VALUES ('orphan-1', 'Orphan', 'task', 'open', 2, '', '', '', NULL, "
                "'{}', '2026-01-01', '2026-01-01')"
            )
            msg = "Simulated unprotected crash"
            raise RuntimeError(msg)

        with patch("filigree.mcp_server._dispatch", _bad_dispatch), pytest.raises(RuntimeError):
            await call_tool("create_issue", {"title": "Irrelevant"})

        # The safety net in call_tool() should have rolled back the dirty txn
        assert not mcp_db.conn.in_transaction, "MCP safety net failed — dirty transaction survived after unhandled exception"

        # The orphan row should NOT be visible after rollback
        orphan = mcp_db.conn.execute("SELECT id FROM issues WHERE id = 'orphan-1'").fetchone()
        assert orphan is None, "Orphan issue row survived — rollback did not happen"


class TestFileTools:
    """Tests for MCP file registration, association, and retrieval tools."""

    async def test_list_tools_includes_file_tools(self, mcp_db: FiligreeDB) -> None:
        tools = await list_tools()
        names = {t.name for t in tools}
        assert {
            "list_files",
            "get_file",
            "get_file_timeline",
            "get_issue_files",
            "add_file_association",
            "register_file",
        }.issubset(names)

    async def test_create_issue_tool_docs_call_out_labels_at_creation(self, mcp_db: FiligreeDB) -> None:
        tools = await list_tools()
        create_tool = next(t for t in tools if t.name == "create_issue")
        assert "labels" in create_tool.description.lower()
        labels_schema = (create_tool.inputSchema or {}).get("properties", {}).get("labels", {})
        assert "creation" in (labels_schema.get("description") or "").lower()

    async def test_register_file_and_get_file_round_trip(self, mcp_db: FiligreeDB) -> None:
        created = _parse(await call_tool("register_file", {"path": "src/example.py", "language": "python"}))
        assert "error" not in created
        assert created["path"] == "src/example.py"
        assert created["id"]

        detail = _parse(await call_tool("get_file", {"file_id": created["id"]}))
        assert detail["file"]["id"] == created["id"]
        assert detail["file"]["path"] == "src/example.py"

    async def test_register_file_is_idempotent_by_path(self, mcp_db: FiligreeDB) -> None:
        first = _parse(await call_tool("register_file", {"path": "src/idempotent.py"}))
        second = _parse(await call_tool("register_file", {"path": "./src/idempotent.py"}))
        assert first["id"] == second["id"]
        assert second["path"] == "src/idempotent.py"

    async def test_register_file_path_traversal_rejected(self, mcp_db: FiligreeDB) -> None:
        result = _parse(await call_tool("register_file", {"path": "../../etc/passwd"}))
        assert result["code"] == "invalid_path"

    async def test_list_files_with_filters(self, mcp_db: FiligreeDB) -> None:
        await call_tool("register_file", {"path": "src/a.py", "language": "python"})
        await call_tool("register_file", {"path": "docs/readme.md", "language": "markdown"})
        result = _parse(await call_tool("list_files", {"path_prefix": "src/", "limit": 10}))
        assert result["total"] == 1
        assert result["results"][0]["path"] == "src/a.py"

    async def test_list_files_invalid_sort_rejected(self, mcp_db: FiligreeDB) -> None:
        result = _parse(await call_tool("list_files", {"sort": "bad_sort"}))
        assert result["code"] == "validation_error"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("language", {"bad": "type"}),
            ("path_prefix", {"bad": "type"}),
            ("scan_source", {"bad": "type"}),
        ],
    )
    async def test_list_files_optional_string_filters_reject_non_strings(
        self, mcp_db: FiligreeDB, field: str, value: dict[str, str]
    ) -> None:
        result = _parse(await call_tool("list_files", {field: value}))
        assert result["code"] == "validation_error"
        assert result["error"] == f"{field} must be a string"

    async def test_add_file_association_and_get_issue_files(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Issue for file association")
        file_data = _parse(await call_tool("register_file", {"path": "src/assoc.py"}))

        created = _parse(
            await call_tool(
                "add_file_association",
                {
                    "file_id": file_data["id"],
                    "issue_id": issue.id,
                    "assoc_type": "task_for",
                },
            )
        )
        assert created["status"] == "created"

        files = _parse(await call_tool("get_issue_files", {"issue_id": issue.id}))
        assert len(files) == 1
        assert files[0]["file_id"] == file_data["id"]
        assert files[0]["assoc_type"] == "task_for"

    async def test_add_file_association_invalid_assoc_type(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Issue for invalid assoc")
        file_data = _parse(await call_tool("register_file", {"path": "src/invalid_assoc.py"}))
        result = _parse(
            await call_tool(
                "add_file_association",
                {
                    "file_id": file_data["id"],
                    "issue_id": issue.id,
                    "assoc_type": "invalid_assoc",
                },
            )
        )
        assert result["code"] == "validation_error"

    async def test_add_file_association_file_not_found(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Issue for missing file")
        result = _parse(
            await call_tool(
                "add_file_association",
                {
                    "file_id": "missing-file-id",
                    "issue_id": issue.id,
                    "assoc_type": "task_for",
                },
            )
        )
        assert result["code"] == "not_found"

    async def test_get_issue_files_not_found(self, mcp_db: FiligreeDB) -> None:
        result = _parse(await call_tool("get_issue_files", {"issue_id": "nonexistent-xyz"}))
        assert result["code"] == "not_found"

    async def test_get_file_timeline_for_association_event(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Issue for timeline")
        file_data = _parse(await call_tool("register_file", {"path": "src/timeline.py"}))
        _parse(
            await call_tool(
                "add_file_association",
                {
                    "file_id": file_data["id"],
                    "issue_id": issue.id,
                    "assoc_type": "mentioned_in",
                },
            )
        )

        timeline = _parse(
            await call_tool(
                "get_file_timeline",
                {"file_id": file_data["id"], "event_type": "association"},
            )
        )
        assert timeline["total"] >= 1
        assert any(e["type"] == "association_created" for e in timeline["results"])

    async def test_get_file_timeline_invalid_event_type(self, mcp_db: FiligreeDB) -> None:
        file_data = _parse(await call_tool("register_file", {"path": "src/timeline_invalid.py"}))
        result = _parse(
            await call_tool(
                "get_file_timeline",
                {"file_id": file_data["id"], "event_type": "bogus"},
            )
        )
        assert result["code"] == "validation_error"


class TestScannerTools:
    """Tests for list_scanners and trigger_scan MCP tools."""

    def _write_scanner_toml(self, mcp_db: FiligreeDB, name: str = "test-scanner") -> None:
        """Helper: write a scanner TOML into the test .filigree/scanners/ dir."""
        import filigree.mcp_server as mcp_mod

        scanners_dir = mcp_mod._filigree_dir / "scanners"
        scanners_dir.mkdir(exist_ok=True)
        (scanners_dir / f"{name}.toml").write_text(
            f'[scanner]\nname = "{name}"\ndescription = "Test scanner"\n'
            # Use 'echo' as the command — exists on all systems, exits immediately
            f'command = "echo"\nargs = ["scan", "{{file}}", "--scan-run-id", "{{scan_run_id}}"]\nfile_types = ["py"]\n'
        )

    async def test_list_scanners_empty(self, mcp_db: FiligreeDB) -> None:
        result = _parse(await call_tool("list_scanners", {}))
        assert result["scanners"] == []

    async def test_list_scanners_with_registry(self, mcp_db: FiligreeDB) -> None:
        self._write_scanner_toml(mcp_db)
        result = _parse(await call_tool("list_scanners", {}))
        assert len(result["scanners"]) == 1
        assert result["scanners"][0]["name"] == "test-scanner"

    async def test_trigger_scan_scanner_not_found(self, mcp_db: FiligreeDB) -> None:
        result = _parse(
            await call_tool(
                "trigger_scan",
                {
                    "scanner": "nonexistent",
                    "file_path": "src/foo.py",
                },
            )
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_trigger_scan_malformed_scanner_returns_not_found(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        scanners_dir = mcp_mod._filigree_dir / "scanners"
        scanners_dir.mkdir(exist_ok=True)
        (scanners_dir / "bad.toml").write_text(
            '[scanner]\nname = "bad"\ndescription = "bad scanner"\ncommand = "echo"\nargs = "not-a-list"\nfile_types = ["py"]\n'
        )

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "bad_target.py"
        try:
            target.write_text("x = 1\n")
            result = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "bad",
                        "file_path": "bad_target.py",
                    },
                )
            )
            assert result["code"] == "scanner_not_found"
            assert "bad" not in result.get("available_scanners", [])
        finally:
            target.unlink(missing_ok=True)
            (scanners_dir / "bad.toml").unlink(missing_ok=True)
            mcp_mod._scan_cooldowns.clear()

    async def test_trigger_scan_path_traversal_rejected(self, mcp_db: FiligreeDB) -> None:
        self._write_scanner_toml(mcp_db)
        result = _parse(
            await call_tool(
                "trigger_scan",
                {
                    "scanner": "test-scanner",
                    "file_path": "../../etc/passwd",
                },
            )
        )
        assert "error" in result
        assert result["code"] == "invalid_path"

    async def test_trigger_scan_scanner_name_traversal_rejected(self, mcp_db: FiligreeDB) -> None:
        result = _parse(
            await call_tool(
                "trigger_scan",
                {
                    "scanner": "../../../etc/crontab",
                    "file_path": "src/foo.py",
                },
            )
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_trigger_scan_non_localhost_api_url_rejected(self, mcp_db: FiligreeDB) -> None:
        """Security: non-localhost api_url must be rejected to prevent result exfiltration."""
        self._write_scanner_toml(mcp_db)
        result = _parse(
            await call_tool(
                "trigger_scan",
                {
                    "scanner": "test-scanner",
                    "file_path": "src/foo.py",
                    "api_url": "https://evil.example.com:8377",
                },
            )
        )
        assert result["code"] == "invalid_api_url"
        assert "non-localhost" in result["error"].lower() or "not allowed" in result["error"].lower()

    async def test_trigger_scan_file_not_found(self, mcp_db: FiligreeDB) -> None:
        self._write_scanner_toml(mcp_db)
        result = _parse(
            await call_tool(
                "trigger_scan",
                {
                    "scanner": "test-scanner",
                    "file_path": "nonexistent/file.py",
                },
            )
        )
        assert "error" in result
        assert "not found" in result["error"].lower() or "not exist" in result["error"].lower()

    async def test_trigger_scan_success(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "test_target.py"
        try:
            target.write_text("x = 1\n")
            self._write_scanner_toml(mcp_db)
            result = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "test-scanner",
                        "file_path": "test_target.py",
                    },
                )
            )
            assert "error" not in result
            assert result["scanner"] == "test-scanner"
            assert result["file_path"] == "test_target.py"
            assert "file_id" in result
            assert "scan_run_id" in result
            assert result["file_id"] != ""

            # Verify the file was registered in file_records
            f = mcp_db.get_file_by_path("test_target.py")
            assert f is not None
        finally:
            target.unlink(missing_ok=True)

    async def test_trigger_scan_uses_canonical_path_in_scanner_command(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        class _Proc:
            pid = 12345

            @staticmethod
            def poll() -> None:
                return None

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "canonical_target.py"
        try:
            target.write_text("x = 1\n")
            self._write_scanner_toml(mcp_db)
            with patch("filigree.mcp_server.subprocess.Popen", return_value=_Proc()) as popen:
                result = _parse(
                    await call_tool(
                        "trigger_scan",
                        {
                            "scanner": "test-scanner",
                            "file_path": "./canonical_target.py",
                        },
                    )
                )
            assert result.get("status") == "triggered"
            cmd = popen.call_args.args[0]
            assert "canonical_target.py" in cmd
            assert "./canonical_target.py" not in cmd
        finally:
            target.unlink(missing_ok=True)
            mcp_mod._scan_cooldowns.clear()

    async def test_trigger_scan_registers_file_idempotent(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "existing.py"
        try:
            target.write_text("y = 2\n")
            existing = mcp_db.register_file("existing.py", language="python")
            self._write_scanner_toml(mcp_db)
            result = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "test-scanner",
                        "file_path": "existing.py",
                    },
                )
            )
            assert result["file_id"] == existing.id
        finally:
            target.unlink(missing_ok=True)

    async def test_trigger_scan_rate_limited(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "rate_test.py"
        try:
            target.write_text("z = 3\n")
            self._write_scanner_toml(mcp_db)
            # First call succeeds
            result1 = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "test-scanner",
                        "file_path": "rate_test.py",
                    },
                )
            )
            assert result1.get("status") == "triggered"

            # Immediate second call should be rate-limited
            result2 = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "test-scanner",
                        "file_path": "rate_test.py",
                    },
                )
            )
            assert result2["code"] == "rate_limited"
        finally:
            target.unlink(missing_ok=True)
            # Clear cooldown state for test isolation
            mcp_mod._scan_cooldowns.clear()

    async def test_trigger_scan_file_type_mismatch_warning(self, mcp_db: FiligreeDB) -> None:
        """Scanning a .txt file with a py-only scanner should succeed with a warning."""
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "readme.txt"
        try:
            target.write_text("hello\n")
            self._write_scanner_toml(mcp_db)  # file_types = ["py"]
            result = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "test-scanner",
                        "file_path": "readme.txt",
                    },
                )
            )
            assert result.get("status") == "triggered"
            assert "warning" in result
            assert "txt" in result["warning"]
        finally:
            target.unlink(missing_ok=True)
            mcp_mod._scan_cooldowns.clear()

    async def test_trigger_scan_allows_templated_executable_path(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "templated_exec_target.py"
        scanner_exec = project_root / "scanner_exec.sh"
        scanners_dir = mcp_mod._filigree_dir / "scanners"
        scanners_dir.mkdir(exist_ok=True)

        try:
            target.write_text("x = 1\n")
            scanner_exec.write_text("#!/usr/bin/env bash\nexit 0\n")
            scanner_exec.chmod(0o755)
            (scanners_dir / "templated-scanner.toml").write_text(
                '[scanner]\nname = "templated-scanner"\ndescription = "Templated executable"\n'
                'command = "{project_root}/scanner_exec.sh"\n'
                'args = ["{file}", "--scan-run-id", "{scan_run_id}"]\nfile_types = ["py"]\n'
            )

            result = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "templated-scanner",
                        "file_path": "templated_exec_target.py",
                    },
                )
            )
            assert result.get("status") == "triggered"
        finally:
            target.unlink(missing_ok=True)
            scanner_exec.unlink(missing_ok=True)
            (scanners_dir / "templated-scanner.toml").unlink(missing_ok=True)
            mcp_mod._scan_cooldowns.clear()

    async def test_trigger_scan_allows_project_relative_executable_path(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "relative_exec_target.py"
        scanner_exec = project_root / "scanner_exec.sh"
        scanners_dir = mcp_mod._filigree_dir / "scanners"
        scanners_dir.mkdir(exist_ok=True)

        try:
            target.write_text("x = 1\n")
            scanner_exec.write_text("#!/usr/bin/env bash\nexit 0\n")
            scanner_exec.chmod(0o755)
            (scanners_dir / "relative-scanner.toml").write_text(
                '[scanner]\nname = "relative-scanner"\ndescription = "Relative executable"\n'
                'command = "./scanner_exec.sh"\n'
                'args = ["{file}", "--scan-run-id", "{scan_run_id}"]\nfile_types = ["py"]\n'
            )

            result = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "relative-scanner",
                        "file_path": "relative_exec_target.py",
                    },
                )
            )
            assert result.get("status") == "triggered"
        finally:
            target.unlink(missing_ok=True)
            scanner_exec.unlink(missing_ok=True)
            (scanners_dir / "relative-scanner.toml").unlink(missing_ok=True)
            mcp_mod._scan_cooldowns.clear()

    async def test_trigger_scan_response_includes_log_path(self, mcp_db: FiligreeDB) -> None:
        """trigger_scan must include log_path in response and create the log file."""
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "log_target.py"
        try:
            target.write_text("x = 1\n")
            self._write_scanner_toml(mcp_db)
            result = _parse(
                await call_tool(
                    "trigger_scan",
                    {"scanner": "test-scanner", "file_path": "log_target.py"},
                )
            )
            assert "error" not in result
            assert "log_path" in result, "Response must include log_path for diagnostics"
            assert result["log_path"].endswith(".log")

            # Verify the log file was actually created on disk
            scan_log = mcp_mod._filigree_dir / "scans"
            assert scan_log.is_dir(), ".filigree/scans/ directory must exist"
            log_files = list(scan_log.glob("*.log"))
            assert len(log_files) >= 1, "At least one scan log file must be created"
        finally:
            target.unlink(missing_ok=True)

    async def test_trigger_scan_closes_log_fd_after_popen(self, mcp_db: FiligreeDB) -> None:
        """Bug filigree-dfe017: scan_log_fd must be closed after Popen to prevent fd leak."""
        import filigree.mcp_server as mcp_mod

        class _Proc:
            pid = 12345

            @staticmethod
            def poll() -> None:
                return None

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "fd_leak_target.py"
        try:
            target.write_text("x = 1\n")
            self._write_scanner_toml(mcp_db)
            mock_fd = MagicMock()
            mock_fd.__enter__ = MagicMock(return_value=mock_fd)
            mock_fd.__exit__ = MagicMock(return_value=False)
            original_open = builtins.open

            def _spy_open(path, *args, **kwargs):
                if str(path).endswith(".log"):
                    return mock_fd
                return original_open(path, *args, **kwargs)

            with (
                patch("filigree.mcp_server.subprocess.Popen", return_value=_Proc()),
                patch("builtins.open", side_effect=_spy_open),
            ):
                result = _parse(
                    await call_tool(
                        "trigger_scan",
                        {"scanner": "test-scanner", "file_path": "fd_leak_target.py"},
                    )
                )
            assert "error" not in result, f"trigger_scan failed: {result}"
            mock_fd.close.assert_called_once(), "scan_log_fd must be closed after Popen succeeds"
        finally:
            target.unlink(missing_ok=True)
            mcp_mod._scan_cooldowns.clear()

    async def test_trigger_scan_cooldown_is_scoped_per_project(self, tmp_path: Path) -> None:
        import filigree.mcp_server as mcp_mod

        original_db = mcp_mod.db
        original_dir = mcp_mod._filigree_dir
        mcp_mod._scan_cooldowns.clear()

        def _make_project(name: str, prefix: str) -> tuple[FiligreeDB, Path]:
            project_root = tmp_path / name
            filigree_dir = project_root / FILIGREE_DIR_NAME
            filigree_dir.mkdir(parents=True)
            write_config(filigree_dir, {"prefix": prefix, "version": 1})
            (filigree_dir / SUMMARY_FILENAME).write_text("# test\n")
            db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=prefix)
            db.initialize()

            scanners_dir = filigree_dir / "scanners"
            scanners_dir.mkdir(exist_ok=True)
            (scanners_dir / "test-scanner.toml").write_text(
                '[scanner]\nname = "test-scanner"\ndescription = "Test scanner"\n'
                'command = "echo"\nargs = ["scan", "{file}", "--scan-run-id", "{scan_run_id}"]\nfile_types = ["py"]\n'
            )
            (project_root / "shared.py").write_text("x = 1\n")
            return db, filigree_dir

        db_a, dir_a = _make_project("proj-a", "alpha")
        db_b, dir_b = _make_project("proj-b", "bravo")

        try:
            mcp_mod.db = db_a
            mcp_mod._filigree_dir = dir_a
            result_a = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "test-scanner",
                        "file_path": "shared.py",
                    },
                )
            )
            assert result_a.get("status") == "triggered"

            mcp_mod.db = db_b
            mcp_mod._filigree_dir = dir_b
            result_b = _parse(
                await call_tool(
                    "trigger_scan",
                    {
                        "scanner": "test-scanner",
                        "file_path": "shared.py",
                    },
                )
            )
            assert result_b.get("status") == "triggered"
        finally:
            mcp_mod.db = original_db
            mcp_mod._filigree_dir = original_dir
            mcp_mod._scan_cooldowns.clear()
            db_a.close()
            db_b.close()


class TestHttpMcpRequestContext:
    """Regression tests for per-request DB and project directory isolation."""

    @staticmethod
    def _make_project(tmp_path: Path, name: str, prefix: str) -> tuple[FiligreeDB, Path]:
        project_root = tmp_path / name
        filigree_dir = project_root / FILIGREE_DIR_NAME
        filigree_dir.mkdir(parents=True)
        write_config(filigree_dir, {"prefix": prefix, "version": 1})
        (filigree_dir / SUMMARY_FILENAME).write_text("# test\n")
        db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=prefix)
        db.initialize()
        return db, filigree_dir

    async def test_create_mcp_app_uses_request_scoped_db_and_dir(self, tmp_path: Path) -> None:
        import filigree.mcp_server as mcp_mod

        db_global, dir_global = self._make_project(tmp_path, "global", "global")
        db_a, dir_a = self._make_project(tmp_path, "proj-a", "alpha")
        db_b, dir_b = self._make_project(tmp_path, "proj-b", "bravo")

        original_db = mcp_mod.db
        original_dir = mcp_mod._filigree_dir
        mcp_mod.db = db_global
        mcp_mod._filigree_dir = dir_global

        selected_key: ContextVar[str] = ContextVar("selected_key", default="a")
        mapping = {"a": db_a, "b": db_b}

        started_a = asyncio.Event()
        started_b = asyncio.Event()
        observed: dict[str, Any] = {}

        class FakeSessionManager:
            def __init__(self, app: Any, json_response: bool, stateless: bool) -> None:
                self.app = app
                self.json_response = json_response
                self.stateless = stateless

            @asynccontextmanager
            async def run(self) -> Any:
                yield

            async def handle_request(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
                key = selected_key.get()
                current_db = mcp_mod._get_db()
                current_dir = mcp_mod._get_filigree_dir()
                safe_parent = mcp_mod._safe_path("nested/file.py").parent

                observed[f"{key}_before"] = (current_db, current_dir, safe_parent)
                if key == "a":
                    started_a.set()
                    await started_b.wait()
                else:
                    await started_a.wait()
                    started_b.set()

                await asyncio.sleep(0)
                observed[f"{key}_after"] = (
                    mcp_mod._get_db(),
                    mcp_mod._get_filigree_dir(),
                    mcp_mod._safe_path("nested/file.py").parent,
                )

        def _resolver() -> FiligreeDB:
            return mapping[selected_key.get()]

        async def _receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(_message: dict[str, Any]) -> None:
            return None

        try:
            with patch("mcp.server.streamable_http_manager.StreamableHTTPSessionManager", FakeSessionManager):
                handler, _lifespan = create_mcp_app(db_resolver=_resolver)

                async def _request(key: str) -> None:
                    token = selected_key.set(key)
                    try:
                        await handler({"type": "http", "path": f"/{key}"}, _receive, _send)
                    finally:
                        selected_key.reset(token)

                await asyncio.gather(_request("a"), _request("b"))

            assert observed["a_before"][0] is db_a
            assert observed["a_after"][0] is db_a
            assert observed["b_before"][0] is db_b
            assert observed["b_after"][0] is db_b

            assert observed["a_before"][1] == dir_a
            assert observed["a_after"][1] == dir_a
            assert observed["b_before"][1] == dir_b
            assert observed["b_after"][1] == dir_b

            assert observed["a_before"][2] == dir_a.parent / "nested"
            assert observed["a_after"][2] == dir_a.parent / "nested"
            assert observed["b_before"][2] == dir_b.parent / "nested"
            assert observed["b_after"][2] == dir_b.parent / "nested"

            # HTTP request handling must not overwrite shared module globals.
            assert mcp_mod.db is db_global
            assert mcp_mod._filigree_dir == dir_global
            assert mcp_mod._get_db() is db_global
            assert mcp_mod._get_filigree_dir() == dir_global
        finally:
            mcp_mod.db = original_db
            mcp_mod._filigree_dir = original_dir
            db_global.close()
            db_a.close()
            db_b.close()


# ---------------------------------------------------------------------------
# Bug: filigree-ebb27d — list_issues drops status_category filter
# ---------------------------------------------------------------------------


class TestListIssuesStatusCategoryEmpty:
    """Bug fix: filigree-ebb27d — status_category must not be silently dropped."""

    async def test_unrecognized_category_returns_empty(self, mcp_db: FiligreeDB) -> None:
        """Passing a non-existent status_category should return empty, not all issues."""
        mcp_db.create_issue("Should not appear")
        result = await call_tool("list_issues", {"status_category": "nonexistent"})
        data = _parse(result)
        assert data["issues"] == [], f"Expected empty results for unknown category, got {len(data['issues'])} issues"

    async def test_valid_category_still_filters(self, mcp_db: FiligreeDB) -> None:
        """A valid category with matching issues should still work correctly."""
        mcp_db.create_issue("Open issue")
        b = mcp_db.create_issue("WIP issue")
        mcp_db.update_issue(b.id, status="in_progress")
        result = await call_tool("list_issues", {"status_category": "wip"})
        data = _parse(result)
        ids = [i["id"] for i in data["issues"]]
        assert b.id in ids


# ---------------------------------------------------------------------------
# Bug: filigree-7f5ee1 — add_file_association misclassifies not_found
# ---------------------------------------------------------------------------


class TestAddFileAssociationIssueNotFound:
    """Bug fix: filigree-7f5ee1 — missing issue should return not_found, not validation_error."""

    async def test_nonexistent_issue_returns_not_found(self, mcp_db: FiligreeDB) -> None:
        """add_file_association with a non-existent issue_id should return code=not_found."""
        file_data = _parse(await call_tool("register_file", {"path": "src/test.py"}))
        result = _parse(
            await call_tool(
                "add_file_association",
                {
                    "file_id": file_data["id"],
                    "issue_id": "nonexistent-xyz",
                    "assoc_type": "task_for",
                },
            )
        )
        assert result["code"] == "not_found", f"Expected 'not_found', got '{result['code']}'"

    async def test_invalid_assoc_type_still_validation_error(self, mcp_db: FiligreeDB) -> None:
        """Bad assoc_type should still be validation_error (not affected by fix)."""
        issue = mcp_db.create_issue("Real issue")
        file_data = _parse(await call_tool("register_file", {"path": "src/valid.py"}))
        result = _parse(
            await call_tool(
                "add_file_association",
                {
                    "file_id": file_data["id"],
                    "issue_id": issue.id,
                    "assoc_type": "invalid_type",
                },
            )
        )
        assert result["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Bug: filigree-5bee22 — trigger_scan cooldown race condition
# ---------------------------------------------------------------------------


class TestTriggerScanCooldownRace:
    """Bug fix: filigree-5bee22 — cooldown must be set before async operations."""

    async def test_cooldown_set_before_async_sleep(self, mcp_db: FiligreeDB) -> None:
        """Cooldown key must be set BEFORE asyncio.sleep (the first await point).

        The race window is between the check and set — if another coroutine runs
        during the await, it could bypass cooldown. We verify by patching sleep
        to assert the cooldown dict already has the key.
        """
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "race_before_sleep.py"
        scanners_dir = mcp_mod._filigree_dir / "scanners"
        scanners_dir.mkdir(exist_ok=True)
        (scanners_dir / "echo-scanner.toml").write_text('[scanner]\nname = "echo-scanner"\ncommand = "echo"\nargs = ["{file_path}"]\n')
        try:
            target.write_text("x = 1\n")
            mcp_mod._scan_cooldowns.clear()

            cooldown_was_set_before_sleep = False
            project_scope = str(mcp_mod._filigree_dir.resolve())
            cooldown_key = (project_scope, "echo-scanner", "race_before_sleep.py")

            original_sleep = asyncio.sleep

            async def spy_sleep(duration: float) -> None:
                nonlocal cooldown_was_set_before_sleep
                if cooldown_key in mcp_mod._scan_cooldowns:
                    cooldown_was_set_before_sleep = True
                await original_sleep(duration)

            with patch("filigree.mcp_server.asyncio.sleep", side_effect=spy_sleep):
                result = _parse(
                    await call_tool(
                        "trigger_scan",
                        {"scanner": "echo-scanner", "file_path": "race_before_sleep.py"},
                    )
                )
            assert result.get("status") == "triggered"
            assert cooldown_was_set_before_sleep, "Cooldown must be set BEFORE asyncio.sleep to prevent race condition"
        finally:
            target.unlink(missing_ok=True)
            (scanners_dir / "echo-scanner.toml").unlink(missing_ok=True)
            mcp_mod._scan_cooldowns.clear()

    async def test_cooldown_rolled_back_on_spawn_failure(self, mcp_db: FiligreeDB) -> None:
        """If process spawn fails (OSError), cooldown should be rolled back."""
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "spawn_fail.py"
        scanners_dir = mcp_mod._filigree_dir / "scanners"
        scanners_dir.mkdir(exist_ok=True)
        (scanners_dir / "spawn-fail.toml").write_text('[scanner]\nname = "spawn-fail"\ncommand = "echo"\nargs = ["{file_path}"]\n')
        try:
            target.write_text("y = 1\n")
            mcp_mod._scan_cooldowns.clear()

            with patch("filigree.mcp_server.subprocess.Popen", side_effect=OSError("mock spawn fail")):
                result = _parse(
                    await call_tool(
                        "trigger_scan",
                        {"scanner": "spawn-fail", "file_path": "spawn_fail.py"},
                    )
                )
            assert result["code"] == "spawn_failed"

            # Cooldown should be rolled back so retry is allowed
            project_scope = str(mcp_mod._filigree_dir.resolve())
            cooldown_key = (project_scope, "spawn-fail", "spawn_fail.py")
            assert cooldown_key not in mcp_mod._scan_cooldowns, "Cooldown should be rolled back after spawn failure"
        finally:
            target.unlink(missing_ok=True)
            (scanners_dir / "spawn-fail.toml").unlink(missing_ok=True)
            mcp_mod._scan_cooldowns.clear()

    async def test_cooldown_rolled_back_on_command_not_found(self, mcp_db: FiligreeDB) -> None:
        """If command validation fails, cooldown should be rolled back."""
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "cmd_fail.py"
        scanners_dir = mcp_mod._filigree_dir / "scanners"
        scanners_dir.mkdir(exist_ok=True)
        (scanners_dir / "bad-cmd.toml").write_text('[scanner]\nname = "bad-cmd"\ncommand = "nonexistent_binary_xyz"\n')
        try:
            target.write_text("z = 1\n")
            mcp_mod._scan_cooldowns.clear()

            result = _parse(
                await call_tool(
                    "trigger_scan",
                    {"scanner": "bad-cmd", "file_path": "cmd_fail.py"},
                )
            )
            assert result["code"] == "command_not_found"

            # Cooldown should be rolled back so retry is allowed
            project_scope = str(mcp_mod._filigree_dir.resolve())
            cooldown_key = (project_scope, "bad-cmd", "cmd_fail.py")
            assert cooldown_key not in mcp_mod._scan_cooldowns, "Cooldown should be rolled back after command validation failure"
        finally:
            target.unlink(missing_ok=True)
            (scanners_dir / "bad-cmd.toml").unlink(missing_ok=True)
            mcp_mod._scan_cooldowns.clear()
