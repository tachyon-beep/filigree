# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] - Unreleased

### Added

- **`.filigree.conf`** — JSON anchor file at the project root. The authoritative discovery target: walk-up looks for this file (not the `.filigree/` directory). Nested `.filigree.conf` files override their parents — first hit wins. Carries `version`, `project_name`, `prefix`, and `db` (path to the database, relative to the conf file).
- **`FiligreeDB.from_conf(conf_path)`** classmethod — open a project DB by its conf anchor.
- **`WrongProjectError`** (`ValueError` subclass) — raised on write operations against IDs whose prefix doesn't match the open DB's prefix. Catches an agent that climbed into a parent's database and tries to mutate a foreign-prefix ticket. Read methods (`get_issue`, `get_comments`, etc.) intentionally do not enforce, so legitimate cross-prefix lookups (migration, history) still work.
- **`ProjectNotInitialisedError`** (`FileNotFoundError` subclass) — raised when no `.filigree.conf` is found anywhere up to `/`. Error message points at `filigree init` and `filigree doctor`.
- **`filigree doctor`** flags `~/.filigree.conf` if present (a conf at `$HOME` claims everything beneath it; almost always a mistake) and reports whether the project's `.filigree.conf` anchor exists.

### Changed

- **`filigree init`** writes `.filigree.conf` alongside `.filigree/`.
- **Discovery** is split: `find_filigree_conf` is strict (returns the conf path or raises) and `find_filigree_anchor` walks up for either a `.filigree.conf` or a legacy `.filigree/` directory, returning `(project_root, conf_path_or_None)`. Both are pure reads — discovery never writes. Legacy installs are still discoverable; the conf is created only by explicit init/install paths so inspection commands work on read-only mounts.
- `find_filigree_root` continues to return the literal `.filigree/` directory next to the project anchor, regardless of any custom `db` location declared in the conf.
- `FiligreeDB.from_project` now resolves via `find_filigree_anchor`, falling back to `from_filigree_dir` for legacy installs.
- Error messages for "project not initialised" now point at `filigree init` and `filigree doctor` explicitly.

### Fixed

