# Keel -- Current System Requirements (Consensus)

**Date**: 2026-02-11
**Method**: Synthesis of 5 specialist position papers (Architecture, Python Quality, UX, Systems Thinking, Documentation)
**Scope**: Keel v1.0 as implemented at commit `2b3ad2f` on `main`
**Facilitator**: Requirements Engineer Agent

---

## 1. Executive Summary

### Totals

- **94 consensus requirements** distilled from 212 raw requirements across 5 position papers
- **49 Functional Requirements** (FR-001 through FR-049)
- **25 Non-Functional Requirements** (NFR-001 through NFR-025)
- **8 Architectural Constraints** (AC-001 through AC-008)
- **12 Interface Requirements** (IR-001 through IR-012) -- distributed across MCP, CLI, Dashboard, and Summary

### Key Themes Across All Specialists

1. **Agent-first, human-second design**: All 5 specialists independently identified this as the dominant design philosophy. The MCP server is the primary interface; CLI and Dashboard are secondary.

2. **Summary-driven coordination**: The pre-computed `context.md` is the central nervous system. Four specialists (Architect, UX, Systems, Docs) identified it as the critical integration point between agents and the database.

3. **SQLite as the sole persistence layer**: All interfaces share a single SQLite file with WAL mode. No daemon, no sync, no external services. Three specialists (Architect, Python, Systems) analyzed this pattern.

4. **Event sourcing as audit backbone**: Append-only event recording enables analytics, session resumption, and activity feeds. Identified by Architect, Systems, and Docs specialists.

5. **Strict code quality discipline**: Python specialist and Docs specialist independently confirmed 100% type coverage (mypy strict), 14 Ruff rule families, and documented suppressions for every deviation.

6. **Optimistic locking for multi-agent safety**: Architect, UX, and Systems specialists all identified `claim_issue` as a critical coordination primitive.

### Notable Conflicts Resolved

- **Template enforcement**: Architect says templates are advisory (no validation); UX says agents are guided to use them. Resolution: templates are advisory by design (see Section 7, Conflict C-01).
- **Custom workflow states vs. MCP schema enum**: Architect identified that MCP hardcodes status enum despite custom workflow support. This is a design tension, not a conflict (see Section 7, Conflict C-02).
- **Summary regeneration: feature vs. risk**: Systems Thinker identifies it as a systemic risk (Shifting the Burden archetype); UX and Docs identify it as a critical feature. Both are true (see Section 7, Conflict C-03).

### Gaps Identified

- No coverage threshold configured (Python specialist)
- No undo/rollback mechanism (UX specialist)
- No automatic stale-claim release timeout (Systems specialist)
- No load testing or performance benchmarks (all specialists)
- Dashboard scalability untested at >1000 issues (UX, Systems)
- Summary format not versioned (Docs specialist)

---

## 2. Functional Requirements

### 2.1 Issue Management

#### FR-001: Issue Creation with Prefixed Short UUIDs

The system shall generate issue IDs in the format `{prefix}-{6hex}` where prefix is project-configurable and the hex suffix is drawn from UUID4. The system shall retry up to 10 collisions before falling back to a 10-character hex suffix.

- **Source**: Architect (REQ-D01), Python (10.1)
- **Evidence**: `src/keel/core.py:435-442` -- `_generate_id()` function
- **Enforcement**: Explicit (function contract with collision handling)
- **Priority**: Must-have

#### FR-002: Fixed Core Schema with Flexible Fields JSON Bag

The system shall maintain 13 typed columns on the `issues` table (id, title, status, priority, type, parent_id, assignee, created_at, updated_at, closed_at, description, notes, fields). Extension data shall be stored in the `fields` column as JSON TEXT with merge semantics on update (`{**current, **new}`).

- **Source**: Architect (REQ-D02, REQ-E04)
- **Evidence**: `src/keel/core.py:64-82` (schema), `src/keel/core.py:778-782` (merge)
- **Enforcement**: Explicit (schema definition + merge code)
- **Priority**: Must-have

#### FR-003: Priority Constraint (0-4 Inclusive)

The system shall enforce priority values between 0 and 4 inclusive via a database-level CHECK constraint. Priority 0 means critical; priority 4 means lowest.

- **Source**: Architect (REQ-D03), UX (REQ-6.1.2)
- **Evidence**: `src/keel/core.py:69,199` -- `CHECK (priority BETWEEN 0 AND 4)`
- **Enforcement**: Explicit (database constraint)
- **Priority**: Must-have

#### FR-004: Application-Layer Status Validation with Custom Workflow Support

The system shall validate status values at the application layer (not database layer) against a configurable `workflow_states` list. The default states shall be `["open", "in_progress", "closed"]`. Custom states shall be configurable via `.keel/config.json`.

- **Source**: Architect (REQ-D04, REQ-E01), UX (REQ-6.1.1)
- **Evidence**: `src/keel/core.py:173-231` (v3 migration removes CHECK), `src/keel/core.py:567-571` (`_validate_status`), `src/keel/core.py:462-466` (config reading)
- **Enforcement**: Explicit (migration + validation code)
- **Priority**: Must-have
- **Note**: `_validate_status` is called in `update_issue` but NOT in `create_issue`, `claim_issue`, or `release_claim`, which hardcode status values. Safe only if default workflow always includes 'open' and 'in_progress'.

#### FR-005: Directed Acyclic Graph (DAG) Dependencies

The system shall enforce that the dependency graph is acyclic. Self-dependencies shall be rejected. Adding a dependency that would create a cycle shall be rejected after BFS traversal verification.

- **Source**: Architect (REQ-D05, REQ-R02), Systems (SR3)
- **Evidence**: `src/keel/core.py:950-994` -- `add_dependency` rejects self-refs, `_would_create_cycle` performs BFS
- **Enforcement**: Explicit (BFS validation code)
- **Priority**: Must-have

#### FR-006: Single-Parent Issue Hierarchy

The system shall support a single-parent hierarchy via the `parent_id` column. Each issue may have at most one parent. No depth limit shall be enforced.

- **Source**: Architect (REQ-D06)
- **Evidence**: `src/keel/core.py:71` -- `parent_id TEXT REFERENCES issues(id)`
- **Enforcement**: Explicit (schema), though FK reference dropped in v3 migration
- **Priority**: Must-have

#### FR-007: Event Sourcing for All State Mutations

The system shall record an event in the `events` table for every state mutation including: creation, status change, title change, priority change, assignee change, dependency add/remove, claim, release, and archive.

- **Source**: Architect (REQ-D08), Systems (SR4, Dynamic 2), Docs (5.1)
- **Evidence**: `src/keel/core.py:1329-1343` -- `_record_event` called from all mutation paths
- **Enforcement**: Explicit (systematic event recording pattern)
- **Priority**: Must-have
- **Note**: Description and notes changes do NOT generate events. This is an implicit design choice.

#### FR-008: Free-Form Labels with Many-to-Many Relationship

The system shall store labels as (issue_id, label) pairs with a composite primary key. Labels shall be free-form strings with no separate registry. Operations shall use INSERT OR IGNORE / DELETE semantics.

