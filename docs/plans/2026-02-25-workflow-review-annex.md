# Workflow Extensibility Design — Review Annex

**Date**: 2026-02-25
**Status**: Review complete, findings incorporated into design document
**Design**: [2026-02-25-workflow-extensibility-design.md](./2026-02-25-workflow-extensibility-design.md)

---

## Review Methodology

The design document and its source transcript were reviewed by three specialized agents and one external reviewer, each examining the design from a different angle:

| Reviewer | Focus | Method |
|----------|-------|--------|
| **Architecture Reviewer** | Schema design, patterns, complexity, anti-patterns | Read codebase source files to validate claims; weighted blast radius analysis |
| **Reality Checker** | Concrete claims vs codebase truth | Symbol-by-symbol, path-by-path verification of every factual claim |
| **Systems Reviewer** | Systemic risks, second-order effects, failure modes | Dependency chain tracing, feedback loop analysis, historical pattern matching |
| **External Reviewer** | Internal consistency, gate vocabulary, pack authoring UX | Line-by-line design doc analysis focused on what a pack author would encounter |

---

## Findings

### Blocking (must resolve before implementation)

#### B1: Cardinality enforcement index contradicts pack extensibility

**Raised by**: All 4 reviewers (unanimous)

The design proposed a static partial unique index:
```sql
CREATE UNIQUE INDEX ix_links_singular
    ON item_links(source_id, link_type)
    WHERE link_type NOT IN ('blocks', 'relates');
```

This hardcodes the exclusion list at schema creation time. Pack authors declaring new `many_to_many` link types would get silent singular cardinality enforcement, producing cryptic UNIQUE constraint errors. SQLite cannot `ALTER INDEX`, so there is no runtime sync mechanism.

**Resolution**: Replaced with per-link-type indexes generated at pack init time. The registry is the source of truth; indexes are materialized from it. See updated Section 1.2 in the design document.

#### B2: `scan_findings`, `file_associations`, and `events` tables unscoped

**Raised by**: Architecture Reviewer, Systems Reviewer

Three tables reference `issues(id)` with their own FK semantics:
- `scan_findings.issue_id TEXT REFERENCES issues(id) ON DELETE SET NULL`
- `file_associations.issue_id TEXT NOT NULL REFERENCES issues(id)` (NOT NULL = requires `rebuild_table()` in SQLite)
- `events.issue_id TEXT NOT NULL REFERENCES issues(id)` (dedup unique index must be rebuilt)

Additionally, `file_associations.assoc_type` has a CHECK constraint with issue-domain vocabulary (`bug_in`, `task_for`) that must be addressed.

The `db_files.py` module (1,218 lines) and `mcp_tools/files.py` (539 lines) were absent from the scope estimate.

**Resolution**: Added explicit migration plan to Section 1.4 of the design document.

#### B3: Gate condition vocabulary mismatch

**Raised by**: External Reviewer

The editorial pack (Section 4.3) uses `all_in_state: [published, spiked]`, but the supported gate conditions table (Section 2.2) only lists `any_in_state` (at-least-one semantics). No `all_in_state` (every-item semantics) was defined.

**Resolution**: Added `all_in_state` as a 7th gate condition. Defined state categories formally in a new Section 2.6.

#### B4: "Category" has no formal definition

**Raised by**: External Reviewer

Multiple gate conditions reference categories (`all_in_category`, `none_in_category`) but the design did not specify where categories are defined or how states map to them.

**Resolution**: Added Section 2.6 (State Categories) defining the category model. Each state declares its category in the type template. Three built-in categories: `open`, `wip`, `done`.

---

### Warnings (should resolve, not blocking)

#### W1: Cycle detection must be link-type-scoped

**Raised by**: Architecture Reviewer, Systems Reviewer

The current `_would_create_cycle()` BFS traverses all edges in `dependencies`. Under the unified model, the same BFS over `item_links` without a `link_type` filter would prevent a `parent` link because a `blocks` path exists between the same items. This is a correctness bug.

