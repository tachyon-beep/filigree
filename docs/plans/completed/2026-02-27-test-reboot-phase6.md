# Test Suite Reboot Phase 6: Install, Hooks, and Remaining Moves

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move install/hooks/server tests into `tests/install/`, consolidate analytics bugfix tests into their target directories, and convert inline `FiligreeDB` construction in hooks tests to use `make_db`.

**Architecture:** Four source files move/merge into existing target directories (`tests/install/`, `tests/analytics/`, `tests/templates/`). The `tests/install/` directory already has `conftest.py` and `__init__.py` from Phase 1. Hooks tests with inline DB construction get converted to the `make_db` factory pattern. No new directories needed.

**Tech Stack:** pytest, filigree.hooks, filigree.install, filigree.server, filigree.analytics, filigree.summary, filigree.templates, tests._db_factory.make_db

---

## Source → Destination Map

### Work Unit A: Install/hooks/server → tests/install/

| Source file | Lines | Tests | Destination |
|-------------|-------|-------|-------------|
| `tests/test_hooks.py` | 651 | 46 | `tests/install/test_hooks.py` |
| `tests/test_install.py` | 1383 | 117 | `tests/install/test_install.py` |
| `tests/test_server.py` | 893 | 58 | `tests/install/test_server.py` |

### Work Unit B: Analytics bugfix consolidation

| Source file | Classes | Destination |
|-------------|---------|-------------|
| `tests/test_analytics_templates_fixes.py` | TestCycleTimeReopen, TestAnalyticsParseIso, TestFlowMetricsDateComparison | `tests/analytics/test_analytics.py` (append) |
| `tests/test_analytics_templates_fixes.py` | TestSummaryTimezoneHandling, TestSummaryWipLimit | `tests/analytics/test_summary.py` (append) |
| `tests/test_analytics_templates_fixes.py` | TestTemplateEnforcementValidation, TestTemplateMalformedShape, TestRolledBackTransition | `tests/templates/test_registry.py` (append) |

### Fixture conversion (within Work Unit A)

11 test methods in `test_hooks.py` construct `FiligreeDB` inline (lines 396-651). These are in classes `TestSessionContextDashboardUrl`, `TestEnsureDashboardEthereal`, and `TestFreshnessCheckLogLevel`. They follow this boilerplate:

```python
filigree_dir = tmp_path / ".filigree"
filigree_dir.mkdir()
config = {"prefix": "test", "version": 1, "mode": "ethereal"}
(filigree_dir / "config.json").write_text(json.dumps(config))
db = FiligreeDB(filigree_dir / DB_FILENAME, prefix="test")
db.initialize()
db.close()
```

These tests need the `.filigree` directory for config files (port files, mode settings) — they test hooks behavior, not DB behavior. They must keep inline construction because they manipulate the `.filigree/` directory directly (writing ephemeral.pid, ephemeral.port, config.json with specific modes). `make_db` cannot be used here since these tests need precise control over the directory layout.

**Decision: No fixture conversion for hooks tests.** The inline construction is intentional — these tests need direct `.filigree/` directory access. Moving them as-is is correct.

---

## Task 1: Move `test_install.py` to `tests/install/`

**Files:**
- Move: `tests/test_install.py` → `tests/install/test_install.py`

**Step 1: Move the file**

```bash
git mv tests/test_install.py tests/install/test_install.py
```

No code changes needed — all fixtures (`db`, `filigree_project`, `cli_runner`) are in root `conftest.py`.

**Step 2: Run the moved file's tests**

Run: `uv run pytest tests/install/test_install.py -v --tb=short`
Expected: All 117 pass

---

## Task 2: Move `test_hooks.py` to `tests/install/`

**Files:**
- Move: `tests/test_hooks.py` → `tests/install/test_hooks.py`

**Step 1: Move the file**

```bash
git mv tests/test_hooks.py tests/install/test_hooks.py
```

No code changes needed — uses `db`, `populated_db`, and `tmp_path` from root conftest/pytest builtins.

**Step 2: Run the moved file's tests**

Run: `uv run pytest tests/install/test_hooks.py -v --tb=short`
Expected: All 46 pass

---

## Task 3: Move `test_server.py` to `tests/install/`

**Files:**
- Move: `tests/test_server.py` → `tests/install/test_server.py`

**Step 1: Move the file**

```bash
git mv tests/test_server.py tests/install/test_server.py
```

No code changes needed — tests use only `tmp_path` and `monkeypatch` (pytest builtins).

**Step 2: Run the moved file's tests**

Run: `uv run pytest tests/install/test_server.py -v --tb=short`
Expected: All 58 pass

---

## Task 4: Consolidate `test_analytics_templates_fixes.py`

