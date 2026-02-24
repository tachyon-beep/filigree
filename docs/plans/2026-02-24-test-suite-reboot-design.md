# Test Suite Reboot — Design

**Epic:** TBD
**Date:** 2026-02-24

## Problem Statement

The test suite (35 files, ~22k lines, 1,468 test functions) evolved organically rather than being designed. All 16 source modules have coverage and the 85% line coverage floor is met, but the structure has accumulated debt:

- God classes (TestFileAssociations: 144 methods, TestCLI: 171 methods, TestTemplateRegistry: 163 methods)
- Fixture duplication (type-specific DBs copy-pasted, `db` redefined locally in multiple files)
- Global state mutation in fixtures (MCP and dashboard patch module globals)
- Parametrize underuse (18 instances for 1,468 tests)
- 6 scattered bug-fix test files (`*_fixes.py` pattern)
- No test markers (no unit/integration/slow distinction)

## Goals

1. **Maintainability** — Tests easy to find, modify, and understand
2. **Agent-friendliness** — Clear patterns for where to add tests and which fixtures to use
3. **Speed & targeting** — Markers and directory structure enable running subsets

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Approach | Reorganize and consolidate | Preserve all test logic, no intentional deletion |
| Layout | By source module | One directory per source module, split into sub-files if large |
| Fix files | Absorb into module files | Each regression test moves to the module it tests, with bug ID comment |

## Target Structure

```
tests/
├── conftest.py                  # Shared fixtures (db_factory, cli_runner, populated_db)
├── core/
│   ├── conftest.py              # Core-specific fixtures
│   ├── test_crud.py             # create, get, update, close, reopen
│   ├── test_query.py            # list, search, filters, pagination
│   ├── test_dependencies.py     # add/remove deps, cycle detection, critical path
│   ├── test_batch.py            # batch_close, batch_update, batch_add_label, batch_add_comment
│   ├── test_plans.py            # create_plan, milestones, phases, steps
│   ├── test_undo.py             # undo_last for all event types
│   ├── test_files.py            # file records, findings, associations
│   └── test_schema.py           # SCHEMA_SQL, migrations, v1->v2->v3->v4
├── cli/
│   ├── conftest.py              # cli_in_project fixture
│   ├── test_issue_commands.py   # create, update, close, show
│   ├── test_query_commands.py   # list, search, ready, blocked
│   ├── test_workflow_commands.py # transitions, validate, types, packs
│   └── test_admin_commands.py   # init, install, doctor, plan, batch ops
├── dashboard/
│   ├── conftest.py              # async client fixture (centralized, no scattered global patching)
│   ├── test_api.py              # REST endpoints
│   ├── test_files_api.py        # File/findings endpoints
│   └── test_graph_api.py        # Graph tab endpoints
├── mcp/
│   ├── conftest.py              # MCP-specific fixture (centralized global patching)
│   └── test_tools.py            # All MCP tool contracts
├── templates/
│   ├── conftest.py              # registry fixture
│   ├── test_registry.py         # template loading, validation, packs
│   ├── test_transitions.py      # state machine transitions
│   └── test_workflows.py        # e2e workflow scenarios
├── infrastructure/
│   ├── test_server.py           # daemon lifecycle
│   ├── test_hooks.py            # hook system
│   ├── test_ephemeral.py        # ephemeral mode
│   ├── test_install.py          # install, doctor
│   ├── test_scanners.py         # scanner registration
│   └── test_logging.py          # logging config
├── analytics/
│   └── test_metrics.py          # cycle time, lead time, throughput, summary
└── migration/
    ├── test_schema_upgrade.py   # v1->v2->v3->v4 migrations
    └── test_beads_import.py     # legacy beads migration
```

## Key Changes

### 1. Fixture consolidation

Root `conftest.py` gets a `db_factory` fixture replacing all copy-pasted type-specific DB fixtures:

```python
@pytest.fixture
def db_factory(tmp_path):
    """Factory fixture -- call with prefix to get a fresh DB."""
    created = []
    def _make(prefix="test", packs=None):
        d = FiligreeDB(tmp_path / f"{prefix}.db", prefix=prefix)
        d.initialize()
        if packs:
            for pack in packs:
                d.enable_pack(pack)
        created.append(d)
        return d
    yield _make
    for d in created:
        d.close()
```

The plain `db` fixture stays as a convenience wrapper around `db_factory("test")`.

### 2. Eliminate global state patching

Dashboard and MCP fixtures currently mutate module globals (`dash_module._db = ...`, `mcp_mod.db = ...`). Centralize into per-module `conftest.py` files with clear setup/teardown. Where possible, refactor toward dependency injection.

### 3. Absorb fix files

The 7 regression/fix files get absorbed into relevant module directories:

| Fix file | Destination(s) |
|----------|---------------|
| `test_error_handling_fixes.py` | `dashboard/test_api.py` + `core/test_crud.py` |
| `test_core_logic_fixes.py` | `core/test_crud.py` + `core/test_query.py` |
| `test_template_validation_fixes.py` | `templates/test_registry.py` |
| `test_analytics_templates_fixes.py` | `analytics/test_metrics.py` |
| `test_codex_bug_hunt.py` | relevant module files |
| `test_minor_fixes.py` | relevant module files |
| `test_peripheral_fixes.py` | relevant module files |

Each absorbed test gets a comment: `# Regression: filigree-<id>` for traceability.

### 4. Break up god classes

Classes with >50 methods split into focused classes:

| God class | Current | Target |
|-----------|---------|--------|
| `TestFileAssociations` (144 methods) | `test_files.py` | Multiple classes in `core/test_files.py` |
| `TestCLI` (171 methods) | `test_cli.py` | Split across `cli/test_*.py` |
| `TestTemplateRegistry` (163 methods) | `test_templates.py` | Split across `templates/test_*.py` |

### 5. Add pytest markers

```python
# conftest.py or pyproject.toml
markers = [
    "slow: tests that take >1s (network, subprocess, large data)",
    "integration: tests spanning multiple modules",
]
```

Unmarked tests are implicitly unit tests. No `@pytest.mark.unit` needed.

### 6. Increase parametrize usage

Convert repeated test patterns to `@pytest.mark.parametrize`. Primary candidates:
- Workflow transition tests (same structure, different types)
- Filter/pagination tests (same assertions, different parameters)
- Batch operation tests (same pattern across batch_close/update/label/comment)

## What Stays the Same

- pytest as test runner
- `asyncio_mode = "auto"`
- 85% coverage floor
- Test naming conventions (already good: `test_<action>_<expectation>`)
- All existing test logic preserved

## Phasing

1. **Infrastructure** — Create directory structure, conftest files, `db_factory` fixture
2. **Core module split** — Move core tests into `tests/core/`
3. **CLI split** — Move CLI tests into `tests/cli/`
4. **Dashboard + MCP** — Move tests, centralize global state patching
5. **Templates + workflows** — Consolidate template/workflow/e2e tests
6. **Infrastructure + analytics + migration** — Move remaining files
7. **Absorb fix files** — Merge regression tests into module homes
8. **Markers + parametrize** — Add markers, convert candidates to parametrize
9. **Cleanup** — Remove old files, verify coverage, update CI

## Success Criteria

1. All tests pass after reorganization (zero test loss)
2. Coverage >= 85% maintained
3. No test file >800 lines, no test class >50 methods
4. All fixtures defined in conftest.py files (no local `db` redefinitions)
5. `pytest tests/core/` runs only core tests; `pytest -m "not slow"` skips slow tests
6. New contributors (human or agent) can find where to add a test by module name alone
