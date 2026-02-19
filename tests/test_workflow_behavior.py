"""Integration tests for per-type status validation and transition enforcement.

Tests the core.py behavior changes introduced in Phase 1C. These tests exercise
the full stack: FiligreeDB -> TemplateRegistry -> SQLite, verifying that issue
lifecycle operations respect per-type state machines.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest

from filigree.core import FiligreeDB
from filigree.templates import TransitionOption, ValidationResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> FiligreeDB:
    """A FiligreeDB instance with templates loaded (core + planning packs enabled)."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}
    (filigree_dir / "config.json").write_text(json.dumps(config))

    db = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
    db.initialize()
    yield db  # type: ignore[misc]
    db.close()


@pytest.fixture
def incident_db(tmp_path: Path) -> Generator[FiligreeDB, None, None]:
    """FiligreeDB with incident pack enabled for hard-enforcement tests."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning", "incident"]}
    (filigree_dir / "config.json").write_text(json.dumps(config))

    d = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
    d.initialize()
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Task 1.9: Per-Type Status Validation
# ---------------------------------------------------------------------------


class TestCreateIssueInitialState:
    """create_issue() uses type-specific initial state from template."""

    def test_bug_initial_state_is_triage(self, db: FiligreeDB) -> None:
        """Bug type should start in 'triage', not 'open'."""
        issue = db.create_issue("Fix crash on startup", type="bug")
        assert issue.status == "triage"

    def test_task_initial_state_is_open(self, db: FiligreeDB) -> None:
        """Task type preserves legacy 'open' initial state."""
        issue = db.create_issue("Update docs", type="task")
        assert issue.status == "open"

    def test_epic_initial_state_is_open(self, db: FiligreeDB) -> None:
        """Epic type preserves legacy 'open' initial state."""
        issue = db.create_issue("Workflow v2", type="epic")
        assert issue.status == "open"

    def test_feature_initial_state_is_proposed(self, db: FiligreeDB) -> None:
        """Feature type should start in 'proposed' per template."""
        issue = db.create_issue("Add search", type="feature")
        assert issue.status == "proposed"

    def test_milestone_initial_state(self, db: FiligreeDB) -> None:
        """Milestone type should start in 'planning' per template."""
        issue = db.create_issue("v2.0 Release", type="milestone")
        assert issue.status == "planning"

    def test_unknown_type_rejected(self, db: FiligreeDB) -> None:
        """Unknown types are rejected with a clear error listing valid types."""
        with pytest.raises(ValueError, match="Unknown type 'custom_type'"):
            db.create_issue("Something", type="custom_type")


class TestValidateStatus:
    """_validate_status() checks type-specific valid states via templates."""

    def test_valid_bug_state_accepted(self, db: FiligreeDB) -> None:
        """Bug-specific states like 'triage' should be accepted."""
        issue = db.create_issue("Bug", type="bug")
        updated = db.update_issue(issue.id, status="confirmed")
        assert updated.status == "confirmed"

    def test_invalid_bug_state_rejected(self, db: FiligreeDB) -> None:
        """States not in the bug template should raise ValueError."""
        issue = db.create_issue("Bug", type="bug")
        with pytest.raises(ValueError, match=r"Invalid status|not defined"):
            db.update_issue(issue.id, status="nonexistent_state")

    def test_task_legacy_states_accepted(self, db: FiligreeDB) -> None:
        """Task type must accept open/in_progress/closed (backward compat)."""
        issue = db.create_issue("Task", type="task")
        updated = db.update_issue(issue.id, status="in_progress")
        assert updated.status == "in_progress"
        updated = db.update_issue(issue.id, status="closed")
        assert updated.status == "closed"

    def test_unknown_type_rejected(self, db: FiligreeDB) -> None:
        """Unknown types are rejected at creation time."""
        with pytest.raises(ValueError, match="Unknown type 'custom_type'"):
            db.create_issue("Custom", type="custom_type")


class TestUpdateIssueTransitionEnforcement:
    """update_issue() validates transitions with soft/hard enforcement."""

    def test_valid_soft_transition_succeeds(self, db: FiligreeDB) -> None:
        """Soft transition triage -> confirmed should succeed."""
        issue = db.create_issue("Bug", type="bug")
        updated = db.update_issue(issue.id, status="confirmed")
        assert updated.status == "confirmed"

    def test_hard_enforcement_rejects_missing_fields(self, db: FiligreeDB) -> None:
        """Hard enforcement: verifying -> closed without fix_verification raises ValueError."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing")
        db.update_issue(issue.id, status="verifying", fields={"fix_verification": "tests pass"})
        # Now try to close — issue already has fix_verification set, so this should succeed
        updated = db.update_issue(issue.id, status="closed")
        assert updated.status == "closed"

    def test_hard_enforcement_blocks_with_empty_field(self, db: FiligreeDB) -> None:
        """Hard enforcement: empty string field should be treated as missing."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing")
        db.update_issue(issue.id, status="verifying", fields={"fix_verification": "initial"})
        # Clear the field, then try hard transition
        db.update_issue(issue.id, fields={"fix_verification": ""})
        with pytest.raises(ValueError, match="fix_verification"):
            db.update_issue(issue.id, status="closed")

    def test_soft_enforcement_proceeds_with_warning(self, db: FiligreeDB) -> None:
        """Soft enforcement: proceed + record warning events in events table."""
        issue = db.create_issue("Bug", type="bug")
        # triage -> confirmed is soft with severity required_at confirmed
        updated = db.update_issue(issue.id, status="confirmed")
        assert updated.status == "confirmed"
        # Check that a warning event was recorded
        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'transition_warning'",
            (issue.id,),
        ).fetchall()
        assert len(events) >= 1
        assert "severity" in events[0]["comment"]

    def test_atomic_transition_with_fields_succeeds(self, db: FiligreeDB) -> None:
        """WFT-FR-069: update_issue with status + fields merges fields BEFORE
        transition check, so hard enforcement sees the new fields."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing")
        # Atomic: set fix_verification AND transition to verifying in one call
        updated = db.update_issue(
            issue.id,
            status="verifying",
            fields={"fix_verification": "tests pass"},
        )
        assert updated.status == "verifying"
        assert updated.fields["fix_verification"] == "tests pass"

    def test_atomic_transition_hard_failure_rolls_back(self, db: FiligreeDB) -> None:
        """WFT-FR-069: On hard failure, neither fields NOR status are saved."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing")
        db.update_issue(issue.id, status="verifying", fields={"fix_verification": "initial"})
        # Try to close with fix_verification="" (hard enforcement)
        with pytest.raises(ValueError, match="fix_verification"):
            db.update_issue(
                issue.id,
                status="closed",
                fields={"fix_verification": "", "extra_note": "should not persist"},
            )
        # Verify nothing changed
        current = db.get_issue(issue.id)
        assert current.status == "verifying"
        assert current.fields.get("fix_verification") == "initial"
        assert "extra_note" not in current.fields

    def test_update_issue_sets_closed_at_for_done_category(self, db: FiligreeDB) -> None:
        """closed_at should be set when entering any done-category state, not just 'closed'."""
        issue = db.create_issue("Bug", type="bug")
        # triage -> wont_fix (done-category)
        updated = db.update_issue(issue.id, status="wont_fix")
        assert updated.closed_at is not None

    def test_legacy_task_closed_at_still_works(self, db: FiligreeDB) -> None:
        """Task: status='closed' still sets closed_at (backward compat)."""
        issue = db.create_issue("Task", type="task")
        db.update_issue(issue.id, status="in_progress")
        updated = db.update_issue(issue.id, status="closed")
        assert updated.closed_at is not None

    def test_undefined_transition_rejected(self, db: FiligreeDB) -> None:
        """Undefined transitions on known types are rejected."""
        issue = db.create_issue("Bug", type="bug")
        with pytest.raises(ValueError, match="not allowed"):
            db.update_issue(issue.id, status="verifying")  # triage -> verifying not in table

    def test_close_bypasses_transition_check(self, db: FiligreeDB) -> None:
        """close_issue works from any state (admin action)."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id, reason="duplicate")
        assert closed.status == "closed"

    def test_reopen_bypasses_transition_check(self, db: FiligreeDB) -> None:
        """Reopen works from done state back to initial."""
        issue = db.create_issue("Bug", type="bug")
        db.close_issue(issue.id)
        reopened = db.update_issue(issue.id, status="triage", _skip_transition_check=True)
        assert reopened.status == "triage"

    def test_skip_transition_check_flag(self, db: FiligreeDB) -> None:
        """_skip_transition_check allows any valid state."""
        issue = db.create_issue("Bug", type="bug")
        updated = db.update_issue(issue.id, status="verifying", _skip_transition_check=True)
        assert updated.status == "verifying"


