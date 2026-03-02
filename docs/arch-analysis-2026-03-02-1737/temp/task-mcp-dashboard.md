## MCP Server

**Location:** `src/filigree/mcp_server.py` + `src/filigree/mcp_tools/`

**Responsibility:** Exposes all filigree operations as MCP (Model Context Protocol) tools for agent consumption, supporting both stdio transport (single-project) and streamable-HTTP transport (multi-project server mode).

**Key Components:**
- `mcp_server.py` (477 LOC) - Server singleton, state accessors (`_get_db`, `_get_filigree_dir`), ContextVar-based per-request DB isolation, tool aggregation loop, MCP prompt/resource registration, `create_mcp_app()` factory for HTTP transport, `call_tool()` dispatch with timing/logging/rollback safety net
- `mcp_tools/__init__.py` (1 LOC) - Package marker, docstring only
- `mcp_tools/common.py` (138 LOC) - Shared utilities: `_text()` JSON serializer, `_parse_args()` type-cast helper, `_resolve_pagination()` with `_MAX_LIST_RESULTS=50` cap, `_validate_actor()`, `_validate_int_range()`, `_validate_str()`, `_build_transition_error()` for structured error hints, `_slim_issue()` for search projections
- `mcp_tools/issues.py` (702 LOC) - 12 tool handlers: get_issue, list_issues, create_issue, update_issue, close_issue, reopen_issue, search_issues, claim_issue, release_claim, claim_next, batch_close, batch_update. Each handler uses `_parse_args` for typed dict casting, validates inputs, delegates to `FiligreeDB`, calls `_refresh_summary()` after mutations, returns structured typed dicts from `types/api.py`
- `mcp_tools/files.py` (559 LOC) - 8 tool handlers: list_files, get_file, get_file_timeline, get_issue_files, add_file_association, register_file, list_scanners, trigger_scan. The `trigger_scan` handler implements per-(project, scanner, file) cooldown with a 30s window, spawns detached scanner processes via `subprocess.Popen`, validates localhost-only API URLs, logs to `.filigree/scans/`, and pre-reserves cooldown before await points to prevent race conditions
- `mcp_tools/meta.py` (531 LOC) - 16 tool handlers: add_comment, get_comments, add_label, remove_label, batch_add_label, batch_add_comment, get_changes, get_summary, get_stats, get_metrics, export_jsonl, import_jsonl, archive_closed, compact_events, undo_last, get_issue_events
- `mcp_tools/planning.py` (265 LOC) - 7 tool handlers: add_dependency, remove_dependency, get_ready, get_blocked, get_plan, create_plan, get_critical_path. Nested priority validation for milestone/phase/step hierarchies
- `mcp_tools/workflow.py` (381 LOC) - 10 tool handlers: get_template, get_workflow_states, list_types, get_type_info, list_packs, get_valid_transitions, validate_issue, get_workflow_guide, explain_state, reload_templates

**Internal Architecture:**

The MCP server uses a **modular domain-grouped registration pattern**. Each domain module (`issues`, `files`, `meta`, `planning`, `workflow`) exports a `register()` function that returns `(list[Tool], dict[str, handler])`. The server aggregates these at module load time via a loop over all domain modules (lines 143-146 of `mcp_server.py`). This avoids a monolithic handler switch and allows domain modules to be developed independently.

**State management** uses a dual-mode accessor pattern:
- **Stdio mode**: Module-level globals `db` and `_filigree_dir` are set once at startup
- **Server mode**: `ContextVar[FiligreeDB | None]` (`_request_db`) and `ContextVar[Path | None]` (`_request_filigree_dir`) are set per-request by the `_handle_mcp` ASGI wrapper
- `_get_db()` resolves: `_request_db.get() or db`, ensuring server-mode isolation without changing handler code

**Tool dispatch** (`call_tool`) includes: timing instrumentation, structured logging, automatic rollback of any uncommitted SQLite transaction in a `finally` block, and unknown-tool error handling.

**Domain modules use deferred imports** to avoid circular dependencies: each handler does `from filigree.mcp_server import _get_db` inside the function body rather than at module level, since `mcp_server.py` imports the domain modules and the domain modules need the state accessors.

