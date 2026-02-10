# Phase 1C: Behavior Change (Per-Type Validation)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Flip the switch -- issue creation, updates, closing, and queries now use per-type state machines and category mappings. This is the high-risk phase where existing behavior changes.

**PR Strategy:** Single PR with thorough regression testing. The `enabled_packs: ["core", "planning"]` default means only core+planning types activate. Existing task/epic/bug/feature types in the core pack preserve their open/in\_progress/closed states, so backward compatibility is maintained.

**Prerequisites:**
- Phase 1B merged (templates load, schema migrated, KeelDB.templates works)
- `make ci` passes clean

**Parent plan:** `2026-02-11-workflow-templates-implementation.md`
**Depends on:** `2026-02-11-phase-1b-integration.md`

**Design doc:** `2026-02-11-workflow-templates-design.md`
**Requirements doc:** `2026-02-11-workflow-templates-requirements.md`

---

## Task Summary

| Task | What | Files modified | Lines (est) |
|------|------|----------------|-------------|
| 1.9 | Per-type status validation in 6 core methods | `src/keel/core.py`, `tests/test_workflow_behavior.py` | ~200 impl, ~350 test |
| 1.10 | Category-aware queries (list, ready, blocked, critical path) | `src/keel/core.py`, `tests/test_workflow_behavior.py` | ~120 impl, ~200 test |
| 1.11 | Issue.to_dict() includes status_category | `src/keel/core.py`, `tests/test_workflow_behavior.py` | ~40 impl, ~80 test |
| 1.12 | get_valid_transitions() and validate_issue() methods | `src/keel/core.py`, `tests/test_workflow_behavior.py` | ~60 impl, ~120 test |
| 1.15 | Regression test suite | `tests/test_backward_compat.py` | ~0 impl, ~250 test |

---

## File Change Summary

| File | Action | What changes |
|------|--------|--------------|
| `src/keel/core.py` | Modify | `_validate_status()`, `create_issue()`, `update_issue()`, `close_issue()`, `claim_issue()`, `release_claim()`, `list_issues()`, `get_ready()`, `get_blocked()`, `get_critical_path()`, `_build_issues_batch()`, `Issue` dataclass, `Issue.to_dict()`, add `get_valid_transitions()`, `validate_issue()`, `_get_states_for_category()` |
| `tests/test_workflow_behavior.py` | Create | Integration tests for Tasks 1.9-1.12 |
| `tests/test_backward_compat.py` | Create | Regression tests for Task 1.15 |

---

## Task 1.9: Core Engine -- Per-Type Status Validation

Modify `_validate_status()`, `create_issue()`, `update_issue()`, `close_issue()`, `claim_issue()`, `release_claim()` to use the template system. This is the highest-risk task -- it changes the behavior of 6 existing methods while maintaining backward compatibility (WFT-AR-011).

**Files:**
- Modify: `src/keel/core.py`
- Create: `tests/test_workflow_behavior.py`

**Requirements covered:** WFT-FR-006, WFT-FR-007, WFT-FR-009, WFT-FR-017, WFT-FR-018, WFT-FR-046, WFT-FR-047, WFT-FR-048, WFT-FR-049, WFT-FR-069, WFT-SR-004

### Step 1: Write the failing tests

Create `tests/test_workflow_behavior.py`:

```python
# tests/test_workflow_behavior.py
"""Integration tests for per-type status validation and transition enforcement.

Tests the core.py behavior changes introduced in Phase 1C. These tests exercise
the full stack: KeelDB -> TemplateRegistry -> SQLite, verifying that issue
lifecycle operations respect per-type state machines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from keel.core import KeelDB, Issue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> KeelDB:
    """A KeelDB instance with templates loaded (core + planning packs enabled)."""
    keel_dir = tmp_path / ".keel"
    keel_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}
    (keel_dir / "config.json").write_text(json.dumps(config))

    db = KeelDB(keel_dir / "keel.db", prefix="test")
    db.initialize()
    return db


@pytest.fixture()
def db_no_packs(tmp_path: Path) -> KeelDB:
    """A KeelDB instance with NO enabled packs (feature flag off)."""
    keel_dir = tmp_path / ".keel"
    keel_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": []}
    (keel_dir / "config.json").write_text(json.dumps(config))

    db = KeelDB(keel_dir / "keel.db", prefix="test")
    db.initialize()
    return db


# ---------------------------------------------------------------------------
# Task 1.9: Per-Type Status Validation
# ---------------------------------------------------------------------------


class TestCreateIssueInitialState:
    """create_issue() uses type-specific initial state from template."""

    def test_bug_initial_state_is_triage(self, db: KeelDB) -> None:
        """Bug type should start in 'triage', not 'open'."""
        issue = db.create_issue("Fix crash on startup", type="bug")
        assert issue.status == "triage"

    def test_task_initial_state_is_open(self, db: KeelDB) -> None:
        """Task type preserves legacy 'open' initial state."""
        issue = db.create_issue("Update docs", type="task")
        assert issue.status == "open"

    def test_epic_initial_state_is_open(self, db: KeelDB) -> None:
        """Epic type preserves legacy 'open' initial state."""
        issue = db.create_issue("Workflow v2", type="epic")
        assert issue.status == "open"

    def test_feature_initial_state_is_open(self, db: KeelDB) -> None:
        """Feature type preserves legacy 'open' initial state."""
        issue = db.create_issue("Add search", type="feature")
        assert issue.status == "open"

    def test_milestone_initial_state(self, db: KeelDB) -> None:
        """Milestone type should use its template initial state."""
        issue = db.create_issue("v2.0 Release", type="milestone")
        assert issue.status == "open"

    def test_unknown_type_initial_state_is_open(self, db: KeelDB) -> None:
        """Unknown types fall back to 'open' initial state."""
        issue = db.create_issue("Something", type="custom_type")
        assert issue.status == "open"

    def test_no_packs_initial_state_is_open(self, db_no_packs: KeelDB) -> None:
        """With no packs enabled, all types start in 'open'."""
        issue = db_no_packs.create_issue("Fix crash", type="bug")
        assert issue.status == "open"


class TestValidateStatus:
    """_validate_status() checks type-specific valid states via templates."""

    def test_valid_bug_state_accepted(self, db: KeelDB) -> None:
        """Bug-specific states like 'triage' should be accepted."""
        issue = db.create_issue("Bug", type="bug")
        updated = db.update_issue(issue.id, status="confirmed")
        assert updated.status == "confirmed"

    def test_invalid_bug_state_rejected(self, db: KeelDB) -> None:
        """States not in the bug template should raise ValueError."""
        issue = db.create_issue("Bug", type="bug")
        with pytest.raises(ValueError, match="Invalid status|not defined"):
            db.update_issue(issue.id, status="nonexistent_state")

    def test_task_legacy_states_accepted(self, db: KeelDB) -> None:
        """Task type must accept open/in_progress/closed (backward compat)."""
        issue = db.create_issue("Task", type="task")
        updated = db.update_issue(issue.id, status="in_progress")
        assert updated.status == "in_progress"
        updated = db.update_issue(issue.id, status="closed")
        assert updated.status == "closed"

    def test_unknown_type_uses_global_states(self, db: KeelDB) -> None:
        """Unknown types fall back to global workflow_states."""
        issue = db.create_issue("Custom", type="custom_type")
        # Global default: open, in_progress, closed
        updated = db.update_issue(issue.id, status="in_progress")
        assert updated.status == "in_progress"


class TestUpdateIssueTransitionEnforcement:
    """update_issue() validates transitions with soft/hard enforcement."""

    def test_valid_soft_transition_succeeds(self, db: KeelDB) -> None:
        """Soft transition triage -> confirmed should succeed."""
        issue = db.create_issue("Bug", type="bug")
        updated = db.update_issue(issue.id, status="confirmed")
        assert updated.status == "confirmed"

    def test_hard_enforcement_rejects_missing_fields(self, db: KeelDB) -> None:
        """Hard enforcement: verifying -> closed without fix_verification raises ValueError."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing")
        db.update_issue(issue.id, status="verifying",
                        fields={"fix_verification": "tests pass"})
        # Now try to close without fix_verification being populated
        # (verifying -> closed is hard-enforced on fix_verification)
        # The issue already has fix_verification set, so this should succeed:
        updated = db.update_issue(issue.id, status="closed")
        assert updated.status == "closed"

    def test_hard_enforcement_blocks_with_empty_field(self, db: KeelDB) -> None:
        """Hard enforcement: empty string field should be treated as missing."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing")
        db.update_issue(issue.id, status="verifying",
                        fields={"fix_verification": "initial"})
        # Clear the field, then try hard transition
        db.update_issue(issue.id, fields={"fix_verification": ""})
        with pytest.raises(ValueError, match="fix_verification"):
            db.update_issue(issue.id, status="closed")

    def test_soft_enforcement_proceeds_with_warning(self, db: KeelDB) -> None:
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

    def test_atomic_transition_with_fields_succeeds(self, db: KeelDB) -> None:
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

    def test_atomic_transition_hard_failure_rolls_back(self, db: KeelDB) -> None:
        """WFT-FR-069: On hard failure, neither fields NOR status are saved."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing")
        db.update_issue(issue.id, status="verifying",
                        fields={"fix_verification": "initial"})
        # Try to close with fix_verification="" (hard enforcement)
        # Passing other fields along should NOT be saved on failure
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

    def test_update_issue_sets_closed_at_for_done_category(self, db: KeelDB) -> None:
        """closed_at should be set when entering any done-category state, not just 'closed'."""
        issue = db.create_issue("Bug", type="bug")
        # triage -> wont_fix (done-category)
        updated = db.update_issue(issue.id, status="wont_fix")
        assert updated.closed_at is not None

    def test_legacy_task_closed_at_still_works(self, db: KeelDB) -> None:
        """Task: status='closed' still sets closed_at (backward compat)."""
        issue = db.create_issue("Task", type="task")
        db.update_issue(issue.id, status="in_progress")
        updated = db.update_issue(issue.id, status="closed")
        assert updated.closed_at is not None


class TestCloseIssue:
    """close_issue() accepts optional status parameter for multi-done types."""

    def test_close_bug_default_done_state(self, db: KeelDB) -> None:
        """close_issue() without status uses first done-category state."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id)
        assert closed.status == "closed"  # 'closed' is first done state for bug

    def test_close_bug_with_specific_done_state(self, db: KeelDB) -> None:
        """close_issue(status='wont_fix') uses specified done state."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id, status="wont_fix")
        assert closed.status == "wont_fix"

    def test_close_bug_rejects_non_done_state(self, db: KeelDB) -> None:
        """close_issue() with a non-done state raises ValueError."""
        issue = db.create_issue("Bug", type="bug")
        with pytest.raises(ValueError, match="done"):
            db.close_issue(issue.id, status="fixing")

    def test_close_task_default(self, db: KeelDB) -> None:
        """close_issue() on task type uses 'closed' (backward compat)."""
        issue = db.create_issue("Task", type="task")
        closed = db.close_issue(issue.id)
        assert closed.status == "closed"

    def test_close_already_closed_noop(self, db: KeelDB) -> None:
        """close_issue() on already-closed issue is a no-op."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id)
        closed_again = db.close_issue(closed.id)
        assert closed_again.closed_at == closed.closed_at


class TestClaimIssue:
    """claim_issue() uses first wip-category state from template."""

    def test_claim_bug_uses_fixing(self, db: KeelDB) -> None:
        """Bug type claim should transition to first wip state (fixing),
        but only from an open-category state. Since bug starts in 'triage',
        claim should work and use the first wip state."""
        issue = db.create_issue("Bug", type="bug")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        # First wip-category state for bug is 'fixing'
        assert claimed.status == "fixing"
        assert claimed.assignee == "agent-1"

    def test_claim_task_uses_in_progress(self, db: KeelDB) -> None:
        """Task type claim uses 'in_progress' (backward compat)."""
        issue = db.create_issue("Task", type="task")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        assert claimed.status == "in_progress"
        assert claimed.assignee == "agent-1"

    def test_claim_already_wip_fails(self, db: KeelDB) -> None:
        """Cannot claim an issue that's already in a wip-category state."""
        issue = db.create_issue("Task", type="task")
        db.claim_issue(issue.id, assignee="agent-1")
        with pytest.raises(ValueError, match="Cannot claim"):
            db.claim_issue(issue.id, assignee="agent-2")

    def test_claim_unknown_type_uses_in_progress(self, db: KeelDB) -> None:
        """Unknown types fall back to 'in_progress'."""
        issue = db.create_issue("Custom", type="custom_type")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        assert claimed.status == "in_progress"


class TestReleaseClaim:
    """release_claim() uses initial state from template instead of hardcoded 'open'."""

    def test_release_bug_returns_to_triage(self, db: KeelDB) -> None:
        """Bug type release should return to initial state ('triage')."""
        issue = db.create_issue("Bug", type="bug")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        released = db.release_claim(claimed.id)
        assert released.status == "triage"
        assert released.assignee == ""

    def test_release_task_returns_to_open(self, db: KeelDB) -> None:
        """Task type release uses 'open' (backward compat)."""
        issue = db.create_issue("Task", type="task")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        released = db.release_claim(claimed.id)
        assert released.status == "open"
        assert released.assignee == ""

    def test_release_non_wip_fails(self, db: KeelDB) -> None:
        """Cannot release an issue that is not in a wip-category state."""
        issue = db.create_issue("Task", type="task")
        with pytest.raises(ValueError, match="Cannot release"):
            db.release_claim(issue.id)

    def test_release_unknown_type_returns_to_open(self, db: KeelDB) -> None:
        """Unknown types fall back to 'open'."""
        issue = db.create_issue("Custom", type="custom_type")
        claimed = db.claim_issue(issue.id, assignee="agent-1")
        released = db.release_claim(claimed.id)
        assert released.status == "open"
```

