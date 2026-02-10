# Pattern 2 + DX Enrichment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix transition enforcement ripple effects (reopen MCP tool, claim=assign-only) and enrich 8 DX responses with actionable metadata.

**Architecture:** Pattern 2 changes core semantics of claim/release (assign-only, no status change) and adds reopen_issue. DX changes are all response-enrichment in the MCP server layer — no core behavioral changes. Each DX fix is independent.

**Tech Stack:** Python 3.13, SQLite, Click CLI, MCP server (fastmcp)

---

### Task 1: Add `reopen_issue` Core Method + MCP Tool

**Files:**
- Modify: `src/keel/core.py` (add reopen_issue method after close_issue)
- Modify: `src/keel/mcp_server.py` (add tool definition + handler)
- Test: `tests/test_workflow_behavior.py`, `tests/test_mcp.py`

**Step 1: Write failing tests**

In `tests/test_workflow_behavior.py`, add after TestReleaseClaim:

```python
class TestReopenIssue:
    def test_reopen_bug_returns_to_triage(self, db: KeelDB) -> None:
        issue = db.create_issue("Bug", type="bug")
        db.close_issue(issue.id)
        reopened = db.reopen_issue(issue.id)
        assert reopened.status == "triage"
        assert reopened.closed_at is None

    def test_reopen_task_returns_to_open(self, db: KeelDB) -> None:
        issue = db.create_issue("Task", type="task")
        db.close_issue(issue.id)
        reopened = db.reopen_issue(issue.id)
        assert reopened.status == "open"

    def test_reopen_already_open_raises(self, db: KeelDB) -> None:
        issue = db.create_issue("Task", type="task")
        with pytest.raises(ValueError, match="not in a done-category state"):
            db.reopen_issue(issue.id)

    def test_reopen_records_event(self, db: KeelDB) -> None:
        issue = db.create_issue("Task", type="task")
        db.close_issue(issue.id)
        db.reopen_issue(issue.id, actor="tester")
        events = db.conn.execute(
            "SELECT * FROM events WHERE issue_id = ? AND event_type = 'reopened'",
            (issue.id,),
        ).fetchall()
        assert len(events) == 1
```

