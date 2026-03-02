## Infrastructure

**Location:** `src/filigree/` (distributed across 15 files spanning installation, lifecycle, migration, scanning, analytics, and observability)

**Responsibility:** Provides all non-core operational capabilities: project installation and integration with AI coding agents (Claude Code, Codex), database schema migration, dashboard lifecycle management (ephemeral and server modes), external scanner orchestration, context/summary generation, flow analytics, input validation, and structured logging.

**Key Components:**

- `install.py` (235 LOC) -- Facade module re-exporting all public symbols from `install_support/` for backward compatibility. Contains the CLAUDE.md/AGENTS.md instruction injection logic (`inject_instructions`), `.gitignore` management (`ensure_gitignore`), skill pack installation (`install_skills`, `install_codex_skills`), and instruction versioning with SHA256 content hashing. Loads instruction templates from `filigree.data` package resources.

- `install_support/__init__.py` (15 LOC) -- Defines three shared constants (`FILIGREE_INSTRUCTIONS_MARKER`, `SKILL_NAME`, `SKILL_MARKER`) used across all install_support submodules and the doctor system. Exists to break circular imports between `install.py` and the submodules.

- `install_support/doctor.py` (564 LOC) -- Health check system implementing `run_doctor()` which runs 13 sequential checks: `.filigree/` existence, config.json validity, database accessibility + schema version, context.md freshness (60-minute threshold), .gitignore presence, Claude Code MCP config, Codex MCP config, Claude Code hooks, Claude Code skills, Codex skills, CLAUDE.md instructions, AGENTS.md instructions, and mode-specific checks (ephemeral PID/port liveness or server daemon status). Returns `list[CheckResult]` dataclass instances with name, pass/fail, message, and fix_hint. Delegates to `_doctor_ethereal_checks()` and `_doctor_server_checks()` based on the project's configured mode.

- `install_support/hooks.py` (267 LOC) -- Claude Code hook installation into `.claude/settings.json`. Manages two SessionStart hooks: `filigree session-context` and `filigree ensure-dashboard`. Key logic: `_hook_cmd_matches()` for flexible command matching (bare, absolute-path, quoted-path, and module-invocation forms), `_upgrade_hook_commands()` for upgrading stale binary paths to current absolute paths, and `install_claude_code_hooks()` which is idempotent and creates backup of malformed settings.json files. Uses `shlex.join` for safe cross-platform path quoting.

- `install_support/integrations.py` (240 LOC) -- MCP server configuration for two AI agents. For Claude Code: tries `claude mcp add` CLI first, falls back to direct `.mcp.json` writes. Supports two transport modes: stdio (ethereal, per-session process) and streamable-http (server, daemon URL with project prefix). For Codex: appends `[mcp_servers.filigree]` block to `.codex/config.toml`. Command discovery via `_find_filigree_mcp_command()` probes PATH, Python venv sibling, and filigree binary sibling directories. Backs up corrupt config files before overwriting.

- `hooks.py` (406 LOC) -- Runtime SessionStart hook logic. `generate_session_context()` produces a project snapshot showing dashboard URL, in-progress work, ready tasks (capped at 15), critical path, and stats. Also checks instruction freshness by comparing SHA256 hashes in CLAUDE.md/AGENTS.md markers against the installed template, auto-updating stale instructions and skill packs. `ensure_dashboard_running()` dispatches to ethereal or server mode. Ethereal mode: checks for existing dashboard via PID/port files, acquires a file lock (portalocker), spawns a background `filigree dashboard` process, writes PID/port files, polls for startup (10 x 300ms). Server mode: calls `register_project()`, then POSTs `/api/reload` to the daemon with a 2-second timeout. Sanitizes issue titles against context injection (newlines, control chars, length truncation to 160 chars).