**Why these tests:** They cover every method being modified, exercise both the new template-driven path and the backward-compatible legacy path, test atomic transition-with-fields (WFT-FR-069) including rollback, and verify enforcement levels (soft warnings recorded, hard blocks enforced).

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_workflow_behavior.py -v
```

Expected: Tests fail because `create_issue()` still hardcodes `'open'`, `claim_issue()` still hardcodes `'in_progress'`, etc.

### Step 3: Implement changes to core.py

**3a. Modify `_validate_status()` (currently at line 567)**

Replace the simple list-membership check with template-aware validation:

```python
def _validate_status(self, status: str, issue_type: str = "task") -> None:
    """Validate status against type-specific states or global workflow states.

    If the issue type has a registered template, validates against that template's
    state list. Otherwise falls back to the global workflow_states config.
    """
    tpl = self.templates.get_type(issue_type) if hasattr(self, 'templates') and self.templates else None
    if tpl is not None:
        valid_states = self.templates.get_valid_states(issue_type)
        if status not in valid_states:
            msg = (
                f"Invalid status '{status}' for type '{issue_type}'. "
                f"Valid states: {', '.join(valid_states)}"
            )
            raise ValueError(msg)
    else:
        # Fallback: global workflow_states (backward compat)
        if status not in self.workflow_states:
            msg = f"Invalid status '{status}'. Valid states: {', '.join(self.workflow_states)}"
            raise ValueError(msg)
```

**3b. Modify `create_issue()` (currently at line 579)**

Use type-specific initial state instead of hardcoded `'open'`:

```python
def create_issue(
    self,
    title: str,
    *,
    type: str = "task",
    priority: int = 2,
    parent_id: str | None = None,
    assignee: str = "",
    description: str = "",
    notes: str = "",
    fields: dict[str, Any] | None = None,
    labels: list[str] | None = None,
    deps: list[str] | None = None,
    actor: str = "",
) -> Issue:
    existing = {r["id"] for r in self.conn.execute("SELECT id FROM issues").fetchall()}
    issue_id = _generate_id(self.prefix, existing_ids=existing)
    now = _now_iso()
    fields = fields or {}

    # Determine initial state from template, fall back to 'open'
    initial_state = "open"
    if hasattr(self, "templates") and self.templates:
        initial_state = self.templates.get_initial_state(type)

    self.conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, parent_id, assignee, "
        "created_at, updated_at, description, notes, fields) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (issue_id, title, initial_state, priority, type, parent_id, assignee,
         now, now, description, notes, json.dumps(fields)),
    )

    self._record_event(issue_id, "created", actor=actor, new_value=title)

    if labels:
        for label in labels:
            self.conn.execute(
                "INSERT OR IGNORE INTO labels (issue_id, label) VALUES (?, ?)",
                (issue_id, label),
            )

    if deps:
        for dep_id in deps:
            self.conn.execute(
                "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) "
                "VALUES (?, ?, 'blocks', ?)",
                (issue_id, dep_id, now),
            )

    self.conn.commit()
    return self.get_issue(issue_id)
```

**3c. Modify `update_issue()` (currently at line 724)**

Implement atomic transition-with-fields (WFT-FR-069): merge fields BEFORE status validation. On hard failure, neither fields nor status are saved.

```python
def update_issue(
    self,
    issue_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    assignee: str | None = None,
    description: str | None = None,
    notes: str | None = None,
    fields: dict[str, Any] | None = None,
    actor: str = "",
) -> Issue:
    current = self.get_issue(issue_id)
    now = _now_iso()
    updates: list[str] = []
    params: list[Any] = []

    if title is not None and title != current.title:
        self._record_event(issue_id, "title_changed", actor=actor,
                           old_value=current.title, new_value=title)
        updates.append("title = ?")
        params.append(title)

    if status is not None and status != current.status:
        self._validate_status(status, current.type)

        # WFT-FR-069: Atomic transition-with-fields
        # Merge proposed fields into current fields BEFORE transition validation
        # so that hard enforcement sees the fields being set in this same call.
        merged_fields = {**current.fields}
        if fields is not None:
            merged_fields.update(fields)

        # Validate transition via template system
        if hasattr(self, "templates") and self.templates:
            result = self.templates.validate_transition(
                current.type, current.status, status, merged_fields
            )
            if not result.allowed:
                # Hard enforcement failure -- raise with missing field names
                missing_str = ", ".join(result.missing_fields)
                msg = (
                    f"Cannot transition '{current.status}' -> '{status}' for type "
                    f"'{current.type}': missing required fields: {missing_str}. "
                    f"Populate these fields before transitioning, or call "
                    f"get_type_info('{current.type}') for field details."
                )
                raise ValueError(msg)

            # Soft enforcement: record warning events
            if result.warnings:
                for warning in result.warnings:
                    self._record_event(
                        issue_id, "transition_warning", actor=actor,
                        old_value=current.status, new_value=status,
                        comment=warning,
                    )
            if result.missing_fields and result.enforcement == "soft":
                self._record_event(
                    issue_id, "transition_warning", actor=actor,
                    old_value=current.status, new_value=status,
                    comment=f"Missing recommended fields: {', '.join(result.missing_fields)}",
                )

        self._record_event(issue_id, "status_changed", actor=actor,
                           old_value=current.status, new_value=status)
        updates.append("status = ?")
        params.append(status)

        # Set closed_at when entering a done-category state
        is_done = False
        if hasattr(self, "templates") and self.templates:
            category = self.templates.get_category(current.type, status)
            is_done = category == "done"
        else:
            is_done = status == "closed"

        if is_done:
            updates.append("closed_at = ?")
            params.append(now)

    if priority is not None and priority != current.priority:
        self._record_event(
            issue_id, "priority_changed", actor=actor,
            old_value=str(current.priority), new_value=str(priority)
        )
        updates.append("priority = ?")
        params.append(priority)

    if assignee is not None and assignee != current.assignee:
        self._record_event(
            issue_id, "assignee_changed", actor=actor,
            old_value=current.assignee, new_value=assignee
        )
        updates.append("assignee = ?")
        params.append(assignee)

    if description is not None:
        updates.append("description = ?")
        params.append(description)

    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)

    if fields is not None:
        # Merge into existing fields
        merged = {**current.fields, **fields}
        updates.append("fields = ?")
        params.append(json.dumps(merged))

    if updates:
        updates.append("updated_at = ?")
        params.append(now)
        params.append(issue_id)
        sql = f"UPDATE issues SET {', '.join(updates)} WHERE id = ?"
        self.conn.execute(sql, params)
        self.conn.commit()

    return self.get_issue(issue_id)