- **Source**: Architect (REQ-D09)
- **Evidence**: `src/keel/core.py:121-125` -- `labels` table schema
- **Enforcement**: Explicit (schema definition)
- **Priority**: Must-have

#### FR-009: Advisory Template System for Issue Types

The system shall provide templates defining field schemas for issue types (8 built-in: task, bug, feature, epic, milestone, phase, step, requirement). Templates shall be advisory -- the system shall accept arbitrary fields without validation against templates. Templates shall be seeded from `BUILT_IN_TEMPLATES` on initialization via INSERT OR IGNORE.

- **Source**: Architect (REQ-D10, REQ-E02), Docs (9.1, 9.2)
- **Evidence**: `src/keel/core.py:127-132,265-377,530-537` -- template table, definitions, seeding
- **Enforcement**: Explicit (table + seed) for templates; Implicit (no enforcement code) for advisory nature
- **Priority**: Must-have

#### FR-010: Free-Form Issue Type (No Enum Constraint)

The system shall accept any string as an issue type. The `type` column shall have no CHECK constraint. Built-in templates define conventional types but do not constrain them.

- **Source**: Architect (REQ-E03)
- **Evidence**: `src/keel/core.py:70` -- `type TEXT NOT NULL DEFAULT 'task'` (no CHECK)
- **Enforcement**: Implicit (no constraint exists)
- **Priority**: Must-have

#### FR-011: FTS5 Full-Text Search with Graceful Fallback

The system shall use SQLite FTS5 for full-text search of titles and descriptions. If FTS5 is unavailable, the system shall fall back to LIKE queries with proper escaping.

- **Source**: Architect (REQ-D11)
- **Evidence**: `src/keel/core.py:146-170` (FTS5 creation), `src/keel/core.py:924-946` (try/except fallback)
- **Enforcement**: Explicit (try/except fallback)
- **Priority**: Must-have

#### FR-012: Optimistic Locking for Issue Claiming

The system shall provide `claim_issue` which atomically transitions an issue from 'open' to 'in_progress' using a conditional UPDATE (`WHERE status = 'open'`) with rowcount verification. If two agents race to claim, only one shall succeed; the other shall receive a ValueError.

- **Source**: Architect (REQ-C03), UX (REQ-1.5.4), Systems (B2, SR2)
- **Evidence**: `src/keel/core.py:806-825` -- conditional UPDATE + rowcount check
- **Enforcement**: Explicit (documented in docstring, verified by test)
- **Priority**: Must-have

#### FR-013: Release Claim with Reverse Optimistic Locking

The system shall provide `release_claim` which atomically transitions from 'in_progress' back to 'open' using the same conditional UPDATE pattern as `claim_issue`.

- **Source**: Architect (REQ-C04)
- **Evidence**: `src/keel/core.py:827-845`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### FR-014: Close Operation Returns Newly-Unblocked Issues

The system shall compute which issues became newly unblocked when closing an issue. The close response shall include the list of issues that were blocked before the close and became ready after.

- **Source**: Architect (REQ-I05), UX (REQ-1.3.3, REQ-8.1.2)
- **Evidence**: `src/keel/mcp_server.py:716-732` -- `close_issue` computes before/after diff
- **Enforcement**: Explicit (deliberate agent workflow feature)
- **Priority**: Must-have

### 2.2 Dependency Management

#### FR-015: Dependency Type Field (Extensible but Currently Single-Value)

The system shall store a `type` column on the dependencies table with default value 'blocks'. The `add_dependency` method shall accept a `dep_type` parameter for future extension, though all current code paths use 'blocks'.

- **Source**: Architect (REQ-E06)
- **Evidence**: `src/keel/core.py:89-95,950-966`
- **Enforcement**: Implicit (extensibility point exists but is unused)
- **Priority**: Should-have

#### FR-016: Ready and Blocked Issue Computation

The system shall compute "ready" status as: `status='open'` AND zero open blockers. The system shall compute "blocked" status as: `status='open'` AND at least one open blocker. These computations shall be consistent across all interfaces.

- **Source**: Architect (OBS-01), UX (REQ-6.2.2), Systems (B1)
- **Evidence**: `src/keel/core.py:1010-1033` -- `get_ready()`, `get_blocked()`; `src/keel/core.py:684-692` -- batch `is_ready` flag
- **Enforcement**: Explicit (SQL queries)
- **Priority**: Must-have

### 2.3 Planning

#### FR-017: Three-Level Plan Hierarchy (Milestone, Phase, Step)

The system shall enforce a three-level plan hierarchy: milestones contain phases (type='phase'), phases contain steps (type='step'). Ordering shall use a `sequence` field in the fields JSON bag, defaulting to 999 for unsequenced items.

- **Source**: Architect (REQ-E05)
- **Evidence**: `src/keel/core.py:1102-1135` -- `get_plan`; `src/keel/core.py:1137-1253` -- `create_plan`
- **Enforcement**: Explicit (`create_plan` enforces structure)
- **Priority**: Must-have

### 2.4 Search and Discovery

#### FR-018: Session Resumption via Change Tracking

The system shall provide a `get_changes` operation that returns events since a given timestamp, enabling agents to resume sessions without re-reading full state.

- **Source**: UX (REQ-1.5.1)
- **Evidence**: `src/keel/mcp_server.py` -- `get_changes` tool with `since` parameter
- **Enforcement**: Explicit
- **Priority**: Should-have

#### FR-019: Paginated List Queries

The system shall support `limit` and `offset` parameters on list and search operations. The default limit shall be 100.

- **Source**: UX (REQ-9.1.1, REQ-9.1.2), Architect (REQ-P04)
- **Evidence**: `src/keel/core.py:893` -- `limit: int = 100`
- **Enforcement**: Explicit
- **Priority**: Must-have

### 2.5 Analytics

#### FR-020: Flow Metrics (Cycle Time, Lead Time, Throughput)

The system shall compute cycle time (first in_progress to closed), lead time (created to closed), and throughput (issues closed per period) from event and issue data.

- **Source**: Architect (REQ-I06), Systems (Dynamic 2)
- **Evidence**: `src/keel/analytics.py:26-48` (cycle_time), `src/keel/analytics.py:51-58` (lead_time)
- **Enforcement**: Explicit (dedicated analytics module)
- **Priority**: Should-have

#### FR-021: Critical Path Computation

The system shall compute the longest dependency chain among open issues using DAG longest-path dynamic programming. The critical path shall be advisory (agents are not required to follow it).

- **Source**: Architect (REQ-E05), Systems (R2, SR9), UX (REQ-4.1.6)
- **Evidence**: `src/keel/core.py:1037-1098` -- topological sort + longest path DP
- **Enforcement**: Explicit (algorithm), Implicit (advisory nature)
- **Priority**: Should-have

### 2.6 Data Lifecycle

#### FR-022: JSONL Export and Import

The system shall export all five record types (issues, dependencies, labels, comments, events) to a JSONL file with a `_type` discriminator. Import shall support merge mode (skip conflicts) and abort mode (fail on conflict).

- **Source**: Architect (REQ-R05)
- **Evidence**: `src/keel/core.py:1417-1550`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### FR-023: Archival and Compaction

The system shall support archiving closed issues older than N days to 'archived' status. The system shall support compacting events for archived issues to keep only the most recent N. VACUUM shall be available to reclaim space.

