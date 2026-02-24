# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.3.0] - 2026-02-24

Server/ethereal operating modes, file intelligence + scanner workflows, Graph v2, and broad safety hardening.

### Added

#### Operating modes and server lifecycle

- `filigree init --mode` and `filigree install --mode` for explicit ethereal/server setup
- Server-mode config and registration system with schema-version enforcement
- Server daemon lifecycle commands and process tracking helpers
- Deterministic port selection and PID lifecycle tracking with atomic writes
- Streamable HTTP MCP endpoint (`/mcp/`) for server mode
- Session context now includes dashboard URL
- Mode-aware doctor checks for ethereal/server installations

#### Files, findings, and scanner platform

- File records and scan findings workflow with metadata timeline events
- Files and Code Health dashboard views (file list/detail/timeline, hotspots, health donut/coverage)
- Split-pane findings workflow and live scan history in dashboard
- Scanner registry loaded from TOML configs in `.filigree/scanners/`
- New MCP tools: `list_scanners` and `trigger_scan`
- Scanner trigger support for `scan_run_id` correlation
- Optional `create_issues` flow for scan ingest to promote findings into candidate `bug` issues and create `bug_in` file associations
- Scan ingest stats extended with `issues_created` and `issue_ids`
- CLI init support for scanner directory creation
- Shared scanner utilities and Claude scanner integration

#### Dashboard graph v2

- Graph v2 shipped with improved focus/path workflows and traversal behavior
- Time-window filter with persisted default
- Progressive-disclosure toolbar with grouped advanced controls
- Improved interaction diagnostics and plain-language status messaging

#### Installation and Codex integration

- `filigree install --codex-skills` to install Codex skills into `.agents/skills/`
- Doctor health check for Codex skills installation state

### Changed

- Dashboard frontend restructured from monolithic HTML script to ES-module architecture
- Dashboard behavior split by mode: ethereal uses simplified single-project flow; server mode uses `ProjectStore` multi-project routing
- API errors standardized, schema discovery surfaced, and instruction generation extracted for reuse
- `filigree server register` and `filigree server unregister` now trigger daemon reload when server mode is already running
- Scanner command validation now resolves project-relative executables (for example `./scanner_exec.sh`) during trigger checks
- Install instruction marker parsing improved to tolerate missing metadata/version fields
- Release workflow pack now enabled by default for all new projects alongside core and planning; `suggested_children` for release type expanded to include epic, milestone, task, bug, and feature
- README/docs expanded with architecture plans, mode guidance, and dashboard visuals

### Fixed

#### Security and correctness

- Dashboard XSS sinks fixed across detail, workflow, kanban, and move-modal surfaces
- File view click-handler escaping fixed for issue IDs containing apostrophes
- All onclick handlers in detail panel, activity feed, and code health views now use `escJsSingle()` for JS string contexts — fixes 6+ XSS injection points where `escHtml()` was misused or escaping was missing entirely
- HTTP MCP request context isolation fixed for per-request DB/project directory selection
- Issue type names now reserved from label taxonomy to prevent collisions
- Duplicate workflow transitions (same `from_state -> to_state`) now rejected at parse and validation time — previously silently accepted with inconsistent dict/tuple behavior
- Enforcement value `"none"` rejected from templates — only `"hard"` and `"soft"` are valid `EnforcementLevel` values
- Release `rolled_back` state recategorized from `done` to `wip` — allows resumption transition to `development`, matching the `incident.resolved` fix pattern

#### Server/daemon reliability