- **filigree-7840eae0bd**: agents in a directory with no `.filigree/` would silently walk up into a parent's `.filigree/` and write tickets into the wrong DB. Mitigated by the explicit `.filigree.conf` claim model plus the `WrongProjectError` write guard.
- `WrongProjectError` no longer rejects legitimate IDs from projects whose prefix contains a hyphen. The check is now anchored on `startswith(prefix + "-")` instead of splitting the ID on the first `-` (which broke any project initialised with a hyphenated `cwd.name`, e.g. `my-app/` generating IDs like `my-app-abc1234567`).
- Project discovery no longer writes during the walk-up. Previously a legacy install discovered via `find_filigree_conf` triggered a `.filigree.conf` backfill, causing `PermissionError` for inspection-only commands (`filigree list`, `filigree doctor`, MCP startup) on read-only checkouts.
- `find_filigree_root` no longer misroutes callers when the conf's `db` field points outside `.filigree/`. It now returns the project's `.filigree/` directory directly, so `mcp_server`, `install`, `dashboard`, `hooks`, and the summary writers operate against the correct database and filesystem location.
- **filigree-fe8956fb16**: `compact_events` no longer accepts a negative `keep_recent` and silently wipes all archived event history. The core method now raises `ValueError`, the MCP tool schema enforces `minimum: 0`, and the MCP handler validates the argument before dispatch. Defense-in-depth now matches the existing CLI guard.
- **filigree-33a938b515**: concurrent MCP tool invocations no longer corrupt each other. The MCP SDK dispatches tool calls concurrently (`tg.start_soon` per request) and `FiligreeDB` caches a single `sqlite3.Connection` — a failing mutation's `finally`-block rollback could erase a sibling coroutine's uncommitted writes on the shared connection. `call_tool` now acquires a per-`FiligreeDB` `asyncio.Lock` around handler execution and the safety-net rollback, serialising tool calls against the shared connection.
- **filigree-78903e4ff7**: MCP `register_file` with `path="."` (project root) no longer escapes as an uncaught `ValueError`. The handler now catches the normalization failure and returns a clean `invalid_path` error response, matching the existing traversal-rejection contract.
- **filigree-0911b35955**: scan ingestion with `path="."` no longer silently persists a `file_records` row with an empty path. `_validate_scan_findings` now re-checks the normalized path and raises `ValueError` with the per-finding index, symmetric with `register_file`'s post-normalization guard.
- **filigree-fda0e2a340**: `FiligreeDB.from_filigree_dir` no longer adopts a hardcoded `prefix="filigree"` when `config.json` is missing or lacks an explicit `prefix` key. It now falls back to the project directory's own name — matching `filigree init`'s default — so a legacy install whose config was deleted or never fully written doesn't silently open with the wrong identity and reject every write to its own issues.
- **filigree-bac0797445**: `import_jsonl` now fails fast when the JSONL file references issue IDs whose prefix doesn't match the destination DB. Previously imports preserved source IDs verbatim, creating rows that could be read but never mutated — every guarded write path raised `WrongProjectError` on them. Migration tools that deliberately need to preserve foreign IDs can opt in via `import_jsonl(..., allow_foreign_ids=True)` (or `filigree import --allow-foreign-ids`).
- **filigree-f863b9d1f8**: `filigree dashboard --server-mode` no longer overwrites the configured daemon port in `server.json` when the caller omits `--port`. The Click option now defaults to `None`, and server mode resolves `--port or read_server_config().port` before invoking `dashboard_main`. Omitting `--port` leaves the persisted config alone; passing one still updates it.
- **filigree-ceb2da2411**: `filigree dashboard --server-mode` now refuses to start when `claim_current_process_as_daemon()` reports a different live daemon is already tracked. Previously the return value was silently discarded and a second server process raced the tracked one for the daemon port.
- **filigree-563d5454e9**: `verify_pid_ownership` now distinguishes this project's dashboard from another filigree project's after PID recycling. `write_pid_file` embeds the dashboard port in the record; `verify_pid_ownership` requires that `--port <N>` appear in the live process argv when a port is recorded. Cross-project PID collisions no longer misidentify a foreign dashboard as our own, preventing `restart_dashboard` from sending SIGTERM to the wrong process.
- **filigree-73e909e6cc**: `cleanup_stale_pid` no longer unlinks a freshly written PID file under TOCTOU. The stale record is now moved aside with an atomic rename, re-verified from quarantine, and either committed (unlinked) or restored if a concurrent writer re-populated it during the check.
- **filigree-ea2a1959e1**: `ensure_dashboard_running` no longer spawns a second dashboard when a hook fires during startup. `write_pid_file` now records a `startup_ts`; when the recorded PID is alive, ours, and the port isn't yet listening but startup is within a 30-second grace window, the hook reports "initializing" instead of respawning.
- **filigree-bff063de18**: Repeated in-process `filigree.dashboard.main()` calls no longer serve the wrong database. The `_db` / `_project_store` module globals are cleared on both entry and exit, so a subsequent call in the opposite mode routes through the correct resolver instead of inheriting stale state from the previous run.

## [1.6.1] - 2026-04-01

### Fixed

- `filigree doctor` no longer reports a false "duplicate install" warning when running from a uv tool venv whose Python is symlinked to a uv-managed interpreter outside the venv

## [1.6.0] - 2026-03-30

### Changed

- Codex MCP install now always writes global stdio config with runtime project autodiscovery instead of project-pinned `--project` args or URL-based routing
- Claude Code stdio MCP install now also uses runtime autodiscovery (`args = []`) so folder switches do not leave stale project targets behind
- Installation and migration docs now describe autodiscovery-based MCP wiring and correct the remaining MCP tool-count references to 71

