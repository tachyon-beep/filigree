# Dashboard Contextual Help & Tutorials Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add comprehensive contextual help, legends, tooltips, enhanced empty states, and a first-time onboarding tour to the Filigree dashboard — bringing help coverage from ~5% to ~80% without cluttering the clean developer-tool aesthetic.

**Architecture:** All changes are pure frontend in `src/filigree/static/dashboard.html`. No backend/API changes needed. Help infrastructure uses a reusable tooltip/popover system, CSS-only legend overlays, localStorage for tour dismissal, and enhanced empty state HTML. The claim modal improvement replaces `prompt()` with a proper modal consistent with existing modal patterns.

**Tech Stack:** Vanilla JS, Tailwind CSS (CDN), localStorage for tour/preferences state

---

## Task 1: Tooltip Infrastructure — CSS + JS Helper

Add the reusable tooltip/popover system that all subsequent tasks will use. This is a lightweight `showPopover(anchorEl, html, options)` function and matching CSS.

**Files:**
- Modify: `src/filigree/static/dashboard.html:11-54` (CSS — add tooltip styles)
- Modify: `src/filigree/static/dashboard.html:1119-1143` (script utilities section — add tooltip helper)

**Step 1: Add tooltip CSS**

In the `<style>` block, after the `.changed-flash` keyframes (line 28), add:

```css
  .popover { position: absolute; z-index: 60; max-width: 320px; }
  .popover-arrow { position: absolute; top: -6px; left: 20px; width: 12px; height: 6px;
    overflow: hidden; }
  .popover-arrow::after { content: ''; position: absolute; top: 3px; left: 0; width: 12px;
    height: 12px; transform: rotate(45deg); }
  .help-icon { display: inline-flex; align-items: center; justify-content: center;
    width: 16px; height: 16px; border-radius: 50%; font-size: 10px; line-height: 1;
    cursor: pointer; vertical-align: middle; margin-left: 4px; flex-shrink: 0; }
```

**Step 2: Add popover helper function**

In the script section, after the `escHtml()` function (around line 1124), add the popover helper:

```javascript
// ---------------------------------------------------------------------------
// Contextual help system
// ---------------------------------------------------------------------------
var _activePopover = null;
function showPopover(anchorEl, html, opts) {
  closePopover();
  opts = opts || {};
  var pop = document.createElement('div');
  pop.id = 'activePopover';
  pop.className = 'popover bg-slate-900 border border-slate-600 rounded-lg shadow-xl p-3 text-xs';
  pop.innerHTML = html +
    '<div class="flex justify-end mt-2"><button onclick="closePopover()" class="text-slate-500 hover:text-slate-300 text-xs">Dismiss</button></div>';
  document.body.appendChild(pop);
  // Position below anchor
  var rect = anchorEl.getBoundingClientRect();
  pop.style.top = (rect.bottom + window.scrollY + 8) + 'px';
  pop.style.left = Math.max(8, Math.min(rect.left + window.scrollX, window.innerWidth - 340)) + 'px';
  _activePopover = pop;
  // Close on outside click
  setTimeout(function() {
    document.addEventListener('click', _popoverOutsideClick);
  }, 0);
  // Auto-dismiss after timeout
  if (opts.timeout) setTimeout(closePopover, opts.timeout);
}

function _popoverOutsideClick(e) {
  if (_activePopover && !_activePopover.contains(e.target)) closePopover();
}

function closePopover() {
  if (_activePopover) { _activePopover.remove(); _activePopover = null; }
  document.removeEventListener('click', _popoverOutsideClick);
}
```

**Step 3: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: add tooltip/popover infrastructure for contextual help system"
```

---

## Task 2: Health Score Contextual Help (R1)

Add an inline help icon next to the health badge that explains the 4-factor scoring formula via popover.

**Files:**
- Modify: `src/filigree/static/dashboard.html:112-115` (header — add help icon after health badge)
- Modify: `src/filigree/static/dashboard.html` (script — add `showHealthHelp()`)

**Step 1: Add help icon next to health badge**

In the header stats section (line 115), after the health badge `<span>`, add a help icon:

```html
<button onclick="showHealthHelp(this)" class="help-icon bg-slate-700 text-slate-400 hover:text-slate-200" title="What is Health Score?" aria-label="Explain health score">?</button>
```

**Step 2: Add showHealthHelp function**

In the script section, after `showHealthBreakdown()`, add:

```javascript
function showHealthHelp(btn) {
  showPopover(btn,
    '<div class="text-slate-200 font-medium mb-2">Health Score (0-100)</div>' +
    '<div class="text-slate-400 space-y-1">' +
      '<div><span class="text-emerald-400 font-medium">Blocked</span> (25 pts) — Fewer blocked issues = higher score</div>' +
      '<div><span class="text-blue-400 font-medium">Freshness</span> (25 pts) — WIP items updated recently, not stale</div>' +
      '<div><span class="text-amber-400 font-medium">Ready</span> (25 pts) — Enough unblocked work available</div>' +
      '<div><span class="text-slate-300 font-medium">Balance</span> (25 pts) — No agent overloaded with WIP</div>' +
    '</div>' +
    '<div class="text-slate-500 mt-2 pt-2 border-t border-slate-700">Click the badge number for a detailed breakdown.</div>'
  );
}
```

**Step 3: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: health score contextual help popover (R1)"
```