- **Source**: Architect (REQ-R06), Systems (Stock: Events Table)
- **Evidence**: `src/keel/core.py:1554-1623`
- **Enforcement**: Explicit
- **Priority**: Should-have

#### FR-024: Schema Versioning with Forward-Only Migrations

The system shall track schema version via `PRAGMA user_version`. Migrations shall run sequentially, only forward, never backward. The current version shall be 4.

- **Source**: Architect (REQ-D12), Python (9.3)
- **Evidence**: `src/keel/core.py:136,254-259,508-518`
- **Enforcement**: Explicit (migration framework)
- **Priority**: Must-have

### 2.7 Batch Operations

#### FR-025: Bulk Operations for Multi-Issue Mutations

The system shall provide `batch_close` and `batch_update` for operating on multiple issues in a single tool call. Batch close shall return newly-unblocked items.

- **Source**: UX (REQ-1.5.2), Architect (OBS-03)
- **Evidence**: `src/keel/mcp_server.py:847-883`
- **Enforcement**: Explicit
- **Priority**: Should-have
- **Note**: Batch operations loop through individual operations with individual commits; they are not truly atomic. A crash mid-loop results in partial updates.

### 2.8 Comments

#### FR-026: Issue Comments with Author Attribution

The system shall support adding comments to issues with author, text, and timestamp. Comments shall be stored in a dedicated `comments` table.

- **Source**: Architect (REQ-D08 evidence), Docs (8.1)
- **Evidence**: `src/keel/core.py:97-104` (schema), `src/keel/core.py:1268-1329` (CRUD)
- **Enforcement**: Explicit
- **Priority**: Must-have

### 2.9 Stale Detection

#### FR-027: Stale Issue Detection (>3 Days Without Activity)

The system shall flag in-progress issues with no activity for more than 3 days as stale. Stale detection shall be surfaced in the summary but shall not trigger automatic action.

- **Source**: UX (REQ-4.1.4), Systems (EB4)
- **Evidence**: `src/keel/summary.py:118-128`
- **Enforcement**: Explicit (detection), Implicit (no automatic release)
- **Priority**: Should-have

### 2.10 Summary Generation

#### FR-028: Pre-Computed Summary (context.md)

The system shall generate a compact markdown summary (~80-120 lines) of project state. The summary shall include: vitals, active plans, ready work, in-progress work, stale issues, blocked issues, epic progress, critical path, and recent activity. Ready work shall appear before blocked work.

- **Source**: UX (REQ-4.1.1 through REQ-4.1.7), Docs (7.1, 7.2), Systems (R1, SR5)
- **Evidence**: `src/keel/summary.py:26-211`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### FR-029: Summary Regeneration After Every Mutation

The system shall regenerate `context.md` after every state-changing operation in both MCP and CLI interfaces.

- **Source**: Architect (REQ-I03), Systems (R1), UX (REQ-4.2.1), Docs (7.1)
- **Evidence**: `src/keel/mcp_server.py:60-63,693,709,722,743,808,813,823,844,857,879,917,927`; `src/keel/cli.py:58-64`
- **Enforcement**: Explicit (deliberate refresh calls)
- **Priority**: Must-have

#### FR-030: Summary Includes Generation Timestamp

The system shall include a generation timestamp in the summary for staleness detection.

- **Source**: UX (REQ-4.2.2)
- **Evidence**: `src/keel/summary.py:37`
- **Enforcement**: Explicit
- **Priority**: Must-have

### 2.11 Installation and Setup

#### FR-031: Convention-Based Project Discovery

The system shall discover the project root by walking up the directory tree from the current working directory looking for a `.keel/` directory. No environment variables or global config shall be required.

- **Source**: Architect (REQ-DEP01), Python (10.2), Docs (1.2)
- **Evidence**: `src/keel/core.py:31-42` -- `find_keel_root`
- **Enforcement**: Explicit (documented in module docstring)
- **Priority**: Must-have

#### FR-032: Multi-Tool Installation Support

The system shall support installing MCP configuration for Claude Code (via `claude mcp add` or `.mcp.json`), Codex (via `.codex/config.toml`), injecting workflow instructions into CLAUDE.md and AGENTS.md, and adding `.keel/` to `.gitignore`. Each component shall be installable individually or all-at-once.

- **Source**: Architect (REQ-DEP04), UX (REQ-5.1.1 through REQ-5.1.3), Docs (10.1)
- **Evidence**: `src/keel/install.py:103-254`; `src/keel/cli.py:600-656`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### FR-033: Idempotent Instruction Injection with HTML Comment Markers

The system shall use `<!-- keel:instructions -->` and `<!-- /keel:instructions -->` markers for idempotent updates to CLAUDE.md and AGENTS.md. Re-running install shall replace the block, not duplicate it.

- **Source**: UX (REQ-5.3.2), Docs (6.3)
- **Evidence**: `src/keel/install.py:33,79`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### FR-034: Doctor Health Checks with Fix Hints

The system shall provide 10 health checks: .keel/ existence, config.json validity, keel.db accessibility + schema version, context.md freshness, .gitignore, Claude Code MCP, Codex MCP, CLAUDE.md instructions, AGENTS.md instructions, git working tree status. Each failed check shall include a fix hint.

- **Source**: Architect (REQ-DEP06), UX (REQ-5.2.1, REQ-5.2.2, REQ-5.2.3), Docs (10.2)
- **Evidence**: `src/keel/install.py:276-544`
- **Enforcement**: Explicit
- **Priority**: Must-have

### 2.12 Migration

#### FR-035: Beads-to-Keel Migration

The system shall provide one-time migration from the beads issue tracker, preserving IDs, dependencies, events, labels, and comments. Domain-specific beads columns shall map to the `fields` JSON bag. Bulk insert methods shall commit only at the end for atomicity.

- **Source**: Architect (REQ-R04)
- **Evidence**: `src/keel/migrate.py:172` -- `tracker.bulk_commit()`; `src/keel/core.py:1367-1413`
- **Enforcement**: Explicit
- **Priority**: Must-have

---

## 3. Non-Functional Requirements

### 3.1 Performance

#### NFR-001: Single-Column Indexes on Issues Table

The system shall maintain indexes on the `status`, `type`, `parent_id`, and `priority` columns of the issues table.

- **Source**: Architect (REQ-P01)
- **Evidence**: `src/keel/core.py:84-87`
- **Enforcement**: Explicit (CREATE INDEX IF NOT EXISTS)
- **Priority**: Must-have

#### NFR-002: Composite Covering Indexes for Common Query Patterns

The system shall maintain composite indexes for: status+priority+created_at (list queries), issue_id+depends_on_id (dep queries), issue_id+created_at DESC (event queries), issue_id+created_at (comment queries). ANALYZE shall be run after index creation.

- **Source**: Architect (REQ-P02, REQ-P06)
- **Evidence**: `src/keel/core.py:234-251` -- v4 migration
- **Enforcement**: Explicit (migration)
- **Priority**: Must-have

#### NFR-003: Batch Issue Building to Eliminate N+1 Queries

The system shall load all related data (labels, blocks, blocked_by, children, open blocker counts) in batched queries using `IN (?)` placeholders, not per-issue queries.

