# Workflow Extensibility Discussion — Transcript

**Date**: 2026-02-25
**Facilitator**: Claude (team lead)
**Format**: Conference Panel — all agents participate each round

## Participants

| Agent | Expertise | Role |
|---|---|---|
| sdlc-engineer | SDLC process design, lifecycle management | State machine design, workflow adoption patterns |
| systems-thinker | Systems thinking, feedback loops, archetypes | Domain-agnostic workflow analysis |
| arch-critic | Architecture quality assessment | Stress-testing proposals, finding failure modes |
| codebase-explorer | Codebase archaeology | Grounding proposals in filigree's actual code |
| ux-designer | UX design, accessibility | Usability for non-programmer audiences |
| doc-writer | Technical writing, documentation quality | Communication clarity — if you can't document it, it's too complex |

## Context

- **ADR-1**: Proposes a "structured" workflow pack with three layers: Strategic (milestone/epic/release), Execution (work_package→phase→step→task/bug), Deliverables (orthogonal definition-of-done). Uses typed FKs (`epic_id`, `release_id`, `milestone_id`) for cross-cutting container assignment.
- **Goal**: (1) Evaluate ADR-1, (2) propose at least one fundamentally different model and one variation within the three-layer paradigm, (3) survey non-programming domains for workflow tracking, (4) assess filigree's extensibility.
- **Filigree's current packs**: core, planning, requirements, risk, roadmap, incident, debt, spike, release (9 packs, ~24 types total)
- **Engine**: `templates.py` provides `TemplateRegistry` with states, transitions (hard/soft enforcement), field schemas, and pack bundling. Engine is domain-agnostic; programming flavor lives entirely in `templates_data.py`.

---

## Round 1: ADR-1 Critique

**Prompt**: Review ADR-001 (Structured Project Model). What's strong? What assumptions are fragile? Where does the three-layer model break? Be specific — cite scenarios where the model fails or creates friction.

### arch-critic

**The three-FK design is a category error.** Epic, release, and milestone are being treated as special taxonomy rather than what they actually are: issues with relationships. Adding hardcoded FK columns means the schema knows about specific business concepts that belong in the workflow layer. Every new cross-cutting concern (team, sprint, component, customer) will demand another nullable FK column — the "one more column" trap.

**Parent_id tree + three FKs = two competing hierarchy models.** What happens when the parent's epic differs from the task's epic? The ADR doesn't say. Silent inconsistencies will emerge that no query can easily detect.

**Deliverables layer is a sketch, not a design.** "Orthogonal definition-of-done bundles linked to any level" is a sentence, not a specification.

**Three layers for a SQLite issue tracker is architectural cosplay.** Most projects will have 1 milestone, 0-2 epics, and 1 release at a time. Labels + conventions solve this at filigree's scale.

**Recommendation**: A single `issue_links` join table with `(source_id, target_id, link_type)` handles epic/release/milestone AND future link types without schema changes. The "at most one" constraint becomes a unique index on `(source_id, link_type)`.

### systems-thinker

**Archetype match: Shifting the Burden.** Optional FKs are the "quick fix" — teams will slap FKs on flat tasks rather than building proper execution trees. The fundamental solution (decomposition) atrophies.

**Archetype match: Success to the Successful.** Releases will get tracked carefully (external visibility). Milestones will drift toward vagueness. Epics become catch-all buckets. Better-tracked containers attract more tooling; neglected ones decay.

**R2 (FK Drift — declining loop):** Optional FKs → partial use → unreliable reports → loss of confidence → even less usage. Optionality looks like flexibility but functions as a hole draining coherence.

**Highest leverage point: the word "optional."** Making strategic assignment mandatory (or strongly defaulted) for items above a certain size would dramatically change behavior.

**Cross-domain parallel: matrix organizations.** Cross-cutting FKs mirror a matrix org. Matrix structures succeed when cross-cutting authority is *explicit and enforced*; they fail when optional. This ADR replicates that risk exactly.

### ux-designer

**8-9 entity types exceeds working memory.** Users must understand both the layer they're in *and* the specific type within it. "Is this a Phase or a Step?" is the first question every new user will have.

