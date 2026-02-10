# Workflow Templates — Design Document

**Date**: 2026-02-11
**Status**: Draft
**Scope**: Extend Keel with rich workflow templates, per-type state machines, and installable workflow packs

---

## 1. Motivation

Keel currently supports 8 issue types with a shared global state set (`open`, `in_progress`, `closed`). All types use the same workflow regardless of domain. This works for basic task tracking but falls short when projects need:

- **Requirements planning** with draft/review/approval lifecycles
- **Risk management** with assessment/mitigation/acceptance flows
- **Incident response** with triage/escalation/resolution chains
- **Release management** with freeze/test/ship gates
- **Strategic planning** with OKR tracking and roadmap items
- **Technical debt** cataloging with prioritization workflows
- **Investigation spikes** with time-boxed research and decision outputs

Rather than hardcode these workflows, the system should support **workflow templates** that carry all the domain complexity as data. The core engine stays generic; templates carry the brains.

### Design Principles

1. **Steal what's good, leave what's heavy** — Lightweight versions of ITIL, PMBOK, ISO 31000, SAFe, and CMMI. Process guidance without bureaucratic overhead.
2. **Data over code** — Workflow logic lives in template definitions, not in Python conditionals.
3. **Hybrid enforcement** — Soft defaults guide agents toward the happy path. Hard guardrails block genuinely dangerous transitions (closing a risk without a rationale, shipping a release with open blockers).
4. **Backward compatible** — Existing projects continue to work unchanged after upgrade.
5. **Agent-first** — Agents can discover workflows, ask for guidance, and navigate state machines without memorizing them.

---

## 2. Architecture Overview

### 2.1 Two-Level Template System

**Type templates** define individual issue types — their states, transitions, field schemas, and enforcement rules.

**Workflow packs** bundle related types and declare the relationships between them. A pack is a coherent workflow practice (e.g., "risk management" bundles risk + mitigation types with their inter-type relationships).

Both levels are necessary:
- A solo developer can grab the `risk` type standalone
- A team doing proper risk management installs the `risk` pack to get the full workflow with relationships and guides

### 2.2 State Category Model

Per-type states map to three universal **categories**:

| Category | Meaning | Query alias |
|----------|---------|-------------|
| `open` | Not yet started or awaiting action | `status_category=open` |
| `wip` | Actively being worked | `status_category=wip` |
| `done` | Terminal state | `status_category=done` |

Cross-type queries (`get_ready`, `get_blocked`, `list --status=open`, dashboard kanban) operate on categories. Per-type states are the refinement layer within each category.

Example: A bug in state `triage` and a risk in state `identified` both have category `open`. A query for "all open issues" returns both.

### 2.3 Enforcement Model

Templates declare two enforcement levels:

| Level | Behavior (MCP) | Behavior (CLI) |
|-------|----------------|----------------|
| **Soft** | Returns success with `warnings` array | Prints warning to stderr, proceeds |
| **Hard** | Returns error, rejects the operation | Prints error, exits non-zero |

Soft enforcement covers recommended transitions and suggested field population. Hard enforcement covers dangerous omissions — closing without required data, skipping mandatory assessment gates.

---

## 3. Type Template Schema

A type template is a JSON document defining everything about an issue type.

### 3.1 Complete Example — Bug

```json
{
  "type": "bug",
  "display_name": "Bug Report",
  "description": "Defects, regressions, and unexpected behavior",
  "pack": "core",

  "states": [
    {"name": "triage",     "category": "open"},
    {"name": "confirmed",  "category": "open"},
    {"name": "fixing",     "category": "wip"},
    {"name": "verifying",  "category": "wip"},
    {"name": "closed",     "category": "done"},
    {"name": "wont_fix",   "category": "done"}
  ],
  "initial_state": "triage",

  "transitions": [
    {"from": "triage",     "to": "confirmed",  "enforcement": "soft"},
    {"from": "triage",     "to": "wont_fix",   "enforcement": "soft"},
    {"from": "confirmed",  "to": "fixing",     "enforcement": "soft"},
    {"from": "fixing",     "to": "verifying",  "enforcement": "soft",
     "requires_fields": ["fix_verification"]},
    {"from": "verifying",  "to": "closed",     "enforcement": "hard",
     "requires_fields": ["fix_verification"]},
    {"from": "verifying",  "to": "fixing",     "enforcement": "soft"}
  ],

  "fields_schema": [
    {
      "name": "severity",
      "type": "enum",
      "options": ["critical", "major", "minor", "cosmetic"],
      "default": "major",
      "description": "Impact severity",
      "required_at": ["confirmed"]
    },
    {
      "name": "component",
      "type": "text",
      "description": "Affected subsystem"
    },
    {
      "name": "steps_to_reproduce",
      "type": "text",
      "description": "Numbered steps to trigger the bug"
    },
    {
      "name": "root_cause",
      "type": "text",
      "description": "Identified root cause (filled during triage/fixing)",
      "required_at": ["fixing"]
    },
    {
      "name": "fix_verification",
      "type": "text",
      "description": "How to verify the fix works",
      "required_at": ["verifying"]
    },
    {
      "name": "expected_behavior",
      "type": "text",
      "description": "What should happen"
    },
    {
      "name": "actual_behavior",
      "type": "text",
      "description": "What actually happens"
    },
    {
      "name": "environment",
      "type": "text",
      "description": "Python version, OS, relevant config"
    },
    {
      "name": "error_output",
      "type": "text",
      "description": "Stack trace or error message"
    }
  ],

  "suggested_children": ["task"],
  "suggested_labels": ["regression", "ux", "perf", "security"]
}
```