- Multi-project reload and port consistency hardened in server mode
- Reload failures now surface as `RELOAD_FAILED` instead of reporting a false-success response
- `unregister_project` updates locked to prevent concurrent config races
- Daemon ownership checks fixed for `python -m filigree` launch mode
- Portable PID ownership fallback added when command-line process inspection is unavailable
- Registry fallback key-collision handling corrected
- Hook command resolution hardened across installation methods
- `read_server_config()` now validates JSON shape and types: non-dict top-level returns defaults, port coerced to int and clamped to 1–65535, non-dict project entries dropped
- Invalid port values in server config now log at WARNING before falling back to default (previously silent coercion)
- `start_daemon()` serialized with `fcntl.flock` on `server.lock` to prevent concurrent start races
- `start_daemon()` and `daemon_status()` verify PID ownership via `verify_pid_ownership()` — stale PIDs from reused processes no longer cause false "already running" or false status
- `start_daemon()` wraps `subprocess.Popen` in `try/except OSError` to return a clean `DaemonResult` instead of propagating raw exceptions while holding the lock
- `stop_daemon()` verifies process death after SIGKILL and reports failure when the process survives; PID file cleaned up in all terminal paths to prevent permanent stuck state
- `claim_current_process_as_daemon()` now verifies PID ownership before refusing to claim — a reused PID from a non-filigree process no longer blocks the claim
- `stop_daemon()` catches `ProcessLookupError` on SIGTERM when the process dies between the liveness check and the signal delivery
- Off-by-one in `find_available_port()` retry loop — now tries `base + PORT_RETRIES` candidates as documented
- `setup_logging()` now removes and closes stale `RotatingFileHandler`s when `filigree_dir` changes — prevents handler leaks and duplicate log writes in long-lived processes
- Session skill freshness check now covers Codex installs under `.agents/skills/` in addition to `.claude/skills/`

#### Files/findings and scanner robustness

- `_parse_toml()` now distinguishes `OSError` from `TOMLDecodeError` with `exc_info` — unreadable scanner TOML files no longer silently vanish from `list_scanners`
- Scanner paths canonicalized; datetime crash fixed; command templates expanded
- Scan API hardened (`scan_run_id` persistence, suggestion support, severity fallback)
- Findings metadata persistence corrected for create/update ingest paths
- Metadata change detection fixed to compare parsed dictionary values
- `min_findings` now counts all non-terminal finding statuses
- `list_files` filter validation and project-fallback detail-state behavior corrected
- `/api/v1/scan-results` now enforces boolean validation for `create_issues`
- `scan_source` validated as string in `/api/v1/scan-results` — non-string values return 400 instead of crashing
- Pagination `limit` and `offset` enforce minimum values (`limit >= 1`, `offset >= 0`) across all API endpoints — prevents SQLite `LIMIT -1` unbounded queries
- `trigger_scan` cooldown set immediately after rate-limit check (before any await) and rolled back on failure — closes check-then-act race window
- `process_scan_results()` validates `path`, `line_start`/`line_end`, and `suggestion` types upfront with clear error messages instead of crashing in SQL/JSON operations
- `add_file_association` pre-checks issue existence and returns `not_found` instead of misclassifying as `validation_error`

#### Dashboard and analytics quality

- Flow metrics now batch status-event loading to remove N+1 event-query behavior
- Graph toolbar overflow/stacking/disclosure behavior corrected across Graph v2 iterations
- Graph controls hardened for inactive focus/path states and large-graph zoom readability
- Files API sort-direction wiring and stale detail-selection clearing fixed
- Missing split-pane window bindings restored; async loader error handling tightened
- Flow metrics now include `archived` issues so `archive_closed()` results count in throughput
- Analytics SQL queries use deterministic tiebreaker (`id ASC`) for stable cycle-time computation when events share timestamps
- `list_issues` returns empty result when `status_category` expansion yields no matching states, instead of silently dropping the filter
- `import_jsonl` event branch uses shared `conflict` variable and counts via `cursor.rowcount` so `merge=True` accurately reports 0 for skipped duplicates
- Migration atomicity restored for FK-referenced table rebuilds; dashboard startup guard added
- Graph zoom-in no longer jumps aggressively from extreme zoom-out levels — `wheelSensitivity` reduced from Cytoscape default (1.0) to 0.15
- Page title reversed from "[project] — Filigree" to "Filigree — [project]"
- `_read_graph_runtime_config()` failure logging elevated from DEBUG to WARNING
- `api_scan_runs` exception handler narrowed from `Exception` to `sqlite3.Error`
- Tour onboarding text corrected from "5 views" to "7 views" (adds Files and Code Health)

#### CLI

- `import` command catches `OSError` for filesystem errors — clean message instead of traceback
- `claim-next` wraps `db.claim_next()` in `ValueError` handling with JSON/plaintext error output
- `session-context` and `ensure-dashboard` hooks now log at WARNING and emit stderr message on failure instead of swallowing at DEBUG
- `read_config()` catches `JSONDecodeError`/`OSError` — corrupt `config.json` returns defaults with warning instead of cascading crashes
- MCP `_build_workflow_text` fallback now logs at WARNING instead of silently returning stale static text
- MCP `release_claim` tool description corrected: clarifies it clears assignee only (does not change status)
- `_install_mcp_server_mode` prefix-read failure narrowed to `JSONDecodeError`/`OSError` and elevated to WARNING; `_install_mcp_ethereal_mode` logs `claude mcp add` stderr on failure
- Duplicate `_check_same_thread` assignment removed from `FiligreeDB.__init__`

