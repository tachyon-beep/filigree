# Workflow Templates

Every issue in filigree has a **type**, and every type has a **state machine** defining valid transitions. Templates enforce these rules so agents and humans follow consistent workflows.

## Contents

- [How It Works](#how-it-works)
- [Runtime Semantics Contract](#runtime-semantics-contract)
- [Packs](#packs)
- [Core Pack](#core-pack) — task, bug, feature, epic
- [Planning Pack](#planning-pack) — milestone, phase, step, work_package, deliverable
- [Requirements Pack](#requirements-pack) — requirement, acceptance_criterion
- [Risk Pack](#risk-pack) — risk, mitigation
- [Spike Pack](#spike-pack) — spike, finding
- [Roadmap Pack](#roadmap-pack) — theme, objective, key_result
- [Incident Pack](#incident-pack) — incident, postmortem
- [Debt Pack](#debt-pack) — debt_item, remediation
- [Release Pack](#release-pack) — release, release_item
- [Enforcement Levels](#enforcement-levels)
- [Discovering Workflows](#discovering-workflows)
- [Priority Scale](#priority-scale)
- [Template Loading](#template-loading)

## How It Works

1. Each issue type defines a set of **states** (e.g., `open`, `in_progress`, `closed`)
2. Each state belongs to a **category**: `open`, `wip` (work-in-progress), or `done`
3. **Transitions** define which state changes are valid
4. Transitions can be **hard** (blocked if invalid) or **soft** (allowed with a warning)
5. Some transitions **require fields** to be populated before they're allowed

## Runtime Semantics Contract

Templates are the runtime contract for issue state, not just documentation.
Known issue types must be registered by an enabled pack or project-local
template; creating an issue with an unknown type is rejected. New issues start
in the type's `initial_state`.

Each concrete state has a universal category: `open`, `wip`, or `done`.
Consumers should use `status_category` for broad workflow logic and keep
`status` for the literal type-specific state. Category-aware commands such as
ready queues, stale-claim discovery, blocker checks, and archive cleanup use
the type-aware category mapping so shared state names can mean different
categories for different types.

Status updates validate against the current type's transition graph. A
transition that is not declared is rejected and callers should inspect
`get_valid_transitions` / `filigree transitions <id>` for the allowed next
states. Transition field requirements are evaluated against the issue fields
after applying the requested update, so callers may set the target status and
its required fields in one write.

Hard and soft enforcement share the same field vocabulary:

- **Hard** transitions fail when required fields or fields required at the
  target state are missing.
- **Soft** transitions succeed, return the warning in `data_warnings[]`, and
  record the same advisory once as a `transition_warning` event.

Closing is a transition into a done-category state. If no close target is
provided, Filigree starts with the type's first done-category state; when that
default is not reachable but exactly one done-category transition is reachable,
it auto-selects that reachable target and returns a warning. Ambiguous close
targets remain caller choices. Close reasons are stored in
`fields.close_reason`; a reason-only close also records the reason on the
status-change event so history readers can display it without reconstructing a
separate field event.

Reopening only works from done-category states. It returns the issue to the
most recent non-done state that transitioned into done, falling back to the
type's `initial_state` when no usable event exists. Reopen clears close-only
fields such as `close_reason`.

Claiming and handoff also respect categories. Open-category issues are
claimable for new work; released wip-category issues are claimable for handoff.
`start_work` / `start_next_work` default to the unique reachable wip-category
target and require an explicit target when several wip states are possible.
`release_claim` reverts wip-category work to the template-defined open
predecessor by default so unassigned work returns to ready discovery; callers
may opt out with `revert_status=false`.

## Packs

Types are grouped into **packs** — bundles of related types that can be enabled or disabled per project.

### Enabled by Default

| Pack | Types | Purpose |
|------|-------|---------|
| `core` | task, bug, feature, epic | Day-to-day development work |
| `planning` | milestone, phase, step, work_package, deliverable | Hierarchical project planning |

### Additional Packs

| Pack | Types | Purpose |
|------|-------|---------|
| `requirements` | requirement, acceptance_criterion | Requirements lifecycle management |
| `risk` | risk, mitigation | ISO 31000-lite risk management |
| `spike` | spike, finding | Time-boxed investigation and research |
| `roadmap` | theme, objective, key_result | OKR-lite strategic planning |
| `incident` | incident, postmortem | ITIL-lite incident response |
| `debt` | debt_item, remediation | Technical debt catalog and remediation |
| `release` | release, release_item | Release lifecycle management |

Enable packs in `.filigree/config.json`:

```json
{
  "prefix": "myproj",
  "version": 1,
  "enabled_packs": ["core", "planning", "risk", "spike"]
}
```

## Core Pack

### Task

General-purpose work item.

```
open(O) ──> in_progress(W) ──> closed(D)
       \──> closed(D)
```

All transitions are **soft** enforced.

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `context` | text | Background context |
| `done_definition` | text | How to know this is complete |
| `estimated_minutes` | number | Rough time estimate |

### Bug

Defects, regressions, and unexpected behavior.

```
triage(O) ──> confirmed(O) ──> fixing(W) ──> verifying(W) ──> closed(D)
         \──> wont_fix(D)                 \──> fixing(W)  [loop]
         \──> not_a_bug(D)
```

**Hard gates:**
- `verifying` → `closed` requires `fix_verification`

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `severity` | enum: critical, major, minor, cosmetic | confirmed | Impact severity |
| `component` | text | — | Affected subsystem |
| `steps_to_reproduce` | text | — | Numbered steps to trigger the bug |
| `root_cause` | text | fixing | Identified root cause |
| `fix_verification` | text | verifying | How to verify the fix works |
| `expected_behavior` | text | — | What should happen |
| `actual_behavior` | text | — | What actually happens |
| `environment` | text | — | Python version, OS, relevant config |
| `error_output` | text | — | Stack trace or error message |

### Feature

User-facing functionality.

```
proposed(O) ──> approved(O) ──> building(W) ──> reviewing(W) ──> done(D)
           \──> deferred(D)                 \──> building(W) [loop]
```

All transitions are **soft** enforced.

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `user_story` | text | — | As a [who], I want [what], so that [why] |
| `acceptance_criteria` | text | approved | Testable conditions for done |
| `design_notes` | text | — | Architecture / UX notes |
| `test_strategy` | text | — | How this will be tested |

### Epic

Large body of work spanning multiple features or tasks.

```
open(O) ──> in_progress(W) ──> closed(D)
```

All transitions are **soft** enforced.

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `scope` | text | What is in and out of scope |
| `success_metrics` | text | How we measure success |

## Planning Pack

### Milestone

Top-level delivery marker containing phases.

```
planning(O) ──> active(W) ──> closing(W) ──> completed(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `target_date` | date | Target completion date |
| `success_criteria` | text | How we know this is achieved |
| `deliverables` | list | Concrete outputs |
| `risks` | text | Known risks |
| `scope_summary` | text | What is in and out of scope |

### Phase

Logical grouping of steps within a milestone.

```
pending(O) ──> active(W) ──> completed(D)
          \──> skipped(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `sequence` | number | Execution order within milestone |
| `entry_criteria` | text | What must be true before start |
| `exit_criteria` | text | What must be true for completion |
| `estimated_effort` | text | Rough effort estimate |

### Step

Atomic unit of work within a phase.

```
pending(O) ──> in_progress(W) ──> completed(D)
          \──> skipped(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `sequence` | number | Execution order within phase |
| `target_files` | list | Files to create or modify |
| `verification` | text | How to verify completion |
| `implementation_notes` | text | Technical guidance |
| `estimated_minutes` | number | Rough time estimate |
| `done_definition` | text | Definition of done |

### Work Package

Bundled unit of assignable work.

```
defined(O) ──> assigned(O) ──> executing(W) ──> delivered(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `effort_estimate` | text | Estimated effort |
| `assigned_team` | text | Team or person responsible |
| `acceptance_criteria` | text | Conditions for delivery |

### Deliverable

Concrete output produced by a work package or phase.

```
planned(O) ──> producing(W) ──> reviewing(W) ──> accepted(D)
                            \──> producing(W) [rework loop]
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `format` | text | Expected format (document, code, artifact, etc.) |
| `audience` | text | Who receives this deliverable |
| `quality_criteria` | text | Quality standards to meet |

## Requirements Pack

Requirements lifecycle management: draft, review, approve, implement, verify.

### Requirement

A functional or non-functional requirement to be reviewed, approved, and verified.

```
drafted(O) ──> reviewing(W) ──> approved(O) ──> implementing(W) ──> verified(D)
           \──> rejected(D)  \──> rejected(D)                    \──> drafted(O) [rework]
           \──> deferred(D)  \──> drafted(O) [revision]
                              \──> deferred(D)
```

**Hard gates:**
- `implementing` → `verified` requires `verification_method`

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `req_type` | enum: functional, non_functional, constraint, interface | — | Classification of requirement |
| `stakeholder` | text | — | Who needs this requirement |
| `rationale` | text | — | Why this requirement exists |
| `verification_method` | enum: test, inspection, analysis, demonstration | verified | How this requirement will be verified |
| `acceptance_criteria` | text | — | Conditions for acceptance |
| `priority_justification` | text | — | Why this requirement has its current priority |

### Acceptance Criterion

A testable condition that must be met to satisfy a requirement. Uses Given/When/Then format.

```
draft(O) ──> validated(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `given` | text | Precondition |
| `when` | text | Action |
| `then` | text | Expected outcome |

## Risk Pack

ISO 31000-lite risk management: identify, assess, and manage project risks.

### Risk

A project risk to identify, assess, and manage.

```
identified(O) ──> assessing(W) ──> assessed(O) ──> mitigating(W) ──> mitigated(D)
              \──> retired(D)                   \──> accepted(D)
                                                \──> escalated(O) ──> mitigating(W)
```

**Hard gates:**
- `assessing` → `assessed` requires `risk_score`, `impact`
- `assessed` → `accepted` requires `risk_owner`, `acceptance_rationale`

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `risk_score` | number | assessed | Numeric risk score (e.g., 1-25) |
| `impact` | text | assessed | Description of potential impact |
| `likelihood` | enum: rare, unlikely, possible, likely, almost_certain | — | Probability of occurrence |
| `risk_owner` | text | accepted | Person responsible for this risk |
| `acceptance_rationale` | text | accepted | Why this risk is accepted |
| `mitigation_strategy` | text | — | Planned approach to reduce risk |
| `residual_risk` | text | — | Remaining risk after mitigation |

### Mitigation

An action to reduce or eliminate a risk.

```
planned(O) ──> in_progress(W) ──> completed(D)
           \──> cancelled(D)   \──> ineffective(O) ──> planned(O) [replan]
                               \──> cancelled(D)
```

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `approach` | text | — | How this mitigation will be executed |
| `outcome` | text | completed, ineffective | Result of the mitigation |
| `effort_estimate` | text | — | Estimated effort to complete |
| `target_date` | date | — | Target completion date |

## Spike Pack

Time-boxed investigation and research with documented findings.

### Spike

A time-boxed investigation to reduce uncertainty.

```
proposed(O) ──> investigating(W) ──> concluded(D) ──> actioned(D)
            \──> abandoned(D)    \──> abandoned(D)
```

**Hard gates:**
- `investigating` → `concluded` requires `findings`

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `hypothesis` | text | — | What we believe and want to verify |
| `time_box` | text | — | Maximum time allocated for investigation |
| `findings` | text | concluded | What was discovered during investigation |
| `recommendation` | text | actioned | Recommended next steps based on findings |
| `decision` | enum: proceed, pivot, stop, more_research | — | Decision made based on findings |

### Finding

A discrete discovery or insight from a spike investigation.

```
draft(O) ──> published(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `summary` | text | Brief summary of the finding |
| `evidence` | text | Supporting evidence or data |
| `implications` | text | What this finding means for the project |

## Roadmap Pack

OKR-lite strategic planning: themes, objectives, and measurable key results.

### Theme

A strategic theme grouping related objectives.

```
proposed(O) ──> active(W) ──> achieved(D)
            \──> sunset(D) \──> sunset(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `time_horizon` | text | Target timeframe (e.g., Q1 2026, H2 2026) |
| `strategic_rationale` | text | Why this theme matters strategically |

### Objective

A qualitative goal to be achieved, measured by key results.

```
defined(O) ──> pursuing(W) ──> achieved(D)
           \──> dropped(D) \──> dropped(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `owner` | text | Person or team responsible |
| `time_horizon` | text | Target timeframe |
| `success_criteria` | text | How we know this objective is achieved |

### Key Result

A measurable outcome that indicates progress toward an objective.

```
defined(O) ──> tracking(W) ──> met(D)
                            \──> missed(D)
```

**Hard gates:**
- `tracking` → `met` requires `current_value`
- `tracking` → `missed` requires `current_value`

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `target_value` | text | — | Target metric value to achieve |
| `current_value` | text | met, missed | Current metric value |
| `unit` | text | — | Unit of measurement (e.g., %, count, ms) |
| `baseline` | text | — | Starting value when tracking began |

## Incident Pack

ITIL-lite incident response with severity tracking and postmortems.

### Incident

A service disruption or degradation requiring urgent response.

```
reported(O) ──> triaging(W) ──> investigating(W) ──> mitigating(W) ──> resolved(D) ──> closed(D)
                                                  \──> resolved(D)
```

**Hard gates:**
- `reported` → `triaging` requires `severity`
- `resolved` → `closed` requires `root_cause`

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `severity` | enum: sev1, sev2, sev3, sev4 | triaging | Severity level (sev1=critical, sev4=minor) |
| `impact_scope` | text | — | What users/systems are affected |
| `root_cause` | text | closed | Root cause of the incident |
| `resolution` | text | — | How the incident was resolved |
| `detection_method` | text | — | How the incident was detected |
| `timeline` | text | — | Key timestamps: detected, acknowledged, mitigated, resolved |

### Postmortem

A blameless retrospective analysis of an incident.

```
drafting(O) ──> reviewing(W) ──> published(D)
                             \──> drafting(O) [revision]
```

**Hard gates:**
- `reviewing` → `published` requires `action_items`

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `summary` | text | — | Brief summary of what happened |
| `contributing_factors` | text | — | What factors contributed to the incident |
| `action_items` | text | published | Concrete follow-up actions to prevent recurrence |
| `lessons_learned` | text | — | Key takeaways for the team |

## Debt Pack

Catalog, assess, and systematically remediate technical debt.

### Debt Item

A piece of technical debt to be cataloged, assessed, and addressed.

```
identified(O) ──> assessed(O) ──> scheduled(O) ──> remediating(W) ──> resolved(D)
              \──> accepted(D) \──> accepted(D)                    \──> assessed(O) [rescope]
```

**Hard gates:**
- `identified` → `assessed` requires `debt_category`, `impact`

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `debt_category` | enum: code, architecture, test, documentation, dependency, infrastructure | assessed | What kind of debt this is |
| `impact` | enum: high, medium, low | assessed | Impact on velocity/quality if left unaddressed |
| `effort_estimate` | text | — | Estimated effort to remediate (e.g., 2d, 1w) |
| `code_location` | text | — | Where in the codebase this debt lives |
| `interest_description` | text | — | Ongoing cost of not fixing this (the "interest" on the debt) |
| `incurred_reason` | text | — | Why this debt was taken on originally |

### Remediation

A concrete action to reduce or eliminate a debt item.

```
planned(O) ──> in_progress(W) ──> completed(D)
           \──> abandoned(D)   \──> abandoned(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `approach` | text | How the debt will be addressed |
| `outcome` | text | Result of the remediation |
| `risk` | text | Risks of performing this remediation |

## Release Pack

Release lifecycle: plan, freeze, test, ship, and roll back if needed.

### Release

A software release to be planned, tested, and shipped.

```
planning(O) ──> development(W) ──> frozen(W) ──> testing(W) ──> staged(W) ──> released(D)
                                \──> development(W) [unfreeze]               \──> rolled_back(D)
                                     \──> development(W) [fix]
                                          \──> development(W) [fix]
```

**Hard gates:**
- `development` → `frozen` requires `version`

**Fields:**

| Field | Type | Required At | Description |
|-------|------|-------------|-------------|
| `version` | text | frozen | Version identifier (e.g., v2.1.0) |
| `target_date` | date | — | Planned release date |
| `changelog` | text | — | Summary of changes in this release |
| `release_manager` | text | — | Person coordinating this release |
| `rollback_plan` | text | — | How to revert if the release fails |

### Release Item

A work item included in a release, tracked through verification.

```
queued(O) ──> included(W) ──> verified(D)
          \──> excluded(D) \──> excluded(D)
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `verification_status` | enum: untested, passing, failing, blocked | Test/verification status for this item |
| `release_notes` | text | User-facing description of this change |

## Enforcement Levels

### Hard Enforcement

The transition is **blocked** if the required fields are missing. The operation returns an error.

Example: `verifying` → `closed` on bugs requires `fix_verification`. Without it, the close operation fails.

### Soft Enforcement

The transition is **allowed with a warning**. The operation succeeds but the response includes a warning about the unconventional transition.

Example: `open` → `closed` on tasks is soft-enforced — skipping `in_progress` is allowed but noted.

## Discovering Workflows

Use these CLI commands to explore available workflows:

```bash
filigree types                       # List all types with state flows
filigree get-template task           # Canonical full definition: pack, states, transitions, fields
filigree type-info task              # Compatibility alias for get-template
filigree guide core                  # Workflow guide for the core pack
filigree transitions <id>            # Valid next states for a specific issue
filigree explain-status bug triage   # What "triage" means for bugs
filigree workflow-statuses           # All statuses grouped by category (open/wip/done)
```

Or via MCP tools:

```
list_types             → All types with pack info
get_template           → Canonical full workflow definition
get_type_info          → Compatibility alias for get_template
get_workflow_guide     → Pack documentation
get_valid_transitions  → Valid next statuses for an issue
explain_status         → Status details and transitions
get_workflow_statuses  → Statuses by category
```

## Priority Scale

| Level | Name | Meaning |
|-------|------|---------|
| P0 | Critical | Drop everything |
| P1 | High | Do next |
| P2 | Medium | Default |
| P3 | Low | When time permits |
| P4 | Backlog | Someday/maybe |

## Template Loading

Templates are loaded in layers, with later layers overriding earlier ones:

1. **Built-in** — compiled into the filigree package (`templates_data.py`)
2. **Installed packs** — loaded from `.filigree/packs/` directory
3. **Project-local** — loaded from `.filigree/templates/` directory

This allows customizing or extending workflows without modifying filigree itself.