- **Source**: Architect (REQ-P03), Systems (SR8)
- **Evidence**: `src/keel/core.py:641-722` -- `_build_issues_batch`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-004: Atomic Summary Writes via Temp-File-Then-Rename

The system shall write summaries to a `.tmp` file first, then use `os.replace` (atomic on POSIX) to swap into place, preventing partial reads.

- **Source**: Architect (REQ-P05), Systems (SR10)
- **Evidence**: `src/keel/summary.py:214-220`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-005: Summary Generation Shall Complete Faster Than Agent Decision Cycle

The system shall maintain summary generation time substantially below agent decision time (typically seconds to minutes). The current target is ~50ms for 600 issues.

- **Source**: Systems (SR1, BC1)
- **Evidence**: Design doc Section 5; `src/keel/summary.py:214-220` (synchronous)
- **Enforcement**: Implicit (no monitoring or circuit breaker)
- **Priority**: Must-have

### 3.2 Reliability

#### NFR-006: Foreign Key Enforcement

The system shall enable foreign key constraints via `PRAGMA foreign_keys=ON` on every database connection.

- **Source**: Architect (REQ-R01)
- **Evidence**: `src/keel/core.py:497`
- **Enforcement**: Explicit
- **Priority**: Must-have
- **Note**: v3 migration drops `REFERENCES issues(id)` from `parent_id`, so parent_id FK integrity is not enforced at the database level post-migration.

#### NFR-007: Migration Safety via Temporary Table Pattern

The system shall use CREATE TABLE new -> INSERT SELECT -> DROP TABLE old -> ALTER TABLE RENAME for table recreation migrations. Foreign keys shall be disabled during this operation.

- **Source**: Architect (REQ-R03)
- **Evidence**: `src/keel/core.py:173-231`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-008: Bulk Import Transactional Atomicity

Bulk import operations shall NOT commit individually. The caller shall call `bulk_commit()` to commit all changes atomically.

- **Source**: Architect (REQ-R04)
- **Evidence**: `src/keel/core.py:1367-1413,1412`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-009: Structured JSON Logging with Rotation

The MCP server shall log tool calls, errors, and durations as JSONL to `.keel/keel.log` with 5MB rotation and 3 backups.

- **Source**: Architect (REQ-R07)
- **Evidence**: `src/keel/logging.py:1-59`; `src/keel/mcp_server.py:644-657`
- **Enforcement**: Explicit
- **Priority**: Must-have

### 3.3 Security

#### NFR-010: SQL Parameterization (No String Interpolation)

The system shall use `?` placeholders for all user input in SQL queries. String interpolation for SQL shall be forbidden. S608 is suppressed in core.py and dashboard.py with documented rationale.

- **Source**: Python (9.1, 2.3), Architect (cross-ref)
- **Evidence**: `pyproject.toml:107` -- S608 suppression; all queries use `?` placeholders
- **Enforcement**: Explicit (Ruff S-family rules + documented suppression)
- **Priority**: Must-have

#### NFR-011: Bandit Security Rules with Documented Exceptions

The system shall enable flake8-bandit (S-family) rules with only 3 global suppressions: S603 (subprocess-without-shell-check), S607 (start-process-with-partial-path), T201 (print). All suppressions shall have documented rationale.

- **Source**: Python (2.3)
- **Evidence**: `pyproject.toml:99-103`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-012: Dashboard Localhost-Only Binding

The dashboard server shall bind to `127.0.0.1`, not `0.0.0.0`. It shall not be designed for network exposure.

- **Source**: Architect (REQ-DEP08)
- **Evidence**: `src/keel/dashboard.py:135`
- **Enforcement**: Explicit
- **Priority**: Must-have

### 3.4 Usability

#### NFR-013: Minimal Required Parameters for MCP Tools

MCP tools shall require at most 1-2 parameters. Optional parameters shall use sensible defaults (priority=2, type="task", limit=100).

- **Source**: UX (REQ-1.2.1, REQ-1.2.2, REQ-8.1.1)
- **Evidence**: Tool input schemas across `src/keel/mcp_server.py:176-634`
- **Enforcement**: Implicit (consistent pattern)
- **Priority**: Must-have

#### NFR-014: Structured Error Responses for Agents

MCP tool errors shall return structured JSON with `error` (message) and `code` (programmatic identifier). Error codes shall include: `not_found`, `invalid`, `conflict`, `unknown_tool`.

- **Source**: UX (REQ-1.3.2), Docs (8.2)
- **Evidence**: `src/keel/mcp_server.py:662-939`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-015: Progressive Workflow Disclosure (CLI)

The CLI shall include "Next:" hints after operations to guide users through the workflow. Example: "Created {id}: {title}\nNext: keel ready".

- **Source**: UX (REQ-2.4.3, REQ-5.1.2, REQ-5.1.3)
- **Evidence**: `src/keel/cli.py:107,161,656`
- **Enforcement**: Implicit (consistent pattern)
- **Priority**: Should-have

### 3.5 Maintainability

#### NFR-016: Mypy Strict Mode with Zero Application-Code Opt-Outs

The system shall enforce mypy strict mode with `disallow_untyped_defs = true`. All `type: ignore` comments shall include error codes. Only external libraries (MCP, FastAPI, Uvicorn) shall have `ignore_missing_imports`.

- **Source**: Python (3.1, 3.2, 3.3)
- **Evidence**: `pyproject.toml:118-135`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-017: 14-Family Ruff Linting with 120-Character Line Length

The system shall enable 14 Ruff rule families (E, W, F, I, N, UP, B, SIM, RUF, S, T20, PT, PIE, C4, DTZ) with 120-character line length. Per-file exceptions shall be documented.

- **Source**: Python (2.1, 2.2)
- **Evidence**: `pyproject.toml:75-109`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-018: Modern Python 3.11+ Idioms

The system shall use PEP 604 union types (`X | None`), PEP 585 generics (`list[str]`), `from __future__ import annotations`, keyword-only arguments (`*`), f-strings, dataclasses with `field(default_factory=...)`, structural pattern matching (`match`/`case`), and context managers.

- **Source**: Python (1.1, 1.2, 1.3, 10.1-10.5)
- **Evidence**: Consistent usage throughout `src/keel/core.py`, `src/keel/cli.py`, `src/keel/mcp_server.py`
- **Enforcement**: Implicit (enforced by Ruff UP rules + mypy + convention)
- **Priority**: Must-have

#### NFR-019: CI Pipeline: Lint, Typecheck, Test (Fail Fast)

The system shall provide a `make ci` target that runs lint, typecheck, then test in that order. Individual `make lint`, `make typecheck`, `make test`, and `make test-cov` targets shall be available.

- **Source**: Python (13.1, 13.2)
- **Evidence**: `Makefile:1-28`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-020: Pytest with Class-Based Organization and Fixtures

Tests shall use pytest with class-based grouping, typed fixtures, generator fixtures for setup/teardown, and branch coverage. asyncio_mode shall be "auto".

- **Source**: Python (5.1-5.5)
- **Evidence**: `pyproject.toml:141-161`; `tests/conftest.py:22-78`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-021: Explicit Public API via __all__

