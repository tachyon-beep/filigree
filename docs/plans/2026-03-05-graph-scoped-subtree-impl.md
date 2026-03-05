# Graph Scoped Subtree Explorer — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the graph tab's "render everything" approach with a sidebar-driven scoped subtree explorer per the design doc at `docs/plans/2026-03-05-graph-scoped-subtree-explorer.md`.

**Architecture:** Left sidebar in `dashboard.html` holds type-filtered root issue list with three visual states (unselected/explicit/clicked-in). New `graphSidebar.js` module owns sidebar state, tree walking, and ghost resolution. Existing `graph.js` is refactored: `renderGraph()` receives pre-filtered node/edge arrays from the sidebar module instead of building them internally. Cytoscape rendering, styles, events, overlays (critical path, path trace, search) are preserved.

**Tech Stack:** Vanilla JS (ES modules), Tailwind CSS, Cytoscape.js, Python/FastAPI (tests only — no backend changes needed).

---

## Task 1: Add Graph Sidebar State to state.js

**Files:**
- Modify: `src/filigree/static/js/state.js:106-124`

**Step 1: Add new state variables after the existing graph state block**

In `state.js`, add these after line 124 (`graphPathEdges: new Set(),`):

```javascript
  // Graph sidebar (scoped subtree explorer)
  graphSidebarSelections: new Map(),   // Map<issueId, {state, causedBy}>
  graphSidebarTypeFilter: new Set(),   // active type filters (empty = all)
  graphSidebarScrollTop: 0,            // preserve scroll position
```

**Step 2: Run tests to verify no regressions**

Run: `uv run pytest tests/api/test_graph_api.py -v --tb=short`
Expected: All existing tests pass (state additions are inert).

**Step 3: Commit**

```bash
git add src/filigree/static/js/state.js
git commit -m "feat(graph): add sidebar selection state variables"
```

---

## Task 2: Add Sidebar HTML Structure to dashboard.html

**Files:**
- Modify: `src/filigree/static/dashboard.html:227-338` (graphView section)

**Step 1: Restructure graphView to include sidebar**

Replace the opening of the graphView div (line 227) through the graph toolbar closing div. The graph view needs to become a two-panel layout: sidebar + canvas area.

Change `<div id="graphView" class="flex-1 hidden flex flex-col">` and wrap the toolbar + canvas in a new flex layout:

```html
<div id="graphView" class="flex-1 hidden flex flex-col">
  <div class="flex flex-1 overflow-hidden">
    <!-- Graph Sidebar -->
    <div id="graphSidebar" class="flex flex-col border-r border-default bg-base" style="width:280px;min-width:200px;max-width:360px;">
      <div class="flex items-center gap-1 px-3 py-1.5 border-b border-default bg-raised text-xs">
        <span class="font-medium text-primary">Explorer</span>
        <span id="graphSidebarHelp" class="text-muted cursor-help ml-1" title="Blue = explicitly selected. Amber = pulled in via dependency. Click amber to pin.">?</span>
        <span class="flex-1"></span>
        <button onclick="graphSidebarSelectAll()" class="px-1.5 py-0.5 rounded bg-overlay bg-overlay-hover text-secondary" title="Select all visible items">All</button>
        <button onclick="graphSidebarClearAll()" class="px-1.5 py-0.5 rounded bg-overlay bg-overlay-hover text-secondary" title="Clear all selections">Clear</button>
        <span id="graphSidebarHiddenBadge" class="hidden text-amber-400 text-[10px]"></span>
      </div>
      <div class="flex flex-wrap gap-1 px-3 py-1.5 border-b border-default bg-raised text-xs" id="graphSidebarTypeFilter">
        <!-- Type filter pills rendered by JS -->
      </div>
      <div id="graphSidebarList" class="flex-1 overflow-y-auto scrollbar-thin px-2 py-1 text-xs" role="listbox" aria-label="Issue tree selector">
        <!-- Sidebar items rendered by JS -->
      </div>
      <div id="graphSidebarStatus" class="px-3 py-1 border-t border-default text-[10px] text-muted" aria-live="polite"></div>
    </div>
    <!-- Graph Canvas Area -->
    <div class="flex flex-1 flex-col min-w-0">
```