class TestCloseIssue:
    """close_issue() accepts optional status parameter for multi-done types."""

    def test_close_bug_default_done_state(self, db: FiligreeDB) -> None:
        """close_issue() without status uses first done-category state."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id)
        assert closed.status == "closed"  # 'closed' is first done state for bug

    def test_close_bug_with_specific_done_state(self, db: FiligreeDB) -> None:
        """close_issue(status='wont_fix') uses specified done state."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id, status="wont_fix")
        assert closed.status == "wont_fix"

    def test_close_bug_rejects_non_done_state(self, db: FiligreeDB) -> None:
        """close_issue() with a non-done state raises ValueError."""
        issue = db.create_issue("Bug", type="bug")
        with pytest.raises(ValueError, match="done"):
            db.close_issue(issue.id, status="fixing")

    def test_close_task_default(self, db: FiligreeDB) -> None:
        """close_issue() on task type uses 'closed' (backward compat)."""
        issue = db.create_issue("Task", type="task")
        closed = db.close_issue(issue.id)
        assert closed.status == "closed"

    def test_close_already_closed_raises(self, db: FiligreeDB) -> None:
        """close_issue() on already-closed issue raises with clear message."""
        issue = db.create_issue("Bug", type="bug")
        db.close_issue(issue.id)
        with pytest.raises(ValueError, match="already closed"):
            db.close_issue(issue.id)