#### Migration

- Priority normalization hardened (`_safe_priority()`) — non-numeric and out-of-range values coerced during migration instead of crashing
- Timestamp normalization added (`_safe_timestamp()`) — NULL/empty timestamps replaced with valid ISO-8601 fallbacks
- `apply_pending_migrations()` guarded against being called inside an existing transaction — raises `RuntimeError` immediately
- Caller's `foreign_keys` PRAGMA setting preserved across migrations instead of unconditionally restoring to ON

### Removed

- Hybrid registration system (`registry.py`) removed in favor of explicit mode-based registration paths
- Checked-in `.mcp.json` removed from version control

## [1.2.0] - 2026-02-21

Multi-project dashboard, UX overhaul, and Deep Teal color theme.

### Added

#### Multi-project support

- Ephemeral project registry (`src/filigree/registry.py`) for discovering local filigree projects
- `ProjectManager` connection pool for serving multiple SQLite databases from a single dashboard instance
- Project switcher dropdown in the dashboard header
- Per-project API routing via FastAPI `APIRouter` — all endpoints scoped to the selected project
- MCP servers self-register with the global registry on startup (best-effort, never fatal)
- `/api/health` endpoint for dashboard process detection

#### Dashboard UX improvements

- Equal-width Kanban columns (`flex: 1 1 0` with `min-width: 280px`) — empty columns no longer shrink
- Drag-and-drop between Kanban columns with transition validation — pre-fetches valid transitions on dragstart, dims invalid targets, optimistic card move with toast confirmation
- Keyboard shortcut `m` opens "Move to..." dropdown as accessible alternative to drag-and-drop
- Type-filter / mode toggle conflict resolved — Standard/Cluster buttons dim when type filter is active, active filter shown as dismissible pill
- WCAG-compliant status badges — open badges use tinted background with higher-contrast text
- P0/P1 text priority labels — critical and high priorities show text badges instead of color-only dots
- Stale badge click shows all stale issues (not just the first)
- Workflow view auto-selects first type on initial load
- Disabled transition buttons show inline `(missing: field)` hints
- Claim modal shows "Not you?" link when pre-filling from localStorage
- Header density reduction — removed duplicate stat spans (footer has the full set)
- Settings gear menu (⚙) in header — replaces standalone theme toggle with a dropdown containing "Reload server" and "Toggle theme"
- `POST /api/reload` endpoint — soft-reloads server state (closes DB connections, re-reads registry, re-registers projects) without process restart

#### Deep Teal color theme