### 3.2 Template Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Unique type identifier (e.g., `"bug"`, `"risk"`) |
| `display_name` | string | Yes | Human-readable name |
| `description` | string | Yes | What this type is for |
| `pack` | string | Yes | Which pack this type belongs to |
| `states` | array | Yes | Valid states with category mappings |
| `states[].name` | string | Yes | State identifier |
| `states[].category` | enum | Yes | One of: `"open"`, `"wip"`, `"done"` |
| `initial_state` | string | Yes | State assigned on creation |
| `transitions` | array | Yes | Valid state transitions |
| `transitions[].from` | string | Yes | Source state |
| `transitions[].to` | string | Yes | Target state |
| `transitions[].enforcement` | enum | Yes | `"hard"` or `"soft"` |
| `transitions[].requires_fields` | array | No | Fields that must be populated for this transition |
| `fields_schema` | array | Yes | Custom field definitions |
| `fields_schema[].name` | string | Yes | Field identifier |
| `fields_schema[].type` | enum | Yes | `"text"`, `"enum"`, `"number"`, `"date"`, `"list"`, `"boolean"` |
| `fields_schema[].options` | array | No | Valid values for enum fields |
| `fields_schema[].default` | any | No | Default value |
| `fields_schema[].description` | string | No | Field description |
| `fields_schema[].required_at` | array | No | States where this field must be populated |
| `suggested_children` | array | No | Types commonly created as children |
| `suggested_labels` | array | No | Commonly used labels for this type |

---

## 4. Workflow Pack Schema

A workflow pack bundles types and declares their relationships.

### 4.1 Complete Example — Risk Management

```json
{
  "pack": "risk",
  "version": "1.0",
  "display_name": "Risk Management",
  "description": "ISO 31000-lite: identify, assess, and manage project risks",
  "requires_packs": ["core"],

  "types": {
    "risk": { "...": "full type template (see Section 3)" },
    "mitigation": { "...": "full type template" }
  },

  "relationships": [
    {
      "name": "mitigation_for",
      "from_types": ["mitigation"],
      "to_types": ["risk"],
      "mechanism": "parent_id",
      "description": "Mitigation actions belong to their parent risk"
    },
    {
      "name": "risk_threatens",
      "from_types": ["risk"],
      "to_types": ["*"],
      "mechanism": "dependency",
      "description": "A risk threatens any issue — links risk to the thing at risk"
    }
  ],

  "cross_pack_relationships": [
    {
      "name": "spike_investigates_risk",
      "from_types": ["spike"],
      "from_packs": ["spike"],
      "to_types": ["risk"],
      "mechanism": "dependency",
      "description": "A spike can investigate whether a risk is real"
    }
  ],

  "guide": {
    "overview": "Lightweight risk management for software projects. Track things that might go wrong, assess their likelihood and impact, and decide whether to mitigate, accept, or watch them.",
    "when_to_use": "Create a risk when you identify something uncertain that could negatively affect the project — technical unknowns, dependency concerns, architectural gambles, external factors.",
    "states_explained": {
      "identified": "You've spotted something risky. Capture it with a title and description. Don't worry about scoring yet.",
      "assessing": "You're actively investigating the risk. Research likelihood, figure out what the blast radius would be. Fill in risk_score (1-5) and impact fields.",
      "assessed": "Assessment complete. The risk is understood and scored. Decision time: mitigate, accept, or escalate.",
      "mitigating": "You're actively doing something to reduce the risk. Create mitigation child issues for the specific actions.",
      "mitigated": "Mitigation actions are complete. Residual risk is acceptable.",
      "accepted": "Deliberately choosing to live with this risk. Requires risk_owner and acceptance_rationale — someone has to own the decision.",
      "escalated": "Risk is beyond your scope. Needs attention from project leadership or a different team.",
      "retired": "Risk is no longer relevant. The feature was descoped, the technology changed, or the risk window passed."
    },
    "typical_flow": "identified → assessing → assessed → mitigating → mitigated. Most risks follow this path. Quick risks can skip straight from identified to accepted if they're low-impact and well-understood.",
    "tips": [
      "Link risks to the issues they threaten using a dependency",
      "Create a spike first if you're not sure whether something IS a risk",
      "P0/P1 risks should have mitigations, not just acceptance",
      "Review open risks at the start of each phase"
    ],
    "common_mistakes": [
      "Assessing without filling in risk_score — the whole point is to quantify",
      "Accepting high-impact risks without rationale — someone has to own it",
      "Leaving risks in 'identified' forever — assess or retire them"
    ]
  }
}
```