```

**3d. Modify `close_issue()` (currently at line 794)**

Accept optional `status` parameter for multi-done types:

```python
def close_issue(
    self, issue_id: str, *, reason: str = "", actor: str = "",
    status: str | None = None,
) -> Issue:
    current = self.get_issue(issue_id)

    # Determine done state
    if hasattr(self, "templates") and self.templates:
        # Check if already in a done-category state
        current_category = self.templates.get_category(current.type, current.status)
        if current_category == "done":
            return current  # Already done -- don't overwrite closed_at

        if status is not None:
            # Validate that the requested status is a done-category state
            target_category = self.templates.get_category(current.type, status)
            if target_category != "done":
                msg = (
                    f"Cannot close with status '{status}': it is not a done-category "
                    f"state for type '{current.type}'."
                )
                raise ValueError(msg)
            done_status = status
        else:
            # Default to first done-category state
            done_status = self.templates.get_first_state_of_category(current.type, "done")
            if done_status is None:
                done_status = "closed"  # Ultimate fallback
    else:
        # Legacy behavior
        if current.status == "closed":
            return current
        done_status = status or "closed"

    return self.update_issue(
        issue_id,
        status=done_status,
        fields={"close_reason": reason} if reason else None,
        actor=actor,
    )
```

**3e. Modify `claim_issue()` (currently at line 805)**

Use first wip-category state from template instead of hardcoded `'in_progress'`:

```python
def claim_issue(self, issue_id: str, *, assignee: str, actor: str = "") -> Issue:
    """Atomically claim an open-category issue with optimistic locking.

    Uses the type's first wip-category state instead of hardcoded 'in_progress'.
    Only succeeds if the issue is currently in an open-category state.
    """
    current = self.get_issue(issue_id)

    # Determine wip state and valid source states
    if hasattr(self, "templates") and self.templates:
        wip_state = self.templates.get_first_state_of_category(current.type, "wip")
        if wip_state is None:
            wip_state = "in_progress"  # Fallback

        # Get all open-category states for this type
        open_states: list[str] = []
        tpl = self.templates.get_type(current.type)
        if tpl is not None:
            open_states = [s.name for s in tpl.states if s.category == "open"]
        if not open_states:
            open_states = ["open"]

        # Build parameterized IN clause
        placeholders = ",".join("?" * len(open_states))
        row = self.conn.execute(
            f"UPDATE issues SET status = ?, assignee = ?, updated_at = ? "
            f"WHERE id = ? AND status IN ({placeholders})",
            [wip_state, assignee, _now_iso(), issue_id, *open_states],
        )
    else:
        # Legacy behavior
        wip_state = "in_progress"
        row = self.conn.execute(
            "UPDATE issues SET status = 'in_progress', assignee = ?, updated_at = ? "
            "WHERE id = ? AND status = 'open'",
            (assignee, _now_iso(), issue_id),
        )

    if row.rowcount == 0:
        exists = self.conn.execute(
            "SELECT status FROM issues WHERE id = ?", (issue_id,)
        ).fetchone()
        if exists is None:
            msg = f"Issue not found: {issue_id}"
            raise KeyError(msg)
        msg = f"Cannot claim {issue_id}: status is '{exists['status']}', expected open-category state"
        raise ValueError(msg)

    self._record_event(issue_id, "claimed", actor=actor, new_value=assignee)
    self.conn.commit()
    return self.get_issue(issue_id)
```

**3f. Modify `release_claim()` (currently at line 827)**

Use initial state from template instead of hardcoded `'open'`:

```python
def release_claim(self, issue_id: str, *, actor: str = "") -> Issue:
    """Release a claimed issue back to its initial (open-category) state.

    Uses the type's initial state instead of hardcoded 'open'.
    Only succeeds if the issue is currently in a wip-category state.
    """
    current = self.get_issue(issue_id)

    # Determine target state and valid source states
    if hasattr(self, "templates") and self.templates:
        initial_state = self.templates.get_initial_state(current.type)

        # Get all wip-category states for this type
        wip_states: list[str] = []
        tpl = self.templates.get_type(current.type)
        if tpl is not None:
            wip_states = [s.name for s in tpl.states if s.category == "wip"]
        if not wip_states:
            wip_states = ["in_progress"]

        placeholders = ",".join("?" * len(wip_states))
        row = self.conn.execute(
            f"UPDATE issues SET status = ?, assignee = '', updated_at = ? "
            f"WHERE id = ? AND status IN ({placeholders})",
            [initial_state, _now_iso(), issue_id, *wip_states],
        )
    else:
        # Legacy behavior
        initial_state = "open"
        row = self.conn.execute(
            "UPDATE issues SET status = 'open', assignee = '', updated_at = ? "
            "WHERE id = ? AND status = 'in_progress'",
            (_now_iso(), issue_id),
        )

    if row.rowcount == 0:
        exists = self.conn.execute(
            "SELECT status FROM issues WHERE id = ?", (issue_id,)
        ).fetchone()
        if exists is None:
            msg = f"Issue not found: {issue_id}"
            raise KeyError(msg)
        msg = f"Cannot release {issue_id}: status is '{exists['status']}', expected wip-category state"
        raise ValueError(msg)

    self._record_event(issue_id, "released", actor=actor)
    self.conn.commit()
    return self.get_issue(issue_id)
```

### Step 4: Run tests to verify they pass

```bash
uv run pytest tests/test_workflow_behavior.py::TestCreateIssueInitialState -v
uv run pytest tests/test_workflow_behavior.py::TestValidateStatus -v
uv run pytest tests/test_workflow_behavior.py::TestUpdateIssueTransitionEnforcement -v
uv run pytest tests/test_workflow_behavior.py::TestCloseIssue -v
uv run pytest tests/test_workflow_behavior.py::TestClaimIssue -v
uv run pytest tests/test_workflow_behavior.py::TestReleaseClaim -v
```

Expected: All tests pass.

### Step 5: Run full CI

```bash
make ci
```

Expected: All existing tests still pass. Ruff and mypy clean. No regressions.

### Step 6: Commit

```bash
git add src/keel/core.py tests/test_workflow_behavior.py
git commit -m "feat(core): per-type status validation and transition enforcement

- _validate_status() checks per-type states via TemplateRegistry
- create_issue() uses get_initial_state() per type
- update_issue() validates transitions with soft/hard enforcement
- Atomic transition-with-fields: merge fields BEFORE status check (WFT-FR-069)
- Hard enforcement raises ValueError with missing field names
- Soft enforcement proceeds + records warning events
- close_issue() supports optional status parameter for multi-done types
- claim_issue() transitions to first wip-category state
- release_claim() returns to initial state from template
- closed_at set on entering done-category, not just literal 'closed'
- Backward compatible: task/epic/feature use open/in_progress/closed

Implements: WFT-FR-006, WFT-FR-007, WFT-FR-009, WFT-FR-017, WFT-FR-018,
WFT-FR-046, WFT-FR-047, WFT-FR-048, WFT-FR-049, WFT-FR-069, WFT-SR-004

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Definition of Done
- [ ] `create_issue(type="bug")` creates with initial state "triage"
- [ ] `create_issue(type="task")` still creates with "open" (backward compat)
- [ ] `_validate_status()` checks type-specific states via TemplateRegistry
- [ ] Unknown types fall back to global `workflow_states`
- [ ] `update_issue()` validates transitions with enforcement levels
- [ ] Hard enforcement rejects missing required fields with descriptive error
- [ ] Soft enforcement proceeds and records `transition_warning` events
- [ ] Atomic transition-with-fields: fields merged before validation (WFT-FR-069)
- [ ] Atomic transition-with-fields: hard failure saves neither fields nor status
- [ ] `close_issue()` accepts optional `status` parameter
- [ ] `close_issue()` defaults to first done-category state
- [ ] `close_issue(status="fixing")` raises ValueError (non-done state)
- [ ] `claim_issue()` uses first wip-category state from template
- [ ] `release_claim()` returns to initial state from template
- [ ] `closed_at` set when entering any done-category state
- [ ] All existing tests still pass (backward compat verified)
- [ ] `make ci` passes clean

---

## Task 1.10: Category-Aware Queries

Modify `list_issues()`, `get_ready()`, `get_blocked()`, and `get_critical_path()` to use category mapping instead of literal status strings. Add `_get_states_for_category()` helper with parameterized SQL placeholders (review B1) and empty state list guard (W7).

**Files:**
- Modify: `src/keel/core.py`
- Modify: `tests/test_workflow_behavior.py`

**Requirements covered:** WFT-FR-009, WFT-FR-048, WFT-SR-012, WFT-SR-015

### Step 1: Write the failing tests

Add to `tests/test_workflow_behavior.py`:

```python
# ---------------------------------------------------------------------------
# Task 1.10: Category-Aware Queries
# ---------------------------------------------------------------------------


class TestGetStatesForCategory:
    """_get_states_for_category() collects states across all enabled types."""

    def test_open_category_includes_triage(self, db: KeelDB) -> None:
        """Open category should include bug's 'triage' and 'confirmed' states."""
        states = db._get_states_for_category("open")
        assert "open" in states  # from task
        assert "triage" in states  # from bug
        assert "confirmed" in states  # from bug

    def test_wip_category_includes_fixing(self, db: KeelDB) -> None:
        """Wip category should include bug's 'fixing' and 'verifying' states."""
        states = db._get_states_for_category("wip")
        assert "in_progress" in states  # from task
        assert "fixing" in states  # from bug
        assert "verifying" in states  # from bug

    def test_done_category_includes_wont_fix(self, db: KeelDB) -> None:
        """Done category should include bug's 'closed' and 'wont_fix' states."""
        states = db._get_states_for_category("done")
        assert "closed" in states  # from task
        assert "wont_fix" in states  # from bug

    def test_no_packs_returns_empty(self, db_no_packs: KeelDB) -> None:
        """With no packs enabled, _get_states_for_category returns empty list."""
        states = db_no_packs._get_states_for_category("open")
        assert states == []

    def test_no_duplicates(self, db: KeelDB) -> None:
        """State names should not appear twice even if multiple types share a state."""
        states = db._get_states_for_category("open")
        assert len(states) == len(set(states))


class TestListIssuesCategory:
    """list_issues(status=) accepts category names and specific states."""

    def test_list_by_open_category(self, db: KeelDB) -> None:
        """list_issues(status='open') returns issues in any open-category state."""
        bug = db.create_issue("Bug", type="bug")       # status=triage (open category)
        task = db.create_issue("Task", type="task")     # status=open (open category)
        db.update_issue(task.id, status="in_progress")  # now wip

        issues = db.list_issues(status="open")
        ids = {i.id for i in issues}
        assert bug.id in ids    # triage is open-category
        assert task.id not in ids  # in_progress is wip-category

    def test_list_by_wip_category(self, db: KeelDB) -> None:
        """list_issues(status='wip') returns issues in any wip-category state."""
        bug = db.create_issue("Bug", type="bug")
        db.update_issue(bug.id, status="confirmed")
        db.update_issue(bug.id, status="fixing")
        task = db.create_issue("Task", type="task")

        issues = db.list_issues(status="wip")
        ids = {i.id for i in issues}
        assert bug.id in ids    # fixing is wip-category
        assert task.id not in ids  # open is open-category

    def test_list_by_specific_state(self, db: KeelDB) -> None:
        """list_issues(status='triage') returns only bugs in literal 'triage' state."""
        bug = db.create_issue("Bug", type="bug")       # triage
        bug2 = db.create_issue("Bug2", type="bug")
        db.update_issue(bug2.id, status="confirmed")    # confirmed

        issues = db.list_issues(status="triage")
        ids = {i.id for i in issues}
        assert bug.id in ids
        assert bug2.id not in ids  # confirmed, not triage

    def test_list_by_done_category(self, db: KeelDB) -> None:
        """list_issues(status='done') returns issues in any done-category state."""
        bug = db.create_issue("Bug", type="bug")
        db.close_issue(bug.id, status="wont_fix")
        task = db.create_issue("Task", type="task")
        db.close_issue(task.id)

        issues = db.list_issues(status="done")
        ids = {i.id for i in issues}
        assert bug.id in ids
        assert task.id in ids

    def test_list_no_packs_literal_match(self, db_no_packs: KeelDB) -> None:
        """With no packs, list_issues(status='open') does literal match (W7 fallback)."""
        task = db_no_packs.create_issue("Task", type="task")
        issues = db_no_packs.list_issues(status="open")
        assert len(issues) == 1
        assert issues[0].id == task.id


class TestGetReadyCategory:
    """get_ready() uses open-category states from ALL enabled types."""

    def test_ready_includes_bug_in_triage(self, db: KeelDB) -> None:
        """Bug in 'triage' (open-category) with no blockers is ready."""
        bug = db.create_issue("Bug", type="bug")
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id in ids

    def test_ready_excludes_wip(self, db: KeelDB) -> None:
        """Bug in 'fixing' (wip-category) is not ready."""
        bug = db.create_issue("Bug", type="bug")
        db.update_issue(bug.id, status="confirmed")
        db.update_issue(bug.id, status="fixing")
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id not in ids

    def test_ready_excludes_blocked(self, db: KeelDB) -> None:
        """Bug in 'triage' blocked by open task is not ready."""
        blocker = db.create_issue("Blocker", type="task")
        bug = db.create_issue("Bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id not in ids

    def test_ready_with_done_blocker_is_unblocked(self, db: KeelDB) -> None:
        """Bug in 'triage' with closed blocker should be ready."""
        blocker = db.create_issue("Blocker", type="task")
        bug = db.create_issue("Bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        db.close_issue(blocker.id)
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id in ids

    def test_ready_with_wont_fix_blocker_is_unblocked(self, db: KeelDB) -> None:
        """Bug blocked by a 'wont_fix' (done-category) bug should be ready."""
        blocker = db.create_issue("Blocker bug", type="bug")
        bug = db.create_issue("Main bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        db.close_issue(blocker.id, status="wont_fix")
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert bug.id in ids

    def test_ready_no_packs_fallback(self, db_no_packs: KeelDB) -> None:
        """With no packs (W7), get_ready() falls back to legacy status='open' check."""
        task = db_no_packs.create_issue("Task", type="task")
        ready = db_no_packs.get_ready()
        ids = {i.id for i in ready}
        assert task.id in ids


class TestGetBlockedCategory:
    """get_blocked() uses open-category + done-category for blocker checks."""

    def test_blocked_in_triage(self, db: KeelDB) -> None:
        """Bug in 'triage' (open-category) with open blocker is blocked."""
        blocker = db.create_issue("Blocker", type="task")
        bug = db.create_issue("Bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        blocked = db.get_blocked()
        ids = {i.id for i in blocked}
        assert bug.id in ids

    def test_not_blocked_if_blocker_done(self, db: KeelDB) -> None:
        """Bug in 'triage' with done-category blocker is NOT blocked."""
        blocker = db.create_issue("Blocker bug", type="bug")
        bug = db.create_issue("Bug", type="bug")
        db.add_dependency(bug.id, blocker.id)
        db.close_issue(blocker.id, status="wont_fix")
        blocked = db.get_blocked()
        ids = {i.id for i in blocked}
        assert bug.id not in ids

    def test_blocked_no_packs_fallback(self, db_no_packs: KeelDB) -> None:
        """With no packs (W7), get_blocked() falls back to legacy behavior."""
        blocker = db_no_packs.create_issue("Blocker", type="task")
        task = db_no_packs.create_issue("Task", type="task")
        db_no_packs.add_dependency(task.id, blocker.id)
        blocked = db_no_packs.get_blocked()
        ids = {i.id for i in blocked}
        assert task.id in ids


class TestGetCriticalPathCategory:
    """get_critical_path() uses done-category for filtering out completed issues."""

    def test_critical_path_excludes_done_category(self, db: KeelDB) -> None:
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

    def test_critical_path_includes_wip(self, db: KeelDB) -> None:
        """Issues in wip-category states should be in critical path."""
        a = db.create_issue("A", type="bug")
        b = db.create_issue("B", type="bug")
        db.add_dependency(b.id, a.id)
        db.update_issue(a.id, status="confirmed")
        db.update_issue(a.id, status="fixing")
        path = db.get_critical_path()
        path_ids = {p["id"] for p in path}
        assert a.id in path_ids  # wip-category included

    def test_critical_path_no_packs_fallback(self, db_no_packs: KeelDB) -> None:
        """With no packs (W7), get_critical_path() uses status != 'closed'."""
        a = db_no_packs.create_issue("A", type="task")
        b = db_no_packs.create_issue("B", type="task")
        db_no_packs.add_dependency(b.id, a.id)
        path = db_no_packs.get_critical_path()
        assert len(path) == 2
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_workflow_behavior.py::TestGetStatesForCategory -v
uv run pytest tests/test_workflow_behavior.py::TestListIssuesCategory -v
uv run pytest tests/test_workflow_behavior.py::TestGetReadyCategory -v
uv run pytest tests/test_workflow_behavior.py::TestGetBlockedCategory -v
uv run pytest tests/test_workflow_behavior.py::TestGetCriticalPathCategory -v
```

Expected: Tests fail because `_get_states_for_category()` does not exist and queries still use literal status strings.

### Step 3: Implement changes to core.py

**3a. Add `_get_states_for_category()` helper**

Add this method to `KeelDB`, after `get_workflow_states()`:

```python
def _get_states_for_category(self, category: str) -> list[str]:
    """Collect all state names that map to a category across enabled types.

    Returns deduplicated list of state names. If no templates are loaded,
    returns an empty list (W7 guard -- caller must handle the empty case).

    SQL safety: State names are pre-validated by StateDefinition.__post_init__
    against ^[a-z][a-z0-9_]{0,63}$. The parameterized placeholders provide
    defense-in-depth (review B1).
    """
    if not (hasattr(self, "templates") and self.templates):
        return []

    states: list[str] = []
    for tpl in self.templates.list_types():
        for s in tpl.states:
            if s.category == category and s.name not in states:
                states.append(s.name)
    return states
```

**3b. Modify `list_issues()` (currently at line 885)**

When `status` is a category name ("open", "wip", "done"), expand to all states in that category. Otherwise use literal match.

```python
def list_issues(
    self,
    *,
    status: str | None = None,
    type: str | None = None,
    priority: int | None = None,
    parent_id: str | None = None,
    assignee: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Issue]:
    conditions: list[str] = []
    params: list[Any] = []

    if status is not None:
        # Check if status is a category name
        category_states: list[str] = []
        if status in ("open", "wip", "done"):
            category_states = self._get_states_for_category(status)

        if category_states:
            placeholders = ",".join("?" * len(category_states))
            conditions.append(f"status IN ({placeholders})")
            params.extend(category_states)
        else:
            # Literal state match (either not a category, or W7 empty guard)
            conditions.append("status = ?")
            params.append(status)

    if type is not None:
        conditions.append("type = ?")
        params.append(type)
    if priority is not None:
        conditions.append("priority = ?")
        params.append(priority)
    if parent_id is not None:
        conditions.append("parent_id = ?")
        params.append(parent_id)
    if assignee is not None:
        conditions.append("assignee = ?")
        params.append(assignee)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])
    rows = self.conn.execute(
        f"SELECT id FROM issues{where} ORDER BY priority, created_at LIMIT ? OFFSET ?",
        params,
    ).fetchall()

    return self._build_issues_batch([r["id"] for r in rows])
```

**3c. Modify `get_ready()` (currently at line 1010)**

Replace `i.status = 'open'` with `i.status IN (...)` using open-category states. Replace `blocker.status != 'closed'` with `blocker.status NOT IN (...)` using done-category states. Empty state list guard: fall back to legacy query.

```python
def get_ready(self) -> list[Issue]:
    """Issues in open-category states with no open blockers."""
    open_states = self._get_states_for_category("open")
    done_states = self._get_states_for_category("done")

    if not open_states:
        # W7: No templates loaded -- fall back to legacy behavior
        rows = self.conn.execute("""\
            SELECT i.id FROM issues i
            WHERE i.status = 'open'
            AND NOT EXISTS (
                SELECT 1 FROM dependencies d
                JOIN issues blocker ON d.depends_on_id = blocker.id
                WHERE d.issue_id = i.id AND blocker.status != 'closed'
            )
            ORDER BY i.priority, i.created_at
        """).fetchall()
        return self._build_issues_batch([r["id"] for r in rows])

    open_ph = ",".join("?" * len(open_states))
    done_ph = ",".join("?" * len(done_states)) if done_states else "'closed'"

    if done_states:
        rows = self.conn.execute(
            f"SELECT i.id FROM issues i "
            f"WHERE i.status IN ({open_ph}) "
            f"AND NOT EXISTS ("
            f"  SELECT 1 FROM dependencies d "
            f"  JOIN issues blocker ON d.depends_on_id = blocker.id "
            f"  WHERE d.issue_id = i.id AND blocker.status NOT IN ({done_ph})"
            f") ORDER BY i.priority, i.created_at",
            [*open_states, *done_states],
        ).fetchall()
    else:
        rows = self.conn.execute(
            f"SELECT i.id FROM issues i "
            f"WHERE i.status IN ({open_ph}) "
            f"AND NOT EXISTS ("
            f"  SELECT 1 FROM dependencies d "
            f"  JOIN issues blocker ON d.depends_on_id = blocker.id "
            f"  WHERE d.issue_id = i.id AND blocker.status != 'closed'"
            f") ORDER BY i.priority, i.created_at",
            open_states,
        ).fetchall()

    return self._build_issues_batch([r["id"] for r in rows])
```