Then close the new canvas wrapper div after the existing `</div>` that closes the `relative flex-1` container holding `#cy` and the legend/diagnostics bar. This means adding `</div>` (close canvas area) and `</div>` (close flex row) before the final `</div>` (close graphView).

**Step 2: Simplify the graph toolbar**

Per the design, remove these controls from the toolbar (lines 240-333):
- The entire `graphFiltersGroup` details (epics only, ready only, blocked only, assignee)
- The entire `graphAdvancedGroup` details (focus mode, root, radius, path trace, node/edge caps, search nav)

Keep:
- Preset selector (Execution/Roadmap)
- Fit button
- Critical Path button
- Legend button
- The `graphNotice` div
- The `graphFilterState` div

Add a node/edge count display to the toolbar:
```html
<span id="graphNodeEdgeCount" class="text-[11px] text-muted"></span>
```

Note: the `graphNodeEdgeCount` element already exists in the diagnostics bar. We're keeping it where it is.

**Step 3: Run tests to see which contract tests break**

Run: `uv run pytest tests/api/test_graph_api.py -v --tb=short`
Expected: Several contract tests will FAIL because they assert presence of removed HTML elements. Note which ones fail — we'll update them in Task 7.

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(graph): add sidebar HTML and simplify toolbar"
```

---

## Task 3: Create graphSidebar.js — Tree Index and Sidebar Rendering

**Files:**
- Create: `src/filigree/static/js/views/graphSidebar.js`

**Step 1: Write the module skeleton with tree indexing**

```javascript
// ---------------------------------------------------------------------------
// Graph Sidebar — scoped subtree explorer for the graph tab.
// ---------------------------------------------------------------------------

import { state, TYPE_COLORS } from "../state.js";

// Type display order for sidebar groups
const TYPE_ORDER = ["milestone", "epic", "release", "feature", "task", "bug"];

// --- Tree index (rebuilt when allIssues changes) ---

let rootIssues = [];       // issues with parent_id === null
let childIndex = {};       // parentId -> [childIssue, ...]
let ancestorIndex = {};    // issueId -> top-level ancestor issueId
let subtreeIndex = {};     // rootId -> Set<issueId> (all descendants including root)
let crossTreeDeps = {};    // rootId -> count of cross-tree dependency edges

export function rebuildTreeIndex() {
  rootIssues = [];
  childIndex = {};
  ancestorIndex = {};
  subtreeIndex = {};
  crossTreeDeps = {};

  for (const issue of state.allIssues) {
    if (!issue.parent_id) {
      rootIssues.push(issue);
    }
    if (issue.parent_id) {
      if (!childIndex[issue.parent_id]) childIndex[issue.parent_id] = [];
      childIndex[issue.parent_id].push(issue);
    }
  }

  // Build subtree and ancestor indices
  for (const root of rootIssues) {
    const subtree = new Set();
    const stack = [root.id];
    const visited = new Set();
    while (stack.length) {
      const id = stack.pop();
      if (visited.has(id)) continue;
      visited.add(id);
      subtree.add(id);
      ancestorIndex[id] = root.id;
      for (const child of (childIndex[id] || [])) {
        stack.push(child.id);
      }
    }
    subtreeIndex[root.id] = subtree;
  }

  // Count cross-tree deps per root
  for (const root of rootIssues) {
    let count = 0;
    const subtree = subtreeIndex[root.id];
    for (const id of subtree) {
      const issue = state.issueMap[id];
      if (!issue) continue;
      for (const depId of (issue.blocks || [])) {
        if (!subtree.has(depId)) count++;
      }
      for (const depId of (issue.blocked_by || [])) {
        if (!subtree.has(depId)) count++;
      }
    }
    crossTreeDeps[root.id] = count;
  }
}

export function getAncestorId(issueId) {
  return ancestorIndex[issueId] || issueId;
}

export function getSubtreeIds(rootId) {
  return subtreeIndex[rootId] || new Set([rootId]);
}

// --- Type filter ---

