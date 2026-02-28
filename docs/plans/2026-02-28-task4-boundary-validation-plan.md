# Task 4: Wire up MCP Validation Helpers & Tighten Boundary Validation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add consistent boundary validation for priority (0-4 range) and actor (non-empty, no control chars, max 128 chars) across all three entry points — MCP, Dashboard REST, CLI — using a shared pure-function validation module.

**Architecture:** New `filigree/validation.py` with `sanitize_actor()` pure function. Each boundary wraps it in its own error format. MCP reuses existing `_validate_int_range` for priority. Dashboard gets `_validate_priority()`. CLI gets `click.IntRange(0, 4)` for priority and `sanitize_actor()` in the group callback.

**Tech Stack:** Python 3.12, pytest, Click, FastAPI, MCP SDK

**Design doc:** `docs/plans/2026-02-28-task4-boundary-validation-design.md`

---

### Task 1: Create `filigree/validation.py` with `sanitize_actor()`

**Files:**
- Create: `src/filigree/validation.py`
- Create: `tests/unit/test_validation.py`

**Step 1: Write the failing tests**

Create `tests/unit/test_validation.py`:

```python
"""Tests for the shared validation module."""

from __future__ import annotations

import pytest

from filigree.validation import sanitize_actor


class TestSanitizeActor:
    """sanitize_actor() pure function tests."""

    def test_valid_simple(self) -> None:
        cleaned, err = sanitize_actor("alice")
        assert cleaned == "alice"
        assert err is None

    def test_strips_whitespace(self) -> None:
        cleaned, err = sanitize_actor("  spaced  ")
        assert cleaned == "spaced"
        assert err is None

    def test_at_max_length(self) -> None:
        cleaned, err = sanitize_actor("a" * 128)
        assert cleaned == "a" * 128
        assert err is None

    def test_over_max_length(self) -> None:
        cleaned, err = sanitize_actor("a" * 129)
        assert cleaned == ""
        assert err is not None
        assert "128" in err

    def test_empty_string(self) -> None:
        cleaned, err = sanitize_actor("")
        assert cleaned == ""
        assert err is not None
        assert "empty" in err

    def test_whitespace_only(self) -> None:
        cleaned, err = sanitize_actor("   ")
        assert cleaned == ""
        assert err is not None
        assert "empty" in err

    def test_not_a_string(self) -> None:
        cleaned, err = sanitize_actor(123)
        assert cleaned == ""
        assert err is not None
        assert "string" in err

    def test_none_value(self) -> None:
        cleaned, err = sanitize_actor(None)
        assert cleaned == ""
        assert err is not None
        assert "string" in err

    def test_control_char_null(self) -> None:
        cleaned, err = sanitize_actor("\x00bad")
        assert cleaned == ""
        assert err is not None
        assert "control" in err.lower()

    def test_control_char_newline(self) -> None:
        cleaned, err = sanitize_actor("\nbad")
        assert cleaned == ""
        assert err is not None
        assert "control" in err.lower()

    def test_bom(self) -> None:
        cleaned, err = sanitize_actor("\uFEFF")
        assert cleaned == ""
        assert err is not None

    def test_zero_width_space(self) -> None:
        cleaned, err = sanitize_actor("\u200B")
        assert cleaned == ""
        assert err is not None

    def test_rtl_override(self) -> None:
        cleaned, err = sanitize_actor("\u202E")
        assert cleaned == ""
        assert err is not None

    def test_unicode_name_allowed(self) -> None:
        """Non-ASCII normal letters are fine."""
        cleaned, err = sanitize_actor("café-bot")
        assert cleaned == "café-bot"
        assert err is None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'filigree.validation'`

**Step 3: Create the `tests/unit/` directory if needed and write implementation**

Ensure `tests/unit/__init__.py` exists (may need creating).

Create `src/filigree/validation.py`:

```python
"""Shared validation functions for all entry points.

Pure functions — no MCP, FastAPI, or Click dependencies.
"""

from __future__ import annotations

import unicodedata
from typing import Any

_MAX_ACTOR_LENGTH = 128


def sanitize_actor(value: Any) -> tuple[str, str | None]:
    """Validate and clean an actor name.

    Returns (cleaned_actor, None) on success or ("", error_message) on failure.
    Strips whitespace, then checks: non-empty, max length, no control/format chars.
    """
    if not isinstance(value, str):
        return ("", "actor must be a string")
    cleaned = value.strip()
    if not cleaned:
        return ("", "actor must not be empty")
    if len(cleaned) > _MAX_ACTOR_LENGTH:
        return ("", f"actor must be at most {_MAX_ACTOR_LENGTH} characters")
    for ch in cleaned:
        cat = unicodedata.category(ch)
        if cat.startswith("C"):  # Cc (control) and Cf (format)
            return ("", f"actor must not contain control characters (found U+{ord(ch):04X})")
    return (cleaned, None)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_validation.py -v`