**3d. Modify `get_blocked()` (currently at line 1024)**

Same category-aware approach:

```python
def get_blocked(self) -> list[Issue]:
    """Issues in open-category states that have at least one non-done blocker."""
    open_states = self._get_states_for_category("open")
    done_states = self._get_states_for_category("done")

    if not open_states:
        # W7: Fall back to legacy behavior
        rows = self.conn.execute("""\
            SELECT DISTINCT i.id FROM issues i
            JOIN dependencies d ON d.issue_id = i.id
            JOIN issues blocker ON d.depends_on_id = blocker.id
            WHERE i.status = 'open' AND blocker.status != 'closed'
            ORDER BY i.priority, i.created_at
        """).fetchall()
        return self._build_issues_batch([r["id"] for r in rows])

    open_ph = ",".join("?" * len(open_states))
    done_ph = ",".join("?" * len(done_states)) if done_states else "'closed'"

    if done_states:
        rows = self.conn.execute(
            f"SELECT DISTINCT i.id FROM issues i "
            f"JOIN dependencies d ON d.issue_id = i.id "
            f"JOIN issues blocker ON d.depends_on_id = blocker.id "
            f"WHERE i.status IN ({open_ph}) AND blocker.status NOT IN ({done_ph}) "
            f"ORDER BY i.priority, i.created_at",
            [*open_states, *done_states],
        ).fetchall()
    else:
        rows = self.conn.execute(
            f"SELECT DISTINCT i.id FROM issues i "
            f"JOIN dependencies d ON d.issue_id = i.id "
            f"JOIN issues blocker ON d.depends_on_id = blocker.id "
            f"WHERE i.status IN ({open_ph}) AND blocker.status != 'closed' "
            f"ORDER BY i.priority, i.created_at",
            open_states,
        ).fetchall()

    return self._build_issues_batch([r["id"] for r in rows])
```

**3e. Modify `get_critical_path()` (currently at line 1037)**

Replace `status != 'closed'` with done-category-aware filtering:

```python
def get_critical_path(self) -> list[dict[str, Any]]:
    """Compute the longest dependency chain among non-done issues.

    Uses topological-order dynamic programming on the open-issue dependency DAG.
    Returns the chain as a list of {id, title, priority, type} dicts, ordered
    from the root blocker to the final blocked issue.
    """
    done_states = self._get_states_for_category("done")

    if done_states:
        done_ph = ",".join("?" * len(done_states))
        open_rows = self.conn.execute(
            f"SELECT id, title, priority, type FROM issues WHERE status NOT IN ({done_ph})",
            done_states,
        ).fetchall()
    else:
        # W7: Fall back to legacy behavior
        open_rows = self.conn.execute(
            "SELECT id, title, priority, type FROM issues WHERE status != 'closed'"
        ).fetchall()

    open_ids = {r["id"] for r in open_rows}
    info = {
        r["id"]: {"id": r["id"], "title": r["title"], "priority": r["priority"], "type": r["type"]}
        for r in open_rows
    }

    # edges: blocker -> list of issues it blocks (forward edges)
    forward: dict[str, list[str]] = {nid: [] for nid in open_ids}
    in_degree: dict[str, int] = dict.fromkeys(open_ids, 0)
    dep_rows = self.conn.execute("SELECT issue_id, depends_on_id FROM dependencies").fetchall()
    for dep in dep_rows:
        from_id, to_id = dep["issue_id"], dep["depends_on_id"]
        if from_id in open_ids and to_id in open_ids:
            forward[to_id].append(from_id)  # to_id blocks from_id
            in_degree[from_id] = in_degree.get(from_id, 0) + 1

    if not open_ids:
        return []

    # Topological sort (Kahn's algorithm) + longest path DP
    queue = [nid for nid in open_ids if in_degree[nid] == 0]
    dist: dict[str, int] = dict.fromkeys(open_ids, 0)
    pred: dict[str, str | None] = dict.fromkeys(open_ids, None)

    while queue:
        node = queue.pop(0)
        for neighbor in forward[node]:
            if dist[node] + 1 > dist[neighbor]:
                dist[neighbor] = dist[node] + 1
                pred[neighbor] = node
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if not dist:
        return []

    end_node = max(dist, key=lambda n: dist[n])
    if dist[end_node] == 0:
        return []

    path: list[str] = []
    current: str | None = end_node
    while current is not None:
        path.append(current)
        current = pred[current]
    path.reverse()

    return [info[nid] for nid in path]
```

**3f. Modify `_build_issues_batch()` `is_ready` computation (currently at line 718)**

Update the open blocker count query to use done-category states:

```python
# Inside _build_issues_batch(), replace the open blocker count query:

# 6. Batch compute open blocker counts (category-aware)
open_blockers_by_id: dict[str, int] = dict.fromkeys(issue_ids, 0)
done_states = self._get_states_for_category("done")
if done_states:
    done_ph = ",".join("?" * len(done_states))
    for r in self.conn.execute(
        f"SELECT d.issue_id, COUNT(*) as cnt FROM dependencies d "
        f"JOIN issues i ON d.depends_on_id = i.id "
        f"WHERE d.issue_id IN ({placeholders}) AND i.status NOT IN ({done_ph}) "
        f"GROUP BY d.issue_id",
        [*issue_ids, *done_states],
    ).fetchall():
        open_blockers_by_id[r["issue_id"]] = r["cnt"]
else:
    # W7 fallback: legacy behavior
    for r in self.conn.execute(
        f"SELECT d.issue_id, COUNT(*) as cnt FROM dependencies d "
        f"JOIN issues i ON d.depends_on_id = i.id "
        f"WHERE d.issue_id IN ({placeholders}) AND i.status != 'closed' "
        f"GROUP BY d.issue_id",
        issue_ids,
    ).fetchall():
        open_blockers_by_id[r["issue_id"]] = r["cnt"]

# Update is_ready computation to use open-category check:
open_states = self._get_states_for_category("open")
# ... in the Issue construction:
# is_ready = (status in open-category AND no open blockers)
```

### Step 4: Run tests to verify they pass

```bash
uv run pytest tests/test_workflow_behavior.py::TestGetStatesForCategory -v
uv run pytest tests/test_workflow_behavior.py::TestListIssuesCategory -v
uv run pytest tests/test_workflow_behavior.py::TestGetReadyCategory -v
uv run pytest tests/test_workflow_behavior.py::TestGetBlockedCategory -v
uv run pytest tests/test_workflow_behavior.py::TestGetCriticalPathCategory -v
```

Expected: All tests pass.

### Step 5: Run full CI

```bash
make ci
```

Expected: All existing tests still pass. Ruff and mypy clean.

### Step 6: Commit

```bash
git add src/keel/core.py tests/test_workflow_behavior.py
git commit -m "feat(core): category-aware queries for list, ready, blocked, critical path

- _get_states_for_category() collects states across all enabled types
- list_issues(status=) accepts categories ('open', 'wip', 'done') and specific states
- get_ready() uses open-category states instead of literal 'open'
- get_blocked() uses open-category and done-category states
- get_critical_path() uses done-category for filtering
- _build_issues_batch() is_ready uses category-aware blocker check
- Two-pass approach: gather state lists, use parameterized ? placeholders (B1)
- Empty state list guard: returns empty result instead of malformed SQL (W7)
- All SQL uses parameterized placeholders -- no string interpolation of state names

Implements: WFT-FR-009, WFT-FR-048, WFT-SR-012, WFT-SR-015

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Definition of Done
- [ ] `_get_states_for_category("open")` returns open-category states from all types
- [ ] `_get_states_for_category()` returns deduplicated list
- [ ] `_get_states_for_category()` returns empty list when no packs loaded
- [ ] `list_issues(status="open")` returns issues in any open-category state
- [ ] `list_issues(status="wip")` returns issues in any wip-category state
- [ ] `list_issues(status="triage")` returns only issues in literal "triage" state
- [ ] `get_ready()` returns issues in open-category states with no non-done blockers
- [ ] `get_ready()` recognizes "wont_fix" as done-category (unblocks dependents)
- [ ] `get_blocked()` uses category-aware open and done checks
- [ ] `get_critical_path()` excludes done-category issues
- [ ] All SQL uses parameterized `?` placeholders (no string interpolation of state names)
- [ ] Empty state list returns empty result (no `WHERE IN ()` executed) -- W7
- [ ] No-packs mode falls back to legacy behavior for all queries
- [ ] `_build_issues_batch()` `is_ready` uses category-aware check
- [ ] All existing tests still pass (backward compat)
- [ ] `make ci` passes clean

---

## Task 1.11: Issue.to_dict() Includes status_category

Add `status_category` computed field to `Issue.to_dict()` output. `_build_issues_batch()` computes the category via `TemplateRegistry.get_category()` with a fallback heuristic for unknown types.

**Files:**
- Modify: `src/keel/core.py` (`Issue` dataclass, `Issue.to_dict()`, `_build_issues_batch()`)
- Modify: `tests/test_workflow_behavior.py`

**Requirements covered:** WFT-FR-038

### Step 1: Write the failing tests

Add to `tests/test_workflow_behavior.py`:

```python
# ---------------------------------------------------------------------------
# Task 1.11: Issue.to_dict() Includes status_category
# ---------------------------------------------------------------------------