- 20 CSS custom properties on `:root` (dark default) and `[data-theme="light"]` for all surface, border, text, accent, scrollbar, graph, and status colors
- 15 utility classes (`.bg-raised`, `.text-primary`, `.bg-accent`, etc.) for static HTML elements
- `THEME_COLORS` global JS object for Cytoscape graphs (which cannot read CSS custom properties), synced in `toggleTheme()` and theme init
- Dark palette: deep teal surfaces (#0B1215 → #243A45), sky-blue accent (#38BDF8)
- Light palette: teal-tinted whites (#F0F6F8 → #DCE9EE), darker sky accent (#0284C7)
- Theme toggle mechanism changed from `classList.toggle('light')` to `dataset.theme` with CSS `[data-theme="light"]` selector
- All `bg-slate-*`, `text-slate-*`, `border-slate-*` Tailwind classes eliminated from dashboard
- Old `.light` CSS override block (9 lines with `!important`) removed

### Changed

- Dashboard API restructured from flat routes to `APIRouter` with project-scoped prefix
- `CATEGORY_COLORS.wip` updated from `#3B82F6` (blue-500) to `#38BDF8` (sky-400)
- `CATEGORY_COLORS.done` updated from `#9CA3AF` (gray) to `#7B919C` (teal-tinted gray)
- `@keyframes flash` color updated to match accent (`rgba(56,189,248,0.5)`)
- Sparkline stroke color uses `THEME_COLORS.accent` instead of hardcoded blue

### Fixed

- Cytoscape graph and workflow graph colors now update on theme toggle (re-render triggered)
- Graph legend status dots use CSS custom properties instead of hardcoded hex
- Kanban column header dots use `CATEGORY_COLORS` instead of hardcoded hex
- Progress bars in cluster cards and plan view use `CATEGORY_COLORS` instead of hardcoded hex

## [1.1.1] - 2026-02-20

Comprehensive bug-fix and hardening release. 31 bugs resolved across 13 source files,
identified through systematic static analysis and verified against HEAD.

### Added

- Template quality checker (`check_type_template_quality()`) wired into template load pipeline

### Changed

- `_category_cache` uses hierarchical keys matching `_transition_cache` convention
- Core `batch_close()` return type changed from `list[Issue]` to `tuple[list[Issue], list[dict[str, str]]]` matching `batch_update()` pattern

### Fixed

#### Transaction safety

- `create_issue()` and `update_issue()` restructured to validate-then-write with explicit rollback on failure, preventing orphaned rows/events via MCP's long-lived connection
- `reopen_issue()` wrapped in try/except rollback to prevent orphaned events on failure
- MCP `call_tool()` safety net: rolls back any uncommitted transaction after every tool dispatch
- `close_issue()` respects hard-enforcement gates on workflow transitions
- `close_issue()` validates `fields` type before processing

#### Template and workflow validation

- `StateDefinition.category` validated at construction time — invalid categories raise `ValueError`
- Duplicate state names detected at both parse and validation time (defense in depth)
- `enabled_packs` config validated as `list[str]` — strings wrapped, non-lists fall back to defaults
- `parse_type_template()` validates transitions/fields_schema types — raises `ValueError` not raw `TypeError`
- Incident `resolved` state re-categorized from `done` to `wip` — `close_issue()` from resolved now works correctly
- Incident workflow guide: stale `resolved(D)` notation corrected to `resolved(W)` in state diagram

#### Dashboard and API

- Batch endpoints validate `issue_ids` as list of strings — null/missing/non-list values return 400
- Batch close returns per-item `closed`/`errors` instead of fail-fast 404/409
- Claim endpoints reject empty/whitespace assignee with 400
- All sync handlers converted to async to fix concurrency race
- Non-string batch IDs rejected with validation error

#### CLI

- `create-plan` validates milestone/phases types, catches `TypeError`/`AttributeError`
- `create-plan --file` wraps file read in error handling (`OSError`, `UnicodeDecodeError`)
- `import` catches `sqlite3.IntegrityError` for constraint violations
- Backend validation errors properly surfaced in `create-plan` output

#### Install and doctor

- `install_claude_code_mcp()` validates `mcpServers` is a dict before use
- Hook detection handles non-dict/non-list JSON structures throughout `_has_hook_command`
- `install_codex_mcp()` rejects malformed TOML instead of silently appending
- `run_doctor()` uses `finally` block to prevent SQLite connection leaks
- `ensure_dashboard_running()` checks `fastapi`/`uvicorn` imports explicitly
- `ensure_dashboard_running()` polls process after spawn, captures stderr on failure
- Executable path resolution uses `Path.parent / "filigree"` instead of string replacement

#### Analytics

- `cycle_time()` guards done-scan with `start is not None` — no break before WIP found
- `get_flow_metrics()` paginates all closed issues instead of hardcoded 10k cap
- `lead_time()` accepts pre-loaded `Issue` object to avoid N+1 re-fetch

#### Logging

- `setup_logging` guarded by `threading.Lock` to prevent duplicate handlers from concurrent calls
- Handler dedup uses `os.path.abspath()` normalization to handle symlink aliases

#### Migration

- Comment dedup includes `created_at` to preserve legitimate repeated comments
- Zero-value filter removed — numeric `0` preserved in migrated fields
- `rebuild_table()` FK check results read and validated, not silently ignored
- `rebuild_table()` FK fallback hardened with `BEGIN IMMEDIATE`

#### Summary generation

- Parent ID lookup chunked in batches of 500 to avoid SQLite variable limit
- `_sanitize_title()` strips control chars, collapses newlines, truncates — prevents markdown/prompt injection

#### MCP server

- `no_limit=true` pagination uses 10M effective limit and computes `has_more` correctly
- Spike cross-pack spawns direction corrected to match dependency contract

#### Undo safety

- `undo_last()` guards against NULL `old_value` in `priority_changed` events — returns graceful error instead of `TypeError` crash
- `undo_last()` guards against NULL `new_value` in `dependency_added` events — returns graceful error instead of `AttributeError` crash

#### Dashboard (additional)

- `remove_dependency` endpoint now passes `actor="dashboard"` for audit trail consistency
- `update_issue`, `create_issue`, and `batch_update` validate priority is an integer — returns 400 instead of 500 `TypeError`

#### MCP server (additional)

- `batch_close` and `batch_update` validate all IDs are strings before processing
- `batch_update` validates `fields` is a dict (or null) before passing to core

### Known Issues

- `cycle_time()` still executes per-issue events query inside `get_flow_metrics()` loop — lead_time N+1 fixed but cycle_time N+1 remains (tracked as filigree-f34f66)

## [1.1.0] - 2026-02-18

### Added

- Claude Code session hooks — `filigree session-context` injects a project snapshot (in-progress, ready queue, critical path, stats) at session start; `filigree ensure-dashboard` auto-starts the web dashboard
- Workflow skill pack — `filigree-workflow` skill teaches agents triage patterns, sprint planning, dependency management, and multi-agent team coordination via progressive disclosure
- `filigree install --hooks` and `filigree install --skills` for component-level setup
- Doctor checks for hooks and skills installation
- MCP pagination — list/search endpoints cap at 50 results with `has_more` indicator and `no_limit` override
- Codex bug hunt script for per-file static analysis

### Changed

- CI workflow is now reusable via `workflow_call` — release pipeline invokes it instead of duplicating logic
- Release workflow adds post-publish smoke test (installs from PyPI, runs `filigree --version`)
- `github-release` job is idempotent — re-runs fall back to artifact upload instead of failing
- Dependency caching enabled across all CI jobs (`enable-cache`)
- Main branch ruleset now requires lint, typecheck, and test status checks before merge

### Fixed

- Core logic: claim race condition, create_plan rollback, dependency validation
- Analytics: summary, templates, flow metrics bugs
- Error handling: CLI exit codes, MCP validation, dashboard robustness
- Security: migration DDL atomicity, MCP path traversal, release branch guard
- Peripheral modules: migration, install, version robustness
- FTS5 search query sanitization
- File discovery now allows custom exclusion directories
- Batch-size validation and out-of-repo scan root handling
- Dev/internal files excluded from sdist

## [1.0.0] - 2026-02-16

### Added

- First PyPI release — all features from 0.1.0 plus CI/CD pipeline and packaging

## [0.1.0] - 2026-02-15

### Added

- SQLite-backed issue database with WAL mode and convention-based `.filigree/` project discovery
- 43 MCP tools for native AI agent interaction (read, write, claim, batch, workflow, data management)
- Full CLI with 30+ commands, `--json` output for scripting, and `--actor` flag for audit trails
- 24 issue types across 9 workflow packs (core and planning enabled by default):
  - **core**: task, bug, feature, epic
  - **planning**: milestone, phase, step, work_package, deliverable
  - **risk**, **spike**, **requirements**, **roadmap**, **incident**, **debt**, **release**
- Enforced workflow state machines with transition validation and field requirements
- Dependency graph with cycle detection, ready queue, and critical path analysis
- Hierarchical planning (milestone/phase/step) with `create-plan` for bulk hierarchy creation
- Atomic claiming with optimistic locking for multi-agent coordination (`claim`, `claim-next`)
- Pre-computed `context.md` summary regenerated on every mutation for instant agent orientation
- Flow analytics: cycle time, lead time, and throughput metrics
- Comments, labels, and full event audit trail with per-issue and global event queries
- Session resumption via `get_changes --since <timestamp>` for agent downtime recovery
- `filigree install` for automated MCP config, CLAUDE.md injection, and .gitignore setup
- `filigree doctor` health checks with `--fix` for auto-repair
- Web dashboard (`filigree-dashboard`) via FastAPI
- Batch operations (`batch-update`, `batch-close`) with per-item error reporting
- Undo support for reversible actions (`undo`)
- Issue validation against workflow templates (`validate`)
- PEP 561 `py.typed` marker for downstream type checking

[Unreleased]: https://github.com/tachyon-beep/filigree/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/tachyon-beep/filigree/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/tachyon-beep/filigree/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/tachyon-beep/filigree/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/tachyon-beep/filigree/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/tachyon-beep/filigree/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/tachyon-beep/filigree/releases/tag/v0.1.0