### Fixed

- `filigree doctor` now rejects deprecated Codex URL routing and stale project-pinned Codex config with a clearer remediation message
- Server-mode Codex installs no longer write daemon URLs that can misroute writes across workspaces

### Tests

- Updated install, doctor, and CLI-admin coverage for autodiscovery-based Claude Code and Codex MCP config

## [1.5.2] - 2026-03-23

### Fixed

- **README accuracy** — MCP tool count corrected from 53 to 71; ruff line-length corrected from 120 to 140
- **Accessibility** — added `aria-label` attributes to `role="button"` elements in dashboard detail panel (blocker links, downstream links, file links)
- **XSS defense** — tour tooltip text now escaped via `escHtml()` (was safe from constants, now safe by construction)
- **CLI help text** — `reopen` command clarifies it returns issues to their type's initial state, not previous state
- Ruff formatting applied to 5 source files that had drifted

### Tests

- **New `tests/test_dashboard.py`** — 25 tests covering `ProjectStore` init/load/corruption, idle watchdog, idle tracking middleware, `_get_db` error paths, ethereal vs server mode app creation
- **New `tests/test_doctor.py`** — 70 tests covering `CheckResult`, `_is_venv_binary`, `_is_absolute_command_path`, config/DB/context/gitignore/MCP/hooks/skills/instruction file checks
- **Expanded `tests/api/test_scanner_tools.py`** — 36 new tests (was 2) covering scan run CRUD, status transitions, cooldown logic, batch runs, log tailing, edge cases

## [1.5.1] - 2026-03-18

### Added

- **Label taxonomy system** — namespace reservation, virtual labels (`age:fresh`, `age:stale`, `has:findings`, `has:plan`, `has:dependencies`), array labels, prefix search (`--label-prefix=cluster/`), and not-label exclusion in `list_issues`
- MCP tools for label discovery: `list_labels` and `get_label_taxonomy`
- CLI commands: `filigree labels`, `filigree taxonomy`, `--label-prefix`, `--not-label`, repeatable `--label` on `list`
- Mutual exclusivity enforcement for `review:` namespace labels
- **Scanner lifecycle tracking** — `scan_runs` table with schema v7→v8 migration, `ScansMixin` with CRUD, cooldown checks, and status transitions
- **Finding triage tools** — `get_finding`, `list_findings` (global), `update_finding` (file_id optional), `dismiss_finding`, `promote_finding`, `batch_update_findings` MCP tools
- **Scanner module extraction** — new `mcp_tools/scanners.py` with `trigger_scan_batch`, `get_scan_status`, `preview_scan`; DB-persisted cooldown replaces in-memory dict
- **Shared scanner pipeline** — `run_scanner_pipeline()` in `scripts/scan_utils.py` with argparse integration, batch orchestration, and API completion logic; slimmed `claude_bug_hunt.py` and `codex_bug_hunt.py`
- Scanner config file: `.filigree/scanners/claude-code.toml`

### Changed

- **Breaking (API):** `POST /api/v1/scan-results` response replaces `issues_created`/`issue_ids` with `observations_created` count. The `create_issues` parameter is replaced by `create_observations`.
- **Breaking:** `update_finding` signature changed — `file_id` is now keyword-only and optional
- `process_scan_results` replaces `create_issues` with `create_observations` for lightweight triage
- Narrowed `except Exception` to specific exception types in scanner MCP handlers to avoid masking programming errors as DB failures
- `batch_update_findings` response now includes `"partial": true` flag when some updates succeed and others fail
- `ScanIngestResult` now tracks `observations_failed` count and reports per-finding failure messages
- Batch scan data warning now distinguishes files from processes
- `process_scan_results` terminal-state detection uses direct DB query instead of brittle string matching

### Fixed

