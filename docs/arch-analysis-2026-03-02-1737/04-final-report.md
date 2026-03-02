# Architecture Analysis — Final Report

**Project:** Filigree v1.4.0 — Agent-native issue tracker
**Date:** 2026-03-02
**Branch:** `v1.4.0-architectural-refactor`
**Analyst:** Claude Opus 4.6 (4 parallel subagents)

---

## Executive Summary

Filigree is a well-structured ~20K LOC Python application that serves as an issue tracker purpose-built for AI agent workflows. Its architecture follows a **hexagonal (ports & adapters) pattern** — a core domain layer (`FiligreeDB` with 6 mixins) is surrounded by three independent interface adapters (CLI, MCP Server, Dashboard REST API), all sharing the same SQLite database.

The codebase demonstrates strong engineering discipline: strict mypy with typed contracts, 85%+ test coverage, mixin decomposition with Protocol-based type checking, comprehensive workflow state machines, and careful security practices (SRI hashes, XSS prevention, input sanitization, parameterized SQL).

The most notable architectural decision is the **triple-adapter design**: agents use MCP (53 tools), humans use CLI (~56 commands), and the dashboard provides a full SPA (8 views, ~7.3K LOC JS). All three adapters share the same underlying core, ensuring consistency.

### Key Metrics

| Metric | Value |
|--------|-------|
| Total source LOC (Python) | ~19,600 |
| Total frontend LOC (HTML+JS) | ~7,350 |
| Test files | 70+ |
| Subsystems identified | 8 |
| SQLite tables | 10 + FTS5 |
| MCP tools | 53 |
| CLI commands | ~56 |
| Workflow packs | 9 (3 default-enabled) |
| TypedDict contracts | 83 exported |
| Schema version | 5 |

---

## Architecture Strengths

### 1. Clean Layering with Hexagonal Pattern
The core domain (`FiligreeDB`) has zero knowledge of its consumers. CLI, MCP, and Dashboard each translate their protocol-specific inputs into core operations and format outputs for their consumers. This enables adding new interfaces without touching core logic.

### 2. Mixin Composition with Protocol-Based Type Safety
The 6-mixin decomposition keeps each concern manageable (250-1,241 LOC per mixin). `DBMixinProtocol` provides the glue — a `typing.Protocol` that declares shared interface methods, allowing mypy to validate cross-mixin calls without circular imports. This is a sophisticated pattern rarely seen in projects this size.

### 3. Comprehensive Type Contracts
83 TypedDicts across 7 modules form explicit API boundaries. The `TOOL_ARGS_MAP` registry enables automated sync testing between MCP JSON Schemas and TypedDict definitions — a pattern that catches schema drift at CI time. The zero-outbound-import constraint on `types/` prevents the most common Python circular import failure mode.

### 4. Workflow Template System
A full state-machine engine with frozen dataclasses, O(1) lookup caches, hard/soft enforcement levels, BFS reachability analysis, and size limits for DoS prevention. The three-layer loading system (built-in → installed → project-local overrides) provides extensibility without complexity.

### 5. Dual Installation Modes
The ethereal/server mode split serves two real use cases cleanly: developers working on a single project (ephemeral dashboard, stdio MCP) vs. teams managing multiple projects (persistent daemon, HTTP MCP, project routing).

### 6. Security Consciousness
- SRI hashes on all CDN resources
- XSS prevention with `escHtml()`/`escJsSingle()` in frontend
- Parameterized SQL throughout (never string interpolation for values)
- Input sanitization (`sanitize_actor`, title sanitization)
- CORS restricted to localhost
- Scanner command validation (path traversal prevention)

---

## Architecture Concerns

### High Priority

**1. Cross-mixin dependency web is implicit and fragile**
Mixin dependencies are declared only via `TYPE_CHECKING` stubs. There is no automated enforcement — forgetting a stub surfaces only at runtime. As the mixin count grows, this web becomes harder to reason about. Consider either:
- A test that validates cross-mixin method availability
- Moving toward a composition-over-inheritance pattern for new functionality

**2. Types import constraint enforced only by comments**
The critical `types/` zero-outbound-import rule has no automated enforcement. A single careless import could introduce a circular import that passes linting but fails at runtime. Add a CI test that `grep -r "from filigree.core\|from filigree.db_" src/filigree/types/` returns no results.

**3. Graph v2 loads all issues into memory**
`dashboard_routes/analytics.py` loads up to 10,000 issues into memory for graph filtering and BFS neighborhood scoping. For large projects, this creates memory pressure and latency. Consider server-side SQL-based filtering or pagination.

### Medium Priority

**4. `db_files.py` disproportionately large (1,241 LOC)**
This mixin handles file records, scan findings, associations, and timeline — four distinct concerns. Splitting into `db_file_records.py` and `db_scan_findings.py` would improve maintainability.