Expected: All 15 tests PASS

**Step 5: Commit**

```bash
git add src/filigree/validation.py tests/unit/ tests/unit/test_validation.py
git commit -m "feat(validation): add shared sanitize_actor() pure function"
```

---

### Task 2: Wire actor validation into MCP handlers

**Files:**
- Modify: `src/filigree/mcp_tools/common.py` — add `_validate_actor()` wrapper
- Modify: `src/filigree/mcp_tools/issues.py` — wire into 9 handlers
- Modify: `src/filigree/mcp_tools/planning.py` — wire into 3 handlers
- Modify: `src/filigree/mcp_tools/meta.py` — wire into 4 handlers
- Create: `tests/mcp/test_boundary_validation.py`

**Step 1: Write the failing tests**

Create `tests/mcp/test_boundary_validation.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/mcp/test_boundary_validation.py -v`
Expected: FAIL — tests pass through to core instead of returning early validation errors

**Step 3: Add `_validate_actor()` wrapper to `mcp_tools/common.py`**

Add at the bottom of `src/filigree/mcp_tools/common.py`:

```python
from filigree.validation import sanitize_actor


def _validate_actor(value: Any) -> tuple[str, list[TextContent] | None]:
    """Sanitize actor, returning (cleaned, None) or ("", error_response)."""
    cleaned, err = sanitize_actor(value)
    if err:
        return ("", _text({"error": err, "code": "validation_error"}))
    return (cleaned, None)
```

**Step 4: Wire into `mcp_tools/issues.py` — 9 handlers**

Add `_validate_actor` to the imports from `filigree.mcp_tools.common`.

For each of these 9 handlers, add actor validation before passing to core:

1. `_handle_create_issue` (line 376) — `arguments.get("actor", "mcp")` on line 391
2. `_handle_update_issue` (line 399) — `arguments.get("actor", "mcp")` on line 415
3. `_handle_close_issue` (line 443) — `arguments.get("actor", "mcp")` on line 452
4. `_handle_reopen_issue` (line 471) — `arguments.get("actor", "mcp")` on line 478
5. `_handle_claim_issue` (line 510) — `arguments.get("actor", arguments["assignee"])` on line 518
6. `_handle_release_claim` (line 528) — `arguments.get("actor", "mcp")` on line 533
7. `_handle_claim_next` (line 542) — `arguments.get("actor", arguments["assignee"])` on line 551
8. `_handle_batch_close` (line 567) — `arguments.get("actor", "mcp")` on line 578
9. `_handle_batch_update` (line 593) — `arguments.get("actor", "mcp")` on line 609

Pattern for each handler — add these lines right after the function body begins (after `tracker = _get_db()` or at the top):

```python
    actor, actor_err = _validate_actor(arguments.get("actor", "mcp"))
    if actor_err:
        return actor_err
```

Then replace the `actor=arguments.get("actor", "mcp")` in the core call with `actor=actor`.

For `_handle_claim_issue` and `_handle_claim_next` where the default is `arguments["assignee"]`:
```python
    actor, actor_err = _validate_actor(arguments.get("actor", arguments["assignee"]))
    if actor_err:
        return actor_err
```

**Step 5: Wire into `mcp_tools/planning.py` — 3 handlers**

Add import: `from filigree.mcp_tools.common import _text, _validate_actor`

1. `_handle_add_dependency` (line 142) — `actor=arguments.get("actor", "mcp")` on line 150
2. `_handle_remove_dependency` (line 159) — `actor=arguments.get("actor", "mcp")` on line 166
3. `_handle_create_plan` (line 209) — `actor=arguments.get("actor", "mcp")` on line 217

Same pattern — validate, early return on error, use cleaned value.

**Step 6: Wire into `mcp_tools/meta.py` — 4 handlers**

Add import: `from filigree.mcp_tools.common import _text, _validate_actor`