**Four independent organizational axes on a single item.** Parent_id + epic + release + milestone means there's no single "correct" way to navigate. Every user invents their own mental model.

**Jargon barriers.** "Work Package" is PMO terminology. "Epic" is Jira-native. Non-technical teams won't recognize these.

**"All optional" creates the blank canvas problem.** Two failure modes: users ignore structure entirely (underuse) or try to model everything perfectly before creating a single task (paralysis).

**Recommendations**: Flatten Execution to two levels max. Establish one primary navigation axis. Provide opinionated starter templates. Drop layer names from UI.

### doc-writer

**Can't be taught in 5 minutes.** The dual-hierarchy concept (parent_id AND FKs) is where teaching breaks down. Users need concrete "you'd use this when…" before they can absorb the abstraction.

**Jargon collision is severe.** "Milestone" means different things in GitHub, Linear, and here. "Epic" carries Jira baggage. Users will wire in wrong mental models.

**5 documents minimum** before any API reference: concepts glossary, choosing-your-type guide, parent_id vs FK explanation, worked example, migration guide. Disproportionate for an opt-in pack.

**No clean real-world analogy exists.** Novel concepts require more explanation, not less.

**Most likely misunderstanding**: Users will treat `epic_id` as a `parent_id` alternative, not an orthogonal tag. They'll ask "should I set parent_id OR epic_id?" when the answer is "both."

### codebase-explorer

**Schema migration is trivial.** 15-line migration using existing `add_column()` helpers. Mirrors existing `parent_id` pattern. Migration system is mature (v4, 3 prior migrations as reference).

**Validation has clear insertion points.** Both `create_issue()` and `update_issue()` use validate-then-write patterns. Adding FK validation would mirror existing `_validate_parent_id()`.

**Critical finding: column-name-to-pack coupling.** The 3 FK column names bake in assumptions about which types exist. If a project doesn't enable the `release` pack, the `release_id` column is semantically meaningless. Schema is universal but semantics are pack-dependent — a mismatch.

**No code pattern-matches on type names today.** The engine is genuinely type-agnostic. Hardcoding type names into the schema would be the first violation of this principle.

**Verdict**: Implementation mechanics are easy. The design question is whether hardcoding 3 specific container types into the schema is the right abstraction for a dynamically-typed pack system.

### sdlc-engineer

**Strengths acknowledged.** The layered separation maps to classic PMI decomposition. Cross-cutting FKs (vs overloading parent_id) is the right call. Opt-in pack design respects adoption curves. "At most one" cardinality prevents accountability diffusion.

**Fixed-arity bet.** Three hardcoded FK columns means every new container type requires a schema migration. A generic `container_assignments` junction table would handle N types without schema changes.

**Deliverables duplicate the requirements pack.** The existing `requirements` pack already has `acceptance_criterion` type with hard enforcement on `verification_method`. The deliverable layer risks confusion about which mechanism to use for definition-of-done.

**Breaking scenarios**: (1) Task moving between releases mid-flight — should be a tracked event, not a silent FK update. (2) Epic progress rollup requires traversing ad-hoc task sets across the parent_id tree — query complexity is unaddressed. (3) Namespace collision with existing `release` pack's `release` type.

**No lifecycle coupling between layers.** When a milestone completes, should associated tasks be validated? Without coupling rules, layers become bookkeeping, not workflow.

---

### Facilitator Synthesis: Round 1

**Six independent experts converged on five core themes:**

| # | Theme | Raised By | Strength |
|---|-------|-----------|----------|
| 1 | **Hardcoded FKs are the wrong abstraction** — use a generic link/junction table instead | arch-critic, codebase-explorer, sdlc-engineer | Unanimous (3/3 technical agents) |
| 2 | **"Optional" is the central design risk** — creates FK drift, blank canvas paralysis, and coherence decay | systems-thinker, ux-designer, doc-writer | Unanimous (3/3 human-facing agents) |
| 3 | **Too many types / too deep a hierarchy** — 8-9 types and 4-level execution tree exceed cognitive capacity | ux-designer, doc-writer, sdlc-engineer | Strong consensus |
| 4 | **Deliverables layer is under-specified and potentially redundant** with requirements pack | sdlc-engineer, arch-critic | Agreement |
| 5 | **No inter-layer lifecycle coupling** — layers become bookkeeping without propagation rules | sdlc-engineer, systems-thinker | Agreement |