- `ephemeral.py` (291 LOC) -- PID and port lifecycle for ethereal (session-scoped) dashboards. Deterministic port selection: `PORT_BASE (8400) + SHA256(resolved_path) % 1000`, with 5 sequential retries then OS-assigned fallback. PID files use JSON format `{"pid": N, "cmd": "filigree"}` with backward-compatible plain-integer legacy parsing. `verify_pid_ownership()` performs three-tier process identity verification: (1) Linux `/proc/{pid}/cmdline`, (2) macOS/BSD `ps -p`, (3) Windows `wmic`, with fallback to PID file metadata. Handles module invocation (`python -m filigree`) and launcher wrappers. Includes `cleanup_legacy_tmp_files()` for removing pre-mode-rename `/tmp/filigree-dashboard.*` files. Cross-platform: Windows uses `ctypes.windll.kernel32.OpenProcess` for PID liveness.

- `server.py` (366 LOC) -- Persistent multi-project daemon management. Config stored at `~/.config/filigree/server.json` with `ServerConfig` dataclass (port + projects dict). `register_project()` uses portalocker for atomic read-modify-write with prefix collision detection. `start_daemon()` spawns a background `filigree dashboard --server-mode` process with lock-protected PID ownership verification. `stop_daemon()` sends SIGTERM, waits 5 seconds (50 x 100ms), escalates to SIGKILL if needed, and always cleans up PID files. `claim_current_process_as_daemon()` allows a running dashboard to self-register its PID. Reuses PID lifecycle primitives from `ephemeral.py`.

- `scanners.py` (223 LOC) -- External scanner registry reading TOML definitions from `.filigree/scanners/*.toml`. Each `ScannerConfig` has name, description, command template, args, and file_types. Template variables: `{file}`, `{api_url}`, `{project_root}`, `{scan_run_id}`. `build_command()` uses `shlex.split` then per-token variable substitution. Safety: name validation via `^[\w-]+$` regex (prevents path traversal), command validation checks binary existence on PATH or as relative path against project root. Skips `.toml.example` files during listing.

- `migrate.py` (246 LOC) -- One-time migration tool from the predecessor "beads" issue tracker. Two-pass insert strategy: pass 1 inserts issues without parent_id (avoiding FK ordering), pass 2 sets parent_id. Maps beads-specific columns (design, acceptance_criteria, estimated_minutes, etc.) into the JSON `fields` bag. Migrates dependencies, events, labels, and comments with dedup. Atomic: full rollback on any failure.

- `migrations.py` (532 LOC) -- Schema migration framework. Uses SQLite's `PRAGMA user_version` for version tracking. Currently 4 migrations registered (v1->v2 through v4->v5). Each migration runs in a `BEGIN IMMEDIATE` transaction with FK enforcement disabled (re-enabled after commit). FK integrity validated via `PRAGMA foreign_key_check` before commit. Includes helper functions: `add_column()` (idempotent), `add_index()` (IF NOT EXISTS), `drop_index()`, `rename_column()`, and `rebuild_table()` (the 12-step SQLite pattern for constraint changes). Migration v4->v5 does data migration (normalizing release version fields to semver). Includes three template migrations as documentation for future contributors. `MigrationError` exception preserves from/to version context.

- `summary.py` (315 LOC) -- Generates `context.md`, a pre-computed markdown summary for agent context. Sections: Vitals (open/wip/done/ready/blocked counts), Active Plans (milestones with Unicode progress bars), Ready to Work (12 items), In Progress, Needs Attention (WIP issues with missing required fields), Stale (in_progress >3 days), Blocked (top 10), Epic Progress (with progress bars), Critical Path, Recent Activity (last 10 events). Batch-fetches parent titles (chunked at 500 to stay under SQLite variable limits). Sanitizes all untrusted text (control chars, newlines, 200-char truncation). `write_summary()` uses atomic write (mkstemp + os.replace).

- `analytics.py` (198 LOC) -- Flow metrics: cycle time (first WIP-category state to first done-category state, in hours), lead time (creation to closure), and aggregate `get_flow_metrics()` with configurable lookback window. Uses the workflow template system to resolve status categories, making it work for all issue types. Batch-fetches status events chunked at 500. Returns `FlowMetrics` typed dict with per-type breakdown.