The server also exposes:
- **1 MCP Resource** (`filigree://context`) - auto-generated project summary
- **1 MCP Prompt** (`filigree-workflow`) - dynamically built from template registry with optional project context, falls back to static text on DB errors

The `create_mcp_app()` factory returns an ASGI handler and a lifespan context manager for `StreamableHTTPSessionManager`, configured as stateless with no JSON response wrapper.

**Scan cooldown mechanism**: `_scan_cooldowns` is a module-level dict mapping `(project_scope, scanner_name, file_path)` to `time.monotonic()` timestamps. Stale entries are garbage-collected on each trigger. The cooldown is reserved *before* any await points (line 460, with issue reference `filigree-5bee22`) to prevent concurrent bypass.

**Dependencies:**
- Inbound: Dashboard (mounts MCP via `create_mcp_app()`), CLI (runs MCP server via `main()`)
- Outbound: Core (`FiligreeDB`, `find_filigree_root`, `read_config`), Summary (`generate_summary`, `write_summary`), Scanners (`list_scanners`, `load_scanner`, `validate_scanner_command`), Analytics (`get_flow_metrics`), Validation (`sanitize_actor`), Types (`types/api.py`, `types/inputs.py`, `types/core.py`, `types/workflow.py`, `types/planning.py`), `mcp` SDK (`Server`, `stdio_server`, `Tool`, `TextContent`, etc.)

**Patterns Observed:**
- Domain-grouped tool registration with `register() -> (tools, handlers)` convention enables modular development
- Deferred imports in handler bodies to break circular dependency between `mcp_server.py` (globals) and domain modules (handlers)
- ContextVar-based per-request database isolation for multi-project server mode without changing handler signatures
- JSON Schema `inputSchema` on every Tool for MCP SDK input validation before handler invocation
- Consistent structured error responses using typed dicts from `types/api.py` (ErrorResponse, TransitionError, etc.)
- `_refresh_summary()` called after every mutation to keep `context.md` up-to-date, wrapped in best-effort error handling
- Safety-net rollback in `call_tool` finally block for any uncommitted SQLite transactions
- Pre-reservation of cooldown slots before async boundaries to prevent race conditions

**Concerns:**
- The `_scan_cooldowns` dict is an in-memory module-level global with no upper bound on entries; long-running servers scanning many unique files could accumulate entries between GC passes (mitigated by stale-entry cleanup on each trigger, but the window between triggers could grow)
- `common.py` `_parse_args` uses `cast()` for type narrowing with no runtime validation, relying entirely on MCP SDK schema validation and core-layer validation. This is documented but fragile if schemas drift from handler expectations
- Domain modules import `_get_db` and `_refresh_summary` inside every handler function body; while necessary to break circular imports, this creates ~50 deferred import sites that are invisible to static analysis tools

**Confidence:** High - Read 100% of `mcp_server.py`, `common.py`, and all 5 domain modules. Verified tool registration pattern across all modules. Cross-validated imports between `mcp_server.py` globals and domain module usages. Verified scan cooldown mechanism including the race-condition fix reference. Confirmed ContextVar pattern in both `mcp_server.py` and `create_mcp_app()`.


## Dashboard (API)

**Location:** `src/filigree/dashboard.py` + `src/filigree/dashboard_routes/`

**Responsibility:** FastAPI-based web server providing a REST API for the dashboard frontend, supporting both single-project "ethereal mode" and multi-project "server mode" with per-request project resolution via ContextVar middleware.

