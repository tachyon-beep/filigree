# Dashboard UX Restructure — Design

## Summary

Restructure the dashboard from 8 top-level tabs to 5 by merging overlapping views, demoting reference material, and simplifying the header filter bar. Add a List mode to Kanban for large-project scale. Prepare the view routing for v1.5.0's templated dashboard.

Informed by a 3-specialist panel review (minutes: `2026-03-04-dashboard-ux-review-minutes.md`).

Covers filigree issues: 581ef7c35b (UX layout consolidation), and prepares for edf8712ea6 (Templated Dashboard UX).

## Navigation

Five top-level tabs replace the current eight:

```
[Kanban] [Graph] [Releases] [Insights] [Files]
```

- **Kanban**: Daily work surface (Board / Cluster / List modes)
- **Graph**: Dependency visualization, critical path, focus mode
- **Releases**: Release roadmap, progress trees, target dates
- **Insights**: Flow metrics + recent activity (merged from Metrics + Activity)
- **Files**: File tracking + code quality overview (merged from Files + Health)

Removed tabs:
- **Health** → merged into Files as collapsible overview header
- **Activity** → merged into Insights as collapsible `<details>` section
- **Workflow** → demoted to Settings gear menu + detail panel contextual link

## Architecture

### New files

| File | Purpose |
|------|---------|
| `static/js/analytics.js` | Health score + impact score computation (extracted from `graph.js`) |

### Modified files

| File | Changes |
|------|---------|
| `static/dashboard.html` | Remove `#healthView`, `#activityView`, `#workflowView` containers and tab buttons; rename `#metricsView` → `#insightsView`; redesign header filter bar (status pills, Done dropdown-toggle, Filters disclosure); add List mode button to Kanban toolbar; add Workflow entry to Settings dropdown; remove help icons |
| `static/js/router.js` | Data-driven `switchView()` using `viewLoaders` registry for DOM toggling; hash redirect aliases (`#health`→`#files`, `#activity`→`#insights`, `#workflow`→`#kanban`); add `kanban-list` hash; rename `standard`→`board` in kanban mode |
| `static/js/state.js` | Update `TOUR_STEPS` for 5-tab layout (note: existing tour text says "7 views" but omits Releases — fix count and list all 5); add `insightsActivityExpanded` flag; update `kanbanMode` default from `"standard"` to `"board"` |
| `static/js/app.js` | Re-wire view registrations (5 views instead of 8); import `computeHealthScore`/`computeImpactScores` from `analytics.js`; update `window.*` exports; remove `window.loadHealth`, `window.loadActivity`, `window.loadWorkflow`; add `window.showWorkflowModal` |
| `static/js/views/files.js` | Import health overview render functions from `health.js`; add collapsible "Code Quality Overview" section above file table; hotspot click → `path_prefix` filter instead of `switchView`; scan run click → `scan_source` filter; lazy-load overview data; unified empty state for zero-file projects |
| `static/js/views/health.js` | Export 4 currently-private widget functions (`renderHotspotsWidget`, `renderDonutWidget`, `renderCoverageWidget`, `renderRecentScansWidget`); add new `renderHealthOverview(container)` composite export; remove `loadHealth()` from view registration (no longer a standalone view); remove `filterFilesByScanSource()` export (behavior reimplemented in files.js without `switchView` call); widget functions accept callbacks instead of calling `switchView` directly |
| `static/js/views/metrics.js` | Absorb Activity rendering as `<details>` section at bottom; cap Activity at 15 events with "Show more"; rename DOM references from `metricsContent` → `insightsContent`; container ID from `metricsView` → `insightsView` |
| `static/js/views/activity.js` | Retain module; export `renderActivitySection(container, limit)` for embedding by metrics.js; remove standalone `loadActivity()` registration |
| `static/js/views/kanban.js` | Add List mode renderer (`renderListMode()`); rename `standard` → `board`; add `btnList` toggle alongside `btnBoard`/`btnCluster`; inline row checkboxes in List mode |
| `static/js/views/workflow.js` | Remove from tab registration; add `showWorkflowModal(type?)` for modal/overlay rendering; retain `loadPlanView` for detail panel use |
| `static/js/views/graph.js` | Remove `computeHealthScore()`, `computeImpactScores()` (moved to `analytics.js`); remove `showHealthHelp()`, `showReadyHelp()`, `showBlockedHelp()` (replaced by `title` attributes on HTML buttons — no new functions needed); import score functions from `analytics.js` |
| `static/js/filters.js` | Replace status checkboxes with pill toggle logic; add Done dropdown-toggle with time-bound (7d/14d/30d/All) filtering on `closed_at`; remove `Updated: N days` dropdown entirely (subsumed by Done time-bound for closed issues) |
| `static/js/ui.js` | Add `showWorkflowModal()` relay. Note: `showHealthHelp()`, `showReadyHelp()`, `showBlockedHelp()` are in `graph.js` (not ui.js) — they are removed entirely and replaced by `title` attributes in the HTML |