**One key disagreement**: The sdlc-engineer sees value in the three-layer separation and "at most one" cardinality as sound process design. The arch-critic sees this as over-engineering for filigree's scale. This tension — **process rigor vs. tool simplicity** — is the central design question for Round 2.

**Emerging consensus on alternative direction**: A generic link/association table rather than hardcoded FK columns. This would preserve the cross-cutting grouping capability while respecting filigree's domain-agnostic engine philosophy.

---

## Round 2: Alternative Models

**Prompt**: Propose at least one fundamentally different model and one variation within the three-layer paradigm. Concrete proposals with data models, tradeoffs, and scenario handling.

### arch-critic

**Model 1: Typed Link Graph.** All cross-cutting relationships are edges in a single `issue_links` table. No new columns on `issues`. Cardinality enforced via partial unique index: `WHERE link_type IN ('epic', 'release', 'milestone')`. New container types require zero schema migration — just add to link_type vocabulary in the template registry.

**Model 2: Registry-Governed Containers.** Same `issue_links` table, but the TemplateRegistry declares container semantics per pack — cardinality rules, valid source/target types, rollup behavior. A pack defines `container_types` with enforcement metadata. New packs can declare their own container types with their own rules.

**Bridge position**: "The three-layer *mental model* is fine. The three-layer *schema* is the problem." Process rigor belongs in the TemplateRegistry, not welded into column definitions.

### systems-thinker

**Model 1: Measurement-Coupled Model (OKR-derived).** Replace administrative FK assignment with functional measurement links — a task must demonstrate how it moves a measurable Key Result. Can't assign without specifying contribution. Avoids Shifting the Burden archetype. Cross-domain precedent: Intel/Google OKRs, clinical pathway adherence in hospitals.

**Model 2: Visible Friction Model (Andon Cord variant).** Accept the three-layer structure but make absence of strategic assignment a first-class system state — unassigned work flows into a visible, WIP-limited holding queue. Breaks the FK Drift declining loop because omission has consequences. Cross-domain precedent: Toyota Andon cord, building permit systems, hospital triage.

**Cardinality verdict**: "At most one" creates healthier dynamics. Many-to-many triggers Tragedy of the Commons — tasks linked to three epics are owned by none. The right response to cross-cutting work is to decompose it, not relax cardinality.

### sdlc-engineer

**Model 1: Process-Centric "Work Streams".** No layers, no hierarchy. Every issue is a peer. Structure emerges from link patterns (`contains`, `delivers`, `tracks`, `relates`). Lifecycle coupling via link-type-specific propagation rules in pack definitions. Adoption is incremental — each link type is independently useful.

**Model 2: Typed Container Registry.** Preserve three-layer separation via generic junction table:
```sql
CREATE TABLE container_assignments (
    issue_id        TEXT NOT NULL REFERENCES issues(id),
    container_id    TEXT NOT NULL REFERENCES issues(id),
    container_role  TEXT NOT NULL,
    assigned_at     TEXT NOT NULL,
    PRIMARY KEY (issue_id, container_role)
);
```
`PRIMARY KEY (issue_id, container_role)` enforces at-most-one cardinality at the database level. Lifecycle coupling via gate validation: containers can't transition until assigned items meet criteria. Event-driven audit trail for assignment changes.

**Bridge position**: "Process rigor doesn't require complexity. It requires constraints at the right layer."

### codebase-explorer

**Critical discovery**: Pack relationship metadata already exists — 29 relationship definitions across all packs with `from_types`, `to_types`, and `mechanism` fields. Currently advisory only, not enforced.

**Model A (issue_links) has high overlap with existing `dependencies` table** — structurally near-identical. Would create a duplicate or require a large refactor.

