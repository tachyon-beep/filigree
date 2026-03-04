# Dashboard UX Restructure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure the dashboard from 8 tabs to 5 by merging Health→Files, Activity→Insights, demoting Workflow, adding Kanban List mode, and simplifying the header filter bar.

**Architecture:** All changes are frontend-only — vanilla JS modules in `src/filigree/static/js/` and one HTML file `src/filigree/static/dashboard.html`. No backend/API changes. No JS test framework exists; verification is manual browser testing against the running dashboard at `http://localhost:8885`.

**Tech Stack:** Vanilla JS (ES modules), Tailwind CSS (CDN), Cytoscape.js (for workflow graph)

**Design doc:** `docs/plans/2026-03-04-dashboard-ux-restructure-design.md`

---

## Task 1: Extract `analytics.js` from `graph.js`

Decouple health scoring from graph visualization. This unblocks Tasks 5 and 9.

**Files:**
- Create: `src/filigree/static/js/analytics.js`
- Modify: `src/filigree/static/js/views/graph.js:1057-1150` (remove two functions)
- Modify: `src/filigree/static/js/app.js:87-88` (change import source)

**Step 1: Create `analytics.js` with the two extracted functions**

Copy `computeImpactScores()` (graph.js:1057-1078) and `computeHealthScore()` (graph.js:1084-1150) into a new module. They only depend on `state` from `state.js`.

```javascript
// src/filigree/static/js/analytics.js
// ---------------------------------------------------------------------------
// Shared analytics — health score + impact score computation.
// Extracted from graph.js to decouple graph visualization from scoring.
// ---------------------------------------------------------------------------

import { state } from "./state.js";

export function computeImpactScores() {
  // ... exact copy from graph.js:1057-1078
}

export function computeHealthScore() {
  // ... exact copy from graph.js:1084-1150
}
```

**Step 2: Update `graph.js` — remove the two functions, import from analytics**

In `graph.js`, delete lines 1053-1150 (both functions + their section headers). At the top of graph.js, add:

```javascript
import { computeHealthScore, computeImpactScores } from "../analytics.js";
```

Do NOT re-export from graph.js — only app.js imports these functions, so update the import source there directly (Step 3). No backwards-compat shim needed.

**Step 3: Update `app.js` — change import source**

In `app.js:86-88`, change:

```javascript
// Before:
import {
  ...
  computeHealthScore,
  computeImpactScores,
  ...
} from "./views/graph.js";

// After:
import { computeHealthScore, computeImpactScores } from "./analytics.js";
```

Remove `computeHealthScore` and `computeImpactScores` from the graph.js import block (lines 87-88).

**Step 4: Verify in browser**

Open `http://localhost:8885`. Check:
- Health badge in top-right still shows a score (not `--`)
- Graph view still renders correctly
- No console errors

**Step 5: Commit**

```bash
git add src/filigree/static/js/analytics.js src/filigree/static/js/views/graph.js src/filigree/static/js/app.js
git commit -m "refactor: extract computeHealthScore and computeImpactScores into analytics.js"
```

---

## Task 2: Data-driven `switchView()` + hash aliases

Replace 8 hardcoded `getElementById` calls in `switchView()` with iteration over the `viewLoaders` registry. Add deprecation aliases for removed tab IDs. This is the foundation for all tab removal work.

**Files:**
- Modify: `src/filigree/static/js/router.js:32-90` (rewrite switchView)
- Modify: `src/filigree/static/js/router.js:145-170` (update parseHash)

**Step 1: Rewrite `switchView()` to be data-driven**

Replace router.js:32-90 with:

```javascript
// Deprecated tab aliases — redirect old tab IDs to new destinations.
const ALIASES = { health: "files", activity: "insights" };

export function switchView(view) {
  if (ALIASES[view]) {
    console.warn(`[switchView] "${view}" is deprecated, redirecting to "${ALIASES[view]}"`);
    view = ALIASES[view];
  }

  state.currentView = view;

  // Data-driven: toggle visibility for all registered views
  for (const name of Object.keys(viewLoaders)) {
    const el = document.getElementById(`${name}View`);
    if (el) el.classList.toggle("hidden", name !== view);
    const btn = document.getElementById(`btn${name[0].toUpperCase()}${name.slice(1)}`);
    if (btn) btn.className = name === view ? ACTIVE_CLASS : INACTIVE_CLASS;
  }

  updateHash();

  // Update skip link to target current view
  const skipLink = document.querySelector('a[href^="#"][class*="sr-only"]');
  if (skipLink) {
    skipLink.href = "#" + view + "View";
  }

  const loader = viewLoaders[view];
  if (loader) {
    try {
      loader();
    } catch (err) {
      console.error(`[switchView] Failed to load "${view}" view:`, err);
      const container = document.getElementById(view + "View");
      if (container) {
        container.innerHTML =
          '<div class="p-4 text-xs" style="color:var(--text-muted)">' +
          `Failed to load the ${view} view. ` +
          '<button class="underline" style="color:var(--accent)" ' +
          `onclick="switchView('${view}')">Retry</button>` +
          "</div>";
      }
    }
  }
}
```

**Step 2: Update `parseHash()` to use ALIASES**

Move the `ALIASES` const to module scope (above `switchView`). In `parseHash()` (router.js:145-170), replace the hardcoded if-else chain with:

```javascript
export function parseHash() {
  const hash = location.hash.slice(1);
  const parts = hash.split("&");
  let view = parts[0] || "kanban";

  // Apply deprecation aliases
  if (ALIASES[view]) {
    console.warn(`[parseHash] Hash "#${view}" is deprecated, redirecting to "#${ALIASES[view]}"`);
    view = ALIASES[view];
  }

  if (view === "kanban-cluster") {
    state.currentView = "kanban";
    state.kanbanMode = "cluster";
  } else if (view === "kanban-list") {
    state.currentView = "kanban";
    state.kanbanMode = "list";
  } else if (viewLoaders[view]) {
    state.currentView = view;
  } else {
    // Unknown view (including "workflow") falls through to default
    state.currentView = "kanban";
    state.kanbanMode = "standard";
  }

  const result = { project: null, issue: null };
  const issuePart = parts.find((p) => p.indexOf("issue=") === 0);
  if (issuePart) {
    state.selectedIssue = issuePart.split("=")[1];
    result.issue = state.selectedIssue;
  }
  const projectPart = parts.find((p) => p.indexOf("project=") === 0);
  if (projectPart) {
    result.project = decodeURIComponent(projectPart.split("=")[1]);
  }
  return result;
}
```