---

## Task 3: Ready/Blocked Contextual Help (R3)

Add help icons next to the Ready and Blocked toggle buttons explaining what these concepts mean.

**Files:**
- Modify: `src/filigree/static/dashboard.html:83-89` (header — add help icons after Ready/Blocked buttons)
- Modify: `src/filigree/static/dashboard.html` (script — add `showReadyHelp()`, `showBlockedHelp()`)

**Step 1: Add help icons after Ready and Blocked buttons**

After the Ready button (line 86), add:

```html
<button onclick="showReadyHelp(this)" class="help-icon bg-slate-700 text-slate-400 hover:text-slate-200" title="What does Ready mean?" aria-label="Explain ready filter">?</button>
```

After the Blocked button (line 89), add:

```html
<button onclick="showBlockedHelp(this)" class="help-icon bg-slate-700 text-slate-400 hover:text-slate-200" title="What does Blocked mean?" aria-label="Explain blocked filter">?</button>
```

**Step 2: Add help functions**

```javascript
function showReadyHelp(btn) {
  showPopover(btn,
    '<div class="text-slate-200 font-medium mb-2">Ready Issues</div>' +
    '<div class="text-slate-400 space-y-1">' +
      '<div>Issues with <span class="text-emerald-400">no open blockers</span> that can be worked on immediately.</div>' +
      '<div class="mt-1"><span class="text-emerald-400">&#9679;</span> Green left border on cards = ready</div>' +
      '<div class="mt-1">Toggle this button to sort ready issues to the top.</div>' +
    '</div>'
  );
}

function showBlockedHelp(btn) {
  showPopover(btn,
    '<div class="text-slate-200 font-medium mb-2">Blocked Issues</div>' +
    '<div class="text-slate-400 space-y-1">' +
      '<div>Issues that <span class="text-red-400">depend on other incomplete work</span>.</div>' +
      '<div class="mt-1"><span class="text-red-400">&#128279;</span> Shows "blocked by N" on cards</div>' +
      '<div class="mt-1">Toggle to filter to only blocked issues — useful for identifying bottlenecks.</div>' +
    '</div>'
  );
}
```

**Step 3: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: ready/blocked contextual help popovers (R3)"
```

---

## Task 4: Graph View Legend Overlay (R2)

Add a persistent, collapsible legend to the graph view explaining node shapes, colors, borders, and interactions.

**Files:**
- Modify: `src/filigree/static/dashboard.html:125-134` (graph view toolbar — add legend toggle button)
- Modify: `src/filigree/static/dashboard.html` (add legend panel HTML inside graph view)

**Step 1: Add legend toggle button and panel**

In the graph view toolbar (line 131), after the Critical Path button, add:

```html
<button id="btnGraphLegend" onclick="toggleGraphLegend()" class="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600">Legend</button>
```

Inside the `#graphView` div, after the toolbar div (after line 133), add the legend panel:

```html
<div id="graphLegend" class="hidden absolute top-12 right-4 bg-slate-900/95 border border-slate-600 rounded-lg p-3 text-xs z-10 max-w-xs shadow-xl">
  <div class="text-slate-200 font-medium mb-2">Graph Legend</div>
  <div class="text-slate-400 space-y-2">
    <div>
      <div class="font-medium text-slate-300 mb-1">Node Shapes</div>
      <div class="grid grid-cols-2 gap-x-3 gap-y-0.5">
        <div>&#11043; Hexagon = Epic/Milestone</div>
        <div>&#9670; Diamond = Bug</div>
        <div>&#9733; Star = Feature</div>
        <div>&#9645; Rectangle = Task/other</div>
      </div>
    </div>
    <div>
      <div class="font-medium text-slate-300 mb-1">Colors</div>
      <div class="flex gap-3">
        <div><span class="inline-block w-3 h-3 rounded" style="background:#64748B"></span> Open</div>
        <div><span class="inline-block w-3 h-3 rounded" style="background:#3B82F6"></span> In Progress</div>
        <div><span class="inline-block w-3 h-3 rounded" style="background:#9CA3AF"></span> Done</div>
      </div>
    </div>
    <div>
      <div class="font-medium text-slate-300 mb-1">Indicators</div>
      <div>Green border = Ready (no blockers)</div>
      <div>Larger node = Higher priority</div>
      <div>Arrows = Dependency direction (blocks &rarr; blocked)</div>
    </div>
    <div>
      <div class="font-medium text-slate-300 mb-1">Interactions</div>
      <div>Click node &rarr; Open detail panel</div>
      <div>Hover node &rarr; Highlight downstream</div>
      <div>Critical Path &rarr; Longest blocking chain</div>
    </div>
  </div>
  <button onclick="toggleGraphLegend()" class="text-slate-500 hover:text-slate-300 mt-2">Close</button>
</div>
```

