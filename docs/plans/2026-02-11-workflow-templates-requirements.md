# Workflow Templates — Requirements (Consensus)

**Date**: 2026-02-11
**Method**: Synthesis of 5 specialist position papers (Architecture, Python, UX, Systems, Documentation)
**Scope**: Workflow Templates extension to Keel v1.0
**Facilitator**: Requirements Engineer Agent
**Baseline**: `2026-02-11-keel-current-requirements.md` (94 requirements, v1.0)
**Design**: `2026-02-11-workflow-templates-design.md`

---

## 1. Executive Summary

### Totals

- **134 consensus requirements** distilled from ~293 raw requirements across 5 position papers + agent self-review
- **75 Functional Requirements** (WFT-FR-001 through WFT-FR-075)
- **18 Non-Functional Requirements** (WFT-NFR-001 through WFT-NFR-018)
- **12 Architectural Requirements** (WFT-AR-001 through WFT-AR-012)
- **14 Documentation Requirements** (WFT-DR-001 through WFT-DR-014)
- **15 Systemic Requirements** (WFT-SR-001 through WFT-SR-015)

### Key Themes Across All Specialists

1. **Phase transition, not incremental change**: All specialists identified that moving from 3 global states to 26 types with ~80 unique states is a qualitative shift. Systems calls it a "phase transition"; Architecture calls it the "single biggest behavioral change"; UX calls it a "10x increase in workflow complexity". The design must handle this discontinuity.

2. **Discovery over memorization**: UX, Systems, and Docs independently converged on the principle that agents must navigate workflows through contextual tools (`get_valid_transitions`, `explain_state`, `get_workflow_guide`) rather than pre-memorizing state machines.

3. **Template caching is the critical leverage point**: Architecture, Python, and Systems all identify template lookup performance as the make-or-break implementation detail. Without O(1) caching, the system hits scaling limits 40% sooner.

4. **Backward compatibility is the hardest constraint to verify**: Architecture identifies 12+ query paths that must be modified yet produce identical results for existing data. Python identifies the `WHERE status = 'open'` to `WHERE status IN (...)` expansion as touching every hot path. Systems quantifies the cost: summary generation increases ~70%.

5. **Documentation is a core feature, not an afterthought**: Docs specialist identifies workflow guides as the PRIMARY agent learning mechanism. All 9 packs require domain-expert-quality guides totaling 5,400-8,100 words. UX confirms guides are essential for the discovery-over-memorization strategy.

6. **Soft enforcement creates systemic risk**: Systems identifies the Eroding Goals archetype where soft warnings are systematically ignored. Architecture and UX both note the lack of warning-ignore tracking. The consensus is that soft enforcement must be measurable.

7. **Round-trip reduction is the dominant agent UX concern**: Agent self-review identified that the spec invests heavily in the template engine (127 requirements) but underspecifies the agent experience of using it. Every unnecessary round-trip, missing compound operation, or information gap in a response is a real cost. Key additions: atomic transition-with-fields (WFT-FR-069), include_transitions on get_issue (WFT-FR-070), claim_next (WFT-FR-072), batch operation semantics (WFT-FR-073).

### Critical Risks Requiring Design Revision

1. **TemplateRegistry-KeelDB circular dependency** (Architecture, Python): The design shows bidirectional dependency with no resolution. Requires architectural decision before implementation.
2. **close_issue() API contract for multi-done-state types** (Architecture, Python): 6 of 26 types have multiple done states. The current API cannot express which one to use.
3. **Pack disable behavior for existing issues** (Architecture, Systems, UX): Design does not specify whether existing issues of disabled-pack types can be updated.
4. **Category vs. state name disambiguation** (Architecture, Python): `open` is both a category and a literal state name for task/epic types.

### Conflicts Resolved

- **C-01**: Dataclass vs Pydantic for template data model (Section 8)
- **C-02**: Two-pass vs pre-computed category queries (Section 8)
- **C-03**: Template override merge semantics (Section 8)
- **C-04**: Soft warning event persistence (Section 8)
- **C-05**: State ordering for claim_issue (Section 8)

### Gaps Identified

- 10 design gaps requiring clarification before implementation (Section 7)
- 8 UX gaps where design specification is insufficient (Section 7)
- 5 information gaps where no empirical data exists (Section 7)

---

## 2. Functional Requirements

### 2.1 Template Engine

#### WFT-FR-001: TemplateRegistry Class

The system shall implement a `TemplateRegistry` class in `src/keel/templates.py` providing methods: `load()`, `get_type()`, `get_pack()`, `list_types()`, `list_packs()`, `validate_transition()`, `get_valid_transitions()`, `validate_fields_for_state()`, `get_initial_state()`, `get_category()`.

- **Source**: Architect (WFT-TE01), Python (REQ-WFT-PY-001)
- **Priority**: Must-have
- **Risk**: Medium (central component; correctness determines feature success)
- **Phase**: 1
- **Design ref**: Section 7.1

#### WFT-FR-002: Three-Layer Template Resolution

The system shall resolve templates from three layers with later layers overriding earlier: (1) Built-in templates from Python source, (2) Installed packs from `.keel/packs/*.json`, (3) Project-local overrides from `.keel/templates/*.json`. Last definition wins per type name via whole-document replacement.

- **Source**: Architect (WFT-TE02), Python (REQ-WFT-PY-005)
- **Priority**: Must-have
- **Risk**: High (three-layer ambiguity; database-vs-file authority unclear)
- **Phase**: 1
- **Design ref**: Section 5.1
- **Note**: Resolution is whole-document replacement, not field-level merge (see Conflict C-03).

#### WFT-FR-003: Template Loading Filtered by Enabled Packs

The system shall only make types from enabled packs available. The `enabled_packs` field in `config.json` controls which packs are active. Default: `["core", "planning"]`.

- **Source**: Architect (WFT-PS01), Python (REQ-WFT-PY-005), UX (REQ-WFT-8.2.1)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Section 5.2

#### WFT-FR-004: Template Loading Idempotent

The system shall make `TemplateRegistry.load()` idempotent: calling it multiple times shall not re-parse or re-load templates.

- **Source**: Python (REQ-WFT-PY-005)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

#### WFT-FR-005: Template Loading Error Resilience

The system shall log a warning and skip files with JSON parse errors during Layer 2/3 loading, rather than crashing. Missing `enabled_packs` in config shall default to `["core", "planning"]`.

- **Source**: Python (REQ-WFT-PY-005)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 1

### 2.2 State Machine

#### WFT-FR-006: Per-Type State Definitions

Each type template shall define its own valid states. States shall not be global. Each state shall map to one of three universal categories: `open`, `wip`, `done`.

- **Source**: Architect (WFT-SM01, WFT-TE03), UX (REQ-WFT-2.1.1), Systems (Section 7.1)
- **Priority**: Must-have
- **Risk**: High (touches every query using string-literal status comparisons)
- **Phase**: 1
- **Design ref**: Sections 2.2, 10.2

#### WFT-FR-007: Initial State Assignment per Type

`create_issue()` shall use `TemplateRegistry.get_initial_state()` to determine the starting state instead of hardcoding `'open'`. Types without templates shall fall back to `'open'`.

- **Source**: Architect (WFT-SM02, WFT-I02), Python (REQ-WFT-PY-011)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1
- **Design ref**: Section 7.2

#### WFT-FR-008: 26 Built-in Type State Machine Definitions

The system shall ship with 26 type definitions across 9 packs, each with complete state machines as specified in design Section 10.2. Each type shall include states with category mappings, transitions with enforcement levels, and field schemas with `required_at` declarations.

- **Source**: Architect (WFT-SM03, WFT-PS05), Python (REQ-WFT-PY-007), Docs (Section 1)
- **Priority**: Must-have
- **Risk**: Medium (large content authoring task; typos cause runtime errors)
- **Phase**: 2
- **Design ref**: Section 10.2

#### WFT-FR-009: Category-Aware Query Operations

`list_issues(status=)` shall accept both category names (`"open"`, `"wip"`, `"done"`) and specific state names (`"triage"`). When a category is provided, the system shall return all issues whose state maps to that category.

- **Source**: Architect (WFT-SM04), UX (REQ-WFT-2.1.2), Python (REQ-WFT-PY-015)
- **Priority**: Must-have
- **Risk**: Medium (disambiguation between category and state name needed)
- **Phase**: 1
- **Design ref**: Section 7.2

#### WFT-FR-010: Per-Type State Ordering

States within a type template shall be ordered by their position in the `states` array. The "first" state of a given category shall mean the first state in array order with that category. This ordering determines `claim_issue()` target state and `close_issue()` default target state.

- **Source**: Architect (WFT-I05 gap), Systems (SR-15), Python (REQ-WFT-PY-014)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1
- **Note**: Design gap -- "first wip state" ordering was not specified. Consensus: use array order.

### 2.3 Validation

#### WFT-FR-011: Transition Validation with Soft/Hard Enforcement

The system shall validate state transitions via `TemplateRegistry.validate_transition()`. Hard enforcement shall reject the operation (raise ValueError). Soft enforcement shall allow the operation but return warnings. Transitions not defined in the template shall be treated as soft-warn by default.

- **Source**: Architect (WFT-TE04), Python (REQ-WFT-PY-012), UX (REQ-WFT-2.2.1, REQ-WFT-2.2.2)
- **Priority**: Must-have
- **Risk**: High (changes the contract of `update_issue()`)
- **Phase**: 1
- **Design ref**: Sections 2.3, 7.1, 8.4

#### WFT-FR-012: Field Requirement Validation at State Level

The system shall validate that fields declared as `required_at` for a given state are populated when transitioning to that state. Empty strings and None shall be treated as unpopulated. Missing required fields on hard transitions shall cause rejection; on soft transitions, warnings.

- **Source**: Architect (WFT-TE05), Python (REQ-WFT-PY-006)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1
- **Design ref**: Sections 3.2, 7.1
- **Note**: Design gap -- empty string treatment was unspecified. Consensus: treat as unpopulated.

#### WFT-FR-013: TransitionResult Return Type

`validate_transition()` shall return a `TransitionResult` dataclass containing: `allowed` (bool), `enforcement` (Literal["hard", "soft"] | None), `missing_fields` (list[str]), `warnings` (list[str]).

- **Source**: Architect (WFT-TE06), Python (REQ-WFT-PY-004)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Section 7.1

#### WFT-FR-014: TransitionOption Return Type

`get_valid_transitions()` shall return a list of `TransitionOption` dataclasses containing: `to` (str), `category` (str), `enforcement` (str | None), `requires_fields` (list[str]), `missing_fields` (list[str]), `ready` (bool).

