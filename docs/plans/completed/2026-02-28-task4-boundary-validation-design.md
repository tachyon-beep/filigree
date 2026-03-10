# Task 4: Wire up MCP Validation Helpers & Tighten Boundary Validation — Design

**Parent epic:** Type Safety & Programming by Contract (`filigree-225ec4`)
**Tracker issue:** `filigree-4aca975fe1`
**Date:** 2026-02-28 (revised)

## Problem

Filigree has three independent entry points — MCP tools (LLM agents), dashboard REST API (browser/scripts), and CLI (humans) — all calling the same `core.py` methods. Core validation is authoritative (`db_issues.py:108` enforces `0 <= priority <= 4`), but boundary validation is inconsistent.

### Verified gap inventory

**Priority range — MCP (`mcp_tools/issues.py` only):**
Only `issues.py` handlers accept priority. The other three MCP modules (`planning.py`, `meta.py`, `workflow.py`) do not accept priority at all. Five handlers pass raw priority to core without validation:
- `_handle_create_issue` — `arguments.get("priority", 2)` (line 384)
- `_handle_update_issue` — `arguments.get("priority")` (line 408)
- `_handle_list_issues` — `arguments.get("priority")` (line 358, used as filter)
- `_handle_batch_update` — `arguments.get("priority")` (line 606)
- `_handle_claim_next` — `arguments.get("priority_min")` / `arguments.get("priority_max")` (lines 549–550)

An out-of-range int causes a core `ValueError` which is caught and returned as `{"error": str, "code": "validation_error"}` — but the error message is less clear than what `_validate_int_range` produces.

**Priority range — Dashboard (`dashboard_routes/issues.py`):**
Three handlers check `isinstance(priority, int)` but skip the 0-4 range check. Their error messages say "between 0 and 4" but don't enforce it:
- `api_update_issue` — line 172
- `api_batch_update` — line 284
- `api_create_issue` — line 346

**Priority range — CLI (`cli_commands/issues.py`, `cli_commands/meta.py`):**
Six `--priority` options use bare `type=int`, accepting any integer:
- `cli_commands/issues.py:22` — `create --priority` (default 2)
- `cli_commands/issues.py:138` — `list --priority` (filter)
- `cli_commands/issues.py:183` — `update --priority`
- `cli_commands/issues.py:359` — `claim-next --priority-min`
- `cli_commands/issues.py:360` — `claim-next --priority-max`
- `cli_commands/meta.py:205` — `batch-update --priority`

The `click.IntRange(min=0)` pattern (lower-bound only) already exists in `cli_commands/admin.py:455,475,492`, establishing `IntRange` as the project convention. The bounded form `IntRange(0, 4)` would be new.

**Actor name validation — zero validation anywhere:**
16 MCP handlers pass actor to core without any validation. 10 dashboard routes accept actor from JSON body. CLI accepts `--actor` as a bare string in `cli.py:17`. No boundary validates actor name for emptiness, length, or control characters.

MCP handlers using actor (16 total):
- `issues.py`: 9 handlers (`create_issue`, `update_issue`, `close_issue`, `reopen_issue`, `claim_issue`, `release_claim`, `claim_next`, `batch_close`, `batch_update`)
- `planning.py`: 3 handlers (`add_dependency`, `remove_dependency`, `create_plan`)
- `meta.py`: 4 handlers (`add_comment`, `batch_add_comment`, `archive_closed`, `undo_last`)
- `workflow.py`: 0 handlers

Note: `batch_add_label` in `meta.py` declares `actor` in its MCP schema but the handler never uses it — not a validation concern, just a schema/handler mismatch to be aware of.

Dashboard routes using actor (10 from JSON body + 1 hardcoded):
- `api_update_issue` (line 170), `api_close_issue` (198), `api_reopen_issue` (217), `api_batch_update` (282), `api_batch_close` (313), `api_create_issue` (359), `api_claim_issue` (374), `api_release_claim` (389), `api_claim_next` (407), `api_add_dependency` (423)
- `api_remove_dependency` (436) — hardcoded `actor="dashboard"`, no user input

**Unused helpers:**
`_validate_int_range` and `_validate_str` in `mcp_tools/common.py` are only imported by `mcp_tools/files.py`. Wiring them into `issues.py` is the primary goal.

### What is NOT a gap