**Step 2: Add toggle function**

```javascript
function toggleGraphLegend() {
  var legend = document.getElementById('graphLegend');
  legend.classList.toggle('hidden');
  var btn = document.getElementById('btnGraphLegend');
  btn.className = legend.classList.contains('hidden')
    ? 'px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600'
    : 'px-2 py-0.5 rounded bg-blue-600 text-white';
}
```

**Step 3: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: graph view legend overlay for node shapes, colors, interactions (R2)"
```

---

## Task 5: Kanban Card Indicator Legend (R4)

Add a small legend to the Kanban view toolbar explaining card border colors and badge meanings.

**Files:**
- Modify: `src/filigree/static/dashboard.html:137-146` (kanban view toolbar — add legend toggle)

**Step 1: Add legend toggle and panel to Kanban toolbar**

In the Kanban toolbar (line 141), after the type filter select, add:

```html
<span class="text-slate-600">|</span>
<button id="btnKanbanLegend" onclick="toggleKanbanLegend()" class="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600">Legend</button>
```

Inside `#kanbanView`, after the toolbar div, add:

```html
<div id="kanbanLegend" class="hidden bg-slate-900/95 border-b border-slate-600 px-4 py-2 text-xs">
  <div class="flex flex-wrap gap-x-6 gap-y-1 text-slate-400">
    <span class="font-medium text-slate-300">Card Borders:</span>
    <span><span class="inline-block w-3 h-0.5 bg-emerald-500 mr-1 align-middle"></span>Ready (no blockers)</span>
    <span><span class="inline-block w-3 h-0.5 bg-amber-500 mr-1 align-middle"></span>Aging (WIP &gt;4h)</span>
    <span><span class="inline-block w-3 h-0.5 bg-red-500 mr-1 align-middle"></span>Stale (WIP &gt;24h)</span>
    <span class="font-medium text-slate-300 ml-4">Badges:</span>
    <span><span class="text-red-400">&#128279;</span> Blocked by N</span>
    <span><span class="text-amber-400">&#9889;</span>N = Blocks N downstream</span>
    <span>&#128100; = Assignee</span>
    <span class="font-medium text-slate-300 ml-4">Modes:</span>
    <span>Standard = 3 columns (Open/WIP/Done)</span>
    <span>Cluster = Grouped by epic with progress bars</span>
  </div>
</div>
```

**Step 2: Add toggle function**

```javascript
function toggleKanbanLegend() {
  var legend = document.getElementById('kanbanLegend');
  legend.classList.toggle('hidden');
  var btn = document.getElementById('btnKanbanLegend');
  btn.className = legend.classList.contains('hidden')
    ? 'px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600'
    : 'px-2 py-0.5 rounded bg-blue-600 text-white';
}
```

**Step 3: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: kanban card indicator legend for borders, badges, modes (R4)"
```

---

## Task 6: Enhanced Empty States with CTAs (R5)

Replace generic "No issues" text with actionable guidance that helps users understand what to do.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (script — `renderStandardKanban()`, `renderTypeKanban()`, `loadMetrics()`, `loadActivity()`, `doSearch()`)

**Step 1: Enhance Kanban empty states**

In `renderStandardKanban()` (line 462), replace the generic empty text:

```javascript
// Replace the "No issues" fallback in the ternary
(issues.length ? issues.map(...).join('') : '<div class="text-xs text-slate-500 p-4 text-center">' +
  (col.key === 'open' ? '<div class="mb-2">No open issues</div><button onclick="showCreateForm()" class="text-blue-400 hover:underline">+ Create an issue</button>'
   : col.key === 'wip' ? '<div>No work in progress</div><div class="text-slate-600 mt-1">Move an open issue to in-progress to start work</div>'
   : '<div>No completed issues yet</div>') + '</div>')