Run: `uv run pytest tests/test_workflow_behavior.py::TestReopenIssue -v`
Expected: FAIL (reopen_issue doesn't exist)

**Step 2: Implement `reopen_issue` in core.py**

Add after `close_issue` method (after line ~1349):

```python
    def reopen_issue(self, issue_id: str, *, actor: str = "") -> Issue:
        """Reopen a closed issue, returning it to its type's initial state.

        Clears closed_at. Only works on issues in done-category states.
        """
        current = self.get_issue(issue_id)
        current_category = self.templates.get_category(current.type, current.status)
        if current_category is None:
            current_category = self._infer_status_category(current.status)
        if current_category != "done":
            msg = f"Cannot reopen {issue_id}: status '{current.status}' is not in a done-category state"
            raise ValueError(msg)

        initial_state = self.templates.get_initial_state(current.type)
        self._record_event(issue_id, "reopened", actor=actor, old_value=current.status, new_value=initial_state)
        return self.update_issue(issue_id, status=initial_state, actor=actor, _skip_transition_check=True)
```

Note: `update_issue` already handles closed_at clearing? Actually no — we need to clear it. Add to `update_issue`'s status handling: when leaving a done state, clear closed_at. Check if this already happens... Actually `update_issue` sets `closed_at` when entering done but doesn't clear it when leaving. Add a clause:

In `update_issue`, after the `if is_done:` block that sets closed_at, add:
```python
            else:
                # Clear closed_at when leaving a done-category state
                old_cat = self.templates.get_category(current.type, current.status)
                if (old_cat or self._infer_status_category(current.status)) == "done":
                    updates.append("closed_at = NULL")
```

Run: `uv run pytest tests/test_workflow_behavior.py::TestReopenIssue -v`
Expected: PASS

**Step 3: Add MCP tool definition and handler**

In `src/keel/mcp_server.py`, add tool definition near close_issue (after line ~319):

```python
        Tool(
            name="reopen_issue",
            description="Reopen a closed issue, returning it to its type's initial state. Clears closed_at.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Issue ID"},
                    "actor": {"type": "string", "description": "Agent/user identity for audit trail"},
                },
                "required": ["id"],
            },
        ),
```

Add handler case (near close_issue handler):

```python
        case "reopen_issue":
            try:
                issue = tracker.reopen_issue(
                    arguments["id"],
                    actor=arguments.get("actor", "mcp"),
                )
                _refresh_summary()
                return _text(issue.to_dict())
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['id']}", "code": "not_found"})
            except ValueError as e:
                return _text({"error": str(e), "code": "invalid"})
```

**Step 4: Update CLI reopen to use new core method**

In `src/keel/cli.py`, the `reopen` command currently calls `update_issue` directly. Change it to use `db.reopen_issue()`:

```python
                issue = db.reopen_issue(issue_id, actor=ctx.obj["actor"])
```

**Step 5: Run full tests, commit**

Run: `make ci`
Commit message: `feat: add reopen_issue core method, MCP tool, and clear closed_at on reopen`

---

### Task 2: Claim = Assign Only (No Status Change)

**Files:**
- Modify: `src/keel/core.py:1351-1387` (claim_issue), `1389-1423` (release_claim), `2185-2191` (undo claimed)
- Modify: `src/keel/mcp_server.py:459-462` (claim_issue description), `772-773` (claim_next description)
- Modify: Many test files (update status assertions)

**Step 1: Update `claim_issue` — assign only, no status change**

Replace the method body in `src/keel/core.py:1351-1387`:

```python
    def claim_issue(self, issue_id: str, *, assignee: str, actor: str = "") -> Issue:
        """Atomically claim an open-category issue with optimistic locking.

        Sets assignee only — does NOT change status. Agent uses update_issue
        to advance through the workflow after claiming.
        """
        current = self.get_issue(issue_id)

        # Get all open-category states for this type
        open_states: list[str] = []
        tpl = self.templates.get_type(current.type)
        if tpl is not None:
            open_states = [s.name for s in tpl.states if s.category == "open"]
        if not open_states:
            open_states = ["open"]

        if current.assignee and current.assignee != assignee:
            msg = f"Cannot claim {issue_id}: already assigned to '{current.assignee}'"
            raise ValueError(msg)

        placeholders = ",".join("?" * len(open_states))
        row = self.conn.execute(
            f"UPDATE issues SET assignee = ?, updated_at = ? WHERE id = ? AND status IN ({placeholders})",
            [assignee, _now_iso(), issue_id, *open_states],
        )

        if row.rowcount == 0:
            exists = self.conn.execute("SELECT status FROM issues WHERE id = ?", (issue_id,)).fetchone()
            if exists is None:
                msg = f"Issue not found: {issue_id}"
                raise KeyError(msg)
            msg = f"Cannot claim {issue_id}: status is '{exists['status']}', expected open-category state"
            raise ValueError(msg)

        self._record_event(issue_id, "claimed", actor=actor, new_value=assignee)
        self.conn.commit()
        return self.get_issue(issue_id)
```

Key changes: removed `status = ?` from SQL, removed wip_state lookup, removed old_value from event (no status change to record). Added assignee conflict check.

**Step 2: Update `release_claim` — clear assignee only, no status change**

Replace `src/keel/core.py:1389-1423`:

```python
    def release_claim(self, issue_id: str, *, actor: str = "") -> Issue:
        """Release a claimed issue by clearing its assignee.

        Does NOT change status. Only succeeds if issue has an assignee.
        """
        current = self.get_issue(issue_id)

        if not current.assignee:
            msg = f"Cannot release {issue_id}: no assignee set"
            raise ValueError(msg)

        self.conn.execute(
            "UPDATE issues SET assignee = '', updated_at = ? WHERE id = ?",
            [_now_iso(), issue_id],
        )

        self._record_event(issue_id, "released", actor=actor, old_value=current.assignee)
        self.conn.commit()
        return self.get_issue(issue_id)
```

**Step 3: Update undo handler for claimed**

In `src/keel/core.py:2185-2191`, undo for claimed should just clear assignee:

```python
            case "claimed":
                # Restore: clear the assignee that was set by claim
                self.conn.execute(
                    "UPDATE issues SET assignee = '', updated_at = ? WHERE id = ?",
                    (now, issue_id),
                )
```

**Step 4: Update MCP descriptions**

In `src/keel/mcp_server.py`:
- Line 460-462: change claim_issue description to `"Atomically claim an open issue by setting assignee (optimistic locking). Does NOT change status — use update_issue to advance through workflow after claiming."`
- Line 773: change claim_next description to `"Claim the highest-priority ready issue by setting assignee. Does NOT change status — use update_issue to advance through workflow after claiming."`

**Step 5: Update all tests that assert status changes after claim**

This is the largest part. Tests to update:

- `tests/test_workflow_behavior.py:261` — `test_claim_bug_uses_fixing`: assert status stays `triage`, not `fixing`
- `tests/test_workflow_behavior.py:268` — `test_claim_task_uses_in_progress`: assert status stays `open`, not `in_progress`
- `tests/test_workflow_behavior.py:275` — `test_claim_already_wip_fails`: needs rework — claim now fails if assigned, not if wip
- `tests/test_workflow_behavior.py:282` — `test_claim_unknown_type_uses_in_progress`: assert status stays `open`
- `tests/test_workflow_behavior.py:292-298` — `test_release_bug_returns_to_triage`: release just clears assignee now
- `tests/test_workflow_behavior.py:300-303` — `test_release_task_returns_to_open`: same
- `tests/test_core_gaps.py:437-441` — `test_claim_success`: status stays `open`
- `tests/test_core_gaps.py:443-449` — `test_claim_step_uses_template_states`: status stays `pending`
- `tests/test_core_gaps.py:451-455` — `test_claim_already_claimed`: error message changes
- `tests/test_core_gaps.py:457-461` — `test_claim_closed_issue`: should still fail
- `tests/test_backward_compat.py:61-65` — `test_task_claim_produces_in_progress`: status stays `open`
- `tests/test_backward_compat.py:67-72` — `test_task_release_produces_open`: just clears assignee
- `tests/test_undo.py:61-72` — `test_undo_claim`: no status restore, just clear assignee
- `tests/test_undo.py:74-89` — `test_undo_claim_from_non_initial_state`: same — just clear assignee
- `tests/test_v05_features.py:21+` — TestReleaseClaim: update all status assertions
- `tests/test_mcp.py:369+` — TestClaimIssue: update status assertions
- `tests/test_cli.py:712+` — TestClaimCli: update assertions

The pattern is the same everywhere: remove/change `assert claimed.status == "..."` to just check `assert claimed.assignee == "agent-1"` and ensure status is unchanged.

**Step 6: Run full tests, commit**

Run: `make ci`
Commit message: `fix: claim_issue assigns only — no status change, agent advances workflow manually`

---

### Task 3: DX Enrichment — get_template includes states + transitions (keel-ea3b72)

**Files:**
- Modify: `src/keel/core.py:849-869` (get_template method)
- Test: `tests/test_core_gaps.py`

**Step 1: Add test**

```python
class TestGetTemplateEnriched:
    def test_get_template_includes_states(self, db: KeelDB) -> None:
        tpl = db.get_template("bug")
        assert "states" in tpl
        state_names = [s["name"] for s in tpl["states"]]
        assert "triage" in state_names
        assert "closed" in state_names
        # Each state has a category
        assert all("category" in s for s in tpl["states"])

    def test_get_template_includes_transitions(self, db: KeelDB) -> None:
        tpl = db.get_template("bug")
        assert "transitions" in tpl
        assert any(t["from"] == "triage" and t["to"] == "confirmed" for t in tpl["transitions"])
```

**Step 2: Enrich get_template response**

In `src/keel/core.py:849-869`, add states and transitions to the return dict:

```python
        return {
            "type": tpl.type,
            "display_name": tpl.display_name,
            "description": tpl.description,
            "states": [{"name": s.name, "category": s.category} for s in tpl.states],
            "initial_state": tpl.initial_state,
            "transitions": [
                {
                    "from": t.from_state,
                    "to": t.to_state,
                    "enforcement": t.enforcement,
                    "requires_fields": list(t.requires_fields),
                }
                for t in tpl.transitions
            ],
            "fields_schema": fields_schema,
        }
```

**Step 3: Run tests, commit**

Commit: `feat(dx): get_template includes states and transitions — closes keel-ea3b72`

---

### Task 4: DX Enrichment — get_valid_transitions enriched missing_fields (keel-bcfc94)

**Files:**
- Modify: `src/keel/mcp_server.py:1281-1296` (get_valid_transitions handler)
- Modify: `src/keel/core.py` (get_valid_transitions to return field metadata)
- Test: `tests/test_mcp.py`

**Step 1: Enrich missing_fields in MCP response**

In `src/keel/mcp_server.py`, the get_valid_transitions handler (line 1281). Change the response to include field metadata for missing fields. Look up the field schema from the template:

```python
        case "get_valid_transitions":
            try:
                transitions = tracker.get_valid_transitions(arguments["issue_id"])
                issue = tracker.get_issue(arguments["issue_id"])
                tpl_data = tracker.get_template(issue.type)
                field_schemas = {f["name"]: f for f in (tpl_data or {}).get("fields_schema", [])}
                return _text(
                    [
                        {
                            "to": t.to,
                            "category": t.category,
                            "enforcement": t.enforcement,
                            "requires_fields": list(t.requires_fields),
                            "missing_fields": [
                                {
                                    "name": f,
                                    **{k: v for k, v in field_schemas.get(f, {}).items() if k != "name"},
                                }
                                for f in t.missing_fields
                            ],
                            "ready": t.ready,
                        }
                        for t in transitions
                    ]
                )
            except KeyError:
                return _text({"error": f"Issue not found: {arguments['issue_id']}", "code": "not_found"})
```

**Step 2: Run tests, commit**

Commit: `feat(dx): get_valid_transitions enriches missing_fields with schema metadata — closes keel-bcfc94`

---

### Task 5: DX Enrichment — get_workflow_guide type-to-pack fallback (keel-4df44e)

**Files:**
- Modify: `src/keel/mcp_server.py:1313-1319`
- Test: `tests/test_mcp.py`

**Step 1: Add type-to-pack lookup on failure**

In `src/keel/mcp_server.py:1313-1319`, when pack lookup fails, check if the name is a type:

```python
        case "get_workflow_guide":
            wf_pack = tracker.templates.get_pack(arguments["pack"])
            if wf_pack is None:
                # Check if the user passed a type name instead of a pack name
                tpl = tracker.templates.get_type(arguments["pack"])
                if tpl is not None:
                    wf_pack = tracker.templates.get_pack(tpl.pack)
                    if wf_pack is not None:
                        # Auto-resolve: return the pack guide
                        if wf_pack.guide is None:
                            return _text({"pack": wf_pack.pack, "guide": None, "message": "No guide available for this pack"})
                        return _text({"pack": wf_pack.pack, "guide": wf_pack.guide, "note": f"Resolved type '{arguments['pack']}' to pack '{wf_pack.pack}'"})
                return _text({
                    "error": f"Unknown pack: '{arguments['pack']}'. Use list_packs to see available packs, or list_types to see types.",
                    "code": "not_found",
                })
            if wf_pack.guide is None:
                return _text({"pack": wf_pack.pack, "guide": None, "message": "No guide available for this pack"})
            return _text({"pack": wf_pack.pack, "guide": wf_pack.guide})
```

**Step 2: Run tests, commit**

Commit: `feat(dx): get_workflow_guide resolves type names to pack names — closes keel-4df44e`

---

### Task 6: DX Enrichment — validate_issue upcoming requirements (keel-dea118)

**Files:**
- Modify: `src/keel/core.py:1592-1618` (validate_issue method)
- Modify: `src/keel/mcp_server.py:1300-1311`
- Test: `tests/test_workflow_behavior.py`

**Step 1: Add test**

```python
class TestValidateIssueUpcoming:
    def test_validate_shows_upcoming_requirements(self, db: KeelDB) -> None:
        """validate_issue should show fields needed for next transitions."""
        issue = db.create_issue("Feature", type="feature")
        result = db.validate_issue(issue.id)
        # Feature in 'proposed' needs acceptance_criteria for proposed->approved
        assert any("acceptance_criteria" in str(w) for w in result.warnings)
```

**Step 2: Enrich validate_issue**

In `src/keel/core.py:validate_issue`, after checking current-state fields, also check fields for next reachable transitions:

```python
    def validate_issue(self, issue_id: str) -> ValidationResult:
        from keel.templates import ValidationResult

        issue = self.get_issue(issue_id)
        tpl = self.templates.get_type(issue.type)
        if tpl is None:
            return ValidationResult(valid=True, warnings=(), errors=())

        warnings: list[str] = []

        # Check required_at fields for current state
        missing = self.templates.validate_fields_for_state(issue.type, issue.status, issue.fields)
        for field_name in missing:
            warnings.append(
                f"Field '{field_name}' is recommended at state '{issue.status}' "
                f"for type '{issue.type}' but is not populated."
            )

        # Check upcoming requirements: fields needed for next transitions
        transitions = self.templates.get_valid_transitions(issue.type, issue.status, issue.fields)
        for t in transitions:
            if t.missing_fields:
                fields_str = ", ".join(t.missing_fields)
                warnings.append(
                    f"Transition to '{t.to}' requires: {fields_str}"
                )

        return ValidationResult(valid=True, warnings=tuple(warnings), errors=())
```

**Step 3: Run tests, commit**

Commit: `feat(dx): validate_issue shows upcoming transition requirements — closes keel-dea118`

---

### Task 7: DX Enrichment — update_issue changed_fields (keel-971846)

**Files:**
- Modify: `src/keel/mcp_server.py:881-896` (update_issue handler)

**Step 1: Track changed fields and add to MCP response**

In `src/keel/mcp_server.py`, the update_issue handler. Before calling `tracker.update_issue`, get the issue before state. After, diff:

```python
        case "update_issue":
            try:
                before = tracker.get_issue(arguments["id"])
                issue = tracker.update_issue(
                    arguments["id"],
                    status=arguments.get("status"),
                    priority=arguments.get("priority"),
                    title=arguments.get("title"),
                    assignee=arguments.get("assignee"),
                    description=arguments.get("description"),
                    notes=arguments.get("notes"),
                    parent_id=arguments.get("parent_id"),
                    fields=arguments.get("fields"),
                    actor=arguments.get("actor", "mcp"),
                )
                _refresh_summary()
                result = issue.to_dict()
                # Compute changed fields for agent DX
                changed: list[str] = []
                if issue.status != before.status:
                    changed.append("status")
                if issue.priority != before.priority:
                    changed.append("priority")
                if issue.title != before.title:
                    changed.append("title")
                if issue.assignee != before.assignee:
                    changed.append("assignee")
                if issue.description != before.description:
                    changed.append("description")
                if issue.notes != before.notes:
                    changed.append("notes")
                if issue.parent_id != before.parent_id:
                    changed.append("parent_id")
                if issue.fields != before.fields:
                    changed.append("fields")
                result["changed_fields"] = changed
                return _text(result)
```

**Step 2: Run tests, commit**

Commit: `feat(dx): update_issue response includes changed_fields list — closes keel-971846`

---

### Task 8: DX Enrichment — claim_next selection reason (keel-8500b6)

**Files:**
- Modify: `src/keel/core.py:1425-1450` (claim_next return value)
- Modify: `src/keel/mcp_server.py:1361-1372` (claim_next handler)

**Step 1: Return selection reason from claim_next**

Change `claim_next` to return a tuple `(Issue | None, str)` — the issue and the reason. Actually, simpler: just modify the MCP handler to compute the reason from the claimed issue's attributes:

```python
        case "claim_next":
            claimed = tracker.claim_next(
                arguments["assignee"],
                type_filter=arguments.get("type"),
                priority_min=arguments.get("priority_min"),
                priority_max=arguments.get("priority_max"),
                actor=arguments.get("actor", arguments["assignee"]),
            )
            if claimed is None:
                return _text({"status": "empty", "reason": "No ready issues matching filters"})
            _refresh_summary()
            result = claimed.to_dict()
            parts = [f"P{claimed.priority}"]
            if claimed.type != "task":
                parts.append(f"type={claimed.type}")
            parts.append("ready issue (no blockers)")
            result["selection_reason"] = f"Highest-priority {', '.join(parts)}"
            return _text(result)
```

**Step 2: Run tests, commit**

Commit: `feat(dx): claim_next explains selection reason — closes keel-8500b6`

---

### Task 9: DX Enrichment — create_plan specific errors (keel-13079e)

**Files:**
- Modify: `src/keel/core.py:1854+` (create_plan validation)

**Step 1: Add validation at the top of create_plan**

Add explicit validation before the inserts:

```python
        # Validate inputs
        if not milestone.get("title", "").strip():
            msg = "Milestone 'title' is required and cannot be empty"
            raise ValueError(msg)
        for phase_idx, phase_data in enumerate(phases):
            if not phase_data.get("title", "").strip():
                msg = f"Phase {phase_idx + 1} 'title' is required and cannot be empty"
                raise ValueError(msg)
            for step_idx, step_data in enumerate(phase_data.get("steps", [])):
                if not step_data.get("title", "").strip():
                    msg = f"Phase {phase_idx + 1}, Step {step_idx + 1} 'title' is required and cannot be empty"
                    raise ValueError(msg)
```

**Step 2: Run tests, commit**

Commit: `feat(dx): create_plan validation specifies which field failed — closes keel-13079e`

---

### Task 10: DX Enrichment — get_plan progress detail (keel-ad4c4f)

**Files:**
- Modify: `src/keel/mcp_server.py:978-983` (get_plan handler)

The `get_plan` core method already returns per-phase `completed` and `total` counts (see core.py:1840-1848). The issue is that the MCP handler just passes it through as-is. But looking at the data, it already has:
- `result["total_steps"]` and `result["completed_steps"]` at the top level
- Each phase has `"total"`, `"completed"`, `"ready"` counts

So this may already be sufficient. Check if the response includes a `progress_pct`:

Add a progress percentage to the MCP response:

```python
        case "get_plan":
            try:
                plan_data = tracker.get_plan(arguments["milestone_id"])
                # Add overall progress percentage
                total = plan_data.get("total_steps", 0)
                completed = plan_data.get("completed_steps", 0)
                plan_data["progress_pct"] = round(completed / total * 100, 1) if total > 0 else 0.0
                return _text(plan_data)
            except KeyError:
                return _text({"error": f"Milestone not found: {arguments['milestone_id']}", "code": "not_found"})
```

**Step 2: Run tests, commit**

Commit: `feat(dx): get_plan includes progress_pct — closes keel-ad4c4f`

---

### Task 11: Housekeeping — Close Fixed Issues

```bash
keel close keel-ea3b72 --reason="Fixed: get_template now includes states and transitions"
keel close keel-bcfc94 --reason="Fixed: get_valid_transitions enriches missing_fields with metadata"
keel close keel-4df44e --reason="Fixed: get_workflow_guide resolves type names to packs"
keel close keel-dea118 --reason="Fixed: validate_issue shows upcoming transition requirements"
keel close keel-971846 --reason="Fixed: update_issue response includes changed_fields"
keel close keel-8500b6 --reason="Fixed: claim_next includes selection_reason"
keel close keel-13079e --reason="Fixed: create_plan validation specifies which field"
keel close keel-ad4c4f --reason="Fixed: get_plan includes progress_pct"
keel close keel-b65fc8 --reason="Fixed: claim_issue is now assign-only, no automatic status change"
keel close keel-0a0e56 --reason="Fixed: claim_issue is now assign-only, no automatic status change"
```