**5. Error format divergence between MCP and Dashboard**
MCP returns flat `{error: str}`, Dashboard returns nested `{error: {message, code, details}}`. While documented, consumers using both interfaces must handle two error shapes. Consider converging on the richer nested format.

**6. Title sanitization duplicated**
`hooks.py` (160-char limit) and `summary.py` (200-char limit) implement similar-but-different sanitization. Extract to a shared utility in `validation.py`.

**7. Hardcoded timeouts**
Multiple modules use hardcoded timeouts (5s git, 10s claude-mcp-add, 2s daemon reload, 300ms×10 startup poll). These should be configurable for slow CI/containerized environments.

### Low Priority

**8. ~90 window-exposed functions in frontend**
The late-bound callback pattern is sound but the ~90 `window` exports for inline handlers is a large global surface. Migrating to `addEventListener` would reduce this.

**9. No frontend bundler/minification**
16 raw ES module HTTP requests with no tree-shaking. For production deployments this adds latency. A simple esbuild step would address this.

**10. `list.pop(0)` for BFS in template validation**
Minor algorithmic issue — should use `collections.deque`. Impact is negligible at MAX_STATES=50.

---

## Dependency Analysis

### Layer Dependencies (Must-Not-Cross Rules)

```
Interface (CLI, MCP, Dashboard)
    │
    │ depends on (allowed)
    ▼
Core (FiligreeDB, mixins)
    │
    │ depends on (allowed)
    ▼
Templates, Types
    │
    │ depends on (FORBIDDEN — circular)
    ✗
Core, Interface
```

**The critical invariant**: `types/` modules must never import from `core.py` or `db_*.py`. Templates are a leaf dependency (stdlib only). These constraints prevent circular imports.

### External Dependency Footprint

Runtime dependencies are minimal:
- `click` (CLI)
- `mcp` (MCP protocol SDK)
- `portalocker` (file locking)
- `fastapi` + `uvicorn` (optional, dashboard)

No ORM, no Redis, no external message queue. SQLite is the sole data store. This is a deliberate design choice for simplicity and portability.

---

## Evolution and Maintenance Considerations

### Adding a New Feature

A new feature touching issue operations requires changes in:
1. `db_issues.py` (or relevant mixin) — core logic
2. `types/api.py` and/or `types/inputs.py` — type contracts
3. `mcp_tools/issues.py` — MCP tool handler + JSON Schema
4. `dashboard_routes/issues.py` — REST endpoint
5. `cli_commands/issues.py` — CLI command (if applicable)
6. Frontend view module (if applicable)
7. Tests for each layer

This is a consequence of the triple-adapter design — each layer needs its own adapter code. The trade-off is high consistency (all interfaces expose the same operations) at the cost of per-feature surface area.

### Schema Evolution

Schema changes require:
1. New migration function in `migrations.py`
2. Update `CURRENT_SCHEMA_VERSION` in `db_schema.py`
3. Update `SCHEMA_SQL` for fresh databases
4. The migration framework handles FK disable/re-enable and includes template functions as documentation

### Template Evolution

New workflow types or packs:
1. Add pack definition to `templates_data.py`
2. Or create `.filigree/packs/custom.json` for project-local types
3. Enable in `.filigree/config.json` `enabled_packs`
4. No code changes required — the template system is fully data-driven

---

## Subsystem Health Summary

| Subsystem | LOC | Files | Health | Notes |
|-----------|-----|-------|--------|-------|
| Core DB | 4,431 | 9 | Good | Mixin decomposition clean; `db_files` oversized |
| Type System | 1,342 | 7 | Good | Import constraint critical but unenforced |
| Templates | 2,541 | 2 | Excellent | Clean data/logic split, thorough validation |
| CLI | 4,757 | 11 | Good | Well-organized; minor inconsistencies |
| MCP Server | ~3,050 | 7 | Good | 53 tools, clean domain grouping |
| Dashboard API | ~2,054 | 6 | Good | Factory pattern; graph v2 complexity |
| Dashboard Frontend | ~7,351 | 16 | Moderate | Large surface area; no bundler |
| Infrastructure | ~3,900 | 15 | Good | Tiered decomposition; some duplication |

---

## Confidence Statement

This analysis was conducted by 4 parallel subagents, each reading 100% of their assigned source files. Cross-subsystem dependency claims were verified via grep across the full `src/filigree/` tree. All agents reported High confidence with evidence-based reasoning. Total source analyzed: ~29,000 LOC across ~73 files.

**Known gaps:**
- Runtime performance characteristics (no profiling data)
- CI pipeline analysis (GitHub Actions not reviewed)
- Test coverage distribution across subsystems (not measured)
- Production usage patterns (none available)