export function renderTypeFilter() {
  const container = document.getElementById("graphSidebarTypeFilter");
  if (!container) return;

  const typesPresent = new Set(rootIssues.map((i) => i.type));
  const ordered = TYPE_ORDER.filter((t) => typesPresent.has(t));
  for (const t of typesPresent) {
    if (!ordered.includes(t)) ordered.push(t);
  }

  container.innerHTML = ordered
    .map((t) => {
      const active = state.graphSidebarTypeFilter.size === 0 || state.graphSidebarTypeFilter.has(t);
      const color = TYPE_COLORS[t] || "#6B7280";
      return `<button onclick="toggleGraphSidebarType('${t}')"
        class="px-1.5 py-0.5 rounded text-[10px] font-medium ${active ? "text-primary" : "text-muted opacity-50"}"
        style="border:1px solid ${active ? color : "var(--border-default)"};${active ? `background:${color}22` : ""}"
        aria-pressed="${active}">${t}</button>`;
    })
    .join("");
}

export function toggleGraphSidebarType(type) {
  if (state.graphSidebarTypeFilter.has(type)) {
    state.graphSidebarTypeFilter.delete(type);
  } else {
    state.graphSidebarTypeFilter.add(type);
  }
  // If all types are selected, clear the filter (= show all)
  const allTypes = new Set(rootIssues.map((i) => i.type));
  if (allTypes.size === state.graphSidebarTypeFilter.size) {
    state.graphSidebarTypeFilter.clear();
  }
  renderGraphSidebar();
}

// --- Sidebar rendering ---

function getVisibleRootIssues() {
  let items = rootIssues;
  if (state.graphSidebarTypeFilter.size > 0) {
    items = items.filter((i) => state.graphSidebarTypeFilter.has(i.type));
  }
  return items;
}

function groupByType(issues) {
  const groups = {};
  for (const issue of issues) {
    const type = issue.type || "other";
    if (!groups[type]) groups[type] = [];
    groups[type].push(issue);
  }
  // Sort within groups by priority then title
  for (const g of Object.values(groups)) {
    g.sort((a, b) => a.priority - b.priority || a.title.localeCompare(b.title));
  }
  // Order groups by TYPE_ORDER
  const ordered = [];
  for (const t of TYPE_ORDER) {
    if (groups[t]) ordered.push([t, groups[t]]);
  }
  for (const [t, items] of Object.entries(groups)) {
    if (!TYPE_ORDER.includes(t)) ordered.push([t, items]);
  }
  return ordered;
}