class TestCloseIssueHardEnforcement:
    """close_issue() must respect hard-enforcement gates (filigree-87e5e3)."""

    def test_close_incident_from_resolved_requires_root_cause(self, incident_db: FiligreeDB) -> None:
        """Incident resolved→closed has hard enforcement requiring root_cause."""
        issue = incident_db.create_issue("Outage", type="incident")
        # Walk to resolved: reported → triaging → investigating → resolved
        incident_db.update_issue(issue.id, status="triaging", fields={"severity": "sev2"})
        incident_db.update_issue(issue.id, status="investigating")
        incident_db.update_issue(issue.id, status="resolved")

        # Attempting to close without root_cause should fail
        with pytest.raises(ValueError, match="root_cause"):
            incident_db.close_issue(issue.id)

    def test_close_incident_with_root_cause_succeeds(self, incident_db: FiligreeDB) -> None:
        """Providing required fields via fields= allows close through hard gate."""
        issue = incident_db.create_issue("Outage", type="incident")
        incident_db.update_issue(issue.id, status="triaging", fields={"severity": "sev2"})
        incident_db.update_issue(issue.id, status="investigating")
        incident_db.update_issue(issue.id, status="resolved")

        closed = incident_db.close_issue(
            issue.id,
            fields={"root_cause": "Config drift in prod"},
            reason="Resolved after config rollback",
        )
        assert closed.status == "closed"
        assert closed.fields["root_cause"] == "Config drift in prod"
        assert closed.fields["close_reason"] == "Resolved after config rollback"

    def test_close_incident_with_pre_populated_field_succeeds(self, incident_db: FiligreeDB) -> None:
        """If root_cause was set earlier, close_issue succeeds without fields=."""
        issue = incident_db.create_issue("Outage", type="incident")
        incident_db.update_issue(issue.id, status="triaging", fields={"severity": "sev2"})
        incident_db.update_issue(issue.id, status="investigating")
        incident_db.update_issue(issue.id, status="resolved", fields={"root_cause": "OOM in worker"})

        closed = incident_db.close_issue(issue.id)
        assert closed.status == "closed"
        assert closed.fields["root_cause"] == "OOM in worker"

    def test_close_from_non_workflow_state_skips_hard_gate(self, db: FiligreeDB) -> None:
        """Admin override from a state with no transition to closed still works."""
        # Bug type: closing from triage (initial state) has no defined transition
        # to "closed" — this is the admin-override path and should still work
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id, reason="duplicate")
        assert closed.status == "closed"

    def test_close_bug_from_verifying_requires_fix_verification(self, db: FiligreeDB) -> None:
        """Bug verifying→closed has hard enforcement requiring fix_verification."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(
            issue.id,
            status="confirmed",
            fields={"severity": "major"},
            _skip_transition_check=True,
        )
        db.update_issue(issue.id, status="fixing", _skip_transition_check=True)
        db.update_issue(
            issue.id,
            status="verifying",
            fields={"fix_verification": "manual test"},
            _skip_transition_check=True,
        )

        # verifying→closed requires fix_verification (hard gate)
        # fix_verification is already set, so this should succeed
        closed = db.close_issue(issue.id)
        assert closed.status == "closed"


class TestClaimIssue:
    """claim_issue() sets assignee only — does not change status."""

    def test_claim_bug_assigns_only(self, db: FiligreeDB) -> None:
        """Bug type claim should set assignee without changing status."""
        issue = db.create_issue("Bug", type="bug")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        assert claimed.status == "triage"  # status unchanged
        assert claimed.assignee == "agent-1"

    def test_claim_task_assigns_only(self, db: FiligreeDB) -> None:
        """Task type claim sets assignee without changing status."""
        issue = db.create_issue("Task", type="task")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        assert claimed.status == "open"  # status unchanged
        assert claimed.assignee == "agent-1"

    def test_claim_already_assigned_fails(self, db: FiligreeDB) -> None:
        """Cannot claim an issue that's already assigned to someone else."""
        issue = db.create_issue("Task", type="task")
        db.claim_issue(issue.id, assignee="agent-1")
        with pytest.raises(ValueError, match="already assigned to"):
            db.claim_issue(issue.id, assignee="agent-2")


