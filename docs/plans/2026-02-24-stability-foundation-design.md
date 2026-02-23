# Stability & Foundation — Design

**Epic:** `filigree-d30e05`
**Date:** 2026-02-24
**Prerequisite:** All 30 blocking P1 bugs resolved before work begins.

## Summary

Decompose the 6 roadmap items from the Stability & Foundation epic into 8 tasks across 3 phases, ordered by risk and dependency.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| ID format | 10 hex chars (not Crockford Base32) | Minimal change, >1T IDs, no new encoding function |
| Windows scope | "Make it possible" | Swap fcntl, abstract PID; defer Windows CI |
| Batch error source | Push into core.py | Single source of truth; CLI and MCP consume same rich errors |

## Phase 1: Low-risk isolated fixes

No schema changes, no API surface changes, no new dependencies.

### Task 1 — Fix `list_templates()` missing `required_at` field

- **File:** `core.py:650-662`
- **Change:** Add `required_at` to field schema dict in `list_templates()`, matching the pattern in `get_template()`
- **Size:** ~5 lines

### Task 2 — Fix lossy `undo_last` for claims

- **Files:** `core.py` — `claim_issue()` and `undo_last()`
- **Change:** Capture `old_value=current.assignee` in the `claimed` event; restore from `old_value` in undo instead of clearing to `""`
- **Backward compat:** Existing `claimed` events have `old_value = NULL`. The undo path must null-guard: `restore_to = row["old_value"] if row["old_value"] is not None else ""`. This preserves current behavior for legacy events while enabling proper restoration for new ones.
- **Tests:** Update existing `test_undo.py:72` (which asserts `assignee == ""` for the no-prior-assignee case) and add a new test for the prior-assignee restoration path.
- **Note:** `release_claim` undo is intentionally excluded — `"released"` is not in `_REVERSIBLE_EVENTS` (by design, per comment at `core.py:2154`).
- **Size:** ~10 lines. No migration (events table already has `old_value` column).

### Task 3 — Refactor `SCHEMA_V1_SQL` definition

- **File:** `core.py:328-330`
- **Change:** Replace `SCHEMA_SQL.split("-- ---- File records & scan findings (v2)")` with a standalone SQL string constant.
- **Safety:** Replace the removed runtime guard with a test assertion (`assert SCHEMA_V1_SQL != SCHEMA_SQL`) to catch future staleness.
- **Impact:** Test-only (`test_migrations.py` uses `SCHEMA_V1_SQL`).

## Phase 2: Cross-cutting changes

Touch multiple layers (core, MCP, CLI) or multiple ID generators.

### Task 4 — Enrich batch error reporting in core.py

- **Files:** `core.py` (all 4 batch methods), `mcp_server.py`, `cli.py`
- **Scope:** All 4 batch methods — `batch_close`, `batch_update`, `batch_add_label`, `batch_add_comment`. Note: `batch_add_label` and `batch_add_comment` already emit `"code": "not_found"` and only need the type annotation fix.
- **Changes:**
  1. `core.py`: Add `"code"` field to `batch_close`/`batch_update` error dicts (`"not_found"`, `"invalid_transition"`)
  2. `core.py`: `batch_update` calls `self.get_valid_transitions(issue_id)` on transition failures and adds `"valid_transitions"` hint to error dict
  3. `core.py`: Update return type annotations on all 4 methods from `list[dict[str, str]]` to `list[dict[str, Any]]`
  4. `mcp_server.py`: Remove its own enrichment logic, consume core's rich errors
  5. `cli.py`: Display error codes in batch output
- **Atomicity:** Core enrichment + MCP removal + CLI update must land in a single commit to avoid a window with inconsistent enrichment.
- **Rationale:** Core becomes single source of truth for error semantics.

### Task 5 — Increase ID entropy to 10 hex chars

- **File:** `core.py` — `_generate_id()`, `_generate_file_id()`, `_generate_finding_id()`
- **Changes:**
  1. Change `hex[:6]` to `hex[:10]` in all three generators
  2. Increase collision fallback from 10 to 16 chars (consistently across all three)
  3. Update tests that assert on ID length (notably `test_core.py:219` and `test_files.py:1797`)
  4. Update or remove `_generate_id_standalone()` at `core.py:491-493` (dead code, still at `hex[:6]`)