```

Similarly for `renderTypeKanban()` (line 658).

**Step 2: Enhance Metrics empty state**

In `loadMetrics()`, replace the "No completed issues" fallback (line 1348):

```javascript
'<div class="text-slate-500 p-6 text-center">' +
  '<div class="text-slate-300 font-medium mb-2">No completed issues in this period</div>' +
  '<div class="text-slate-500">Metrics track throughput, cycle time, and lead time.</div>' +
  '<div class="text-slate-500 mt-1">Close issues to see flow data here.</div>' +
  '<div class="mt-3"><button onclick="document.getElementById(\'metricsDays\').value=\'90\';loadMetrics()" class="text-blue-400 hover:underline text-xs">Try 90-day window</button></div>' +
'</div>'
```

**Step 3: Enhance Activity empty state**

In `loadActivity()`, replace "No recent activity" (line 1387):

```javascript
'<div class="text-slate-500 p-6 text-center">' +
  '<div class="text-slate-300 font-medium mb-2">No recent activity</div>' +
  '<div class="text-slate-500">Events appear here when issues are created, updated, or closed.</div>' +
'</div>'
```

**Step 4: Enhance Search no-results state**

In `doSearch()`, when `searchResults` is empty after a search, show guidance. In `renderStandardKanban()`, when all columns are empty AND searchResults is active:

No special code needed — the empty column messages already display. But add a search-level empty state: in `getFilteredIssues()`, track if filtering returned 0 results. In `renderKanban()`, if the filtered list is empty and searchResults is active, show a banner:

```javascript
// At the top of renderKanban(), after getFilteredIssues():
if (!items.length && searchResults !== null) {
  board.innerHTML = '<div class="flex-1 flex items-center justify-center text-slate-500 text-xs">' +
    '<div class="text-center"><div class="text-slate-300 mb-2">No matches found</div>' +
    '<div>Try broader search terms or <button onclick="document.getElementById(\'filterSearch\').value=\'\';searchResults=null;render();" class="text-blue-400 hover:underline">clear search</button></div></div></div>';
  return;
}
```

**Step 5: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 6: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: enhanced empty states with actionable guidance (R5)"
```

---

## Task 7: Create Form Field Hints (R6)

Add inline help text and descriptive priority options to the create form modal.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (script — `showCreateForm()`)

**Step 1: Enhance priority options with descriptions**

In `showCreateForm()` (line 1766), replace the plain priority `<select>` options:

```javascript
'<option value="0">P0 — Critical (drop everything)</option>' +
'<option value="1">P1 — High (do next)</option>' +
'<option value="2" selected>P2 — Medium (default)</option>' +
'<option value="3">P3 — Low</option>' +
'<option value="4">P4 — Backlog</option>'
```

**Step 2: Add inline help text below fields**

After the type/priority row, add a hint:

```html
'<div class="text-xs text-slate-500 -mt-1">Type determines workflow states. Priority P0-P1 should be used sparingly.</div>' +
```

After the description textarea, add:

```html
'<div class="text-xs text-slate-500 -mt-1">Markdown not supported. Keep descriptions concise.</div>' +
```

After the labels input, add:

```html
'<div class="text-xs text-slate-500 -mt-1">Example: ui, backend, urgent</div>' +
```

**Step 3: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: create form field hints and descriptive priority options (R6)"
```

---

## Task 8: Comprehensive Tooltip Coverage (R7)

Add `title` attributes to all interactive elements that lack them, covering buttons, badges, icons, and indicators.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (HTML header, footer, modals; script — card rendering, detail panel)

**Step 1: Header element tooltips**

Add/update `title` attributes on header elements:

- `#btnGraph`: `title="Dependency graph visualization"`
- `#btnKanban`: `title="Kanban board view"`
- `#btnMetrics`: `title="Flow metrics — throughput, cycle time, lead time"`
- `#btnActivity`: `title="Recent events across all issues"`
- `#btnWorkflow`: `title="Workflow state machine diagram for each issue type"`
- `#btnReady`: `title="Toggle: sort ready (unblocked) issues first"`
- `#btnBlocked`: `title="Toggle: show only blocked issues"`
- `#btnMultiSelect`: `title="Toggle multi-select mode for batch operations"`
- `#filterPreset`: `title="Load a saved filter preset"`
- Save button: `title="Save current filter settings as a named preset"`
- `#graphEpicsOnly`: parent label `title="Show only epic/milestone nodes for a high-level view"`
- `#btnCritPath`: `title="Highlight the longest dependency chain (most sequential blockers)"`
- Fit button: `title="Reset zoom and center the graph"`
- `#btnStandard`: `title="Three columns: Open, In Progress, Done"`
- `#btnCluster`: `title="Group issues by parent epic with progress bars"`