export function renderGraphSidebar() {
  const list = document.getElementById("graphSidebarList");
  if (!list) return;

  const scrollTop = list.scrollTop;
  const visible = getVisibleRootIssues();
  const groups = groupByType(visible);

  // Update hidden selections badge
  const hiddenBadge = document.getElementById("graphSidebarHiddenBadge");
  if (hiddenBadge) {
    let hiddenCount = 0;
    for (const [id] of state.graphSidebarSelections) {
      const issue = state.issueMap[id];
      if (issue && state.graphSidebarTypeFilter.size > 0 && !state.graphSidebarTypeFilter.has(issue.type)) {
        hiddenCount++;
      }
    }
    if (hiddenCount > 0) {
      hiddenBadge.textContent = `${hiddenCount} hidden`;
      hiddenBadge.classList.remove("hidden");
    } else {
      hiddenBadge.classList.add("hidden");
    }
  }

  if (rootIssues.length === 0) {
    list.innerHTML = '<div class="px-2 py-4 text-muted text-center">No top-level issues found. Issues must have no parent to appear here.</div>';
    renderTypeFilter();
    return;
  }

  if (visible.length === 0) {
    list.innerHTML = '<div class="px-2 py-4 text-muted text-center">No issues match the current type filter.</div>';
    renderTypeFilter();
    return;
  }

  const html = [];
  for (const [type, items] of groups) {
    const color = TYPE_COLORS[type] || "#6B7280";
    html.push(`<div class="mt-2 mb-1 flex items-center gap-1">
      <span class="inline-block w-2 h-2 rounded-full" style="background:${color}"></span>
      <span class="font-medium text-secondary uppercase tracking-wider text-[10px]">${type}s (${items.length})</span>
    </div>`);
    for (const issue of items) {
      const sel = state.graphSidebarSelections.get(issue.id);
      const selState = sel ? sel.state : null;
      const crossDeps = crossTreeDeps[issue.id] || 0;

      let bgClass = "bg-transparent hover:bg-overlay";
      let textClass = "text-secondary";
      let indicator = "";
      if (selState === "explicit") {
        bgClass = "bg-accent/20 hover:bg-accent/30";
        textClass = "text-primary";
      } else if (selState === "clicked-in") {
        bgClass = "bg-amber-500/15 hover:bg-amber-500/25";
        textClass = "text-primary";
        indicator = '<span class="text-amber-400 text-[10px] ml-auto shrink-0" title="Explored via dependency">dep</span>';
      }

      const depBadge = crossDeps > 0
        ? `<span class="text-muted text-[10px]" title="${crossDeps} cross-tree dependencies">${crossDeps}x</span>`
        : "";

      const title = issue.title.length > 40 ? issue.title.slice(0, 38) + ".." : issue.title;

      html.push(`<div role="option" aria-selected="${!!selState}"
        class="flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer ${bgClass} ${textClass}"
        onclick="toggleGraphSidebarItem('${issue.id}')"
        tabindex="0"
        onkeydown="if(event.key===' '||event.key==='Enter'){event.preventDefault();toggleGraphSidebarItem('${issue.id}')}"
        title="${issue.title}">
        <span class="truncate">${title}</span>
        ${depBadge}
        ${indicator}
      </div>`);
    }
  }

  list.innerHTML = html.join("");
  renderTypeFilter();
  list.scrollTop = scrollTop;
}
```

**Step 2: Run linter**

Run: `uv run ruff check src/filigree/static/js/views/graphSidebar.js` — this is a JS file so ruff won't check it, but verify no syntax issues by loading the page.

**Step 3: Commit**

```bash
git add src/filigree/static/js/views/graphSidebar.js
git commit -m "feat(graph): add graphSidebar.js — tree index and sidebar rendering"
```

---

## Task 4: Add Selection Logic and Ghost Resolution to graphSidebar.js

**Files:**
- Modify: `src/filigree/static/js/views/graphSidebar.js`

**Step 1: Add selection toggle, select all, clear all**

Append to graphSidebar.js:

```javascript
// --- Selection logic ---

const SOFT_NODE_CAP = 300;

export function toggleGraphSidebarItem(issueId) {
  const existing = state.graphSidebarSelections.get(issueId);
  if (!existing) {
    // Unselected -> explicit
    state.graphSidebarSelections.set(issueId, { state: "explicit", causedBy: new Set() });
  } else if (existing.state === "clicked-in") {
    // Clicked-in -> promote to explicit
    state.graphSidebarSelections.set(issueId, { state: "explicit", causedBy: new Set() });
  } else {
    // Explicit -> unselected (with cascade)
    deselectWithCascade(issueId);
  }
  renderGraphSidebar();
  if (callbacks.renderGraph) callbacks.renderGraph();
}

function deselectWithCascade(issueId) {
  state.graphSidebarSelections.delete(issueId);
  // Remove clicked-in items whose causedBy set becomes empty
  for (const [id, sel] of state.graphSidebarSelections) {
    if (sel.state === "clicked-in") {
      sel.causedBy.delete(issueId);
      if (sel.causedBy.size === 0) {
        state.graphSidebarSelections.delete(id);
      }
    }
  }
}

export function graphSidebarSelectAll() {
  const visible = getVisibleRootIssues();
  for (const issue of visible) {
    if (!state.graphSidebarSelections.has(issue.id)) {
      state.graphSidebarSelections.set(issue.id, { state: "explicit", causedBy: new Set() });
    }
  }
  renderGraphSidebar();
  if (callbacks.renderGraph) callbacks.renderGraph();
}

export function graphSidebarClearAll() {
  state.graphSidebarSelections.clear();
  renderGraphSidebar();
  if (callbacks.renderGraph) callbacks.renderGraph();
}

// --- Ghost click handler (called from graph.js when a ghost node is tapped) ---