### No backend changes required

All changes are frontend-only. The dashboard API endpoints remain unchanged.

---

## 1. Files + Health Overview Merge

### Layout

The Files tab gains a collapsible "Code Quality Overview" section above the existing file table. The overview contains the same four widgets currently in the Health tab, rendered in a 2×2 grid:

```
┌─────────────────────────────────────────────────────────────────┐
│  Files                                                          │
├─────────────────────────────────────────────────────────────────┤
│  ▾ Code Quality Overview                              [collapse]│
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ Top Hotspot Files    │  │ Findings by Severity  │            │
│  │  src/core.py    ████ │  │      ┌───┐           │            │
│  │  src/dash.py    ███  │  │     /     \   32 crit │            │
│  │  src/api.js     ██   │  │    ( donut )  18 high │            │
│  │  (click → filter)    │  │     \     /   45 med  │            │
│  └──────────────────────┘  └──────────────────────┘            │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ Scan Coverage   42%  │  │ Recent Scan Activity  │            │
│  │ ████████░░░░░░░░░░░  │  │ ruff   scan-abc  2h  │            │
│  │ 7 files w/ findings  │  │ claude scan-xyz  1d  │            │
│  │ of 17 tracked        │  │ (click → filter)     │            │
│  └──────────────────────┘  └──────────────────────┘            │
├─────────────────────────────────────────────────────────────────┤
│  [Filter by path...______]  [Critical only ☐]                   │
│                                                                 │
│  Path          │ Lang │ Crit │ High │ Med │ Low │ Issues │ Scan │
│  ──────────────│──────│──────│──────│─────│─────│────────│──────│
│  src/core.py   │ py   │ 12   │ 5    │ 8   │ 2   │ 3      │ 2h  │
│  ...           │      │      │      │     │     │        │      │
│  [Prev] Page 1 of 4 [Next]                                     │
└─────────────────────────────────────────────────────────────────┘
```

### Behavior

**Collapsible overview:**
- Expanded by default on first visit
- Collapse state persisted in `localStorage` per project (key: `filigree_files_overview_collapsed.<projectKey>`)
- Toggle via a `<details open>` element or a custom collapse button

**Lazy loading:**
- When expanded, fires 3 API calls in parallel: `fetchHotspots(10)`, `fetchFileStats()`, `fetchScanRuns(10)`
- When collapsed (from persisted state), skips these calls entirely — only `loadFiles()` runs
- Re-fetches overview data if user expands after initial load

**Hotspot click behavior (changed):**
- Current: `switchView('files'); setTimeout(() => openFileDetail('...'), 100)` — fragile cross-tab hack
- New: apply `path_prefix` filter to the file table so the clicked file appears at the top, then open its detail panel. No `switchView` needed since we're already on the Files tab.