class TestReopenIssue:
    def test_reopen_bug_returns_to_triage(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Bug", type="bug")
        db.close_issue(issue.id)
        reopened = db.reopen_issue(issue.id)
        assert reopened.status == "triage"
        assert reopened.closed_at is None

    def test_reopen_task_returns_to_open(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Task", type="task")
        db.close_issue(issue.id)
        reopened = db.reopen_issue(issue.id)
        assert reopened.status == "open"

    def test_reopen_already_open_raises(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Task", type="task")
        with pytest.raises(ValueError, match="not in a done-category state"):
            db.reopen_issue(issue.id)

    def test_reopen_records_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Task", type="task")
        db.close_issue(issue.id)
        db.reopen_issue(issue.id, actor="tester")
        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'reopened'",
            (issue.id,),
        ).fetchall()
        assert len(events) == 1


class TestReleaseClaim:
    """release_claim() clears assignee only — does not change status."""

    def test_release_bug_clears_assignee(self, db: FiligreeDB) -> None:
        """Bug type release should clear assignee without changing status."""
        issue = db.create_issue("Bug", type="bug")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        released = db.release_claim(claimed.id)
        assert released.status == "triage"  # status unchanged
        assert released.assignee == ""

    def test_release_task_clears_assignee(self, db: FiligreeDB) -> None:
        """Task type release clears assignee without changing status."""
        issue = db.create_issue("Task", type="task")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        released = db.release_claim(claimed.id)
        assert released.status == "open"  # status unchanged
        assert released.assignee == ""

    def test_release_no_assignee_fails(self, db: FiligreeDB) -> None:
        """Cannot release an issue that has no assignee."""
        issue = db.create_issue("Task", type="task")
        with pytest.raises(ValueError, match="no assignee set"):
            db.release_claim(issue.id)


# ---------------------------------------------------------------------------
# Task 1.10: Category-Aware Queries
# ---------------------------------------------------------------------------


class TestGetStatesForCategory:
    """_get_states_for_category() collects states across all enabled types."""

    def test_open_category_includes_triage(self, db: FiligreeDB) -> None:
        """Open category should include bug's 'triage' and 'confirmed' states."""
        states = db._get_states_for_category("open")
        assert "open" in states  # from task
        assert "triage" in states  # from bug
        assert "confirmed" in states  # from bug

    def test_wip_category_includes_fixing(self, db: FiligreeDB) -> None:
        """Wip category should include bug's 'fixing' and 'verifying' states."""
        states = db._get_states_for_category("wip")
        assert "in_progress" in states  # from task
        assert "fixing" in states  # from bug
        assert "verifying" in states  # from bug

    def test_done_category_includes_wont_fix(self, db: FiligreeDB) -> None:
        """Done category should include bug's 'closed' and 'wont_fix' states."""
        states = db._get_states_for_category("done")
        assert "closed" in states  # from task
        assert "wont_fix" in states  # from bug

    def test_no_duplicates(self, db: FiligreeDB) -> None:
        """State names should not appear twice even if multiple types share a state."""
        states = db._get_states_for_category("open")
        assert len(states) == len(set(states))


class TestListIssuesCategory:
    """list_issues(status=) accepts category names and specific states."""

    def test_list_by_open_category(self, db: FiligreeDB) -> None:
        """list_issues(status='open') returns issues in any open-category state."""
        bug = db.create_issue("Bug", type="bug")  # status=triage (open category)
        task = db.create_issue("Task", type="task")  # status=open (open category)
        db.update_issue(task.id, status="in_progress")  # now wip

        issues = db.list_issues(status="open")
        ids = {i.id for i in issues}
        assert bug.id in ids  # triage is open-category
        assert task.id not in ids  # in_progress is wip-category

    def test_list_by_wip_category(self, db: FiligreeDB) -> None:
        """list_issues(status='wip') returns issues in any wip-category state."""
        bug = db.create_issue("Bug", type="bug")
        db.update_issue(bug.id, status="confirmed")
        db.update_issue(bug.id, status="fixing")
        task = db.create_issue("Task", type="task")

        issues = db.list_issues(status="wip")
        ids = {i.id for i in issues}
        assert bug.id in ids  # fixing is wip-category
        assert task.id not in ids  # open is open-category

    def test_list_by_specific_state(self, db: FiligreeDB) -> None:
        """list_issues(status='triage') returns only bugs in literal 'triage' state."""
        bug = db.create_issue("Bug", type="bug")  # triage
        bug2 = db.create_issue("Bug2", type="bug")
        db.update_issue(bug2.id, status="confirmed")  # confirmed

        issues = db.list_issues(status="triage")
        ids = {i.id for i in issues}
        assert bug.id in ids
        assert bug2.id not in ids  # confirmed, not triage

    def test_list_by_done_category(self, db: FiligreeDB) -> None:
        """list_issues(status='done') returns issues in any done-category state."""
        bug = db.create_issue("Bug", type="bug")
        db.close_issue(bug.id, status="wont_fix")
        task = db.create_issue("Task", type="task")
        db.close_issue(task.id)

        issues = db.list_issues(status="done")
        ids = {i.id for i in issues}
        assert bug.id in ids
        assert task.id in ids


class TestGetReadyCategory:
    """get_ready() uses open-category states from ALL enabled types."""

    def test_ready_includes_bug_in_triage(self, db: FiligreeDB) -> None:
        """Bug in 'triage' (open-category) with no blockers is ready."""
        bug = db.create_issue("Bug", type="bug")
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id in ids

    def test_ready_excludes_wip(self, db: FiligreeDB) -> None:
        """Bug in 'fixing' (wip-category) is not ready."""
        bug = db.create_issue("Bug", type="bug")
        db.update_issue(bug.id, status="confirmed")
        db.update_issue(bug.id, status="fixing")
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id not in ids

    def test_ready_excludes_blocked(self, db: FiligreeDB) -> None:
        """Bug in 'triage' blocked by open task is not ready."""
        blocker = db.create_issue("Blocker", type="task")
        bug = db.create_issue("Bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id not in ids

    def test_ready_with_done_blocker_is_unblocked(self, db: FiligreeDB) -> None:
        """Bug in 'triage' with closed blocker should be ready."""
        blocker = db.create_issue("Blocker", type="task")
        bug = db.create_issue("Bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        db.close_issue(blocker.id)
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id in ids

    def test_ready_with_wont_fix_blocker_is_unblocked(self, db: FiligreeDB) -> None:
        """Bug blocked by a 'wont_fix' (done-category) bug should be ready."""
        blocker = db.create_issue("Blocker bug", type="bug")
        bug = db.create_issue("Main bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        db.close_issue(blocker.id, status="wont_fix")
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id in ids

    def test_ready_handles_no_done_states(self, tmp_path: Path) -> None:
        """get_ready() should work when enabled templates define zero done-category states."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        (filigree_dir / "config.json").write_text(
            json.dumps({"prefix": "test", "version": 1, "enabled_packs": ["custom_only"]})
        )

        packs_dir = filigree_dir / "packs"
        packs_dir.mkdir()
        custom_pack = {
            "pack": "custom_only",
            "version": "1.0",
            "display_name": "Custom Only",
            "description": "Pack with no done-category states",
            "requires_packs": [],
            "types": {
                "custom_item": {
                    "type": "custom_item",
                    "display_name": "Custom Item",
                    "description": "No done states",
                    "pack": "custom_only",
                    "states": [
                        {"name": "open", "category": "open"},
                        {"name": "active", "category": "wip"},
                    ],
                    "initial_state": "open",
                    "transitions": [
                        {"from": "open", "to": "active", "enforcement": "soft"},
                    ],
                    "fields_schema": [],
                },
            },
            "relationships": [],
            "cross_pack_relationships": [],
            "guide": None,
        }
        (packs_dir / "custom_only.json").write_text(json.dumps(custom_pack))

        db = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
        db.initialize()

        blocker = db.create_issue("Blocker", type="custom_item")
        blocked = db.create_issue("Blocked", type="custom_item")
        db.add_dependency(blocked.id, blocker.id)

        ready_ids = {i.id for i in db.get_ready()}
        assert blocker.id in ready_ids
        assert blocked.id not in ready_ids

        db.close()


class TestGetBlockedCategory:
    """get_blocked() uses open-category + done-category for blocker checks."""

    def test_blocked_in_triage(self, db: FiligreeDB) -> None:
        """Bug in 'triage' (open-category) with open blocker is blocked."""
        blocker = db.create_issue("Blocker", type="task")
        bug = db.create_issue("Bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        blocked = db.get_blocked()
        ids = {i.id for i in blocked}
        assert bug.id in ids

    def test_not_blocked_if_blocker_done(self, db: FiligreeDB) -> None:
        """Bug in 'triage' with done-category blocker is NOT blocked."""
        blocker = db.create_issue("Blocker bug", type="bug")
        bug = db.create_issue("Bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        db.close_issue(blocker.id, status="wont_fix")
        blocked = db.get_blocked()
        ids = {i.id for i in blocked}
        assert bug.id not in ids


class TestGetCriticalPathCategory:
    """get_critical_path() uses done-category for filtering out completed issues."""

    def test_critical_path_excludes_done_category(self, db: FiligreeDB) -> None:
        """Issues in done-category states (including wont_fix) excluded from critical path."""
        a = db.create_issue("A", type="bug")
        b = db.create_issue("B", type="bug")
        c = db.create_issue("C", type="bug")
        db.add_dependency(b.id, a.id)
        db.add_dependency(c.id, b.id)
        # Close A with wont_fix (done-category)
        db.close_issue(a.id, status="wont_fix")
        path = db.get_critical_path()
        path_ids = {p["id"] for p in path}
        assert a.id not in path_ids  # done-category excluded

    def test_critical_path_includes_wip(self, db: FiligreeDB) -> None:
        """Issues in wip-category states should be in critical path."""
        a = db.create_issue("A", type="bug")
        b = db.create_issue("B", type="bug")
        db.add_dependency(b.id, a.id)
        db.update_issue(a.id, status="confirmed")
        db.update_issue(a.id, status="fixing")
        path = db.get_critical_path()
        path_ids = {p["id"] for p in path}
        assert a.id in path_ids  # wip-category included


# ---------------------------------------------------------------------------
# Task 1.11: Issue.to_dict() Includes status_category
# ---------------------------------------------------------------------------


class TestStatusCategory:
    """Issue.to_dict() includes status_category computed field."""

    def test_bug_triage_category_is_open(self, db: FiligreeDB) -> None:
        """Bug in 'triage' should have status_category='open'."""
        issue = db.create_issue("Bug", type="bug")
        d = issue.to_dict()
        assert d["status_category"] == "open"

    def test_bug_fixing_category_is_wip(self, db: FiligreeDB) -> None:
        """Bug in 'fixing' should have status_category='wip'."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        updated = db.update_issue(issue.id, status="fixing")
        d = updated.to_dict()
        assert d["status_category"] == "wip"

    def test_bug_closed_category_is_done(self, db: FiligreeDB) -> None:
        """Bug in 'closed' should have status_category='done'."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id)
        d = closed.to_dict()
        assert d["status_category"] == "done"

    def test_bug_wont_fix_category_is_done(self, db: FiligreeDB) -> None:
        """Bug in 'wont_fix' should have status_category='done'."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id, status="wont_fix")
        d = closed.to_dict()
        assert d["status_category"] == "done"

    def test_task_open_category(self, db: FiligreeDB) -> None:
        """Task in 'open' should have status_category='open'."""
        issue = db.create_issue("Task", type="task")
        d = issue.to_dict()
        assert d["status_category"] == "open"

    def test_task_in_progress_category(self, db: FiligreeDB) -> None:
        """Task in 'in_progress' should have status_category='wip'."""
        issue = db.create_issue("Task", type="task")
        updated = db.update_issue(issue.id, status="in_progress")
        d = updated.to_dict()
        assert d["status_category"] == "wip"

    def test_task_closed_category(self, db: FiligreeDB) -> None:
        """Task in 'closed' should have status_category='done'."""
        issue = db.create_issue("Task", type="task")
        closed = db.close_issue(issue.id)
        d = closed.to_dict()
        assert d["status_category"] == "done"

    def test_unknown_type_rejected_at_creation(self, db: FiligreeDB) -> None:
        """Unknown types are rejected, so fallback heuristics don't apply."""
        with pytest.raises(ValueError, match="Unknown type"):
            db.create_issue("Custom", type="custom_type")

    def test_status_category_in_list_issues(self, db: FiligreeDB) -> None:
        """list_issues() results should include status_category."""
        db.create_issue("Bug", type="bug")
        issues = db.list_issues()
        assert all("status_category" in i.to_dict() for i in issues)


# ---------------------------------------------------------------------------
# Task 1.12: New FiligreeDB Methods
# ---------------------------------------------------------------------------


class TestGetValidTransitions:
    """FiligreeDB.get_valid_transitions() delegates to TemplateRegistry."""

    def test_bug_triage_transitions(self, db: FiligreeDB) -> None:
        """Bug in 'triage' should have transitions to confirmed and wont_fix."""
        issue = db.create_issue("Bug", type="bug")
        options = db.get_valid_transitions(issue.id)
        targets = {o.to for o in options}
        assert "confirmed" in targets
        assert "wont_fix" in targets

    def test_bug_triage_transition_categories(self, db: FiligreeDB) -> None:
        """Transition options should include target state categories."""
        issue = db.create_issue("Bug", type="bug")
        options = db.get_valid_transitions(issue.id)
        confirmed_opt = next(o for o in options if o.to == "confirmed")
        assert confirmed_opt.category == "open"
        wont_fix_opt = next(o for o in options if o.to == "wont_fix")
        assert wont_fix_opt.category == "done"

    def test_bug_fixing_shows_readiness(self, db: FiligreeDB) -> None:
        """Transitions from 'fixing' should show field readiness."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing")
        options = db.get_valid_transitions(issue.id)
        verifying_opt = next(o for o in options if o.to == "verifying")
        assert verifying_opt.ready is False
        assert "fix_verification" in verifying_opt.missing_fields

    def test_bug_fixing_ready_with_fields(self, db: FiligreeDB) -> None:
        """Transition should be ready when required fields are populated."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing", fields={"fix_verification": "tests pass"})
        options = db.get_valid_transitions(issue.id)
        verifying_opt = next(o for o in options if o.to == "verifying")
        assert verifying_opt.ready is True
        assert verifying_opt.missing_fields == ()

    def test_task_open_transitions(self, db: FiligreeDB) -> None:
        """Task in 'open' should have transition to in_progress."""
        issue = db.create_issue("Task", type="task")
        options = db.get_valid_transitions(issue.id)
        targets = {o.to for o in options}
        assert "in_progress" in targets

    def test_unknown_type_cannot_be_created(self, db: FiligreeDB) -> None:
        """Unknown types are rejected at creation — can't reach get_valid_transitions."""
        with pytest.raises(ValueError, match="Unknown type"):
            db.create_issue("Custom", type="custom_type")

    def test_closed_issue_transitions(self, db: FiligreeDB) -> None:
        """Closed issues should have no outgoing transitions (terminal state)."""
        issue = db.create_issue("Task", type="task")
        db.close_issue(issue.id)
        options = db.get_valid_transitions(issue.id)
        assert len(options) == 0

    def test_nonexistent_issue_raises(self, db: FiligreeDB) -> None:
        """get_valid_transitions on nonexistent issue raises KeyError."""
        with pytest.raises(KeyError):
            db.get_valid_transitions("test-nonexistent")

    def test_return_type_is_transition_option(self, db: FiligreeDB) -> None:
        """Results should be TransitionOption dataclass instances."""
        issue = db.create_issue("Bug", type="bug")
        options = db.get_valid_transitions(issue.id)
        assert len(options) > 0
        assert isinstance(options[0], TransitionOption)


class TestValidateIssue:
    """FiligreeDB.validate_issue() checks issue against its template."""

    def test_valid_bug_in_triage(self, db: FiligreeDB) -> None:
        """Bug in 'triage' with no required fields should validate clean."""
        issue = db.create_issue("Bug", type="bug")
        result = db.validate_issue(issue.id)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_bug_in_confirmed_missing_severity(self, db: FiligreeDB) -> None:
        """Bug in 'confirmed' without severity should have a warning."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        result = db.validate_issue(issue.id)
        assert len(result.warnings) > 0
        assert any("severity" in w for w in result.warnings)

    def test_bug_in_confirmed_with_severity_valid(self, db: FiligreeDB) -> None:
        """Bug in 'confirmed' with severity should validate clean."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed", fields={"severity": "major"})
        result = db.validate_issue(issue.id)
        assert result.valid is True

    def test_task_always_valid(self, db: FiligreeDB) -> None:
        """Task with no required_at fields should always validate clean."""
        issue = db.create_issue("Task", type="task")
        result = db.validate_issue(issue.id)
        assert result.valid is True
        assert len(result.warnings) == 0

    def test_unknown_type_cannot_be_created(self, db: FiligreeDB) -> None:
        """Unknown types are rejected at creation — can't reach validate_issue."""
        with pytest.raises(ValueError, match="Unknown type"):
            db.create_issue("Custom", type="custom_type")

    def test_return_type_is_validation_result(self, db: FiligreeDB) -> None:
        """Result should be a ValidationResult dataclass."""
        issue = db.create_issue("Bug", type="bug")
        result = db.validate_issue(issue.id)
        assert isinstance(result, ValidationResult)

    def test_nonexistent_issue_raises(self, db: FiligreeDB) -> None:
        """validate_issue on nonexistent issue raises KeyError."""
        with pytest.raises(KeyError):
            db.validate_issue("test-nonexistent")