Additionally, cycle detection semantics differ by link type:
- `blocks` and `parent`: cycles absolutely forbidden
- `relates`: cycles permitted (A relates-to B relates-to A is valid)
- Container roles: cycles structurally impossible (cardinality prevents them)

**Resolution**: Added cycle detection semantics to Section 2.1. Specified that `_would_create_cycle()` must accept a `link_type` parameter.

#### W2: Migration tooling undervalued

**Raised by**: Architecture Reviewer, Systems Reviewer

The developer uses filigree to track filigree-next's own development. A clean break with no migration means losing the active tracker state. The concrete deliverable is ~50 lines of Python: `issues` -> `items`, `dependencies` -> `item_links WHERE link_type='blocks'`, `parent_id` values -> `item_links WHERE link_type='parent'`.

**Resolution**: Elevated from "Nice-to-have" to "Should-have" in Section 7. Added "do-before-cutover" qualifier.

#### W3: MCP tool names are an external API surface

**Raised by**: Systems Reviewer

`get_issue`, `list_issues`, `create_issue` are names consumed by AI agents, shell scripts, and CLAUDE.md prompts. Renaming them breaks external consumers silently. The design called this "mechanical" when it is actually API-breaking.

**Resolution**: Updated Section 6.1 to classify `mcp_tools/` as "API-breaking rename" rather than "Mechanical."

#### W4: File count estimate is inaccurate

**Raised by**: Reality Checker, Systems Reviewer

Actual counts: 41 Python source files, 14 JS files (not "32 Python + 12 JS"). With test files (14 test files, 24,550 lines), `db_files.py`, `mcp_tools/files.py`, and CLAUDE.md, the true blast radius is ~70+ files.

**Resolution**: Updated Section 6.1 with corrected estimate and separate test suite line item.

#### W5: Batch query simplification overstated

**Raised by**: Architecture Reviewer, Reality Checker