**Scan run click behavior (changed):**
- Current: `filterFilesByScanSource()` which calls `switchView('files')`
- New: apply `scan_source` filter directly to the file table. Already on Files tab.

**Empty state (zero tracked files):**
- If no files are tracked AND no scan data exists: collapse overview entirely, show single unified message: "No files tracked yet. Ingest scan results to see code health and file tracking."
- Do NOT show four empty widgets above an empty table.

### Module structure

`health.js` remains a separate module. It exports individual widget render functions that `files.js` imports:

```javascript
// health.js — exports
export function renderHealthOverview(container, options) { ... }
export function renderHotspotsWidget(hotspots, onClickFile) { ... }
export function renderDonutWidget(agg) { ... }
export function renderCoverageWidget(filesWithFindings, total) { ... }
export function renderRecentScansWidget(scanRuns, onClickScanRun) { ... }

// files.js — imports
import { renderHealthOverview } from './health.js';
```

Widget functions accept callback parameters for click actions instead of calling `switchView` directly. This preserves the component boundary for v1.5.0's component library where each widget becomes a registered component with `render(container, data, config)`.

---

## 2. Insights (Metrics + Activity Merge)

### Layout

The current Metrics view absorbs Activity as a collapsible `<details>` section at the bottom:

```
┌─────────────────────────────────────────────────────────────────┐
│  Insights                                            [30d ▾]    │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                      │
│  │ 42       │  │ 3.2h     │  │ 8.1h     │                      │
│  │Throughput│  │Cycle Time│  │Lead Time │                      │
│  └──────────┘  └──────────┘  └──────────┘                      │
│                                                                 │
│  By Type      │ Avg Cycle │ Count                               │
│  ─────────────│───────────│──────                               │
│  bug          │ 1.5h      │ 12                                  │
│  task         │ 4.2h      │ 30                                  │
│                                                                 │
│  Agent Workload (Active WIP)                                    │
│  bot-1  ████████████░░░  8                                      │
│  bot-2  ██████░░░░░░░░░  4                                      │
│                                                                 │
│  ▸ Recent Activity (15 events)           [Refresh]              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  ── 2026-03-04 ──                                        │   │
│  │  12:30  status_changed  Fix auth bug      open→wip  bot1│   │
│  │  11:45  created         Add validation    —         john │   │
│  │  ...                                                     │   │
│  │  [Show more]                                             │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Behavior

**Activity section:**
- Rendered as a `<details>` element, collapsed by default
- Expand state NOT persisted (lightweight — re-collapse on tab switch is fine)
- Capped at 15 events initially; "Show more" loads next 15
- Activity fetches independently from metrics (calls `fetchActivity(15)`)
- Time selector (30d dropdown) applies to flow metrics only; Activity always shows latest events

**Container renaming:**
- `#metricsView` → `#insightsView`
- `#metricsContent` → `#insightsContent`
- `btnMetrics` → `btnInsights`
- View registration: `registerView("insights", loadInsights)`

**activity.js retained as module:**
- Exports `renderActivitySection(container, limit)` for embedding
- `loadActivity()` standalone function retained but no longer registered as a view
- `metrics.js` calls `renderActivitySection()` to append the `<details>` block

---

## 3. Kanban List Mode

### Mode switcher

The Kanban toolbar gains a third mode button. "Standard" is renamed to "Board":

```
  [Board] [Cluster] [List]  | Type: [All types ▾] | Legend
```

### List mode layout

```
  P │ Type    │ Status      │ Title              │ Assignee │ Updated │ ⚡│ ☐ │
  ──│─────────│─────────────│────────────────────│──────────│─────────│──│───│
  0 │ 🐛 bug  │ in_progress │ Fix auth bypass    │ bot-1    │ 2h ago  │ 3│ ☐ │
  1 │ ✨ feat │ open        │ Add dark mode      │ —        │ 1d ago  │ 0│ ☐ │
  2 │ 📋 task │ open        │ Update docs        │ —        │ 3d ago  │ 0│ ☐ │
  2 │ 📋 task │ open        │ Refactor filters   │ john     │ 5d ago  │ 1│ ☐ │
```