**Step 2: Footer element tooltips**

- `#sparkline`: already has `title="14-day throughput trend"` — keep
- `#staleBadge`: add `title="WIP issues with no updates for >2 hours. Click to view."`

**Step 3: Card rendering tooltips**

In `renderCard()`, enhance the existing priority dot title:

```javascript
'" title="Priority ' + issue.priority + ' (' + ['Critical','High','Medium','Low','Backlog'][issue.priority] + ')">'
```

Add title to the impact score badge:

```javascript
'<span class="text-amber-400" title="Blocks ' + impactScores[issue.id] + ' downstream issue' + (impactScores[issue.id] !== 1 ? 's' : '') + '">'
```

Add title to the status badge:

```javascript
'<span class="rounded px-1" style="..." title="Status: ' + issue.status + ' (category: ' + cat + ')">'
```

**Step 4: Detail panel tooltips**

In `openDetail()`, add tooltip to the CLI hint at the bottom:

```javascript
'<div class="mt-3 text-xs text-slate-600 select-all" title="Copy this command to view in terminal">filigree show ' + d.id + '</div>'
```

**Step 5: Add tooltip initialization function**

After the init section, add a function that sets tooltips programmatically for dynamically-rendered elements:

```javascript
function initStaticTooltips() {
  var tips = {
    'themeToggle': 'Toggle between dark and light theme',
    'sparkline': '14-day throughput trend (issues closed per day)',
  };
  Object.keys(tips).forEach(function(id) {
    var el = document.getElementById(id);
    if (el && !el.title) el.title = tips[id];
  });
}
```

Call `initStaticTooltips()` in the init section.

**Step 6: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 7: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: comprehensive tooltip coverage for all interactive elements (R7)"
```

---

## Task 9: First-Time Onboarding Tour (R8)

Add a guided walkthrough that runs on first visit (stored in localStorage), highlighting key UI areas with step-by-step explanations. Re-triggerable via `Shift+?`.

**Files:**
- Modify: `src/filigree/static/dashboard.html:11-54` (CSS — tour overlay styles)
- Modify: `src/filigree/static/dashboard.html` (script — tour system)
- Modify: `src/filigree/static/dashboard.html:1063-1085` (keyboard shortcuts — add Shift+? and tour entry to help modal)

**Step 1: Add tour CSS**

In the `<style>` block, add:

```css
  .tour-highlight { outline: 3px solid #3B82F6 !important; outline-offset: 4px; position: relative; z-index: 55; }
  .tour-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 50; }
  .tour-tooltip { position: fixed; z-index: 56; max-width: 360px; }
```

**Step 2: Add tour system**

```javascript
// ---------------------------------------------------------------------------
// Onboarding tour
// ---------------------------------------------------------------------------
var TOUR_STEPS = [
  { el: '#btnKanban', text: 'The dashboard has 5 views: Kanban (default), Graph, Metrics, Activity, and Workflow. Each shows your issues differently.', pos: 'bottom' },
  { el: '#btnReady', text: 'Ready issues have no blockers and can be worked on immediately. Toggle this to sort them first.', pos: 'bottom' },
  { el: '#filterSearch', text: 'Search issues by title or ID. Press "/" anywhere to focus this field instantly.', pos: 'bottom' },
  { el: '#healthBadge', text: 'Health score (0-100) measures project flow. Click it for a detailed breakdown of what affects the score.', pos: 'bottom' },
  { el: '#kanbanBoard', text: 'Click any card to open its detail panel. Use j/k to navigate between cards with your keyboard.', pos: 'top' },
  { el: null, text: 'Press "?" anytime to see all keyboard shortcuts. Look for small "?" icons next to features for contextual help. Happy tracking!', pos: 'center' },
];

function startTour() {
  showTourStep(0);
}

