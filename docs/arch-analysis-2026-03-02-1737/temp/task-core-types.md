## Core DB Layer

**Location:** `src/filigree/` (files: `core.py`, `db_base.py`, `db_issues.py`, `db_files.py`, `db_events.py`, `db_planning.py`, `db_meta.py`, `db_schema.py`, `db_workflow.py`)

**Responsibility:** Provides the single source of truth for all SQLite operations -- issue CRUD, file records, scan findings, event sourcing, dependency DAGs, workflow templates, and project metadata -- via a mixin-composed `FiligreeDB` class.

**Key Components:**
- `core.py` (461 lines) - Defines `FiligreeDB` (6-mixin diamond composition), dataclasses (`Issue`, `FileRecord`, `ScanFinding`), convention-based `.filigree/` discovery (`find_filigree_root`), config read/write, atomic file writes, and built-in pack seeding. Re-exports key types and constants from mixins for backward compatibility.
- `db_base.py` (39 lines) - `DBMixinProtocol`: a `typing.Protocol` declaring `conn`, `db_path`, `prefix`, `_conn`, `_template_registry`, `_enabled_packs_override`, and `get_issue()`. All 6 mixins inherit this Protocol so mypy can type-check `self.conn` and `self.get_issue()` without `type: ignore` annotations. Also provides `StatusCategory` Literal type and `_now_iso()` timestamp helper.
- `db_issues.py` (954 lines) - `IssuesMixin`: issue CRUD (`create_issue`, `get_issue`, `update_issue`, `close_issue`, `reopen_issue`), batch operations (`batch_close`, `batch_update`, `batch_add_label`, `batch_add_comment`), search (FTS5 with LIKE fallback), claiming with optimistic locking (`claim_issue`, `claim_next`, `release_claim`), field validation (patterns, uniqueness), and `_build_issues_batch` which eliminates N+1 queries via batched label/dep/children fetches.
- `db_files.py` (1241 lines) - `FilesMixin`: file registration with upsert-by-path, scan result ingestion (`process_scan_results` -- validates, deduplicates findings, auto-creates bug issues for high-severity findings, marks unseen findings), paginated file listing with severity/source filtering, finding CRUD, file-issue associations, file hotspot scoring (weighted severity), and merged file timeline (finding + association + metadata events with deterministic SHA-256 IDs).
- `db_events.py` (296 lines) - `EventsMixin`: `_record_event` (INSERT OR IGNORE with dedup index), event queries (`get_recent_events`, `get_events_since`, `get_issue_events`), `undo_last` (reverses the most recent reversible event using match/case on 9 event types), archival (`archive_closed` moves done-category issues older than N days to "archived"), compaction (`compact_events` prunes old events for archived issues), `vacuum`, and `analyze`.
- `db_planning.py` (575 lines) - `PlanningMixin`: dependency management (`add_dependency` with BFS cycle detection, `remove_dependency`), ready/blocked queries (open-category issues with/without non-done blockers), critical path computation (Kahn's algorithm topological sort + longest-path DP), plan tree CRUD (`create_plan` builds milestone->phase->step hierarchy in one transaction with cross-phase dependency wiring, `get_plan` returns tree with progress stats), and release tree queries (`get_releases_summary`, `get_release_tree` with recursive `_build_tree`).
- `db_meta.py` (334 lines) - `MetaMixin`: comments (add/get), labels (add/remove with normalization), aggregate stats (`get_stats` -- by status, category, type, ready/blocked counts), bulk insert methods for migration (`bulk_insert_issue`, `bulk_insert_dependency`, `bulk_insert_event`, `bulk_commit`), and JSONL export/import with per-record-type routing and merge mode.
- `db_schema.py` (281 lines) - `SCHEMA_SQL` (the canonical DDL for all 10 tables: `issues`, `dependencies`, `events`, `comments`, `labels`, `type_templates`, `packs`, `file_records`, `scan_findings`, `file_associations`, `file_events`) plus indexes (including composite indexes for status+priority, dedup unique indexes for events and findings), FTS5 virtual table with sync triggers, and `CURRENT_SCHEMA_VERSION = 5`. Also contains `SCHEMA_V1_SQL` for migration tests.
- `db_workflow.py` (250 lines) - `WorkflowMixin`: lazy-loaded `TemplateRegistry` via property (created on first access, avoids circular imports), template seeding, template retrieval (`get_template`, `list_templates`), status validation against type-specific states, parent_id validation, `_get_states_for_category` (collects all state names across enabled types for a given category), `_resolve_status_category` (template lookup with heuristic fallback), label name validation (rejects type names as labels), and template-aware queries (`get_valid_transitions`, `validate_issue`).

**Internal Architecture:**
The core pattern is **mixin composition via multiple inheritance**. `FiligreeDB` inherits from 6 mixins in this MRO order: `FilesMixin, IssuesMixin, EventsMixin, WorkflowMixin, MetaMixin, PlanningMixin`. Each mixin inherits `DBMixinProtocol`, a `typing.Protocol` that declares the shared interface (`conn`, `get_issue`, etc.) -- this exists purely for static analysis so mypy can validate cross-mixin method calls without every mixin importing every other mixin.

Cross-mixin calls are resolved at runtime through Python's MRO. Each mixin declares TYPE_CHECKING-only stubs for methods it calls from other mixins (e.g., `IssuesMixin` declares stubs for `_record_event` from `EventsMixin` and `templates` from `WorkflowMixin`). This creates a web of implicit inter-mixin dependencies:

- `IssuesMixin` depends on: `EventsMixin._record_event`, `WorkflowMixin.templates/_validate_status/_validate_parent_id/_validate_label_name/_get_states_for_category/_resolve_status_category`, `MetaMixin.add_label/add_comment`, `PlanningMixin.get_ready/get_valid_transitions`
- `FilesMixin` depends on: `IssuesMixin._generate_unique_id/create_issue`
- `EventsMixin` depends on: `WorkflowMixin._resolve_status_category/_get_states_for_category`
- `PlanningMixin` depends on: `EventsMixin._record_event`, `WorkflowMixin.templates/_get_states_for_category`, `IssuesMixin._generate_unique_id/_build_issues_batch/list_issues`
- `MetaMixin` depends on: `WorkflowMixin._validate_label_name/_validate_parent_id/_resolve_status_category`, `PlanningMixin._resolve_open_done_states`
- `WorkflowMixin` depends on: none (base provider of template operations)

The SQLite connection is lazily created via a `conn` property on `FiligreeDB` that configures WAL journal mode, foreign keys ON, and 5-second busy timeout. Schema initialization follows a version-stamped approach: `PRAGMA user_version` tracks the current version, fresh databases get the full DDL from `SCHEMA_SQL`, and existing databases run pending migrations from `filigree.migrations`.

The event system functions as a lightweight event-sourcing pattern: every state mutation (status change, title change, priority change, assignee change, dependency add/remove, etc.) records an event row with old_value/new_value. The dedup unique index on events (`issue_id, event_type, actor, old_value, new_value, created_at`) prevents duplicate events. The `undo_last` method finds the most recent reversible event and applies the inverse operation via direct SQL (bypassing validation for status undos).

Data models have a dual representation: runtime `@dataclass` objects (`Issue`, `FileRecord`, `ScanFinding`) with `to_dict()` methods that produce TypedDicts (`IssueDict`, `FileRecordDict`, `ScanFindingDict`). The TypedDicts serve as the wire-format contract consumed by MCP tool handlers and dashboard routes.

**Dependencies:**
- Inbound: `dashboard.py`, `dashboard_routes/` (issues, files, analytics, releases, common), `mcp_server.py`, `mcp_tools/` (issues, files, planning, meta, workflow, common), `server.py`, `cli_common.py`, `cli_commands/admin.py`, `analytics.py`, `summary.py`, `hooks.py`, `ephemeral.py`, `migrate.py`, `install_support/doctor.py` -- effectively the entire application layer depends on this subsystem.
- Outbound: `filigree.templates` (TemplateRegistry, validate_field_pattern, TransitionOption, ValidationResult), `filigree.templates_data` (BUILT_IN_PACKS for seeding), `filigree.migrations` (apply_pending_migrations), `filigree.types.core` (IssueDict, FileRecordDict, ScanFindingDict, PaginatedResult, ProjectConfig, ISOTimestamp), `filigree.types.events` (EventRecord, EventRecordWithTitle, UndoResult), `filigree.types.files` (ScanIngestResult, FileDetail, FileAssociation, etc.), `filigree.types.planning` (CriticalPathNode, DependencyRecord, PlanTree, PlanPhase, CommentRecord, StatsResult), `filigree.types.workflow` (TemplateInfo, StateInfo, TransitionInfo, FieldSchemaInfo, TemplateListItem).

**Patterns Observed:**
- **Mixin composition with Protocol-based type safety**: 6 mixins inherit a shared `DBMixinProtocol` to satisfy mypy without circular imports. Cross-mixin method stubs are declared under `TYPE_CHECKING` guards.
- **Lazy connection initialization**: SQLite connection created on first `conn` property access with WAL mode, foreign keys, and busy timeout.
- **Event sourcing for undo**: Every mutation records old/new values in an events table; `undo_last` uses pattern matching to apply inverse operations.
- **Dedup-safe idempotent writes**: `INSERT OR IGNORE` used throughout for events, labels, dependencies, and file associations, backed by unique indexes.
- **Batch query elimination of N+1**: `_build_issues_batch` fetches labels, blocks, blocked_by, children, and open blocker counts in 6 batched queries instead of per-issue.
- **FTS5 with graceful degradation**: Full-text search uses FTS5 with trigger-based sync; falls back to LIKE queries if FTS5 is unavailable.
- **Version-stamped schema migration**: `PRAGMA user_version` tracks schema version; `initialize()` applies pending migrations or creates fresh schema.
- **Optimistic locking for claim**: `claim_issue` uses a single atomic `UPDATE...WHERE` with guard conditions to prevent race conditions.
- **Convention-based discovery**: Walks up directory tree to find `.filigree/` directory, reads `config.json` for project configuration.
- **Deterministic IDs**: Issue IDs use `prefix-uuid[:10]` format with collision retry; file timeline entries use SHA-256 hashes for client-side dedup.

**Concerns:**
- The implicit cross-mixin dependency web (declared only via TYPE_CHECKING stubs) is fragile -- adding a new mixin or changing method signatures requires updating stubs in multiple files, and forgetting a stub only surfaces at runtime, not at import time.
- `db_files.py` at 1241 lines is the largest mixin and handles file records, scan findings, associations, and timeline -- it could be split into file-record and scan-findings sub-mixins.
- `_build_issues_batch` constructs SQL with f-string placeholder injection (`f"SELECT id FROM issues WHERE id IN ({placeholders})"`). While placeholders are `?`-only (safe), the pattern is error-prone if modified. The `_generate_unique_id` method uses f-string table name injection (`f"SELECT 1 FROM {table} WHERE id = ?"`) -- table names are hardcoded at call sites but the pattern is risky.
- `process_scan_results` in `db_files.py` is a ~250-line method handling validation, file upsert, finding upsert, issue creation, and mark-unseen logic in a single transaction -- high cyclomatic complexity.
- The `import_jsonl` method uses f-string for `INSERT {conflict}` where `conflict` is either "OR IGNORE" or "OR ABORT" -- safe since it is derived from a boolean, but unconventional.
- Several `list_issues` category aliases are hardcoded (e.g., `"in_progress": "wip"`, `"closed": "done"`) rather than derived from template metadata.

**Confidence:** High - Read 100% of all 9 source files (4431 lines total). Cross-verified mixin dependency claims by reading TYPE_CHECKING stubs in each mixin. Verified SQLite patterns (WAL, foreign keys, busy timeout) in `conn` property. Confirmed schema tables and indexes by reading `db_schema.py` in full. Validated inbound dependencies via grep across `src/filigree/`. Confirmed types subsystem usage by checking imports in every mixin file.

---

## Type System

**Location:** `src/filigree/types/`

**Responsibility:** Provides TypedDict-based return-value contracts and input-argument schemas that form the API boundary between the core DB layer, MCP tool handlers, and dashboard routes -- without importing from any core or DB module, preventing circular imports.

**Key Components:**
- `__init__.py` (163 lines) - Re-export hub: imports all TypedDicts from the 6 sub-modules and exposes them via `__all__` (83 exported names). Enforces the import constraint via a comment-level directive: "types/ modules must only import from typing, stdlib, and each other."
- `core.py` (85 lines) - Foundation types: `ISOTimestamp` (NewType over str), `ProjectConfig` (total=False TypedDict for `.filigree/config.json` shape), `PaginatedResult` (envelope for paginated queries), `IssueDict` (21-key shape of `Issue.to_dict()`), `FileRecordDict` (7-key shape of `FileRecord.to_dict()`), `ScanFindingDict` (16-key shape of `ScanFinding.to_dict()`). These are imported by every other types sub-module.
- `api.py` (366 lines) - MCP and dashboard response types: 42 TypedDicts organized into groups -- shared types (`TransitionDetail`, `SlimIssue`, `BlockedIssue`, `ErrorResponse`, `TransitionError`), flat-inheritance extensions of `IssueDict` (`IssueWithTransitions`, `IssueWithChangedFields`, `IssueWithUnblocked`, `ClaimNextResponse`, `EnrichedIssueDetail`), envelope types (`IssueListResponse`, `SearchResponse`, `BatchUpdateResponse`, `BatchCloseResponse`, `PlanResponse`), and handler-specific responses (`DependencyActionResponse`, `CriticalPathResponse`, `AddCommentResult`, `LabelActionResponse`, `JsonlTransferResponse`, `ArchiveClosedResponse`, `CompactEventsResponse`, `ClaimNextEmptyResponse`, `WorkflowStatesResponse`, `PackListItem`, `ValidationResult`, `WorkflowGuideResponse`, `StateExplanation`). Uses `NotRequired` for optional fields and a split-base pattern (`_BatchCloseRequired` + `BatchCloseResponse(total=False)`) to maintain required/optional key correctness.
- `events.py` (53 lines) - Event types: `EventRecord` (8-key row from events table), `EventRecordWithTitle` (extends with joined `issue_title`), `UndoSuccess` / `UndoFailure` (discriminated union via `Literal[True]`/`Literal[False]` on `undone` field), and `UndoResult` TypeAlias union.
- `files.py` (119 lines) - File domain types: `FileAssociation` (file-to-issue direction with joined title/status), `IssueFileAssociation` (issue-to-file direction with joined path/language), `SeverityBreakdown` (reusable severity-bucketed counts), `FindingsSummary` (extends SeverityBreakdown), `GlobalFindingsStats` (extends FindingsSummary), `HotspotFileRef`, `FileHotspot` (embedded ref + score + breakdown), `FileDetail` (file + associations + findings + summary), `ScanRunRecord`, `ScanIngestResult` (ingestion stats), `CleanStaleResult`.
- `inputs.py` (380 lines) - MCP tool argument types: 37 TypedDicts mirroring MCP JSON Schema `inputSchema` definitions, using `NotRequired` for optional fields. Organized by handler module (issues, meta, planning, workflow, files). Contains `TOOL_ARGS_MAP` -- a registry mapping 37 tool names to their TypedDict class. This registry enables sync-testing: a test can iterate over all registered tools and verify that the TypedDict's `__required_keys__` and `__optional_keys__` match the JSON Schema's `required` and `properties`. Note: intentionally does NOT use `from __future__ import annotations` because that breaks `__required_keys__` / `__optional_keys__` introspection on Python <3.14.
- `planning.py` (91 lines) - Planning and analytics types: `CriticalPathNode`, `DependencyRecord` (functional-form TypedDict because it uses `"from"` as a key -- a Python keyword), `PlanPhase`, `PlanTree`, `CommentRecord`, `StatsResult`, `TypeMetrics`, `FlowMetrics`.
- `workflow.py` (85 lines) - Workflow template types: `StateInfo`, `TransitionInfo` (functional-form for `"from"` key), `FieldSchemaInfo` (split-base pattern: `_FieldSchemaRequired` + optional extension), `TemplateInfo`, `TemplateListItem`, `TypeListItem`, `TypeInfoResponse` (extends TemplateInfo with `pack`).

**Internal Architecture:**
The types package is structured as a strict dependency DAG with `core.py` at the root. The internal import graph is:

```
core.py  (foundation -- no intra-package imports)
  |-- events.py     (imports ISOTimestamp, IssueDict from core)
  |-- files.py      (imports FileRecordDict, ISOTimestamp, ScanFindingDict from core)
  |-- planning.py   (imports ISOTimestamp, IssueDict from core)
  |-- workflow.py   (no intra-package imports -- standalone)
  |-- api.py        (imports from core + planning)
  `-- inputs.py     (no intra-package imports -- standalone)
```

The critical constraint is that no types module may import from `core.py`, `db_base.py`, or any mixin module. This is enforced by comment-level directives in `__init__.py` and `inputs.py`. The constraint exists because the DB mixins import from types modules for their return-type annotations -- if types imported back from DB modules, Python would hit circular import errors.

TypedDicts serve three distinct roles:
1. **Dataclass serialization contracts** (`core.py`): Shape of `to_dict()` returns. Each dataclass (`Issue`, `FileRecord`, `ScanFinding`) has a corresponding TypedDict (`IssueDict`, `FileRecordDict`, `ScanFindingDict`).
2. **API response envelopes** (`api.py`, `events.py`, `files.py`, `planning.py`, `workflow.py`): Shape of what MCP tool handlers and dashboard routes return. Many extend `IssueDict` via flat inheritance with `NotRequired` extras.
3. **Input argument schemas** (`inputs.py`): Shape of what MCP tools accept. The `TOOL_ARGS_MAP` registry enables automated verification that TypedDict shapes stay in sync with MCP JSON Schema definitions.

The package uses several TypedDict patterns worth noting:
- **Split-base pattern**: When a TypedDict needs both required and optional keys, a `_FooRequired(TypedDict)` base has the required keys, and the public `Foo(_FooRequired, total=False)` adds optional keys. Used by `BatchCloseResponse`, `FieldSchemaInfo`, and `WorkflowGuideResponse`.
- **Functional-form TypedDicts**: `DependencyRecord` and `TransitionInfo` use `TypedDict("Name", {"from": str, ...})` because `"from"` is a Python keyword that cannot be used as a class attribute name.
- **Discriminated unions**: `UndoResult = UndoSuccess | UndoFailure` uses `Literal[True]`/`Literal[False]` on the `undone` field for type narrowing.
- **Reserved extension keys**: `api.py` documents which key names are reserved and must never be added to `IssueDict` to prevent flat-inheritance conflicts.

**Dependencies:**
- Inbound: `core.py` (imports `IssueDict`, `FileRecordDict`, `ScanFindingDict`, `PaginatedResult`, `ProjectConfig`, `ISOTimestamp`), `db_events.py` (imports `EventRecord`, `EventRecordWithTitle`, `UndoResult`), `db_files.py` (imports `ScanIngestResult`, `FileDetail`, `FileAssociation`, etc.), `db_planning.py` (imports `CriticalPathNode`, `DependencyRecord`, `PlanPhase`, `PlanTree`), `db_meta.py` (imports `CommentRecord`, `StatsResult`), `db_workflow.py` (imports `TemplateInfo`, `StateInfo`, `TransitionInfo`, `FieldSchemaInfo`, `TemplateListItem`), `mcp_tools/` (imports api, inputs, core, workflow types), `dashboard_routes/` (imports api, core, planning types), `analytics.py` (imports `FlowMetrics`, `TypeMetrics`).
- Outbound: Python stdlib only (`typing`, `typing_extensions` via `NotRequired`). No imports from any `filigree.*` module outside `types/`. This is the critical architectural invariant.

**Patterns Observed:**
- **Zero-outbound-dependency constraint**: Types modules import only from stdlib and each other, preventing circular imports with the DB layer that imports from types for return annotations.
- **Split-base pattern for mixed required/optional TypedDicts**: Avoids the `total=False` inheritance trap where extending a TypedDict with `total=False` makes all inherited keys optional.
- **TOOL_ARGS_MAP registry for schema sync testing**: Maps tool names to TypedDict classes, enabling automated verification that MCP JSON Schema `inputSchema` definitions agree with TypedDict required/optional key sets.
- **Functional-form TypedDicts for Python-keyword keys**: `DependencyRecord` and `TransitionInfo` use `TypedDict("Name", {...})` to include `"from"` as a key name.
- **Discriminated union with Literal types**: `UndoResult` uses `Literal[True]`/`Literal[False]` for exhaustive pattern matching.
- **Deliberate omission of `from __future__ import annotations`** in `inputs.py` to preserve TypedDict introspection on Python <3.14.
- **Re-export hub in `__init__.py`**: Single import point for consumers, with 83-entry `__all__` list.

**Concerns:**
- The import constraint is enforced only by comments, not by tooling (e.g., no import linter rule or test that verifies types modules do not import from `core`/`db_*`). A careless import addition could introduce a circular import that only surfaces at runtime.
- `api.py` at 366 lines with 42 TypedDicts is approaching the point where it could benefit from being split by domain (issue responses, batch responses, workflow responses, file responses).
- The reserved extension keys for `IssueDict` flat inheritance are documented only in a comment block in `api.py` -- there is no runtime or test-time enforcement that these key names are never added to `IssueDict`.
- Some TypedDicts in `api.py` use `dict[str, Any]` for their `failed` fields (e.g., `BatchUpdateResponse.failed: list[dict[str, Any]]`), losing type safety on error shape structures.

**Confidence:** High - Read 100% of all 7 source files (1342 lines total). Verified the import constraint by checking every import statement in every types sub-module. Confirmed `TOOL_ARGS_MAP` covers 37 tools by reading the full registry. Cross-validated inbound dependency claims via grep across `src/filigree/`. Verified the split-base pattern in 3 TypedDicts and functional-form pattern in 2 TypedDicts.

---

### Confidence Assessment

Both subsystem analyses were performed at the highest evidence level. Every source file was read in its entirety (5,773 lines across 16 files). Cross-mixin dependency claims were verified by reading TYPE_CHECKING stubs. Import constraints were verified via ripgrep across the full `src/filigree/` tree. Schema claims were verified against the literal SQL in `db_schema.py`.

### Risk Assessment

- **Core DB Layer**: The mixin composition pattern is well-established but creates a hidden dependency web. Refactoring any mixin's public interface requires updating TYPE_CHECKING stubs across multiple files. The `db_files.py` mixin is disproportionately large (1241 lines vs 250-575 for others).
- **Type System**: The zero-outbound-dependency constraint is critical for preventing circular imports but is enforced only by convention. Any violation would cause runtime `ImportError` that tests may not catch if the import chain is conditional.

### Information Gaps

- The `filigree.templates` module (TemplateRegistry, validate_field_pattern) was not analyzed -- it is an outbound dependency of the Core DB Layer but outside the scope of this task.
- The `filigree.migrations` module was not analyzed -- it handles schema upgrades from version N to CURRENT_SCHEMA_VERSION.
- Whether the `TOOL_ARGS_MAP` sync test actually exists and runs in CI was not verified (the registry references it but the test file was not read).

### Caveats

- Line counts and file counts were measured on the `v1.4.0-architectural-refactor` branch as of 2026-03-02. These may differ on `main`.
- The mixin dependency graph was reconstructed from TYPE_CHECKING stubs, which represent the static-analysis view. Runtime behavior depends on MRO resolution, which could differ if mixins are composed in a different order.