export function handleGhostClick(nodeId) {
  const ancestorId = getAncestorId(nodeId);
  if (!ancestorId || state.graphSidebarSelections.has(ancestorId)) return;

  // Find which explicit selections reference this ghost via cross-tree deps
  const causedBy = new Set();
  for (const [selId, sel] of state.graphSidebarSelections) {
    if (sel.state !== "explicit") continue;
    const subtree = getSubtreeIds(selId);
    for (const id of subtree) {
      const issue = state.issueMap[id];
      if (!issue) continue;
      const deps = [...(issue.blocks || []), ...(issue.blocked_by || [])];
      const targetSubtree = getSubtreeIds(ancestorId);
      if (deps.some((d) => targetSubtree.has(d))) {
        causedBy.add(selId);
        break;
      }
    }
  }

  state.graphSidebarSelections.set(ancestorId, { state: "clicked-in", causedBy });
  const statusEl = document.getElementById("graphSidebarStatus");
  const issue = state.issueMap[ancestorId];
  if (statusEl && issue) {
    statusEl.textContent = `Added: ${issue.title.slice(0, 40)}`;
  }
  renderGraphSidebar();
  if (callbacks.renderGraph) callbacks.renderGraph();
}
```

**Step 2: Add the graph node/edge resolution function**

This is the core function that `renderGraph` will call to get the filtered nodes and edges:

```javascript
// --- Resolve visible nodes and edges for Cytoscape ---

export function resolveGraphScope() {
  if (state.graphSidebarSelections.size === 0) {
    return { nodes: [], edges: [], ghostIds: new Set() };
  }

  // Collect all selected subtree node IDs
  const selectedIds = new Set();
  for (const [rootId, sel] of state.graphSidebarSelections) {
    const subtree = getSubtreeIds(rootId);
    for (const id of subtree) selectedIds.add(id);
  }

  // Identify ghost nodes: nodes referenced by deps from selected set but not in selected set
  const ghostIds = new Set();
  for (const id of selectedIds) {
    const issue = state.issueMap[id];
    if (!issue) continue;
    for (const depId of [...(issue.blocks || []), ...(issue.blocked_by || [])]) {
      if (!selectedIds.has(depId) && state.issueMap[depId]) {
        ghostIds.add(depId);
      }
    }
  }

  // Build node list (selected + ghosts)
  const allVisibleIds = new Set([...selectedIds, ...ghostIds]);
  const nodes = [];
  for (const id of allVisibleIds) {
    const issue = state.issueMap[id];
    if (!issue) continue;
    nodes.push(issue);
  }

  // Build edge list from deps where both ends are visible
  const edges = [];
  const edgeSeen = new Set();
  for (const id of allVisibleIds) {
    const issue = state.issueMap[id];
    if (!issue) continue;
    for (const blockedId of (issue.blocks || [])) {
      if (allVisibleIds.has(blockedId)) {
        const key = `${id}->${blockedId}`;
        if (!edgeSeen.has(key)) {
          edgeSeen.add(key);
          edges.push({ source: id, target: blockedId });
        }
      }
    }
  }

  return { nodes, edges, ghostIds };
}

// --- Soft node cap check ---

export function checkNodeCap(additionalRootId) {
  // Estimate total nodes if we add this root's subtree
  let total = 0;
  for (const [rootId] of state.graphSidebarSelections) {
    total += (getSubtreeIds(rootId)).size;
  }
  if (additionalRootId) {
    total += (getSubtreeIds(additionalRootId)).size;
  }
  return { total, exceedsCap: total > SOFT_NODE_CAP };
}

// --- Callbacks ---