- **Migration:** None. Existing 6-char IDs remain valid (forward-only change).

## Phase 3: Platform abstraction

Largest surface area. New dependency, multi-file changes.

### Task 6 — Add `portalocker` dependency

- **File:** `pyproject.toml`
- **Change:** Add `portalocker>=2.7,<4` to core dependencies. Use portalocker directly (cross-platform; uses fcntl on Unix, msvcrt on Windows).
- **Rationale for core (not optional):** `server.py` lock paths are required for daemon start, which is part of the base install flow. Cannot be optional.

### Task 7 — Replace `fcntl.flock()` with portalocker

- **Files:** `server.py` (register_project, unregister_project, start_daemon), `hooks.py`
- **Change:** Replace `fcntl.flock()` calls with `portalocker.lock()`/`portalocker.unlock()`
- **Critical: exception handler update.** `hooks.py:302` uses `fcntl.LOCK_EX | fcntl.LOCK_NB` with `except OSError` to gracefully skip when another session holds the lock. portalocker raises `portalocker.LockException` (not an `OSError` subclass). The except clause must be updated to `except (OSError, portalocker.LockException)` or the graceful skip path breaks on Linux — this is a behavioral regression on the current primary platform, not just a Windows concern.
- **`server.py` calls are safe:** All three lock sites use blocking `LOCK_EX` with no exception handler — portalocker's blocking mode is a drop-in replacement.
- **Tests:** Add a test that verifies the non-blocking contention path in `hooks.py` still returns the graceful skip message.
- **Verification:** Existing tests pass on Linux (portalocker delegates to fcntl on Unix).

### Task 8 — Abstract PID verification beyond `/proc`

- **File:** `ephemeral.py` — `verify_pid_ownership()`, `_read_os_command_line()`
- **Current state:** Already has fallback chain (`/proc` -> `ps` -> metadata). Needs review for Windows path.
- **Change:** Evaluate whether to add a `psutil`-based or `wmic`-based path for Windows, or whether the existing `ps` fallback (available in Git Bash/WSL) is sufficient for the "make it possible" scope.

## Dependency graph

```
Phase 1 (independent):    T1 ──┐
                          T2 ──┤
                          T3 ──┤
                                ├── Phase 2:  T4 ──┐
                                │             T5 ──┤
                                │                   ├── Phase 3:  T6 → T7 → T8
```

- Phase 1 tasks are fully independent of each other.
- Phase 2 tasks are independent of each other but logically follow Phase 1.
- Phase 3 is sequential: dependency first (T6), then usage (T7), then verification (T8).

## Ordering rationale

1. **Phase 1 first:** Three quick wins, each < 15 lines, easy to review. Reduces tech debt surface.
2. **Phase 2 middle:** Batch errors touch 3 files across layers; ID entropy touches 3 generators. Benefits from simpler fixes already merged.
3. **Phase 3 last:** Highest surface area (new dependency + 3 files). All other stability work already in, so regressions from platform abstraction are isolated.

## Success criteria

From the epic:
1. All linked P1 bugs resolved or consciously deferred with rationale.
2. Windows: `filigree init` + basic CRUD works without fcntl errors.
3. ID collisions: probability < 1e-6 at 10k issues.
4. CI green on all platforms (Linux; Windows deferred).

## Review record

**Date:** 2026-02-24
**Reviewers:** Architecture, Reality, Quality, Systems
**Round 1 verdict:** CHANGES REQUESTED (3 blocking issues)

Blocking issues resolved in this revision:
- B1 (T7): Added portalocker exception handler update and non-blocking contention test requirement
- B2 (T2): Added NULL `old_value` backward-compat guard and test plan for both legacy/new events
- B3 (T4): Widened scope to all 4 batch methods, specified type annotation update to `dict[str, Any]`, required atomic commit

Additional warnings addressed:
- T3: Replaced prose placeholder with actual marker string; added test assertion replacement for runtime guard
- T5: Added `_generate_id_standalone()` cleanup and specific test files to update
- T6: Added version pin `>=2.7,<4` and rationale for core dependency placement
