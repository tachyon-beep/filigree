"""MCP server contract tests — test all 20 tools via call_tool()."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, SUMMARY_FILENAME, FiligreeDB, write_config
from filigree.mcp_server import _text, call_tool, get_workflow_prompt, list_resources, read_context


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


class TestListAndSearch:
    async def test_list_issues(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("List A")
        mcp_db.create_issue("List B")
        result = await call_tool("list_issues", {})
        data = _parse(result)
        assert len(data) == 2

    async def test_list_issues_filter(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("Open one")
        b = mcp_db.create_issue("Close one")
        mcp_db.close_issue(b.id)
        result = await call_tool("list_issues", {"status": "open"})
        data = _parse(result)
        assert len(data) == 1

    async def test_search(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("Authentication bug")
        mcp_db.create_issue("Something else")
        result = await call_tool("search_issues", {"query": "auth"})
        data = _parse(result)
        assert len(data) == 1
        assert data[0]["title"] == "Authentication bug"


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
        result = await call_tool("add_label", {"issue_id": issue.id, "label": "bug"})
        data = _parse(result)
        assert data["status"] == "added"
        assert data["label"] == "bug"
        # Verify it was actually added
        updated = mcp_db.get_issue(issue.id)
        assert "bug" in updated.labels

    async def test_remove_label(self, mcp_db: FiligreeDB) -> None:
        issue = mcp_db.create_issue("Labelable", labels=["bug", "urgent"])
        result = await call_tool("remove_label", {"issue_id": issue.id, "label": "bug"})
        data = _parse(result)
        assert data["status"] == "removed"
        updated = mcp_db.get_issue(issue.id)
        assert "bug" not in updated.labels
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
        # Should return open issues (not in_progress ones)
        statuses = {d["status"] for d in data}
        assert "in_progress" not in statuses
        assert a.id in [d["id"] for d in data]

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