- `validation.py` (34 LOC) -- Single function `sanitize_actor()` that validates actor names: string type check, control/format character rejection (Unicode category check), whitespace stripping, emptiness check, 128-char max length. Returns tuple of (cleaned_value, error_message).

- `logging.py` (73 LOC) -- JSONL structured logging with rotation (5MB, 3 backups) to `.filigree/filigree.log`. Custom `_JsonFormatter` outputs timestamp, level, message, and optional tool/args/duration_ms/error/exception fields. Thread-safe setup with `threading.Lock`. Deduplicates handlers by comparing `baseFilename` to avoid leaks on repeated calls.

- `db_schema.py` (281 LOC) -- Canonical schema SQL for fresh database creation, V1 schema for migration tests, and `CURRENT_SCHEMA_VERSION = 5`. Not part of the runtime infrastructure per se but tightly coupled to the migration system.

**Internal Architecture:**

The infrastructure subsystem has a layered decomposition with three clear tiers:

1. **Installation tier** (`install.py` + `install_support/`): Handles first-time project setup and AI agent integration. The `install_support/` subpackage was extracted from a monolithic `install.py` to manage complexity. The parent `install.py` serves as a re-export facade preserving backward compatibility for 6+ existing callers. The subpackage `__init__.py` holds shared constants to break circular imports. Each submodule (doctor, hooks, integrations) is independently testable.

2. **Lifecycle tier** (`hooks.py`, `ephemeral.py`, `server.py`): Manages the two dashboard installation modes:
   - **Ethereal mode**: Session-scoped, one dashboard per project, spawned on demand by the `ensure-dashboard` SessionStart hook. Uses deterministic port assignment (hash-based), PID/port files in `.filigree/`, and portalocker for atomic startup. The dashboard process is detached (`start_new_session=True`) so it survives the hook timeout.
   - **Server mode**: Persistent multi-project daemon with config at `~/.config/filigree/`. Projects register themselves (prefix-based routing), the daemon can be started/stopped independently, and MCP uses streamable-http transport instead of stdio. Lock-protected registration prevents prefix collisions.

   The `hooks.py` module acts as the runtime entry point for both Claude Code SessionStart hooks, delegating to mode-specific functions. It also handles instruction freshness checking (auto-updating stale CLAUDE.md/AGENTS.md content).

3. **Data/Operations tier** (`migrations.py`, `migrate.py`, `summary.py`, `analytics.py`, `scanners.py`, `validation.py`, `logging.py`): Utility modules that operate on or alongside the core database. The migration system uses SQLite's `PRAGMA user_version` with a registry pattern (version number -> function mapping). Summary and analytics are read-only consumers of `FiligreeDB`. The scanner subsystem is entirely configuration-driven (TOML) with process spawning delegated to subprocess.

Key design decisions:
- File locks (portalocker) used consistently for all concurrent state mutations (server.json, PID files, ephemeral startup).
- Atomic file writes (`write_atomic` from core, or mkstemp+rename) used for all state files.
- Backup-before-overwrite pattern for corrupt config files (settings.json, .mcp.json, server.json).
- SHA256-based content hashing for detecting stale instruction files without version coupling.

**Dependencies:**

- Inbound: CLI commands (`cli_commands/admin.py`, `cli_commands/server.py`), MCP tools (`mcp_tools/files.py`, `mcp_tools/meta.py`), MCP server (`mcp_server.py`), dashboard (`dashboard.py`, `dashboard_routes/`), CLI common (`cli_common.py`, `cli.py`), and Core (`core.py` calls `apply_pending_migrations`).
- Outbound: Core (`filigree.core` -- `FiligreeDB`, `find_filigree_root`, `find_filigree_command`, `read_config`, `get_mode`, `write_atomic`), DB Schema (`filigree.db_schema` -- `CURRENT_SCHEMA_VERSION`), Types (`filigree.types.planning` -- `FlowMetrics`, `TypeMetrics`), and external packages (`portalocker`, `tomllib`).

**Patterns Observed:**