**Model B (container_assignments) is the cleaner fit**: orthogonal to dependencies, built-in cardinality via UNIQUE constraint, maps 1:1 to the 18 existing `parent_id`-mechanism relationships. Implementation is ~20-line migration, 7th batch query in `_build_issues_batch()`, subquery filter in `list_issues()`. New `Issue` field: `containers: dict[str, str]`. ~15 lines of new validation code in `templates.py`.

### ux-designer

**"Flat Tags with Teeth"**: Excellent onboarding (2 concepts: items and tags). Falls apart at scale — can't represent parent-child or sequencing. Users build hierarchy via naming conventions (the CSV-in-spreadsheet antipattern).

**"Progressive Structure"**: Strongest cognitive design. Starts with just Task/Bug. Layers activate when users create their first Epic/Phase. Key risk: activations must feel intentional and be reversible, not like UI bugs.

**Recommendation**: Progressive Structure wins, with flat tags living *inside* it for cross-cutting assignment. Drop "Strategic/Execution/Deliverable" from UI — show type names grouped contextually ("Planning" section, "Work" section).

### doc-writer

**Generic Links**: Most teachable — 3-sentence explanation works. Low jargon collision. Clean analogy (documents referencing other documents). ~2 docs needed vs ADR-1's 5+. Risk: relationship type vocabulary becomes the new complexity surface.

**Reduced Type Set** (Goal, Release, Phase, Task, Bug): Removes 3 highest-collision terms. "Phase" beats "Work Package." But "Goal" has no implied scale — creates a scope vacuum. Teams will use it inconsistently without attached size conventions.

**On merging Milestone+Epic into "Goal"**: Partial fix. Removes jargon collision but doesn't resolve scope ambiguity. Needs explicit sizing guidance ("a Goal is achievable in one quarter") to avoid dissolving into meaninglessness.

---

### Facilitator Synthesis: Round 2