export const callbacks = { renderGraph: null };
```

**Step 3: Commit**

```bash
git add src/filigree/static/js/views/graphSidebar.js
git commit -m "feat(graph): add selection logic, ghost resolution, and node cap"
```

---

## Task 5: Refactor graph.js to Use Sidebar Scope

**Files:**
- Modify: `src/filigree/static/js/views/graph.js`

This is the largest task. The key change: `renderGraph()` no longer builds its own node/edge lists from filters. Instead it calls `resolveGraphScope()` from graphSidebar.js and uses those.

**Step 1: Add import and update renderGraph**

At the top of `graph.js`, add import:

```javascript
import { resolveGraphScope, handleGhostClick, rebuildTreeIndex, renderGraphSidebar, callbacks as sidebarCallbacks } from "./graphSidebar.js";
```

**Step 2: Refactor renderGraph to use resolveGraphScope**

Replace the two rendering paths (v2 at lines 787-812 and legacy at lines 813-908) with a single path that uses the sidebar's resolved scope:

```javascript
  // --- Scoped subtree rendering (replaces v2 and legacy paths) ---
  const { nodes: scopeNodes, edges: scopeEdges, ghostIds } = resolveGraphScope();

  if (scopeNodes.length === 0 && state.graphSidebarSelections.size === 0) {
    // Blank state — no selections
    if (state.cy) { state.cy.destroy(); state.cy = null; }
    container.innerHTML = '<div class="flex items-center justify-center h-full text-secondary text-sm">Select items from the sidebar to explore their dependency graph.</div>';
    updateGraphClearButtons();
    return;
  }

  // Restore container if it had the blank prompt
  if (container.firstElementChild?.tagName === "DIV") {
    container.innerHTML = "";
  }

  // Apply status pill filters
  const showOpen = state.statusPills.open;
  const showActive = state.statusPills.active;
  const showClosed = state.statusPills.done;

  const filteredNodes = scopeNodes.filter((n) => {
    const cat = n.status_category || "open";
    if (cat === "open" && !showOpen) return false;
    if (cat === "wip" && !showActive) return false;
    if (cat === "done" && !showClosed) return false;
    return true;
  });

  const filteredIds = new Set(filteredNodes.map((n) => n.id));
  const search = document.getElementById("filterSearch")?.value?.toLowerCase().trim() || "";

  cyNodes = filteredNodes.map((n) => {
    const title = n.title || n.id;
    const isGhost = ghostIds.has(n.id);
    const matchesSearch = !search || title.toLowerCase().includes(search) || n.id.toLowerCase().includes(search);
    return {
      data: {
        id: n.id,
        label: title.length > 30 ? `${title.slice(0, 28)}..` : title,
        status: n.status,
        statusCategory: n.status_category || "open",
        priority: n.priority,
        type: n.type,
        isReady: !!n.is_ready,
        childCount: (n.children || []).length,
        isGhost: isGhost,
        opacity: isGhost ? 0.45 : (matchesSearch ? 1 : 0.2),
      },
    };
  });

  cyEdges = scopeEdges
    .filter((e) => filteredIds.has(e.source) && filteredIds.has(e.target))
    .map((e, i) => ({
      data: { id: `e-${e.source}-${e.target}`, source: e.source, target: e.target },
    }));
```

**Step 3: Add ghost node styles to graphStyles()**

Add a new selector for ghost nodes after the existing `node:selected` style:

```javascript
    {
      selector: "node[?isGhost]",
      style: {
        "border-width": 2,
        "border-style": "dashed",
        "border-color": "#8FAAB8",
        "background-opacity": 0.3,
        "cursor": "pointer",
      },
    },
    {
      selector: "node[?isGhost]:active",
      style: {
        "border-color": THEME_COLORS.accent,
        "background-opacity": 0.5,
      },
    },
```

**Step 4: Update bindGraphEvents to handle ghost node clicks**

In `bindGraphEvents()`, modify the existing tap handler:

```javascript
  state.cy.on("tap", "node", (evt) => {
    const nodeId = evt.target.id();
    if (evt.target.data("isGhost")) {
      handleGhostClick(nodeId);
      return;
    }
    if (callbacks.openDetail) callbacks.openDetail(nodeId);
  });