**Key Components:**
- `dashboard.py` (454 LOC) - App factory (`create_app()`), `ProjectStore` class for multi-project management (lazy DB connections, `reload()` with stale-handle cleanup, prefix-collision detection), module-level `_get_db()` with dual-mode resolution, `_create_project_router()` that assembles domain sub-routers, CORS restricted to localhost, MCP streamable-HTTP mount at `/mcp`, static file serving, root-level endpoints (`/`, `/api/health`, `/api/projects`, `/api/reload`), lifespan management for MCP session manager
- `dashboard_routes/__init__.py` (1 LOC) - Package marker
- `dashboard_routes/common.py` (222 LOC) - Shared helpers: `_error_response()` with structured nested format `{error: {message, code, details}}`, `_parse_json_body()`, `_parse_pagination()`, `_safe_int()`, `_safe_bounded_int()`, `_parse_bool_value()`, `_get_bool_param()`, `_validate_priority()`, `_validate_actor()`, `_parse_csv_param()`, `_coerce_graph_mode()`, `_resolve_graph_runtime()` (reads from env vars + project config), graph-related constants
- `dashboard_routes/issues.py` (481 LOC) - Issue, workflow, and dependency endpoints: GET `/issues`, GET `/issue/{id}` (enriched with dep details, events, comments), GET `/dependencies`, GET `/type/{name}`, GET `/issue/{id}/transitions`, GET `/issue/{id}/files`, GET `/issue/{id}/findings`, PATCH `/issue/{id}`, POST `/issue/{id}/close`, POST `/issue/{id}/reopen`, POST `/issue/{id}/comments`, GET `/search`, GET `/plan/{id}`, POST `/batch/update`, POST `/batch/close`, GET `/types`, POST `/issues`, POST `/issue/{id}/claim`, POST `/issue/{id}/release`, POST `/claim-next`, POST `/issue/{id}/dependencies`, DELETE `/issue/{id}/dependencies/{dep_id}`
- `dashboard_routes/files.py` (317 LOC) - File tracking and scan endpoints: GET `/files`, GET `/files/hotspots`, GET `/files/stats`, GET `/files/_schema` (API discovery), GET `/files/{id}`, GET `/files/{id}/findings`, PATCH `/files/{id}/findings/{fid}`, GET `/files/{id}/timeline`, POST `/files/{id}/associations`, POST `/v1/scan-results`, GET `/scan-runs`. Route order matters: `_schema` registered before `{file_id}` for correct matching
- `dashboard_routes/analytics.py` (457 LOC) - Analytics, graph, and metrics: GET `/config` (graph runtime config), GET `/graph` (dual-mode: legacy flat nodes/edges vs. v2 with filtering, scoping, critical path, BFS neighborhood, time windowing, truncation telemetry), GET `/stats`, GET `/metrics`, GET `/critical-path`, GET `/activity`. The graph v2 endpoint is the most complex with `_GraphV2Params` (12 validated parameters), `_filter_graph_nodes()`, `_filter_graph_edges()`, performance timing
- `dashboard_routes/releases.py` (123 LOC) - Release management: GET `/releases` (with semver sorting, "Future" detection), GET `/release/{id}/tree`. Clean separation of sort-as-UI-concern vs. DB-layer data

**Internal Architecture:**

The dashboard uses a **factory pattern** for both the FastAPI app and domain routers. `create_app(server_mode=False)` builds the entire application, while each domain module (`issues`, `files`, `analytics`, `releases`) exports a `create_router()` function returning an `APIRouter`. The `_create_project_router()` in `dashboard.py` composes these into a single project-scoped router.

**Dual-mount pattern in server mode**: The same project router is mounted at both `/api/p/{project_key}/...` and `/api/...` (for default project). A `ProjectMiddleware` extracts the project key from the URL path and sets a `ContextVar[str]` that `_get_db()` reads.

**`ProjectStore`** manages multi-project state:
- `load()` reads `server.json`, validates prefix uniqueness, skips missing directories
- `get_db(key)` lazily opens `FiligreeDB` connections with `check_same_thread=False`
- `reload()` performs safe hot-reload: retains current state on read failure, closes stale DB handles for removed or path-changed projects
- Fail-fast on corrupt JSON before modifying internal state

**Database resolution** in `_get_db()`:
- Server mode: reads `_current_project_key` ContextVar, falls back to `default_key`, raises 503/404 HTTPException
- Ethereal mode: returns module-level `_db`, raises 500 if uninitialized
- All route handlers use `Depends(_get_db)` for FastAPI dependency injection

**All handlers are intentionally async** despite doing synchronous SQLite I/O. This serializes DB access on the event loop thread, avoiding concurrent multi-thread access to the shared connection. This design decision is documented in comments on each `create_router()`.