- **Source**: Architect (WFT-TE07), Python (REQ-WFT-PY-028), UX (REQ-WFT-1.2.1)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Sections 7.1, 8.1

#### WFT-FR-015: ValidationResult Return Type

`validate_issue()` shall return a `ValidationResult` dataclass containing: `valid` (bool), `warnings` (list[str]), `errors` (list[str]).

- **Source**: Python (REQ-WFT-PY-029), UX (REQ-WFT-1.2.2)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

#### WFT-FR-016: Fallback Behavior for Types Without Templates

Types without templates shall use the global `workflow_states` from config (default: open/in_progress/closed). All transitions shall be allowed (soft). No field validation shall be applied.

- **Source**: Architect (WFT-TE08), Python (REQ-WFT-PY-046), UX (REQ-WFT-8.2.1)
- **Priority**: Must-have
- **Risk**: Medium (fallback path must be tested independently)
- **Phase**: 1
- **Design ref**: Section 7.3

#### WFT-FR-017: Transition Validation in update_issue()

`update_issue()` shall call `validate_transition()` before applying status changes. Hard failures shall raise ValueError. Soft failures shall record warning events in the events table and include warnings in the response.

- **Source**: Architect (WFT-I03), Python (REQ-WFT-PY-012), UX (REQ-WFT-2.2.1)
- **Priority**: Must-have
- **Risk**: High (most impactful integration point; changes behavior of all interfaces)
- **Phase**: 1
- **Design ref**: Section 7.2
- **Note**: Soft warnings shall be persisted as events (see Conflict C-04).

#### WFT-FR-018: Per-Type Status Validation

`_validate_status()` shall accept both status and issue type parameters. It shall check per-type states via TemplateRegistry when a template exists, falling back to global `workflow_states` otherwise.

- **Source**: Architect (WFT-I01), Python (REQ-WFT-PY-010)
- **Priority**: Must-have
- **Risk**: High (method signature change affects all call sites)
- **Phase**: 1
- **Design ref**: Section 7.2

### 2.4 Pack System

#### WFT-FR-019: Nine Built-in Packs

The system shall ship with 9 built-in packs: core, planning, requirements, risk, roadmap, incident, debt, spike, release. Each shall include complete type templates, state machines, relationships, and workflow guides.

- **Source**: Architect (WFT-PS05), Python (REQ-WFT-PY-007), Docs (Section 1)
- **Priority**: Must-have
- **Risk**: Medium (large content authoring effort)
- **Phase**: 2
- **Design ref**: Section 10.1

#### WFT-FR-020: Pack Dependency Validation

Packs shall declare dependencies via `requires_packs`. The system shall validate that required packs are enabled before enabling a dependent pack. Enabling a pack with unsatisfied dependencies shall fail with a clear error listing missing packs.

- **Source**: Architect (WFT-PS02), Python (REQ-WFT-PY-035), Systems (SR-16)
- **Priority**: Must-have
- **Risk**: Medium (cycle detection, cascade disable unspecified)
- **Phase**: 5
- **Design ref**: Sections 4.2, 5.3

#### WFT-FR-021: Pack Installation from File

`keel pack install <path>` shall copy a JSON pack file to `.keel/packs/`, validate it against the pack schema, and reject invalid packs with specific error messages.

- **Source**: Architect (WFT-PS03), Python (REQ-WFT-PY-021), UX (REQ-WFT-4.2.1)
- **Priority**: Must-have
- **Risk**: Medium (validation surface for untrusted input)
- **Phase**: 5
- **Design ref**: Section 5.3

#### WFT-FR-022: Pack Enable/Disable Operations

The system shall support enabling and disabling packs via `keel pack enable/disable <name>`. Disabling a pack shall check for existing issues of those types and warn the user. Disabled packs hide types from availability but do not delete data.

- **Source**: Architect (WFT-PS04), Python (REQ-WFT-PY-021), UX (REQ-WFT-4.2.2)
- **Priority**: Must-have
- **Risk**: Medium (behavior of existing issues from disabled packs is a design gap)
- **Phase**: 5
- **Design ref**: Section 5.3
- **Note**: Design gap -- pack disable safety check not specified. Consensus: warn about existing issues.

#### WFT-FR-023: Pack Disable Safety Check

When disabling a pack, the system shall check for open issues of types from that pack and display a warning listing affected issues. The user must confirm to proceed.

- **Source**: UX (REQ-WFT-4.2.2 gap), Systems (Section 4.1)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 5
- **Note**: Design gap -- not in design document. Identified by UX and Systems.

#### WFT-FR-024: Cross-Pack Relationships

Packs shall declare `relationships` and `cross_pack_relationships` to define semantic connections between types. All relationships shall use existing Keel primitives (`parent_id`, `dependency`, `label`, `field_ref`). No new tables required.

- **Source**: Architect (WFT-PS06)
- **Priority**: Must-have
- **Risk**: Low (declarative metadata layer)
- **Phase**: 2
- **Design ref**: Sections 4.2, 4.3, 10.3

#### WFT-FR-025: Workflow Guides per Pack

Each pack shall include a `guide` object with: `overview`, `when_to_use`, `states_explained` (map of all state names to explanations), `typical_flow`, `tips` (array), and `common_mistakes` (array).

- **Source**: Architect (WFT-PS07), UX (REQ-WFT-3.1.1), Docs (Section 1)
- **Priority**: Must-have
- **Risk**: Low (content, not logic)
- **Phase**: 2
- **Design ref**: Sections 4.1, 4.2

### 2.5 Agent Discovery

#### WFT-FR-026: MCP Tool -- list_types

The MCP server shall provide a `list_types` tool returning all available types across enabled packs, including type name, pack, display name, description, states, and initial state. Accepts optional `pack` parameter to filter to a single pack's types.

- **Source**: Architect (WFT-IF01), UX (REQ-WFT-1.1.1), Python (REQ-WFT-PY-017)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.1
- **Note**: Agent review considered merging into `list_packs`. Kept separate because they serve different purposes: `list_types` is the frequent "what can I create?" query; `list_packs` is the rare "what's installed?" query. Added pack filter parameter to avoid needing both tools together.

#### WFT-FR-027: MCP Tool -- get_type_info

The MCP server shall provide a `get_type_info` tool returning the full type template including states, transitions, fields schema, relationships, suggested children, and suggested labels. This replaces `get_template`. Accepts optional `include_guide=true` parameter to embed the workflow guide in the response, eliminating the need for a separate `get_workflow_guide` call.

- **Source**: Architect (WFT-IF01), UX (REQ-WFT-1.1.2), Python (REQ-WFT-PY-017), **Agent Review (AR-3)**
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.1
- **Note**: Agent review identified that `get_type_info` + `get_workflow_guide` are frequently needed together. The `include_guide` parameter collapses this to one call. `get_workflow_guide` (WFT-FR-031) remains available for pack-level guide access.

#### WFT-FR-028: MCP Tool -- get_valid_transitions

The MCP server shall provide a `get_valid_transitions` tool that, given an issue ID, returns valid next states with enforcement level, required fields, missing fields, and readiness status. This is the primary agent workflow navigation mechanism.

- **Source**: Architect (WFT-IF01), UX (REQ-WFT-1.2.1 -- CRITICAL), Python (REQ-WFT-PY-017)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.1

#### WFT-FR-029: MCP Tool -- list_packs

The MCP server shall provide a `list_packs` tool returning enabled packs with their types, descriptions, enabled status, and pack dependencies.

- **Source**: Architect (WFT-IF01), UX (REQ-WFT-1.1.3), Python (REQ-WFT-PY-017)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.1

#### WFT-FR-030: MCP Tool -- validate_issue

The MCP server shall provide a `validate_issue` tool that checks an issue against its type template, returning missing fields, invalid state warnings, and suggested transitions. Non-mutating.

- **Source**: Architect (WFT-IF01), UX (REQ-WFT-1.2.2), Python (REQ-WFT-PY-017)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.1

#### WFT-FR-031: MCP Tool -- get_workflow_guide

The MCP server shall provide a `get_workflow_guide` tool returning the guide for a pack, including overview, state explanations, typical flow, tips, and common mistakes. The guide shall include a `state_diagram` field with a compact ASCII state machine representation. The `overview` field shall be under 50 words and the `when_to_use` field under 30 words. The `tips` and `common_mistakes` arrays are the highest-value fields for agents and shall be prioritized for quality.

- **Source**: Architect (WFT-IF01), UX (REQ-WFT-3.1.1), Python (REQ-WFT-PY-017), **Agent Review (AR-2)**
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.1
- **Note**: Agent review identified that narrative prose guides are less useful to agents than compact state diagrams and actionable bullet points. Word limits ensure guides are consumable mid-task.

#### WFT-FR-032: MCP Tool -- explain_state

The MCP server shall provide an `explain_state` tool returning a human-readable explanation of a state, suggested next steps, and current required-fields compliance for that state. Shall accept either (type, state) or (issue_id) as input.

- **Source**: Architect (WFT-IF01), UX (REQ-WFT-3.1.3)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.1
- **Note**: UX recommends supporting both type+state and issue_id modes. Design only specifies type+state.

#### WFT-FR-033: MCP Status Parameter — Category Enum + State Names

MCP tool schemas for `list_issues`, `update_issue`, and any tool accepting status parameters shall replace the hardcoded `"enum": ["open", "in_progress", "closed"]` with `"enum": ["open", "wip", "done"]` for category-level filtering. The status parameter description shall state that per-type state names (e.g., `triage`, `confirmed`, `fixing`) are also accepted, and direct agents to `list_types` or `get_valid_transitions` for discovery of type-specific state names.

- **Source**: Architect (WFT-IF02), Python (REQ-WFT-PY-018), **Agent Review (AR-4)**
- **Priority**: Must-have
- **Risk**: Medium (category enum provides machine-readable guidance; description covers per-type states)
- **Phase**: 3
- **Design ref**: Section 7.2
- **Note**: Agent review identified that removing the enum entirely leaves agents without machine-readable guidance for the common case. Keeping a category enum preserves schema-level discovery while accepting state names extends capability.

#### WFT-FR-034: Pack-Aware Workflow Prompt

The MCP `keel-workflow` prompt shall become pack-aware, listing enabled packs, key workflow discovery tools (`get_valid_transitions`, `get_workflow_guide`, `explain_state`), and the navigation pattern (create, query transitions, populate fields, transition, repeat).

- **Source**: Architect (WFT-IF03), UX (REQ-WFT-3.1.2), Python (REQ-WFT-PY-019)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.2

#### WFT-FR-035: MCP get_template Backward Compatibility