- **`workflow.py`** — none of its 9 handlers accept priority or actor. No changes needed.
- **`planning.py` priority** — `create_plan` has priority nested inside milestone/phases objects. Core's `create_plan` calls `create_issue` for each item, which validates priority. Boundary validation of deeply nested objects adds complexity without proportional benefit. Excluded from scope.
- **MCP string ID types** — MCP schemas already declare `"type": "string"` and `"required": ["id"]`. The MCP protocol layer rejects missing or wrong-type values before handlers run. The only gap is empty strings (`""`), which produce reasonable `KeyError → "not_found"` errors from core. Low priority.

## Design Decisions

### 1. Shared validation module: `filigree/validation.py`

New module with pure functions — no MCP, FastAPI, or Click dependencies:

```python
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

This avoids duplicating the Cc/Cf check logic across three boundaries. Each boundary wraps it in its own error format.

**Why reject all Unicode C-categories?** Category Cc covers ASCII control chars (0x00–0x1F, 0x7F) and category Cf covers invisible format characters (BOM `\uFEFF`, zero-width space `\u200B`, RTL override `\u202E`). These cause display manipulation, data corruption, and identity confusion — unacceptable for an audit-trail field.

### 2. Boundary-specific wrappers

**MCP** — add `_validate_actor()` in `mcp_tools/common.py`:
```python
def _validate_actor(value: Any) -> tuple[str, list[TextContent] | None]:
    """Sanitize actor, returning (cleaned, None) or ("", error_response)."""
    cleaned, err = sanitize_actor(value)
    if err:
        return ("", _text({"error": err, "code": "validation_error"}))
    return (cleaned, None)
```

Usage pattern in handlers:
```python
raw_actor = arguments.get("actor", "mcp")
actor, err = _validate_actor(raw_actor)
if err:
    return err
# use cleaned `actor` from here
```

**Dashboard** — add `_validate_actor()` in `dashboard_routes/common.py`:
```python
def _validate_actor(value: Any) -> tuple[str, JSONResponse | None]:
    cleaned, err = sanitize_actor(value)
    if err:
        return ("", _error_response(err, "VALIDATION_ERROR", 400))
    return (cleaned, None)
```

**CLI** — validate in `cli.py` group callback:
```python
cleaned, err = sanitize_actor(actor)
if err:
    raise click.BadParameter(err, param_hint="'--actor'")
ctx.obj["actor"] = cleaned
```

### 3. MCP priority validation

Wire the existing `_validate_int_range` helper into the 5 `issues.py` handlers listed above. The helper is already in `mcp_tools/common.py` — just needs to be imported and called.

For `_handle_list_issues`, validate the priority filter the same way — an out-of-range filter is a caller mistake.

For `_handle_claim_next`, validate both `priority_min` and `priority_max` independently.

### 4. Dashboard priority range check

Note: `dashboard_routes/common.py` already has `_safe_bounded_int` (line 160), but that parses *query-string* values (str → int coercion). JSON body priority needs different handling: no string coercion, bool rejection, and `None` semantics for optional fields. A dedicated `_validate_priority()` is warranted.

Add `_validate_priority()` helper in `dashboard_routes/common.py`:

```python
def _validate_priority(value: Any, *, required: bool = False) -> int | None | JSONResponse:
    """Validate a priority value from JSON body.

    Returns the validated int, None (if optional and absent), or a JSONResponse error.
    """
    if value is None:
        if required:
            return _error_response("priority is required", "VALIDATION_ERROR", 400)
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        return _error_response("priority must be an integer between 0 and 4", "INVALID_PRIORITY", 400)
    if not (0 <= value <= 4):
        return _error_response(f"priority must be between 0 and 4, got {value}", "INVALID_PRIORITY", 400)
    return value