function showTourStep(index) {
  // Clean up previous
  var prev = document.getElementById('tourOverlay');
  if (prev) prev.remove();
  document.querySelectorAll('.tour-highlight').forEach(function(el) { el.classList.remove('tour-highlight'); });

  if (index >= TOUR_STEPS.length) {
    localStorage.setItem('filigree_tour_done', 'true');
    return;
  }

  var step = TOUR_STEPS[index];
  var targetEl = step.el ? document.querySelector(step.el) : null;

  // Highlight target
  if (targetEl) targetEl.classList.add('tour-highlight');

  // Create overlay
  var overlay = document.createElement('div');
  overlay.id = 'tourOverlay';
  overlay.className = 'tour-overlay';

  // Create tooltip
  var tooltip = document.createElement('div');
  tooltip.className = 'tour-tooltip bg-slate-800 border border-blue-500 rounded-lg p-4 shadow-xl';
  tooltip.innerHTML =
    '<div class="text-sm text-slate-200 mb-3 leading-relaxed">' + step.text + '</div>' +
    '<div class="flex items-center justify-between">' +
      '<span class="text-xs text-slate-500">' + (index + 1) + ' of ' + TOUR_STEPS.length + '</span>' +
      '<div class="flex gap-2">' +
        '<button onclick="endTour()" class="text-xs text-slate-500 hover:text-slate-300 px-2 py-1">Skip</button>' +
        '<button onclick="showTourStep(' + (index + 1) + ')" class="text-xs bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700">' +
          (index === TOUR_STEPS.length - 1 ? 'Done' : 'Next') +
        '</button>' +
      '</div>' +
    '</div>';

  document.body.appendChild(overlay);
  document.body.appendChild(tooltip);

  // Position tooltip
  if (targetEl) {
    var rect = targetEl.getBoundingClientRect();
    if (step.pos === 'bottom') {
      tooltip.style.top = (rect.bottom + 12) + 'px';
      tooltip.style.left = Math.max(16, Math.min(rect.left, window.innerWidth - 380)) + 'px';
    } else if (step.pos === 'top') {
      tooltip.style.bottom = (window.innerHeight - rect.top + 12) + 'px';
      tooltip.style.left = Math.max(16, Math.min(rect.left, window.innerWidth - 380)) + 'px';
    }
  } else {
    // Center
    tooltip.style.top = '50%';
    tooltip.style.left = '50%';
    tooltip.style.transform = 'translate(-50%, -50%)';
  }

  // Allow clicking overlay to skip
  overlay.onclick = function() { endTour(); };
}

function endTour() {
  var overlay = document.getElementById('tourOverlay');
  if (overlay) overlay.remove();
  // Remove any lingering tooltip
  document.querySelectorAll('.tour-tooltip').forEach(function(el) { el.remove(); });
  document.querySelectorAll('.tour-highlight').forEach(function(el) { el.classList.remove('tour-highlight'); });
  localStorage.setItem('filigree_tour_done', 'true');
}
```

**Step 3: Trigger tour on first visit**

In the init section (after `fetchData().then(...)` around line 1969), add:

```javascript
// First-time tour
if (!localStorage.getItem('filigree_tour_done')) {
  setTimeout(startTour, 1500);
}
```

**Step 4: Add Shift+? keyboard shortcut**

In the keyboard event handler, update the `?` handler (line 1063) to check for Shift:

```javascript
if (e.key === '?' && e.shiftKey) {
  e.preventDefault();
  localStorage.removeItem('filigree_tour_done');
  startTour();
  return;
}
```

**Step 5: Update help modal to mention tour**

In the help modal HTML (line 1073), add a new entry:

```html
'<div><kbd class="bg-slate-700 px-1 rounded">Shift+?</kbd> Restart onboarding tour</div>' +
```

**Step 6: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 7: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: first-time onboarding tour with Shift+? replay (R8)"
```

---

## Task 10: Replace Claim prompt() with Modal (Minor)

Replace the blocking `window.prompt()` in `claimIssue()` with an inline modal matching the existing modal pattern, and remember the last assignee in localStorage.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (script — `claimIssue()`)

**Step 1: Replace claimIssue function**

Replace the `claimIssue` function:

```javascript
async function claimIssue(issueId) {
  var existing = document.getElementById('claimModal');
  if (existing) existing.remove();
  var lastAssignee = localStorage.getItem('filigree_last_assignee') || '';
  var modal = document.createElement('div');
  modal.id = 'claimModal';
  modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  modal.innerHTML = '<div class="bg-slate-800 rounded-lg border border-slate-600 p-4 w-80 shadow-xl">' +
    '<div class="text-sm text-slate-200 mb-2">Claim Issue</div>' +
    '<label for="claimNameInput" class="text-xs text-slate-400">Your name or agent ID</label>' +
    '<input id="claimNameInput" type="text" value="' + escHtml(lastAssignee) + '" class="w-full bg-slate-700 text-slate-200 text-xs rounded px-3 py-2 border border-slate-600 mt-1 mb-3 focus:outline-none focus:border-blue-500">' +
    '<div class="flex justify-end gap-2">' +
      '<button id="claimCancel" class="text-xs bg-slate-700 px-3 py-1.5 rounded hover:bg-slate-600">Cancel</button>' +
      '<button id="claimConfirm" class="text-xs bg-emerald-600 text-white px-3 py-1.5 rounded hover:bg-emerald-700">Claim</button>' +
    '</div></div>';
  document.body.appendChild(modal);
  var input = document.getElementById('claimNameInput');
  input.focus();
  input.select();
  modal.onclick = function(e) { if (e.target === modal) modal.remove(); };
  document.getElementById('claimCancel').onclick = function() { modal.remove(); };
  input.onkeydown = function(e) { if (e.key === 'Enter') document.getElementById('claimConfirm').click(); };
  document.getElementById('claimConfirm').onclick = async function() {
    var name = input.value.trim();
    if (!name) { showToast('Name is required', 'error'); return; }
    modal.remove();
    localStorage.setItem('filigree_last_assignee', name);
    var resp = await fetch('/api/issue/' + issueId + '/claim', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({assignee: name}),
    });
    if (!resp.ok) { var err = await resp.json(); showToast('Error: ' + (err.error || 'Claim failed'), 'error'); return; }
    showToast('Claimed by ' + name, 'success');
    await fetchData();
    if (selectedIssue === issueId) openDetail(issueId);
  };
}
```

**Step 2: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 3: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix: replace claim prompt() with modal + localStorage assignee recall"
```

---

## Task 11: Search Clear Button + Filter Presets Help (Minor)

Add a visible clear button to the search input and a help icon explaining filter presets.

**Files:**
- Modify: `src/filigree/static/dashboard.html:97-99` (header — wrap search input)
- Modify: `src/filigree/static/dashboard.html:106-109` (header — add presets help icon)

**Step 1: Add search clear button**

Wrap the search input in a relative container and add a clear button:

Replace the search input (line 98-99) with:

```html
<div class="relative">
  <label for="filterSearch" class="sr-only">Search</label>
  <input id="filterSearch" type="text" placeholder="Search..." oninput="debouncedSearch()"
         class="bg-slate-700 text-slate-200 text-xs rounded px-3 py-1 pr-6 border border-slate-600 w-56 focus:outline-none focus:border-blue-500">
  <button onclick="document.getElementById('filterSearch').value='';searchResults=null;render();"
          class="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 text-sm leading-none"
          title="Clear search" aria-label="Clear search">&times;</button>
</div>
```

**Step 2: Add presets help icon**

After the Save preset button (line 109), add:

```html
<button onclick="showPresetsHelp(this)" class="help-icon bg-slate-700 text-slate-400 hover:text-slate-200" title="How filter presets work" aria-label="Explain filter presets">?</button>
```

Add the help function:

```javascript
function showPresetsHelp(btn) {
  showPopover(btn,
    '<div class="text-slate-200 font-medium mb-2">Filter Presets</div>' +
    '<div class="text-slate-400 space-y-1">' +
      '<div>1. Set your filters (status, priority, search)</div>' +
      '<div>2. Click <span class="text-slate-300">Save</span> and name your preset</div>' +
      '<div>3. Select from the dropdown to restore filters</div>' +
    '</div>' +
    '<div class="text-slate-500 mt-2 pt-2 border-t border-slate-700">Presets are stored in your browser (localStorage).</div>'
  );
}
```

**Step 3: Also replace the savePreset prompt() with a modal**

Replace `savePreset()` to use a proper modal instead of `prompt()`:

```javascript
function savePreset() {
  var existing = document.getElementById('presetNameModal');
  if (existing) existing.remove();
  var modal = document.createElement('div');
  modal.id = 'presetNameModal';
  modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  modal.innerHTML = '<div class="bg-slate-800 rounded-lg border border-slate-600 p-4 w-72 shadow-xl">' +
    '<div class="text-sm text-slate-200 mb-2">Save filter preset</div>' +
    '<input id="presetNameInput" type="text" placeholder="Preset name..." class="w-full bg-slate-700 text-slate-200 text-xs rounded px-3 py-2 border border-slate-600 mb-3 focus:outline-none focus:border-blue-500">' +
    '<div class="flex justify-end gap-2">' +
      '<button onclick="document.getElementById(\'presetNameModal\').remove()" class="text-xs bg-slate-700 px-3 py-1.5 rounded hover:bg-slate-600">Cancel</button>' +
      '<button onclick="confirmSavePreset()" class="text-xs bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700">Save</button>' +
    '</div></div>';
  document.body.appendChild(modal);
  modal.onclick = function(e) { if (e.target === modal) modal.remove(); };
  document.getElementById('presetNameInput').focus();
}

