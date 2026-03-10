# Dashboard UX Review — Panel Discussion Minutes

**Date:** 2026-03-04
**Format:** 3-specialist facilitated panel, 3 rounds
**Subject:** First-principles review of dashboard 8-tab structure
**Outcome:** Unanimous consensus — reduce to 5 tabs with specific merges and redesigns

## Panel Composition

| # | Role | Agent Type | Focus |
|---|------|-----------|-------|
| 1 | Senior Power User | general-purpose | Daily workflow mapping, usefulness ranking, practical friction |
| 2 | Systems Thinker | yzmir-systems-thinking:leverage-analyst | Information flow, feedback loops, cognitive load, architecture alignment |
| 3 | UX Specialist | lyra-ux-designer:ux-critic | Information architecture, heuristics, user journeys, progressive disclosure |

## Context

The Filigree dashboard has 8 top-level tabs: Graph (1223 LOC), Kanban (414), Metrics (223), Activity (82), Workflow (246), Files (777), Health (259), Releases (679). The header filter bar has ~17 interactive elements. The review was prompted by concerns about redundancy, cognitive overload, and alignment with the v1.5.0 Templated Dashboard UX roadmap (filigree-edf8712ea6) and v2.0 Progressive Structure plan (filigree-3c35fe71ff).

---

## Round 1: Independent Deep Dives

Each panelist independently read all source code and future plans, then delivered a written analysis.

### Senior Power User — Key Findings

- **Daily workflow uses only 3 tabs**: Kanban (every session), Graph (daily for dependency checks), Releases (weekly for milestone review).
- **Tabs never visited**: Activity (same data available in issue detail panel), Workflow (reference material used once), Health (summary index of Files data — "every interaction leads back to Files").
- **Usefulness ranking** (most → least): Kanban, Graph, Releases, Metrics, Files, Health, Workflow, Activity.
- **"Entry hall" metaphor**: Health is an entry hall to Files — you walk through it to get somewhere else. That's a navigation tax, not a feature.
- **Missing capability**: No dense table/list view for issues at 200+ scale.
- **Recommendation**: 8 → 5 tabs. Merge Health→Files, merge Activity→Metrics ("Pulse"), demote Workflow.

### Systems Thinker — Key Findings

- **Two disconnected data universes**: Universe A (issue data, feeds 6 tabs via `state.allIssues`) and Universe B (file/scan data, feeds 2 tabs independently). No cross-referencing.
- **Cognitive load**: 8 tabs × 17 header filters × detail panel = three concurrent cognitive contexts. Header filters only apply to ~3 views but are always visible.
- **System archetype: "Fixes that Backfire"**: Each new capability got a new tab. Tab accumulation is now itself the problem v1.5.0 is designed to solve.
- **Highest leverage intervention**: Level 6 (Information Flows) — make the right information visible at the right time without tab switches, rather than adding more tabs.
- **Health score computed in `graph.js`**: `computeHealthScore()` is conceptually orthogonal to graph visualization but lives in the 1223 LOC graph module. Structural coupling.
- **Recommendation**: Merge Health+Metrics+Activity into "Pulse", extract health scoring from graph.js, reduce to 6 tabs.

### UX Specialist — Key Findings

- **Tab structure organized by implementation domain, not user intent**: Tabs map to features added over time, not user mental models. Card-sorting would produce different groupings (Active Work, Planning, Observability, Reference).
- **4 user journeys mapped**: "Find work" (2-3 clicks optimal, 5-8 for newcomers), "Understand blocking" (high friction — requires Graph Advanced panel), "Check project health" (requires 3-4 tab switches), "Review release readiness" (Releases buried as last tab).
- **Nielsen's heuristics**: Header filter bar violates "aesthetic and minimalist design" — 17 interactive elements always visible. Graph's Focus mode violates "recognition rather than recall" — most useful power feature buried in Advanced disclosure.
- **Activity too thin**: 82 LOC, zero interactivity beyond click-to-detail. Insufficient for a top-level tab.
- **Redundancy**: Health vs. Files classified as "different zoom levels, not redundant." Health is a digest that orients before drill-down.
- **Recommendation**: 6 tabs (Kanban, Releases, Graph, Insights, Files, Code Quality). Rename Health→"Code Quality", keep separate.

### Round 1 Consensus