**Error response format** differs from MCP: Dashboard uses nested `{error: {message, code, details}}` while MCP uses flat `{error: str, code: str}`. The comment in `common.py` (line 43) explicitly documents this divergence.

**MCP integration**: The app mounts the MCP streamable-HTTP handler at `/mcp`. In server mode, an `_McpProjectWrapper` ASGI class extracts `?project=` query param and sets the ContextVar. The MCP lifespan is managed within the FastAPI lifespan context manager.

**SRI (Subresource Integrity)**: Referenced in commit message `227730e` but enforcement happens in the static HTML file (`dashboard.html`), not in the Python API layer. The dashboard serves `dashboard.html` via `(STATIC_DIR / "dashboard.html").read_text()` with no server-side SRI processing.

**Dependencies:**
- Inbound: CLI (calls `main()`), Frontend (consumes REST API)
- Outbound: Core (`FiligreeDB`, `find_filigree_root`, `read_config`), MCP Server (`create_mcp_app`), Analytics (`get_flow_metrics`), Validation (`sanitize_actor`), Server (`read_server_config`, `SERVER_CONFIG_FILE`), Types (`types/api.py`, `types/core.py`, `types/planning.py`), FastAPI, Starlette, uvicorn

**Patterns Observed:**
- Router factory pattern (`create_router()`) mirrors MCP's `register()` pattern, keeping domain logic in separate modules
- Dual-mount for server mode allows both explicit project addressing and default-project convenience
- ContextVar middleware for per-request state isolation (same pattern as MCP server)
- FastAPI dependency injection (`Depends(_get_db)`) for clean testability
- Structured error responses with consistent `{error: {message, code, details}}` shape
- Intentional async-over-sync for SQLite thread safety (documented pattern)
- Graph v2 endpoint uses BFS-based neighborhood scoping with configurable radius, time-window filtering, and truncation telemetry
- Hot-reload safety: `ProjectStore.reload()` retains state on failure, closes stale handles

**Concerns:**
- The `_get_db()` function in `dashboard.py` does a runtime import of `fastapi.HTTPException` on every call (line 204). While Python caches imports, this is atypical and may confuse developers
- Graph v2 endpoint loads ALL issues (`limit=10000`) into memory for filtering/BFS, which could be problematic for very large projects
- The `_error_response` helper in `common.py` logs every error at WARNING level, which could be noisy for expected validation errors (e.g., user typos in priority values)
- Dashboard and MCP error formats diverge (nested vs. flat) -- documented but could cause confusion for consumers that use both interfaces

**Confidence:** High - Read 100% of `dashboard.py`, `common.py`, and all 4 route modules. Verified dual-mount pattern, ProjectStore lifecycle, ContextVar middleware, MCP integration, and error format divergence. Cross-validated `_get_db()` resolution logic against both ethereal and server mode paths.


## Dashboard (Frontend)

**Location:** `src/filigree/static/`

**Responsibility:** Single-page application providing an interactive project management UI with kanban board, dependency graph, flow metrics, activity feed, workflow visualization, file/scan tracking, code health, and release roadmap views.

