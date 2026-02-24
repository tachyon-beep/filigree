# ADR-001: Structured Project Model — Strategic / Execution / Deliverable Layers

**Status**: Proposed
**Date**: 2026-02-24
**Deciders**: John (project lead)
**Context**: Filigree's existing packs (core, planning, release) provide issue types but don't enforce a coherent project structure model

## Summary

Introduce a "structured" workflow pack that formalises three distinct layers of project organisation: a **strategic layer** (milestones, epics, releases) for steering, an **execution layer** (work packages -> phases -> steps -> tasks/bugs) for day-to-day work, and **deliverables** as an orthogonal definition-of-done mechanism. Work items can optionally be tagged to at most one of each strategic container, providing cross-cutting visibility without rigid nesting.

## Context

Filigree currently ships three workflow packs:

- **core**: task, bug, feature, epic — flat, general-purpose work tracking
- **planning**: milestone, phase, step, work_package, deliverable — hierarchical decomposition
- **release**: release, release_item — shipping ceremony

These packs provide *types* but don't prescribe *how they relate to each other*. A project can use any combination, but there's no enforced model for which types contain which, how deep the hierarchy goes, or what role each type plays. This works for simple projects but becomes unclear as projects grow:

- Is an epic a container or a work item?
- Can a task belong to both a phase and a release?
- Where do deliverables fit — are they children of milestones? Phases? Releases?
- What's the difference between a milestone and an epic in practice?

The existing `parent_id` field provides tree structure but only allows one parent. A task that's structurally under a phase can't also be "in" a release without a separate mechanism.

### Constraints

- Must not break existing projects using core/planning/release packs independently
- Must work with the current SQLite schema (nullable FK columns are feasible)
- Must remain optional — simple projects should stay simple
- Agent workflows (claim, claim-next, ready, blocked) must continue to work unchanged

## Decision

We will introduce a **structured project model** as a new workflow pack (working name: `structured`) that can be enabled alongside or instead of the existing packs. This model formalises three layers:

### Layer 1: Strategic (Steering)

| Type | Role | Quantity |
|---|---|---|
| **Milestone** | Named goal — "what are we trying to achieve?" | A handful per project |
| **Epic** | Cross-cutting theme — "what themes of work exist?" | A handful per project |
| **Release** | Versioned shippable output — "what are we shipping?" | A handful per project |

Strategic types don't generate work directly. They organise and steer it. A project lead looks at this layer.

### Layer 2: Execution (Day-to-Day)

The decomposition chain via `parent_id`:

```
Work Package -> Phase -> Step -> Task / Bug
```

This is where agents operate. Tasks and bugs are the leaf-level work items that get claimed, worked on, and closed. Features sit at the same level as tasks — they're user-facing work items with an approval gate.

### Layer 3: Deliverables (Definition of Done)

Deliverables are bundles of requirements, acceptance criteria, or artifacts. They answer "what does done look like?" rather than "what work needs doing?"

Deliverables are orthogonal — they can be linked to any level (a release, a phase, a milestone) as the acceptance definition for that scope.

### Container Assignment

Work items (task, bug, feature, step) gain three optional typed foreign keys:

- `epic_id` — at most one epic
- `release_id` — at most one release
- `milestone_id` — at most one milestone

These are independent of `parent_id`. A task can live under Phase 2 (structural hierarchy) while also being tagged to Release v1.3.0 and Epic "Codebase Intelligence" (strategic visibility).

Validation rules:
- Referenced issue must exist and be the correct type
- At most one of each — no multi-epic assignment
- All optional — if no containers of a type exist, the fields stay null

## Alternatives Considered

### Alternative 1: Enforce Structure via parent_id Only

**Description**: Use the existing parent_id tree to model everything — milestones contain phases, phases contain steps, epics contain features, releases contain release_items. No new fields.

**Pros**:
- No schema changes
- Simple mental model (everything is a tree)
- Already works today

**Cons**:
- A task can only have one parent — can't be in both a phase AND a release
- Epics and releases become structurally incompatible (a feature can't be "in" both)
- Forces artificial nesting (release containing tasks directly, rather than tagging)

**Why rejected**: The single-parent tree can't represent the cross-cutting nature of strategic containers. A task belongs to an execution hierarchy (phase -> step) AND to strategic containers (epic, release) simultaneously. One axis of nesting isn't enough.

### Alternative 2: Labels / Tags Instead of Typed FKs

**Description**: Use the existing label system to tag issues with container names — `epic:codebase-intelligence`, `release:v1.3.0`, `milestone:stability`.

**Pros**:
- Zero schema changes
- Infinitely flexible
- Already implemented

**Cons**:
- No validation (can tag with non-existent containers, typos go undetected)
- No cardinality enforcement (nothing prevents tagging with two epics)
- No type safety (label "epic:foo" doesn't verify that "foo" is actually an epic)
- Querying is string matching, not FK joins

**Why rejected**: Labels are too loose for structural relationships. The value of this model is *enforcement* — the system guarantees that a task points to exactly zero or one real epic, not a freeform string.

### Alternative 3: Modify Existing Packs In-Place

**Description**: Add the container FK fields and hierarchy rules directly to the core, planning, and release packs.

**Pros**:
- No new pack to discover/enable
- Immediate benefit for all projects

**Cons**:
- Breaking change for projects that use these types informally
- Forces a specific model on projects that may not want it
- Harder to iterate — changes affect all users

**Why rejected**: Making this a separate opt-in pack preserves the simplicity of existing packs while offering the structured model for projects that want it. Projects can adopt it when they're ready.

## Consequences

### Positive

- Clear separation of concerns: strategic steering vs execution vs acceptance
- Cross-cutting container membership without breaking tree hierarchy
- Agents can filter work by strategic context ("show me all tasks in this release")
- Dashboard can show container badges on kanban cards
- Enforced type safety prevents orphan references and mis-categorisation
- Simple projects can ignore the pack entirely

### Negative

- Three new nullable columns on the issues table (migration required)
- Validation logic adds complexity to create/update paths
- Users must understand the three-layer model to use it effectively
- The pack name and discoverability need thought — "structured" is placeholder

### Neutral

- Existing parent_id hierarchy unchanged — this is additive
- Existing packs continue to work as-is
- MCP and CLI need new filter flags (--epic, --release, --milestone) but these are additive

## Implementation Notes

- Schema migration: add `epic_id`, `release_id`, `milestone_id` as nullable TEXT columns with FK constraints to `issues(id)`
- Add indexes on each FK column for efficient container-scoped queries
- Validation in `create_issue()` and `update_issue()`: if set, referenced issue must exist and have the correct type
- New CLI/MCP filter flags: `--epic`, `--release`, `--milestone` on `list`, `ready`, `blocked`
- Dashboard: optional container badges on kanban cards, filter dropdowns
- New pack definition in `.filigree/packs/structured.json` with type overrides that add the FK fields to task, bug, feature, step templates

## Related Decisions

- **Related to**: filigree-3f26ed (feature ticket tracking implementation)
- **Related to**: filigree-510466 (pin strategic types to top of kanban)
- **Related to**: filigree-93504c (bugs must be associated with files — similar enforcement philosophy)

## References

- Filigree workflow pack system: `.filigree/packs/`, `src/filigree/templates.py`
- Existing type definitions: `src/filigree/templates_data.py`