| Topic | Senior User | Systems Thinker | UX Specialist |
|-------|------------|-----------------|---------------|
| Merge Activity into Metrics | Yes | Yes | Yes |
| Demote Workflow | Yes | Yes | Yes |
| Keep Kanban, Graph, Releases | Yes | Yes | Yes |
| Health tab fate | Merge → Files | Merge → Pulse | Keep separate ("Code Quality") |
| Tab count | 5 | 6 | 6 |

**Key disagreement**: Health tab — three different destinations proposed.

---

## Round 2: The Health Tab Debate + New Topics

Facilitator framed a concrete design proposal: "Files tab gets a collapsible Code Quality Overview section at top, containing all current Health widgets."

### Health Tab Resolution

**Senior Power User** (maintained position): Refined with specific design — overview section expanded by default, collapsible, collapse state in localStorage. The orientation function the UX specialist identified is preserved because the summary is the first thing you see when opening Files.

**Systems Thinker** (revised position): Originally proposed merging Health into Metrics/Pulse. Now agrees Health belongs with Files because they share a single feedback loop (code quality loop: files scanned → findings raised → findings resolved). Metrics operates on a different loop (work flow: issues created → worked → completed). Merging dissimilar loops creates conceptual coupling without data coupling.

**UX Specialist** (revised position): Conceded that the "hospital triage board" analogy actually argues *for* the collapsible header design — "the triage board is at the entrance to the ward, not in a separate building." The separate tab provided no value beyond what summary-above-detail achieves. Added implementation constraint: aggregate stats (severity donut, coverage %) must be visible without scrolling when expanded.

**Result**: Unanimous — Health merges into Files as collapsible overview header. Tab count: 5.

### New Topic A: Table/List View

**Unanimous**: Add as a third Kanban mode (Board / Cluster / List), not a 6th tab. Rationale:
- Reuses existing mode-switcher pattern and `getFilteredIssues()` data pipeline
- Filter state persists across mode switches
- Zero navigation cost
- Maps to v2.0 progressive thresholds: 5-50 issues (Board), 50-200 (Cluster), 200+ (List)

### New Topic B: Filter Bar Element Triage

All three panelists audited all 17 header elements. Areas of agreement and disagreement:

| Element | Senior User | Systems Thinker | UX Specialist |
|---------|------------|-----------------|---------------|
| Ready toggle | Always visible | Always visible | Always visible |
| Blocked toggle | Behind disclosure | Always visible | Always visible |
| Priority dropdown | Behind disclosure | Behind disclosure | Always visible |
| Search | Always visible | Always visible | Always visible |
| Status checkboxes | Behind disclosure | Always visible | Always visible (as pills) |
| Multi-select | Behind disclosure | Behind disclosure | Relocate to view toolbar |
| Updated-days | Behind disclosure | Behind disclosure | Remove (Done time-bound covers it) |
| Presets + Save | Behind disclosure | Behind disclosure | Behind disclosure |
| Help icons (×3) | Remove | Remove | Remove |

---

## Round 3: Final Convergence

### Filter Bar Votes (resolved by majority with concessions)

**Blocked toggle → always visible** (2/3 agreed). Senior user conceded: "the count is ambient information worth having visible."

**Priority dropdown → behind disclosure** (2/3 agreed). UX specialist conceded: "used more like 'set at session start' than 'adjusted mid-session.'"

**Status → always visible as compact pills** (2/3 agreed). Senior user conceded: "the pill redesign makes them compact enough to earn header real estate." UX specialist additionally recommended changing default to Open+Active only (Done requires opt-in).