### 4.2 Pack Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pack` | string | Yes | Unique pack identifier |
| `version` | string | Yes | Semver version |
| `display_name` | string | Yes | Human-readable name |
| `description` | string | Yes | What this pack provides |
| `requires_packs` | array | No | Packs that must be enabled for this pack to work |
| `types` | object | Yes | Map of type name → type template |
| `relationships` | array | No | Intra-pack type relationships |
| `relationships[].name` | string | Yes | Relationship identifier |
| `relationships[].from_types` | array | Yes | Source types (`["*"]` for any) |
| `relationships[].to_types` | array | Yes | Target types (`["*"]` for any) |
| `relationships[].mechanism` | enum | Yes | `"parent_id"`, `"dependency"`, `"label"`, `"field_ref"` |
| `relationships[].description` | string | Yes | What this relationship means |
| `cross_pack_relationships` | array | No | Relationships involving types from other packs |
| `cross_pack_relationships[].from_packs` | array | No | Source pack(s) |
| `cross_pack_relationships[].to_packs` | array | No | Target pack(s) |
| `guide` | object | No | Narrative workflow guide for agent consumption |
| `guide.overview` | string | Yes | What this workflow is for |
| `guide.when_to_use` | string | Yes | When to create issues of these types |
| `guide.states_explained` | object | Yes | Map of state name → explanation |
| `guide.typical_flow` | string | Yes | The happy path described narratively |
| `guide.tips` | array | No | Best practices |
| `guide.common_mistakes` | array | No | Anti-patterns to avoid |

### 4.3 Relationship Mechanisms

All relationships are implemented using existing Keel primitives — no new tables:

| Mechanism | Keel primitive | Semantics |
|-----------|---------------|-----------|
| `parent_id` | `issues.parent_id` | Structural containment: "belongs to", "is part of" |
| `dependency` | `dependencies` table | Execution ordering: "blocks", "is blocked by", "verifies", "investigates" |
| `label` | `labels` table | Soft grouping: "tagged as" |
| `field_ref` | `issues.fields` JSON (e.g., `{"release_id": "rel-a3f9b2"}`) | Arbitrary cross-reference |

---

## 5. Storage & Resolution

### 5.1 Three-Layer Loading

Templates are resolved from three layers, where later layers override earlier:

```
Layer 1: Built-in       src/keel/templates/      (ships with keel)
Layer 2: Installed packs .keel/packs/             (installable pack files)
Layer 3: Project local   .keel/templates/          (per-project overrides)
```

**Resolution rules:**
1. Keel loads built-in type templates and pack definitions from Python source
2. Scans `.keel/packs/*.json` for installed packs — these add new types and can override built-in type definitions
3. Scans `.keel/templates/*.json` for project-local type overrides
4. Last definition wins per type name. A project can override `bug.json` to customize the state machine without modifying keel source.

### 5.2 Project Configuration

`.keel/config.json` gains an `enabled_packs` field:

```json
{
  "prefix": "myproject",
  "version": 1,
  "enabled_packs": ["core", "planning", "risk", "spike"],
  "workflow_states": ["open", "in_progress", "closed"]
}
```

- `enabled_packs`: Only types from enabled packs are available. Defaults to `["core", "planning"]` for backward compatibility.
- `workflow_states`: Retained for projects not using per-type states. Acts as the fallback for types without explicit state definitions.

### 5.3 Pack Installation