The package shall export only `Issue`, `KeelDB`, and `__version__` via `__all__` in `__init__.py`. Private methods shall use `_` prefix.

- **Source**: Python (6.1, 6.2)
- **Evidence**: `src/keel/__init__.py:1-7`
- **Enforcement**: Explicit
- **Priority**: Must-have

### 3.6 Portability

#### NFR-022: Python >= 3.11 Required

The system shall require Python 3.11 or higher. Classifiers shall declare support for 3.11, 3.12, and 3.13.

- **Source**: Architect (REQ-DEP07), Python (1.1)
- **Evidence**: `pyproject.toml:9,18-20,76,119`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-023: Minimal Core Dependencies (Click Only)

The base installation shall require only `click>=8.0`. MCP (`mcp>=1.0,<2`) and Dashboard (`fastapi>=0.115`, `uvicorn>=0.34`) shall be optional extras. Core functionality shall use stdlib only (sqlite3, json, pathlib, dataclasses).

- **Source**: Architect (REQ-DEP02), Python (7.1, 7.2)
- **Evidence**: `pyproject.toml:23-44`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-024: Hatchling Build Backend with Src-Layout

The system shall use hatchling as the PEP 517 build backend with a `src/keel/` package layout.

- **Source**: Python (8.1, 8.2)
- **Evidence**: `pyproject.toml:1-3,52`
- **Enforcement**: Explicit
- **Priority**: Must-have

#### NFR-025: UV Package Manager with PEP 735 Dependency Groups

Development dependencies shall use PEP 735 dependency groups managed by UV. `uv.lock` shall be committed.

- **Source**: Python (7.3)
- **Evidence**: `pyproject.toml:58-69`
- **Enforcement**: Explicit
- **Priority**: Must-have

---

## 4. Architectural Constraints

#### AC-001: Single SQLite File as Sole Persistence Layer

All data shall be stored in a single SQLite file (`.keel/keel.db`). No external databases, services, or daemons shall be required. CLI, MCP server, and Dashboard shall interact only through this shared file.

- **Source**: Architect (REQ-I01), Systems (coupling analysis), Docs (1.2)
- **Evidence**: `src/keel/core.py` module docstring: "No daemon, no sync -- just direct SQLite with WAL mode"
- **Priority**: Must-have

#### AC-002: WAL Mode with 5-Second Busy Timeout

The database shall use WAL (Write-Ahead Logging) mode for concurrent reads with serialized writes. Busy timeout shall be 5000ms. Isolation level shall be DEFERRED.

- **Source**: Architect (REQ-C01, REQ-C02, REQ-C06), Systems (SR6, BC3)
- **Evidence**: `src/keel/core.py:493-498`
- **Priority**: Must-have

#### AC-003: Single Connection Per KeelDB Instance (No Pooling)

Each KeelDB instance shall hold a single `sqlite3.Connection`, created lazily. There shall be no connection pool, no thread-local connections, and no connection-per-request model.

- **Source**: Architect (REQ-C05)
- **Evidence**: `src/keel/core.py:467,488-499`
- **Priority**: Must-have (architectural choice)
- **Note**: Dashboard uses FastAPI (async) with a single KeelDB instance in a module global. Concurrent async handlers share the same connection, mitigated by single-worker uvicorn.

#### AC-004: MCP Server Uses stdio Transport

The MCP server shall use stdio (stdin/stdout) as its transport layer. No HTTP endpoint shall be exposed by the MCP server.

- **Source**: Architect (REQ-DEP09)
- **Evidence**: `src/keel/mcp_server.py:971-972`
- **Priority**: Must-have

#### AC-005: Dashboard Is Read-Only

The dashboard shall only call read operations on KeelDB. It shall never call create, update, close, or claim operations. Direct SQL access shall also be read-only.

- **Source**: Architect (REQ-I02), UX (REQ-8.2.2)
- **Evidence**: `src/keel/dashboard.py:47-104` -- all endpoints call read-only methods
- **Priority**: Must-have (deliberate design decision)

#### AC-006: .keel/ Directory Excluded from Version Control

The `.keel/` directory shall be added to `.gitignore`. Doctor shall check for this.

- **Source**: Architect (REQ-DEP05)
- **Evidence**: `src/keel/install.py:238-254,392-415`
- **Priority**: Must-have

#### AC-007: Module-Level Globals for MCP Server State

The MCP server shall store the KeelDB instance and keel_dir as module-level globals, set once during `_run()` and used by all tool handlers.

- **Source**: Architect (REQ-I04)
- **Evidence**: `src/keel/mcp_server.py:48-50,946-963`
- **Priority**: Must-have (architectural choice)

#### AC-008: Three Entry Points for Three Audiences

The system shall provide three console scripts: `keel` (CLI for humans), `keel-mcp` (MCP server for agents), `keel-dashboard` (web UI for visualization).

- **Source**: Architect (REQ-DEP03)
- **Evidence**: `pyproject.toml:47-49`
- **Priority**: Must-have

---

## 5. Interface Requirements

### 5.1 MCP Server Interface

#### IR-001: 30+ Tools with Verb-First Underscore Naming

MCP tools shall use underscore_case with verb-first structure (e.g., `get_issue`, `create_issue`, `add_dependency`). Tool names shall be semantically unambiguous.

- **Source**: UX (REQ-1.1.1, REQ-1.1.2, REQ-1.6.1)
- **Evidence**: All tools in `src/keel/mcp_server.py:176-634`
- **Priority**: Must-have

#### IR-002: Agent-Optimized Tool Descriptions

Every MCP tool shall have an action-oriented, specific description. Descriptions shall specify outputs and mention edge cases where relevant. Parameter descriptions shall specify constraints inline (e.g., "Priority 0-4 (0=critical)").

- **Source**: UX (REQ-1.4.1, REQ-1.4.2), Docs (3.1, 3.2, 3.3)
- **Evidence**: `src/keel/mcp_server.py:176-634` -- 30/30 tools with descriptions
- **Priority**: Must-have

#### IR-003: Canonical Workflow Prompt and Context Resource

The MCP server shall provide a `keel-workflow` prompt defining a 5-step agent workflow (read context, get ready, claim, work, close). It shall also provide a `keel://context` resource for single-read project state.

- **Source**: UX (REQ-1.4.3, REQ-1.4.4, REQ-8.1.3), Docs (6.4)
- **Evidence**: `src/keel/mcp_server.py:103-134` (workflow prompt); resource definition
- **Priority**: Must-have

### 5.2 CLI Interface

#### IR-004: Natural Verb Phrases with Hyphenated Multi-Word Commands

CLI commands shall use natural verb phrases (e.g., `create`, `show`, `close`, `ready`). Multi-word commands shall use hyphens (e.g., `dep-add`, `critical-path`). Short flags shall exist for common options (-p, -d, -f, -l).

- **Source**: UX (REQ-2.1.1, REQ-2.1.2, REQ-2.2.1)
- **Evidence**: `src/keel/cli.py` command definitions
- **Priority**: Must-have

#### IR-005: JSON Output Mode on Read Commands

Read commands (show, list, ready, stats, search, plan, metrics, critical-path) shall support a `--json` flag for programmatic output.