class TestStatusCategory:
    """Issue.to_dict() includes status_category computed field."""

    def test_bug_triage_category_is_open(self, db: KeelDB) -> None:
        """Bug in 'triage' should have status_category='open'."""
        issue = db.create_issue("Bug", type="bug")
        d = issue.to_dict()
        assert d["status_category"] == "open"

    def test_bug_fixing_category_is_wip(self, db: KeelDB) -> None:
        """Bug in 'fixing' should have status_category='wip'."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        updated = db.update_issue(issue.id, status="fixing")
        d = updated.to_dict()
        assert d["status_category"] == "wip"

    def test_bug_closed_category_is_done(self, db: KeelDB) -> None:
        """Bug in 'closed' should have status_category='done'."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id)
        d = closed.to_dict()
        assert d["status_category"] == "done"

    def test_bug_wont_fix_category_is_done(self, db: KeelDB) -> None:
        """Bug in 'wont_fix' should have status_category='done'."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id, status="wont_fix")
        d = closed.to_dict()
        assert d["status_category"] == "done"

    def test_task_open_category(self, db: KeelDB) -> None:
        """Task in 'open' should have status_category='open'."""
        issue = db.create_issue("Task", type="task")
        d = issue.to_dict()
        assert d["status_category"] == "open"

    def test_task_in_progress_category(self, db: KeelDB) -> None:
        """Task in 'in_progress' should have status_category='wip'."""
        issue = db.create_issue("Task", type="task")
        updated = db.update_issue(issue.id, status="in_progress")
        d = updated.to_dict()
        assert d["status_category"] == "wip"

    def test_task_closed_category(self, db: KeelDB) -> None:
        """Task in 'closed' should have status_category='done'."""
        issue = db.create_issue("Task", type="task")
        closed = db.close_issue(issue.id)
        d = closed.to_dict()
        assert d["status_category"] == "done"

    def test_unknown_type_fallback_heuristic(self, db: KeelDB) -> None:
        """Unknown types should use fallback heuristic for category."""
        issue = db.create_issue("Custom", type="custom_type")
        d = issue.to_dict()
        # 'open' status maps to 'open' category via heuristic
        assert d["status_category"] == "open"

    def test_unknown_type_in_progress_heuristic(self, db: KeelDB) -> None:
        """Unknown type in 'in_progress' should map to 'wip' via heuristic."""
        issue = db.create_issue("Custom", type="custom_type")
        updated = db.update_issue(issue.id, status="in_progress")
        d = updated.to_dict()
        assert d["status_category"] == "wip"

    def test_unknown_type_closed_heuristic(self, db: KeelDB) -> None:
        """Unknown type in 'closed' should map to 'done' via heuristic."""
        issue = db.create_issue("Custom", type="custom_type")
        closed = db.update_issue(issue.id, status="closed")
        d = closed.to_dict()
        assert d["status_category"] == "done"

    def test_status_category_in_list_issues(self, db: KeelDB) -> None:
        """list_issues() results should include status_category."""
        db.create_issue("Bug", type="bug")
        issues = db.list_issues()
        assert all("status_category" in i.to_dict() for i in issues)

    def test_no_packs_fallback_heuristic(self, db_no_packs: KeelDB) -> None:
        """With no packs, category falls back to heuristic."""
        issue = db_no_packs.create_issue("Task", type="task")
        d = issue.to_dict()
        assert d["status_category"] == "open"
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_workflow_behavior.py::TestStatusCategory -v
```

Expected: `KeyError: 'status_category'` -- the field does not exist in `to_dict()` yet.

### Step 3: Implement changes to core.py

**3a. Add `status_category` field to `Issue` dataclass (currently at line 386)**

```python
@dataclass
class Issue:
    id: str
    title: str
    status: str = "open"
    priority: int = 2
    type: str = "task"
    parent_id: str | None = None
    assignee: str = ""
    created_at: str = ""
    updated_at: str = ""
    closed_at: str | None = None
    description: str = ""
    notes: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    # Computed (not stored directly)
    labels: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    is_ready: bool = False
    children: list[str] = field(default_factory=list)
    status_category: str = "open"  # NEW: computed from template or heuristic
```

**3b. Update `Issue.to_dict()` (currently at line 407)**

Add `status_category` to the output dict:

```python
def to_dict(self) -> dict[str, Any]:
    return {
        "id": self.id,
        "title": self.title,
        "status": self.status,
        "status_category": self.status_category,
        "priority": self.priority,
        "type": self.type,
        "parent_id": self.parent_id,
        "assignee": self.assignee,
        "created_at": self.created_at,
        "updated_at": self.updated_at,
        "closed_at": self.closed_at,
        "description": self.description,
        "notes": self.notes,
        "fields": self.fields,
        "labels": self.labels,
        "blocks": self.blocks,
        "blocked_by": self.blocked_by,
        "is_ready": self.is_ready,
        "children": self.children,
    }
```

**3c. Add `_infer_status_category()` static method to `KeelDB`**

Fallback heuristic for unknown types:

```python
@staticmethod
def _infer_status_category(status: str) -> str:
    """Infer status category from status name when no template is available.

    Heuristic mapping:
    - 'open' -> 'open'
    - 'in_progress', 'fixing', 'verifying', etc. -> 'wip'
    - 'closed', 'done', 'resolved', 'wont_fix' -> 'done'
    - Anything else -> 'open' (safe default)
    """
    done_names = {"closed", "done", "resolved", "wont_fix", "cancelled", "archived"}
    wip_names = {"in_progress", "fixing", "verifying", "reviewing", "testing", "active"}
    if status in done_names:
        return "done"
    if status in wip_names:
        return "wip"
    return "open"
```

**3d. Update `_build_issues_batch()` (currently at line 641)**

Compute `status_category` for each issue using the template registry or fallback heuristic:

```python
# Inside _build_issues_batch(), after building all lookup dicts and before
# constructing Issue objects:

# 7. Compute status_category for each issue
def _get_category(issue_type: str, status: str) -> str:
    if hasattr(self, "templates") and self.templates:
        cat = self.templates.get_category(issue_type, status)
        if cat is not None:
            return cat
    return self._infer_status_category(status)

# In the Issue construction loop, add:
result.append(
    Issue(
        # ... existing fields ...
        status_category=_get_category(row["type"], row["status"]),
    )
)
```

### Step 4: Run tests to verify they pass

```bash
uv run pytest tests/test_workflow_behavior.py::TestStatusCategory -v
```

Expected: All 12 tests pass.

### Step 5: Run full CI

```bash
make ci
```

Expected: All existing tests still pass. Ruff and mypy clean.

### Step 6: Commit

```bash
git add src/keel/core.py tests/test_workflow_behavior.py
git commit -m "feat(core): Issue.to_dict() includes status_category field

- Issue dataclass gains status_category computed field
- _build_issues_batch() resolves category via TemplateRegistry.get_category()
- Fallback heuristic: open/in_progress/closed map to open/wip/done
- _infer_status_category() handles unknown types/states safely
- All query results (list, get, search) include status_category

Implements: WFT-FR-038

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Definition of Done
- [ ] `Issue` dataclass has `status_category` field (default "open")
- [ ] `Issue.to_dict()` includes `status_category` in output
- [ ] `_build_issues_batch()` computes category via TemplateRegistry
- [ ] Bug "triage" -> "open", "fixing" -> "wip", "closed" -> "done", "wont_fix" -> "done"
- [ ] Task "open" -> "open", "in_progress" -> "wip", "closed" -> "done"
- [ ] Unknown types use fallback heuristic
- [ ] No-packs mode uses fallback heuristic
- [ ] `list_issues()` results include `status_category`
- [ ] All existing tests still pass
- [ ] `make ci` passes clean

---

## Task 1.12: New KeelDB Methods -- get_valid_transitions() and validate_issue()

Implement `KeelDB.get_valid_transitions(issue_id)` and `KeelDB.validate_issue(issue_id)` that delegate to TemplateRegistry with current issue state and fields. These are the agent-facing discovery methods that let agents ask "what can I do next?" and "is this issue valid?"

**Files:**
- Modify: `src/keel/core.py`
- Modify: `tests/test_workflow_behavior.py`

**Requirements covered:** WFT-FR-050

### Step 1: Write the failing tests

Add to `tests/test_workflow_behavior.py`:

```python
from keel.templates import TransitionOption, ValidationResult


# ---------------------------------------------------------------------------
# Task 1.12: New KeelDB Methods
# ---------------------------------------------------------------------------


class TestGetValidTransitions:
    """KeelDB.get_valid_transitions() delegates to TemplateRegistry."""

    def test_bug_triage_transitions(self, db: KeelDB) -> None:
        """Bug in 'triage' should have transitions to confirmed and wont_fix."""
        issue = db.create_issue("Bug", type="bug")
        options = db.get_valid_transitions(issue.id)
        targets = {o.to for o in options}
        assert "confirmed" in targets
        assert "wont_fix" in targets

    def test_bug_triage_transition_categories(self, db: KeelDB) -> None:
        """Transition options should include target state categories."""
        issue = db.create_issue("Bug", type="bug")
        options = db.get_valid_transitions(issue.id)
        confirmed_opt = next(o for o in options if o.to == "confirmed")
        assert confirmed_opt.category == "open"
        wont_fix_opt = next(o for o in options if o.to == "wont_fix")
        assert wont_fix_opt.category == "done"

    def test_bug_fixing_shows_readiness(self, db: KeelDB) -> None:
        """Transitions from 'fixing' should show field readiness."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing")
        options = db.get_valid_transitions(issue.id)
        verifying_opt = next(o for o in options if o.to == "verifying")
        assert verifying_opt.ready is False
        assert "fix_verification" in verifying_opt.missing_fields

    def test_bug_fixing_ready_with_fields(self, db: KeelDB) -> None:
        """Transition should be ready when required fields are populated."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        db.update_issue(issue.id, status="fixing",
                        fields={"fix_verification": "tests pass"})
        options = db.get_valid_transitions(issue.id)
        verifying_opt = next(o for o in options if o.to == "verifying")
        assert verifying_opt.ready is True
        assert verifying_opt.missing_fields == ()

    def test_task_open_transitions(self, db: KeelDB) -> None:
        """Task in 'open' should have transition to in_progress."""
        issue = db.create_issue("Task", type="task")
        options = db.get_valid_transitions(issue.id)
        targets = {o.to for o in options}
        assert "in_progress" in targets

    def test_unknown_type_returns_empty(self, db: KeelDB) -> None:
        """Unknown types should return empty list (no template)."""
        issue = db.create_issue("Custom", type="custom_type")
        options = db.get_valid_transitions(issue.id)
        assert options == []

    def test_closed_issue_transitions(self, db: KeelDB) -> None:
        """Closed issues should have no outgoing transitions (terminal state)."""
        issue = db.create_issue("Task", type="task")
        db.close_issue(issue.id)
        options = db.get_valid_transitions(issue.id)
        # 'closed' is typically a terminal state with no outgoing transitions
        assert len(options) == 0

    def test_nonexistent_issue_raises(self, db: KeelDB) -> None:
        """get_valid_transitions on nonexistent issue raises KeyError."""
        with pytest.raises(KeyError):
            db.get_valid_transitions("test-nonexistent")

    def test_return_type_is_transition_option(self, db: KeelDB) -> None:
        """Results should be TransitionOption dataclass instances."""
        issue = db.create_issue("Bug", type="bug")
        options = db.get_valid_transitions(issue.id)
        assert len(options) > 0
        assert isinstance(options[0], TransitionOption)


class TestValidateIssue:
    """KeelDB.validate_issue() checks issue against its template."""

    def test_valid_bug_in_triage(self, db: KeelDB) -> None:
        """Bug in 'triage' with no required fields should validate clean."""
        issue = db.create_issue("Bug", type="bug")
        result = db.validate_issue(issue.id)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_bug_in_confirmed_missing_severity(self, db: KeelDB) -> None:
        """Bug in 'confirmed' without severity should have a warning."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed")
        result = db.validate_issue(issue.id)
        # severity is required_at 'confirmed' -- should warn
        assert len(result.warnings) > 0
        assert any("severity" in w for w in result.warnings)

    def test_bug_in_confirmed_with_severity_valid(self, db: KeelDB) -> None:
        """Bug in 'confirmed' with severity should validate clean."""
        issue = db.create_issue("Bug", type="bug")
        db.update_issue(issue.id, status="confirmed",
                        fields={"severity": "major"})
        result = db.validate_issue(issue.id)
        assert result.valid is True

    def test_task_always_valid(self, db: KeelDB) -> None:
        """Task with no required_at fields should always validate clean."""
        issue = db.create_issue("Task", type="task")
        result = db.validate_issue(issue.id)
        assert result.valid is True
        assert len(result.warnings) == 0

    def test_unknown_type_valid(self, db: KeelDB) -> None:
        """Unknown types should validate as valid (no template to check against)."""
        issue = db.create_issue("Custom", type="custom_type")
        result = db.validate_issue(issue.id)
        assert result.valid is True

    def test_return_type_is_validation_result(self, db: KeelDB) -> None:
        """Result should be a ValidationResult dataclass."""
        issue = db.create_issue("Bug", type="bug")
        result = db.validate_issue(issue.id)
        assert isinstance(result, ValidationResult)

    def test_nonexistent_issue_raises(self, db: KeelDB) -> None:
        """validate_issue on nonexistent issue raises KeyError."""
        with pytest.raises(KeyError):
            db.validate_issue("test-nonexistent")
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_workflow_behavior.py::TestGetValidTransitions -v
uv run pytest tests/test_workflow_behavior.py::TestValidateIssue -v
```

Expected: `AttributeError: 'KeelDB' object has no attribute 'get_valid_transitions'`

### Step 3: Implement changes to core.py

Add these two methods to `KeelDB`, after the existing query methods:

```python
from keel.templates import TransitionOption, ValidationResult