```bash
# Install from a local file
keel pack install ./itil-lite.json

# Enable/disable
keel pack enable incident
keel pack disable roadmap

# List installed packs
keel packs
```

Installing a pack copies the JSON file to `.keel/packs/` and validates it against the schema. The `keel doctor` command checks for missing pack dependencies.

### 5.4 Filesystem Layout

```
.keel/
  keel.db                     # SQLite database (unchanged)
  config.json                 # Project config (+ enabled_packs)
  context.md                  # Pre-computed summary (unchanged)
  keel.log                    # Structured log (unchanged)
  packs/                      # Installed workflow packs
    incident.json
    custom-workflow.json
  templates/                  # Project-local type overrides
    bug.json                  # Custom bug workflow for this project
```

---

## 6. Database Changes

### 6.1 Schema Migration (v4 → v5)

Minimal schema additions — the `issues` table does not change.

```sql
-- Rich type template registry (replaces simple templates table)
CREATE TABLE IF NOT EXISTS type_templates (
    type          TEXT PRIMARY KEY,
    pack          TEXT NOT NULL DEFAULT 'core',
    definition    TEXT NOT NULL,   -- Full JSON template
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Pack registry
CREATE TABLE IF NOT EXISTS packs (
    name          TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    definition    TEXT NOT NULL,   -- Full JSON pack definition
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    enabled       BOOLEAN NOT NULL DEFAULT 1
);
```

### 6.2 Migration Steps

1. Create `type_templates` and `packs` tables
2. Migrate existing `templates` rows into `type_templates` with enriched definitions (adding default states, transitions)
3. Seed built-in packs into `packs` table
4. Drop old `templates` table
5. Existing issues remain untouched — their `status` values are valid states in the core pack

### 6.3 What Stays the Same

| Table | Changes |
|-------|---------|
| `issues` | None. `status` stays TEXT, `fields` stays JSON, `type` still references templates |
| `dependencies` | None |
| `events` | None |
| `comments` | None |
| `labels` | None |
| `issues_fts` | None |

---

## 7. Core Engine Changes

### 7.1 New Module: `src/keel/templates.py` (~300-400 lines)

```python
class TemplateRegistry:
    """Loads, caches, and queries workflow templates and packs."""

    def load(self, db: KeelDB, keel_dir: Path) -> None:
        """Three-layer template resolution."""

    def get_type(self, type_name: str) -> TypeTemplate | None:
        """Get a type template by name."""

    def get_pack(self, pack_name: str) -> WorkflowPack | None:
        """Get a pack by name."""

    def list_types(self) -> list[TypeTemplate]:
        """All types from enabled packs."""

    def list_packs(self) -> list[WorkflowPack]:
        """All enabled packs."""

    def validate_transition(
        self, type_name: str, from_state: str, to_state: str, fields: dict
    ) -> TransitionResult:
        """Check if a transition is valid.

        Returns:
            TransitionResult with:
              - allowed: bool
              - enforcement: "hard" | "soft" | None
              - missing_fields: list[str]
              - warnings: list[str]
        """

    def get_valid_transitions(
        self, type_name: str, current_state: str, fields: dict
    ) -> list[TransitionOption]:
        """All valid transitions from current state with readiness info."""

    def validate_fields_for_state(
        self, type_name: str, state: str, fields: dict
    ) -> list[str]:
        """Return list of fields required at this state but not yet populated."""

    def get_initial_state(self, type_name: str) -> str:
        """Initial state for a type. Falls back to 'open' if no template."""

    def get_category(self, type_name: str, state: str) -> str:
        """Map a per-type state to its universal category (open/wip/done)."""
```

### 7.2 Changes to `core.py`

| Method | Change |
|--------|--------|
| `_validate_status()` | Checks per-type states via `TemplateRegistry` instead of global list |
| `create_issue()` | Uses `get_initial_state()` instead of hardcoded `"open"` |
| `update_issue()` | Calls `validate_transition()` before status changes. Hard failures raise `ValueError`. Soft failures emit warning events. |
| `close_issue()` | Validates target state is a `done`-category state for this type |
| `claim_issue()` | Maps to the first `wip`-category state (or uses explicit state if provided) |
| `list_issues(status=)` | Accepts both categories (`"open"`) and specific states (`"triage"`). Category queries use `get_category()` lookups. |
| `get_ready()` | Uses category `open` instead of literal `"open"` string |
| `get_blocked()` | Uses category `open` instead of literal `"open"` string |

New methods on `KeelDB`:

| Method | Purpose |
|--------|---------|
| `get_valid_transitions(issue_id)` | Delegates to `TemplateRegistry` with current issue state and fields |
| `validate_issue(issue_id)` | Full template validation — missing required fields, transition suggestions |

### 7.3 Backward Compatibility

The `TemplateRegistry` provides fallback behavior:
- Types without templates use the global `workflow_states` from config (default: `open/in_progress/closed`)
- All transitions are allowed (soft) for types without transition definitions
- No field validation for types without `required_at` declarations
- The `core` pack's `task` type uses `open/in_progress/closed` as its states, so existing task issues work unchanged

---

## 8. Interface Changes

### 8.1 New MCP Tools

| Tool | Description |
|------|-------------|
| `get_type_info` | Full template for a type: states, transitions, fields, relationships. Replaces `get_template`. |
| `get_valid_transitions` | Given an issue ID, returns valid next states with enforcement level, required fields, and readiness status. |
| `list_packs` | Enabled packs with their types and descriptions. |
| `list_types` | All available types across enabled packs. |
| `validate_issue` | Check an issue against its template — missing fields, suggested transitions. |
| `get_workflow_guide` | Full narrative guide for a pack — overview, state explanations, tips, mistakes. |
| `explain_state` | Quick contextual help: "What does this state mean and what should I do next?" |

**Key tool: `get_valid_transitions`**

This is how agents navigate workflows without memorizing state machines. Response format:

```json
{
  "issue_id": "proj-a3f9b2",
  "type": "bug",
  "current_state": "fixing",
  "current_category": "wip",
  "valid_transitions": [
    {
      "to": "verifying",
      "category": "wip",
      "enforcement": "soft",
      "requires_fields": ["fix_verification"],
      "missing_fields": ["fix_verification"],
      "ready": false
    },
    {
      "to": "confirmed",
      "category": "open",
      "enforcement": "soft",
      "requires_fields": [],
      "missing_fields": [],
      "ready": true
    }
  ]
}
```

**Key tool: `explain_state`**

Contextual help mid-workflow. Response format:

```json
{
  "type": "risk",
  "state": "assessed",
  "category": "open",
  "explanation": "Assessment complete. The risk is understood and scored. Decision time: mitigate, accept, or escalate.",
  "next_steps": [
    "If you can reduce the risk: create mitigation issues, move to 'mitigating'",
    "If the risk is low-impact and acceptable: move to 'accepted' (requires risk_owner + rationale)",
    "If this is beyond your scope: move to 'escalated'"
  ],
  "required_fields_status": {
    "risk_score": {"required": true, "populated": true, "value": "3"},
    "impact": {"required": true, "populated": true, "value": "Could delay Phase 2 by a week"}
  }
}
```

### 8.2 Updated MCP Prompt

The `workflow` prompt becomes pack-aware:

```
Prompt: "workflow"
Arguments: {"pack": "risk"} (optional — omit for general guidance)
```

Without arguments, returns guidance for all enabled packs. With a pack argument, returns the pack's full workflow guide.

### 8.3 New CLI Commands

| Command | Description |
|---------|-------------|
| `keel types` | List available types and their states |
| `keel type-info <type>` | Show full template for a type |
| `keel transitions <issue_id>` | Show valid next states for an issue |
| `keel packs` | List installed/enabled packs |
| `keel pack enable <name>` | Enable a pack |
| `keel pack disable <name>` | Disable a pack |
| `keel pack install <path>` | Install a pack from a JSON file |
| `keel validate <issue_id>` | Check issue against its template |
| `keel guide <pack>` | Print workflow guide narrative |
| `keel guide <pack> --state <state>` | Explain a specific state and next steps |

### 8.4 Enforcement UX

**Soft enforcement** (recommended but not required):
- MCP: Returns success with `"warnings": ["Transition triage→closed is not in the standard workflow. Recommended path: triage→confirmed→fixing→verifying→closed"]`
- CLI: Prints warning to stderr, proceeds with the operation

**Hard enforcement** (required):
- MCP: Returns error `{"error": "Cannot transition to 'verified': missing required fields: fix_verification"}`
- CLI: Prints error, exits non-zero

---

## 9. Affected Subsystems

### 9.1 Summary Generator (`summary.py`)

- "Vitals" section groups by category (open/wip/done) across all types
- "Ready to Work" and "In Progress" sections include the specific state in parentheses:
  ```
  ## Ready to Work
  - P1 proj-a3f9b2 [bug] "Login crash" (confirmed)
  - P2 proj-c8d1e0 [risk] "API rate limits" (assessed)
  ```