The existing `get_template` MCP tool shall remain functional, delegating to `get_type_info`.

- **Source**: Architect (WFT-BC02)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 11.2

#### WFT-FR-036: Soft Enforcement Warning Return Path

MCP tools that perform status changes shall return soft enforcement warnings in the response alongside the operation result. The response shall include an optional `warnings` array when warnings exist.

- **Source**: Architect (WFT-IF05), UX (REQ-WFT-2.2.1), Python (REQ-WFT-PY-012)
- **Priority**: Must-have
- **Risk**: Medium (changes response contract for existing tools)
- **Phase**: 3
- **Design ref**: Section 8.4

#### WFT-FR-037: Hard Enforcement Error with Remediation Guidance

Hard enforcement errors shall include the specific fields that are missing, the current and attempted states, and a remediation hint. Error messages shall be identical in substance across MCP and CLI.

- **Source**: UX (REQ-WFT-2.2.2, REQ-WFT-2.3.1, REQ-WFT-7.3.1), Systems (SR-14), Docs (Section 9)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3

#### WFT-FR-038: Response Format Includes Both State and Category

All MCP tool responses returning issue data shall include both `status` (the per-type state name) and `status_category` (the universal category: open/wip/done).

- **Source**: UX (REQ-WFT-2.1.1 -- CRITICAL)
- **Priority**: Must-have
- **Risk**: Medium (requires modifying Issue serialization)
- **Phase**: 1

### 2.6 CLI Interface

#### WFT-FR-039: CLI -- keel types

The CLI shall provide `keel types` listing all available types grouped by pack, with display name, description, and state summary. Shall support `--json` output.

- **Source**: Architect (WFT-IF04), UX (REQ-WFT-4.1.2), Python (REQ-WFT-PY-020)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.3

#### WFT-FR-040: CLI -- keel type-info

The CLI shall provide `keel type-info <type>` displaying the full template: states with categories, transitions with enforcement levels and required fields, and field schema summary.

- **Source**: Architect (WFT-IF04), UX (REQ-WFT-4.1.3), Python (REQ-WFT-PY-020)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.3

#### WFT-FR-041: CLI -- keel transitions

The CLI shall provide `keel transitions <issue_id>` showing valid next states for a specific issue, with enforcement level, required fields, and readiness.

- **Source**: Architect (WFT-IF04), UX (REQ-WFT-7.2.1), Python (REQ-WFT-PY-020)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.3

#### WFT-FR-042: CLI -- keel packs

The CLI shall provide `keel packs` listing installed/enabled packs with version, description, and enabled status. Shall support `--json` output.

- **Source**: Architect (WFT-IF04), UX (REQ-WFT-4.1.1), Python (REQ-WFT-PY-020)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.3

#### WFT-FR-043: CLI -- keel pack enable/disable/install

The CLI shall provide `keel pack enable <name>`, `keel pack disable <name>`, and `keel pack install <path>` commands. Enable/disable shall update `config.json` `enabled_packs` field. Install shall validate pack JSON and copy to `.keel/packs/`.

- **Source**: Architect (WFT-IF04), Python (REQ-WFT-PY-021), UX (REQ-WFT-4.2.1, REQ-WFT-4.2.2)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 5
- **Design ref**: Sections 5.3, 8.3

#### WFT-FR-044: CLI -- keel validate

The CLI shall provide `keel validate <issue_id>` checking an issue against its template, showing missing fields, invalid state warnings, and suggested next steps.

- **Source**: Architect (WFT-IF04), Python (REQ-WFT-PY-020)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Section 8.3

#### WFT-FR-045: CLI -- keel guide

The CLI shall provide `keel guide <pack>` printing the full workflow guide narrative. With `--state <state>`, it shall display contextual help for a specific state including next steps and required field status.

- **Source**: Architect (WFT-IF04), UX (REQ-WFT-4.3.1, REQ-WFT-4.3.2), Python (REQ-WFT-PY-020)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Design ref**: Sections 8.3

### 2.7 Core Engine Integration

#### WFT-FR-046: close_issue() Category Validation

`close_issue()` shall validate that the target state has `category="done"`. It shall accept an optional `status` parameter to specify which done state. When not specified, it shall use the first done-category state from the template (array order). For types without templates, default to `"closed"`.

- **Source**: Architect (WFT-I04), Python (REQ-WFT-PY-013)
- **Priority**: Must-have
- **Risk**: High (API contract change for multi-done-state types)
- **Phase**: 1
- **Design ref**: Section 7.2
- **Note**: Design gap -- design does not specify target state selection. Consensus: optional parameter, default to first done state.

#### WFT-FR-047: claim_issue() Category-Aware Transition

`claim_issue()` shall transition from any open-category state to the first wip-category state for the issue's type (array order). The optimistic locking shall check category in Python before the atomic UPDATE, using the exact current status value in the WHERE clause.

- **Source**: Architect (WFT-I05), Python (REQ-WFT-PY-014), Systems (B2 analysis)
- **Priority**: Must-have
- **Risk**: High (atomicity guarantee changes; "first wip state" ordering critical)
- **Phase**: 1
- **Design ref**: Section 7.2
- **Note**: Consensus: check category in Python, use exact status in SQL WHERE clause.

#### WFT-FR-048: get_ready() and get_blocked() Category Awareness

`get_ready()` shall return issues whose status maps to category `open` with no open blockers. `get_blocked()` shall return issues whose status maps to category `open` with at least one blocker whose status maps to a non-`done` category.

- **Source**: Architect (WFT-I06), Python (REQ-WFT-PY-015), Systems (B1 analysis)
- **Priority**: Must-have
- **Risk**: High (SQL query expansion; performance implications at scale)
- **Phase**: 1
- **Design ref**: Section 7.2

#### WFT-FR-049: _build_issues_batch() Category-Aware is_ready

The `is_ready` computation in `_build_issues_batch()` shall use category mapping instead of `row["status"] == "open"`.

- **Source**: Architect (WFT-I07), Python (REQ-WFT-PY-015)
- **Priority**: Must-have
- **Risk**: Medium (workhorse function; per-issue template lookup adds overhead)
- **Phase**: 1

#### WFT-FR-050: New KeelDB Methods

KeelDB shall expose two new methods: `get_valid_transitions(issue_id)` and `validate_issue(issue_id)`, both delegating to TemplateRegistry with current issue state and fields.

- **Source**: Architect (WFT-I08), Python (REQ-WFT-PY-016)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Section 7.2

### 2.8 Migration

#### WFT-FR-051: Schema Migration v4 to v5

The system shall migrate from schema version 4 to 5, creating `type_templates` and `packs` tables as defined in design Section 6.1.

- **Source**: Architect (WFT-D01), Python (REQ-WFT-PY-022, REQ-WFT-PY-023)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1
- **Design ref**: Sections 6.1, 6.2

#### WFT-FR-052: type_templates Table Creation

The migration shall create a `type_templates` table with columns: `type` (TEXT PK), `pack` (TEXT NOT NULL DEFAULT 'core'), `definition` (TEXT NOT NULL), `is_builtin` (BOOLEAN NOT NULL DEFAULT 0), `created_at` (TEXT NOT NULL), `updated_at` (TEXT NOT NULL).

- **Source**: Architect (WFT-D02), Python (REQ-WFT-PY-023)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Section 6.1

#### WFT-FR-053: packs Table Creation

The migration shall create a `packs` table with columns: `name` (TEXT PK), `version` (TEXT NOT NULL), `definition` (TEXT NOT NULL), `is_builtin` (BOOLEAN NOT NULL DEFAULT 0), `enabled` (BOOLEAN NOT NULL DEFAULT 1).

- **Source**: Architect (WFT-D03), Python (REQ-WFT-PY-023)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Section 6.1

#### WFT-FR-054: Built-in Pack Seeding

The migration shall seed all 9 built-in packs into the `packs` table and their 26 type templates into `type_templates`, marked as `is_builtin=1`.

- **Source**: Architect (WFT-PS05, WFT-IN01), Python (REQ-WFT-PY-024)
- **Priority**: Must-have
- **Risk**: Medium (large data volume during migration)
- **Phase**: 1
- **Design ref**: Section 6.2

#### WFT-FR-055: Old Templates Table Migration

The migration shall migrate custom templates from the old `templates` table to `type_templates`, enriching them with default 3-state machines (open/in_progress/closed). Built-in types that already exist in the new table shall be skipped. Old templates shall be assigned to a "custom" pack.

- **Source**: Architect (WFT-D07), Python (REQ-WFT-PY-025)
- **Priority**: Must-have
- **Risk**: High (data enrichment, not just schema change)
- **Phase**: 1
- **Design ref**: Section 6.2

#### WFT-FR-056: Old Templates Table Drop

After data migration, the old `templates` table shall be dropped.

- **Source**: Python (REQ-WFT-PY-026)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Section 6.2

#### WFT-FR-057: Config File Backward Compatibility

Existing `config.json` files without `enabled_packs` shall be treated as defaulting to `["core", "planning"]`. The migration or post-migration step shall add `enabled_packs` to config if missing.

- **Source**: Architect (WFT-BC04), Python (REQ-WFT-PY-027, REQ-WFT-PY-045)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Section 5.2

#### WFT-FR-058: Existing Data Preservation

All existing issues shall remain untouched during migration. Existing `status` values (`open`, `in_progress`, `closed`) shall remain valid states in the core pack. No data in `issues`, `dependencies`, `events`, `comments`, or `labels` tables shall be modified.

- **Source**: Architect (WFT-D06, WFT-D07, WFT-BC01)
- **Priority**: Must-have
- **Risk**: High (constraint verification across all modified query paths)
- **Phase**: 1
- **Design ref**: Sections 6.3, 11.1, 11.2

#### WFT-FR-059: JSONL Export/Import Backward Compatibility

JSONL export shall include `type_templates` and `packs` records. Import shall handle old-format files that lack these record types gracefully.

- **Source**: Architect (WFT-BC03)
- **Priority**: Should-have
- **Risk**: Medium
- **Phase**: 5
- **Design ref**: Section 11.2

### 2.9 Summary Updates

#### WFT-FR-060: Summary Category-Based Vitals

The summary generator shall group issues by category (`open`/`wip`/`done`) across all types in the Vitals section, not by literal status strings.

- **Source**: Architect (WFT-SD01), UX (REQ-WFT-6.1.1)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 4
- **Design ref**: Section 9.1

#### WFT-FR-061: Summary Shows Specific States

Ready-to-work and in-progress items in the summary shall include the specific state in parentheses after the title. Example: `P1 proj-a3f9b2 [bug] "Login crash" (confirmed)`.