**Key Components:**
- `dashboard.html` (541 LOC) - HTML shell with Tailwind CSS custom properties (dark/light theme), CSS-only design system (custom scrollbars, card states, drag-and-drop, responsive breakpoints, animations), header with project switcher + view tabs + filter bar + settings, 8 view containers (graph, kanban, metrics, activity, workflow, files, health, releases), detail panel (slide-in from right), footer stats bar with sparkline canvas, batch action bar, toast container. Loads Tailwind CDN, Cytoscape.js, dagre, cytoscape-dagre all with SRI hashes. Sole script entry point: `<script type="module" src="/static/js/app.js">`
- `js/app.js` (675 LOC) - Entry point and orchestration: imports all modules, wires cross-module callbacks (late-bound pattern to break circular deps), registers 8 views with router, sets up keyboard shortcuts (/, Esc, j/k, Enter, c, x, m, ?, Shift+?), visibility-change auto-refresh, theme initialization, init sequence (load projects, parse hash, restore state), exposes ~90 functions on `window` for inline event handlers, 15s auto-refresh interval, 60s project list refresh
- `js/api.js` (337 LOC) - Pure API client with zero DOM manipulation: all `fetch()` calls to backend REST API, `writeRequest()` generic helper returning `{ok, data?, error?}`, `fetchAllData()` using `Promise.allSettled()` for parallel issue/deps/stats fetching, file/findings/scan-run APIs, error extraction from structured/legacy response formats
- `js/state.js` (174 LOC) - Single source of truth: exported `state` object with ~50 fields (core data, view state, graph instances, filter state, drag-and-drop, multi-project, health/critical path, change tracking, file views), color constants (`CATEGORY_COLORS`, `THEME_COLORS`, `PRIORITY_COLORS`, `TYPE_ICONS`, `TYPE_COLORS`, `SEVERITY_COLORS`), tour steps definition, `REFRESH_INTERVAL = 15000`
- `js/router.js` (186 LOC) - View routing: `registerView()` registry, `switchView()` with DOM class toggling + tab highlighting + skip-link update, `switchKanbanMode()`, `render()` dispatch, hash-based URL state (`#view&project=key&issue=id`), `parseHash()` for deep-link restoration
- `js/filters.js` (427 LOC) - Filtering engine: `getFilteredIssues()` applies status-category/priority/updated-days/search/ready/blocked filters, per-project filter persistence in localStorage, preset save/load system, type-filter with async template loading, debounced search via API, change tracking between refreshes
- `js/ui.js` (511 LOC) - UI utilities: XSS prevention (`escHtml()`, `escJsSingle()`), contextual popovers, onboarding tour system (6 steps with overlay/highlight), toast notifications, batch action modals, settings dropdown, theme toggle (updates CSS vars + JS color objects), issue creation modal with type/priority selection, button loading states, focus management
- `js/views/graph.js` (1223 LOC) - Dependency graph using Cytoscape.js: dual-mode (legacy/v2) rendering, dagre layout, node shapes by type (hexagon=epic, diamond=bug, star=feature), node sizing by priority, edge styling, critical path highlighting, BFS neighborhood scoping, focus mode, path tracing between issues, graph search with prev/next navigation, presets (execution/roadmap), health score computation, impact score calculation, hover effects (downstream highlighting)
- `js/views/kanban.js` (414 LOC) - Kanban board: standard mode (open/wip/done columns), cluster mode (grouped by parent epic with progress bars), type-filtered mode (columns per workflow state), drag-and-drop with transition validation, card rendering with priority colors/type icons/badges (blocked count, blocks count, assignee), aging/stale border indicators, empty-state messaging, `changed-flash` animation for updated cards
- `js/views/detail.js` (598 LOC) - Issue detail panel: full issue display, inline status/priority/title/description editing, claim/release, close/reopen, comment thread, dependency management (add/remove blockers), navigation history with back button, transition loading, file associations display, findings display
- `js/views/metrics.js` (223 LOC) - Flow metrics view: throughput, cycle time, lead time with bar charts, sparkline rendering on canvas, stale issue detection (WIP > 2h with no updates)
- `js/views/files.js` (777 LOC) - File tracking view: paginated file list with sorting/filtering, file detail with tabs (findings, timeline), finding status management (close, link to issue), scan source filtering, severity-based styling
- `js/views/health.js` (259 LOC) - Code health dashboard: hotspot ranking, severity breakdown, scan coverage stats, scan source filtering
- `js/views/activity.js` (82 LOC) - Activity feed: recent events across all issues, timestamped event rendering
- `js/views/workflow.js` (246 LOC) - Workflow state machine diagram using Cytoscape.js: renders type workflow as directed graph, plan/milestone tree view
- `js/views/releases.js` (679 LOC) - Release roadmap: release list with progress rollups, hierarchical tree view (expandable/collapsible), status badges, retry on load failure

**Internal Architecture:**

The frontend has been modularized from an original single-file architecture into **ES modules** loaded via `<script type="module" src="/static/js/app.js">`. The HTML file serves as a pure structural shell with CSS styling, while all behavior is in JavaScript modules.