- "Recent Activity" shows state transitions with specific state names

### 9.2 Dashboard (`dashboard.py`)

- **Default kanban**: 3 columns by category (open/wip/done) — works unchanged
- **Type-filtered view**: When filtering to a single type, expands to that type's full state columns (e.g., filtering to `bug` shows `triage | confirmed | fixing | verifying | closed | wont_fix`)
- New API endpoint: `GET /api/type/{type_name}` — returns template info for the dashboard to build dynamic views

### 9.3 Analytics (`analytics.py`)

No changes required. Cycle time and lead time calculations use `status_changed` events and timestamps. Richer state machines produce more events, providing better analytics granularity automatically.

### 9.4 Install (`install.py`)

- `keel install` seeds default packs
- `keel doctor` checks: pack dependency validation, orphaned type references, template schema validation
- CLAUDE.md instructions updated to mention pack-specific workflows

### 9.5 Migration (`migrate.py`)

No changes. Beads migration produces issues with `open/in_progress/closed` states, which remain valid in the `core` pack.

---

## 10. Complete Pack Reference

### 10.1 Pack Overview

| Pack | Inspired by | Types | Description |
|------|------------|-------|-------------|
| **core** | — | task, bug, feature, epic | Foundational software development types |
| **planning** | PMBOK-lite | milestone, phase, step, work_package, deliverable | Hierarchical project planning |
| **requirements** | CMMI/IEEE-lite | requirement, test_case, decision_record | Requirements lifecycle management |
| **risk** | ISO 31000-lite | risk, mitigation | Risk identification, assessment, and response |
| **roadmap** | SAFe/OKR-lite | theme, objective, key_result, initiative | Strategic planning and OKR tracking |
| **incident** | ITIL-lite | incident, problem, change_request | Incident response and change management |
| **debt** | — | tech_debt, refactoring | Technical debt cataloging and remediation |
| **spike** | — | spike, finding | Time-boxed investigation and research |
| **release** | — | release, changelog_entry | Release coordination and tracking |

Total: **9 packs, 26 types**.

### 10.2 State Machines

#### Core Pack

**task**:
```
open(O) → in_progress(W) → closed(D)
```

**bug**:
```
triage(O) → confirmed(O) → fixing(W) → verifying(W) → closed(D)
  ↘ wont_fix(D)                    ↗ (back to fixing)
Hard: verifying→closed requires fix_verification
Hard: confirmed requires severity
```

**feature**:
```
proposed(O) → approved(O) → building(W) → reviewing(W) → done(D)
  ↘ deferred(D)                              ↗ (back to building)
Hard: approved requires acceptance_criteria
```

**epic**:
```
open(O) → in_progress(W) → closed(D)
```

#### Planning Pack

**milestone**:
```
planning(O) → active(W) → closing(W) → completed(D)
```

**phase**:
```
pending(O) → active(W) → completed(D)
  ↘ skipped(D)
```

**step**:
```
pending(O) → in_progress(W) → completed(D)
  ↘ skipped(D)
```

**work_package**:
```
defined(O) → assigned(O) → executing(W) → delivered(D)
```

**deliverable**:
```
planned(O) → producing(W) → reviewing(W) → accepted(D)
```

#### Requirements Pack

**requirement**:
```
draft(O) → review(O) → approved(O) → implementing(W) → verified(D)
  ↘ rejected(D)                         ↘ deferred(D)
Hard: approved requires acceptance_criteria
Hard: verified requires at least one linked test_case
```

**test_case**:
```
draft(O) → ready(O) → passing(D)
                     → failing(W)
                     → blocked(O)
```

**decision_record**:
```
proposed(O) → accepted(D) → deprecated(D)
                           → superseded(D)
```

#### Risk Pack

**risk**:
```
identified(O) → assessing(W) → assessed(O) → mitigating(W) → mitigated(D)
                                  ↘ accepted(D)
                                  ↘ escalated(O)
                                  ↘ retired(D)
Hard: assessed requires risk_score and impact
Hard: accepted requires risk_owner and acceptance_rationale
```

**mitigation**:
```
planned(O) → executing(W) → completed(D)
                           → ineffective(O)
```

#### Roadmap Pack

**theme**:
```
active(O) → completed(D)
          → retired(D)
```

**objective**:
```
draft(O) → committed(O) → tracking(W) → achieved(D)
                                       → missed(D)
```

**key_result**:
```
defined(O) → tracking(W) → met(D)
                          → missed(D)
```

**initiative**:
```
proposed(O) → approved(O) → executing(W) → delivered(D)
                                          → cancelled(D)
```

