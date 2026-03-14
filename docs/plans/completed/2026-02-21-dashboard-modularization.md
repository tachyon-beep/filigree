# Dashboard Modularization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract `dashboard.html` (2686 lines) into a multi-file ES module architecture — 12 JS files + a ~250-line HTML shell — while preserving all existing functionality and passing all existing tests.

**Issue:** filigree-1623b4

**Architecture:** Currently a single monolithic HTML file at `src/filigree/static/dashboard.html`. Backend serves it via `dashboard.py` (FastAPI). Tests in `tests/test_dashboard.py` (1457 lines, 80+ tests) exercise the API — they don't test JS directly, so they should pass unchanged.

**Tech Stack:** Vanilla JS (ES modules), Tailwind CSS (CDN), Cytoscape.js (CDN), FastAPI + Starlette StaticFiles

**Design doc:** `docs/plans/2026-02-21-dashboard-modularization-design.md`

---

### Task 1: Add StaticFiles Mount to dashboard.py

**Files:**
- Modify: `src/filigree/dashboard.py` (add StaticFiles import + mount)

**Step 1: Add the StaticFiles mount**

In `dashboard.py`, inside `create_app()`, after all route registrations but before `return app`, add:

```python
from starlette.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
```

The mount must come AFTER all `app.include_router()` calls and AFTER the root `/` route. FastAPI matches routes top-down; StaticFiles is a catch-all that would shadow API routes if mounted first.

**Step 2: Verify existing tests pass**

```bash
uv run pytest tests/test_dashboard.py --tb=short -q
```

No test changes expected — tests don't request static files.

**Done when:** `GET /static/js/app.js` would return a 200 (once the file exists). All existing tests pass.

---

### Task 2: Create state.js — Shared State and Constants

**Files:**
- Create: `src/filigree/static/js/state.js`
- Modify: `src/filigree/static/dashboard.html` (remove state declarations, add module import)

**Step 1: Create `js/state.js`**

Create `src/filigree/static/js/state.js` exporting:

```js
// --- Constants ---
export const CATEGORY_COLORS = { open: '#64748B', wip: '#38BDF8', done: '#7B919C' };
export const PRIORITY_COLORS = { 0: '#EF4444', 1: '#F97316', 2: '#EAB308', 3: '#22C55E', 4: '#64748B' };
export const TYPE_ICONS = { /* copy from dashboard.html */ };
export const THEME_COLORS = { /* copy from dashboard.html */ };
export const REFRESH_INTERVAL = 30000;

// --- Mutable state ---
export const state = {
  allIssues: [],
  allDeps: [],
  issueMap: {},
  stats: null,
  currentView: 'kanban',
  kanbanMode: 'standard',
  selectedIssue: null,
  selectedCards: new Set(),
  multiSelectMode: false,
  expandedEpics: new Set(),
  detailHistory: [],
  cy: null,
  workflowCy: null,
  typeTemplate: null,
  searchResults: null,
  _dragIssueId: null,
  _dragTransitions: [],
  _transitionsLoaded: false,
  API_BASE: '/api',
  currentProjectKey: '',
  allProjects: [],
  criticalPathIds: new Set(),
  healthScore: null,
};
```

Copy exact values from `dashboard.html` lines 349-387.

**Step 2: Remove var declarations from dashboard.html**

Delete the `var` declarations block (lines ~349-387) from the inline `<script>`. Replace with:
```html
<script type="module" src="/static/js/app.js"></script>
```

This is done incrementally — during the transition, the inline script block shrinks as functions move to modules. This task only moves state; the inline script continues to reference `state.*` via a temporary bridge (see Step 3).

**Step 3: Temporary compatibility bridge**

Until all functions are extracted, the inline `<script>` needs access to the state. Add a synchronous `<script>` tag that imports and re-exports to window:

```html
<script type="module">
  import { state, CATEGORY_COLORS, PRIORITY_COLORS, TYPE_ICONS, THEME_COLORS } from '/static/js/state.js';
  window._state = state;
  window._CATEGORY_COLORS = CATEGORY_COLORS;
  // ... etc
</script>
```

**Note:** This bridge is removed in Task 8 when the last inline code is extracted.

**Done when:** `state.js` exists, dashboard loads without errors, state is accessible.

---

### Task 3: Create api.js — API Client Layer

**Files:**
- Create: `src/filigree/static/js/api.js`
- Modify: `src/filigree/static/dashboard.html` (remove API functions from inline script)

**Step 1: Create `js/api.js`**

Extract all API call functions:

```js
import { state } from './state.js';

function apiUrl(path) {
  return state.API_BASE + path;
}

export async function fetchIssues() { /* from fetchData() */ }
export async function fetchDeps() { /* ... */ }
export async function fetchStats() { /* ... */ }
export async function updateIssue(id, body) { /* ... */ }
export async function closeIssue(id, reason, actor) { /* ... */ }
export async function reopenIssue(id, actor) { /* ... */ }
export async function claimIssue(id, assignee, actor) { /* ... */ }
export async function releaseIssue(id, actor) { /* ... */ }
export async function addDependency(id, depId) { /* ... */ }
export async function removeDependency(id, depId) { /* ... */ }
export async function addComment(id, text, author) { /* ... */ }
export async function loadTransitions(id) { /* ... */ }
export async function searchIssues(query) { /* ... */ }
export async function batchUpdate(ids, fields) { /* ... */ }
export async function batchClose(ids, reason, actor) { /* ... */ }
export async function fetchMetrics(days) { /* ... */ }
export async function fetchActivity(params) { /* ... */ }
export async function fetchTypeInfo(typeName) { /* ... */ }
export async function fetchPlan(milestoneId) { /* ... */ }
export async function loadProjects(ttl) { /* ... */ }
export async function setProject(key) { /* ... */ }
export async function reloadServer() { /* ... */ }
export async function fetchCriticalPath() { /* ... */ }
```

Each function returns the parsed JSON response. Error handling (try/catch with toast) stays in the calling code or uses a shared error handler.

**Step 2: Remove API functions from inline script**

Delete the extracted functions from `dashboard.html`. Update the compatibility bridge to expose them on `window._api`.

**Done when:** All API calls go through `api.js`. Dashboard still functions.

---

### Task 4: Create ui.js — Shared UI Utilities

**Files:**
- Create: `src/filigree/static/js/ui.js`
- Modify: `src/filigree/static/dashboard.html` (remove UI utility functions)

**Step 1: Create `js/ui.js`**

Extract these utilities:

```js
export function escHtml(str) { /* XSS prevention */ }
export function setLoading(el, loading) { /* button disable/enable */ }
export function trapFocus(panel) { /* accessibility: focus first element */ }
export function showToast(msg, type) { /* toast notification */ }
export function showPopover(anchor, html) { /* positioned popup */ }
export function closePopover() { /* hide popup */ }
export function showModal(title, bodyHtml, onConfirm) { /* modal dialog */ }
export function toggleTheme() { /* dark/light theme */ }
export function toggleSettingsMenu(e) { /* settings dropdown */ }
export function startTour() { /* onboarding tour */ }
export function showTourStep(index) { /* tour step */ }
export function endTour() { /* complete tour */ }
export function updateBatchBar() { /* batch action bar visibility */ }
```

Group: DOM helpers, toasts, popovers, modals, theme, tour, batch bar.

**Step 2: Remove from inline script, update bridge**

**Done when:** All UI utilities work from the module. Toast notifications, theme toggle, settings menu, tour all function.

---

### Task 5: Create router.js and filters.js

**Files:**
- Create: `src/filigree/static/js/router.js`
- Create: `src/filigree/static/js/filters.js`
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Create `js/router.js`**

Extract hash routing and view switching:

```js
import { state } from './state.js';

export function updateHash() { /* set URL hash */ }
export function parseHash() { /* restore state from hash */ }
export function switchView(view) { /* toggle view visibility, trigger load */ }
export function switchKanbanMode(mode) { /* standard/cluster/type */ }
```

`switchView` calls the appropriate view's `load` function. To avoid circular deps, use a registry pattern:

```js
const viewLoaders = {};
export function registerView(name, loader) { viewLoaders[name] = loader; }
```

Each view module calls `registerView('kanban', renderKanban)` on import.

**Step 2: Create `js/filters.js`**

Extract filter and preset logic:

```js
import { state } from './state.js';

export function getFilteredIssues() { /* apply filters, return sorted list */ }
export function applyFilters() { /* re-render after filter change */ }
export function toggleReady() { /* ready filter */ }
export function toggleBlocked() { /* blocked filter */ }
export function toggleMultiSelect() { /* multi-select mode */ }
export function toggleCardSelect(e, id) { /* select/deselect card */ }
export function getFilterState() { /* capture current filters */ }
export function applyFilterState(preset) { /* apply saved preset */ }
export function savePreset() { /* save to localStorage */ }
export function loadPreset() { /* load from dropdown */ }
export function populatePresets() { /* build preset dropdown */ }
export function debouncedSearch() { /* debounced search handler */ }
export function doSearch() { /* execute search */ }
```

**Done when:** View switching, hash routing, and all filter/preset/search functionality work.

---

### Task 6: Extract View Modules (Simple Views First)