### Behavior

**Data pipeline:** Consumes `getFilteredIssues()` — same as Board and Cluster. All header filters (Ready, Blocked, Priority, Search, Status) apply identically.

**Columns (sortable):**
- Priority (P) — numeric, sort asc/desc
- Type — icon + name
- Status — badge with category color
- Title — truncated, full text in title tooltip
- Assignee — or "—" if unassigned
- Updated — relative time ("2h ago", "1d ago")
- Blocks (⚡) — count of downstream issues this blocks
- Checkbox (☐) — inline batch select (replaces global multi-select toggle)

**Interactions:**
- Row click → `openDetail(issueId)` — same detail panel slide-in as card click
- Column header click → sort by that column (toggle asc/desc)
- Checkbox click → add to batch selection; batch bar appears at bottom (same as current multi-select)
- Ready issues: green left border (same `ready-border` class as cards)
- Blocked issues: red left border
- Row density: ~40-60px per row, targeting ~30 visible rows without scrolling on a 1080p screen

**State:**
- `state.kanbanMode` values: `"board"` (was `"standard"`), `"cluster"`, `"list"`
- Mode persisted via hash: `#kanban` (board), `#kanban-cluster`, `#kanban-list`
- `switchKanbanMode("list")` follows existing pattern

**No pagination initially.** All filtered issues render. Virtual scrolling or "Load more" can be added later if performance requires it (the panel identified this as a v2.0 progressive structure concern, not a v1.4.x blocker).

### Router changes

```javascript
// switchKanbanMode updated for 3 modes
document.getElementById("btnBoard").className = mode === "board" ? ACTIVE : INACTIVE;
document.getElementById("btnCluster").className = mode === "cluster" ? ACTIVE : INACTIVE;
document.getElementById("btnList").className = mode === "list" ? ACTIVE : INACTIVE;

// parseHash updated
} else if (view === "kanban-list") {
  state.currentView = "kanban";
  state.kanbanMode = "list";
}
```

---

## 4. Header Filter Bar Redesign

### Current state: 17 interactive elements in the filter bar

(Ready toggle, Ready `?`, Blocked toggle, Blocked `?`, Priority dropdown, Updated-days dropdown, Search input, Search clear, Multi-select button, Open checkbox, Active checkbox, Closed checkbox, Presets dropdown, Save button, Health badge, Health `?`, Settings gear)

### Target state: 8 always visible, 2 behind disclosure, 4 removed, 1 relocated, 2 merged

### Header (non-filter-bar, unchanged)

| Element | Notes |
|---------|-------|
| Filigree logo/title | No change |
| Project switcher | No change (hidden when single-project) |
| "+ New" button | No change |
| Tab buttons (×5) | Reduced from 8. Labels: Kanban, Graph, Releases, Insights, Files |

### Filter bar — always visible (8 of original 17)

| # | Element | Notes |
|---|---------|-------|
| 1 | Ready toggle + count | No change. `title` tooltip replaces `?` help icon |
| 2 | Blocked toggle + count | No change. `title` tooltip replaces `?` help icon |
| 3 | Search input + clear | No change |
| 4 | Status pills (Open) | Redesigned from checkbox — see below |
| 5 | Status pills (Active) | Redesigned from checkbox — see below |
| 6 | Status pills (Done) | Dropdown-toggle hybrid — see below |
| 7 | Health badge | No change. `title` tooltip replaces `?` help icon. Click → breakdown modal |
| 8 | Settings gear | Gains "Workflow diagram" entry |

### Status pills (redesigned from checkboxes)

Replace three labeled checkboxes with compact pill toggles:

```
Current:  ☑ Open  ☑ Active  ☑ Closed
New:      [Open] [Active] [Done: off]
```