**Multi-select → view-specific toolbar** (unanimous after UX specialist's proposal). All agreed this is strictly better than any header placement. In List mode, replaced by inline row checkboxes.

### Implementation Caveats (collected from all panelists)

**Files + Health merge:**
- Lazy-load Health widgets when expanded; skip API calls when collapsed (Senior User)
- Keep `health.js` as separate importable module — don't inline into `files.js` (Systems Thinker, Senior User)
- Hotspot click applies `path_prefix` filter, not "scroll to row" — handles pagination correctly (UX Specialist)
- When detail panel opens, consider collapsing overview to prevent occlusion (UX Specialist)
- Zero-file projects: collapse overview, show single unified empty state (all three)

**Insights (Metrics + Activity):**
- Three internal structure options proposed: two-column layout (UX Specialist), internal tabs (Systems Thinker), vertical stacking with Activity capped at 10-15 events (Senior User). All viable; choose based on implementation budget.
- Metrics content must always be above/before Activity (unanimous)

**List mode:**
- Rename "Standard" → "Board" for clarity alongside "List" (UX Specialist)
- Cap Kanban mode switcher at 3 built-in modes; future modes become pack-registered views (Systems Thinker)
- Default status filter to Open+Active prevents "suddenly everything visible" shock in List mode (UX Specialist)

**Workflow demotion:**
- Two access paths: "View workflow" link in detail panel status section (primary) + Settings gear menu (secondary) (Systems Thinker)

**Updated-days dropdown:**
- Originally all three placed behind disclosure. After the Done dropdown-toggle with time-bound was introduced (replacing the need for Updated-days on closed issues), UX Specialist revised to "remove entirely" — the Done time-bound covers the primary use case. Senior User and Systems Thinker concurred (unanimous remove). Can be re-added behind disclosure later if needed for open/active recency filtering.

**v1.5.0 compatibility:**
- Refactor `switchView()` to be data-driven (~20 LOC change) — directly prepares for view registry phase 1 (Systems Thinker)
- Add URL hash redirect aliases for removed tab IDs (`health`→`files`, `activity`→`insights`, `workflow`→settings) with `console.warn` deprecation (UX Specialist)
- Extract `computeHealthScore()` from `graph.js` into shared analytics module (Systems Thinker)

**Edge cases:**
- Projects with no releases: consider hiding Releases tab until first release exists (UX Specialist)
- Projects with no scan data: single unified empty state in Files, overview collapsed (Senior User, UX Specialist)

---

## Final Recommendation (Unanimous)

### Tab Structure: 8 → 5

| # | Tab | Contents | Source |
|---|-----|----------|--------|
| 1 | **Kanban** | Board / Cluster / List modes, drag-and-drop, type filtering | Existing + new List mode |
| 2 | **Graph** | Dependency graph, critical path, focus mode, path tracing | Existing (unchanged) |
| 3 | **Releases** | Release roadmap, progress trees, target dates | Existing (promoted from #8) |
| 4 | **Insights** | Flow metrics + activity feed (merged) | Metrics + Activity |
| 5 | **Files** | Code quality overview (header) + file table + findings | Files + Health |

**Demoted**: Workflow → Settings gear menu + detail panel contextual link
**View registry mapping**: core=[kanban, graph], planning=[releases], analytics=[insights], engineering=[files], optional=[workflow]

### Header Filter Bar: 17 → 8 Always-Visible Elements

The original 17 elements in the filter bar (Ready toggle, Ready `?`, Blocked toggle, Blocked `?`, Priority dropdown, Updated-days dropdown, Search input, Search clear, Multi-select button, Open checkbox, Active checkbox, Closed checkbox, Presets dropdown, Save button, Health badge, Health `?`, Settings gear) are reduced as follows:

**Always visible (8)**: Ready toggle, Blocked toggle, Search, Status pills (Open/Active/Done dropdown-toggle), Health badge, Settings gear
**Behind disclosure (2)**: Priority, Presets (with Save merged in)
**Relocated (1)**: Multi-select → view-specific toolbar
**Removed (4)**: 3 help icons (replaced by `title` tooltips), Updated-days dropdown (subsumed by Done time-bound for closed issues; can be re-added behind disclosure later if needed for open/active recency)
**Merged (2→1)**: Save button merged into Presets dropdown

### Migration Prep (do alongside tab consolidation)

1. Refactor `switchView()` to iterate over `viewLoaders` map (v1.5.0 phase 1 prep)
2. Add redirect aliases for removed tab hash routes
3. Extract `computeHealthScore()` from `graph.js`

---

## Related Issues

- filigree-581ef7c35b: "Re-examine dashboard UX layout: consolidate panels and add pagination" — this recommendation supersedes and expands on that issue's scope
- filigree-edf8712ea6: "Templated Dashboard UX" (v1.5.0 epic) — recommendations designed to reduce migration cost
- filigree-3c35fe71ff: "Dashboard: Progressive Structure & Andon Cord" (v2.0) — List mode and progressive thresholds align with this vision
- filigree-0d43e044c8: "v1.5.0 — Next Foundation (Templated Web UX)" — tab consolidation + `switchView()` refactor directly prepare for this release
