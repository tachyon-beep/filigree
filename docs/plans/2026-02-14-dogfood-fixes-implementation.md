# Dogfood Bug Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the top dogfood bugs — enforce workflow transitions, allow reparenting, close resolved/positive-feedback issues.

**Architecture:** Three targeted changes to core + templates, plus housekeeping. Transition enforcement changes `validate_transition` to reject undefined transitions for known types. A private `_skip_transition_check` flag lets `close_issue` and CLI `reopen` bypass enforcement (they already validate category correctness). Reparenting adds `parent_id` to `update_issue` with cycle detection.

**Tech Stack:** Python 3.13, SQLite, Click CLI, MCP server (fastmcp)

---

### Task 1: Enforce Workflow Transitions (keel-ab92aa)

**Files:**
- Modify: `src/keel/templates.py:411-421`
- Modify: `src/keel/core.py:1136-1148` (update_issue signature)
- Modify: `src/keel/core.py:1169-1201` (transition validation block)
- Modify: `src/keel/core.py:1270-1302` (close_issue)
- Modify: `src/keel/cli.py:387-414` (reopen command)
- Test: `tests/test_templates.py:460-466`
- Test: `tests/test_workflow_behavior.py` (new tests)

**Step 1: Write failing test — undefined transition rejected**

In `tests/test_templates.py`, update the existing test at line 460:

```python
def test_undefined_transition_rejected_for_known_type(self, registry: TemplateRegistry) -> None:
    """Transitions not in the table are rejected for known types."""
    result = registry.validate_transition("bug", "triage", "closed", {})
    assert result.allowed is False
    assert result.enforcement is None
    assert len(result.warnings) >= 1
    assert "not in the standard workflow" in result.warnings[0]
```

Run: `uv run pytest tests/test_templates.py::TestTransitionValidation::test_undefined_transition_rejected_for_known_type -v`
Expected: FAIL (currently returns `allowed=True`)

**Step 2: Fix `validate_transition` to reject undefined transitions**

In `src/keel/templates.py`, change lines 411-421:

```python
        if transition is None:
            # Transition not in table: REJECTED for known types (WFT-FR-011)
            return TransitionResult(
                allowed=False,
                enforcement=None,
                missing_fields=(),
                warnings=(
                    f"Transition '{from_state}' -> '{to_state}' is not in the standard workflow for '{type_name}'. "
                    f"Use get_valid_transitions() to see recommended transitions.",
                ),
            )
```

Only change: `allowed=True` → `allowed=False`.

Run: `uv run pytest tests/test_templates.py::TestTransitionValidation::test_undefined_transition_rejected_for_known_type -v`
Expected: PASS

**Step 3: Add `_skip_transition_check` to `update_issue`**

In `src/keel/core.py`, add a private parameter to `update_issue` signature:

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
        _skip_transition_check: bool = False,
    ) -> Issue:
```

Then wrap the transition validation block (lines 1169-1201) in `if not _skip_transition_check:`:

```python
        if status is not None and status != current.status:
            self._validate_status(status, current.type)

            if not _skip_transition_check:
                # WFT-FR-069: Atomic transition-with-fields
                merged_fields = {**current.fields}
                if fields is not None:
                    merged_fields.update(fields)

                # Validate transition via template system (unknown types pass through)
                tpl = self.templates.get_type(current.type)
                if tpl is not None:
                    result = self.templates.validate_transition(current.type, current.status, status, merged_fields)
                    if not result.allowed:
                        missing_str = ", ".join(result.missing_fields)
                        if result.missing_fields:
                            msg = (
                                f"Cannot transition '{current.status}' -> '{status}' for type "
                                f"'{current.type}': missing required fields: {missing_str}"
                            )
                        else:
                            msg = (
                                f"Transition '{current.status}' -> '{status}' is not allowed for type "
                                f"'{current.type}'. Use get_valid_transitions() to see allowed transitions."
                            )
                        raise ValueError(msg)

                    # Soft enforcement: record warning events
                    if result.warnings:
                        for warning in result.warnings:
                            self._record_event(
                                issue_id,
                                "transition_warning",
                                actor=actor,
                                old_value=current.status,
                                new_value=status,
                                comment=warning,
                            )
                    if result.missing_fields and result.enforcement == "soft":
                        self._record_event(
                            issue_id,
                            "transition_warning",
                            actor=actor,
                            old_value=current.status,
                            new_value=status,
                            comment=f"Missing recommended fields: {', '.join(result.missing_fields)}",
                        )