- **Source**: UX (REQ-2.2.3)
- **Evidence**: `src/keel/cli.py` -- `--json` flag declarations
- **Priority**: Must-have

#### IR-006: Compact List Format with Priority Prefix

List output shall use the format: `P{priority} {id} [{type}] {status} "{title}"`. Ready issues shall have an asterisk suffix ` *`.

- **Source**: UX (REQ-2.3.1, REQ-2.3.2)
- **Evidence**: `src/keel/cli.py:247-248`
- **Priority**: Must-have

### 5.3 Dashboard Interface

#### IR-007: Two View Modes (Graph and Kanban) with URL Hash State

The dashboard shall provide Graph view and Kanban view (with Standard and Cluster sub-modes). View state shall persist via URL hash.

- **Source**: UX (REQ-3.1.1, REQ-3.1.2, REQ-3.1.3)
- **Evidence**: `src/keel/static/dashboard.html` toggle buttons, hash state management
- **Priority**: Must-have

#### IR-008: Multi-Dimensional Visual Encoding

The dashboard shall encode: priority via colored dot (P0=red, P1=orange, P2=gray), status via background color, ready status via green left border, type via emoji icon, node size inversely proportional to priority in graph view, and node shape by type.

- **Source**: UX (REQ-3.2.1 through REQ-3.2.4, REQ-3.3.2, REQ-3.3.3, REQ-8.2.3)
- **Evidence**: `src/keel/static/dashboard.html` -- PRIORITY_COLORS, STATUS_COLORS, TYPE_ICONS
- **Priority**: Must-have

#### IR-009: DAG Layout with Blocker-to-Blocked Edge Direction

The graph shall use dagre DAG layout with top-to-bottom rank direction. Edges shall point from blocker to blocked issue.

- **Source**: UX (REQ-3.3.1, REQ-3.3.4)
- **Evidence**: `src/keel/static/dashboard.html` -- `layout: { name: 'dagre', rankDir: 'TB' }`
- **Priority**: Must-have

#### IR-010: Auto-Refresh on Tab Visibility Change

The dashboard shall refresh data when the browser tab becomes visible.

- **Source**: UX (REQ-3.8.1)
- **Evidence**: `src/keel/static/dashboard.html` -- `visibilitychange` event listener
- **Priority**: Should-have

### 5.4 Summary (context.md) Interface

#### IR-011: Agent-Scannable Markdown with Unicode Progress Indicators

The summary shall use markdown with section headers, tables, bullet lists, and Unicode progress bars (filled: `\u2588`, empty: `\u2591`). Symbols shall include checkmark, play arrow, circle, and left-arrow for status encoding.

- **Source**: Docs (7.1, 7.3), UX (REQ-4.1.2)
- **Evidence**: `src/keel/summary.py:26-211`
- **Priority**: Must-have

#### IR-012: Capped Information Density with Priority-Based Truncation

The summary shall show at most 15 ready issues, 10 blocked issues, 15 epics, and 10 recent events. Overflow shall show "...and N more". Items shall be sorted by priority (P0 first).

- **Source**: Systems (SR5, BC4), UX (REQ-4.1.5, REQ-4.1.7)
- **Evidence**: `src/keel/summary.py:95-97,131-146,152-177,189-209`
- **Priority**: Must-have

---

## 6. Cross-Cutting Concerns

### 6.1 Feedback Loops

#### CC-001: Agent-Summary Amplification Loop (Reinforcing)

The dominant system dynamic is the reinforcing loop: DB state -> summary generation -> agent reads summary -> agent acts -> DB mutations -> summary regeneration. More agent activity produces a fresher summary, which enables better agent decisions and more activity.

- **Source**: Systems (R1), UX (REQ-4.2.1), Architect (REQ-I03)
- **Evidence**: `src/keel/mcp_server.py:60-63` (refresh), `src/keel/summary.py:214-220` (atomic write)
- **Impact**: This loop is the system's core value proposition. It also creates tight coupling between summary generation and all mutation operations.

#### CC-002: Ready/Blocked Balancing Loop

The system naturally seeks equilibrium where blockers are resolved and issues become ready. Closing blockers causes previously-blocked issues to become ready, driving the next cycle of work.

- **Source**: Systems (B1), Architect (REQ-I05)
- **Evidence**: `src/keel/core.py:1010-1033` (ready/blocked queries), `src/keel/mcp_server.py:716-730` (newly-unblocked)
- **Impact**: Positive -- enables autonomous agent work chaining.

#### CC-003: Claim/Release Coordination Loop (Balancing)

Optimistic locking prevents double-work by ensuring only one agent claims each issue. This balancing loop seeks one-agent-per-issue equilibrium.

- **Source**: Systems (B2), Architect (REQ-C03), UX (REQ-1.5.4)
- **Evidence**: `src/keel/core.py:805-845`
- **Impact**: Works well in serial. At scale (10+ concurrent agents), claim conflicts may increase.

### 6.2 Emergent Behaviors

#### CC-004: Summary Becomes De Facto Source of Truth

Agents read the summary, not raw DB state. Over time, summary format shapes what agents prioritize. Issues not visible in the summary (below top-15 ready, below top-10 blocked) become effectively invisible to agents.

- **Source**: Systems (EB1, BC4)
- **Evidence**: `src/keel/mcp_server.py:103-134` (workflow says "read context first")
- **Impact**: Positive for coordination; negative for issue visibility at scale.

#### CC-005: Dependency Graphs Create Natural Work Phases

Even without explicit phase issues, dependency structure creates temporal ordering. Issues with no deps become "Phase 1"; their dependents become "Phase 2", and so on.

- **Source**: Systems (EB2)
- **Evidence**: `src/keel/core.py:1037-1098` (critical path reveals implicit phasing)
- **Impact**: Positive -- self-organizing work sequencing.

#### CC-006: Template-Driven Standardization

Templates define expected fields per issue type. Over time, agents converge on template-compliant issues. This creates a reinforcing standardization loop.

- **Source**: Systems (Dynamic 3), Architect (REQ-D10)
- **Evidence**: `src/keel/core.py:265-377` (templates), `src/keel/mcp_server.py:357-370` (get_template tool)
- **Impact**: Positive -- increased consistency. Risk: template changes could orphan old issues.

### 6.3 Cross-Interface Consistency

#### CC-007: Vocabulary Alignment Across All Interfaces

Status names (`open`, `in_progress`, `closed`), priority scale (0-4, 0=critical), type vocabulary, dependency semantics ("A depends on B" means "B blocks A"), ID format (`{prefix}-{6hex}`), and "ready" definition (open + no blockers) shall be identical across MCP, CLI, Dashboard, and Summary.

- **Source**: UX (REQ-6.1.1 through REQ-6.2.3)
- **Evidence**: Consistent usage across `src/keel/core.py`, `src/keel/cli.py`, `src/keel/mcp_server.py`, `src/keel/summary.py`, `src/keel/static/dashboard.html`
- **Priority**: Must-have

### 6.4 Documentation as System Component

#### CC-008: Dual-Audience Documentation Culture

All documentation shall serve both developers (module docstrings, inline comments, section dividers) and agents (MCP tool descriptions, summary format, KEEL_INSTRUCTIONS block). Every module shall have a docstring. All public functions shall have docstrings. Comments shall explain "why", not "what".