- `batch_update_findings` now logs individual failure warnings server-side (previously only in MCP response)
- `promote_finding_to_observation` surfaces a note when file record is missing instead of silently losing context
- `process_scan_results` docstring corrected: `severity` is optional (defaults to `"info"`), `suggestion` added to optional fields
- `_handle_get_scan_status`, `_handle_dismiss_finding`, `_handle_list_labels`, and `_handle_get_label_taxonomy` now catch `sqlite3.Error` instead of returning raw exception traces
- Scanner batch file report read wrapped in try/except so one corrupt file no longer kills the entire batch
- Scan-run completion POST failure now counted in `api_failures` for correct exit code
- Fragile parallel-list index coupling in batch scan replaced with `zip(..., strict=True)`
- Unused variable lint violation in test_scans.py

### Tests

- 6 new test files: `test_scans.py`, `test_finding_triage.py`, `test_label_discovery.py`, `test_label_query.py`, `test_scanner_lifecycle_tools.py`, `test_finding_triage_tools.py`
- Test for breaking `create_issues` → `create_observations` parameter rename
- Test for `update_finding` with mismatched `file_id` raises `KeyError`
- Parametrized severity-to-priority mapping tests for all 5 severity levels
- Security boundary tests: path traversal, non-localhost URL rejection, reserved namespace enforcement

## [1.5.0] - 2026-03-09

### Added

- **Observations subsystem** — fire-and-forget agent scratchpad with TTL expiry, audit trail, atomic promote-to-issue, and file anchoring (schema v6→v7 migration)
- MCP tools for observations: `observe`, `list_observations`, `dismiss_observation`, `promote_observation`
- Observation awareness in session context, project summary, and MCP prompt
- Observation triage workflow with promote-to-type selection and requirements pack support
- Dashboard observation stats on Insights page and observation counts in Files table and detail panel
- Kanban List mode with sortable table view
- Scoped subtree explorer replacing the standalone Graph tab — sidebar-driven, renders parent-child hierarchy edges

### Changed

- Consolidated dashboard from 7 tabs to 5: Activity merged into Insights, Health merged into Files as collapsible Code Quality Overview, Workflow demoted to Settings modal
- Redesigned header filter bar with status pills, Done time-bound dropdown, and cleaner layout
- Decomposed `process_scan_results` monolith into focused helpers with table-driven `export_jsonl`
- Simplified TypedDict patterns using `PlanResponse` inheritance and `NotRequired`
- **Breaking (MCP):** `get_valid_transitions` and `get_issue` `missing_fields` now returns bare field name strings instead of full schema objects — consumers expecting `{name, type, description}` dicts must update to plain `list[str]`
- Threaded `Severity`/`FindingStatus`/`AssocType` Literal types through API signatures

### Fixed

