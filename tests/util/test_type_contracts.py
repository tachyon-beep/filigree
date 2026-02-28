"""Contract tests for TypedDict shapes vs actual runtime return values."""

from __future__ import annotations

import ast
from collections.abc import Generator
from pathlib import Path
from typing import get_type_hints

import pytest

from filigree.core import FileRecord, FiligreeDB
from filigree.types.api import (
    BatchCloseResponse,
    BatchUpdateResponse,
    ClaimNextResponse,
    DepDetail,
    EnrichedIssueDetail,
    ErrorResponse,
    IssueDetailEvent,
    IssueListResponse,
    IssueWithChangedFields,
    IssueWithTransitions,
    IssueWithUnblocked,
    PlanResponse,
    SearchResponse,
    SlimIssue,
    StatsWithPrefix,
    TransitionError,
)
from filigree.types.core import FileRecordDict, IssueDict, PaginatedResult, ScanFindingDict
from filigree.types.events import EventRecord, EventRecordWithTitle, UndoFailure, UndoSuccess
from filigree.types.files import (
    CleanStaleResult,
    FileAssociation,
    FileDetail,
    FileHotspot,
    FindingsSummary,
    GlobalFindingsStats,
    IssueFileAssociation,
    ScanIngestResult,
    ScanRunRecord,
)
from filigree.types.planning import (
    CommentRecord,
    CriticalPathNode,
    FlowMetrics,
    PlanTree,
    StatsResult,
)
from filigree.types.workflow import TemplateInfo, TemplateListItem
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
#
# NOTE: get_type_hints() resolves string annotations to actual types,
# but for TypedDict it returns the declared key→type mapping — we only
# compare *key sets* here, not value types, because NewType (e.g.
# ISOTimestamp) resolves to its base type (str) under get_type_hints().
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

    def test_closed_issue_value_types(self, db: FiligreeDB) -> None:
        """Verify closed_at is a string (not None) for a closed issue."""
        issue = db.create_issue("Test", type="task")
        db.close_issue(issue.id)
        closed = db.get_issue(issue.id)
        result = closed.to_dict()
        assert isinstance(result["closed_at"], str)
        assert result["closed_at"] != ""


class TestPaginatedResultShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.create_issue("Test", type="task")
        result = db.list_files_paginated(limit=5)
        hints = get_type_hints(PaginatedResult)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        result = db.list_files_paginated(limit=5)
        assert isinstance(result["results"], list)
        assert isinstance(result["total"], int)
        assert isinstance(result["limit"], int)
        assert isinstance(result["offset"], int)
        assert isinstance(result["has_more"], bool)


class TestProjectConfigShape:
    def test_keys_present_in_defaults(self, tmp_path: Path) -> None:
        """ProjectConfig is total=False — verify that read_config() returns at least the core keys."""
        import json

        from filigree.core import read_config

        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "prefix": "test",
                    "version": 1,
                    "enabled_packs": ["core"],
                }
            )
        )
        result = read_config(tmp_path)
        assert "prefix" in result
        assert "version" in result
        assert "enabled_packs" in result
        assert isinstance(result["prefix"], str)
        assert isinstance(result["version"], int)
        assert isinstance(result["enabled_packs"], list)


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


class TestFileAssociationShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        issue = db.create_issue("Bug in main", type="bug")
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        db.add_file_association(file_id, issue.id, "bug_in")
        result = db.get_file_associations(file_id)
        assert len(result) >= 1
        hints = get_type_hints(FileAssociation)
        assert set(result[0].keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        issue = db.create_issue("Bug in main", type="bug")
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        db.add_file_association(file_id, issue.id, "bug_in")
        result = db.get_file_associations(file_id)[0]
        assert isinstance(result["file_id"], str)
        assert isinstance(result["issue_id"], str)
        assert isinstance(result["assoc_type"], str)
        assert isinstance(result["created_at"], str)


class TestIssueFileAssociationShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        issue = db.create_issue("Bug in main", type="bug")
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        db.add_file_association(file_id, issue.id, "bug_in")
        result = db.get_issue_files(issue.id)
        assert len(result) >= 1
        hints = get_type_hints(IssueFileAssociation)
        assert set(result[0].keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        issue = db.create_issue("Bug in main", type="bug")
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        db.add_file_association(file_id, issue.id, "bug_in")
        result = db.get_issue_files(issue.id)[0]
        assert isinstance(result["file_id"], str)
        assert isinstance(result["issue_id"], str)
        assert isinstance(result["file_path"], str)
        assert isinstance(result["assoc_type"], str)


class TestFindingsSummaryShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        result = db.get_file_findings_summary(file_id)
        hints = get_type_hints(FindingsSummary)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        result = db.get_file_findings_summary(file_id)
        assert isinstance(result["total_findings"], int)
        assert isinstance(result["open_findings"], int)
        assert isinstance(result["critical"], int)
        assert isinstance(result["high"], int)


class TestGlobalFindingsStatsShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        result = db.get_global_findings_stats()
        hints = get_type_hints(GlobalFindingsStats)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        result = db.get_global_findings_stats()
        assert isinstance(result["total_findings"], int)
        assert isinstance(result["open_findings"], int)
        assert isinstance(result["files_with_findings"], int)
        assert isinstance(result["critical"], int)


class TestFileDetailShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        result = db.get_file_detail(file_id)
        hints = get_type_hints(FileDetail)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        files = db.list_files_paginated(limit=1)
        file_id = files["results"][0]["id"]
        result = db.get_file_detail(file_id)
        assert isinstance(result["file"], dict)
        assert isinstance(result["associations"], list)
        assert isinstance(result["recent_findings"], list)
        assert isinstance(result["summary"], dict)


class TestFileHotspotShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "critical"}],
        )
        result = db.get_file_hotspots(limit=1)
        assert len(result) >= 1
        hints = get_type_hints(FileHotspot)
        assert set(result[0].keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "critical"}],
        )
        result = db.get_file_hotspots(limit=1)[0]
        assert isinstance(result["file"], dict)
        assert isinstance(result["score"], int)
        assert isinstance(result["findings_breakdown"], dict)


class TestScanRunRecordShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            scan_run_id="run-001",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        result = db.get_scan_runs(limit=1)
        assert len(result) >= 1
        hints = get_type_hints(ScanRunRecord)
        assert set(result[0].keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        db.process_scan_results(
            scan_source="test",
            scan_run_id="run-001",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        result = db.get_scan_runs(limit=1)[0]
        assert isinstance(result["scan_run_id"], str)
        assert isinstance(result["scan_source"], str)
        assert isinstance(result["total_findings"], int)
        assert isinstance(result["files_scanned"], int)


class TestScanIngestResultShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        result = db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        hints = get_type_hints(ScanIngestResult)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.register_file("/src/main.py", language="python", file_type="source")
        result = db.process_scan_results(
            scan_source="test",
            findings=[{"path": "/src/main.py", "rule_id": "R1", "message": "m", "severity": "high"}],
        )
        assert isinstance(result["files_created"], int) or isinstance(result["files_updated"], int)
        assert isinstance(result["findings_created"], int)
        assert isinstance(result["new_finding_ids"], list)
        assert isinstance(result["warnings"], list)
        assert isinstance(result["issues_created"], int)
        assert isinstance(result["issue_ids"], list)


class TestCleanStaleResultShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        result = db.clean_stale_findings(days=30)
        hints = get_type_hints(CleanStaleResult)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        result = db.clean_stale_findings(days=30)
        assert isinstance(result["findings_fixed"], int)


# ---------------------------------------------------------------------------
# 1C. Events, planning, workflow, and analytics shape tests
# ---------------------------------------------------------------------------


class TestEventRecordShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, status="in_progress")
        events = db.get_issue_events(issue.id, limit=1)
        assert len(events) >= 1
        hints = get_type_hints(EventRecord)
        assert set(events[0].keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, status="in_progress")
        event = db.get_issue_events(issue.id, limit=1)[0]
        assert isinstance(event["id"], int)
        assert isinstance(event["issue_id"], str)
        assert isinstance(event["event_type"], str)
        assert isinstance(event["created_at"], str)


class TestEventRecordWithTitleShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, status="in_progress")
        events = db.get_recent_events(limit=1)
        assert len(events) >= 1
        hints = get_type_hints(EventRecordWithTitle)
        assert set(events[0].keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, status="in_progress")
        event = db.get_recent_events(limit=1)[0]
        assert isinstance(event["issue_title"], str)
        assert isinstance(event["event_type"], str)


