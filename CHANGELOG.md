# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

- `rebuild_table()` FK fallback path still calls `conn.commit()` mid-migration, breaking strict atomicity for FK-referenced table rebuilds (tracked as filigree-938818)
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

[Unreleased]: https://github.com/tachyon-beep/filigree/compare/v1.1.1...HEAD
[1.1.1]: https://github.com/tachyon-beep/filigree/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/tachyon-beep/filigree/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/tachyon-beep/filigree/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/tachyon-beep/filigree/releases/tag/v0.1.0