```

**Step 5: Remove the old v2/legacy code paths and obsolete helper functions**

Remove:
- `buildGraphQuery()` function (~lines 119-160)
- `shouldUseGraphV2()` references in renderGraph
- `refreshGraphData()` function (~lines 510-585) — no longer needed for the sidebar-driven approach
- The v2 rendering path (lines 787-812)
- The legacy rendering path (lines 813-908)
- Focus mode functions: `onGraphFocusModeChange`, `onGraphFocusRootInput`, `clearGraphFocus`
- Epics-only handler: `onGraphEpicsOnlyChange`
- Assignee handler: `onGraphAssigneeInput`
- Path tracing: `traceGraphPath`, `clearGraphPath`, `onGraphPathInput`, `applyPathTraceStyles`
- Graph search: `graphSearchNext`, `graphSearchPrev`, `applySearchFocus`
- Node/edge limit-related code in renderGraph

Keep:
- `renderGraph()` (refactored)
- `graphStyles()` (extended with ghost styles)
- `bindGraphEvents()` (modified)
- `graphFit()`
- `toggleCriticalPath()` and `applyCriticalPathStyles()`
- `showHealthBreakdown()`
- Cytoscape create/update/position-reuse logic (lines 910-1003)
- `computeGraphMinZoom`, `enforceReadableZoomBounds`, `fitGraphWithCaps`
- `setGraphNotice`, `updateGraphPerfState`, `updateGraphClearButtons`
- Time window persistence functions

**Step 6: Wire sidebar callback in renderGraph**

At the top of `renderGraph()`, ensure the sidebar callback is wired:

```javascript
  sidebarCallbacks.renderGraph = renderGraph;