**Files:**
- Create: `src/filigree/static/js/views/metrics.js`
- Create: `src/filigree/static/js/views/activity.js`
- Create: `src/filigree/static/js/views/workflow.js`
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Create `js/views/metrics.js`**

```js
import { state } from '../state.js';
import { fetchMetrics } from '../api.js';
import { registerView } from '../router.js';

export function loadMetrics() { /* render metrics view */ }
registerView('metrics', loadMetrics);
```

Extract `loadMetrics()` from dashboard.html (~lines 1850-1921).

**Step 2: Create `js/views/activity.js`**

Extract `loadActivity()` (~lines 1922-1961).

**Step 3: Create `js/views/workflow.js`**

Extract `loadWorkflow()` and `renderWorkflowGraph()` (~lines 2217-2281). These use `window.cytoscape` — accessed directly since it's a CDN global.

**Done when:** Metrics, Activity, and Workflow views render correctly from their module files.

---

### Task 7: Extract View Modules (Complex Views)

**Files:**
- Create: `src/filigree/static/js/views/graph.js`
- Create: `src/filigree/static/js/views/detail.js`
- Create: `src/filigree/static/js/views/kanban.js`
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Create `js/views/graph.js`**

Extract (~lines 1007-1150, 2025-2103):
```js
export function renderGraph() { /* build cytoscape instance */ }
export function graphFit() { /* reset zoom */ }
export function toggleCriticalPath() { /* highlight critical path */ }
export function computeImpactScores() { /* BFS downstream count */ }
export function computeHealthScore() { /* weighted health scoring */ }
export function showHealthBreakdown() { /* health modal */ }
```

**Step 2: Create `js/views/detail.js`**

Extract (~lines 1157-1645):
```js
export function openDetail(issueId) { /* fetch + render detail panel */ }
export function closeDetail() { /* slide panel off */ }
export function detailBack() { /* pop navigation history */ }
```

This is the most interconnected view — it calls API functions (claim, close, reopen, add comment, add/remove dep) and uses modals. All those are now in `api.js` and `ui.js`, so this module imports from both.

**Step 3: Create `js/views/kanban.js`**

Extract (~lines 610-1002):
```js
export function renderKanban() { /* dispatcher */ }
export function renderStandardKanban(cols) { /* 3-column board */ }
export function renderClusterKanban(cols) { /* grouped by epic */ }
export function renderTypeKanban(type, cols) { /* type-filtered */ }
export function renderCard(issue) { /* issue card HTML */ }
export function initDragAndDrop() { /* drag-and-drop handlers */ }
```

Drag-and-drop is tightly coupled to kanban rendering — keep them in the same module.

**Done when:** All 6 views render and interact correctly from their modules. Graph hover highlighting, critical path, detail panel navigation, drag-and-drop status transitions all work.

---

### Task 8: Create app.js Entry Point and Remove Inline Script

**Files:**
- Create: `src/filigree/static/js/app.js`
- Modify: `src/filigree/static/dashboard.html` (remove ALL inline JS, remove compatibility bridge)

**Step 1: Create `js/app.js`**

The entry point orchestrates initialization:

```js
import { state } from './state.js';
import { loadProjects, setProject } from './api.js';
import { parseHash, switchView, switchKanbanMode } from './router.js';
import { toggleTheme, startTour } from './ui.js';
import { populatePresets } from './filters.js';
import { initDragAndDrop } from './views/kanban.js';
import { openDetail } from './views/detail.js';

// Import all views to trigger registerView() calls
import './views/kanban.js';
import './views/graph.js';
import './views/detail.js';
import './views/metrics.js';
import './views/activity.js';
import './views/workflow.js';

async function init() {
  // 1. Restore theme
  const savedTheme = localStorage.getItem('filigree-theme');
  if (savedTheme === 'light') document.documentElement.setAttribute('data-theme', 'light');

  // 2. Parse URL hash
  parseHash();

  // 3. Load presets
  populatePresets();

  // 4. Load projects
  const projects = await loadProjects(6);
  if (projects.length > 0) {
    await setProject(state.currentProjectKey || projects[0].key);
  } else {
    await fetchData();
  }

  // 5. Initialize views
  switchView(state.currentView);
  switchKanbanMode(state.kanbanMode);
  if (state.selectedIssue) openDetail(state.selectedIssue);
  startTour();
  initDragAndDrop();

  // 6. Auto-refresh
  setInterval(fetchData, state.REFRESH_INTERVAL || 30000);
  setInterval(loadProjects, 60000);
}

init();
```

**Step 2: Clean up dashboard.html**