Note: `kanbanMode` still says `"standard"` for the default fallback — that gets renamed in Task 3.

**Step 3: Export ALIASES for use by parseHash**

The `ALIASES` const is already at module scope (above `switchView`), so `parseHash` can reference it directly. No extra export needed since both functions are in the same module.

**Step 4: Verify in browser**

- All 8 existing tabs still work (switchView is backwards-compatible — all 8 views are still registered)
- Navigate to `#health` — console.warn fires, redirects to Files view
- Navigate to `#activity` — console.warn fires, redirects to Metrics (currently, "insights" won't exist yet — that's fine, it falls through to kanban. This alias becomes active after Task 4 renames the view.)
- No console errors on normal tab switching

**Step 5: Commit**

```bash
git add src/filigree/static/js/router.js
git commit -m "refactor: data-driven switchView with hash aliases for v1.5.0 prep"
```

---

## Task 3: Rename kanban mode `standard` → `board`

Small rename that unblocks Task 8 (List mode).

**Files:**
- Modify: `src/filigree/static/js/state.js:98` (default value)
- Modify: `src/filigree/static/js/router.js:92-113` (switchKanbanMode)
- Modify: `src/filigree/static/js/router.js` parseHash default case
- Modify: `src/filigree/static/dashboard.html` (button ID `btnStandard` → `btnBoard`)
- Modify: `src/filigree/static/js/app.js:529-530` (init mode switch)

**Step 1: Update `state.js`**

Change line 98:
```javascript
// Before:
kanbanMode: "standard",
// After:
kanbanMode: "board",
```

**Step 2: Update `switchKanbanMode()` in `router.js`**

Lines 100-103, change `btnStandard` → `btnBoard` and `"standard"` → `"board"`:

```javascript
document.getElementById("btnBoard").className =
  mode === "board"
    ? "px-2 py-0.5 rounded bg-accent text-primary"
    : "px-2 py-0.5 rounded bg-overlay bg-overlay-hover";
```

**Step 3: Update `parseHash()` default case**

In the new parseHash (from Task 2), the else fallback already sets `state.kanbanMode = "standard"`. Change to:

```javascript
state.kanbanMode = "board";
```

**Step 4: Update `dashboard.html` button**

Find the kanban mode buttons (search for `btnStandard`). Change:

```html
<!-- Before: -->
<button id="btnStandard" onclick="switchKanbanMode('standard')" ...>Standard</button>
<!-- After: -->
<button id="btnBoard" onclick="switchKanbanMode('board')" ...>Board</button>
```

**Step 5: Update `app.js` init**

Lines 529-530:
```javascript
// Before:
else switchKanbanMode("standard");
// After:
else switchKanbanMode("board");
```

**Step 6: Verify in browser**

- Kanban view loads, Board button is highlighted
- Switching Board/Cluster works
- Hash shows `#kanban` for Board mode, `#kanban-cluster` for Cluster

**Step 7: Commit**

```bash
git add src/filigree/static/js/state.js src/filigree/static/js/router.js src/filigree/static/dashboard.html src/filigree/static/js/app.js
git commit -m "refactor: rename kanban mode 'standard' to 'board'"
```

---

## Task 4: Merge Activity into Insights (Metrics + Activity → Insights)

Remove the Activity tab. Rename Metrics → Insights. Add Activity as a collapsible `<details>` section at the bottom of the Insights view.

**Files:**
- Modify: `src/filigree/static/js/views/activity.js` (add `renderActivitySection` export)
- Modify: `src/filigree/static/js/views/metrics.js` (rename to Insights, embed Activity)
- Modify: `src/filigree/static/dashboard.html` (remove Activity tab button + container, rename Metrics → Insights)
- Modify: `src/filigree/static/js/app.js` (remove Activity view registration + window export, rename metrics registration)

**Step 1: Add `renderActivitySection()` to `activity.js`**

Keep the existing `loadActivity()` for backwards compat. Add a new export that renders into any container with a configurable limit:

```javascript
/**
 * Render activity events into a container (for embedding in Insights view).
 * Returns the number of events rendered.
 */
export async function renderActivitySection(container, limit = 15) {
  container.innerHTML = '<div style="color:var(--text-muted)" class="text-xs">Loading activity...</div>';
  try {
    const events = await fetchActivity(limit);
    if (!events || !events.length) {
      container.innerHTML =
        '<div class="text-xs" style="color:var(--text-muted)">No recent activity.</div>';
      return 0;
    }
    let lastDay = "";
    container.innerHTML = events
      .map((e) => {
        // ... same rendering logic as loadActivity() lines 26-72
        // but using the passed container instead of getElementById
      })
      .join("");
    return events.length;
  } catch (err) {
    console.error("[renderActivitySection] Failed:", err);
    container.innerHTML =
      '<div class="text-xs text-red-400">Failed to load activity.</div>';
    return 0;
  }
}
```

Extract the event-rendering map function from `loadActivity()` into a shared helper to avoid duplication.

**Step 2: Update `metrics.js` to embed Activity**

Add import at top:
```javascript
import { renderActivitySection } from "./activity.js";
```

At the end of `loadMetrics()`, after the agent workload section (after line 108), append the Activity `<details>` block:

```javascript
// Activity feed (collapsed by default)
const activityDetails = document.createElement("details");
activityDetails.className = "rounded mt-4";
activityDetails.style.cssText = "background:var(--surface-raised);border:1px solid var(--border-default)";
activityDetails.innerHTML =
  '<summary class="cursor-pointer select-none text-xs font-medium px-4 py-3" style="color:var(--text-secondary)">' +
  'Recent Activity</summary>' +
  '<div id="activityEmbedContent" class="px-4 pb-3"></div>';
container.appendChild(activityDetails);

activityDetails.addEventListener("toggle", async () => {
  if (activityDetails.open) {
    const content = document.getElementById("activityEmbedContent");
    if (content && !content.dataset.loaded) {
      const count = await renderActivitySection(content, 15);
      content.dataset.loaded = "1";
      // Update summary with count
      const summary = activityDetails.querySelector("summary");
      if (summary && count) summary.textContent = `Recent Activity (${count} events)`;
    }
  }
});
```

**Step 3: Rename DOM IDs in `metrics.js`**

Replace all references:
- `document.getElementById("metricsDays")` → `document.getElementById("insightsDays")`
- `document.getElementById("metricsContent")` → `document.getElementById("insightsContent")`

**Step 4: Update `dashboard.html`**

Remove the Activity tab button (`#btnActivity`) and container (`#activityView`).

Rename Metrics elements:
- `id="btnMetrics"` → `id="btnInsights"`, `onclick="switchView('metrics')"` → `onclick="switchView('insights')"`
- Button label: `Metrics` → `Insights`
- `id="metricsView"` → `id="insightsView"`
- `id="metricsContent"` → `id="insightsContent"`
- `id="metricsDays"` → `id="insightsDays"`

**Step 5: Update `app.js`**

Remove Activity view registration and window export:
```javascript
// Remove:
registerView("activity", loadActivity);
// Remove:
window.loadActivity = loadActivity;

// Change:
registerView("metrics", loadMetrics);
// To:
registerView("insights", loadMetrics);
```

Update the `loadActivity` import — keep it for `renderActivitySection` but remove standalone registration.

**Step 6: Verify in browser**

- Tab bar shows: Graph, Kanban, Insights (no Activity, no Metrics)
- Insights tab shows flow metrics cards, by-type table, agent workload
- "Recent Activity" disclosure at bottom — click to expand
- Expanding shows day-grouped timeline events
- `#activity` hash redirects to Insights (via alias from Task 2)
- No console errors

**Step 7: Commit**

```bash
git add src/filigree/static/js/views/activity.js src/filigree/static/js/views/metrics.js src/filigree/static/dashboard.html src/filigree/static/js/app.js
git commit -m "feat: merge Activity into Insights tab (Metrics + Activity)"
```

---

## Task 5: Merge Health into Files (collapsible overview)

Remove the Health tab. Add a collapsible "Code Quality Overview" section at the top of the Files view.

**Files:**
- Modify: `src/filigree/static/js/views/health.js` (export widgets, add renderHealthOverview, accept callbacks)
- Modify: `src/filigree/static/js/views/files.js` (import health overview, add collapsible section)
- Modify: `src/filigree/static/dashboard.html` (remove Health tab button + container)
- Modify: `src/filigree/static/js/app.js` (remove Health view registration + window exports)

**Step 1: Refactor `health.js` to export widget functions with callbacks**

Export the four currently-private widget functions. Change `renderHotspotsWidget` to accept a callback instead of hardcoding `switchView('files')`:

```javascript
// Before (line 98):
onclick="switchView('files');setTimeout(()=>openFileDetail('${escJsSingle(f.id)}'),100)"
// After:
onclick="${onClickFile ? onClickFile(f.id) : ''}"
```

Actually, since these return HTML strings, the callback should be a string expression:

```javascript
export function renderHotspotsWidget(hotspots, onClickFileExpr) {
  // ... existing logic, but line 98 changes to:
  `<div ... onclick="${onClickFileExpr(f.id)}" ...>`
}
```

For simplicity, accept a function that takes a file ID and returns an onclick expression string:

```javascript
// Called from files.js:
renderHotspotsWidget(hotspots, (fileId) => `openFileDetail('${escJsSingle(fileId)}')`)
```

Similarly, `renderRecentScansWidget` line 240 changes from `filterFilesByScanSource(...)` to a callback:

```javascript
export function renderRecentScansWidget(scanRuns, onClickScanExpr) {
  // line 240: onclick="${onClickScanExpr(run.scan_source || '')}"
}
```

Add `renderHealthOverview()` composite function:

```javascript
export async function renderHealthOverview(container, { onClickFile, onClickScan }) {
  container.innerHTML = '<div style="color:var(--text-muted)" class="text-xs">Loading code quality...</div>';
  try {
    const [hotspots, fileData, stats, scanRunData] = await Promise.all([
      fetchHotspots(10),
      fetchFiles({ limit: 1, offset: 0 }),
      fetchFileStats(),
      fetchScanRuns(10),
    ]);

    if (!hotspots && !fileData && !stats) {
      container.innerHTML =
        '<div class="text-xs" style="color:var(--text-muted)">No scan data yet — ingest results to see code health.</div>';
      return;
    }

    state.hotspots = hotspots;
    const agg = {
      critical: stats?.critical || 0,
      high: stats?.high || 0,
      medium: stats?.medium || 0,
      low: stats?.low || 0,
      info: stats?.info || 0,
    };
    const totalFiles = fileData?.total || 0;
    const filesWithFindings = stats?.files_with_findings || 0;
    const scanRuns = scanRunData?.scan_runs || [];

    container.innerHTML =
      '<div class="grid grid-cols-1 md:grid-cols-2 gap-4">' +
      renderHotspotsWidget(hotspots, onClickFile) +
      renderDonutWidget(agg) +
      renderCoverageWidget(filesWithFindings, totalFiles) +
      renderRecentScansWidget(scanRuns, onClickScan) +
      "</div>";
  } catch (_e) {
    container.innerHTML = '<div class="text-xs text-red-400">Failed to load health data.</div>';
  }
}
```

Remove `filterFilesByScanSource` export and `loadHealth` from being a standalone view.

**Step 2: Add collapsible overview to `files.js`**

At the top of `loadFiles()` (files.js:48), before the file table loads, insert the overview section:

```javascript
import { renderHealthOverview } from "./health.js";

export async function loadFiles() {
  const container = document.getElementById("filesContent");
  if (!container) return;

  // --- Code Quality Overview (collapsible) ---
  const projectKey = state.currentProjectKey || "__default__";
  const storageKey = `filigree_files_overview_collapsed.${projectKey}`;
  const collapsed = localStorage.getItem(storageKey) === "1";

  // Create or reuse overview section
  let overview = document.getElementById("filesOverview");
  if (!overview) {
    overview = document.createElement("details");
    overview.id = "filesOverview";
    overview.className = "mb-4 rounded";
    overview.style.cssText = "background:var(--surface-raised);border:1px solid var(--border-default)";
    if (!collapsed) overview.open = true;
    overview.innerHTML =
      '<summary class="cursor-pointer select-none text-xs font-medium px-4 py-3" style="color:var(--text-secondary)">' +
      "Code Quality Overview</summary>" +
      '<div id="filesOverviewContent" class="px-4 pb-3"></div>';

    const overviewCallbacks = {
      onClickFile: (fileId) => `openFileDetail('${escJsSingle(fileId)}')`,
      onClickScan: (source) => `filterFilesByScanSourceInline('${escJsSingle(source)}')`,
    };

    // Single helper that guards against double-render (including async race)
    const loadOverviewOnce = (content) => {
      if (content.dataset.loaded === "1" || content.dataset.loading === "1") return;
      content.dataset.loading = "1";
      renderHealthOverview(content, overviewCallbacks).then(() => {
        content.dataset.loaded = "1";
        content.dataset.loading = "";
      });
    };

    overview.addEventListener("toggle", () => {
      localStorage.setItem(storageKey, overview.open ? "0" : "1");
      if (overview.open) {
        const content = document.getElementById("filesOverviewContent");
        if (content) loadOverviewOnce(content);
      }
    });

    container.parentNode.insertBefore(overview, container);

    // Load overview data if expanded
    if (!collapsed) {
      const content = document.getElementById("filesOverviewContent");
      if (content) loadOverviewOnce(content);
    }
  }

  // ... existing file table loading continues below
}
```

Add the inline scan source filter function (replaces `filterFilesByScanSource`):

```javascript
// In files.js, module-level:
function filterFilesByScanSourceInline(source) {
  state.filesScanSource = source || "";
  state.filesPage.offset = 0;
  loadFiles();
}
window.filterFilesByScanSourceInline = filterFilesByScanSourceInline;
```

**Step 3: Update `dashboard.html`**

Remove the Health tab button (`#btnHealth`) and container (`#healthView`).

**Step 4: Update `app.js`**

```javascript
// Remove:
registerView("health", loadHealth);
// Remove:
window.loadHealth = loadHealth;
window.filterFilesByScanSource = filterFilesByScanSource;
// Remove the import of loadHealth and filterFilesByScanSource from health.js
```

**Step 5: Verify in browser**

- Tab bar has no "Health" button
- Files tab shows "Code Quality Overview" disclosure at top (expanded by default)
- Clicking collapse saves state; refreshing preserves collapse state
- Hotspot file click opens file detail panel (no tab switch needed)
- Scan run click filters file table by scan source
- `#health` hash redirects to Files (alias from Task 2)
- Empty project: overview collapses, shows "No scan data yet" message

**Step 6: Commit**

```bash
git add src/filigree/static/js/views/health.js src/filigree/static/js/views/files.js src/filigree/static/dashboard.html src/filigree/static/js/app.js
git commit -m "feat: merge Health tab into Files as collapsible Code Quality Overview"
```

---

## Task 6: Demote Workflow tab to modal

Remove Workflow from tab bar. Add modal access via Settings gear and detail panel.

**Files:**
- Modify: `src/filigree/static/js/views/workflow.js` (add `showWorkflowModal`)
- Modify: `src/filigree/static/dashboard.html` (remove Workflow tab button + container, add Settings menu entry)
- Modify: `src/filigree/static/js/app.js` (remove Workflow view registration, add modal window export)

**Step 1: Add `showWorkflowModal()` to `workflow.js`**

```javascript
/**
 * Show workflow state-machine diagram in a modal overlay.
 * If type is provided, renders that type's workflow directly.
 * If no type, shows a type selector dropdown.
 */
export function showWorkflowModal(type) {
  // Remove existing modal if any
  const existing = document.getElementById("workflowModal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "workflowModal";
  modal.className = "fixed inset-0 bg-black/50 flex items-center justify-center z-50";
  modal.onclick = (ev) => {
    if (ev.target === modal) modal.remove();
  };

  modal.innerHTML =
    '<div class="rounded-lg shadow-xl" style="background:var(--surface-raised);border:1px solid var(--border-strong);width:90vw;max-width:800px;height:70vh;display:flex;flex-direction:column">' +
    '<div class="flex items-center justify-between px-4 py-3" style="border-bottom:1px solid var(--border-default)">' +
    '<div class="flex items-center gap-3">' +
    '<span class="text-sm font-semibold" style="color:var(--text-primary)">Workflow Diagram</span>' +
    '<select id="workflowModalType" onchange="loadWorkflowInModal()" class="bg-overlay text-primary text-xs rounded px-2 py-1 border border-strong">' +
    '<option value="">Select type...</option></select>' +
    '</div>' +
    '<button onclick="document.getElementById(\'workflowModal\').remove()" class="text-muted text-primary-hover text-lg">&times;</button>' +
    '</div>' +
    '<div id="workflowModalCy" class="flex-1" style="min-height:0"></div>' +
    '</div>';

  document.body.appendChild(modal);

  // Populate type dropdown
  populateWorkflowModalTypes(type);
}
```

Add helper to populate and render:

```javascript
async function populateWorkflowModalTypes(preselect) {
  const select = document.getElementById("workflowModalType");
  if (!select) return;
  try {
    const registered = await fetchTypes();
    for (const t of registered) {
      if (WORKFLOW_HIDDEN[t.type]) continue;
      const opt = document.createElement("option");
      opt.value = t.type;
      opt.textContent = t.display_name || t.type;
      select.appendChild(opt);
    }
    if (preselect) {
      select.value = preselect;
      loadWorkflowInModal();
    } else if (select.options.length > 1) {
      select.value = select.options[1].value;
      loadWorkflowInModal();
    }
  } catch (_e) { /* non-critical */ }
}

export async function loadWorkflowInModal() {
  const select = document.getElementById("workflowModalType");
  const container = document.getElementById("workflowModalCy");
  if (!select || !container) return;
  const typeName = select.value;
  if (!typeName) return;

  // Reuse existing rendering logic from loadWorkflow but target the modal container
  try {
    const info = await fetchTypeInfo(typeName);
    if (!info) return;
    // Render Cytoscape into workflowModalCy
    renderWorkflowGraph(container, info, typeName);
  } catch (err) {
    container.innerHTML = '<div class="p-4 text-xs text-red-400">Failed to load workflow.</div>';
  }
}
```

Note: The existing `loadWorkflow()` function renders into `#workflowCy`. Extract the Cytoscape rendering into a shared `renderWorkflowGraph(container, info, typeName)` helper that both `loadWorkflow()` (for plan view) and `loadWorkflowInModal()` can call.

**Step 2: Update `dashboard.html`**

Remove `#btnWorkflow` button and `#workflowView` container.

Add to Settings dropdown (after the theme toggle button):

```html
<button onclick="showWorkflowModal();closeSettingsMenu()" class="w-full text-left px-3 py-2 bg-overlay-hover text-primary">&#8862; Workflow diagram</button>
```

Adjust rounding: the theme toggle is no longer `rounded-b-lg` — the new Workflow button gets `rounded-b-lg` instead.

**Step 3: Update `app.js`**

```javascript
// Remove:
registerView("workflow", loadWorkflow);
// Remove:
window.loadWorkflow = loadWorkflow;

// Add:
import { showWorkflowModal, loadWorkflowInModal } from "./views/workflow.js";
window.showWorkflowModal = showWorkflowModal;
window.loadWorkflowInModal = loadWorkflowInModal;
```

Keep `window.loadPlanView = loadPlanView` — plan view is still used in the detail panel.

**Step 4: Verify in browser**

- Tab bar has no "Workflow" button
- Settings gear → "Workflow diagram" opens modal with type dropdown + Cytoscape graph
- Escape or clicking backdrop closes modal
- `#workflow` hash falls through to kanban (default)

**Step 5: Commit**

```bash
git add src/filigree/static/js/views/workflow.js src/filigree/static/dashboard.html src/filigree/static/js/app.js
git commit -m "feat: demote Workflow tab to modal accessible via Settings gear"
```

---

## Task 7: Header filter bar redesign

Replace status checkboxes with pill toggles, add Done dropdown-toggle, remove help icons, remove Updated-days dropdown, move Presets + Priority behind disclosure, relocate Multi-select.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (header filter bar HTML)
- Modify: `src/filigree/static/js/filters.js` (pill toggle logic, Done time-bound, remove Updated-days)
- Modify: `src/filigree/static/js/views/graph.js:1187-1223` (remove help functions)
- Modify: `src/filigree/static/js/app.js` (remove help function window exports)

**Step 1: Redesign the filter bar HTML in `dashboard.html`**

Replace the filter bar section (lines 164-205) with:

```html
<!-- Filter bar -->
<div class="flex items-center gap-3">
  <button id="btnReady" onclick="toggleReady()" class="px-2 py-1 rounded text-xs font-medium bg-emerald-900/50 text-emerald-400 border border-emerald-700" title="Ready issues have no blockers and can be worked on immediately">
    &#9679; Ready (<span id="readyCount">0</span>)
  </button>
  <button id="btnBlocked" onclick="toggleBlocked()" class="px-2 py-1 rounded text-xs font-medium bg-overlay text-secondary border border-strong" title="Blocked issues are waiting on dependencies to be resolved">
    &#128279; Blocked (<span id="blockedCount">0</span>)
  </button>
  <label for="filterSearch" class="text-xs text-secondary sr-only">Search</label>
  <div class="relative">
    <input id="filterSearch" type="text" placeholder="Search..." oninput="debouncedSearch()"
           class="bg-overlay text-primary text-xs rounded px-3 py-1 pr-6 border border-strong w-56 focus:outline-none focus-accent">
    <button id="searchClear" onclick="clearSearch()"
            class="hidden absolute right-1.5 top-1/2 -translate-y-1/2 text-muted text-primary-hover text-xs leading-none" title="Clear search" aria-label="Clear search">&times;</button>
  </div>
  <!-- Status pills -->
  <div class="flex gap-1">
    <button id="pillOpen" onclick="toggleStatusPill('open')" class="px-2 py-1 rounded text-xs font-medium bg-accent text-primary">Open</button>
    <button id="pillActive" onclick="toggleStatusPill('active')" class="px-2 py-1 rounded text-xs font-medium bg-accent text-primary">Active</button>
    <div class="relative">
      <button id="pillDone" onclick="toggleStatusPill('done')" class="px-2 py-1 rounded text-xs font-medium bg-overlay text-secondary border border-strong">Done</button>
      <select id="doneTimeBound" onchange="applyFilters()" class="hidden absolute top-full right-0 mt-1 bg-overlay text-primary text-xs rounded px-1 py-0.5 border border-strong z-10">
        <option value="7">7 days</option>
        <option value="14">14 days</option>
        <option value="30">30 days</option>
        <option value="0">All time</option>
      </select>
    </div>
  </div>
  <!-- Filters disclosure -->
  <details class="shrink-0">
    <summary class="cursor-pointer select-none px-2 py-1 rounded text-xs font-medium bg-overlay text-secondary border border-strong">Filters</summary>
    <div class="absolute mt-1 rounded-lg shadow-xl text-xs z-40 p-3 flex flex-col gap-2" style="background:var(--surface-raised);border:1px solid var(--border-strong)">
      <label for="filterPriority" class="text-xs text-secondary">Priority</label>
      <select id="filterPriority" onchange="applyFilters()" class="bg-overlay text-primary text-xs rounded px-2 py-1 border border-strong">
        <option value="all">All</option>
        <option value="0-1">P0-P1</option>
        <option value="2">P2</option>
        <option value="3-4">P3-P4</option>
      </select>
      <label for="filterPreset" class="text-xs text-secondary">Presets</label>
      <select id="filterPreset" onchange="loadPreset()" class="bg-overlay text-primary text-xs rounded px-2 py-1 border border-strong">
        <option value="">Presets...</option>
      </select>
    </div>
  </details>
</div>
```