- **Facade re-export pattern**: `install.py` re-exports all public symbols from `install_support/` submodules, maintaining backward compatibility while allowing internal decomposition. The `__all__` list explicitly enumerates 24 symbols.
- **Mode dispatch pattern**: Both `install_claude_code_mcp()` and `ensure_dashboard_running()` branch on the project's configured mode (ethereal vs server), choosing different transport mechanisms and lifecycle strategies.
- **Idempotent installation**: Every install function checks for existing state before modifying (hooks, MCP config, skills, gitignore). Re-running is always safe and upgrades stale configurations.
- **Lock-protected state mutation**: `portalocker.LOCK_EX` used for ephemeral dashboard startup, server config read-modify-write, and daemon PID file claiming. Non-blocking lock attempt (`LOCK_NB`) used in ephemeral mode to skip if another session is already starting.
- **Atomic file writes**: PID files, port files, summary, and server config all use write-to-temp-then-rename for crash safety.
- **Content-addressed freshness**: Instructions in CLAUDE.md/AGENTS.md carry a SHA256 hash in their HTML comment marker, enabling drift detection without version number coupling.
- **Graceful degradation**: Dashboard hook checks for `fastapi`/`uvicorn` imports before attempting to start; Codex skills and AGENTS.md are optional (no warnings if absent); doctor continues past individual check failures.
- **Cross-platform PID verification**: Three-tier fallback chain (Linux /proc, macOS ps, Windows wmic) with advisory PID file metadata as last resort, preventing false-positive "daemon running" reports from PID reuse.
- **Migration templates as documentation**: Three `_template_*` functions in `migrations.py` serve as copy-paste starting points with inline comments explaining SQLite ALTER TABLE limitations.

**Concerns:**

- **TOCTOU race in port allocation**: `find_available_port()` checks port availability then returns the port for a subprocess to bind later. The code documents this explicitly and considers it acceptable, but under high concurrency (multiple agents starting dashboards simultaneously) the retry-on-bind-failure path is not exercised by the caller in `hooks.py` -- the startup polling loop would report "may still be initializing" rather than retrying with a new port.
- **Lock file cleanup**: `ephemeral.lock` files in `.filigree/` are never explicitly removed after use. While harmless (file locks are advisory), they accumulate as zero-byte files.
- **Hardcoded timeouts**: Several timeouts are hardcoded (5s for git operations in doctor, 10s for `claude mcp add`, 0.5s for socket connect checks, 2s for daemon reload POST, 300ms x 10 for startup polling). None are configurable, which could be problematic in slow CI environments.
- **Title sanitization duplicated**: Both `hooks.py` (`_sanitize_context_title`, 160 char limit) and `summary.py` (`_sanitize_title`, 200 char limit) implement similar but not identical title sanitization logic. This should ideally be consolidated.
- **ISO timestamp parsing duplicated**: `analytics.py` and `summary.py` both define `_parse_iso()` functions with slightly different return types (None vs sentinel object). Could be a shared utility.
- **Migration v4->v5 does data migration inline**: The semver normalization migration embeds business logic (regex matching, comment insertion) directly in the migration function. This is common but makes the migration harder to test in isolation.
- **Server mode config path not configurable**: `SERVER_CONFIG_DIR` is hardcoded to `~/.config/filigree/`. No environment variable or config override for non-standard home directories or containerized environments.
- **Legacy cleanup in hot path**: `cleanup_legacy_tmp_files()` runs on every ephemeral dashboard startup, attempting to unlink three `/tmp` files. This is a startup cost for a migration from a previous era that could eventually be removed.

**Confidence:** High -- Read 100% of all 15 source files listed (install.py, install_support/__init__.py, install_support/doctor.py, install_support/hooks.py, install_support/integrations.py, hooks.py, ephemeral.py, server.py, scanners.py, migrate.py, migrations.py, summary.py, analytics.py, validation.py, logging.py), plus supporting db_schema.py and an example scanner TOML. Cross-verified all dependency claims by tracing imports in both directions (grep for inbound consumers across the codebase, verified outbound imports within each file). Confirmed the install_support decomposition pattern by reading the re-export facade and checking backward-compatibility callers listed in install.py comments.