- **Source**: Docs (1.1, 1.2, 2.1, 2.3, 5.1, 5.2, 5.3, 11.2), Python (12.1, 12.2)
- **Evidence**: 100% module docstring coverage, 100% public function docstring coverage, 16.9% average documentation density
- **Priority**: Must-have

---

## 7. Conflict Resolution

### C-01: Template Enforcement -- Advisory vs. Validating

**Conflict**: Architect (REQ-D10) identifies that templates are advisory with no runtime enforcement. UX (REQ-1.5.3) presents `get_template` as enabling "type-specific field discovery" for agents. Docs (9.1) documents templates as defining "expected fields."

**Positions**:
- Architect: Templates are structurally advisory -- no validation code exists
- UX: Templates guide agent behavior through discoverability
- Docs: Templates document expectations without enforcement

**Resolution**: All three are correct and consistent. Templates are advisory by design. The guidance model (agents query templates, then voluntarily comply) is intentional. This is not a conflict but a deliberate soft constraint. **Requirement FR-009 captures this as "advisory."**

---

### C-02: Custom Workflow States vs. MCP Schema Enum

**Conflict**: Architect (REQ-E01, OBS-02) identifies that custom workflow states are supported at the core layer, but MCP tool schemas hardcode `"enum": ["open", "in_progress", "closed"]` for status parameters. An agent using a custom state would be blocked by JSON schema validation before the tool is called.

**Positions**:
- Architect: This is a gap -- the MCP schema contradicts the core capability
- UX: MCP schema provides helpful constraints for default workflows
- Systems: Custom workflow states are a secondary use case (most projects use defaults)

**Resolution**: This is a genuine design tension. The core supports custom states, but the MCP interface restricts them. For v1.0, this is acceptable because custom workflow states are an advanced feature and the default states cover the majority of use cases. **Tracked as an open question in Section 8.**

---

### C-03: Summary Regeneration -- Feature vs. Systemic Risk

**Conflict**: Systems Thinker identifies synchronous summary regeneration as a "Shifting the Burden" archetype with scaling risks. UX and Docs identify it as a critical feature enabling agent coordination.

**Positions**:
- Systems: Tight coupling creates single point of failure; at scale (>5000 issues, >10 concurrent agents), regeneration time becomes a bottleneck. No degraded mode exists.
- UX: Summary freshness is essential for agent decision quality. Agents depend on it for session start and work discovery.
- Docs: Summary is well-documented and a key differentiator.

**Resolution**: Both perspectives are valid at different scales. At current scale (~600 issues, 1-2 agents), synchronous regeneration is a net positive. At projected scale (5000+ issues, 10+ agents), it becomes a constraint. The current design is correct for v1.0. The systems risk is real but does not require immediate action. **Requirement FR-029 captures the current behavior; NFR-005 captures the performance constraint; Gap G-05 tracks the scaling concern.**

---

### C-04: Description/Notes Event Recording

**Conflict**: Architect (REQ-D08 note, OBS-04) identifies that description and notes changes do not generate events, while all other mutations do.

**Positions**:
- Architect: This is an inconsistency in the event sourcing model
- Docs: Not explicitly addressed

**Resolution**: This is an implicit design choice, likely to avoid storing large text diffs in the events table. The omission is consistent with the event system's focus on scalar state transitions. **Tracked as an observation under FR-007 and as a gap in Section 8.**

---

### C-05: Batch Operations Atomicity

**Conflict**: Architect (OBS-03) identifies that `batch_close` and `batch_update` loop through individual operations with individual commits, making them non-atomic.

**Positions**:
- Architect: A crash mid-loop results in partial updates
- UX: Batch operations reduce tool call overhead (a feature)

**Resolution**: The current behavior is a pragmatic trade-off. True atomicity would require wrapping all operations in a single transaction, which conflicts with the per-operation event recording and summary refresh pattern. **Documented under FR-025 with a note about the atomicity limitation.**

---

## 8. Gaps and Open Questions

### G-01: No Coverage Threshold

**Identified by**: Python specialist
**Description**: No minimum coverage percentage is configured in `pyproject.toml`. Branch coverage is enabled, but there is no enforcement of a coverage floor.
**Impact**: Coverage could regress silently.
**Recommendation**: Define a minimum coverage threshold (e.g., 80%).

### G-02: No Undo/Rollback Mechanism

**Identified by**: UX specialist (REQ-8.3.1)
**Description**: All mutations are immediate and irreversible. There is no confirmation prompt for destructive operations (close, batch_close) and no undo capability.
**Impact**: Accidental bulk closures or status changes cannot be reversed.
**Recommendation**: Evaluate need for soft-delete or undo buffer for high-risk operations.

### G-03: No Automatic Stale-Claim Release

**Identified by**: Systems specialist (EB4)
**Description**: In-progress issues older than 3 days are flagged in the summary but not automatically released. An agent that crashes after claiming an issue leaves it in limbo indefinitely.
**Impact**: Blocked dependent issues cannot proceed without manual intervention via `release_claim`.
**Recommendation**: Consider a configurable auto-release timeout.

### G-04: No Load Testing or Performance Benchmarks

**Identified by**: All specialists (various gaps)
**Description**: No empirical measurements exist for summary generation time at scale, concurrent agent behavior, or dependency graph BFS performance at high issue counts.
**Impact**: Cannot predict when performance limits will be reached.
**Recommendation**: Benchmark summary generation at 100, 500, 1k, 5k, 10k issues. Stress test concurrent claims with 5-50 agents.

### G-05: Summary Scaling Concern (Shifting the Burden)

**Identified by**: Systems specialist (Archetype analysis)
**Description**: Summary generation is O(N) with issue count. At ~5000 issues, generation may exceed agent decision cycle time. No monitoring, circuit breaker, or degraded mode exists.
**Impact**: System performance degrades linearly; no warning before hitting limit.
**Recommendation**: Add monitoring for summary generation time. Consider incremental or async summary updates for future versions.

### G-06: MCP Status Enum Conflicts with Custom Workflow

**Identified by**: Architect (OBS-02)
**Description**: MCP tool schemas for `list_issues` and `update_issue` include `"enum": ["open", "in_progress", "closed"]` for status, which prevents agents from using custom workflow states.
**Impact**: Custom workflow states are a core capability that cannot be used through the primary (MCP) interface.
**Recommendation**: Replace enum with description-based guidance, or dynamically generate schema from configured workflow states.

### G-07: Parent_id Foreign Key Dropped in v3 Migration

**Identified by**: Architect (REQ-R01 note, REQ-D06)
**Description**: The v3 migration recreates the issues table without `REFERENCES issues(id)` on `parent_id`. Post-v3, parent_id referential integrity is not enforced at the database level.
**Impact**: Orphaned parent references could be created without error.
**Recommendation**: Evaluate whether application-layer validation is sufficient or if the FK should be restored.

### G-08: Description/Notes Changes Have No Audit Trail

**Identified by**: Architect (OBS-04)
**Description**: `update_issue` records events for title, status, priority, and assignee changes, but silently applies description and notes changes with no event record.
**Impact**: Cannot track who changed a description or when.
**Recommendation**: Evaluate whether text-change events should be recorded (possibly without diff content to avoid bloat).