```

**Step 7: Commit**

```bash
git add src/filigree/static/js/views/graph.js
git commit -m "refactor(graph): use sidebar scope instead of v2/legacy rendering paths"
```

---

## Task 6: Wire Everything in app.js

**Files:**
- Modify: `src/filigree/static/js/app.js`

**Step 1: Import new sidebar functions**

Add imports from graphSidebar.js alongside the existing graph.js imports:

```javascript
import {
  renderGraphSidebar,
  toggleGraphSidebarItem,
  toggleGraphSidebarType,
  graphSidebarSelectAll,
  graphSidebarClearAll,
  rebuildTreeIndex,
  callbacks as sidebarCallbacks,
} from "./views/graphSidebar.js";
```

**Step 2: Call rebuildTreeIndex after data fetch**

In the `fetchData()` function (around line 156), after `state.allIssues` is updated, add:

```javascript
rebuildTreeIndex();
```

**Step 3: Wire sidebar callback**

After the existing `graphCallbacks.openDetail = openDetail;` line:

```javascript
sidebarCallbacks.renderGraph = renderGraph;
```

**Step 4: Update registerView for graph**

The existing `registerView("graph", renderGraph)` should also render the sidebar. Change to:

```javascript
registerView("graph", () => { renderGraphSidebar(); renderGraph(); });
```

**Step 5: Expose new functions on window**

Replace the removed graph window exposures with the new sidebar ones:

```javascript
// Graph sidebar
window.toggleGraphSidebarItem = toggleGraphSidebarItem;
window.toggleGraphSidebarType = toggleGraphSidebarType;
window.graphSidebarSelectAll = graphSidebarSelectAll;
window.graphSidebarClearAll = graphSidebarClearAll;
```

Remove the window exposures for deleted functions:
- `window.clearGraphFocus`
- `window.onGraphAssigneeInput`
- `window.onGraphFocusModeChange`
- `window.onGraphFocusRootInput`
- `window.onGraphEpicsOnlyChange`
- `window.onGraphPathInput`
- `window.graphSearchNext`
- `window.graphSearchPrev`
- `window.traceGraphPath`
- `window.clearGraphPath`

**Step 6: Commit**

```bash
git add src/filigree/static/js/app.js
git commit -m "feat(graph): wire sidebar into app initialization and routing"
```

---

## Task 7: Update Tests

**Files:**
- Modify: `tests/api/test_graph_api.py`

**Step 1: Remove or update broken contract tests**

Tests that assert removed HTML elements or JS functions need updating. Remove tests for:
- `test_graph_query_builder_includes_v2_filters` — v2 query builder removed
- `test_focus_controls_coupled_and_tap_no_longer_mutates_root` — focus mode removed
- `test_graph_inputs_use_debounced_render` — removed inputs
- `test_trace_button_disabled_until_both_path_inputs_present` — path trace removed
- `test_preset_and_epics_toggle_stay_in_sync` — epics-only removed
- `test_graph_toolbar_progressive_disclosure_groups_present` — filters/advanced groups removed
- `test_graph_caps_are_within_advanced_disclosure_group` — advanced group removed
- `test_graph_clear_buttons_disable_when_inactive` — focus/path buttons removed
- `test_hover_traversal_uses_outgoers_not_full_edge_scan` — keep if hover logic preserved
- `test_path_tracing_uses_outgoers_not_full_edge_scan` — path tracing removed
- `test_search_nav_buttons_have_disabled_state_logic` — search nav removed
- `test_graph_search_idle_state_uses_plain_language` — search state removed

**Step 2: Add new contract tests for the sidebar**

```python
class TestGraphSidebarContracts:
    def test_graph_sidebar_html_structure(self) -> None:
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert 'id="graphSidebar"' in html
        assert 'id="graphSidebarList"' in html
        assert 'id="graphSidebarTypeFilter"' in html
        assert 'id="graphSidebarStatus"' in html
        assert 'role="listbox"' in html
        assert 'aria-live="polite"' in html

    def test_graph_sidebar_module_exists(self) -> None:
        sidebar_js = (STATIC_DIR / "js" / "views" / "graphSidebar.js").read_text()
        assert "export function rebuildTreeIndex()" in sidebar_js
        assert "export function renderGraphSidebar()" in sidebar_js
        assert "export function resolveGraphScope()" in sidebar_js
        assert "export function toggleGraphSidebarItem(" in sidebar_js
        assert "export function handleGhostClick(" in sidebar_js

    def test_graph_ghost_node_style_defined(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        assert "isGhost" in graph_js
        assert "border-style" in graph_js or "dashed" in graph_js

    def test_graph_sidebar_wired_in_app(self) -> None:
        app_js = (STATIC_DIR / "js" / "app.js").read_text()
        assert "rebuildTreeIndex" in app_js
        assert "renderGraphSidebar" in app_js
        assert "toggleGraphSidebarItem" in app_js
        assert "graphSidebarSelectAll" in app_js
        assert "graphSidebarClearAll" in app_js

    def test_graph_sidebar_state_model(self) -> None:
        state_js = (STATIC_DIR / "js" / "state.js").read_text()
        assert "graphSidebarSelections" in state_js
        assert "graphSidebarTypeFilter" in state_js
```

**Step 3: Keep existing API tests that still apply**

The `TestGraphAPI` and `TestGraphAdvancedAPI` classes test the server-side `/api/graph` endpoint which still exists. Keep these — the server endpoint is preserved even though the frontend no longer uses it as the primary path.

**Step 4: Run the full test suite**

Run: `uv run pytest tests/api/test_graph_api.py -v --tb=short`
Expected: All tests pass.

**Step 5: Commit**

```bash
git add tests/api/test_graph_api.py
git commit -m "test(graph): update contract tests for sidebar-driven graph"
```

---

## Task 8: Full CI Verification

**Step 1: Run the complete CI pipeline**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short
```

Expected: All pass.

**Step 2: Manual smoke test**

1. Open `http://localhost:8885` → Graph tab
2. Verify blank canvas with sidebar showing root issues grouped by type
3. Click a milestone/epic → verify subtree renders with edges
4. Look for ghost nodes (dashed border, dimmed) on cross-tree deps
5. Click a ghost node → verify its ancestor tree appears in sidebar (amber)
6. Click amber item in sidebar → verify it promotes to blue (explicit)
7. Deselect an explicit item → verify cascade removes orphaned clicked-in items
8. Test type filter pills in sidebar
9. Test Select All / Clear All buttons
10. Test status pills still filter within the graph

**Step 3: Commit any fixes, then final commit**

```bash
git add -A
git commit -m "chore(graph): polish and verify scoped subtree explorer"
```

---

## Summary

| Task | Description | Key Files |
|------|-------------|-----------|
| 1 | Add sidebar state to state.js | `state.js` |
| 2 | Add sidebar HTML, simplify toolbar | `dashboard.html` |
| 3 | Create graphSidebar.js — tree index + rendering | `graphSidebar.js` (new) |
| 4 | Add selection logic and ghost resolution | `graphSidebar.js` |
| 5 | Refactor graph.js to use sidebar scope | `graph.js` |
| 6 | Wire everything in app.js | `app.js` |
| 7 | Update contract tests | `test_graph_api.py` |
| 8 | Full CI verification + smoke test | All |

**Dependencies:** Tasks 1-4 can proceed independently. Task 5 depends on 3-4. Task 6 depends on 3-5. Task 7 depends on 2, 5-6. Task 8 depends on all.