# Add to KeelDB class:

def get_valid_transitions(self, issue_id: str) -> list[TransitionOption]:
    """Return valid next states for an issue with readiness info.

    Delegates to TemplateRegistry.get_valid_transitions() with the issue's
    current state and fields. Returns an empty list for unknown types.

    Args:
        issue_id: The issue to check.

    Returns:
        List of TransitionOption with readiness and field requirement info.

    Raises:
        KeyError: If the issue does not exist.
    """
    issue = self.get_issue(issue_id)
    if not (hasattr(self, "templates") and self.templates):
        return []
    return self.templates.get_valid_transitions(
        issue.type, issue.status, issue.fields
    )

def validate_issue(self, issue_id: str) -> ValidationResult:
    """Validate an issue against its template.

    Checks whether all fields required at the current state are populated.
    Returns a ValidationResult with warnings for missing recommended fields.

    Args:
        issue_id: The issue to validate.

    Returns:
        ValidationResult with valid flag, warnings, and errors.

    Raises:
        KeyError: If the issue does not exist.
    """
    issue = self.get_issue(issue_id)
    if not (hasattr(self, "templates") and self.templates):
        return ValidationResult(valid=True, warnings=(), errors=())

    tpl = self.templates.get_type(issue.type)
    if tpl is None:
        return ValidationResult(valid=True, warnings=(), errors=())

    # Check required_at fields for current state
    missing = self.templates.validate_fields_for_state(
        issue.type, issue.status, issue.fields
    )

    warnings: list[str] = []
    errors: list[str] = []

    if missing:
        for field_name in missing:
            warnings.append(
                f"Field '{field_name}' is recommended at state '{issue.status}' "
                f"for type '{issue.type}' but is not populated."
            )

    # An issue is "valid" if there are no hard errors (missing required fields
    # for the current state are warnings, not errors, because the issue may
    # have entered the state before the template was enabled)
    return ValidationResult(
        valid=len(errors) == 0,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )
```

### Step 4: Run tests to verify they pass

```bash
uv run pytest tests/test_workflow_behavior.py::TestGetValidTransitions -v
uv run pytest tests/test_workflow_behavior.py::TestValidateIssue -v
```

Expected: All tests pass.

### Step 5: Run full CI

```bash
make ci
```

Expected: All existing tests still pass. Ruff and mypy clean.

### Step 6: Commit

```bash
git add src/keel/core.py tests/test_workflow_behavior.py
git commit -m "feat(core): add get_valid_transitions() and validate_issue() methods

- get_valid_transitions(issue_id) returns TransitionOptions with readiness info
- validate_issue(issue_id) checks issue fields against template requirements
- Both delegate to TemplateRegistry with current state and fields
- Unknown types: get_valid_transitions returns [], validate_issue returns valid
- Agent-friendly: enables 'what can I do next?' and 'is this issue valid?' queries

Implements: WFT-FR-050

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Definition of Done
- [ ] `get_valid_transitions(issue_id)` returns list of `TransitionOption`
- [ ] Transition options include target state, category, enforcement, and readiness
- [ ] Missing fields shown in `missing_fields` tuple
- [ ] `ready` flag reflects whether hard-enforcement fields are populated
- [ ] Unknown types return empty list
- [ ] Nonexistent issue raises `KeyError`
- [ ] `validate_issue(issue_id)` returns `ValidationResult`
- [ ] Missing `required_at` fields shown as warnings
- [ ] Unknown types validate as valid
- [ ] All existing tests still pass
- [ ] `make ci` passes clean

---

## Task 1.15: Regression Test Suite

Run the full existing test suite and fix any failures caused by Phase 1C changes. Then add specific backward-compatibility regression tests that lock in the guarantee that old-style data works identically.

**Files:**
- Modify: various test files as needed (fix any failures)
- Create: `tests/test_backward_compat.py`

**Requirements covered:** WFT-AR-011, WFT-SR-015

### Step 1: Run existing test suite

```bash
uv run pytest tests/ -v --tb=short 2>&1 | head -200
```

Identify and fix any failures. Common failure patterns to watch for:
- Tests that assert `issue.status == "open"` after creating a bug (now "triage")
- Tests that assert `claim_issue` produces `status == "in_progress"` for non-task types
- Tests that assert exact `to_dict()` output (now includes `status_category`)
- Tests that depend on `close_issue()` always producing `status == "closed"`

### Step 2: Write backward-compatibility regression tests

Create `tests/test_backward_compat.py`:

```python
# tests/test_backward_compat.py
"""Backward compatibility regression tests for workflow templates.

These tests lock in the guarantee that existing behavior is preserved when
workflow templates are enabled. If these tests break, it means the template
system is NOT backward compatible and must be fixed before merging.

Validates: WFT-AR-011, WFT-SR-015
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keel.core import KeelDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> KeelDB:
    """Standard KeelDB with core + planning packs enabled."""
    keel_dir = tmp_path / ".keel"
    keel_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}
    (keel_dir / "config.json").write_text(json.dumps(config))
    db = KeelDB(keel_dir / "keel.db", prefix="test")
    db.initialize()
    return db


@pytest.fixture()
def legacy_db(tmp_path: Path) -> KeelDB:
    """KeelDB with no packs (simulates pre-template project)."""
    keel_dir = tmp_path / ".keel"
    keel_dir.mkdir()
    config = {"prefix": "test", "version": 1, "enabled_packs": []}
    (keel_dir / "config.json").write_text(json.dumps(config))
    db = KeelDB(keel_dir / "keel.db", prefix="test")
    db.initialize()
    return db


# ---------------------------------------------------------------------------
# Task type regression (the most critical backward compat guarantee)
# ---------------------------------------------------------------------------


class TestTaskTypeBackwardCompat:
    """Task type must behave identically to pre-template behavior."""

    def test_task_creates_with_open(self, db: KeelDB) -> None:
        issue = db.create_issue("Do something", type="task")
        assert issue.status == "open"

    def test_task_transitions_open_to_in_progress(self, db: KeelDB) -> None:
        issue = db.create_issue("Do something", type="task")
        updated = db.update_issue(issue.id, status="in_progress")
        assert updated.status == "in_progress"

    def test_task_transitions_in_progress_to_closed(self, db: KeelDB) -> None:
        issue = db.create_issue("Do something", type="task")
        db.update_issue(issue.id, status="in_progress")
        updated = db.update_issue(issue.id, status="closed")
        assert updated.status == "closed"
        assert updated.closed_at is not None

    def test_task_claim_produces_in_progress(self, db: KeelDB) -> None:
        issue = db.create_issue("Do something", type="task")
        claimed = db.claim_issue(issue.id, assignee="agent")
        assert claimed.status == "in_progress"
        assert claimed.assignee == "agent"

    def test_task_release_produces_open(self, db: KeelDB) -> None:
        issue = db.create_issue("Do something", type="task")
        db.claim_issue(issue.id, assignee="agent")
        released = db.release_claim(issue.id)
        assert released.status == "open"
        assert released.assignee == ""

    def test_task_close_produces_closed(self, db: KeelDB) -> None:
        issue = db.create_issue("Do something", type="task")
        closed = db.close_issue(issue.id)
        assert closed.status == "closed"
        assert closed.closed_at is not None

    def test_task_list_by_open_includes_task(self, db: KeelDB) -> None:
        issue = db.create_issue("Do something", type="task")
        issues = db.list_issues(status="open")
        ids = {i.id for i in issues}
        assert issue.id in ids

    def test_task_get_ready_includes_task(self, db: KeelDB) -> None:
        issue = db.create_issue("Do something", type="task")
        ready = db.get_ready()
        ids = {i.id for i in ready}
        assert issue.id in ids


class TestEpicTypeBackwardCompat:
    """Epic type must behave identically to pre-template behavior."""

    def test_epic_creates_with_open(self, db: KeelDB) -> None:
        issue = db.create_issue("Big feature", type="epic")
        assert issue.status == "open"

    def test_epic_transitions_to_in_progress(self, db: KeelDB) -> None:
        issue = db.create_issue("Big feature", type="epic")
        updated = db.update_issue(issue.id, status="in_progress")
        assert updated.status == "in_progress"

    def test_epic_close(self, db: KeelDB) -> None:
        issue = db.create_issue("Big feature", type="epic")
        closed = db.close_issue(issue.id)
        assert closed.status == "closed"


# ---------------------------------------------------------------------------
# Legacy project (no packs) regression
# ---------------------------------------------------------------------------


class TestLegacyProjectRegression:
    """Projects with enabled_packs: [] must work exactly like v1.0."""

    def test_create_open(self, legacy_db: KeelDB) -> None:
        issue = legacy_db.create_issue("Task", type="task")
        assert issue.status == "open"

    def test_bug_creates_open_without_packs(self, legacy_db: KeelDB) -> None:
        """Without packs, even bug type starts as 'open'."""
        issue = legacy_db.create_issue("Bug", type="bug")
        assert issue.status == "open"

    def test_claim_in_progress(self, legacy_db: KeelDB) -> None:
        issue = legacy_db.create_issue("Task", type="task")
        claimed = legacy_db.claim_issue(issue.id, assignee="agent")
        assert claimed.status == "in_progress"

    def test_release_open(self, legacy_db: KeelDB) -> None:
        issue = legacy_db.create_issue("Task", type="task")
        legacy_db.claim_issue(issue.id, assignee="agent")
        released = legacy_db.release_claim(issue.id)
        assert released.status == "open"

    def test_close_closed(self, legacy_db: KeelDB) -> None:
        issue = legacy_db.create_issue("Task", type="task")
        closed = legacy_db.close_issue(issue.id)
        assert closed.status == "closed"

    def test_list_issues_literal_match(self, legacy_db: KeelDB) -> None:
        issue = legacy_db.create_issue("Task", type="task")
        issues = legacy_db.list_issues(status="open")
        ids = {i.id for i in issues}
        assert issue.id in ids

    def test_get_ready_legacy(self, legacy_db: KeelDB) -> None:
        issue = legacy_db.create_issue("Task", type="task")
        ready = legacy_db.get_ready()
        ids = {i.id for i in ready}
        assert issue.id in ids

    def test_get_blocked_legacy(self, legacy_db: KeelDB) -> None:
        blocker = legacy_db.create_issue("Blocker", type="task")
        task = legacy_db.create_issue("Blocked", type="task")
        legacy_db.add_dependency(task.id, blocker.id)
        blocked = legacy_db.get_blocked()
        ids = {i.id for i in blocked}
        assert task.id in ids

    def test_critical_path_legacy(self, legacy_db: KeelDB) -> None:
        a = legacy_db.create_issue("A", type="task")
        b = legacy_db.create_issue("B", type="task")
        legacy_db.add_dependency(b.id, a.id)
        path = legacy_db.get_critical_path()
        assert len(path) == 2

    def test_update_issue_in_progress(self, legacy_db: KeelDB) -> None:
        issue = legacy_db.create_issue("Task", type="task")
        updated = legacy_db.update_issue(issue.id, status="in_progress")
        assert updated.status == "in_progress"

    def test_update_issue_closed(self, legacy_db: KeelDB) -> None:
        issue = legacy_db.create_issue("Task", type="task")
        updated = legacy_db.update_issue(issue.id, status="closed")
        assert updated.status == "closed"


# ---------------------------------------------------------------------------
# to_dict() output stability
# ---------------------------------------------------------------------------


class TestToDictStability:
    """Issue.to_dict() must include all previously-existing keys."""

    def test_to_dict_keys_superset(self, db: KeelDB) -> None:
        """to_dict() must contain at least the original v1.0 keys."""
        issue = db.create_issue("Task", type="task")
        d = issue.to_dict()
        required_keys = {
            "id", "title", "status", "priority", "type", "parent_id",
            "assignee", "created_at", "updated_at", "closed_at",
            "description", "notes", "fields", "labels", "blocks",
            "blocked_by", "is_ready", "children",
        }
        assert required_keys.issubset(set(d.keys()))

    def test_to_dict_has_status_category(self, db: KeelDB) -> None:
        """to_dict() now includes status_category (additive, not breaking)."""
        issue = db.create_issue("Task", type="task")
        d = issue.to_dict()
        assert "status_category" in d

    def test_to_dict_types_unchanged(self, db: KeelDB) -> None:
        """Core field types should be unchanged."""
        issue = db.create_issue("Task", type="task")
        d = issue.to_dict()
        assert isinstance(d["id"], str)
        assert isinstance(d["title"], str)
        assert isinstance(d["status"], str)
        assert isinstance(d["priority"], int)
        assert isinstance(d["labels"], list)
        assert isinstance(d["fields"], dict)
        assert isinstance(d["is_ready"], bool)


# ---------------------------------------------------------------------------
# Dependencies and blocking
# ---------------------------------------------------------------------------


class TestDependencyBackwardCompat:
    """Dependencies must work the same with templates enabled."""

    def test_add_dependency(self, db: KeelDB) -> None:
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        db.add_dependency(b.id, a.id)
        b_fresh = db.get_issue(b.id)
        assert a.id in b_fresh.blocked_by

    def test_cycle_detection(self, db: KeelDB) -> None:
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        db.add_dependency(b.id, a.id)
        with pytest.raises(ValueError, match="cycle"):
            db.add_dependency(a.id, b.id)

    def test_closing_blocker_unblocks(self, db: KeelDB) -> None:
        a = db.create_issue("Blocker", type="task")
        b = db.create_issue("Blocked", type="task")
        db.add_dependency(b.id, a.id)
        assert db.get_issue(b.id).is_ready is False
        db.close_issue(a.id)
        assert db.get_issue(b.id).is_ready is True


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------


class TestBatchBackwardCompat:
    """Batch operations must work with templates."""

    def test_batch_close(self, db: KeelDB) -> None:
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        results = db.batch_close([a.id, b.id])
        assert all(r.status == "closed" for r in results)

    def test_batch_update_status(self, db: KeelDB) -> None:
        a = db.create_issue("A", type="task")
        b = db.create_issue("B", type="task")
        results = db.batch_update([a.id, b.id], status="in_progress")
        assert all(r.status == "in_progress" for r in results)
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_backward_compat.py -v
```

Expected: Most tests should pass if Tasks 1.9-1.12 are correct. Any failures indicate regressions that must be fixed.

### Step 3: Fix any regressions

Common fixes:
- If existing tests in `test_core.py` assert `issue.status == "open"` after creating a bug, update the test or ensure the template preserves the "open" initial state for task type.
- If `to_dict()` output has changed shape, existing JSON comparisons may need updating.
- If `get_stats()` uses hardcoded `status == 'open'` strings, update to use category-aware checks.

Run the full suite iteratively:

```bash
uv run pytest tests/ -v --tb=short
```

Fix each failure, re-run. Repeat until all green.

### Step 4: Run full CI

```bash
make ci
```

Expected: All tests pass. Ruff and mypy clean. Coverage >= 90% on new/modified code.

### Step 5: Commit

```bash
git add tests/test_backward_compat.py
git add -u  # any fixes to existing test files
git commit -m "test: backward compatibility regression tests for workflow templates

- Task type: open/in_progress/closed lifecycle unchanged
- Epic type: open/in_progress/closed lifecycle unchanged
- Legacy projects (enabled_packs: []): identical to v1.0 behavior
- to_dict() output: superset of v1.0 keys (status_category added)
- Dependencies: add, cycle detection, blocker unblock all work
- Batch operations: batch_close, batch_update work with templates
- Fixes any regressions found during regression testing

Validates: WFT-AR-011, WFT-SR-015

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Definition of Done
- [ ] ALL existing tests in `tests/` pass without modification (or with minimal backward-compat fixes)
- [ ] Task type lifecycle unchanged: open -> in_progress -> closed
- [ ] Epic type lifecycle unchanged: open -> in_progress -> closed
- [ ] `enabled_packs: []` produces identical behavior to pre-template v1.0
- [ ] `to_dict()` output is a superset of v1.0 (no keys removed, `status_category` added)
- [ ] Dependency operations unchanged (add, remove, cycle detection)
- [ ] Batch operations work with template-enabled types
- [ ] `get_stats()` still returns correct counts
- [ ] `make ci` passes clean
- [ ] Coverage >= 90% on new/modified code

---

## Definition of Done (Phase 1C)

All task-level DoD checklists above must be satisfied, plus:

- [ ] `create_issue()` with typed issues uses type-specific initial state
- [ ] `update_issue()` validates transitions with soft/hard enforcement
- [ ] Atomic transition-with-fields works: fields merged before validation (WFT-FR-069)
- [ ] Atomic transition-with-fields: hard failure saves neither fields nor status
- [ ] `close_issue()` accepts optional `status` parameter for multi-done types
- [ ] `claim_issue()` uses first wip-category state from template
- [ ] `release_claim()` returns to initial state from template
- [ ] Category-aware queries work: `list_issues`, `get_ready`, `get_blocked`, `get_critical_path`
- [ ] `list_issues(status="open")` returns issues in all open-category states
- [ ] Empty state list guard (W7): no malformed SQL when no templates loaded
- [ ] All SQL uses parameterized `?` placeholders (B1)
- [ ] `Issue.to_dict()` includes `status_category`
- [ ] `get_valid_transitions()` returns `TransitionOption` list with readiness
- [ ] `validate_issue()` checks issue against template requirements
- [ ] ALL existing tests pass (backward compat verified) -- WFT-AR-011
- [ ] Backward-compatibility regression tests added and passing -- WFT-SR-015
- [ ] Task/epic/feature types still use open/in_progress/closed states
- [ ] Legacy projects (`enabled_packs: []`) behave identically to v1.0
- [ ] `make ci` passes clean (lint + typecheck + tests)
- [ ] Coverage >= 90% on all new/modified code
- [ ] All commits follow conventional format with requirement IDs