#### Incident Pack

**incident**:
```
reported(O) → triaging(W) → active(W) → resolved(D) → closed(D)
                               ↘ escalated(O)
Hard: resolved requires resolution_summary
Soft: resolved should link to a problem
```

**problem**:
```
identified(O) → investigating(W) → root_caused(O) → resolving(W) → resolved(D)
```

**change_request**:
```
requested(O) → assessing(O) → approved(O) → implementing(W) → validating(W) → complete(D)
                 ↘ rejected(D)
Hard: approved requires impact_assessment and rollback_plan
Hard: complete requires validation_result
```

#### Debt Pack

**tech_debt**:
```
cataloged(O) → prioritized(O) → scheduled(O) → addressing(W) → resolved(D)
                                                              → accepted(D)
Soft: prioritized should have impact_score and effort_estimate
Hard: accepted requires acceptance_rationale
```

**refactoring**:
```
planned(O) → executing(W) → validating(W) → completed(D)
```

#### Spike Pack

**spike**:
```
proposed(O) → investigating(W) → concluded(D) → actioned(D)
                                → abandoned(D)
Hard: concluded requires findings
Soft: concluded should have recommendation
Soft: actioned should link to spawned work items
```

**finding**:
```
draft(O) → published(D)
```

#### Release Pack

**release**:
```
planning(O) → executing(W) → freezing(W) → testing(W) → shipping(W) → shipped(D)
                                ↘ blocked(O)
Hard: shipping requires all child items closed or deferred
Soft: shipped should have changelog_entries
```

**changelog_entry**:
```
draft(O) → final(D)
```

### 10.3 Cross-Pack Relationships

```
Core ←──────────── Planning
  bug, feature,       milestone → phase → step (parent_id)
  task → epic          work_package → milestone (parent_id)
  (parent_id)          deliverable → work_package (dependency)

Core ←──────────── Requirements
  requirement ──────→ test_case (dependency: "verifies")
  decision_record ──→ requirement (field_ref: "decided_by")

Core ←──────────── Risk
  risk ─────────────→ any issue (dependency: "threatens")
  mitigation ───────→ risk (parent_id)

Core ←──────────── Roadmap
  initiative ───────→ objective → theme (parent_id)
  key_result ───────→ objective (dependency: "measures")
  initiative ───────→ epic, feature (dependency: "implements")

Incident ──────────→ Core
  incident → problem → change_request (dependency chain)
  change_request ───→ bug, feature, task (dependency: "implements")

Debt ──────────────→ Core
  tech_debt ────────→ any issue (field_ref: "affects")
  refactoring ──────→ tech_debt (parent_id)

Spike ─────────────→ Any
  spike ────────────→ any issue (dependency: "investigates")
  finding ──────────→ spike (parent_id)
  spike ────────────→ task, bug, feature, mitigation, change_request (dependency: "spawns")

Release ───────────→ Core
  bug, feature, task → release (parent_id: "assigned to")
  changelog_entry ──→ release (parent_id)

Full ITIL chain:
  incident → problem → change_request → bug/feature → release
```

---

## 11. Migration Path

### 11.1 Upgrade from Current Keel

1. **Schema migration v5**: Creates `type_templates` and `packs` tables. Migrates data from old `templates` table.
2. **Pack seeding**: All 9 built-in packs loaded. Only `core` and `planning` enabled by default.
3. **Existing issues**: Remain untouched. `status=open/in_progress/closed` are valid states in the `core` pack's `task`, `epic` types and the `planning` pack's `step`, `phase`, `milestone` types.
4. **Config update**: `config.json` gets `"enabled_packs": ["core", "planning"]` added.
5. **Template data migration**: Old `templates` rows mapped to new `type_templates` with enriched definitions.

### 11.2 Backward Compatibility Guarantees

- Projects that do nothing after upgrade work identically
- All existing queries (`list --status=open`, `get_ready`, `get_blocked`) work unchanged via category mapping
- The `open/in_progress/closed` state set remains valid for core types
- `context.md` format remains compatible (enhanced with specific states in parentheses)
- JSONL export/import format extended but backward compatible
- MCP `get_template` tool remains functional (delegates to `get_type_info`)

---

## 12. Implementation Phases

### Phase 1: Template Engine Foundation
- New `templates.py` module with `TemplateRegistry`
- Schema migration v5 (type_templates + packs tables)
- Three-layer loading (built-in → packs → project)
- Per-type state validation in `core.py`
- Transition validation (soft + hard enforcement)
- Field requirement validation