**Module dependency graph**: `app.js` is the sole entry point that imports from all other modules. The architecture uses a **late-bound callback pattern** to break circular dependencies: modules export a `callbacks` object with null slots, and `app.js` wires these up after all modules are loaded (e.g., `uiCallbacks.fetchData = fetchData`). This allows modules like `ui.js` to trigger data fetches without importing `app.js`.

**State management** is centralized in `state.js` with a single mutable `state` object. All modules import from `state.js` and read/write properties directly. There is no event system or state change notification -- modules re-render by calling `render()` from `router.js` which dispatches to the active view's loader.

**View lifecycle**: Views register a loader function with `router.js` via `registerView(name, loader)`. `switchView()` hides/shows DOM containers, updates tab styling, calls the loader. `render()` re-calls the current view's loader. There is no virtual DOM or diffing -- views rebuild their DOM content on each render.

**Auto-refresh**: A 15-second `setInterval` calls `fetchData()` which hits `/api/issues`, `/api/dependencies`, and `/api/stats` in parallel via `Promise.allSettled()`, updates global state, and triggers a render. `document.visibilitychange` also triggers a refresh when the tab regains focus.

**Inline event handlers**: HTML uses `onclick`, `onchange`, etc. that reference functions on `window`. `app.js` exposes ~90 functions on `window` at the bottom of the file, bridging ES module scope to the global scope needed by inline handlers.

**Multi-project support**: The project switcher sets `state.API_BASE` to either `/api` or `/api/p/{key}`, and all API calls in `api.js` use `state.API_BASE + path`. Project-specific filter settings are persisted in localStorage keyed by project.

**Dependencies:**
- Inbound: None (leaf node -- end-user interface)
- Outbound: Dashboard API (REST endpoints), CDN libraries (Tailwind CSS 3.4.17, Cytoscape.js 3.30.4, dagre 0.8.5, cytoscape-dagre 2.5.0, JetBrains Mono font) all loaded with SRI hashes

**Patterns Observed:**
- ES module architecture with single entry point and late-bound callbacks to avoid circular imports
- Centralized mutable state object (no framework, no virtual DOM)
- Full DOM rebuild on each render cycle (simple but potentially inefficient for large datasets)
- CSS custom properties for theming with light/dark mode, toggled via `data-theme` attribute
- SRI (Subresource Integrity) hashes on all CDN resources for supply-chain security
- Keyboard shortcuts (/, Esc, j/k, Enter, c/x/m, ?) for power-user navigation
- Onboarding tour with overlay/highlight system, persisted completion in localStorage
- XSS prevention: `escHtml()` and `escJsSingle()` used consistently when interpolating user data into HTML
- Hash-based routing for deep-linking (view, project, selected issue)
- Per-project filter persistence in localStorage with validation/normalization
- Change tracking: flashes recently-changed cards with CSS animation
- Drag-and-drop kanban with workflow transition validation before allowing drops
- Responsive design with mobile breakpoints (768px, 1200px) and touch-friendly target sizes

**Concerns:**
- ~90 functions exposed on `window` for inline handlers is a large global surface area; migrating to `addEventListener` in JS would reduce this
- Full DOM rebuild on every 15-second refresh cycle could cause jank on boards with hundreds of cards; no diffing or incremental update strategy
- `state.js` is a flat mutable object with no encapsulation or change notification; any module can mutate any property, making data flow hard to trace
- Graph view loads up to 2000 nodes (configurable) and the graph.js file is 1223 LOC, making it the most complex view and a maintenance concern
- No bundler or minification -- ES modules are served raw from the static directory, which adds HTTP requests (16 files) and has no tree-shaking

**Confidence:** High - Read 100% of `dashboard.html`, `app.js`, `api.js`, `state.js`, `router.js`, `filters.js`, `ui.js`, and verified structure/patterns in all 9 view modules (read all of `graph.js`, `kanban.js`, `detail.js`, `files.js`, `releases.js` fully; verified patterns in `metrics.js`, `activity.js`, `workflow.js`, `health.js`). Cross-validated API client calls against dashboard route endpoints. Verified SRI hashes on all CDN scripts. Total frontend: 541 LOC HTML + 6810 LOC JavaScript = ~7351 LOC.