**Open pill:** Toggle on/off. On by default.
**Active pill:** Toggle on/off. On by default.
**Done pill:** Dropdown-toggle hybrid. Off by default.
- Click toggles on/off
- When on, shows time-bound: `[Done: 7d ▾]`
- Dropdown options: 7 days (default), 14 days, 30 days, All time
- Time-bound filters on `closed_at` timestamp (not `updated_at`)
- Persisted in `localStorage` per project with the other filter settings

```
OFF:   [Open] [Active] [Done]
ON:    [Open] [Active] [Done: 7d ▾]
                        ├─ 7 days
                        ├─ 14 days
                        ├─ 30 days
                        └─ All time
```

### Behind "Filters ▾" disclosure (2 of original 17)

A `<details>` element (matching Graph's existing filter disclosure pattern) contains:

| Element | Notes |
|---------|-------|
| Priority dropdown | `[All] [P0-P1] [P2] [P3-P4]` — unchanged |
| Presets dropdown | "Save current..." merged as last option in dropdown (removes separate Save button — 2 original elements become 1) |

### Relocated (1 of original 17)

| Element | New location |
|---------|-------------|
| Multi-select toggle | Kanban and Graph view-specific toolbars. In List mode, replaced by inline row checkboxes. Not visible on Insights, Files, or Releases views. |

### Removed (4 of original 17)

| Element | Replacement |
|---------|------------|
| Ready `?` help icon | `title="Ready issues have no blockers and can be worked on immediately"` on the Ready button |
| Blocked `?` help icon | `title="Blocked issues are waiting on dependencies to be resolved"` on the Blocked button |
| Health `?` help icon | Explanation already in the health breakdown modal (click the badge) |
| Updated-days dropdown | Subsumed by Done time-bound for closed issues. Can be re-added behind disclosure later if needed for open/active recency filtering. |

### Settings gear dropdown (updated)

```
⚙ Settings
├─ ↻ Reload server
├─ ☀ Toggle theme
└─ ⊞ Workflow diagram    ← NEW
```

---

## 5. Workflow Demotion

The Workflow tab is removed from primary navigation. Two access paths replace it:

### Access path 1: Detail panel contextual link

In the issue detail panel's status section (where transition buttons are rendered), add a small link:

```
Status: in_progress  [→ review] [→ done]
View workflow for this type →
```

Clicking opens `showWorkflowModal(issue.type)` which renders the Cytoscape state-machine diagram in a modal overlay. The current node (the issue's status) is highlighted.

### Access path 2: Settings gear menu

"Workflow diagram" entry in the Settings dropdown opens `showWorkflowModal()` with the type dropdown selector, same as the current Workflow tab's toolbar.

### Implementation

`workflow.js` retains all its rendering logic. Changes:

1. Remove `registerView("workflow", loadWorkflow)` from `app.js`
2. Remove `#workflowView` container and `#btnWorkflow` button from `dashboard.html`
3. Add `showWorkflowModal(type?)` that creates a modal overlay containing:
   - Type dropdown selector (if no type passed)
   - The Cytoscape `#workflowCy` canvas (moved into modal)
   - Close button
4. The `loadPlanView()` function is unaffected — it renders inside the detail panel and has no dependency on the Workflow tab

---

## 6. Migration Prep (v1.5.0 Compatibility)

Three refactors done as part of this work to reduce v1.5.0 migration cost:

### 6a. Data-driven `switchView()`

Current `switchView()` in `router.js` has 8 hardcoded `getElementById` calls and 8 className assignments. Replace with iteration over the `viewLoaders` registry:

```javascript
// Current (hardcoded):
document.getElementById("graphView").classList.toggle("hidden", view !== "graph");
document.getElementById("kanbanView").classList.toggle("hidden", view !== "kanban");
// ... 6 more lines

// New (data-driven):
const VIEW_IDS = Object.fromEntries(
  Object.keys(viewLoaders).map(name => [name, `${name}View`])
);
const BTN_IDS = Object.fromEntries(
  Object.keys(viewLoaders).map(name => [name, `btn${name[0].toUpperCase()}${name.slice(1)}`])
);

export function switchView(view) {
  // Handle aliases (deprecated tab IDs)
  const ALIASES = { health: "files", activity: "insights" };
  if (ALIASES[view]) {
    console.warn(`[switchView] "${view}" is deprecated, redirecting to "${ALIASES[view]}"`);
    view = ALIASES[view];
  }

  state.currentView = view;

  for (const [name, elId] of Object.entries(VIEW_IDS)) {
    const el = document.getElementById(elId);
    if (el) el.classList.toggle("hidden", name !== view);
  }
  for (const [name, btnId] of Object.entries(BTN_IDS)) {
    const btn = document.getElementById(btnId);
    if (btn) btn.className = name === view ? ACTIVE_CLASS : INACTIVE_CLASS;
  }

  updateHash();
  // ... rest unchanged
}
```

This converts the hardcoded view list into a dynamic one — exactly what v1.5.0's view registry needs as its foundation.

### 6b. Hash redirect aliases

`parseHash()` maps deprecated hash values to their new destinations:

```javascript
// In parseHash() — reuse the same ALIASES map from switchView():
let view = parts[0] || "kanban";
if (ALIASES[view]) {
  console.warn(`[parseHash] Hash "#${view}" is deprecated, redirecting to "#${ALIASES[view]}"`);
  view = ALIASES[view];
}
// #workflow falls through to default (kanban) — no explicit alias needed
```

This prevents silent breakage for bookmarks, external links, or agent-side code that references old tab IDs.

### 6c. Extract health scoring from graph.js

Move `computeHealthScore()` and `computeImpactScores()` from `graph.js` (1223 LOC) to a new `analytics.js` shared module:

```javascript
// analytics.js (new, ~80 LOC)
import { state } from "./state.js";

export function computeHealthScore() { ... }  // moved from graph.js
export function computeImpactScores() { ... } // moved from graph.js
```

Both `app.js` and `graph.js` import from `analytics.js`. This decouples graph from health scoring and reduces `graph.js` by ~80 LOC.

---

## 7. View Registry Mapping (v1.5.0 Forward Design)

The 5-tab model maps to the planned v1.5.0 view registry as follows:

```
core_pack:        [kanban, graph]     — always registered, always visible
planning_pack:    [releases]          — registered when planning pack enabled
analytics_pack:   [insights]          — registered when analytics pack enabled
engineering_pack: [files]             — registered when engineering pack enabled
optional:         [workflow]          — user must explicitly enable in config
```

New users see 2 tabs (Kanban + Graph), growing to 5 as they enable packs. This aligns with v2.0's progressive structure thresholds:
- 5 issues: Board mode, Kanban + Graph sufficient
- 50 issues: Cluster mode, add Insights for flow tracking
- 200+ issues: List mode, full 5 tabs, all packs enabled

---

## Edge Cases

### Projects with no scan data
Files tab shows the overview collapsed by default with a single CTA: "No scan data yet — ingest results to see code health." The file table may still show registered files (without findings).

### Projects with no releases
Releases tab shows "No active releases" empty state. In v1.5.0, the view registry could support a "show when data exists" flag to hide Releases from the tab bar until the first release is created.

### Transition from old URLs
Hash aliases handle `#health`, `#activity`, `#workflow` gracefully with console.warn deprecation notices. Aliases removed in v1.5.0 when the view registry makes them unnecessary.

### Detail panel + overview occlusion
When the file detail panel slides in from the right on the Files tab, the overview section may be partially obscured. If this proves problematic in practice, programmatically collapse the overview when the detail panel opens.

### Kanban mode rename
`state.kanbanMode` value `"standard"` → `"board"`. The hash `#kanban` (no suffix) maps to Board mode. Old `#kanban` URLs continue to work (Board is the default).