### G-09: Dashboard Scalability Unknown

**Identified by**: UX (GAP-10.1), Systems (BC4)
**Description**: Dashboard fetches all issues (limit 10000) on page load. Performance with >1000 issues is untested.
**Impact**: Browser may become unresponsive with large issue counts.
**Recommendation**: Test dashboard with 1000+ issues. Consider server-side pagination or virtualized rendering.

### G-10: No Pre-Commit Hooks

**Identified by**: Python specialist (Gap 2)
**Description**: No `.pre-commit-config.yaml` exists. `make ci` must be run manually.
**Impact**: Developers may push code that fails CI checks.
**Recommendation**: Consider adding pre-commit hooks that mirror `make ci`.

### G-11: No Docstring Convention Specified

**Identified by**: Python specialist (Gap 4)
**Description**: No explicit docstring convention (Google, NumPy, Sphinx) is configured. Complex functions use informal `Args:/Returns:` blocks.
**Impact**: Inconsistent documentation format.
**Recommendation**: Document the chosen convention (currently closest to Google style).

### G-12: Summary Format Not Versioned

**Identified by**: Docs specialist (Risk 12.2)
**Description**: The `context.md` format is code-defined but not specification-documented. If the format changes, agents with cached expectations could misparse it.
**Impact**: Low currently (agents read fresh each session), but increases if summary format becomes a stable interface.
**Recommendation**: Document summary schema if it becomes a public API.

### G-13: ID Generation Full-Table Scan

**Identified by**: Architect (REQ-P07, OBS-05)
**Description**: `create_issue` loads ALL existing issue IDs into a Python set for collision checking. At 10K+ issues, this is a per-create-call table scan plus memory allocation.
**Impact**: Performance degrades linearly with issue count.
**Recommendation**: Use EXISTS query instead of loading all IDs into memory.

### G-14: Concurrent Agent Retry Storms

**Identified by**: Systems specialist (EB3)
**Description**: With 10+ concurrent agents, claim conflicts increase. No retry logic or exponential backoff exists in MCP server or CLI. Conflicts are returned as errors for agents to handle.
**Impact**: At scale, significant wasted retries and summary thrashing.
**Recommendation**: Document recommended retry strategy for agents. Consider adding server-side backoff hints.

---

## 9. Traceability Matrix

The following table maps each requirement ID to its source specialist position paper(s).

| Req ID | Architect | Python | UX | Systems | Docs |
|--------|:---------:|:------:|:--:|:-------:|:----:|
| **Functional Requirements** | | | | | |
| FR-001 | X | X | | | |
| FR-002 | X | | | | |
| FR-003 | X | | X | | |
| FR-004 | X | | X | | |
| FR-005 | X | | | X | |
| FR-006 | X | | | | |
| FR-007 | X | | | X | X |
| FR-008 | X | | | | |
| FR-009 | X | | | | X |
| FR-010 | X | | | | |
| FR-011 | X | | | | |
| FR-012 | X | | X | X | |
| FR-013 | X | | | | |
| FR-014 | X | | X | | |
| FR-015 | X | | | | |
| FR-016 | X | | X | X | |
| FR-017 | X | | | | |
| FR-018 | | | X | | |
| FR-019 | X | | X | | |
| FR-020 | X | | | X | |
| FR-021 | X | | X | X | |
| FR-022 | X | | | | |
| FR-023 | X | | | X | |
| FR-024 | X | X | | | |
| FR-025 | | | X | | |
| FR-026 | X | | | | X |
| FR-027 | | | X | X | |
| FR-028 | | | X | X | X |
| FR-029 | X | | X | X | X |
| FR-030 | | | X | | |
| FR-031 | X | X | | | X |
| FR-032 | X | | X | | X |
| FR-033 | | | X | | X |
| FR-034 | X | | X | | X |
| FR-035 | X | | | | |
| **Non-Functional Requirements** | | | | | |
| NFR-001 | X | | | | |
| NFR-002 | X | | | | |
| NFR-003 | X | | | X | |
| NFR-004 | X | | | X | |
| NFR-005 | | | | X | |
| NFR-006 | X | | | | |
| NFR-007 | X | | | | |
| NFR-008 | X | | | | |
| NFR-009 | X | | | | |
| NFR-010 | | X | | | |
| NFR-011 | | X | | | |
| NFR-012 | X | | | | |
| NFR-013 | | | X | | |
| NFR-014 | | | X | | X |
| NFR-015 | | | X | | |
| NFR-016 | | X | | | |
| NFR-017 | | X | | | |
| NFR-018 | | X | | | |
| NFR-019 | | X | | | |
| NFR-020 | | X | | | |
| NFR-021 | | X | | | |
| NFR-022 | X | X | | | |
| NFR-023 | X | X | | | |
| NFR-024 | | X | | | |
| NFR-025 | | X | | | |
| **Architectural Constraints** | | | | | |
| AC-001 | X | | | X | X |
| AC-002 | X | | | X | |
| AC-003 | X | | | | |
| AC-004 | X | | | | |
| AC-005 | X | | X | | |
| AC-006 | X | | | | |
| AC-007 | X | | | | |
| AC-008 | X | | | | |
| **Interface Requirements** | | | | | |
| IR-001 | | | X | | |
| IR-002 | | | X | | X |
| IR-003 | | | X | | X |
| IR-004 | | | X | | |
| IR-005 | | | X | | |
| IR-006 | | | X | | |
| IR-007 | | | X | | |
| IR-008 | | | X | | |
| IR-009 | | | X | | |
| IR-010 | | | X | | |
| IR-011 | | | | | X |
| IR-012 | | | | X | |
| **Cross-Cutting Concerns** | | | | | |
| CC-001 | X | | X | X | |
| CC-002 | X | | | X | |
| CC-003 | X | | X | X | |
| CC-004 | | | | X | |
| CC-005 | | | | X | |
| CC-006 | X | | | X | |
| CC-007 | | | X | | |
| CC-008 | | X | | | X |

### Source Coverage Summary

| Specialist | Requirements Sourced | Sole Source | Shared Source |
|------------|---------------------|-------------|---------------|
| Architect | 51 | 22 | 29 |
| Python | 19 | 11 | 8 |
| UX | 35 | 14 | 21 |
| Systems | 20 | 4 | 16 |
| Docs | 14 | 2 | 12 |

**Highest-corroborated requirements** (3+ specialists):
- FR-007 (Event sourcing): Architect + Systems + Docs
- FR-012 (Optimistic locking): Architect + UX + Systems
- FR-016 (Ready/blocked computation): Architect + UX + Systems
- FR-028 (Pre-computed summary): UX + Systems + Docs
- FR-029 (Summary regeneration): Architect + UX + Systems + Docs (4 sources)
- CC-001 (Agent-summary loop): Architect + UX + Systems
- CC-003 (Claim/release loop): Architect + UX + Systems

---

*End of Consensus Requirements Document*
*Generated: 2026-02-11*
*Input: 5 specialist position papers totaling 212 raw requirements*
*Output: 94 consensus requirements + 14 gaps + 5 conflict resolutions*
