# Subsystem Catalog

## 1. Core DB Layer

**Location:** `src/filigree/core.py`, `src/filigree/db_base.py`, `src/filigree/db_*.py`

**Responsibility:** Single source of truth for all SQLite operations — issue CRUD, file records, scan findings, event sourcing, dependency DAGs, workflow templates, and project metadata — via a mixin-composed `FiligreeDB` class.

**Key Components:**
- `core.py` (461 LOC) — `FiligreeDB` class (6-mixin diamond composition), dataclasses (`Issue`, `FileRecord`, `ScanFinding`), convention-based `.filigree/` discovery, config read/write, atomic file writes, built-in pack seeding
- `db_base.py` (39 LOC) — `DBMixinProtocol`: typing.Protocol declaring `conn`, `db_path`, `prefix`, `get_issue()`. All mixins inherit this for mypy type-checking without circular imports
- `db_issues.py` (954 LOC) — `IssuesMixin`: CRUD, batch ops, FTS5 search, optimistic locking for claiming, N+1 elimination via `_build_issues_batch`
- `db_files.py` (1,241 LOC) — `FilesMixin`: file registration, scan result ingestion (dedup, severity-based auto-bug creation), hotspot scoring, merged file timeline
- `db_events.py` (296 LOC) — `EventsMixin`: event recording (INSERT OR IGNORE), `undo_last` (match/case on 9 event types), archival, compaction
- `db_planning.py` (575 LOC) — `PlanningMixin`: dependency DAG (BFS cycle detection), ready/blocked queries, critical path (Kahn's topological sort + longest-path DP), plan tree CRUD
- `db_meta.py` (334 LOC) — `MetaMixin`: comments, labels, stats, bulk insert, JSONL export/import
- `db_schema.py` (281 LOC) — Canonical DDL (10 tables, FTS5 virtual table, sync triggers), `CURRENT_SCHEMA_VERSION = 5`
- `db_workflow.py` (250 LOC) — `WorkflowMixin`: lazy `TemplateRegistry`, template seeding, state/transition validation, category resolution

**Internal Architecture:**

`FiligreeDB` inherits from 6 mixins in MRO order: `FilesMixin, IssuesMixin, EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin`. Each mixin inherits `DBMixinProtocol` for type-safe cross-mixin method calls. Cross-mixin dependencies are declared as TYPE_CHECKING stubs:

```
WorkflowMixin (base provider — no dependencies)
  ← EventsMixin (depends on WorkflowMixin)
  ← MetaMixin (depends on WorkflowMixin, PlanningMixin)
  ← IssuesMixin (depends on EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin)
  ← FilesMixin (depends on IssuesMixin)
  ← PlanningMixin (depends on EventsMixin, WorkflowMixin, IssuesMixin)
```

SQLite configured with WAL mode, foreign keys ON, 5s busy timeout. Schema versioned via `PRAGMA user_version`. Event sourcing records old/new values for all mutations; dedup unique index prevents duplicates.

**Dependencies:**
- Inbound: All interface layers (CLI, MCP, Dashboard), Infrastructure (hooks, summary, analytics, migrations)
- Outbound: Templates (`TemplateRegistry`), Types (TypedDict contracts), Migrations

**Patterns:**
- Mixin composition with Protocol-based type safety
- Lazy SQLite connection with WAL/FK/busy-timeout
- Event sourcing with reversible undo (match/case on 9 event types)
- INSERT OR IGNORE with dedup indexes for idempotent writes
- Batch query N+1 elimination
- FTS5 with LIKE fallback
- Optimistic locking for atomic claim
- Convention-based `.filigree/` discovery

**Concerns:**
- Implicit cross-mixin dependency web (TYPE_CHECKING stubs only) — fragile across refactors
- `db_files.py` at 1,241 LOC is disproportionately large; could split into file-record vs scan-findings sub-mixins
- `process_scan_results` is a ~250-line single method with high cyclomatic complexity
- f-string table name injection in `_generate_unique_id` (safe but pattern-risky)

**Confidence:** High — all 9 files read in full (4,431 LOC), cross-mixin dependencies verified via TYPE_CHECKING stubs

---

## 2. Type System

**Location:** `src/filigree/types/`

**Responsibility:** TypedDict-based return-value contracts and input-argument schemas that form the API boundary between core, MCP, and dashboard — with a critical zero-outbound-dependency constraint preventing circular imports.

**Key Components:**
- `__init__.py` (163 LOC) — Re-export hub: 83 TypedDict names from 6 sub-modules
- `core.py` (85 LOC) — Foundation: `ISOTimestamp` (NewType), `ProjectConfig`, `PaginatedResult`, `IssueDict`, `FileRecordDict`, `ScanFindingDict`
- `api.py` (366 LOC) — 42 MCP/dashboard response TypedDicts: `TransitionDetail`, `SlimIssue`, `ErrorResponse`, flat-inheritance `IssueWith*` extensions, batch/envelope types
- `inputs.py` (380 LOC) — 37 MCP tool argument TypedDicts + `TOOL_ARGS_MAP` registry for automated schema sync testing. Deliberately omits `from __future__ import annotations` for introspection on Python <3.14
- `events.py` (53 LOC) — `EventRecord`, `UndoResult` (discriminated union via `Literal[True/False]`)
- `files.py` (119 LOC) — `FileDetail`, `ScanIngestResult`, `FileHotspot`, `SeverityBreakdown`
- `planning.py` (91 LOC) — `PlanTree`, `FlowMetrics`, `CriticalPathNode`, `DependencyRecord` (functional-form for `"from"` keyword)
- `workflow.py` (85 LOC) — `TemplateInfo`, `TypeListItem`, `FieldSchemaInfo` (split-base pattern)

**Internal Architecture:**

Strict dependency DAG with `core.py` as root. Import constraint: types modules import ONLY from stdlib and each other — never from `core.py`, `db_base.py`, or any mixin. This prevents circular imports since DB mixins import types for return annotations.

Three TypedDict roles:
1. **Dataclass serialization contracts** (`core.py`) — shape of `to_dict()` returns
2. **API response envelopes** (`api.py`, `events.py`, etc.) — MCP/dashboard response shapes
3. **Input argument schemas** (`inputs.py`) — MCP tool argument shapes with `TOOL_ARGS_MAP` sync testing

Notable patterns: split-base for mixed required/optional keys, functional-form for Python keyword keys (`"from"`), discriminated unions with `Literal`.

**Dependencies:**
- Inbound: Core (all mixins), MCP tools, Dashboard routes, Analytics
- Outbound: Python stdlib only (critical architectural invariant)

**Concerns:**
- Import constraint enforced only by comments — no automated linter or test
- `api.py` (42 TypedDicts, 366 LOC) approaching split threshold
- Reserved extension keys for `IssueDict` flat-inheritance documented only in comments

**Confidence:** High — all 7 files read in full (1,342 LOC), import constraint verified via grep

---

## 3. Workflow Templates

**Location:** `src/filigree/templates.py`, `src/filigree/templates_data.py`

**Responsibility:** Define and enforce type-specific workflow state machines, including state transitions, field validation, and pack-level bundling of related issue types.

**Key Components:**
- `templates.py` (823 LOC) — `TemplateRegistry` + frozen dataclass hierarchy: `StateDefinition`, `TransitionDefinition`, `FieldSchema`, `TypeTemplate`, `WorkflowPack`, `TransitionResult`, `TransitionOption`, `ValidationResult`
- `templates_data.py` (1,718 LOC) — Pure data: 9 built-in packs via dict constants (`_CORE_PACK`, `_PLANNING_PACK`, `_RELEASE_PACK`, etc.) exported as `BUILT_IN_PACKS`. Zero logic.

**Internal Architecture:**

Clean data/logic separation: `templates_data.py` is pure data, `templates.py` is pure logic.

Three-layer loading system:
1. Built-in packs from `templates_data.py`
2. Installed packs from `.filigree/packs/*.json`
3. Project-local overrides from `.filigree/templates/*.json` (last-write-wins)

O(1) caches built at registration time: `_category_cache[type]` (state→category) and `_transition_cache[type]` ((from,to)→TransitionDefinition). Hard/soft enforcement: "hard" blocks transitions when required fields are missing; "soft" allows with warnings.

Size limits for DoS prevention: MAX_STATES=50, MAX_TRANSITIONS=200, MAX_FIELDS=50. BFS reachability analysis detects orphaned states.

9 built-in packs: core (task, bug, feature, epic), planning (milestone, phase, step, work_package, deliverable), release (release, release_item), requirements, risk, spike, roadmap, incident, debt. Default enabled: core, planning, release.

**Dependencies:**
- Inbound: Core (TemplateRegistry), WorkflowMixin, IssuesMixin, PlanningMixin, CLI, MCP, Dashboard
- Outbound: None (pure logic + data, stdlib only)

**Concerns:**
- `list.pop(0)` for BFS instead of `collections.deque` (negligible impact at MAX_STATES=50)
- Silent pack mismatch correction could mask data errors
- No validation that `requires_packs` dependencies are actually loaded

**Confidence:** High — both files read in full (2,541 LOC)

---

## 4. CLI

**Location:** `src/filigree/cli.py`, `src/filigree/cli_common.py`, `src/filigree/cli_commands/`

**Responsibility:** Click-based command-line interface organized into 6 domain modules (~56 commands), translating user input into FiligreeDB operations with dual human/JSON output.

**Key Components:**
- `cli.py` (35 LOC) — Entry point: Click group with `--actor`, registers 6 domain modules via `register()` loop
- `cli_common.py` (44 LOC) — `get_db()` (convention-based discovery), `refresh_summary()` (post-mutation hook)
- `cli_commands/issues.py` (458 LOC) — 10 commands: create, show, list, update, close, reopen, claim, claim-next, release, undo
- `cli_commands/planning.py` (269 LOC) — 8 commands: ready, blocked, plan, add-dep, remove-dep, critical-path, create-plan, changes
- `cli_commands/meta.py` (380 LOC) — 11 commands: comments, labels, stats, search, events, batch operations
- `cli_commands/workflow.py` (378 LOC) — 9 commands: templates, types, type-info, transitions, packs, validate, guide, explain-state, workflow-states
- `cli_commands/admin.py` (522 LOC) — 13 commands: init, install, doctor, migrate, dashboard, session-context, metrics, export/import, archive, compact
- `cli_commands/server.py` (129 LOC) — 5 commands (subgroup): start, stop, status, register, unregister

**Internal Architecture:**

Module registration pattern: each domain module exposes `register(cli)` that calls `cli.add_command()`. Flat namespace except `server` (Click subgroup) and `templates` (group with `reload` subcommand).

Actor identity: `--actor` validated via `sanitize_actor()`, stored in `ctx.obj["actor"]`, threaded to all mutations for audit trail.

Lazy imports for heavy optional deps (dashboard, install, migrate, analytics, hooks, server) to keep CLI startup fast. All commands support `--json` for machine-readable output.

**Dependencies:**
- Inbound: `pyproject.toml` entry point
- Outbound: Core, Validation, Summary, Install, Migrate, Dashboard, Analytics, Hooks, Server, Click

**Concerns:**
- Multi-ID `close` exits on first error (inconsistent with `batch-close` which collects per-item errors)
- `workflow-states` accesses private `db._get_states_for_category()`
- Legacy `--design` shortcut inconsistent with generic `--field key=value`
- No shell tab completion

**Confidence:** High — all 11 files read in full (4,757 LOC)

---

## 5. MCP Server

**Location:** `src/filigree/mcp_server.py`, `src/filigree/mcp_tools/`

**Responsibility:** Exposes all filigree operations as 53 MCP tools for agent consumption, supporting both stdio (single-project) and streamable-HTTP (multi-project) transport.

**Key Components:**
- `mcp_server.py` (477 LOC) — Server singleton, state accessors (`_get_db`), ContextVar per-request isolation, tool aggregation, `call_tool()` with timing/logging/rollback safety net, MCP prompt/resource registration, `create_mcp_app()` factory for HTTP transport
- `mcp_tools/common.py` (138 LOC) — `_text()` JSON serializer, `_parse_args()` cast helper, `_resolve_pagination()`, validators, `_build_transition_error()`
- `mcp_tools/issues.py` (702 LOC) — 12 tools: get/list/create/update/close/reopen/search/claim/release/claim_next/batch_close/batch_update
- `mcp_tools/files.py` (559 LOC) — 8 tools: list/get/timeline/issue_files/add_assoc/register/list_scanners/trigger_scan (with 30s cooldown)
- `mcp_tools/meta.py` (531 LOC) — 16 tools: comments, labels, batch, changes, summary, stats, metrics, export/import, archive, compact, undo, events
- `mcp_tools/planning.py` (265 LOC) — 7 tools: deps, ready, blocked, plan, create_plan, critical_path
- `mcp_tools/workflow.py` (381 LOC) — 10 tools: template, states, types, type_info, packs, transitions, validate, guide, explain_state, reload

**Internal Architecture:**

Domain-grouped registration: each module exports `register() -> (tools, handlers)`. Server aggregates at module load. Dual-mode state via ContextVar: stdio uses module globals, HTTP sets per-request ContextVar. ~50 deferred import sites in handler bodies break circular deps.

1 MCP Resource (`filigree://context`) + 1 MCP Prompt (`filigree-workflow`).

Scan cooldown: `(project, scanner, file) -> monotonic timestamp` dict with 30s window and pre-reservation before await points.

**Dependencies:**
- Inbound: Dashboard (mounts via `create_mcp_app()`), CLI (runs via `main()`)
- Outbound: Core, Summary, Scanners, Analytics, Validation, Types, `mcp` SDK

**Concerns:**
- `_parse_args` uses `cast()` with no runtime validation (relies on MCP SDK schema enforcement)
- ~50 deferred import sites invisible to static analysis
- In-memory `_scan_cooldowns` has no upper bound (mitigated by GC on each trigger)

**Confidence:** High — all 7 files read in full (~3,050 LOC)

---

## 6. Dashboard (API)

**Location:** `src/filigree/dashboard.py`, `src/filigree/dashboard_routes/`

**Responsibility:** FastAPI REST API serving the dashboard frontend, supporting single-project ethereal mode and multi-project server mode with per-request DB resolution via ContextVar middleware.

**Key Components:**
- `dashboard.py` (454 LOC) — App factory, `ProjectStore` (lazy DB connections, hot-reload safety), dual-mount for server mode, CORS (localhost only), MCP streamable-HTTP at `/mcp`, static file serving, lifespan management
- `dashboard_routes/common.py` (222 LOC) — Structured error responses `{error: {message, code, details}}`, pagination/validation helpers, graph runtime config
- `dashboard_routes/issues.py` (481 LOC) — 22 issue/workflow/dependency endpoints
- `dashboard_routes/files.py` (317 LOC) — 11 file/scan endpoints including API discovery (`/files/_schema`)
- `dashboard_routes/analytics.py` (457 LOC) — Graph v2 (12 params, BFS neighborhood, critical path, time windowing), stats, metrics, activity
- `dashboard_routes/releases.py` (123 LOC) — Release list (semver sort) + tree view

**Internal Architecture:**

Factory pattern: `create_app()` builds FastAPI app, each domain module exports `create_router()`. In server mode, same router dual-mounted at `/api/p/{key}/` and `/api/` with `ProjectMiddleware` setting ContextVar.

All handlers intentionally async over synchronous SQLite I/O to serialize thread access.

Error format diverges from MCP: nested `{error: {message, code, details}}` vs. flat (documented).

**Dependencies:**
- Inbound: CLI, Frontend
- Outbound: Core, MCP Server (`create_mcp_app`), Analytics, Validation, Server config, Types, FastAPI/uvicorn

**Concerns:**
- Graph v2 loads ALL issues (limit=10,000) into memory for filtering/BFS — problematic for very large projects
- Dashboard and MCP error formats diverge (documented but could confuse consumers)
- Runtime import of `fastapi.HTTPException` inside `_get_db()` on every call

**Confidence:** High — all 6 files read in full (~2,054 LOC)

---

## 7. Dashboard (Frontend)

**Location:** `src/filigree/static/`

**Responsibility:** Single-page application providing interactive project management UI with 8 views: kanban, dependency graph, metrics, activity, workflow, files, health, releases.

**Key Components:**
- `dashboard.html` (541 LOC) — HTML shell: Tailwind CSS theming, CDN scripts (all with SRI hashes), 8 view containers, header/footer, modals
- `js/app.js` (675 LOC) — Entry point: module wiring (late-bound callbacks), keyboard shortcuts, auto-refresh (15s), ~90 `window` exports for inline handlers
- `js/api.js` (337 LOC) — Pure API client (zero DOM): `Promise.allSettled()` for parallel fetches
- `js/state.js` (174 LOC) — Centralized mutable state object (~50 fields), color/icon constants
- `js/router.js` (186 LOC) — View routing, hash-based deep linking
- `js/filters.js` (427 LOC) — Filtering engine with localStorage persistence, presets, debounced search
- `js/ui.js` (511 LOC) — XSS prevention (`escHtml`/`escJsSingle`), tour, toasts, modals, theme toggle
- `js/views/graph.js` (1,223 LOC) — Cytoscape.js dependency graph: dual-mode, dagre layout, critical path, BFS neighborhood, path tracing
- `js/views/kanban.js` (414 LOC) — Kanban with drag-and-drop (transition validation), cluster mode, aging indicators
- `js/views/detail.js` (598 LOC) — Issue detail panel: inline editing, comments, dependency management
- `js/views/files.js` (777 LOC) — File tracking: paginated list, findings management, scan source filtering
- `js/views/releases.js` (679 LOC) — Release roadmap: hierarchical tree, progress rollups
- +4 smaller view modules (metrics, activity, workflow, health)

**Internal Architecture:**

ES modules with single entry point (`app.js`). Late-bound callback pattern breaks circular deps between modules. Centralized mutable `state` object — no framework, no virtual DOM. Full DOM rebuild every 15s refresh cycle. 8 views register loaders via `router.js`.

Multi-project: `state.API_BASE` switches between `/api` and `/api/p/{key}`. Per-project filter persistence in localStorage.

**Dependencies:**
- Inbound: None (leaf node)
- Outbound: Dashboard REST API, CDN libraries (Tailwind 3.4.17, Cytoscape.js 3.30.4, dagre, cytoscape-dagre) with SRI hashes

**Total:** 541 LOC HTML + 6,810 LOC JavaScript = **~7,351 LOC**

**Concerns:**
- ~90 `window`-exposed functions (large global surface area)
- Full DOM rebuild on 15s interval (no diffing — could jank with hundreds of cards)
- Flat mutable `state` object with no encapsulation or change notification
- No bundler/minification — 16 raw ES module HTTP requests
- `graph.js` at 1,223 LOC is the most complex single view

**Confidence:** High — all 16 frontend files read

---

## 8. Infrastructure

**Location:** Distributed across 15 files in `src/filigree/`

**Responsibility:** All non-core operational capabilities: installation/agent integration, schema migration, dashboard lifecycle (ethereal + server modes), scanner orchestration, context generation, flow analytics, validation, and logging.

**Key Components:**
- `install.py` (235 LOC) + `install_support/` (1,086 LOC) — Facade + subpackage: MCP config, CLAUDE.md injection (SHA256-versioned), doctor (13 checks), Claude Code hooks, Codex integration
- `hooks.py` (406 LOC) — SessionStart hook runtime: project snapshot, instruction freshness, dashboard launch
- `ephemeral.py` (291 LOC) — Deterministic port (`8400 + hash(path) % 1000`), PID lifecycle, cross-platform process verification
- `server.py` (366 LOC) — Multi-project daemon: config at `~/.config/filigree/`, portalocker registration, SIGTERM→SIGKILL shutdown
- `scanners.py` (223 LOC) — TOML-configured external scanners, template variable substitution, command validation
- `migrations.py` (532 LOC) — Schema migration framework (4 registered migrations v1→v5), `PRAGMA user_version`, SQLite 12-step rebuild
- `migrate.py` (246 LOC) — One-time "beads" predecessor migration
- `summary.py` (315 LOC) — `context.md` generation: vitals, active plans, ready/blocked/stale, epic progress, critical path
- `analytics.py` (198 LOC) — Flow metrics: cycle time, lead time, throughput (template-aware status category resolution)
- `validation.py` (34 LOC) — `sanitize_actor()` (128-char, no control chars)
- `logging.py` (73 LOC) — JSONL structured logging with rotation (5MB, 3 backups)

**Internal Architecture:**

Three tiers:
1. **Installation** — `install_support/` extracted from monolithic `install.py` (re-export facade). 13 health checks in doctor. Idempotent installation with backup-before-overwrite.
2. **Lifecycle** — Ethereal (session-scoped, PID/port files, portalocker) vs Server (persistent daemon, `server.json`, SIGTERM/SIGKILL). `hooks.py` dispatches to mode-specific functions.
3. **Data/Operations** — Migration framework (`PRAGMA user_version`), summary/analytics (read-only), scanners (TOML + subprocess), logging (JSONL).

**Dependencies:**
- Inbound: CLI, MCP tools, MCP server, Dashboard, Core
- Outbound: Core, DB Schema, Types, portalocker, tomllib

**Concerns:**
- TOCTOU race in port allocation (documented, caller doesn't retry)
- Title sanitization duplicated: `hooks.py` (160 chars) vs `summary.py` (200 chars)
- ISO timestamp parsing duplicated: `analytics.py` vs `summary.py` (different return types)
- Hardcoded timeouts throughout (not configurable)
- `~/.config/filigree/` path not configurable (no env var override)

**Confidence:** High — all 15 files read in full