- **Source**: Architect (WFT-SD02), UX (REQ-WFT-6.1.2)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 4
- **Design ref**: Section 9.1

#### WFT-FR-062: Summary Shows State Transitions with Specific States

Recent activity in the summary shall show actual state names in transitions (e.g., `triage->closed` instead of `open->closed`).

- **Source**: UX (REQ-WFT-6.1.3)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 4

### 2.10 Dashboard Adaptation

#### WFT-FR-063: Default Kanban Uses Category Columns

The default dashboard kanban view shall use 3 columns by category (Open / In Progress / Closed) with issues grouped by `status_category`. Specific state names shall be visible in card details.

- **Source**: Architect (WFT-SD03), UX (REQ-WFT-5.1.1)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 4
- **Design ref**: Section 9.2

#### WFT-FR-064: Type-Filtered Kanban with Per-Type State Columns

When filtering the dashboard kanban to a single type, columns shall expand to that type's full state list instead of the 3-column category view.

- **Source**: Architect (WFT-SD03), UX (REQ-WFT-5.1.2)
- **Priority**: Should-have
- **Risk**: Medium (dynamic column generation in frontend)
- **Phase**: 4
- **Design ref**: Section 9.2

#### WFT-FR-065: Dashboard Type Info API Endpoint

The dashboard shall expose `GET /api/type/{type_name}` returning the type template for dynamic view construction. Read-only.

- **Source**: Architect (WFT-SD04), UX (REQ-WFT-5.3.1)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 4
- **Design ref**: Section 9.2

### 2.11 Install and Doctor

#### WFT-FR-066: keel install Seeds Default Packs

`keel install` shall seed all 9 built-in packs into the database. Only `core` and `planning` shall be enabled by default.

- **Source**: Architect (WFT-IN01)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Section 9.4

#### WFT-FR-067: Doctor Pack Validation Checks

`keel doctor` shall check: pack dependency validation (missing required packs), orphaned type references (issues with types from disabled packs), and template schema validation.

- **Source**: Architect (WFT-EX04), Python (REQ-WFT-PY-045)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 5
- **Design ref**: Section 9.4

#### WFT-FR-068: CLAUDE.md Instructions Updated for Pack Workflows

The CLAUDE.md instruction block shall mention pack-specific workflows, guide agents to use `get_workflow_guide` for domain guidance, and include quick-reference commands for `types`, `transitions`, `guide`, and `validate`.

- **Source**: Architect (WFT-IN02), Docs (Section 8)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 3

### 2.12 Agent Ergonomics (Agent Review Additions)