`_build_issues_batch()` currently runs 6-7 batched queries (confirmed by source). The "collapse to ~2" claim is optimistic. Realistic estimate: 6-7 queries collapse to ~4 (labels remain separate; link queries consolidate but don't fully merge).

**Resolution**: Updated Section 6.2 with corrected estimate.

#### W6: `create_plan()` is not mechanical

**Raised by**: Systems Reviewer

`db_planning.py`'s `create_plan()` hardcodes `'milestone'`, `'phase'`, `'step'` type names in SQL INSERTs and event recording. `get_plan()` traverses a two-level `parent_id` tree that becomes a recursive JOIN on `item_links WHERE link_type = 'parent'`. These are structural rewrites.

**Resolution**: Updated Section 6.1 to classify `db_planning.py` impact correctly.

#### W7: `file_associations.assoc_type` embeds issue-domain vocabulary

**Raised by**: Architecture Reviewer

The CHECK constraint `assoc_type IN ('bug_in', 'task_for', 'scan_finding', 'mentioned_in')` uses software-domain terms. In a domain-agnostic v2.0, these need generalization.

**Resolution**: Addressed in expanded Section 1.4.

#### W8: Convenience views hardcode role link types

**Raised by**: External Reviewer

`v_containers` filters `link_type IN ('goal', 'cycle', 'stream')`. This is consistent with the design's intent (role names are universal; only display labels vary), but the invariant should be stated explicitly.

**Resolution**: Added invariant note to Section 1.3.

#### W9: Open items priority reassessment

**Raised by**: Systems Reviewer

The design listed 3 must-haves + 5 deferrable. Analysis shows 5 are actually must-haves: link events, orphan recovery flow, FTS/search, view query mapping, and migration tooling. FTS rename requires DROP + CREATE with data re-index and trigger rewrites — deeper than a one-liner.

**Resolution**: Updated Section 7 with revised priorities.

---

### Recommendations (incorporated into design)

#### R1: Add a tracer bullet milestone

**Raised by**: Architecture Reviewer

Before implementing all 11 in-scope features, define an internal v2.0-alpha checkpoint: `items` table, `item_links` table, one link type (`blocks`) enforced, `filigree create`/`list`/`add-dep` working end-to-end. Validates integration assumptions before building gates, editorial pack, and Andon Cord.

**Resolution**: Added to Section 6 as new subsection 6.4.

#### R2: Specify registry API contract

**Raised by**: External Reviewer

Pack authors need a clear contract for what must be declared: types (states, transitions, categories, icons), link types (cardinality, valid_pairs, cycle_check), and roles (goal/cycle/stream labels).

**Resolution**: Added as new Section 9 (Registry API Contract).

#### R3: Define error model

**Raised by**: External Reviewer

Machine-readable error codes and pack-overridable message templates make CLI/UX coherent and documentation-friendly.

**Resolution**: Added to Section 9.

#### R4: Specify date format invariant

**Raised by**: External Reviewer

`due_at TEXT` needs a canonical format. SQLite won't enforce it, so the write layer must.

**Resolution**: Added format note to Section 1.1.

#### R5: Link type creation must be registry-backed

**Raised by**: External Reviewer

Schema can remain free-text, but CLI and dashboard must present link types as a closed picker derived from the registry. Free-text link type creation produces typos that are syntactically valid and semantically dead.

**Resolution**: Noted in expanded Section 2.1.

#### R6: Add `LinksMixin` for consistency

**Raised by**: Architecture Reviewer

The existing codebase uses mixin composition (`IssuesMixin`, `PlanningMixin`, etc.). The new link operations should follow the same pattern with a dedicated `LinksMixin`.

**Resolution**: Added to Section 6.1 module table.

---

## Factual Accuracy Report (Reality Checker)

| Claim | Verdict | Detail |
|-------|---------|--------|
| "~44 files (32 Python + 12 JS)" | Inaccurate | Actual: 41 Python, 14 JS; ~20 Python files need changes; ~70+ with tests |
| "29 relationship definitions in packs" | Off by one | Actual count: 30 `from_types` entries |
| "`_build_issues_batch()` 6 queries collapse to ~2" | 6 confirmed; ~2 is optimistic | Realistic: collapses to ~4 |
| "No code pattern-matches on type names" | True for Python, false for JS | `graph.js`, `kanban.js`, `state.js` hardcode type names |
| "`dependencies` table structure" | Accurate | PK differs: 2-column vs proposed 3-column (noted) |
| "6 hardcoded types in dashboard" | Accurate | `TYPE_ICONS` and `TYPE_COLORS` map 6 types in `state.js` |
| "`create_issue()` type default" | Accurate | Default `'task'` appears in 4 places |
| "Kanban cluster mode hardcoded to epic/milestone" | Accurate | `kanban.js:114-115` filters on `"epic"` and `"milestone"` |
| "Migration system v4 with 3 migrations" | Accurate | `CURRENT_SCHEMA_VERSION = 4`, 3 registered migrations |
| "Template registry is type-agnostic" | Accurate | Confirmed in `templates.py` |
| "Mixin composition pattern" | Accurate | `FiligreeDB` composes 6 mixins via MRO |
| "`_validate_link_target()` symbol" | Does not exist | Proposed new function, not an existing refactor target |
| "`db_items.py` path" | Does not exist | Proposed rename of `db_issues.py`, not yet created |

---

## Historical Pattern Assessment (Systems Reviewer)

| Pattern | Match Level | Mitigation |
|---------|-------------|------------|
| Second-system effect | Strong | Tracer bullet milestone (R1) limits blast radius |
| God table emergence | Partial | Convenience views provide semantic guardrails |
| Implicit SQL-vs-registry contract | Was present | Resolved by B1 (per-link-type indexes from registry) |
| "Mechanical rename" underestimation | Was present | Resolved by W3, W4, W6 (reclassified impact levels) |
| Big-bang rewrite risk | Present | Tracer bullet (R1) + sequenced delivery recommended |

---

## Review Sources

| Source | Reviewer Type | Duration | Files Examined |
|--------|---------------|----------|----------------|
| Architecture Review | plan-review-architecture agent | ~2 min | 17 source files |
| Reality Check | plan-review-reality agent | ~4 min | 62 source files |
| Systems Review | plan-review-systems agent | ~4 min | 56 source files |
| External Review | ChatGPT (external) | Manual | Design doc only |