```

**Step 4: Make `close_issue` use `_skip_transition_check=True`**

In `src/keel/core.py`, line 1297:

```python
        return self.update_issue(
            issue_id,
            status=done_status,
            fields={"close_reason": reason} if reason else None,
            actor=actor,
            _skip_transition_check=True,
        )
```

**Step 5: Make CLI `reopen` use `_skip_transition_check=True`**

In `src/keel/cli.py`, line 395:

```python
                issue = db.update_issue(issue_id, status=initial_state, actor=ctx.obj["actor"], _skip_transition_check=True)
```

**Step 6: Write integration tests for enforcement**

Add to `tests/test_workflow_behavior.py`:

```python
    def test_undefined_transition_rejected(self, db: KeelDB) -> None:
        """Undefined transitions on known types are rejected."""
        issue = db.create_issue("Bug", type="bug")
        with pytest.raises(ValueError, match="not allowed"):
            db.update_issue(issue.id, status="verifying")  # triage -> verifying not in table

    def test_close_bypasses_transition_check(self, db: KeelDB) -> None:
        """close_issue works from any state (admin action)."""
        issue = db.create_issue("Bug", type="bug")
        closed = db.close_issue(issue.id, reason="duplicate")
        assert closed.status == "closed"

    def test_skip_transition_check_flag(self, db: KeelDB) -> None:
        """_skip_transition_check allows any valid state."""
        issue = db.create_issue("Bug", type="bug")
        updated = db.update_issue(issue.id, status="verifying", _skip_transition_check=True)
        assert updated.status == "verifying"
```

Run: `uv run pytest tests/test_workflow_behavior.py -v -k "undefined_transition_rejected or close_bypasses or skip_transition_check"`
Expected: PASS

**Step 7: Run full test suite**

Run: `make ci`
Expected: All pass

**Step 8: Commit**

```bash
git add src/keel/templates.py src/keel/core.py src/keel/cli.py tests/test_templates.py tests/test_workflow_behavior.py
git commit -m "fix: enforce workflow transitions — reject undefined state jumps for known types

Undefined transitions (e.g. triage→verifying) now raise ValueError instead of
proceeding with a warning. close_issue and reopen bypass enforcement since they
validate category correctness separately. Unknown types still allow all transitions.