> Requirements WFT-FR-069 through WFT-FR-075 were added based on a self-review by an AI agent
> evaluating the requirements from the perspective of the primary user (an AI coding agent using
> Keel's MCP tools daily). These focus on reducing round-trips, improving session resumption,
> and clarifying batch operation semantics.

#### WFT-FR-069: Atomic Transition with Field Population

`update_issue()` shall merge provided `fields` into the issue BEFORE evaluating transition validation when both `status` and `fields` are provided in the same call. This allows agents to populate required fields and transition in a single atomic operation rather than requiring two separate calls (one to populate fields, one to transition).

- **Source**: Agent Review (AR-1 — top-priority feature request)
- **Priority**: Must-have
- **Risk**: Medium (changes the ordering semantic of update_issue when both status and fields are provided)
- **Phase**: 1
- **Design ref**: Section 7.2
- **Note**: Without this, every transition requiring field population costs 2 tool calls instead of 1. This is the single biggest round-trip reducer in the agent workflow.

#### WFT-FR-070: get_issue with Optional Transition Context

The `get_issue` MCP tool shall accept an optional `include_transitions` parameter (default: false). When true, the response shall include a `valid_transitions` array matching the output format of `get_valid_transitions`. This collapses the two-call session-resumption pattern (`get_issue` + `get_valid_transitions`) into a single call per issue.

- **Source**: Agent Review (AR-2 — second-priority feature request)
- **Priority**: Should-have
- **Risk**: Low (additive; existing responses unchanged when parameter omitted)
- **Phase**: 3
- **Note**: Session resumption is the #2 most common agent workflow after "work on current issue." Reducing it from 2N calls to N calls (where N = in-progress issues) significantly improves agent startup time.

#### WFT-FR-071: Summary "Needs Attention" Section

The summary generator shall include a "Needs Attention" section listing in-progress issues that have missing required fields for their most likely next transition. For each issue, show the issue reference, current state, target transition, and missing field names. This is the workflow-template equivalent of the existing "Blocked" section.

- **Source**: Agent Review (AR-3 — third-priority feature request)
- **Priority**: Should-have
- **Risk**: Medium (requires transition analysis during summary generation; potential performance impact)
- **Phase**: 4
- **Design ref**: Section 9.1
- **Note**: Without this, agents must call `validate_issue` on every in-progress issue to discover field gaps, defeating the purpose of the pre-computed summary.

#### WFT-FR-072: claim_next Compound Operation

The system shall provide a `claim_next` MCP tool that atomically selects and claims the highest-priority ready issue matching optional filters (`type`, `priority_min`, `priority_max`, `labels`). Returns the claimed issue or an empty result if no ready work matches. This eliminates the `get_ready` + `claim_issue` race condition in multi-agent scenarios.

- **Source**: Agent Review (AR-4 — fourth-priority feature request)
- **Priority**: Should-have
- **Risk**: Low (compound of existing operations with atomic guarantee)
- **Phase**: 3
- **Note**: In multi-agent scenarios, two agents calling `get_ready()` simultaneously may both attempt to claim the same issue. `claim_next` resolves this by making selection and claiming atomic. In single-agent scenarios, it saves one tool call per work-claiming cycle.

#### WFT-FR-073: Batch Operations Under Workflow Templates

`batch_close` and `batch_update` (when performing status changes) shall: (a) validate each issue individually against its type template, (b) collect soft warnings per issue and return them in aggregate, (c) not let a hard failure on one issue prevent processing of other issues, (d) return per-issue results with success/failure/warning status. The response format shall include `succeeded`, `failed`, and `warnings` arrays.

- **Source**: Agent Review (AR-5 — fifth-priority feature request)
- **Priority**: Must-have
- **Risk**: Medium (changes response format of existing batch operations)
- **Phase**: 3
- **Note**: Currently unspecified. Without this, agents closing a milestone with 10+ issues of mixed types have no way to predict or handle partial failures.

#### WFT-FR-074: Phase 2 Scope — Core and Planning Packs First

Phase 2 shall deliver complete, fully-tested definitions for the `core` pack (4 types) and `planning` pack (5 types) first. The remaining 7 packs (`requirements`, `risk`, `roadmap`, `incident`, `debt`, `spike`, `release`) shall be delivered incrementally in priority order based on actual usage data, not as a monolithic release. The `risk` and `spike` packs are recommended as the next tier.

- **Source**: Agent Review (AR-1)
- **Priority**: Must-have
- **Risk**: Low (reduces scope and risk of Phase 2; deferred packs are additive)
- **Phase**: 2
- **Note**: Agent review identified that core + planning cover ~90% of actual agent work. Shipping 26 types simultaneously risks quality dilution across workflow guides and field schemas. Incremental delivery allows quality to be verified per-pack.

#### WFT-FR-075: Hard Enforcement Errors Include Valid Transitions

When a hard enforcement error rejects a transition, the error response shall include the `valid_transitions` list for the issue's current state. This allows agents to self-correct in a single retry without making a separate `get_valid_transitions` call. Error responses should also include a `hint` field (e.g., `"Use get_valid_transitions to see allowed next states"`).

- **Source**: Agent Review (AR-6), related to WFT-FR-037
- **Priority**: Should-have
- **Risk**: Low (additive to existing error response)
- **Phase**: 3
- **Note**: Without this, a hard enforcement error triggers a predictable 2-call recovery pattern (read error → call get_valid_transitions → retry). Including transitions in the error collapses this to a 1-call recovery. Also mitigates agent retry storms (review W6).

---

## 3. Non-Functional Requirements

### 3.1 Performance

#### WFT-NFR-001: Template Load Time Budget

Template loading shall complete in under 100ms for 9 built-in packs on typical hardware. Templates shall be loaded once per KeelDB instance and cached.

- **Source**: Python (REQ-WFT-PY-037), Systems (SR-11)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1

#### WFT-NFR-002: Category Mapping O(1) Lookup

Mapping a (type, state) pair to its category shall be O(1) via a pre-computed dictionary cache. This cache shall be built during template loading.

- **Source**: Systems (SR-17), Python (REQ-WFT-PY-038)
- **Priority**: Must-have
- **Risk**: High (without this, summary generation becomes O(N * T))
- **Phase**: 1

#### WFT-NFR-003: Transition Validation Performance

`validate_transition()` shall complete in under 5ms for a typical template (6 states, 10 transitions). Pre-compute a transition lookup dictionary at template load time for O(1) transition lookups.

- **Source**: Python (REQ-WFT-PY-038)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

#### WFT-NFR-004: Summary Generation Impact Budget

Workflow templates shall not degrade summary generation time by more than 70%. For 600 issues, the v1.1 target is under 85ms (v1.0 baseline: ~50ms).

- **Source**: Systems (Sections 3.1, 9.1), Python (REQ-WFT-PY-039)
- **Priority**: Must-have
- **Risk**: High (projection shows scaling limit arrives 40% sooner)
- **Phase**: 1

#### WFT-NFR-005: Template Immutability Within Session

Type templates shall not change during a KeelDB instance lifetime. Changing templates requires restarting the MCP server or CLI session. This enables safe caching without invalidation logic.

- **Source**: Systems (SR-18)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

### 3.2 Reliability

#### WFT-NFR-006: Migration Safety

The v4-to-v5 migration shall use the established temporary-table pattern (NFR-007 from baseline). It shall be safe to re-run (idempotent table creation via IF NOT EXISTS). Fresh databases shall create v5 directly.

- **Source**: Python (REQ-WFT-PY-022), Architect (WFT-D01)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1

#### WFT-NFR-007: Pack Schema Validation

Pack JSON files shall be validated during installation. Invalid packs shall be rejected with specific, actionable error messages including filename and the specific validation failure.

- **Source**: Architect (WFT-EX03), Python (REQ-WFT-PY-036)
- **Priority**: Must-have
- **Risk**: Medium (hand-rolled validation vs. dependency)
- **Phase**: 5

#### WFT-NFR-008: Template Definition Validation at Load Time

Type templates shall be validated at load time (eager validation). Invalid templates shall be rejected with errors identifying the specific issue (missing fields, invalid state references, invalid transitions). Validation shall check: required fields exist, initial_state is in states list, transitions reference valid states, `required_at` fields reference valid states.

- **Source**: Python (REQ-WFT-PY-033), Architect (WFT-D04)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1

### 3.3 Usability

#### WFT-NFR-009: Enforcement Level Preview

Agents shall be able to preview enforcement levels and required fields BEFORE attempting a transition, via `get_valid_transitions`. This reduces failed-then-retry cycles.

- **Source**: UX (REQ-WFT-2.2.3)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3

#### WFT-NFR-010: Summary Length Control

The summary shall remain under 150 lines with rich workflow data. State information added to issue lines shall be compensated by reducing ready-issue limits from 15 to 12 and epic progress from unlimited to 10.

- **Source**: UX (REQ-WFT-6.2.1)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 4

#### WFT-NFR-011: Vocabulary Consistency Across Interfaces

State names (lowercase identifiers), category names (`open`, `wip`, `done`), field names (snake_case), and enforcement levels (`hard`, `soft`) shall be identical across MCP, CLI, Dashboard, and Summary.

- **Source**: UX (REQ-WFT-7.1.1, REQ-WFT-7.1.2, REQ-WFT-7.3.1)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

### 3.4 Maintainability

#### WFT-NFR-012: Circular Import Avoidance

The circular dependency between `templates.py` (needs KeelDB type) and `core.py` (needs TemplateRegistry) shall be resolved using `TYPE_CHECKING` guards for type hints and lazy runtime imports inside methods.

- **Source**: Python (CONFLICT-WFT-PY-001), Architect (WFT-I09)
- **Priority**: Must-have
- **Risk**: Medium (affects module organization)
- **Phase**: 1

#### WFT-NFR-013: Built-in Pack Data Separation

Built-in pack definitions (~1,350 lines of JSON-compatible Python data) shall be stored in a separate module (`templates_data.py`) to keep `templates.py` focused on logic.

- **Source**: Python (REQ-WFT-PY-007)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 2

#### WFT-NFR-014: Frozen Dataclasses for Template Types

TypeTemplate, WorkflowPack, StateDefinition, TransitionDefinition, FieldSchema, TransitionResult, TransitionOption, and ValidationResult shall be `@dataclass(frozen=True)` following the existing `Issue` dataclass pattern. No Pydantic dependency.

- **Source**: Python (REQ-WFT-PY-002, CONFLICT-WFT-PY-002)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

#### WFT-NFR-015: Type Aliases for Enforcement and Category

The system shall define `StateCategory = Literal["open", "wip", "done"]`, `EnforcementLevel = Literal["hard", "soft"]`, and `FieldType = Literal["text", "enum", "number", "date", "list", "boolean"]` for use throughout the codebase.

- **Source**: Python (REQ-WFT-PY-030, REQ-WFT-PY-031, REQ-WFT-PY-032)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

### 3.5 Testability

#### WFT-NFR-016: Template Validation Test Suite

A `tests/test_templates.py` shall include parametrized tests for all 26 built-in types, valid/invalid state transition tests, field requirement validation tests, pack dependency resolution tests, and template override precedence tests. Target: 100% branch coverage of `templates.py`.

- **Source**: Python (REQ-WFT-PY-040)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

#### WFT-NFR-017: Migration Test Suite

A `tests/test_migration_v5.py` shall test: fresh DB v5 creation, v4-to-v5 upgrade path, old templates table migration, config enrichment, and built-in pack seeding verification.

- **Source**: Python (REQ-WFT-PY-041)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

#### WFT-NFR-018: End-to-End Workflow Tests

A `tests/test_e2e_workflows.py` shall simulate 3-5 representative agent workflows (risk, incident, requirement) exercising the full create-transition-validate-close cycle including hard enforcement failures and soft warnings.

- **Source**: Python (REQ-WFT-PY-044)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 2

---

## 4. Architectural Requirements

#### WFT-AR-001: TemplateRegistry Lifecycle in KeelDB

KeelDB shall accept an optional `template_registry` parameter in `__init__()`. When not provided, the registry shall be created lazily on first access via a `templates` property. The lazy creation shall use a runtime import to avoid circular imports.

- **Source**: Architect (WFT-I09), Python (REQ-WFT-PY-009)
- **Priority**: Must-have
- **Risk**: High (circular dependency resolution)
- **Phase**: 1
- **Design ref**: Section 7.1, 7.2
- **Note**: Design gap -- bidirectional dependency not addressed. Consensus: lazy initialization with runtime import.

#### WFT-AR-002: Existing Tables Unchanged

The `issues`, `dependencies`, `events`, `comments`, `labels`, and `issues_fts` tables shall not be modified. The `issues.status` column remains TEXT. The `issues.type` column continues to reference templates. The `issues.fields` column continues to store JSON.

- **Source**: Architect (WFT-D06)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Design ref**: Section 6.3

#### WFT-AR-003: Template Definition as JSON Document

Each type template shall be stored as a single JSON document in the `definition` column of `type_templates`, containing the full schema defined in design Section 3.2.

- **Source**: Architect (WFT-D04)
- **Priority**: Must-have
- **Risk**: Medium (26 types, ~1,500 lines of JSON)
- **Phase**: 1
- **Design ref**: Sections 3.1, 3.2

#### WFT-AR-004: Pack Definition as JSON Document

Each workflow pack shall be stored as a single JSON document in the `definition` column of `packs`, containing the full schema defined in design Section 4.2.

- **Source**: Architect (WFT-D05)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1
- **Design ref**: Sections 4.1, 4.2

#### WFT-AR-005: Relationship Mechanisms Use Existing Primitives

All cross-type and cross-pack relationships shall be implemented using existing Keel primitives: `parent_id` (structural containment), `dependency` (execution ordering), `label` (soft grouping), `field_ref` (arbitrary cross-reference). No new tables.

- **Source**: Architect (WFT-PS06)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 2
- **Design ref**: Section 4.3

#### WFT-AR-006: Filesystem Layout for Packs and Templates

The `.keel/` directory shall include: `packs/` for installed workflow packs (*.json) and `templates/` for project-local type overrides (*.json).

- **Source**: Architect (WFT-EX01, WFT-EX02)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 5
- **Design ref**: Section 5.4

#### WFT-AR-007: New Module Structure

The implementation shall add `src/keel/templates.py` (~300-400 lines of logic) and `src/keel/templates_data.py` (~1,350 lines of pack definitions). No other new modules required.

- **Source**: Architect (WFT-TE01), Python (REQ-WFT-PY-007)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

#### WFT-AR-008: Template Override is Whole-Document Replacement

When Layer 3 (project-local) overrides a type, the entire template definition is replaced, not merged field-by-field. A project override for `bug.json` must include the complete type template.

- **Source**: Architect (WFT-EX01), Python (GAP-WFT-PY-001)
- **Priority**: Must-have
- **Risk**: Medium (user must define complete template to override one field)
- **Phase**: 5
- **Note**: Design gap resolved -- consensus is whole-document replacement for simplicity.

#### WFT-AR-009: Database Serves as Seed Store, Filesystem as Override

The database `type_templates` and `packs` tables store seeded built-in definitions. The filesystem (`.keel/packs/` and `.keel/templates/`) provides override layers. At load time, the TemplateRegistry reads from both sources with filesystem overriding database.

- **Source**: Architect (WFT-TE02 gap)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1
- **Note**: Design gap -- database vs. filesystem authority was unclear. Consensus: database is seed, filesystem overrides.

#### WFT-AR-010: Custom Exception Types for Enforcement

The system shall define: `TransitionNotAllowedError(ValueError)` for transitions not in the transition table, and `HardEnforcementError(ValueError)` for hard-enforced transitions failing field validation.

- **Source**: Python (REQ-WFT-PY-034)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 1

#### WFT-AR-011: Backward Compatibility Guarantees

Projects that do nothing after upgrade shall work identically. All existing queries shall produce the same results. The `open/in_progress/closed` state set shall remain valid. `context.md` format shall remain compatible. MCP `get_template` shall remain functional.

- **Source**: Architect (WFT-BC01), UX (REQ-WFT-8.1.1, REQ-WFT-8.1.2), Systems (Section 7.1)
- **Priority**: Must-have
- **Risk**: High (hardest constraint to verify; 12+ query paths modified)
- **Phase**: 1
- **Design ref**: Section 11.2

#### WFT-AR-012: Gradual Workflow Adoption

Projects shall be able to adopt workflows gradually by enabling packs one at a time. Existing issues continue using simple states while new issues use type-specific states. No forced migration.

- **Source**: UX (REQ-WFT-8.2.2)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

---

## 5. Documentation Requirements

#### WFT-DR-001: Workflow Guides for All 9 Packs

All 9 built-in packs shall include complete workflow guides meeting the quality standard demonstrated by the risk pack example in design Section 4.1. Each guide shall include overview, when_to_use, states_explained (100% state coverage), typical_flow, 3-5 tips, and 3-5 common_mistakes.

- **Source**: Docs (Sections 1.1-1.6), UX (REQ-WFT-3.1.1, REQ-WFT-3.2.1)
- **Priority**: Must-have
- **Risk**: High (domain expertise required: PMBOK, ITIL, ISO 31000, SAFe, CMMI)
- **Phase**: 2
- **Volume**: 5,400-8,100 words

#### WFT-DR-002: State Explanations for All States

All ~60 unique states across all types shall have explanations that include: (1) definition of what the state means, (2) what to do next, (3) optional context. Each explanation shall be under 40 words.

- **Source**: Docs (Section 2), UX (REQ-WFT-3.1.3)
- **Priority**: Must-have
- **Risk**: Medium (state-explanation drift as code evolves)
- **Phase**: 2
- **Volume**: 1,200-1,800 words

#### WFT-DR-003: MCP Tool Descriptions for 7 New Tools

All 7 new MCP tools shall have action-oriented descriptions following the existing pattern: action verb, what it does, key constraints, return value. The `get_valid_transitions` description shall explain enforcement levels and readiness semantics.

- **Source**: Docs (Section 3), UX (REQ-WFT-3.1.2)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Volume**: ~500 words

#### WFT-DR-004: CLI Help Text for New Commands

All 10 new CLI commands shall have 1-sentence imperative-voice help text following the existing Click pattern.

- **Source**: Docs (Section 4)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 3
- **Volume**: ~100 words

#### WFT-DR-005: Type Descriptions for All 26 Types

Each of the 26 types shall have a 1-sentence `description` field in its template definition explaining what the type represents.

- **Source**: Docs (Section 5.4)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 2
- **Volume**: ~208 words

#### WFT-DR-006: Field Descriptions for All Template Fields

Each field in each type's `fields_schema` shall have a `description` explaining what goes in the field (2-5 words).

- **Source**: Docs (Section 5.5)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 2
- **Volume**: ~780 words

#### WFT-DR-007: Relationship Descriptions

Every relationship and cross-pack relationship in pack definitions shall have a clear `description` field (1 sentence, explains semantics).

- **Source**: Docs (Section 5.2, 5.3)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 2
- **Volume**: ~200 words

#### WFT-DR-008: Error and Warning Message Templates

The system shall define consistent error message templates for: missing required fields for transition, hard constraint violations, pack dependency errors, invalid state for type, and type not in enabled packs. Warning templates for: soft transition violations and missing suggested fields.

- **Source**: Docs (Section 9), UX (REQ-WFT-7.3.1)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Volume**: ~250 words

#### WFT-DR-009: Upgrade Guide

An upgrade guide shall document: what's new, backward compatibility guarantees (6 from design Section 11.2), how to enable packs, automatic migrations, and confirmation of no breaking changes.

- **Source**: Docs (Section 7)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 5
- **Volume**: ~600 words

#### WFT-DR-010: CLAUDE.md Instructions Update

The KEEL_INSTRUCTIONS block shall be updated with: pack-aware workflow mentions, quick reference for new commands (`types`, `transitions`, `guide`, `validate`), and discovery tool guidance.

- **Source**: Docs (Section 8), Architect (WFT-IN02)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 3
- **Volume**: ~150 words

#### WFT-DR-011: Docstring Coverage for New Code

All new public methods and classes in `templates.py` shall have docstrings following Google style with Args/Returns sections. All public methods shall have complete type hints (mypy strict).

- **Source**: Python (REQ-WFT-PY-047, REQ-WFT-PY-048), Docs (implied)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

#### WFT-DR-012: Cross-Pack Workflow Narratives

Workflow guides for packs with cross-pack relationships (incident, spike) shall include narrative descriptions of multi-pack workflows (e.g., the ITIL chain: incident -> problem -> change_request -> bug -> release).

- **Source**: Docs (Gap 15.3)
- **Priority**: Nice-to-have
- **Risk**: Low
- **Phase**: 2

#### WFT-DR-013: Summary Format Code Comments

The summary generator code shall include comments explaining that state is shown for context and category is used for filtering, wherever state rendering occurs.

- **Source**: Docs (Section 6)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 4

#### WFT-DR-014: Agent-Facing Documentation Quality Standard

All agent-facing documentation (workflow guides, state explanations, MCP tool descriptions) shall meet a 9/10 quality bar: active voice, concrete examples, no undefined jargon, scannable in under 10 seconds. Validation: agent can construct valid tool calls from descriptions alone.

- **Source**: Docs (Section 12.1, 18)
- **Priority**: Must-have
- **Risk**: Medium (quality assurance is effort-intensive)
- **Phase**: 2

---

## 6. Systemic Requirements

#### WFT-SR-001: Template Registry Caching

Type templates shall be loaded once and cached in memory for the lifetime of the KeelDB instance. Changes to templates shall require process restart to take effect. This is the critical leverage point preventing the R5 (Complexity Accumulation) loop from dominating.

- **Source**: Systems (SR-11), Python (REQ-WFT-PY-008), Architect (WFT-TE01)
- **Priority**: Must-have
- **Risk**: High (without this, 10x performance penalty)
- **Phase**: 1

#### WFT-SR-002: Pre-Computed Category Cache

The TemplateRegistry shall build a `{(type, state): category}` dictionary at load time. All category lookups during summary generation and query execution shall use this O(1) cache.

- **Source**: Systems (SR-17)
- **Priority**: Must-have
- **Risk**: High (without this, quadratic growth in summary generation)
- **Phase**: 1

#### WFT-SR-003: Pre-Computed Transition Lookup

The TemplateRegistry shall build a `{(from_state, to_state): TransitionDefinition}` dictionary per type at load time. Transition validation shall use O(1) lookups.

- **Source**: Python (REQ-WFT-PY-038)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1

#### WFT-SR-004: Soft Warning Event Tracking

Soft enforcement warnings shall be recorded as events in the events table with event_type `"warning"`. This enables measuring the warning-ignore rate to detect the Eroding Goals archetype.

- **Source**: Systems (SR-13), Python (GAP-WFT-PY-003)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1
- **Note**: Design gap resolved -- consensus: persist warnings as events for audit trail.

#### WFT-SR-005: Workflow Guides Agent-Parseable

Workflow guides shall use the structured JSON format defined in design Section 4.2. Agents shall be able to programmatically extract `when_to_use`, `typical_flow`, and `common_mistakes` from the structured response.

- **Source**: Systems (SR-12)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 2

#### WFT-SR-006: Hard Enforcement with Remediation

Hard enforcement errors shall include specific guidance on how to fix the issue, not just what failed. Error messages shall include the missing field names, the field descriptions from the template, and a suggestion to call `get_type_info` for full schema.

- **Source**: Systems (SR-14), UX (REQ-WFT-2.3.1)
- **Priority**: Must-have
- **Risk**: Low
- **Phase**: 1

#### WFT-SR-007: Pack Dependency Enforcement at Enable Time

Enabling a pack shall verify `requires_packs` are already enabled. Disabling a pack shall check if other enabled packs depend on it. Both operations shall fail with clear error messages rather than silently creating broken configurations.

- **Source**: Systems (SR-16), Architect (WFT-PS02)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 5

#### WFT-SR-008: Scaling Threshold Monitoring

The system should track summary generation time. When summary generation exceeds 200ms, `keel doctor` shall flag a performance warning with recommendations (reduce enabled packs, archive old issues).

- **Source**: Systems (Limits to Growth analysis)
- **Priority**: Nice-to-have
- **Risk**: Low
- **Phase**: 5

#### WFT-SR-009: Accidental Adversaries Mitigation -- Visibility

The system should make agent behavior visible: `keel doctor` or analytics should be able to report validation failure rates per type, most-skipped workflow steps, and warning-ignore frequency.

- **Source**: Systems (Archetype intervention, Section 15)
- **Priority**: Nice-to-have
- **Risk**: Low
- **Phase**: 5

#### WFT-SR-010: Eroding Goals Mitigation -- Monitoring

The system should expose soft-warning-ignore metrics through analytics or doctor. Teams should be able to identify which workflow steps are systematically bypassed.

- **Source**: Systems (Archetype intervention, Section 15)
- **Priority**: Nice-to-have
- **Risk**: Low
- **Phase**: 5

#### WFT-SR-011: Complexity Accumulation Mitigation -- Usage Metrics

`keel doctor` should warn about enabled-but-unused packs (packs with zero issues of their types). This helps prevent pack proliferation without utilization.

- **Source**: Systems (R5 mitigation, Section 15)
- **Priority**: Nice-to-have
- **Risk**: Low
- **Phase**: 5

#### WFT-SR-012: Category Query Implementation Strategy

Category-based queries shall initially use a two-pass approach (fetch issues, filter by category in Python). This prioritizes correctness over performance. If profiling shows need, optimize to pre-computed state lists injected into SQL WHERE IN clauses.

- **Source**: Python (CONFLICT-WFT-PY-003), Systems (Section 3.1)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 1
- **Note**: Conflict resolved -- two-pass for v1, optimize later.

#### WFT-SR-013: Template-Summary Interaction Management

Summary generation shall use the pre-computed category cache (WFT-SR-002) for all per-issue category lookups. Summary generation shall NOT perform transition validation or field requirement checking -- only category mapping for grouping.

- **Source**: Systems (R5, R1 interaction analysis)
- **Priority**: Must-have
- **Risk**: Medium
- **Phase**: 4

#### WFT-SR-014: Gradual Pack Adoption Guidance

Default enabled packs (`["core", "planning"]`) shall provide a minimal-complexity starting point. Pack enable commands shall include "Next:" hints suggesting related packs. Doctor shall suggest recommended pack combinations.

- **Source**: Systems (Section 7.2), UX (REQ-WFT-8.2.2)
- **Priority**: Should-have
- **Risk**: Low
- **Phase**: 5

#### WFT-SR-015: Phase Transition Safety

The migration from v1.0 to v1.1+ shall not change observable behavior for projects that do not enable additional packs. All category-based query changes shall be verified against test suites running with both old-format (open/in_progress/closed) and new-format (per-type) state data.

- **Source**: Systems (Section 7.1), Architect (WFT-BC01)
- **Priority**: Must-have
- **Risk**: High
- **Phase**: 1

---

## 7. Critical Risks & Design Gaps

### 7.1 Design Gaps Requiring Resolution

| # | Gap | Severity | Specialists | Recommended Resolution |
|---|-----|----------|-------------|----------------------|
| DG-1 | TemplateRegistry-KeelDB circular dependency | Critical | Architect, Python | Lazy init with runtime import in KeelDB.templates property (WFT-AR-001) |
| DG-2 | close_issue() target state for multi-done types | High | Architect, Python | Add optional `status` parameter, default to first done-category state (WFT-FR-046) |
| DG-3 | Pack disable behavior for existing issues | High | Architect, Systems, UX | Existing issues continue to work with fallback behavior; doctor warns about orphaned types (WFT-FR-023) |
| DG-4 | Category vs. state name disambiguation | High | Architect, Python | Category names (`open`, `wip`, `done`) are reserved; if input matches a category, treat as category query (WFT-FR-009) |
| DG-5 | Database vs. filesystem authority for templates | Medium | Architect | Database is seed store, filesystem layers override at load time (WFT-AR-009) |
| DG-6 | Template override merge semantics | Medium | Architect, Python | Whole-document replacement (WFT-AR-008) |
| DG-7 | State ordering for "first wip/done state" | Medium | Architect, Systems | Array order in states definition (WFT-FR-010) |
| DG-8 | Empty string treatment in field validation | Medium | Python | Treat as unpopulated/missing (WFT-FR-012) |
| DG-9 | Soft warning event persistence | Medium | Python, Systems | Persist as events with type "warning" (WFT-SR-004) |
| DG-10 | Pack install type name conflicts | Medium | Architect | Installed pack types override built-in types of same name (Layer 2 > Layer 1) |
| DG-11 | Pack dependency validation timing gap | Medium | Agent Review | Pack dependency validation is Phase 5 but types with cross-pack relationships ship in Phase 2. 3-phase gap where broken configurations are possible. Recommended: add basic dependency check at template load time (Phase 1), even if CLI commands are Phase 5. |
| DG-12 | Batch operations under workflow templates | High | Agent Review | `batch_close` and `batch_update` interaction with per-type state machines, hard/soft enforcement, and required fields is unspecified. Must define per-issue validation, partial failure handling, and aggregate response format. See WFT-FR-073. |

### 7.2 UX Specification Gaps

| # | Gap | Severity | Source | Status |
|---|-----|----------|--------|--------|
| UG-1 | `list_types` response format not specified | Medium | UX (GAP-WFT-1) | Specified in WFT-FR-026 |
| UG-2 | Dashboard type filter UI design not specified | Low | UX (GAP-WFT-2) | Deferred to Phase 4 implementation |
| UG-3 | Summary length limits not specified | Low | UX (GAP-WFT-3) | Addressed in WFT-NFR-010 |
| UG-4 | `explain_state` issue-specific mode not specified | Medium | UX (GAP-WFT-5) | Specified in WFT-FR-032 (support both modes) |
| UG-5 | Dashboard API response format not fully specified | Low | UX (GAP-WFT-6) | Deferred to Phase 4 implementation |
| UG-6 | Pack disable safety check not specified | Medium | UX (GAP-WFT-7) | Addressed in WFT-FR-023 |
| UG-7 | Current-state validation in get_valid_transitions | Low | UX (GAP-WFT-8) | Deferred to post-launch |
| UG-8 | Agent workflow prompt full text not specified | Low | UX (GAP-WFT-4) | Deferred to Phase 3 implementation |

### 7.3 Information Gaps (No Empirical Data)

| # | Gap | Severity | Source | Recommendation |
|---|-----|----------|--------|---------------|
| IG-1 | No template performance benchmarks | High | Systems (Gap A) | Benchmark validate_transition() and get_category() with and without caching |
| IG-2 | No soft warning ignore rate data | Medium | Systems (Gap B) | Instrument MCP server to log warning events |
| IG-3 | No cross-pack usage patterns | Low | Systems (Gap C) | Survey after initial adoption |
| IG-4 | No agent frustration metrics | Medium | Systems (Gap D) | Track hard enforcement errors and subsequent agent actions |
| IG-5 | No migration agent compatibility testing | High | Systems (Gap E) | Test v1.0 agent scripts against v1.1 database |

### 7.4 Critical Risks

| # | Risk | Severity | Specialists | Mitigation |
|---|------|----------|-------------|-----------|
| CR-1 | Category-aware SQL query performance at scale | High | Architect, Systems, Python | Pre-computed category cache (WFT-SR-002); two-pass queries initially (WFT-SR-012) |
| CR-2 | Backward compatibility verification | High | Architect, Systems | Comprehensive regression testing with old-format data (WFT-AR-011, WFT-SR-015) |
| CR-3 | 26-type content authoring errors | Medium | Architect, Python, Docs | Automated template validation (WFT-NFR-008); parametrized tests for all types (WFT-NFR-016) |
| CR-4 | Cognitive overload (156 possible states) | Medium | UX, Systems | Discovery tools (WFT-FR-028), workflow guides (WFT-FR-025), category abstraction (WFT-FR-006) |
| CR-5 | Eroding Goals from soft enforcement | Medium | Systems | Warning event tracking (WFT-SR-004); monitoring metrics (WFT-SR-010) |
| CR-6 | Accidental Adversaries (agents vs. validation) | Medium | Systems | Remediation guidance in errors (WFT-SR-006); workflow guides (WFT-FR-025) |
| CR-7 | Summary generation 70% slower | Medium | Systems | Caching (WFT-SR-001, WFT-SR-002); monitoring (WFT-SR-008) |
| CR-8 | Domain expertise for workflow guides | Medium | Docs | Engage domain experts; validate with agent sessions |

---

## 8. Conflict Resolution

### C-01: Dataclass vs. Pydantic for Template Data Model

**Conflict**: Python specialist evaluated both approaches for template data structures.

**Positions**:
- **Pydantic**: Automatic validation, JSON parsing, but adds dependency (~1MB)
- **Dataclass**: Stdlib, lightweight, manual validation (~100 lines), matches existing `Issue` pattern

**Resolution**: **Dataclass** (frozen). Rationale: (1) Aligns with existing codebase conventions. (2) Avoids adding a dependency (preserves NFR-023: Click-only core dependency). (3) Manual validation is manageable at ~100 lines. (4) If complexity grows, Pydantic can be adopted later without breaking changes.

**Impact**: WFT-NFR-014 specifies frozen dataclasses.

---

### C-02: Two-Pass vs. Pre-Computed Category Queries

**Conflict**: Python specialist proposed two approaches for implementing category-based queries.

**Positions**:
- **Two-pass**: Fetch all issues, filter by category in Python. Correct but potentially slow.
- **Pre-computed state lists**: Build `WHERE status IN (...)` from all category-matching states. Faster but more complex SQL.

**Resolution**: **Two-pass for initial implementation** with optimization path. Rationale: (1) Two-pass is simpler and provably correct. (2) Pre-computed optimization can be applied later if profiling shows need. (3) With O(1) category cache, the Python-side filtering is fast (microseconds per issue).

**Impact**: WFT-SR-012 specifies this strategy.

---

### C-03: Template Override -- Merge vs. Replace

**Conflict**: Architect and Python specialists both identified that override semantics are ambiguous.

**Positions**:
- **Merge**: Project override for `bug.json` with 1 custom field adds to built-in's 10 fields (11 total). More convenient but complex to implement and debug.
- **Replace**: Project override is the complete template definition. Simpler, more predictable, but requires users to copy the entire template to change one field.

**Resolution**: **Whole-document replacement**. Rationale: (1) Simpler to implement and reason about. (2) More predictable behavior. (3) If merge is needed later, it can be added as an explicit `"extends"` field (noted in design Open Question 5 as deferred). (4) Users can copy and modify built-in templates.

**Impact**: WFT-AR-008 specifies replacement semantics.

---

### C-04: Soft Warning Event Persistence

**Conflict**: Design does not specify whether soft warnings are recorded as events.

**Positions**:
- **Python specialist**: Record as events for audit trail (event_type="warning")
- **Systems specialist**: Essential for detecting Eroding Goals archetype (warning-ignore rate)
- **Design doc**: Silent on this topic

**Resolution**: **Persist as events**. Rationale: (1) Enables measuring warning-ignore rate. (2) Provides audit trail for workflow compliance review. (3) Event storage cost is minimal. (4) Events are already the audit backbone (FR-007). (5) Systems Thinker's Eroding Goals analysis depends on this data.

**Impact**: WFT-FR-017 and WFT-SR-004 specify warning event recording.

---

### C-05: State Ordering for claim_issue()

**Conflict**: Design says `claim_issue()` maps to "first wip-category state" but does not define what "first" means.

**Positions**:
- **Architecture**: Notes the ambiguity (WFT-I05 gap)
- **Systems**: Requires deterministic ordering (SR-15)
- **Python**: Proposes array-order semantics

**Resolution**: **Array order**. The first state in the `states` array with the target category is the "first" state. Rationale: (1) Array order is explicit in JSON. (2) Template authors control ordering by arrangement. (3) Simple, deterministic, no additional schema needed.

**Impact**: WFT-FR-010 specifies array-order semantics for all "first state of category" operations.

---

## 9. Implementation Phasing

Requirements are mapped to the 5 phases from the design document. Dependencies are noted.

### Phase 1: Template Engine Foundation

**Requirements**: WFT-FR-001 through WFT-FR-018, WFT-FR-038, WFT-FR-046 through WFT-FR-058, WFT-FR-066, WFT-FR-069, WFT-NFR-001 through WFT-NFR-006, WFT-NFR-008, WFT-NFR-011 through WFT-NFR-017, WFT-AR-001 through WFT-AR-004, WFT-AR-007, WFT-AR-009 through WFT-AR-012, WFT-SR-001 through WFT-SR-004, WFT-SR-006, WFT-SR-012, WFT-SR-013, WFT-SR-015, WFT-DR-008, WFT-DR-011

**Key deliverables**:
- `templates.py` module with TemplateRegistry
- `templates_data.py` with built-in pack data (placeholder -- full content in Phase 2)
- Schema migration v4 to v5
- core.py modifications (per-type status validation, transition enforcement, category-aware queries)
- Dataclass definitions (TypeTemplate, WorkflowPack, TransitionResult, etc.)
- Template validation and caching infrastructure
- Atomic transition-with-fields semantic in update_issue (WFT-FR-069)

**Dependencies**: None (foundational phase)

### Phase 2: Built-in Packs

**Requirements**: WFT-FR-008, WFT-FR-019, WFT-FR-024, WFT-FR-025, WFT-FR-074, WFT-NFR-013, WFT-NFR-018, WFT-DR-001 through WFT-DR-007, WFT-DR-012, WFT-DR-014, WFT-SR-005

**Key deliverables**:
- **Tier 1 (this phase)**: `core` (4 types) and `planning` (5 types) with complete state machines, field schemas, workflow guides, state explanations, and relationships
- **Tier 2 (follow-up)**: `risk` and `spike` packs — next priority based on agent usage patterns
- **Tier 3 (deferred)**: `requirements`, `roadmap`, `incident`, `debt`, `release` — delivered incrementally based on actual usage data
- Workflow guides with compact state diagrams and word limits per WFT-FR-031
- End-to-end workflow tests

**Dependencies**: Phase 1 (template engine must exist)

**Note (WFT-FR-074)**: Agent review identified that core + planning cover ~90% of actual agent work. Delivering all 26 types simultaneously risks quality dilution. Incremental delivery allows per-pack quality verification.

### Phase 3: Agent Interface

**Requirements**: WFT-FR-026 through WFT-FR-037, WFT-FR-039 through WFT-FR-045, WFT-FR-068, WFT-FR-070, WFT-FR-072, WFT-FR-073, WFT-FR-075, WFT-NFR-009, WFT-DR-003, WFT-DR-004, WFT-DR-010

**Key deliverables**:
- 8 new MCP tools (including `reload_templates` and `claim_next`)
- Pack-aware workflow prompt
- 11 new CLI commands
- MCP status parameter: category enum + state name acceptance (WFT-FR-033)
- Soft enforcement warning return path
- Hard enforcement errors with valid_transitions included (WFT-FR-075)
- `get_issue` with optional `include_transitions` parameter (WFT-FR-070)
- Batch operation semantics under workflow templates (WFT-FR-073)
- CLAUDE.md instruction updates

**Dependencies**: Phase 1 (engine), Phase 2 (pack data for tools to serve)

### Phase 4: Dashboard & Summary

**Requirements**: WFT-FR-060 through WFT-FR-065, WFT-FR-071, WFT-NFR-010, WFT-DR-013

**Key deliverables**:
- Summary generator: category-based vitals, per-type state display
- Summary "Needs Attention" section for issues with missing required fields (WFT-FR-071)
- Dashboard: category column default, type-filtered expanded kanban
- Dashboard: type info API endpoint
- Summary length optimization

**Dependencies**: Phase 1 (category mapping), Phase 2 (type data)

### Phase 5: Pack Management

**Requirements**: WFT-FR-020 through WFT-FR-023, WFT-FR-043, WFT-FR-059, WFT-FR-067, WFT-NFR-007, WFT-AR-006, WFT-AR-008, WFT-DR-009, WFT-SR-007 through WFT-SR-011, WFT-SR-014

**Key deliverables**:
- Pack install/enable/disable CLI and MCP commands
- Pack dependency validation
- Doctor pack checks
- Project-local template overrides
- JSONL export/import extensions
- Upgrade guide documentation
- Usage metrics and monitoring

**Dependencies**: Phases 1-3 (full engine and interface)

---

## 10. Traceability Matrix

| Req ID | Architect | Python | UX | Systems | Docs | Design Section |
|--------|:---------:|:------:|:--:|:-------:|:----:|:--------------:|
| **Functional Requirements** | | | | | | |
| WFT-FR-001 | X | X | | | | 7.1 |
| WFT-FR-002 | X | X | | | | 5.1 |
| WFT-FR-003 | X | X | X | | | 5.2 |
| WFT-FR-004 | | X | | | | 5.1 |
| WFT-FR-005 | | X | | | | 5.1 |
| WFT-FR-006 | X | | X | X | | 2.2, 10.2 |
| WFT-FR-007 | X | X | | | | 7.2 |
| WFT-FR-008 | X | X | | | X | 10.2 |
| WFT-FR-009 | X | X | X | | | 7.2 |
| WFT-FR-010 | X | X | | X | | 7.2 (gap) |
| WFT-FR-011 | X | X | X | | | 2.3, 7.1 |
| WFT-FR-012 | X | X | | | | 3.2, 7.1 |
| WFT-FR-013 | X | X | | | | 7.1 |
| WFT-FR-014 | X | X | X | | | 7.1, 8.1 |
| WFT-FR-015 | | X | X | | | 7.2 |
| WFT-FR-016 | X | X | X | | | 7.3 |
| WFT-FR-017 | X | X | X | | | 7.2 |
| WFT-FR-018 | X | X | | | | 7.2 |
| WFT-FR-019 | X | X | | | X | 10.1 |
| WFT-FR-020 | X | X | | X | | 4.2, 5.3 |
| WFT-FR-021 | X | X | X | | | 5.3 |
| WFT-FR-022 | X | X | X | | | 5.3 |
| WFT-FR-023 | | | X | X | | (gap) |
| WFT-FR-024 | X | | | | | 4.2, 4.3 |
| WFT-FR-025 | X | | X | | X | 4.1, 4.2 |
| WFT-FR-026 | X | X | X | | | 8.1 |
| WFT-FR-027 | X | X | X | | | 8.1 |
| WFT-FR-028 | X | X | X | | | 8.1 |
| WFT-FR-029 | X | X | X | | | 8.1 |
| WFT-FR-030 | X | X | X | | | 8.1 |
| WFT-FR-031 | X | X | X | | | 8.1 |
| WFT-FR-032 | X | | X | | | 8.1 |
| WFT-FR-033 | X | X | | | | 7.2 |
| WFT-FR-034 | X | X | X | | | 8.2 |
| WFT-FR-035 | X | | | | | 11.2 |
| WFT-FR-036 | X | X | X | | | 8.4 |
| WFT-FR-037 | | | X | X | X | 8.4 |
| WFT-FR-038 | | | X | | | 2.2 |
| WFT-FR-039 | X | X | X | | | 8.3 |
| WFT-FR-040 | X | X | X | | | 8.3 |
| WFT-FR-041 | X | X | X | | | 8.3 |
| WFT-FR-042 | X | X | X | | | 8.3 |
| WFT-FR-043 | X | X | X | | | 5.3, 8.3 |
| WFT-FR-044 | X | X | | | | 8.3 |
| WFT-FR-045 | X | X | X | | | 8.3 |
| WFT-FR-046 | X | X | | | | 7.2 |
| WFT-FR-047 | X | X | | X | | 7.2 |
| WFT-FR-048 | X | X | | X | | 7.2 |
| WFT-FR-049 | X | X | | | | 7.2 |
| WFT-FR-050 | X | X | | | | 7.2 |
| WFT-FR-051 | X | X | | | | 6.1, 6.2 |
| WFT-FR-052 | X | X | | | | 6.1 |
| WFT-FR-053 | X | X | | | | 6.1 |
| WFT-FR-054 | X | X | | | | 6.2 |
| WFT-FR-055 | X | X | | | | 6.2 |
| WFT-FR-056 | | X | | | | 6.2 |
| WFT-FR-057 | X | X | | | | 5.2 |
| WFT-FR-058 | X | | | | | 6.3, 11 |
| WFT-FR-059 | X | | | | | 11.2 |
| WFT-FR-060 | X | | X | | | 9.1 |
| WFT-FR-061 | X | | X | | | 9.1 |
| WFT-FR-062 | | | X | | | 9.1 |
| WFT-FR-063 | X | | X | | | 9.2 |
| WFT-FR-064 | X | | X | | | 9.2 |
| WFT-FR-065 | X | | X | | | 9.2 |
| WFT-FR-066 | X | | | | | 9.4 |
| WFT-FR-067 | X | X | | | | 9.4 |
| WFT-FR-068 | X | | | | X | 9.4 |
| **Non-Functional Requirements** | | | | | | |
| WFT-NFR-001 | | X | | X | | 7.1 |
| WFT-NFR-002 | | X | | X | | 7.1 |
| WFT-NFR-003 | | X | | | | 7.1 |
| WFT-NFR-004 | | X | | X | | 9.1 |
| WFT-NFR-005 | | | | X | | (implied) |
| WFT-NFR-006 | X | X | | | | 6.1 |
| WFT-NFR-007 | X | X | | | | 5.3 |
| WFT-NFR-008 | X | X | | | | 3.2 |
| WFT-NFR-009 | | | X | | | 8.1 |
| WFT-NFR-010 | | | X | | | 9.1 |
| WFT-NFR-011 | | | X | | | (cross-cutting) |
| WFT-NFR-012 | X | X | | | | (implied) |
| WFT-NFR-013 | | X | | | | (implied) |
| WFT-NFR-014 | | X | | | | (implied) |
| WFT-NFR-015 | | X | | | | 2.2, 2.3 |
| WFT-NFR-016 | | X | | | | (testing) |
| WFT-NFR-017 | | X | | | | (testing) |
| WFT-NFR-018 | | X | | | | (testing) |
| **Architectural Requirements** | | | | | | |
| WFT-AR-001 | X | X | | | | 7.1, 7.2 |
| WFT-AR-002 | X | | | | | 6.3 |
| WFT-AR-003 | X | | | | | 3.1, 3.2 |
| WFT-AR-004 | X | | | | | 4.1, 4.2 |
| WFT-AR-005 | X | | | | | 4.3 |
| WFT-AR-006 | X | X | | | | 5.4 |
| WFT-AR-007 | X | X | | | | 7.1 |
| WFT-AR-008 | X | X | | | | 5.1 (gap) |
| WFT-AR-009 | X | | | | | 5.1 (gap) |
| WFT-AR-010 | | X | | | | 8.4 |
| WFT-AR-011 | X | | X | X | | 11.2 |
| WFT-AR-012 | | | X | | | 11.2 |
| **Documentation Requirements** | | | | | | |
| WFT-DR-001 | | | X | | X | 4.1, 4.2 |
| WFT-DR-002 | | | X | | X | 4.2 |
| WFT-DR-003 | | | X | | X | 8.1 |
| WFT-DR-004 | | | | | X | 8.3 |
| WFT-DR-005 | | | | | X | 3.1 |
| WFT-DR-006 | | | | | X | 3.2 |
| WFT-DR-007 | | | | | X | 4.1 |
| WFT-DR-008 | | | X | | X | 8.4 |
| WFT-DR-009 | | | | | X | 11 |
| WFT-DR-010 | X | | | | X | 9.4 |
| WFT-DR-011 | | X | | | | (implied) |
| WFT-DR-012 | | | | | X | 10.3 |
| WFT-DR-013 | | | | | X | 9.1 |
| WFT-DR-014 | | | | | X | (quality) |
| **Systemic Requirements** | | | | | | |
| WFT-SR-001 | X | X | | X | | 7.1 |
| WFT-SR-002 | | | | X | | 7.1 |
| WFT-SR-003 | | X | | | | 7.1 |
| WFT-SR-004 | | X | | X | | 8.4 (gap) |
| WFT-SR-005 | | | | X | | 4.2 |
| WFT-SR-006 | | | X | X | | 8.4 |
| WFT-SR-007 | X | | | X | | 5.3 |
| WFT-SR-008 | | | | X | | (monitoring) |
| WFT-SR-009 | | | | X | | (monitoring) |
| WFT-SR-010 | | | | X | | (monitoring) |
| WFT-SR-011 | | | | X | | (monitoring) |
| WFT-SR-012 | | X | | X | | 7.2 |
| WFT-SR-013 | | | | X | | 9.1 |
| WFT-SR-014 | | | X | X | | 11.2 |
| WFT-SR-015 | X | | | X | | 11.2 |

### Source Coverage Summary

| Specialist | Requirements Sourced | Sole Source | Shared Source |
|------------|---------------------|-------------|---------------|
| Architect | 69 | 14 | 55 |
| Python | 62 | 12 | 50 |
| UX | 47 | 8 | 39 |
| Systems | 30 | 10 | 20 |
| Docs | 22 | 8 | 14 |

**Highest-corroborated requirements** (4+ specialists):
- WFT-FR-006 (Per-type states): Architect + UX + Systems + implied by Python
- WFT-FR-011 (Transition enforcement): Architect + Python + UX
- WFT-FR-016 (Fallback behavior): Architect + Python + UX
- WFT-AR-011 (Backward compatibility): Architect + UX + Systems
- WFT-SR-001 (Template caching): Architect + Python + Systems

---

*End of Consensus Requirements Document*
*Generated: 2026-02-11, updated with agent self-review*
*Input: 5 specialist position papers totaling ~293 raw requirements + 1 agent self-review*
*Output: 134 consensus requirements + 12 design gaps + 5 conflict resolutions + 8 critical risks*
*Design document: 1,037 lines across 13 sections + 2 appendices*