- Codex MCP install and doctor now validate the config Codex actually uses (`~/.codex/config.toml`), rewrite stale `filigree` entries that still target another project, and support server-mode MCP URL installs
- Restored schema `v6` compatibility for historical databases by reinstating the missing `v5 -> v6` migration for the `issues.parent_id` self-foreign-key, including FTS rebuild handling after the table swap
- JSONL export/import now round-trips the file subsystem (`file_records`, `scan_findings`, `file_associations`, `file_events`), reconciles the seeded `Future` release singleton on restore, and makes `merge=True` idempotent for imported comments and file history rows
- Ethereal/server lifecycle helpers now degrade cleanly under restricted socket permissions, treat `PermissionError` liveness checks as live processes, and verify PID ownership against the expected dashboard command shape before reusing or stopping processes
- Older Filigree binaries now refuse to open databases with a newer schema version instead of silently attempting an unsupported downgrade path
- Dashboard issue creation now preserves custom `fields`, so release/version metadata and other template-backed values survive `POST /api/issues`
- CLI, dashboard, hooks, and MCP project openers now honor configured `enabled_packs` instead of silently falling back to the default pack set
- File lookups by path now normalize equivalent path spellings on read, matching the write-time identity rules used by scan ingestion and file registration
- Transaction safety hardening: rollback guards on promote/close, savepoint leak fixes, undo race conditions, and phantom write prevention
- Template engine hardening: reverse-reachability BFS validation, crash-on-anomaly for category cache, rejection of unknown types in transitions and initial state lookups. `get_mode` raises `ValueError` for unknown modes (all callers already handle this). `get_initial_state` raises `ValueError` for unknown types (callers guard upstream or propagate correctly). `list_issues` raises `ValueError` for negative limit/offset (API schema prevents negative values at boundary).
- TOCTOU race fixes in PID ownership and cleanup, unchecked return codes in OS command reads
- Numerous type-safety fixes: generic `PaginatedResult`, typed observations and planning responses, `EventType` Literal enforcement at SQL boundary
- CLI runtime fixes: partial-failure data loss prevention, correct exit codes, and `--json` support for all commands
- Issue creation/update now reject non-dict `fields` inputs with a stable validation error instead of crashing with an internal `AttributeError`
- Dashboard issue create/update and batch update endpoints now translate invalid non-dict `fields` payloads into `400 VALIDATION_ERROR` responses instead of leaking `500` errors
- Dashboard filter composability: type filter and cluster mode now work together correctly

### Tests

- Shape contract tests for 14 MCP handler response TypedDicts
- 42 new tests for previously untested error paths and edge cases
- DB core test gap closure for transactions, cycle detection, and import paths

## [1.4.1] - 2026-03-03

### Changed

- Dashboard (`fastapi`, `uvicorn`) is now part of core dependencies — no more `filigree[dashboard]` extra required

### Fixed

- `filigree init` on existing installs now reports schema migrations ("Schema upgraded v1 → v5") instead of silently applying them
- `filigree doctor --fix` can now auto-repair outdated database schemas (was missing from the fixable check map)
- Dashboard broken by Tailwind CSS CDN SRI integrity hash mismatch — removed incompatible SRI attribute from dynamic CDN resource

## [1.4.0] - 2026-03-01

Architectural refactor: decompose monolithic modules into domain-specific subpackages, add type safety with TypedDicts, boundary validation, releases tracking, and comprehensive test restructuring.

### Added

#### Workflow

- `not_a_bug` done-state for bug workflow — distinct from `wont_fix` for triage rejections (transitions from `triage` and `confirmed`)
- `retired` state added to release workflow with quality-check refinements

#### Dashboard UX

- Click-to-copy on issue IDs in kanban cards and detail panel header (hover underline, toast feedback, keyboard accessible)
- "Updated in last X days" dropdown filter in the main issue toolbar (1d, 7d, 14d, 30d, 90d) — persisted with other filter settings
- Sticky headers for metrics, activity, files, and health views (header stays visible while content scrolls)

#### Configuration

- `name` field in `ProjectConfig` / `.filigree/config.json` — separates human-readable project name from the technical ID prefix
- `filigree init --name` option to set display name independently of `--prefix`
- Dashboard title and server-mode project list now use `name` with fallback to `prefix`

### Changed

#### Architecture (v1.4.0 refactor)

- `FiligreeDB` decomposed into domain mixins: `EventsMixin`, `WorkflowMixin`, `MetaMixin`, `PlanningMixin`, `IssuesMixin`, `FilesMixin` — each in its own module under `src/filigree/`
- `DBMixinProtocol` wired into all mixins, eliminating 33 `type: ignore` comments
- CLI commands split from monolithic `cli.py` into `cli_commands/` subpackage
- MCP tools split into domain modules
- Dashboard routes split into `dashboard_routes/` subpackage
- `install.py` split into `install_support/` subpackage

#### Documentation

- Plugin system & language packs design document added with 8-specialist review consensus
- ADR-001 superseded in favour of workflow extensibility design
- Issue ID format documentation corrected from `{6hex}` to `{10hex}`