Remove the 3 help icon buttons, the Multi-select button, the Updated-days dropdown, and the Save preset button.

**Step 2: Update `filters.js` — pill toggle logic**

First, add pill state tracking to `state.js` (near the filter-related properties):

```javascript
statusPills: { open: true, active: true, done: false },
```

This is the source of truth for pill state — never sniff CSS classes.

Add `toggleStatusPill()` function:

```javascript
const PILL_ON = "px-2 py-1 rounded text-xs font-medium bg-accent text-primary";
const PILL_OFF = "px-2 py-1 rounded text-xs font-medium bg-overlay text-secondary border border-strong";

export function toggleStatusPill(category) {
  const pillBtn = document.getElementById(
    category === "open" ? "pillOpen" : category === "active" ? "pillActive" : "pillDone"
  );
  state.statusPills[category] = !state.statusPills[category];
  const isOn = state.statusPills[category];

  if (category === "done") {
    const dropdown = document.getElementById("doneTimeBound");
    if (isOn) {
      const days = dropdown.value || "7";
      pillBtn.className = PILL_ON;
      pillBtn.textContent = `Done: ${days === "0" ? "All" : days + "d"}`;
      dropdown.classList.remove("hidden");
    } else {
      pillBtn.className = PILL_OFF;
      pillBtn.textContent = "Done";
      dropdown.classList.add("hidden");
    }
  } else {
    pillBtn.className = isOn ? PILL_ON : PILL_OFF;
  }
  applyFilters();
}
```

Update `getFilteredIssues()` to read from state instead of DOM:

```javascript
// Replace:
const showOpen = document.getElementById("filterOpen").checked;
const showActive = document.getElementById("filterInProgress").checked;
const showClosed = document.getElementById("filterClosed").checked;

// With:
const showOpen = state.statusPills.open;
const showActive = state.statusPills.active;
const showDone = state.statusPills.done;
```

Add Done time-bound filter logic:

```javascript
if (showDone) {
  const doneTimeBound = parseInt(document.getElementById("doneTimeBound")?.value || "7", 10);
  if (doneTimeBound > 0) {
    const cutoff = Date.now() - doneTimeBound * 86400000;
    items = items.filter((i) => {
      const cat = i.status_category || "open";
      if (cat === "done") {
        // Time-bound: only show Done issues closed within the window
        const closedAt = i.closed_at || i.updated_at;
        return closedAt && new Date(closedAt).getTime() >= cutoff;
      }
      return true; // Open/Active pass through
    });
  }
}
```

Remove the `filterUpdatedDays` logic (lines 129-136 of filters.js).

Update `applyFilterState()` and `getFilterState()` to use pills instead of checkboxes. Update `DEFAULT_PROJECT_FILTERS` to default `closed: false` (Done is off by default). Add `doneTimeBound: "7"` to the filter settings.

**Step 3: Remove help functions from `graph.js`**

Delete `showHealthHelp()` (line 1187-1199), `showReadyHelp()` (1201-1211), `showBlockedHelp()` (1213-1223). These are replaced by `title` attributes on the HTML buttons.

**Step 4: Update `app.js`**

Remove window exports:
```javascript
// Remove:
window.showHealthHelp = showHealthHelp;
window.showReadyHelp = showReadyHelp;
window.showBlockedHelp = showBlockedHelp;

// Add:
window.toggleStatusPill = toggleStatusPill;
```

Remove the imports of `showHealthHelp`, `showReadyHelp`, `showBlockedHelp` from graph.js.

Remove `window.toggleMultiSelect` (relocated to view-specific toolbars — but keep the function in filters.js for kanban/graph to call directly via import).

**Step 5: Verify in browser**

- Filter bar shows: Ready, Blocked, Search, [Open] [Active] [Done], Filters disclosure
- Open/Active pills highlighted by default, Done is off
- Clicking Done turns it on, shows "Done: 7d" and dropdown appears
- Changing dropdown value updates button label and filters
- "Filters" disclosure opens showing Priority + Presets
- No help icon `?` buttons visible
- No Updated-days dropdown
- No Multi-select button in header (will be re-added to kanban toolbar in Task 8)

**Step 6: Commit**

```bash
git add src/filigree/static/dashboard.html src/filigree/static/js/filters.js src/filigree/static/js/views/graph.js src/filigree/static/js/app.js
git commit -m "feat: redesign header filter bar — status pills, Done time-bound, remove help icons"
```

---

## Task 8: Kanban List mode

Add a third mode to the Kanban view — a dense sortable table for large projects.

**Files:**
- Modify: `src/filigree/static/js/views/kanban.js` (add `renderListMode()`)
- Modify: `src/filigree/static/js/router.js` (add `btnList` to switchKanbanMode, update hash for list)
- Modify: `src/filigree/static/dashboard.html` (add List button to kanban toolbar)
- Modify: `src/filigree/static/js/router.js` updateHash (add kanban-list case)

**Step 1: Add List mode button to `dashboard.html`**

Find the kanban toolbar buttons (search for `btnBoard`). After the Cluster button, add:

```html
<button id="btnList" onclick="switchKanbanMode('list')" class="px-2 py-0.5 rounded bg-overlay bg-overlay-hover" title="Table list view for large projects">List</button>
```

**Step 2: Update `switchKanbanMode()` in `router.js`**

Add the `btnList` toggle (after the existing Board and Cluster toggles):