### Phase 2: Built-in Packs
- Define all 9 packs with full type templates, state machines, and field schemas
- Pack guides (narrative documentation)
- Relationship declarations

### Phase 3: Agent Interface
- New MCP tools: `get_type_info`, `get_valid_transitions`, `list_packs`, `list_types`, `validate_issue`
- New MCP tools: `get_workflow_guide`, `explain_state`
- Updated `workflow` prompt (pack-aware)
- New CLI commands: `types`, `type-info`, `transitions`, `packs`, `pack`, `validate`, `guide`

### Phase 4: Dashboard & Summary
- Summary generator: specific states in parentheses, category-based vitals
- Dashboard: type-filtered kanban with per-type state columns
- Dashboard: pack info display

### Phase 5: Pack Management
- `keel pack install/enable/disable` commands
- MCP equivalents for pack management
- `keel doctor` pack validation checks
- Project-local template overrides

---

## 13. Open Questions

1. **Pack versioning**: When a built-in pack is updated in a new keel release, how do we handle projects with customized versions? Options: merge, prompt, keep-local.
2. **Cross-pack dependency validation**: Should `keel doctor` warn if you have `incident` pack enabled but not `core`? (Yes — `requires_packs` enforces this.)
3. **State history migration**: When a project enables a new pack and re-types existing issues (e.g., changing a `task` to a `risk`), should the status be automatically mapped to the new type's initial state?
4. **Dashboard pack views**: Should each pack get its own dashboard tab/view, or one unified view with pack-based filtering?
5. **Template inheritance**: Should types be able to extend other types (e.g., `security_bug` extends `bug` with additional fields)? Deferred for now — keep it simple.

---

## Appendix A: Example Session — Agent Using Risk Workflow

```
Agent: [calls get_workflow_guide(pack="risk")]
Keel:  Returns full risk management guide

Agent: [calls create_issue(title="SQLite WAL may not work on NFS", type="risk")]
Keel:  Created proj-a1b2c3 in state "identified"

Agent: [calls get_valid_transitions(id="proj-a1b2c3")]
Keel:  {to: "assessing", enforcement: "soft", requires: [], ready: true}

Agent: [calls update_issue(id="proj-a1b2c3", status="assessing")]
Keel:  Updated. State: assessing (category: wip)

Agent: [calls update_issue(id="proj-a1b2c3", status="assessed",
         fields={"risk_score": 3, "impact": "Data corruption on NFS mounts"})]
Keel:  Updated. State: assessed (category: open)

Agent: [calls explain_state(type="risk", state="assessed")]
Keel:  "Decision time: mitigate, accept, or escalate..."

Agent: [calls create_issue(title="Add NFS detection warning", type="mitigation",
         parent_id="proj-a1b2c3")]
Keel:  Created proj-d4e5f6 as child of proj-a1b2c3

Agent: [calls update_issue(id="proj-a1b2c3", status="mitigating")]
Keel:  Updated. State: mitigating (category: wip)

Agent: [completes the mitigation work, closes proj-d4e5f6]

Agent: [calls update_issue(id="proj-a1b2c3", status="mitigated")]
Keel:  Updated. State: mitigated (category: done)
```

---

## Appendix B: Example Session — Agent Using Incident → Release Chain

```
Agent: [calls create_issue(title="Production API returning 500s", type="incident")]
Keel:  Created proj-i1 in state "reported"

Agent: [triages, investigates, finds root cause]
Agent: [calls create_issue(title="Connection pool exhaustion under load",
         type="problem", deps=["proj-i1"])]
Keel:  Created proj-p1, linked to incident

Agent: [calls create_issue(title="Implement connection pool limits",
         type="change_request", deps=["proj-p1"])]
Keel:  Created proj-cr1, linked to problem

Agent: [calls update_issue(id="proj-cr1", status="approved",
         fields={"impact_assessment": "Low risk, config change only",
                 "rollback_plan": "Revert connection pool config"})]
Keel:  Updated. Hard constraint satisfied.

Agent: [calls create_issue(title="Fix connection pool config", type="bug",
         deps=["proj-cr1"])]
Keel:  Created proj-b1, linked to change request

Agent: [fixes the bug, closes proj-b1]
Agent: [closes proj-cr1 (complete), proj-p1 (resolved), proj-i1 (resolved→closed)]

Agent: [calls update_issue(id="proj-b1", parent_id="proj-rel-2.3")]
Keel:  Bug assigned to release v2.3

Full chain: incident → problem → change_request → bug → release
```