function confirmSavePreset() {
  var name = document.getElementById('presetNameInput').value.trim();
  if (!name) { showToast('Name is required', 'error'); return; }
  var presets = JSON.parse(localStorage.getItem('filigree_presets') || '{}');
  presets[name] = getFilterState();
  localStorage.setItem('filigree_presets', JSON.stringify(presets));
  document.getElementById('presetNameModal').remove();
  populatePresets();
  showToast('Preset "' + name + '" saved', 'success');
}
```

**Step 4: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 5: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix: search clear button, presets help, replace prompt() in savePreset"
```

---

## Task 12: Activity Feed Day Separators (Minor)

Group activity events by date with day headers for easier scanning.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (script — `loadActivity()`)

**Step 1: Add date grouping to loadActivity**

Replace the activity rendering in `loadActivity()`:

```javascript
// Group events by date
var grouped = {};
events.forEach(function(e) {
  var dateKey = e.created_at ? e.created_at.slice(0, 10) : 'unknown';
  if (!grouped[dateKey]) grouped[dateKey] = [];
  grouped[dateKey].push(e);
});

var today = new Date().toISOString().slice(0, 10);
var yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);

container.innerHTML = Object.keys(grouped).sort().reverse().map(function(dateKey) {
  var label = dateKey === today ? 'Today' : dateKey === yesterday ? 'Yesterday' : dateKey;
  var eventsHtml = grouped[dateKey].map(function(e) {
    var time = e.created_at ? e.created_at.slice(11, 16) : '';
    var title = e.issue_title ? escHtml(e.issue_title.slice(0, 50)) : e.issue_id;
    var detail = '';
    if (e.event_type === 'status_changed') detail = e.old_value + ' \u2192 ' + e.new_value;
    else if (e.new_value) detail = e.new_value;
    return '<div class="flex items-start gap-3 py-2 border-b border-slate-800 cursor-pointer hover:bg-slate-800/50" onclick="openDetail(\'' + e.issue_id + '\')">' +
      '<span class="text-slate-600 shrink-0 w-12">' + time + '</span>' +
      '<span class="text-slate-400 shrink-0 w-32">' + escHtml(e.event_type) + '</span>' +
      '<span class="text-slate-300 truncate">' + title + '</span>' +
      (detail ? '<span class="text-slate-500 shrink-0">' + escHtml(detail) + '</span>' : '') +
      (e.actor ? '<span class="text-slate-600 shrink-0">' + escHtml(e.actor) + '</span>' : '') +
    '</div>';
  }).join('');
  return '<div class="mb-4"><div class="text-xs font-medium text-slate-500 mb-2 pb-1 border-b border-slate-700">' + label + '</div>' + eventsHtml + '</div>';
}).join('');
```

**Step 2: Verify no regressions**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: 87 passed

**Step 3: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix: activity feed day separators for easier scanning"
```

---

## Task 13: Final Verification and Integration Commit

Run full test suite, verify all help features work together, and make a final commit.

**Step 1: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: All tests pass (no backend changes in this plan)

**Step 2: Run dashboard-specific tests**

Run: `python -m pytest tests/test_dashboard.py -v`
Expected: 87 passed

**Step 3: Verify HTML is well-formed**

Run: `python -c "from filigree.dashboard import STATIC_DIR; html = (STATIC_DIR / 'dashboard.html').read_text(); assert '<!DOCTYPE html>' in html; assert '</html>' in html; print(f'Dashboard: {len(html)} chars, {html.count(chr(10))} lines')"`

**Step 4: Final commit (if any uncommitted changes)**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: contextual help system — tooltips, legends, tour, enhanced UX (Phase 4)"
```

---

## Summary

| Task | Description | Type | Priority |
|------|-------------|------|----------|
| 1 | Tooltip infrastructure (CSS + JS) | Foundation | High |
| 2 | Health score contextual help | Help (R1) | High |
| 3 | Ready/Blocked contextual help | Help (R3) | High |
| 4 | Graph view legend overlay | Help (R2) | High |
| 5 | Kanban card indicator legend | Help (R4) | High |
| 6 | Enhanced empty states with CTAs | Help (R5) | Medium |
| 7 | Create form field hints | Help (R6) | Medium |
| 8 | Comprehensive tooltip coverage | Help (R7) | Medium |
| 9 | First-time onboarding tour | Help (R8) | Medium |
| 10 | Replace claim prompt() with modal | UX Fix | Low |
| 11 | Search clear + presets help + savePreset modal | UX Fix | Low |
| 12 | Activity feed day separators | UX Fix | Low |
| 13 | Final verification | QA | - |