class TestUndoResultShape:
    def test_failure_keys(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        result = db.undo_last(issue.id)
        hints = get_type_hints(UndoFailure)
        assert set(result.keys()) == set(hints.keys())

    def test_success_keys(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, status="in_progress")
        result = db.undo_last(issue.id)
        hints = get_type_hints(UndoSuccess)
        assert set(result.keys()) == set(hints.keys())

    def test_success_value_types(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, status="in_progress")
        result = db.undo_last(issue.id)
        assert result["undone"] is True
        assert isinstance(result["event_type"], str)
        assert isinstance(result["event_id"], int)
        assert isinstance(result["issue"], dict)


class TestCommentRecordShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.add_comment(issue.id, "hello")
        comments = db.get_comments(issue.id)
        assert len(comments) >= 1
        hints = get_type_hints(CommentRecord)
        assert set(comments[0].keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.add_comment(issue.id, "hello")
        comment = db.get_comments(issue.id)[0]
        assert isinstance(comment["id"], int)
        assert isinstance(comment["text"], str)
        assert isinstance(comment["created_at"], str)


class TestStatsResultShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.create_issue("Test", type="task")
        result = db.get_stats()
        hints = get_type_hints(StatsResult)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        db.create_issue("Test", type="task")
        result = db.get_stats()
        assert isinstance(result["by_status"], dict)
        assert isinstance(result["by_type"], dict)
        assert isinstance(result["ready_count"], int)
        assert isinstance(result["blocked_count"], int)


class TestDependencyRecordShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        db.add_dependency(a.id, b.id)
        deps = db.get_all_dependencies()
        assert len(deps) >= 1
        # DependencyRecord uses functional-form TypedDict with "from" key
        assert set(deps[0].keys()) == {"from", "to", "type"}

    def test_value_types(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        db.add_dependency(a.id, b.id)
        dep = db.get_all_dependencies()[0]
        assert isinstance(dep["from"], str)
        assert isinstance(dep["to"], str)
        assert isinstance(dep["type"], str)


class TestCriticalPathNodeShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        db.add_dependency(b.id, a.id)
        path = db.get_critical_path()
        assert len(path) >= 1
        hints = get_type_hints(CriticalPathNode)
        assert set(path[0].keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        db.add_dependency(b.id, a.id)
        node = db.get_critical_path()[0]
        assert isinstance(node["id"], str)
        assert isinstance(node["title"], str)
        assert isinstance(node["priority"], int)


class TestPlanTreeShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        milestone = db.create_issue("Milestone", type="milestone")
        phase = db.create_issue("Phase", type="phase", parent_id=milestone.id)
        db.create_issue("Step", type="step", parent_id=phase.id)
        result = db.get_plan(milestone.id)
        hints = get_type_hints(PlanTree)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        milestone = db.create_issue("Milestone", type="milestone")
        phase = db.create_issue("Phase", type="phase", parent_id=milestone.id)
        db.create_issue("Step", type="step", parent_id=phase.id)
        result = db.get_plan(milestone.id)
        assert isinstance(result["milestone"], dict)
        assert isinstance(result["phases"], list)
        assert isinstance(result["total_steps"], int)
        assert isinstance(result["completed_steps"], int)


class TestFlowMetricsShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.analytics import get_flow_metrics

        result = get_flow_metrics(db, days=30)
        hints = get_type_hints(FlowMetrics)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        from filigree.analytics import get_flow_metrics

        result = get_flow_metrics(db, days=30)
        assert isinstance(result["period_days"], int)
        assert isinstance(result["throughput"], int)
        assert isinstance(result["by_type"], dict)


class TestTemplateInfoShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        result = db.get_template("task")
        assert result is not None
        hints = get_type_hints(TemplateInfo)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        result = db.get_template("task")
        assert result is not None
        assert isinstance(result["type"], str)
        assert isinstance(result["states"], list)
        assert isinstance(result["transitions"], list)
        assert isinstance(result["fields_schema"], list)


class TestTemplateListItemShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        result = db.list_templates()
        assert len(result) >= 1
        hints = get_type_hints(TemplateListItem)
        assert set(result[0].keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        result = db.list_templates()
        item = result[0]
        assert isinstance(item["type"], str)
        assert isinstance(item["display_name"], str)
        assert isinstance(item["fields_schema"], list)


# ---------------------------------------------------------------------------
# 1D. API response shape tests (types/api.py)
# ---------------------------------------------------------------------------


class TestSlimIssueShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.mcp_tools.common import _slim_issue

        issue = db.create_issue("Test", type="task")
        result = _slim_issue(issue)
        hints = get_type_hints(SlimIssue)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        from filigree.mcp_tools.common import _slim_issue

        issue = db.create_issue("Test", type="task")
        result = _slim_issue(issue)
        assert isinstance(result["id"], str)
        assert isinstance(result["priority"], int)


class TestIssueWithTransitionsShape:
    def test_keys_without_transitions(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        result = IssueWithTransitions(**issue.to_dict())
        # NotRequired keys may be absent
        assert {"id", "title", "status"} <= set(result.keys())

    def test_keys_with_transitions(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        result = IssueWithTransitions(**issue.to_dict(), valid_transitions=[])
        hints = get_type_hints(IssueWithTransitions)
        assert set(result.keys()) == set(hints.keys())


class TestIssueWithChangedFieldsShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, title="Updated")
        updated = db.get_issue(issue.id)
        result = IssueWithChangedFields(**updated.to_dict(), changed_fields=["title"])
        hints = get_type_hints(IssueWithChangedFields)
        assert set(result.keys()) == set(hints.keys())


class TestIssueWithUnblockedShape:
    def test_keys_without_unblocked(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        result = IssueWithUnblocked(**issue.to_dict())
        assert {"id", "title", "status"} <= set(result.keys())

    def test_keys_with_unblocked(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        result = IssueWithUnblocked(
            **issue.to_dict(),
            newly_unblocked=[SlimIssue(id="x", title="t", status="open", priority=2, type="task")],
        )
        hints = get_type_hints(IssueWithUnblocked)
        assert set(result.keys()) == set(hints.keys())


class TestClaimNextResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        result = ClaimNextResponse(**issue.to_dict(), selection_reason="P2 ready issue")
        hints = get_type_hints(ClaimNextResponse)
        assert set(result.keys()) == set(hints.keys())

    def test_value_types(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        result = ClaimNextResponse(**issue.to_dict(), selection_reason="P2 ready issue")
        assert isinstance(result["selection_reason"], str)


class TestIssueListResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        db.create_issue("Test", type="task")
        issues = db.list_issues(limit=1)
        result = IssueListResponse(
            issues=[i.to_dict() for i in issues],
            limit=1,
            offset=0,
            has_more=False,
        )
        hints = get_type_hints(IssueListResponse)
        assert set(result.keys()) == set(hints.keys())


class TestSearchResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        from filigree.mcp_tools.common import _slim_issue

        issue = db.create_issue("Searchable", type="task")
        result = SearchResponse(
            issues=[_slim_issue(issue)],
            limit=10,
            offset=0,
            has_more=False,
        )
        hints = get_type_hints(SearchResponse)
        assert set(result.keys()) == set(hints.keys())


class TestBatchUpdateResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        result = BatchUpdateResponse(succeeded=["a"], failed=[], count=1)
        hints = get_type_hints(BatchUpdateResponse)
        assert set(result.keys()) == set(hints.keys())


class TestBatchCloseResponseShape:
    def test_required_keys(self, db: FiligreeDB) -> None:
        """Required keys (succeeded, failed, count) are always present."""
        result = BatchCloseResponse(succeeded=["a"], failed=[], count=1)
        assert {"succeeded", "failed", "count"} <= set(result.keys())

    def test_with_newly_unblocked(self, db: FiligreeDB) -> None:
        """All 4 keys present when newly_unblocked is populated."""
        result = BatchCloseResponse(
            succeeded=["a"],
            failed=[],
            count=1,
            newly_unblocked=[SlimIssue(id="x", title="t", status="open", priority=2, type="task")],
        )
        hints = get_type_hints(BatchCloseResponse)
        assert set(result.keys()) == set(hints.keys())


class TestErrorResponseShape:
    def test_keys_match(self) -> None:
        result = ErrorResponse(error="not found", code="not_found")
        hints = get_type_hints(ErrorResponse)
        assert set(result.keys()) == set(hints.keys())


class TestTransitionErrorShape:
    def test_keys_match(self) -> None:
        result = TransitionError(error="bad", code="invalid_transition")
        # NotRequired keys may be absent — check required subset
        assert {"error", "code"} <= set(result.keys())


class TestDepDetailShape:
    def test_keys_match(self) -> None:
        result = DepDetail(title="t", status="open", status_category="open", priority=2)
        hints = get_type_hints(DepDetail)
        assert set(result.keys()) == set(hints.keys())


class TestIssueDetailEventFromSQL:
    def test_construction_from_sql_row(self, db: FiligreeDB) -> None:
        """Exercise IssueDetailEvent construction from a real SQL row."""
        issue = db.create_issue("Test", type="task")
        db.update_issue(issue.id, status="in_progress")
        rows = db.conn.execute(
            "SELECT event_type, actor, old_value, new_value, created_at FROM events WHERE issue_id = ? LIMIT 1",
            (issue.id,),
        ).fetchall()
        assert len(rows) >= 1
        event = IssueDetailEvent(
            event_type=rows[0]["event_type"],
            actor=rows[0]["actor"],
            old_value=rows[0]["old_value"],
            new_value=rows[0]["new_value"],
            created_at=rows[0]["created_at"],
        )
        hints = get_type_hints(IssueDetailEvent)
        assert set(event.keys()) == set(hints.keys())


class TestStatsWithPrefixShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        stats = db.get_stats()
        result = StatsWithPrefix(**stats, prefix="TEST")
        hints = get_type_hints(StatsWithPrefix)
        assert set(result.keys()) == set(hints.keys())


class TestPlanResponseShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        milestone = db.create_issue("M", type="milestone")
        phase = db.create_issue("P", type="phase", parent_id=milestone.id)
        db.create_issue("S", type="step", parent_id=phase.id)
        plan = db.get_plan(milestone.id)
        result = PlanResponse(
            milestone=plan["milestone"],
            phases=plan["phases"],
            total_steps=plan["total_steps"],
            completed_steps=plan["completed_steps"],
            progress_pct=0.0,
        )
        hints = get_type_hints(PlanResponse)
        assert set(result.keys()) == set(hints.keys())


class TestEnrichedIssueDetailShape:
    def test_keys_match(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="task")
        result = EnrichedIssueDetail(
            **issue.to_dict(),
            dep_details={},
            events=[],
            comments=[],
        )
        hints = get_type_hints(EnrichedIssueDetail)
        assert set(result.keys()) == set(hints.keys())


class TestDanglingDepDetail:
    def test_fallback_for_missing_dep(self, db: FiligreeDB) -> None:
        """Verify the fallback DepDetail for a deleted dependency."""
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        db.add_dependency(a.id, b.id)
        # Disable FK constraints to simulate orphaned dep row
        db.conn.execute("PRAGMA foreign_keys = OFF")
        db.conn.execute("DELETE FROM issues WHERE id = ?", (b.id,))
        db.conn.commit()
        db.conn.execute("PRAGMA foreign_keys = ON")
        # Simulate what api_issue_detail does for dangling deps
        with pytest.raises(KeyError):
            db.get_issue(b.id)
        fallback = DepDetail(title=b.id, status="unknown", status_category="open", priority=2)
        assert set(fallback.keys()) == {"title", "status", "status_category", "priority"}


# ---------------------------------------------------------------------------
# 2. Import constraint test — AST-based
# ---------------------------------------------------------------------------

TYPES_DIR = Path(__file__).resolve().parents[2] / "src" / "filigree" / "types"
FORBIDDEN_MODULES = {"filigree.core", "filigree.db_base"}
FORBIDDEN_PREFIXES = ("filigree.db_",)


def _get_imports_from_file(filepath: Path) -> list[str]:
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
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


# ---------------------------------------------------------------------------
# 4. Guard: ensure TYPES_DIR exists to prevent vacuous parametrize pass (W8)
# ---------------------------------------------------------------------------


def test_types_dir_exists() -> None:
    """Sanity check: TYPES_DIR must exist, otherwise the import constraint
    test would produce zero parametrize cases and pass vacuously."""
    assert TYPES_DIR.exists(), f"types dir not found at {TYPES_DIR}"


# ---------------------------------------------------------------------------
# 5. Dashboard JS contract: enriched issue detail keys (W9)
# ---------------------------------------------------------------------------

# Keys the JS frontend reads from the enriched issue detail endpoint
DASHBOARD_ENRICHED_KEYS = DASHBOARD_ISSUE_KEYS | {
    "dep_details",
    "events",
    "comments",
}


def test_enriched_issue_detail_keys_cover_dashboard_contract() -> None:
    """EnrichedIssueDetail must contain all keys the dashboard JS reads
    from the issue detail endpoint."""
    hints = get_type_hints(EnrichedIssueDetail)
    missing = DASHBOARD_ENRICHED_KEYS - set(hints.keys())
    assert not missing, f"EnrichedIssueDetail missing keys consumed by dashboard JS: {missing}"