```javascript
const btnList = document.getElementById("btnList");
if (btnList) {
  btnList.className = mode === "list"
    ? "px-2 py-0.5 rounded bg-accent text-primary"
    : "px-2 py-0.5 rounded bg-overlay bg-overlay-hover";
}
```

**Step 3: Update `updateHash()` in `router.js`**

Add the kanban-list hash case (after the kanban-cluster case at line 133):

```javascript
if (state.currentView === "kanban" && state.kanbanMode === "list") {
  hash = "#kanban-list";
} else if (state.currentView === "kanban" && state.kanbanMode === "cluster") {
  hash = "#kanban-cluster";
}
```

**Step 4: Add `renderListMode()` to `kanban.js`**

In `renderKanban()`, add a new branch after the cluster/typeTemplate checks:

```javascript
if (state.kanbanMode === "list") {
  board.innerHTML = renderListMode(items);
  return;
}
```

Implement `renderListMode()`:

```javascript
function renderListMode(items) {
  if (!items.length) {
    return '<div class="p-6 text-center text-xs" style="color:var(--text-muted)">No issues match filters.</div>';
  }

  // Sort state (module-level)
  const sortCol = state._listSortCol || "priority";
  const sortDir = state._listSortDir || "asc";

  // Sort items
  const sorted = [...items].sort((a, b) => {
    let cmp = 0;
    switch (sortCol) {
      case "priority": cmp = a.priority - b.priority; break;
      case "type": cmp = (a.type || "").localeCompare(b.type || ""); break;
      case "status": cmp = (a.status || "").localeCompare(b.status || ""); break;
      case "title": cmp = (a.title || "").localeCompare(b.title || ""); break;
      case "assignee": cmp = (a.assignee || "").localeCompare(b.assignee || ""); break;
      case "updated": cmp = new Date(a.updated_at || 0) - new Date(b.updated_at || 0); break;
      case "blocks": cmp = (state.impactScores[a.id] || 0) - (state.impactScores[b.id] || 0); break;
    }
    return sortDir === "desc" ? -cmp : cmp;
  });

  const headerCell = (col, label) =>
    `<th class="text-left py-2 px-2 cursor-pointer select-none text-primary-hover" onclick="sortListMode('${col}')" style="color:var(--text-muted)">${label}${sortCol === col ? (sortDir === "asc" ? " ▲" : " ▼") : ""}</th>`;

  const rows = sorted.map((i) => {
    const icon = TYPE_ICONS[i.type] || "";
    const cat = i.status_category || "open";
    const catColor = CATEGORY_COLORS[cat] || CATEGORY_COLORS.open;
    const updated = i.updated_at ? _relativeTime(i.updated_at) : "—";
    const blocks = state.impactScores[i.id] || 0;
    const readyClass = i.is_ready ? "border-l-4 border-l-emerald-500" : "";
    const blockedClass = !i.is_ready && (i.blocked_by || []).length ? "border-l-4 border-l-red-500" : "";
    const borderClass = readyClass || blockedClass;
    const isSelected = state.selectedCards.has(i.id);

    return (
      `<tr class="cursor-pointer bg-overlay-hover ${borderClass}" onclick="openDetail('${escJsSingle(i.id)}')" style="border-bottom:1px solid var(--border-default)">` +
      `<td class="py-2 px-2 text-xs" style="color:${PRIORITY_COLORS[i.priority] || '#6B7280'}">${i.priority}</td>` +
      `<td class="py-2 px-2 text-xs" style="color:var(--text-secondary)">${icon} ${escHtml(i.type || "")}</td>` +
      `<td class="py-2 px-2"><span class="text-xs px-1.5 py-0.5 rounded" style="background:${catColor};color:#fff">${escHtml(i.status || "")}</span></td>` +
      `<td class="py-2 px-2 text-xs truncate" style="max-width:300px;color:var(--text-primary)" title="${escHtml(i.title)}">${escHtml(i.title)}</td>` +
      `<td class="py-2 px-2 text-xs" style="color:var(--text-secondary)">${escHtml(i.assignee || "—")}</td>` +
      `<td class="py-2 px-2 text-xs" style="color:var(--text-muted)">${escHtml(updated)}</td>` +
      `<td class="py-2 px-2 text-xs text-right" style="color:var(--text-muted)">${blocks || ""}</td>` +
      `<td class="py-2 px-1 text-center" onclick="event.stopPropagation();toggleCardSelect(event,'${escJsSingle(i.id)}')">` +
      `<input type="checkbox" ${isSelected ? "checked" : ""} style="accent-color:var(--accent)" class="cursor-pointer"></td>` +
      "</tr>"
    );
  }).join("");

  return (
    '<div class="overflow-x-auto h-full"><table class="w-full text-xs" style="border-collapse:collapse">' +
    "<thead><tr>" +
    headerCell("priority", "P") +
    headerCell("type", "Type") +
    headerCell("status", "Status") +
    headerCell("title", "Title") +
    headerCell("assignee", "Assignee") +
    headerCell("updated", "Updated") +
    headerCell("blocks", "⚡") +
    '<th class="py-2 px-1" style="color:var(--text-muted)">☐</th>' +
    "</tr></thead><tbody>" +
    rows +
    "</tbody></table></div>"
  );
}
```

Add relative time helper and sort handler:

```javascript
function _relativeTime(isoStr) {
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}
```

**Step 5: Add sort handler to `app.js`**

```javascript
window.sortListMode = function(col) {
  if (state._listSortCol === col) {
    state._listSortDir = state._listSortDir === "asc" ? "desc" : "asc";
  } else {
    state._listSortCol = col;
    state._listSortDir = "asc";
  }
  renderKanban();
};
```

Add `_listSortCol` and `_listSortDir` to `state.js`:

```javascript
_listSortCol: "priority",
_listSortDir: "asc",
```

**Step 6: Verify in browser**

- Kanban toolbar shows Board / Cluster / List buttons
- List mode renders a sortable table
- Column header clicks sort (toggle asc/desc)
- Row click opens detail panel
- Checkbox column works for batch selection
- Ready issues have green left border, blocked have red
- Hash shows `#kanban-list`
- Switching modes preserves filter state