**Strong consensus on data model**: The `container_assignments` table (sdlc-engineer's Model 2, validated by codebase-explorer) is the winning approach:
- `PRIMARY KEY (issue_id, container_role)` gives cardinality enforcement at the database level
- Orthogonal to existing `dependencies` table (no collision)
- Maps to 18 existing pack relationship definitions (validation vocabulary already exists)
- ~20-line migration, minimal code changes
- New container roles added without schema migration

**Strong consensus on cardinality**: "At most one" container per role. Systems-thinker provided the theoretical foundation (measurement integrity, Tragedy of the Commons); sdlc-engineer designed it into the schema constraint.

**Strong consensus on UX**: Progressive Structure with contextual type grouping. Layer names dropped from UI. Activation must be legible and reversible.

**Novel proposals not yet debated**: The systems-thinker's Measurement-Coupled Model (OKR) and Visible Friction Model (Andon Cord) are the most innovative ideas on the table. The Andon Cord concept — making unassigned work *visible and friction-generating* — directly solves the "optional = drift" problem and could be combined with the container_assignments approach.

**Open question for Round 3**: How do these models extend to non-programming domains? The container_assignments table is domain-agnostic by construction. But are the container *roles* (epic, release, milestone) too software-specific?

---

## Mid-Session Update: Clean Break

**The project lead announced that filigree-next has NO backwards compatibility requirements.** This is a clean break — schema, API, CLI, terminology can all be redesigned from scratch. All agents acknowledged and factored this in.

**Immediate implications identified by agents:**
- "Item" replaces "issue" at every layer — database, API, CLI, UI (ux-designer, doc-writer, sdlc-engineer)
- `parent_id`, `dependencies`, and `container_assignments` can be unified into a single `item_links` table (sdlc-engineer, arch-critic)
- Link-aware transition gates become a first-class template feature (sdlc-engineer)
- Documentation needs no migration guide — write for first-time users only (doc-writer)
- Clean break closes the "Shifting the Burden" escape hatch — no fallback to old model (systems-thinker)

---

## Round 3: Non-Programming Domains

**Prompt**: Survey non-programming domains where filigree's workflow model would provide value. Identify an exemplar domain for a demonstration pack. Assess what's generic vs software-specific in the codebase.

### systems-thinker (Domain Survey)

Surveyed 6 domains with system dynamics analysis:

| Domain | Container Fit | Key Archetype | Verdict |
|--------|--------------|---------------|---------|
| Hiring/Recruitment | Strong | Shifting the Burden (agencies) | Good fit, event history doubles as compliance audit |
| **Content Publishing** | **Excellent** | Drifting Goals (quality erosion under volume) | **Best exemplar** |
| Legal/Compliance | Strong | Tragedy of the Commons (shared legal resources) | Contracts may span multiple domains (cardinality tension) |
| Event Planning | Moderate | Limits to Growth (hard deadline) | Rich type heterogeneity, complex to configure |
| Manufacturing/Ops | Very strong structurally | Growth and Underinvestment | Dominated by existing MES/ERP systems |
| Research/Academia | Good | Success to the Successful (funding cycles) | Long uncertain timelines need "on-hold" as first-class state |

**Recommendation: Content Publishing (Editorial).** Wins on all three criteria: (a) exercises multiple types with distinct state machines, (b) completely foreign vocabulary to software, (c) universally relatable — everyone has encountered editorial publishing.

### sdlc-engineer (Lifecycle Domains + Universal Vocabulary)

Sketched 4 domains with full pack definitions:
- **Clinical Trial Management**: trial/site/subject/adverse_event with regulatory gates
- **Curriculum Development**: course/module/assessment/learning_outcome with accreditation gates
- **Construction Closeout**: punch_item/submittal/inspection/commissioning_item with contractual enforcement
- **Grant Lifecycle**: proposal/deliverable/expenditure/report with compliance gates

**Universal container role vocabulary:**

| Universal | Software | Clinical | Curriculum | Construction | Grants |
|-----------|----------|----------|------------|-------------|--------|
| **Goal** | Milestone | Study | Program | Project Phase | Strategic Goal |
| **Cycle** | Release | Protocol Version | Accreditation Cycle | Contract Package | Grant Period |
| **Stream** | Epic | Therapeutic Area | Competency Strand | Trade | Funding Agency |

Goal = purpose ("what are we achieving?"), Cycle = time ("what bounded output?"), Stream = classification ("what cross-cutting theme?"). Packs provide domain-specific display labels via `role_labels`.

### arch-critic (Stress Test)

Modeled academic research and restaurant kitchen operations. The grouping model (container_assignments) **holds up across domains**. The **state machine is the real constraint**:

Four missing primitives identified:
1. **Time-driven transitions** — SLA timers, auto-escalation (no actor present)
2. **Concurrent sub-states** — parallel items within one entity (order with dishes at different stations)
3. **Composite entities** — one item containing sub-items with independent state
4. **Iteration counters** — tracking "which revision round" on cyclic transitions

Conclusion: filigree handles **human-driven sequential workflows**. It breaks for operations/real-time domains.

### ux-designer (Persona Analysis)

| Persona | Fit | Key Issue |
|---------|-----|-----------|
| Content Manager | Moderate | Needs calendar view; "bug" is alien; thinks in publish dates |
| HR Recruiter | **Architectural mismatch** | Tracks people not work items; per-candidate state is the wrong shape |
| Event Planner | Strong | Needs timeline view; understands dependencies naturally |

**Recommendation**: Rename "issue" to "item" everywhere (UI, API, CLI, DB). "Issue" implies "problem" — damages first impressions with non-technical users.

### doc-writer (Audience Pitches)

Drafted 2-sentence pitches and concept mapping tables for content teams, HR teams, and operations teams.

Key findings:
- Content teams: "piece" or "assignment" not "issue." States = "stages." No "bug" — only "correction"
- HR teams: "request" or "action item." Middle hierarchy layers feel like overhead
- Operations teams: "task" works. "Release" and "version" are opaque. "Work package" sounds like HR paperwork

**Universal term recommendation: "item"** — most neutral, works as modifier ("content item," "hiring item"), no tool association.

### codebase-explorer (Software Assumptions Audit)

| Layer | Status | Fix Effort |
|-------|--------|------------|
| Schema / DB layer | **Generic** | None |
| CLI commands & flags | **Generic** | None (except default type) |
| Template / pack system | **Generic** | None |
| `create_issue()` type default | Software-biased | Trivial (1 line) |
| Default enabled packs | Software-biased | Small (init-time selection) |
| Graph shapes/icons/colors | Hardcoded to 6 types | Medium (data-driven mapping) |
| Kanban cluster mode | Hardcoded to epic/milestone | Medium (generic container grouping) |

**Bottom line**: Core engine is domain-neutral. Only ~5 hardcoded type references in the dashboard presentation layer need to become data-driven. A non-programming pack works today for tracking; it just lacks visual polish.

---

### Facilitator Synthesis: Round 3

**Consensus decisions:**

| Decision | Status | Supporters |
|----------|--------|------------|
| Rename "issue" → "item" everywhere | **Unanimous** | All 6 agents |
| Exemplar domain: Content Publishing | **Strong consensus** | systems-thinker (recommended), ux-designer, doc-writer (confirmed fit) |
| Universal container roles: Goal/Cycle/Stream | **Proposed, uncontested** | sdlc-engineer (proposed), maps cleanly across all surveyed domains |
| Unified `item_links` table (clean break) | **Strong consensus** | sdlc-engineer, arch-critic, systems-thinker |
| HR/Recruiting is out of scope | **Agreement** | ux-designer (architectural mismatch), sdlc-engineer (wrong data shape) |

**Key finding**: The grouping model is general. The state machine is the real limit. Filigree serves human-driven sequential workflows — not real-time operations.

**Open questions for Round 4:**
1. Should the unified `item_links` table replace ALL relationship mechanisms (parent_id, deps, containers)?
2. How should link-aware transition gates work in the template system?
3. Should filigree-next address any of the four missing primitives (timers, sub-states, composite entities, iteration counters)?
4. What does the editorial pack look like concretely?

---

## Round 4: Extensibility Architecture

**Prompt**: Design the concrete architecture for filigree-next — schema, template extensions, dashboard UX, documentation, and code scope.

### arch-critic (Schema Critique)

**ON DELETE: RESTRICT, not CASCADE.** Deleting an epic should not silently unlink all tasks. Application layer must explicitly unlink first with user confirmation. Soft-delete (status='archived') preserves links.

**Link type: free-text**, validated by registry at write time. No CHECK constraints — fights pack extensibility. Just `NOT NULL` and length limit.

**Required indexes:**
```sql
CREATE INDEX ix_links_target ON item_links(target_id, link_type);
CREATE INDEX ix_links_source ON item_links(source_id, link_type);
CREATE INDEX ix_items_status ON items(status);
CREATE INDEX ix_items_type_status ON items(type, status);
```

**Add `created_by TEXT`** to items — "who created this?" shouldn't require scanning events.

**Views per link semantic** (`v_blocks`, `v_parent`, `v_containers`) — safety net against queries forgetting `WHERE link_type`.

**JSON fields: write-validated, read-permissive.** Don't index into JSON for queries — anything queryable belongs as a column.

### sdlc-engineer (Template Extensions + Editorial Pack)

**Link type declarations in packs:**
- `cardinality`: many_to_one | many_to_many | one_to_one
- `valid_pairs`: array of {source: [types], target: [types]} or "any"
- `cycle_check`: boolean (essential for parent/blocks, unnecessary for containers)
- `inverse_name`: display name for reverse direction

**Link-aware transition gates:**
```json
"gates": [{
  "link_type": "cycle",
  "direction": "inbound",
  "condition": "all_in_category",
  "params": {"category": ["done"]},
  "message": "All items must be done before closing"
}]
```
Supported conditions: `all_in_category`, `none_in_category`, `count_gte`, `count_eq`, `all_field_set`, `any_in_state`. Gates inherit transition's hard/soft enforcement.

**Role labels** — packs map Goal/Cycle/Stream to domain-specific display names, icons, colors.

**Complete editorial pack delivered** — pitch, piece, revision, series (goal container), publication_target (cycle container). All transitions use link-aware gates. Series can't complete until all pieces done. Edition can't publish until all pieces published/spiked.

### systems-thinker (Systemic Analysis)

**Healthy loops:** Link Density → Visibility (reinforcing), Transition Gate (balancing), Cycle Closure (balancing).

**Pathological loops to guard against:**
- Link Inflation — junk assignments to satisfy gates
- Gate Avoidance — throwaway containers to clear gates
- Cycle Rot — all items pile into current cycle

**Three-tier Andon Cord:**
1. Permanent "Unanchored Items" dashboard panel (always visible)
2. Soft WIP nudge when unanchored items exceed threshold at creation
3. Hard gate at close — terminal states require at least one link

Key principle: *"Creating items is frictionless. Closing items requires completeness."*

**Missing primitives ranked:**
- Time-driven transitions: **Address in filigree-next** (shadow workflows break audit trails)
- Iteration counters: **Address in filigree-next** (title-encoding corrupts search)
- Composite entities: **Defer** (parent trees are workable)
- Concurrent sub-states: **Out of scope** (linked items are clean workaround)

**Editorial encoding:** Non-skippable review state for public-facing Streams. Review Queue Depth ratio as dashboard metric. Stream as quality anchor (quality belongs to the Stream, not the sprint).

### codebase-explorer (Architecture Delta)

**Schema**: `items` table (current `issues` minus `parent_id`, no default type). `item_links` table with `CHECK(source_id != target_id)`. Partial unique index for single-cardinality links.

**Code scope: ~44 files touched** (32 Python + 12 JS). Heavy: db_issues.py, db_planning.py. Medium: core.py, db_workflow.py, templates.py, dashboard.py. Mechanical: cli_commands/, mcp_tools/, static/js/.

**What gets simpler:** `_build_issues_batch()` collapses 6 queries to ~2. CRUD has one validation pattern instead of three. Dashboard graph builds from one query. Planning mixin unifies dep/parent queries.

**What gets harder:** Cycle detection must be link-type-scoped. "Give me all children" becomes a JOIN (negligible perf at filigree scale). Cardinality enforcement via partial unique index needs sync with pack metadata.

### ux-designer (Dashboard UX)

**Software project:** Sidebar-progressive (sections appear as containers are created). Flat list at 5 items → Kanban at 50 → container pages at 200. Triage panel as persistent Andon Cord with count badge.

**Editorial project:** Calendar-primary (not Kanban). Edition as date-range filter. Series as color-coded swim lanes. Slot/capacity indicators. Pipeline Kanban as secondary view.

**Pack-declared views from bounded menu:** `list | kanban | calendar | timeline`. Packs declare default view and axis mappings. No arbitrary widget plugin API.

### doc-writer (Documentation Architecture)

**Core docs (5 sections):** What filigree is → Your first item → How items connect → Organizing with packs → Quick reference.

**Pack guides:** Identical structure across domains. Who-this-is-for → Item types → How work flows → Worked example → Quick reference.

**Terminology:** "Item" in core docs. Domain term in pack docs with one bridge sentence.

**Teachability test passed:** `item_links` explained in 3 sentences to non-technical user.

---

### Facilitator Synthesis: Round 4

**Answered questions from Round 3:**

| Question | Answer | Status |
|----------|--------|--------|
| Unified `item_links` replaces all mechanisms? | **Yes** — parent_id, dependencies, and containers all become link types | Consensus |
| Link-aware transition gates? | **Concrete design delivered** — 6 gate conditions, hard/soft enforcement, direction-aware | Consensus |
| Missing primitives? | Time-driven transitions + iteration counters: IN SCOPE. Composite/concurrent: DEFERRED | Strong agreement |
| Editorial pack? | **Complete pack definition delivered** with working gate logic | Delivered |

**One disagreement: ON DELETE behavior.**
- arch-critic: RESTRICT (deletion fails if links exist, app must unlink first)
- codebase-explorer: CASCADE (links removed when item deleted)

This needs resolution in Round 5.

**Emerging complete architecture:**
- `items` table + `item_links` table (2 core tables)
- Template registry governs link types, cardinality, valid pairs, cycle detection
- Link-aware transition gates (6 condition types, hard/soft enforcement)
- Goal/Cycle/Stream as universal container roles with pack-specific labels
- Three-tier Andon Cord (panel → nudge → hard gate at close)
- Pack-declared views from bounded menu (list | kanban | calendar | timeline)
- "Item" everywhere, domain terms in pack docs
- ~44 files to change, net simplification of CRUD and query patterns

---

## Round 5: Convergence

**Prompt**: State final positions on the architecture. Resolve open questions: ON DELETE behavior, time-driven transitions, iteration counters. Flag any remaining disagreements.

### Final Votes

**Architecture endorsement: 6/6 — Unanimous.**

All agents endorsed the core design with no remaining disagreements on fundamentals.

**Q1: ON DELETE — RESTRICT (6/6 Unanimous)**

| Agent | Position | Reasoning |
|-------|----------|-----------|
| sdlc-engineer | RESTRICT | Scope changes must be deliberate and auditable |
| arch-critic | RESTRICT | CASCADE silently rewrites the relationship graph |
| systems-thinker | RESTRICT | Audit trail is a first-class asset |
| ux-designer | RESTRICT | CASCADE causes unpredictable silent data loss |
| doc-writer | RESTRICT | CASCADE produces mysteries; RESTRICT produces teachable errors |
| codebase-explorer | RESTRICT | Filigree doesn't hard-delete today; RESTRICT matches existing pattern |

**Q2: Time-driven transitions — `due_at` column + dashboard warnings (5/6 consensus)**

| Agent | Position |
|-------|----------|
| sdlc-engineer | `due_at` column, dashboard warnings, NOT auto-transitions |
| arch-critic | `due_at` + warnings; no daemon/scheduler architecture exists |
| ux-designer | `due_at` + warnings; auto-transition as opt-in pack feature at most |
| doc-writer | `due_at` + warnings; auto-transitions are magic that can't be documented simply |
| codebase-explorer | `due_at` + warnings; fits existing staleness detection pattern |
| systems-thinker | `due_at` + warnings PLUS pack-declarable stale-transition rules (hybrid) |

Resolution: `due_at` as a first-class column with dashboard surfacing. Pack-declarable stale-transition rules noted as future enhancement, not core v1 feature.

**Q3: Iteration counters — Split resolved as "both/and"**

| Agent | Position |
|-------|----------|
| sdlc-engineer | Metadata on `item_links` (nullable JSON column) |
| systems-thinker | `sequence` field on `item_links` (preserve old links) |
| ux-designer | Metadata on `item_links` |
| arch-critic | `fields` JSON on items (auto-increment on transition) |
| codebase-explorer | `fields` JSON (fits existing FieldSchema infrastructure) |
| doc-writer | Dedicated column if first-class; else `fields` JSON |

Resolution: Both mechanisms serve different use cases. Simple counters (revision number, draft version) live in pack-declared `fields` JSON with auto-increment rules. Relationship-specific history (which Cycle is this the Nth assignment to) uses optional metadata on `item_links`. Pack authors choose the right mechanism for their domain.

### Additional Gaps Flagged

| Gap | Flagged By | Priority |
|-----|-----------|----------|
| Link events in audit trail (`link_created`, `link_removed`) | sdlc-engineer, arch-critic | Must-have |
| Container closure orphans → Andon Cord | systems-thinker | Must-have |
| Error message vocabulary per link type | doc-writer | Should-have |
| Link types as closed picker, not free-text input | doc-writer | Should-have (UX, not schema) |
| Search specification (which fields indexed) | ux-designer | Must-have for implementation |
| Bulk operations on item_links | ux-designer | Should-have |
| View state persistence | ux-designer | Should-have |
| Migration tooling from current filigree | arch-critic | Nice-to-have |
| View query mapping specification | arch-critic | Must-have for implementation |
| FTS5 virtual table rename | codebase-explorer | Implementation detail |
| Write-time cycle detection enforcement | systems-thinker | Must-have |

---

## Discussion Complete

5 rounds, 6 agents, full consensus achieved on filigree-next architecture. Design document follows as a separate file.