```

Replace the 3 inline `isinstance(priority, int)` checks with calls to this helper.

### 5. CLI priority — `click.IntRange(0, 4)`

Replace `type=int` with `type=click.IntRange(0, 4)` on all 6 priority options. Click auto-generates error messages like "Error: Invalid value for '--priority': 5 is not in the range 0<=x<=4."

### 6. CLI actor validation

Add validation in `cli.py:19–22` group callback, after extracting the `--actor` value. Call `sanitize_actor()`, raise `click.BadParameter` on failure, store cleaned value in `ctx.obj["actor"]`.

## Architecture

```
                    ┌─────────────────────────────────┐
                    │     filigree/validation.py       │
                    │  sanitize_actor() → (str, err?)  │
                    └──────┬──────────────┬────────────┘
                           │              │
          ┌────────────────┴──┐     ┌─────┴───────────────┐
          │  mcp_tools/       │     │  dashboard_routes/  │
          │  common.py        │     │  common.py          │
          │                   │     │                     │
          │ _validate_actor() │     │ _validate_actor()   │
          │   → TextContent?  │     │   → JSONResponse?   │
          │ _validate_int_    │     │ _validate_priority() │
          │   range() [exist] │     │   → JSONResponse?   │
          └────────┬──────────┘     └─────┬───────────────┘
                   │                      │
          ┌────────┴──────────┐     ┌─────┴───────────────┐
          │  issues.py        │     │  issues.py          │
          │  planning.py *    │     └─────────────────────┘
          │  meta.py *        │
          │  files.py [exist] │     * actor validation only
          └───────────────────┘       (no priority params)

          ┌───────────────────┐
          │  cli.py           │
          │  actor validation │
          │                   │
          │  cli_commands/    │
          │  issues.py        │  ← click.IntRange(0,4)
          │  meta.py          │  ← click.IntRange(0,4)
          └───────────────────┘
```

## Error format per boundary

| Boundary | Success | Error |
|----------|---------|-------|
| MCP | `(cleaned, None)` → continue | `("", list[TextContent])` with `{"error": str, "code": "validation_error"}` |
| Dashboard | `(cleaned, None)` → continue | `("", JSONResponse(status_code=400))` |
| CLI | Continue with cleaned value | `click.BadParameter(message)` or `click.IntRange` auto-error |

## Scope

**In scope:**
- `filigree/validation.py` — new module, `sanitize_actor()` pure function
- `mcp_tools/common.py` — add `_validate_actor()` wrapper
- `mcp_tools/issues.py` — wire `_validate_int_range` (5 handlers) + `_validate_actor` (9 handlers)
- `mcp_tools/planning.py` — wire `_validate_actor` (3 handlers)
- `mcp_tools/meta.py` — wire `_validate_actor` (4 handlers)
- `dashboard_routes/common.py` — add `_validate_priority()` + `_validate_actor()` wrappers
- `dashboard_routes/issues.py` — wire priority validation (3 routes) + actor validation (10 routes)
- `cli.py` — actor validation in group callback
- `cli_commands/issues.py` — `click.IntRange(0, 4)` on 5 options
- `cli_commands/meta.py` — `click.IntRange(0, 4)` on 1 option

**Out of scope:**
- Core validation — unchanged, remains authoritative
- `mcp_tools/workflow.py` — no priority or actor params in any handler
- `create_plan` nested priority — core validates via `create_issue` calls
- MCP schema changes — schemas already declare types; this adds runtime enforcement
- New TypedDicts — Task 3 covered response shapes
- `batch_add_label` actor mismatch — handler ignores declared schema param (separate issue)

## Negative test matrix

From issue description + QA reviewer addenda:

### Priority (all 3 boundaries)
| Input | Expected |
|-------|----------|
| `priority=-1` | Validation error |
| `priority=5` | Validation error |
| `priority=0` | Accepted (lower bound) |
| `priority=4` | Accepted (upper bound) |
| `priority=2.5` (MCP/dashboard) | Type error |
| `priority=2**31` (MCP/dashboard) | Validation error (out of range) |
| `priority=None` (MCP/dashboard) | Accepted when optional, error when required |

### Actor name (all 3 boundaries)
| Input | Expected |
|-------|----------|
| `actor=""` | Validation error (empty) |
| `actor="\x00bad"` | Validation error (control chars) |
| `actor="\nbad"` | Validation error (control chars) |
| `actor="\uFEFF"` (BOM) | Validation error (Cf category) |
| `actor="\u200B"` (zero-width space) | Validation error (Cf category) |
| `actor="\u202E"` (RTL override) | Validation error (Cf category) |
| `actor="a" * 129` | Validation error (over max) |
| `actor="a" * 128` | Accepted (at max) |
| `actor="  spaced  "` | Accepted after strip, stored as `"spaced"` |

### Error format verification
- MCP: `{"error": str, "code": "validation_error"}` dict
- Dashboard: `JSONResponse(status_code=400, ...)` with `{"error": {"message": ..., "code": ...}}`
- CLI: Click error message to stderr + non-zero exit code