- Remove the entire inline `<script>` block (everything between `<script>` tags, ~2300 lines)
- Remove the compatibility bridge `<script type="module">` block
- Add a single entry point:
  ```html
  <script type="module" src="/static/js/app.js"></script>
  ```
- Keep: `<!DOCTYPE html>`, `<head>`, `<style>` block, HTML body structure

**Step 3: Wire up event handlers**

Inline HTML event handlers like `onclick="switchView('kanban')"` won't work with ES modules (functions aren't global). Two approaches:

**Option A (recommended):** Replace inline handlers with `data-*` attributes and add event delegation in `app.js`:
```html
<!-- Before -->
<button onclick="switchView('kanban')">Kanban</button>
<!-- After -->
<button data-view="kanban">Kanban</button>
```

```js
// In app.js
document.addEventListener('click', (e) => {
  const viewBtn = e.target.closest('[data-view]');
  if (viewBtn) switchView(viewBtn.dataset.view);
  // ... etc
});
```

**Option B:** Attach functions to `window` explicitly in each module. Less clean but simpler migration.

Use Option A for view switching, filter toggles, and other top-level buttons. Use Option B sparingly for dynamically generated HTML (cards, detail panel actions) where `data-*` + delegation would be overly complex.

**Done when:** The entire inline `<script>` block is gone. `dashboard.html` is ~250 lines. All functionality works from ES modules.

---

### Task 9: Final Verification

**Files:**
- None modified (verification only)

**Step 1: Run full test suite**

```bash
uv run pytest tests/test_dashboard.py --tb=short -q
```

All 80+ tests must pass.

**Step 2: Run CI pipeline**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short
```

**Step 3: Manual verification checklist**

Open `http://localhost:8377` and verify:

- [ ] Dashboard loads without console errors
- [ ] Kanban view: standard, cluster, type-filtered modes
- [ ] Drag-and-drop: cards move between columns, status updates
- [ ] Graph view: nodes render, hover highlighting, critical path toggle
- [ ] Graph: epics-only filter, fit button, legend
- [ ] Detail panel: opens on card click, shows deps/comments/events
- [ ] Detail: transition buttons work, close/reopen/claim/release
- [ ] Detail: add comment, add/remove blocker
- [ ] Detail: back navigation (multi-step)
- [ ] Metrics view: renders with 7/30/90 day selector
- [ ] Activity view: renders event timeline
- [ ] Workflow view: type selector, state machine diagram
- [ ] Search: type in search box, results filter correctly
- [ ] Filter presets: save, load, delete
- [ ] Ready/Blocked toggles: filter works
- [ ] Priority filter: dropdown filters correctly
- [ ] Status checkboxes: Open/Active/Closed toggles
- [ ] Multi-select: toggle, select cards, batch bar appears
- [ ] Batch operations: set priority, close selected
- [ ] Theme toggle: dark ↔ light, persists on reload
- [ ] Settings gear: dropdown opens, reload server works
- [ ] Hash routing: refresh page preserves view + selection
- [ ] Multi-project: project switcher works (if multiple projects)
- [ ] Health score: displays, help icon shows breakdown
- [ ] Stale badges: appear on aging WIP items
- [ ] Auto-refresh: data updates after interval
- [ ] Tour: clears localStorage, tour restarts correctly
- [ ] Issue creation: modal works from detail panel
- [ ] Plan view: milestone plan tree renders

**Step 4: Line count verification**

```bash
wc -l src/filigree/static/dashboard.html
# Expected: ~250 lines

wc -l src/filigree/static/js/*.js src/filigree/static/js/views/*.js
# Expected: ~2400 lines total across 12 files, each under 350 lines
```

**Done when:** All tests pass, all CI checks pass, manual verification complete, line counts meet targets.

---

## Summary

| Task | Description | Risk | Est. LOC moved |
|------|-------------|------|----------------|
| 1 | StaticFiles mount | Low | 3 lines added |
| 2 | state.js | Low | ~40 lines |
| 3 | api.js | Medium | ~300 lines |
| 4 | ui.js | Medium | ~200 lines |
| 5 | router.js + filters.js | Medium | ~250 lines |
| 6 | Simple views (metrics, activity, workflow) | Low | ~200 lines |
| 7 | Complex views (graph, detail, kanban) | High | ~750 lines |
| 8 | app.js + event handler migration | High | ~600 lines |
| 9 | Final verification | None | 0 lines |

**Highest risk tasks:** 7 and 8. The complex views have the most cross-module interactions, and the event handler migration (inline `onclick` → delegated events) touches every interactive element.

**Recommended execution:** Tasks 1-6 can proceed confidently. Pause after Task 6 for a manual smoke test before tackling the riskier Tasks 7-8.