### Fixed

- Issue ID entropy increased from 6 to 10 hex characters to reduce collision probability at scale
- `import_jsonl` uses `cursor.rowcount` for all record types — accurate counts for merge dedup
- Batch error reporting enriched with `code` and `valid_transitions` fields
- Stale `filigree[mcp]` extra removed from packaging; WMIC parsing made quoting-aware for Windows compatibility
- PID verification abstracted beyond `/proc` for cross-platform support
- `fcntl.flock()` replaced with `portalocker` for cross-platform file locking
- Dead code `_generate_id_standalone()` removed

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

#### Dashboard UX

- Kanban cards now display a left-edge colour band indicating issue type (bug=red, feature=purple, task=blue, epic=amber, milestone=emerald, step=grey)

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
- ADR-001 added documenting the structured project model (strategic/execution/deliverable layers)
- README/docs expanded with architecture plans, mode guidance, and dashboard visuals
- Stale comments and docstrings fixed across 10 source files: endpoint counts, module docstrings, internal spec references (WFT-*), naming discrepancies, and misleading path references all corrected or removed

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
- `ProjectStore.get_db()` guarded against `UnboundLocalError` when `read_config()` fails before DB initialization
- `FindingStatus` type alias aligned with DB schema — added `acknowledged` and `unseen_in_latest`, removed stale `wont_fix` and `duplicate`
- Dead `_OPEN_FINDINGS_FILTER_F` and duplicate `_VALID_SEVERITIES` class attributes removed from `FiligreeDB`

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
- MCP `_build_workflow_text` now separates `sqlite3.Error` (with actionable "run `filigree doctor`" message) from generic exceptions; both log at ERROR
- MCP `get_workflow_prompt` narrows `except RuntimeError` to only silence "not initialized"; unexpected RuntimeErrors now logged at ERROR
- `generate_session_context` freshness-check now splits expected errors (`OSError`, `UnicodeDecodeError`, `ValueError`) at WARNING from unexpected errors at ERROR; both include `project_root` for debuggability
- `ProjectStore.reload()` DB close errors now log at WARNING (matching `close_all()`) instead of DEBUG
- `create_app` MCP ImportError now logged at DEBUG with `exc_info` instead of silently swallowed
- MCP `release_claim` tool description corrected: clarifies it clears assignee only (does not change status)
- `_install_mcp_server_mode` prefix-read failure narrowed to `JSONDecodeError`/`OSError` and elevated to WARNING; `_install_mcp_ethereal_mode` logs `claude mcp add` stderr on failure
- Duplicate `_check_same_thread` assignment removed from `FiligreeDB.__init__`
- `list_templates()` now includes `required_at`, `options`, and `default` in field schema — matches `get_template()` output
- `claim_issue()` now records prior assignee as `old_value` in claimed event; `undo_last` restores it instead of always blanking
- `SCHEMA_V1_SQL` refactored from brittle `SCHEMA_SQL.split()` to standalone constant with test assertions for subset integrity

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

[Unreleased]: https://github.com/tachyon-beep/filigree/compare/v1.6.0...HEAD
[1.6.0]: https://github.com/tachyon-beep/filigree/compare/v1.5.2...v1.6.0
[1.5.2]: https://github.com/tachyon-beep/filigree/compare/v1.5.1...v1.5.2
[1.5.1]: https://github.com/tachyon-beep/filigree/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/tachyon-beep/filigree/compare/v1.4.1...v1.5.0
[1.4.1]: https://github.com/tachyon-beep/filigree/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/tachyon-beep/filigree/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/tachyon-beep/filigree/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/tachyon-beep/filigree/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/tachyon-beep/filigree/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/tachyon-beep/filigree/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/tachyon-beep/filigree/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/tachyon-beep/filigree/releases/tag/v0.1.0