**Step 7: Commit**

```bash
git add src/filigree/static/js/views/kanban.js src/filigree/static/js/router.js src/filigree/static/dashboard.html src/filigree/static/js/app.js src/filigree/static/js/state.js
git commit -m "feat: add Kanban List mode — sortable table view for large projects"
```

---

## Task 9: Update TOUR_STEPS + final cleanup

Update the guided tour for the 5-tab layout. Clean up any remaining references to removed tabs.

**Files:**
- Modify: `src/filigree/static/js/state.js:52-83` (TOUR_STEPS)
- Modify: `src/filigree/static/js/app.js` (verify no dead imports/exports remain)

**Step 1: Update TOUR_STEPS**

Replace state.js:52-83:

```javascript
export const TOUR_STEPS = [
  {
    el: "#btnKanban",
    text: "The dashboard has 5 views: Kanban (default), Graph, Releases, Insights, and Files. Each shows your project differently.",
    pos: "bottom",
  },
  {
    el: "#btnReady",
    text: "Ready issues have no blockers and can be worked on immediately. Toggle this to sort them first.",
    pos: "bottom",
  },
  {
    el: "#filterSearch",
    text: 'Search issues by title or ID. Press "/" anywhere to focus this field instantly.',
    pos: "bottom",
  },
  {
    el: "#healthBadge",
    text: "Health score (0\u201399) measures project flow. Click it for a detailed breakdown of what affects the score.",
    pos: "bottom",
  },
  {
    el: "#kanbanBoard",
    text: "Click any card to open its detail panel. Use j/k to navigate between cards. Switch between Board, Cluster, and List modes.",
    pos: "top",
  },
  {
    el: null,
    text: 'Press "?" anytime to see all keyboard shortcuts. Happy tracking!',
    pos: "center",
  },
];
```

**Step 2: Audit `app.js` for dead references**

Search for any remaining imports/exports that reference removed tabs:
- `loadActivity` — should only be used internally by `renderActivitySection` now
- `loadHealth` — should be removed
- `loadWorkflow` — should be removed (keep `loadPlanView`)
- `filterFilesByScanSource` — should be removed
- `showHealthHelp`, `showReadyHelp`, `showBlockedHelp` — should be removed

Remove any remaining dead `window.*` exports.

**Step 3: Audit `dashboard.html` for dead references**

Search for any remaining references to `activityView`, `healthView`, `workflowView`, `btnActivity`, `btnHealth`, `btnWorkflow`, `filterUpdatedDays`, `btnMultiSelect`, `filterOpen`, `filterInProgress`, `filterClosed`. All should be gone.

**Step 4: Verify in browser — full regression check**

- [ ] Tab bar shows exactly 5 tabs: Kanban, Graph, Releases, Insights, Files
- [ ] Kanban: Board, Cluster, List modes all work
- [ ] Graph: renders, critical path works, health badge updates
- [ ] Releases: loads release trees
- [ ] Insights: shows metrics + collapsible Activity
- [ ] Files: shows collapsible Code Quality Overview + file table
- [ ] Settings gear → Workflow diagram opens modal
- [ ] Filter bar: Ready, Blocked, Search, status pills, Filters disclosure
- [ ] Done pill: off by default, click → on with "Done: 7d" + dropdown
- [ ] Hash navigation: `#health` → Files, `#activity` → Insights, `#workflow` → Kanban
- [ ] Guided tour works with 5-tab text
- [ ] No console errors

**Step 5: Commit**

```bash
git add src/filigree/static/js/state.js src/filigree/static/js/app.js
git commit -m "chore: update TOUR_STEPS for 5-tab layout and clean up dead references"
```

---

## Task 10: Tab order in HTML

Ensure the 5 tab buttons appear in the designed order in `dashboard.html`. The design specifies:

```
[Kanban] [Graph] [Releases] [Insights] [Files]
```

Currently Graph comes before Kanban. Reorder the button elements so Kanban is first (it's the default view and most-used tab).

**Step 1: Reorder buttons in `dashboard.html`**

Find the tab buttons `div` and reorder:

```html
<div class="flex gap-1">
  <button id="btnKanban" onclick="switchView('kanban')" class="px-3 py-1 rounded text-xs font-medium" title="Kanban board view">Kanban</button>
  <button id="btnGraph" onclick="switchView('graph')" class="px-3 py-1 rounded text-xs font-medium" title="Dependency graph visualization">Graph</button>
  <button id="btnReleases" onclick="switchView('releases')" class="px-3 py-1 rounded text-xs font-medium" title="Release roadmap and progress">Releases</button>
  <button id="btnInsights" onclick="switchView('insights')" class="px-3 py-1 rounded text-xs font-medium" title="Flow metrics — throughput, cycle time, lead time">Insights</button>
  <button id="btnFiles" onclick="switchView('files')" class="px-3 py-1 rounded text-xs font-medium" title="File records, scan findings, and code health">Files</button>
</div>
```

**Step 2: Verify tab order in browser**

Tabs appear in order: Kanban, Graph, Releases, Insights, Files.

**Step 3: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "chore: reorder tab buttons to match 5-tab design"
```

---

## Summary

| Task | Description | Dependencies |
|------|-------------|-------------|
| 1 | Extract `analytics.js` from `graph.js` | None |
| 2 | Data-driven `switchView()` + hash aliases | None |
| 3 | Rename kanban mode `standard` → `board` | Task 2 |
| 4 | Merge Activity → Insights | Task 2 |
| 5 | Merge Health → Files (collapsible overview) | Tasks 1, 2 |
| 6 | Demote Workflow to modal | Task 2 |
| 7 | Header filter bar redesign | Tasks 4-6 |
| 8 | Kanban List mode | Task 3 |
| 9 | TOUR_STEPS + cleanup | Tasks 4-8 |
| 10 | Tab order in HTML | Tasks 4-6 |

Tasks 1 and 2 can be done in parallel. Tasks 3-6 can be done in parallel after Task 2 completes. Tasks 7-10 depend on the tab removal tasks completing first.