1. `_handle_add_comment` (line 246) — `author=arguments.get("actor", "mcp")` on line 258
2. `_handle_batch_add_comment` (line 332) — `author=arguments.get("actor", "mcp")` on line 344
3. `_handle_archive_closed` (line 423) — `actor=arguments.get("actor", "mcp")` on line 429
4. `_handle_undo_last` (line 443) — `actor=arguments.get("actor", "mcp")` on line 448

Note: `_handle_add_comment` and `_handle_batch_add_comment` pass actor as `author=` parameter, not `actor=`.

**Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/mcp/test_boundary_validation.py -v`
Expected: All 9 tests PASS

**Step 8: Run full MCP test suite to check for regressions**

Run: `uv run pytest tests/mcp/ -v`
Expected: All existing tests PASS

**Step 9: Commit**

```bash
git add src/filigree/mcp_tools/common.py src/filigree/mcp_tools/issues.py src/filigree/mcp_tools/planning.py src/filigree/mcp_tools/meta.py tests/mcp/test_boundary_validation.py
git commit -m "feat(mcp): wire actor validation into 16 MCP handlers"
```

---

### Task 3: Wire priority validation into MCP `issues.py`

**Files:**
- Modify: `src/filigree/mcp_tools/issues.py` — add `_validate_int_range` to 5 handlers
- Modify: `tests/mcp/test_boundary_validation.py` — add priority tests

**Step 1: Write the failing tests**

Add to `tests/mcp/test_boundary_validation.py`:

```python
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
        result = await call_tool(
            "claim_next", {"assignee": "bot", "priority_min": -1}
        )
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_claim_next_priority_max_out_of_range(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool(
            "claim_next", {"assignee": "bot", "priority_max": 5}
        )
        data = _parse(result)
        assert data["code"] == "validation_error"

    async def test_update_issue_priority_none_allowed(self, mcp_db: FiligreeDB) -> None:
        """Not providing priority should be fine (optional)."""
        issue = mcp_db.create_issue("Target")
        result = await call_tool("update_issue", {"id": issue.id, "title": "New"})
        data = _parse(result)
        assert "error" not in data or "code" not in data
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/mcp/test_boundary_validation.py::TestMCPPriorityValidation -v`
Expected: FAIL — out-of-range priorities pass through to core

**Step 3: Wire `_validate_int_range` into 5 handlers**

Add `_validate_int_range` to the imports from `filigree.mcp_tools.common` in `issues.py`.

For each handler, add validation before the core call:

1. **`_handle_create_issue`** — validate `arguments.get("priority", 2)`:
```python
    priority = arguments.get("priority", 2)
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
```
Then use `priority=priority` in the core call.

2. **`_handle_update_issue`** — validate `arguments.get("priority")` (optional):
```python
    priority = arguments.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
```
Then use `priority=priority` in the core call.

3. **`_handle_list_issues`** — validate `arguments.get("priority")` (filter):
```python
    priority = arguments.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
```
Then use `priority=priority` in the core call.

4. **`_handle_batch_update`** — validate `arguments.get("priority")`:
```python
    priority = arguments.get("priority")
    priority_err = _validate_int_range(priority, "priority", min_val=0, max_val=4)
    if priority_err:
        return priority_err
```

5. **`_handle_claim_next`** — validate both:
```python
    priority_min = arguments.get("priority_min")
    pmin_err = _validate_int_range(priority_min, "priority_min", min_val=0, max_val=4)
    if pmin_err:
        return pmin_err
    priority_max = arguments.get("priority_max")
    pmax_err = _validate_int_range(priority_max, "priority_max", min_val=0, max_val=4)
    if pmax_err:
        return pmax_err
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/mcp/test_boundary_validation.py -v`
Expected: All tests PASS (both actor and priority classes)

**Step 5: Run full MCP test suite**

Run: `uv run pytest tests/mcp/ -v`
Expected: All existing tests PASS

**Step 6: Commit**

```bash
git add src/filigree/mcp_tools/issues.py tests/mcp/test_boundary_validation.py
git commit -m "feat(mcp): wire priority range validation into 5 issue handlers"
```

---

### Task 4: Dashboard priority and actor validation

**Files:**
- Modify: `src/filigree/dashboard_routes/common.py` — add `_validate_priority()` and `_validate_actor()`
- Modify: `src/filigree/dashboard_routes/issues.py` — wire into routes
- Create: `tests/api/test_boundary_validation.py`

**Step 1: Write the failing tests**

Create `tests/api/test_boundary_validation.py`:

```python
"""Dashboard API boundary validation tests for priority and actor."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


class TestDashboardPriorityValidation:
    """Priority range checks in dashboard routes."""

    async def test_update_issue_priority_too_high(self, client: AsyncClient) -> None:
        # Create an issue first
        resp = await client.post("/api/issues", json={"title": "Target"})
        assert resp.status_code == 201
        issue_id = resp.json()["id"]
        # Try invalid priority
        resp = await client.patch(f"/api/issue/{issue_id}", json={"priority": 5})
        assert resp.status_code == 400
        assert "INVALID_PRIORITY" in resp.json()["error"]["code"]

    async def test_update_issue_priority_too_low(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.patch(f"/api/issue/{issue_id}", json={"priority": -1})
        assert resp.status_code == 400

    async def test_create_issue_priority_out_of_range(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Bad", "priority": 99})
        assert resp.status_code == 400
        assert "INVALID_PRIORITY" in resp.json()["error"]["code"]

    async def test_create_issue_priority_boundary_0(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "P0", "priority": 0})
        assert resp.status_code == 201
        assert resp.json()["priority"] == 0

    async def test_create_issue_priority_boundary_4(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "P4", "priority": 4})
        assert resp.status_code == 201
        assert resp.json()["priority"] == 4

    async def test_batch_update_priority_out_of_range(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.post(
            "/api/batch/update",
            json={"issue_ids": [issue_id], "priority": 5},
        )
        assert resp.status_code == 400

    async def test_create_issue_priority_float(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Bad", "priority": 2.5})
        assert resp.status_code == 400

    async def test_create_issue_priority_bool(self, client: AsyncClient) -> None:
        """bool is a subclass of int — should be rejected."""
        resp = await client.post("/api/issues", json={"title": "Bad", "priority": True})
        assert resp.status_code == 400


class TestDashboardActorValidation:
    """Actor validation in dashboard routes."""

    async def test_update_issue_empty_actor(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.patch(f"/api/issue/{issue_id}", json={"actor": "", "title": "New"})
        assert resp.status_code == 400
        assert "VALIDATION_ERROR" in resp.json()["error"]["code"]

    async def test_close_issue_control_char_actor(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.post(f"/api/issue/{issue_id}/close", json={"actor": "\x00evil"})
        assert resp.status_code == 400

    async def test_create_issue_bom_actor(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Test", "actor": "\uFEFF"})
        assert resp.status_code == 400

    async def test_claim_issue_valid_actor(self, client: AsyncClient) -> None:
        """Valid actor should pass through."""
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.post(
            f"/api/issue/{issue_id}/claim",
            json={"assignee": "bot", "actor": "test-agent"},
        )
        assert resp.status_code == 200
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_boundary_validation.py -v`
Expected: FAIL — out-of-range priorities and invalid actors pass through

**Step 3: Add helpers to `dashboard_routes/common.py`**

Add at the bottom of `src/filigree/dashboard_routes/common.py`:

```python
from filigree.validation import sanitize_actor as _sanitize_actor


def _validate_priority(value: Any, *, required: bool = False) -> int | None | JSONResponse:
    """Validate a priority value from JSON body.

    Returns the validated int, None (if optional and absent), or a JSONResponse error.
    """
    from fastapi.responses import JSONResponse

    if value is None:
        if required:
            return _error_response("priority is required", "VALIDATION_ERROR", 400)
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        return _error_response(
            "priority must be an integer between 0 and 4", "INVALID_PRIORITY", 400
        )
    if not (0 <= value <= 4):
        return _error_response(
            f"priority must be between 0 and 4, got {value}", "INVALID_PRIORITY", 400
        )
    return value


def _validate_actor(value: Any) -> tuple[str, JSONResponse | None]:
    """Validate an actor name from JSON body.

    Returns (cleaned_actor, None) on success or ("", JSONResponse) on error.
    """
    from fastapi.responses import JSONResponse

    cleaned, err = _sanitize_actor(value)
    if err:
        return ("", _error_response(err, "VALIDATION_ERROR", 400))
    return (cleaned, None)
```

**Step 4: Wire into `dashboard_routes/issues.py` — priority (3 routes)**

Add to imports: `from filigree.dashboard_routes.common import _error_response, _parse_json_body, _validate_actor, _validate_priority`

1. **`api_update_issue`** (line 164) — replace lines 171-173:
```python
        priority = _validate_priority(body.get("priority"))
        if isinstance(priority, JSONResponse):
            return priority
```

2. **`api_batch_update`** (line 271) — replace lines 283-285:
```python
        priority = _validate_priority(body.get("priority"))
        if isinstance(priority, JSONResponse):
            return priority
```

3. **`api_create_issue`** (line 338) — replace lines 345-347:
```python
        priority = _validate_priority(body.get("priority", 2))
        if isinstance(priority, JSONResponse):
            return priority
```

**Step 5: Wire into `dashboard_routes/issues.py` — actor (10 routes)**

For each route that reads actor from `body.get("actor", ...)` or `body.pop("actor", ...)`, add validation:

```python
        actor, actor_err = _validate_actor(body.get("actor", "dashboard"))
        if actor_err:
            return actor_err
```

The 10 routes are:
1. `api_update_issue` — `body.pop("actor", "dashboard")` (line 170)
2. `api_close_issue` — `body.get("actor", "dashboard")` (line 198)
3. `api_reopen_issue` — `body.get("actor", "dashboard")` (line 217)
4. `api_batch_update` — `body.get("actor", "dashboard")` (line 282)
5. `api_batch_close` — `body.get("actor", "dashboard")` (line 313)
6. `api_create_issue` — `body.get("actor", "")` (line 359)
7. `api_claim_issue` — `body.get("actor", "dashboard")` (line 374)
8. `api_release_claim` — `body.get("actor", "dashboard")` (line 389)
9. `api_claim_next` — `body.get("actor", "dashboard")` (line 407)
10. `api_add_dependency` — `body.get("actor", "dashboard")` (line 423)

Note: `api_update_issue` uses `body.pop("actor", "dashboard")` — change to `body.get` + validate, then pop after validation succeeds to preserve the existing behavior of removing actor from body before passing to update_issue.

Note: `api_create_issue` defaults to `""` not `"dashboard"` — keep the empty default but validate. Since empty string fails actor validation, change the default to `"dashboard"` to match other routes, OR skip validation when no actor is provided. Looking at the code, `body.get("actor", "")` passes empty string to core — this is the existing behavior. We should default to `"dashboard"` for consistency.

Note: `api_remove_dependency` (line 432-439) hardcodes `actor="dashboard"` — no user input, no validation needed.

**Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_boundary_validation.py -v`
Expected: All tests PASS

**Step 7: Run full API test suite**

Run: `uv run pytest tests/api/ -v`
Expected: All existing tests PASS

**Step 8: Commit**

```bash
git add src/filigree/dashboard_routes/common.py src/filigree/dashboard_routes/issues.py tests/api/test_boundary_validation.py
git commit -m "feat(dashboard): add priority range and actor validation to API routes"
```

---

### Task 5: CLI priority and actor validation

**Files:**
- Modify: `src/filigree/cli_commands/issues.py` — `click.IntRange(0, 4)` on 5 options
- Modify: `src/filigree/cli_commands/meta.py` — `click.IntRange(0, 4)` on 1 option
- Modify: `src/filigree/cli.py` — actor validation in group callback
- Create: `tests/cli/test_boundary_validation.py`

**Step 1: Write the failing tests**

Create `tests/cli/test_boundary_validation.py`:

```python
"""CLI boundary validation tests for priority and actor."""

from __future__ import annotations

import os
from pathlib import Path

from click.testing import CliRunner

from filigree.cli import cli


class TestCLIPriorityValidation:
    """click.IntRange(0, 4) on all priority options."""

    def test_create_priority_too_high(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test", "--priority", "5"])
        assert result.exit_code != 0
        assert "not in the range" in result.output or "not in the range" in (result.output + str(result.exception or ""))

    def test_create_priority_too_low(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test", "--priority", "-1"])
        assert result.exit_code != 0

    def test_create_priority_boundary_0(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test", "--priority", "0"])
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_create_priority_boundary_4(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test", "--priority", "4"])
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_list_priority_filter_too_high(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["list", "--priority", "5"])
        assert result.exit_code != 0

    def test_update_priority_too_high(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        # Create an issue first
        create_result = runner.invoke(cli, ["create", "Target"])
        assert create_result.exit_code == 0
        issue_id = create_result.output.split(":")[0].replace("Created ", "").strip()
        result = runner.invoke(cli, ["update", issue_id, "--priority", "5"])
        assert result.exit_code != 0

    def test_claim_next_priority_min_too_low(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(
            cli, ["claim-next", "--assignee", "bot", "--priority-min", "-1"]
        )
        assert result.exit_code != 0

    def test_claim_next_priority_max_too_high(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(
            cli, ["claim-next", "--assignee", "bot", "--priority-max", "5"]
        )
        assert result.exit_code != 0

    def test_batch_update_priority_too_high(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        create_result = runner.invoke(cli, ["create", "Target"])
        issue_id = create_result.output.split(":")[0].replace("Created ", "").strip()
        result = runner.invoke(cli, ["batch-update", issue_id, "--priority", "5"])
        assert result.exit_code != 0


class TestCLIActorValidation:
    """Actor validation in CLI group callback."""

    def test_empty_actor(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["--actor", "", "create", "Test"])
        assert result.exit_code != 0
        assert "actor" in result.output.lower() or "actor" in str(result.exception or "").lower()

    def test_control_char_actor(
        self, cli_in_project: tuple[CliRunner, Path]
    ) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["--actor", "\x00bad", "create", "Test"])
        assert result.exit_code != 0

    def test_valid_actor(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["--actor", "my-bot", "create", "Test"])
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_default_actor_cli(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        """Default actor 'cli' should pass validation."""
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "Test"])
        assert result.exit_code == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_boundary_validation.py -v`
Expected: FAIL — priority tests accept out-of-range values, actor tests pass through

**Step 3: Change priority options to `click.IntRange(0, 4)` in `cli_commands/issues.py`**

Replace `type=int` with `type=click.IntRange(0, 4)` on these 5 lines:

1. Line 22: `@click.option("--priority", "-p", default=2, type=int, ...)`
   → `@click.option("--priority", "-p", default=2, type=click.IntRange(0, 4), ...)`

2. Line 138: `@click.option("--priority", "-p", default=None, type=int, ...)`
   → `@click.option("--priority", "-p", default=None, type=click.IntRange(0, 4), ...)`

3. Line 183: `@click.option("--priority", "-p", default=None, type=int, ...)`
   → `@click.option("--priority", "-p", default=None, type=click.IntRange(0, 4), ...)`

4. Line 359: `@click.option("--priority-min", default=None, type=int, ...)`
   → `@click.option("--priority-min", default=None, type=click.IntRange(0, 4), ...)`

5. Line 360: `@click.option("--priority-max", default=None, type=int, ...)`
   → `@click.option("--priority-max", default=None, type=click.IntRange(0, 4), ...)`

**Step 4: Change priority option in `cli_commands/meta.py`**

Line 205: `@click.option("--priority", "-p", default=None, type=int, ...)`
→ `@click.option("--priority", "-p", default=None, type=click.IntRange(0, 4), ...)`

**Step 5: Add actor validation to `cli.py` group callback**

Modify `src/filigree/cli.py`:

```python
from filigree.validation import sanitize_actor

@click.group()
@click.version_option(version=__version__, prog_name="filigree")
@click.option("--actor", default="cli", help="Actor identity for audit trail (default: cli)")
@click.pass_context
def cli(ctx: click.Context, actor: str) -> None:
    """Filigree — agent-native issue tracker."""
    ctx.ensure_object(dict)
    cleaned, err = sanitize_actor(actor)
    if err:
        raise click.BadParameter(err, param_hint="'--actor'")
    ctx.obj["actor"] = cleaned
```

**Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_boundary_validation.py -v`
Expected: All tests PASS

**Step 7: Run full CLI test suite**

Run: `uv run pytest tests/cli/ -v`
Expected: All existing tests PASS

**Step 8: Commit**

```bash
git add src/filigree/cli.py src/filigree/cli_commands/issues.py src/filigree/cli_commands/meta.py tests/cli/test_boundary_validation.py
git commit -m "feat(cli): add IntRange(0,4) priority and actor validation"
```

---

### Task 6: Full CI verification and type checking

**Files:**
- No new files — verification only

**Step 1: Run ruff check**

Run: `uv run ruff check src/ tests/`
Expected: No errors

**Step 2: Run ruff format check**

Run: `uv run ruff format --check src/ tests/`
Expected: No formatting issues

**Step 3: Run mypy**

Run: `uv run mypy src/filigree/`
Expected: No new errors (some pre-existing may exist)

**Step 4: Run full test suite**

Run: `uv run pytest --tb=short`
Expected: All tests PASS

**Step 5: Commit any fixups needed**

If any lint/type fixes were needed, commit:
```bash
git add -u
git commit -m "fix: address lint and type issues from boundary validation"
```