This file contains bugfix tests that belong in three different target directories. We split it and append each group to the appropriate existing file.

**Files:**
- Source: `tests/test_analytics_templates_fixes.py`
- Append to: `tests/analytics/test_analytics.py`
- Append to: `tests/analytics/test_summary.py`
- Append to: `tests/templates/test_registry.py`
- Delete: `tests/test_analytics_templates_fixes.py`

**Step 1: Append analytics classes to `tests/analytics/test_analytics.py`**

Append these 3 classes from `test_analytics_templates_fixes.py` to the end of `tests/analytics/test_analytics.py`:
- `TestCycleTimeReopen` (lines 31-51)
- `TestAnalyticsParseIso` (lines 59-91)
- `TestFlowMetricsDateComparison` (lines 98-120)

Add the missing imports to the top of `tests/analytics/test_analytics.py`:
```python
from datetime import UTC, datetime, timedelta
from filigree.analytics import _parse_iso as analytics_parse_iso
```

Note: `cycle_time`, `get_flow_metrics`, `FiligreeDB` are already imported.

**Step 2: Append summary classes to `tests/analytics/test_summary.py`**

Append these 2 classes from `test_analytics_templates_fixes.py` to the end of `tests/analytics/test_summary.py`:
- `TestSummaryTimezoneHandling` (lines 128-159)
- `TestSummaryWipLimit` (lines 167-183)

Add the missing imports to the top of `tests/analytics/test_summary.py`:
```python
from unittest.mock import patch
from filigree.summary import _parse_iso as summary_parse_iso
```

Note: `generate_summary`, `FiligreeDB`, `datetime`, `UTC`, `timedelta` are already imported.

**Step 3: Append template classes to `tests/templates/test_registry.py`**

Append these 3 classes from `test_analytics_templates_fixes.py` to the end of `tests/templates/test_registry.py`:
- `TestTemplateEnforcementValidation` (lines 191-233)
- `TestTemplateMalformedShape` (lines 236-321)
- `TestRolledBackTransition` (lines 329-358)

No new imports needed — `TemplateRegistry`, `BUILT_IN_PACKS`, `json`, `Path`, and `pytest` are already imported in `test_registry.py`.

**Step 4: Run each target file**

Run: `uv run pytest tests/analytics/test_analytics.py tests/analytics/test_summary.py tests/templates/test_registry.py -v --tb=short`
Expected: All pass (existing + newly appended)

**Step 5: Delete the source file**

```bash
git rm tests/test_analytics_templates_fixes.py
```

---

## Task 5: Delete source files and verify

**Step 1: Run full test suite**

Run: `uv run pytest --tb=short`
Expected: All pass, same test count as baseline (no tests lost or duplicated)

**Step 2: Commit**

```bash
git add tests/install/ tests/analytics/ tests/templates/test_registry.py tests/test_install.py tests/test_hooks.py tests/test_server.py tests/test_analytics_templates_fixes.py
git commit -m "refactor: test suite reboot Phase 6 — install, hooks, server, and bugfix consolidation"
```

---

## Task 6: Close filigree issues

**Step 1: Close Phase 6 step issues**

- `filigree-b90406` — Move infrastructure tests (covered by Tasks 1-3 — install, hooks, server moved)
- `filigree-0f1184207f` — Move test_install.py and test_hooks.py to tests/install/ (covered by Tasks 1-2)
- `filigree-4cc46f` — Move analytics and migration tests (covered by Task 4 — analytics bugfixes consolidated; migration was already moved in Phase 5)

**Step 2: Close Phase 6**

- `filigree-1f622d` — Phase 6: Install, hooks, and remaining moves

---

## Pre-flight checks

Before starting, capture baseline test count:
```bash
uv run pytest --co -q | tail -1
```

After Task 5, the count must match exactly.

---

## Notes

**Why no `tests/infrastructure/` directory?** The original step description mentioned an `infrastructure/` target, but `tests/install/` already exists from Phase 1 with a conftest.py and __init__.py. Install, hooks, and server tests all relate to filigree setup/infrastructure, so `tests/install/` is the natural home. Creating a separate `infrastructure/` directory would scatter related tests.

**Why no hooks fixture conversion?** The 11 inline `FiligreeDB` constructions in hooks tests are intentional — those tests manipulate `.filigree/` directory contents directly (config.json with specific modes, ephemeral.pid, ephemeral.port). They need precise directory layout control that `make_db` doesn't provide. Moving them as-is preserves correctness.

**Step description discrepancies:** Several files mentioned in the filigree step descriptions no longer exist (test_ephemeral.py, test_scanners.py, test_logging.py, test_analytics.py, test_summary.py, test_migrations.py) — they were moved or never existed. This plan reflects the actual current state.