Closes keel-ab92aa"
```

---

### Task 2: Allow Reparenting (keel-908d0e)

**Files:**
- Modify: `src/keel/core.py:1136-1148` (update_issue signature — add parent_id)
- Modify: `src/keel/core.py` (add cycle detection for parent hierarchy)
- Modify: `src/keel/mcp_server.py:285-305` (update_issue MCP schema)
- Modify: `src/keel/cli.py` (update command — add --parent flag)
- Test: `tests/test_core_gaps.py` (new tests)

**Step 1: Write failing tests**

Add to `tests/test_core_gaps.py`:

```python
class TestReparenting:
    def test_update_parent_id(self, db: KeelDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child")
        updated = db.update_issue(child.id, parent_id=parent.id)
        assert updated.parent_id == parent.id

    def test_update_parent_id_records_event(self, db: KeelDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child")
        db.update_issue(child.id, parent_id=parent.id, actor="tester")
        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'parent_changed'",
            (child.id,),
        ).fetchall()
        assert len(events) == 1
        assert events[0]["new_value"] == parent.id

    def test_update_parent_id_invalid_parent_raises(self, db: KeelDB) -> None:
        child = db.create_issue("Child")
        with pytest.raises(ValueError, match="does not reference"):
            db.update_issue(child.id, parent_id="nonexistent-123456")

    def test_update_parent_id_self_reference_raises(self, db: KeelDB) -> None:
        issue = db.create_issue("Issue")
        with pytest.raises(ValueError, match="cannot be its own parent"):
            db.update_issue(issue.id, parent_id=issue.id)

    def test_update_parent_id_cycle_raises(self, db: KeelDB) -> None:
        grandparent = db.create_issue("Grandparent")
        parent = db.create_issue("Parent", parent_id=grandparent.id)
        child = db.create_issue("Child", parent_id=parent.id)
        with pytest.raises(ValueError, match="circular"):
            db.update_issue(grandparent.id, parent_id=child.id)

    def test_clear_parent_id(self, db: KeelDB) -> None:
        parent = db.create_issue("Parent")
        child = db.create_issue("Child", parent_id=parent.id)
        # Use empty string to clear parent
        updated = db.update_issue(child.id, parent_id="")
        assert updated.parent_id is None
```

Run: `uv run pytest tests/test_core_gaps.py::TestReparenting -v`
Expected: FAIL (parent_id not accepted by update_issue)

**Step 2: Add `parent_id` to `update_issue`**

In `src/keel/core.py`, update_issue signature — add after `notes`:

```python
        parent_id: str | None = None,
```

Then add the handling block after the `notes` block (around line 1250, after description handling):

```python
        if parent_id is not None:
            if parent_id == "":
                # Clear parent
                if current.parent_id is not None:
                    self._record_event(issue_id, "parent_changed", actor=actor, old_value=current.parent_id, new_value="")
                    updates.append("parent_id = NULL")
            else:
                if parent_id == issue_id:
                    msg = f"Issue {issue_id} cannot be its own parent"
                    raise ValueError(msg)
                self._validate_parent_id(parent_id)
                # Check for circular parent chain
                ancestor = parent_id
                while ancestor is not None:
                    row = self.conn.execute("SELECT parent_id FROM issues WHERE id = ?", (ancestor,)).fetchone()
                    if row is None:
                        break
                    ancestor = row["parent_id"]
                    if ancestor == issue_id:
                        msg = f"Setting parent_id to '{parent_id}' would create a circular parent chain"
                        raise ValueError(msg)
                if parent_id != current.parent_id:
                    self._record_event(
                        issue_id, "parent_changed", actor=actor,
                        old_value=current.parent_id or "", new_value=parent_id,
                    )
                    updates.append("parent_id = ?")
                    params.append(parent_id)
```

Run: `uv run pytest tests/test_core_gaps.py::TestReparenting -v`
Expected: PASS

**Step 3: Add `parent_id` to MCP update_issue schema**

In `src/keel/mcp_server.py`, in the update_issue Tool inputSchema properties (around line 301), add:

```python
                    "parent_id": {"type": "string", "description": "New parent issue ID (empty string to clear)"},
```

And in the MCP handler for update_issue, pass it through:

```python
                parent_id=arguments.get("parent_id"),
```

**Step 4: Add `--parent` to CLI update command**

In `src/keel/cli.py`, add `--parent` option to the `update` command and pass `parent_id=parent` to `db.update_issue()`.

**Step 5: Run full test suite**

Run: `make ci`
Expected: All pass

**Step 6: Commit**

```bash
git add src/keel/core.py src/keel/mcp_server.py src/keel/cli.py tests/test_core_gaps.py
git commit -m "feat: allow reparenting issues via update_issue

Add parent_id parameter to update_issue with validation (exists, no self-ref,
no cycles). Exposed in MCP schema and CLI --parent flag. Empty string clears parent.

Closes keel-908d0e"
```

---

### Task 3: Housekeeping — Close Resolved Issues

**Step 1: Close pagination issue (already fixed)**

```bash
keel close keel-2d1d0b --reason="Already fixed — list_issues has limit=100, offset=0 defaults"
```

**Step 2: Close positive-feedback issues**

```bash
keel batch-close keel-a0cd06 keel-7ceb24 keel-e31f0a keel-2b180e keel-885345
```

**Step 3: Deduplicate claim_next selection logic issues**

```bash
keel close keel-a67afa --reason="Duplicate of keel-8500b6"
```

**Step 4: Update dogfood session epic**

```bash
keel add-comment keel-568457 "Transition enforcement and reparenting fixed. Remaining open issues are DX improvements."
```
