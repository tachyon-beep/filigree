# Dashboard Phase 3: UX Fixes + Advanced Features Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix critical UX/accessibility issues identified by the UX specialist review, implement Phase 3 features (R21-R30), and finalize documentation.

**Architecture:** All changes are in two files: `src/filigree/static/dashboard.html` (frontend) and `src/filigree/dashboard.py` (backend, new endpoints only). Three new API endpoints needed for claim/release/dependency management. Everything else is pure frontend work — CSS fixes, new components, localStorage for filter presets and theme. Tests go in `tests/test_dashboard.py`.

**Tech Stack:** Vanilla JS, Tailwind CSS (CDN), FastAPI, httpx/pytest for tests, Cytoscape.js (already loaded)

---

## Task 1: UX Critical — Touch Targets, Form Labels, Contrast

Fix the three critical UX issues: (1) buttons/inputs below 44px min touch target, (2) missing visible `<label>` elements for form inputs (WCAG 2.1 SC 3.3.2), (3) insufficient contrast on `text-slate-500` elements.

**Files:**
- Modify: `src/filigree/static/dashboard.html:11-30` (CSS styles)
- Modify: `src/filigree/static/dashboard.html:41-86` (header — buttons, inputs, labels)
- Modify: `src/filigree/static/dashboard.html:895-916` (detail panel actions — priority select, assignee input, comment input)

**Step 1: Fix touch targets and contrast in CSS**

In `dashboard.html`, after line 29 (the `@keyframes flash` block), add CSS to enforce minimum touch target sizes and fix contrast:

```css
  button, select, input[type="text"], input[type="checkbox"] { min-height: 36px; }
  @media (pointer: coarse) { button, select, input[type="text"] { min-height: 44px; min-width: 44px; } }
  .text-slate-500 { color: #94A3B8 !important; } /* Upgrade slate-500 (#64748B) to slate-400 (#94A3B8) for AA contrast on #0F172A bg */
```

Note: The `text-slate-500` override fixes the global contrast issue. The `@media (pointer: coarse)` targets touch devices specifically so desktop button sizes stay compact.

**Step 2: Add visible labels to header filter inputs**

In the header filter bar (lines 57-77), add visible `<label>` elements for the search input and priority select. Replace line 64:

```html
<label for="filterPriority" class="text-xs text-slate-400">Priority:</label>
<select id="filterPriority" ...>
```

Replace line 70-71 (search input):

```html
<label for="filterSearch" class="text-xs text-slate-400">Search:</label>
<input id="filterSearch" type="text" ...>
```

**Step 3: Add visible labels to detail panel inputs**

In the detail panel actions section (around lines 895-916), add labels for the priority select, assignee input, and comment input:

Before the `<select id="prioSelect"` element, add:
```html
<label for="prioSelect" class="text-xs text-slate-400">Priority:</label>
```

Before the `<input id="assigneeInput"` element, add:
```html
<label for="assigneeInput" class="text-xs text-slate-400">Assignee:</label>
```

Before the `<input id="commentInput"` element (line 912), change the `<div>` heading to a `<label>`:
```html
<label for="commentInput" class="text-xs font-medium text-slate-400 mb-1">Add Comment</label>
```

**Step 4: Verify changes render correctly**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: All 79 tests pass (no backend changes)

**Step 5: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix: UX critical — touch targets, form labels, contrast (WCAG)"
```

---

## Task 2: UX Major — Toast Notifications Replace alert()

Replace all `alert()` and `prompt()` calls with inline toast notifications and modal dialogs. This fixes the UX major issue of blocking browser dialogs.

**Files:**
- Modify: `src/filigree/static/dashboard.html:156-161` (add toast container after batch bar)
- Modify: `src/filigree/static/dashboard.html:1037-1093` (action functions — `updateIssue`, `closeIssue`, `reopenIssue`, `addComment`)
- Modify: `src/filigree/static/dashboard.html:1342-1365` (batch operations — `batchSetPriority`, `batchCloseSelected`)

**Step 1: Add toast container and showToast function**

After the batch bar `</div>` (line 161), add a toast container:

```html
<div id="toastContainer" class="fixed top-4 right-4 z-50 flex flex-col gap-2" aria-live="polite"></div>
```

In the `<script>` section, add toast function before the actions section (before line 1034):

```javascript
function showToast(message, type) {
  var container = document.getElementById('toastContainer');
  var toast = document.createElement('div');
  var bg = type === 'error' ? 'bg-red-900/90 border-red-700 text-red-200'
         : type === 'success' ? 'bg-emerald-900/90 border-emerald-700 text-emerald-200'
         : 'bg-slate-800/90 border-slate-600 text-slate-200';
  toast.className = 'px-4 py-2 rounded border text-xs shadow-lg ' + bg;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(function() { toast.remove(); }, 4000);
}
```

**Step 2: Replace alert() calls in action functions**

In `updateIssue()` (line 1045): Replace `alert('Error: ' + ...)` with `showToast('Error: ' + (err.error || 'Update failed'), 'error')`
In `updateIssue()` (line 1052): Replace `alert('Network error')` with `showToast('Network error', 'error')`
In `closeIssue()` (line 1061): Replace `alert(...)` with `showToast(...)`
In `reopenIssue()` (line 1071): Replace `alert(...)` with `showToast(...)`

**Step 3: Replace prompt() in closeIssue with inline modal**

Replace the `closeIssue` function to use an inline text input instead of `prompt()`:

```javascript
async function closeIssue(issueId) {
  var existing = document.getElementById('closeReasonModal');
  if (existing) existing.remove();
  var modal = document.createElement('div');
  modal.id = 'closeReasonModal';
  modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  modal.innerHTML = '<div class="bg-slate-800 rounded-lg border border-slate-600 p-4 w-80 shadow-xl">' +
    '<div class="text-sm text-slate-200 mb-2">Close reason (optional)</div>' +
    '<input id="closeReasonInput" type="text" class="w-full bg-slate-700 text-slate-200 text-xs rounded px-3 py-2 border border-slate-600 mb-3 focus:outline-none focus:border-blue-500">' +
    '<div class="flex justify-end gap-2">' +
      '<button id="closeReasonCancel" class="text-xs bg-slate-700 px-3 py-1.5 rounded hover:bg-slate-600">Cancel</button>' +
      '<button id="closeReasonConfirm" class="text-xs bg-red-600 text-white px-3 py-1.5 rounded hover:bg-red-700">Close Issue</button>' +
    '</div></div>';
  document.body.appendChild(modal);
  document.getElementById('closeReasonInput').focus();
  document.getElementById('closeReasonCancel').onclick = function() { modal.remove(); };
  modal.onclick = function(e) { if (e.target === modal) modal.remove(); };
  document.getElementById('closeReasonConfirm').onclick = async function() {
    var reason = document.getElementById('closeReasonInput').value || '';
    modal.remove();
    var resp = await fetch('/api/issue/' + issueId + '/close', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({reason: reason}),
    });
    if (!resp.ok) { var err = await resp.json(); showToast('Error: ' + (err.error || 'Close failed'), 'error'); return; }
    showToast('Issue closed', 'success');
    await fetchData();
    if (selectedIssue === issueId) openDetail(issueId);
  };
}
```

**Step 4: Replace prompt() in batchSetPriority with inline modal**

Same pattern: replace `prompt('Set priority (0-4):')` with an inline modal containing a `<select>` with P0-P4 options.

**Step 5: Replace confirm() in batchCloseSelected with inline modal**

Replace `confirm(...)` with an inline modal showing "Close N issues?" with Cancel/Confirm buttons.

**Step 6: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: All tests pass

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix: replace alert/prompt/confirm with toast notifications and modals"
```

---

## Task 3: UX Major — Loading States and Focus Indicators

Add loading spinners/disabled states to async action buttons, and enhance keyboard focus indicators.

**Files:**
- Modify: `src/filigree/static/dashboard.html:11-30` (CSS — focus styles)
- Modify: `src/filigree/static/dashboard.html:1037-1093` (action functions — add loading state)

**Step 1: Add focus indicator CSS**

In the `<style>` block, add:

```css
  button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid #60A5FA; outline-offset: 2px; }
  .btn-loading { opacity: 0.6; pointer-events: none; }
```

**Step 2: Add loading state helper**

Add helper functions before the actions section:

```javascript
function setLoading(el, loading) {
  if (!el) return;
  if (loading) { el.classList.add('btn-loading'); el.dataset.origText = el.textContent; el.textContent = 'Saving...'; }
  else { el.classList.remove('btn-loading'); if (el.dataset.origText) el.textContent = el.dataset.origText; }
}
```

**Step 3: Apply loading states to transition buttons**

In `updateIssue()`, add loading state: get the clicked button via `document.activeElement`, call `setLoading(btn, true)` before the fetch, `setLoading(btn, false)` after.

**Step 4: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix: add loading states to action buttons and focus-visible indicators"
```

---

## Task 4: UX Major — Detail Panel Width and Responsive Basics (R28)

Make the detail panel wider (500px default), add responsive breakpoints for mobile. This addresses both UX major (panel width) and R28 (responsive layout).

**Files:**
- Modify: `src/filigree/static/dashboard.html:5` (viewport meta — already present)
- Modify: `src/filigree/static/dashboard.html:11-30` (CSS — responsive styles)
- Modify: `src/filigree/static/dashboard.html:142` (detail panel width)

**Step 1: Add responsive CSS**

In the `<style>` block, add responsive rules:

```css
  @media (max-width: 768px) {
    .kanban-col { min-width: 100% !important; }
    #detailPanel { width: 100% !important; }
    header { flex-wrap: wrap; gap: 0.5rem; }
    header > div:nth-child(2) { flex-wrap: wrap; }
    footer { flex-wrap: wrap; }
  }
  @media (min-width: 769px) and (max-width: 1200px) {
    #detailPanel { width: 450px !important; }
    .kanban-col { min-width: 280px; }
  }
```

**Step 2: Update detail panel default width**

On line 142, change `w-[400px]` to `w-[500px]`:

```html
<div id="detailPanel" ... class="... w-[500px] ...">
```

**Step 3: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: responsive layout and wider detail panel (R28)"
```

---

## Task 5: System Health Score (R21)

Add a composite 0-100 health score to the header. Computes from: blocked ratio, WIP age, throughput trend, stale count. Click to show breakdown.

**Files:**
- Modify: `src/filigree/static/dashboard.html:80-85` (header — add health badge)
- Modify: `src/filigree/static/dashboard.html` (script section — add `computeHealthScore()`)

**Step 1: Add health score badge to header**

In the header stats section (line 80), add before the existing stats:

```html
<span id="healthBadge" onclick="showHealthBreakdown()" class="cursor-pointer px-2 py-0.5 rounded text-xs font-bold" title="System Health Score">--</span>
```

**Step 2: Add health score computation**

Add `computeHealthScore()` function in the script section, called from `fetchData()` after `computeImpactScores()`:

```javascript
function computeHealthScore() {
  if (!allIssues.length) return;
  var total = allIssues.length;
  // Factor 1: Blocked ratio (0-25 pts, fewer blocked = higher score)
  var openIssues = allIssues.filter(function(i) { return (i.status_category || 'open') !== 'done'; });
  var blockedCount = openIssues.filter(function(i) {
    return (i.blocked_by || []).some(function(bid) {
      var b = issueMap[bid]; return b && (b.status_category || 'open') !== 'done';
    });
  }).length;
  var blockedRatio = openIssues.length ? blockedCount / openIssues.length : 0;
  var blockedScore = Math.round(25 * (1 - blockedRatio));
  // Factor 2: WIP freshness (0-25 pts, fewer stale WIP = higher)
  var wipIssues = allIssues.filter(function(i) { return (i.status_category || 'open') === 'wip'; });
  var staleWip = wipIssues.filter(function(i) {
    return i.updated_at && (Date.now() - new Date(i.updated_at).getTime()) > 24 * 3600000;
  }).length;
  var freshScore = wipIssues.length ? Math.round(25 * (1 - staleWip / wipIssues.length)) : 25;
  // Factor 3: Ready availability (0-25 pts, more ready = higher)
  var readyCount = allIssues.filter(function(i) { return i.is_ready; }).length;
  var readyScore = openIssues.length ? Math.min(25, Math.round(25 * readyCount / Math.max(openIssues.length * 0.3, 1))) : 25;
  // Factor 4: WIP balance (0-25 pts, no overloaded agents)
  var agentWip = {};
  wipIssues.forEach(function(i) { if (i.assignee) agentWip[i.assignee] = (agentWip[i.assignee] || 0) + 1; });
  var maxWip = Math.max.apply(null, Object.values(agentWip).concat([0]));
  var balanceScore = maxWip > 5 ? 10 : maxWip > 3 ? 18 : 25;

  var score = blockedScore + freshScore + readyScore + balanceScore;
  var badge = document.getElementById('healthBadge');
  if (!badge) return;
  badge.textContent = score;
  badge.title = 'Health: ' + score + '/100';
  if (score >= 75) { badge.className = 'cursor-pointer px-2 py-0.5 rounded text-xs font-bold bg-emerald-900/50 text-emerald-400 border border-emerald-700'; }
  else if (score >= 50) { badge.className = 'cursor-pointer px-2 py-0.5 rounded text-xs font-bold bg-amber-900/50 text-amber-400 border border-amber-700'; }
  else { badge.className = 'cursor-pointer px-2 py-0.5 rounded text-xs font-bold bg-red-900/50 text-red-400 border border-red-700'; }

  // Store breakdown for drill-down
  window._healthBreakdown = {
    score: score,
    blocked: { score: blockedScore, max: 25, detail: blockedCount + ' blocked of ' + openIssues.length + ' open' },
    freshness: { score: freshScore, max: 25, detail: staleWip + ' stale WIP of ' + wipIssues.length },
    ready: { score: readyScore, max: 25, detail: readyCount + ' ready issues' },
    balance: { score: balanceScore, max: 25, detail: 'Max agent WIP: ' + maxWip },
  };
}

function showHealthBreakdown() {
  var b = window._healthBreakdown;
  if (!b) return;
  var panel = document.getElementById('detailContent');
  var dp = document.getElementById('detailPanel');
  dp.classList.remove('translate-x-full');
  selectedIssue = null;
  panel.innerHTML =
    '<div class="flex items-center justify-between mb-3">' +
      '<span class="text-base font-semibold text-slate-200">System Health: ' + b.score + '/100</span>' +
      '<button onclick="closeDetail()" class="text-slate-500 hover:text-slate-300 text-lg">&times;</button></div>' +
    ['blocked', 'freshness', 'ready', 'balance'].map(function(k) {
      var f = b[k];
      var pct = Math.round(f.score / f.max * 100);
      return '<div class="mb-3"><div class="flex justify-between text-xs mb-1">' +
        '<span class="text-slate-300 capitalize">' + k + '</span>' +
        '<span class="text-slate-400">' + f.score + '/' + f.max + '</span></div>' +
        '<div class="w-full h-2 rounded-full bg-slate-900 overflow-hidden">' +
          '<div class="h-full rounded-full ' + (pct >= 75 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-red-500') +
          '" style="width:' + pct + '%"></div></div>' +
        '<div class="text-xs text-slate-500 mt-0.5">' + f.detail + '</div></div>';
    }).join('');
}
```

**Step 3: Wire into fetchData**

In `fetchData()` (line 201), after `computeImpactScores();` add:
```javascript
    computeHealthScore();
```

**Step 4: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: system health score badge with drill-down breakdown (R21)"
```

---

## Task 6: Workflow State Machine Visualization (R22)

Render an issue type's state machine as a Cytoscape.js graph. States as nodes colored by category, edges from transitions, issue counts overlaid.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (header — add "Workflow" view button)
- Modify: `src/filigree/static/dashboard.html` (views section — add workflowView container)
- Modify: `src/filigree/static/dashboard.html` (script — add `loadWorkflow()`, `renderWorkflowGraph()`, update `switchView()`)

**Step 1: Add Workflow view button and container**

In the header view button group (line 48-53), add after the Activity button:

```html
<button id="btnWorkflow" onclick="switchView('workflow')" class="px-3 py-1 rounded text-xs font-medium">Workflow</button>
```

After the activity view container (line 139), add:

```html
<!-- Workflow view -->
<div id="workflowView" class="flex-1 hidden flex flex-col">
  <div class="flex items-center gap-2 px-4 py-1 bg-slate-800/50 text-xs text-slate-400 border-b border-slate-700">
    <label for="workflowType" class="text-xs text-slate-400">Type:</label>
    <select id="workflowType" onchange="loadWorkflow()" class="bg-slate-700 text-slate-200 text-xs rounded px-2 py-1 border border-slate-600">
      <option value="">Select type...</option>
    </select>
  </div>
  <div id="workflowCy" class="flex-1"></div>
</div>
```

**Step 2: Update `switchView()` to handle workflow**

Add `workflowView` toggle and `btnWorkflow` styling in `switchView()`.

**Step 3: Implement `loadWorkflow()` and `renderWorkflowGraph()`**

```javascript
async function loadWorkflow() {
  var typeName = document.getElementById('workflowType').value;
  if (!typeName) return;
  var resp = await fetch('/api/type/' + typeName);
  if (!resp.ok) return;
  var tpl = await resp.json();
  // Count issues per state
  var stateCounts = {};
  allIssues.filter(function(i) { return i.type === typeName; }).forEach(function(i) {
    stateCounts[i.status] = (stateCounts[i.status] || 0) + 1;
  });
  renderWorkflowGraph(tpl, stateCounts);
}

function renderWorkflowGraph(template, stateCounts) {
  var container = document.getElementById('workflowCy');
  var nodes = template.states.map(function(s) {
    var count = stateCounts[s.name] || 0;
    return { data: { id: s.name, label: s.name + (count ? ' (' + count + ')' : ''), category: s.category, count: count } };
  });
  var edges = template.transitions.map(function(t, i) {
    return { data: { id: 'wt' + i, source: t.from, target: t.to, enforcement: t.enforcement } };
  });
  if (window._workflowCy) window._workflowCy.destroy();
  window._workflowCy = cytoscape({
    container: container,
    elements: nodes.concat(edges),
    layout: { name: 'dagre', rankDir: 'LR', rankSep: 100, nodeSep: 60, padding: 30 },
    style: [
      { selector: 'node', style: {
        'label': 'data(label)', 'font-size': '12px', 'font-family': 'JetBrains Mono, monospace',
        'text-valign': 'center', 'text-halign': 'center', 'color': '#F1F5F9',
        'text-outline-color': '#0F172A', 'text-outline-width': 2,
        'width': 80, 'height': 40, 'shape': 'round-rectangle',
        'background-color': function(ele) { return CATEGORY_COLORS[ele.data('category')] || '#64748B'; },
      }},
      { selector: 'edge', style: {
        'width': 2, 'line-color': '#475569', 'target-arrow-color': '#475569',
        'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.8,
        'label': 'data(enforcement)', 'font-size': '9px', 'color': '#94A3B8',
        'text-rotation': 'autorotate', 'text-margin-y': -10,
      }},
    ],
    minZoom: 0.3, maxZoom: 3,
  });
  window._workflowCy.fit(undefined, 40);
}
```

**Step 4: Populate workflow type dropdown**

In `updateStats()`, after `populateTypeFilter()`, add:

```javascript
  var wfSelect = document.getElementById('workflowType');
  if (wfSelect && wfSelect.options.length <= 1) {
    var types = {}; allIssues.forEach(function(i) { types[i.type] = true; });
    Object.keys(types).sort().forEach(function(t) {
      var opt = document.createElement('option'); opt.value = t; opt.textContent = t;
      wfSelect.appendChild(opt);
    });
  }
```

**Step 5: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: workflow state machine visualization view (R22)"
```

---

## Task 7: Issue Creation Form (R23)

Add a "+" button that opens a modal form to create issues. Uses `/api/types` for type dropdown, existing `POST /api/issues` endpoint.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (header — add create button)
- Modify: `src/filigree/static/dashboard.html` (script — add `showCreateForm()`, `submitCreateForm()`)

**Step 1: Add create button to header**

In the header (line 46-47), after the Filigree title, add:

```html
<button onclick="showCreateForm()" class="text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700" title="Create Issue">+ New</button>
```

**Step 2: Implement create form modal**

```javascript
async function showCreateForm() {
  var existing = document.getElementById('createModal');
  if (existing) existing.remove();
  // Fetch types
  var types = [];
  try { var tr = await fetch('/api/types'); types = await tr.json(); } catch(e) {}
  var typeOpts = types.map(function(t) {
    return '<option value="' + t.type + '"' + (t.type === 'task' ? ' selected' : '') + '>' + t.display_name + '</option>';
  }).join('');

  var modal = document.createElement('div');
  modal.id = 'createModal';
  modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  modal.innerHTML = '<div class="bg-slate-800 rounded-lg border border-slate-600 p-5 w-96 shadow-xl max-h-[80vh] overflow-y-auto">' +
    '<div class="text-sm font-semibold text-slate-200 mb-3">Create Issue</div>' +
    '<div class="flex flex-col gap-3">' +
      '<div><label for="createTitle" class="text-xs text-slate-400">Title *</label>' +
        '<input id="createTitle" type="text" class="w-full bg-slate-700 text-slate-200 text-xs rounded px-3 py-2 border border-slate-600 mt-1 focus:outline-none focus:border-blue-500"></div>' +
      '<div class="flex gap-2">' +
        '<div class="flex-1"><label for="createType" class="text-xs text-slate-400">Type</label>' +
          '<select id="createType" class="w-full bg-slate-700 text-slate-200 text-xs rounded px-2 py-2 border border-slate-600 mt-1">' + typeOpts + '</select></div>' +
        '<div class="w-20"><label for="createPriority" class="text-xs text-slate-400">Priority</label>' +
          '<select id="createPriority" class="w-full bg-slate-700 text-slate-200 text-xs rounded px-2 py-2 border border-slate-600 mt-1">' +
            '<option value="0">P0</option><option value="1">P1</option><option value="2" selected>P2</option><option value="3">P3</option><option value="4">P4</option>' +
          '</select></div></div>' +
      '<div><label for="createDesc" class="text-xs text-slate-400">Description</label>' +
        '<textarea id="createDesc" rows="3" class="w-full bg-slate-700 text-slate-200 text-xs rounded px-3 py-2 border border-slate-600 mt-1 focus:outline-none focus:border-blue-500 resize-none"></textarea></div>' +
      '<div><label for="createAssignee" class="text-xs text-slate-400">Assignee</label>' +
        '<input id="createAssignee" type="text" class="w-full bg-slate-700 text-slate-200 text-xs rounded px-3 py-2 border border-slate-600 mt-1 focus:outline-none focus:border-blue-500"></div>' +
      '<div><label for="createLabels" class="text-xs text-slate-400">Labels (comma-separated)</label>' +
        '<input id="createLabels" type="text" class="w-full bg-slate-700 text-slate-200 text-xs rounded px-3 py-2 border border-slate-600 mt-1 focus:outline-none focus:border-blue-500"></div>' +
    '</div>' +
    '<div class="flex justify-end gap-2 mt-4">' +
      '<button onclick="document.getElementById(\'createModal\').remove()" class="text-xs bg-slate-700 px-3 py-1.5 rounded hover:bg-slate-600">Cancel</button>' +
      '<button onclick="submitCreateForm()" class="text-xs bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700">Create</button>' +
    '</div></div>';
  document.body.appendChild(modal);
  modal.onclick = function(e) { if (e.target === modal) modal.remove(); };
  document.getElementById('createTitle').focus();
}

async function submitCreateForm() {
  var title = document.getElementById('createTitle').value.trim();
  if (!title) { showToast('Title is required', 'error'); return; }
  var labelsRaw = document.getElementById('createLabels').value.trim();
  var labels = labelsRaw ? labelsRaw.split(',').map(function(l) { return l.trim(); }).filter(Boolean) : null;
  var body = {
    title: title,
    type: document.getElementById('createType').value,
    priority: parseInt(document.getElementById('createPriority').value),
    description: document.getElementById('createDesc').value.trim(),
    assignee: document.getElementById('createAssignee').value.trim(),
  };
  if (labels && labels.length) body.labels = labels;
  try {
    var resp = await fetch('/api/issues', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!resp.ok) { var err = await resp.json(); showToast('Error: ' + (err.error || 'Create failed'), 'error'); return; }
    var created = await resp.json();
    document.getElementById('createModal').remove();
    showToast('Created ' + created.id, 'success');
    await fetchData();
    openDetail(created.id);
  } catch (e) { showToast('Network error', 'error'); }
}
```

**Step 3: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: issue creation form modal (R23)"
```

---

## Task 8: Claim/Release Endpoints and UI (R24)

Add claim/release/claim-next API endpoints and integrate into the detail panel.

**Files:**
- Modify: `src/filigree/dashboard.py:339-361` (add claim/release/claim-next endpoints before `return app`)
- Modify: `src/filigree/static/dashboard.html` (detail panel actions — add claim/release buttons)
- Test: `tests/test_dashboard.py` (add TestClaimAPI)

**Step 1: Write the failing tests**

In `tests/test_dashboard.py`, add:

```python
class TestClaimAPI:
    async def test_claim_issue(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['a']}/claim",
            json={"assignee": "agent-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["assignee"] == "agent-1"

    async def test_release_claim(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        dashboard_db.claim_issue(ids["a"], assignee="agent-1")
        resp = await client.post(
            f"/api/issue/{ids['a']}/release",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["assignee"] == ""

    async def test_claim_next(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        resp = await client.post(
            "/api/claim-next",
            json={"assignee": "agent-2"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["assignee"] == "agent-2"

    async def test_claim_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issue/nonexistent/claim",
            json={"assignee": "x"},
        )
        assert resp.status_code == 404
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dashboard.py::TestClaimAPI -v`
Expected: FAIL (endpoints don't exist)

**Step 3: Add the API endpoints**

In `dashboard.py`, before `return app` (line 362), add:

```python
    @app.post("/api/issue/{issue_id}/claim")
    async def api_claim_issue(issue_id: str, request: Request) -> JSONResponse:
        """Claim an issue."""
        db = _get_db()
        body = await request.json()
        assignee = body.get("assignee", "")
        actor = body.get("actor", "dashboard")
        try:
            issue = db.claim_issue(issue_id, assignee=assignee, actor=actor)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @app.post("/api/issue/{issue_id}/release")
    async def api_release_claim(issue_id: str, request: Request) -> JSONResponse:
        """Release a claimed issue."""
        db = _get_db()
        body = await request.json()
        actor = body.get("actor", "dashboard")
        try:
            issue = db.release_claim(issue_id, actor=actor)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @app.post("/api/claim-next")
    async def api_claim_next(request: Request) -> JSONResponse:
        """Claim the highest-priority ready issue."""
        db = _get_db()
        body = await request.json()
        assignee = body.get("assignee", "")
        actor = body.get("actor", "dashboard")
        try:
            issue = db.claim_next(assignee, actor=actor)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())
```

**Step 4: Add claim/release buttons to detail panel**

In `openDetail()` in `dashboard.html`, in the actions section (after the close/reopen button, around line 906), add claim/release buttons:

```javascript
(statusCat !== 'done' && !d.assignee
  ? '<button onclick="claimIssue(\'' + d.id + '\')" class="text-xs bg-emerald-900/50 text-emerald-400 px-3 py-1 rounded border border-emerald-800 hover:bg-emerald-900 mb-2 ml-1">Claim</button>'
  : '') +
(d.assignee
  ? '<button onclick="releaseIssue(\'' + d.id + '\')" class="text-xs bg-slate-700 text-slate-300 px-3 py-1 rounded border border-slate-600 hover:bg-slate-600 mb-2 ml-1">Release</button>'
  : '') +
```

Add the JS functions:

```javascript
async function claimIssue(issueId) {
  var assignee = prompt('Claim as:') || '';
  if (!assignee) return;
  var resp = await fetch('/api/issue/' + issueId + '/claim', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({assignee: assignee}),
  });
  if (!resp.ok) { var err = await resp.json(); showToast('Error: ' + (err.error || 'Claim failed'), 'error'); return; }
  showToast('Claimed by ' + assignee, 'success');
  await fetchData();
  if (selectedIssue === issueId) openDetail(issueId);
}

async function releaseIssue(issueId) {
  var resp = await fetch('/api/issue/' + issueId + '/release', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({}),
  });
  if (!resp.ok) { var err = await resp.json(); showToast('Error: ' + (err.error || 'Release failed'), 'error'); return; }
  showToast('Issue released', 'success');
  await fetchData();
  if (selectedIssue === issueId) openDetail(issueId);
}
```

**Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: All tests pass

**Step 6: Commit**

```bash
git add src/filigree/dashboard.py src/filigree/static/dashboard.html tests/test_dashboard.py
git commit -m "feat: claim/release/claim-next API endpoints and UI (R24)"
```

---

## Task 9: Dependency Management Endpoints and UI (R25)

Add/remove dependency API endpoints and integrate into detail panel with searchable issue picker.

**Files:**
- Modify: `src/filigree/dashboard.py` (add dependency endpoints before `return app`)
- Modify: `src/filigree/static/dashboard.html` (detail panel — add/remove dep buttons)
- Test: `tests/test_dashboard.py` (add TestDependencyManagementAPI)

**Step 1: Write the failing tests**

```python
class TestDependencyManagementAPI:
    async def test_add_dependency(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['a']}/dependencies",
            json={"depends_on": ids["b"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] is True

    async def test_remove_dependency(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        # A already depends on epic (from populated_db fixture)
        dashboard_db.add_dependency(ids["a"], ids["b"])
        resp = await client.delete(
            f"/api/issue/{ids['a']}/dependencies/{ids['b']}",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] is True

    async def test_add_dep_cycle_detection(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        # Create A->B then try B->A (cycle)
        dashboard_db.add_dependency(ids["a"], ids["b"])
        resp = await client.post(
            f"/api/issue/{ids['b']}/dependencies",
            json={"depends_on": ids["a"]},
        )
        assert resp.status_code == 409

    async def test_add_dep_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issue/nonexistent/dependencies",
            json={"depends_on": "also-nonexistent"},
        )
        assert resp.status_code == 404
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dashboard.py::TestDependencyManagementAPI -v`
Expected: FAIL

**Step 3: Add API endpoints**

In `dashboard.py`, before `return app`:

```python
    @app.post("/api/issue/{issue_id}/dependencies")
    async def api_add_dependency(issue_id: str, request: Request) -> JSONResponse:
        """Add a dependency: issue_id depends on depends_on."""
        db = _get_db()
        body = await request.json()
        depends_on = body.get("depends_on", "")
        actor = body.get("actor", "dashboard")
        try:
            added = db.add_dependency(issue_id, depends_on, actor=actor)
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse({"added": added})

    @app.delete("/api/issue/{issue_id}/dependencies/{dep_id}")
    async def api_remove_dependency(issue_id: str, dep_id: str, request: Request) -> JSONResponse:
        """Remove a dependency."""
        db = _get_db()
        try:
            removed = db.remove_dependency(issue_id, dep_id)
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        return JSONResponse({"removed": removed})
```

**Step 4: Add UI in detail panel**

In the detail panel's blocked_by section, add a remove button on each dependency row and an "Add blocker" button at the end:

- Each blocked_by row gets a `[x]` button that calls `removeDependency(issueId, bid)`
- After the blocked_by list, add: `<button onclick="showAddBlocker('${d.id}')" class="text-xs text-blue-400 hover:underline mt-1">+ Add blocker</button>`
- `showAddBlocker(issueId)` opens a mini-modal with a search input that calls `/api/search` and shows clickable results
- `removeDependency(issueId, depId)` calls `DELETE /api/issue/{issueId}/dependencies/{depId}`

**Step 5: Run tests**

Run: `python -m pytest tests/test_dashboard.py -x -q`
Expected: All pass

**Step 6: Commit**

```bash
git add src/filigree/dashboard.py src/filigree/static/dashboard.html tests/test_dashboard.py
git commit -m "feat: dependency management API and UI with search picker (R25)"
```

---

## Task 10: Saved Filter Presets (R26)

Save/load named filter combinations in localStorage.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (header — preset dropdown + save button)
- Modify: `src/filigree/static/dashboard.html` (script — `savePreset()`, `loadPreset()`, `deletePreset()`)

**Step 1: Add preset controls to header filter bar**

After the filter bar checkboxes (line 77), add:

```html
<select id="filterPreset" onchange="loadPreset()" class="bg-slate-700 text-slate-200 text-xs rounded px-2 py-1 border border-slate-600">
  <option value="">Presets...</option>
</select>
<button onclick="savePreset()" class="text-xs bg-slate-700 text-slate-400 px-2 py-1 rounded border border-slate-600 hover:bg-slate-600" title="Save current filters as preset">Save</button>
```

**Step 2: Implement preset functions**

```javascript
function getFilterState() {
  return {
    open: document.getElementById('filterOpen').checked,
    active: document.getElementById('filterInProgress').checked,
    closed: document.getElementById('filterClosed').checked,
    priority: document.getElementById('filterPriority').value,
    ready: readyFilter,
    blocked: blockedFilter,
    search: document.getElementById('filterSearch').value,
  };
}

function applyFilterState(state) {
  document.getElementById('filterOpen').checked = state.open;
  document.getElementById('filterInProgress').checked = state.active;
  document.getElementById('filterClosed').checked = state.closed;
  document.getElementById('filterPriority').value = state.priority;
  readyFilter = state.ready;
  blockedFilter = state.blocked;
  if (state.search) { document.getElementById('filterSearch').value = state.search; doSearch(); }
  else { document.getElementById('filterSearch').value = ''; searchResults = null; }
  render();
}

function savePreset() {
  var name = prompt('Preset name:');
  if (!name) return;
  var presets = JSON.parse(localStorage.getItem('filigree_presets') || '{}');
  presets[name] = getFilterState();
  localStorage.setItem('filigree_presets', JSON.stringify(presets));
  populatePresets();
  showToast('Preset "' + name + '" saved', 'success');
}

function loadPreset() {
  var name = document.getElementById('filterPreset').value;
  if (!name) return;
  var presets = JSON.parse(localStorage.getItem('filigree_presets') || '{}');
  if (presets[name]) applyFilterState(presets[name]);
  document.getElementById('filterPreset').value = '';
}

function populatePresets() {
  var select = document.getElementById('filterPreset');
  if (!select) return;
  var presets = JSON.parse(localStorage.getItem('filigree_presets') || '{}');
  select.innerHTML = '<option value="">Presets...</option>';
  Object.keys(presets).sort().forEach(function(name) {
    var opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    select.appendChild(opt);
  });
}
```

Call `populatePresets()` in the init section.

**Step 3: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: saved filter presets with localStorage (R26)"
```

---

## Task 11: Throughput Sparkline (R27) and Stale Issue Alerts (R29)

Add a sparkline in the footer showing 14-day throughput trend, and a persistent notification badge for stale issues.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (footer — sparkline + stale badge)
- Modify: `src/filigree/static/dashboard.html` (script — `renderSparkline()`, `updateStaleBadge()`)

**Step 1: Add sparkline canvas and stale badge to footer**

In the footer (lines 148-154), add:

```html
<canvas id="sparkline" width="100" height="20" title="14-day throughput trend"></canvas>
<span id="staleBadge" class="hidden text-xs bg-red-900/50 text-red-400 px-2 py-0.5 rounded border border-red-800 cursor-pointer" onclick="showStaleIssues()"></span>
```

**Step 2: Implement sparkline rendering**

The sparkline uses activity events to compute issues closed per day over the last 14 days:

```javascript
async function renderSparkline() {
  try {
    var resp = await fetch('/api/activity?limit=500');
    var events = await resp.json();
    var closedByDay = {};
    var now = Date.now();
    events.forEach(function(e) {
      if (e.event_type !== 'closed' && e.event_type !== 'status_changed') return;
      if (e.event_type === 'status_changed' && e.new_value !== 'closed') return;
      var dayAgo = Math.floor((now - new Date(e.created_at).getTime()) / 86400000);
      if (dayAgo < 14) closedByDay[dayAgo] = (closedByDay[dayAgo] || 0) + 1;
    });
    var data = [];
    for (var i = 13; i >= 0; i--) data.push(closedByDay[i] || 0);
    var canvas = document.getElementById('sparkline');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    var max = Math.max.apply(null, data.concat([1]));
    ctx.strokeStyle = '#3B82F6'; ctx.lineWidth = 1.5; ctx.beginPath();
    data.forEach(function(v, i) {
      var x = i / (data.length - 1) * w;
      var y = h - (v / max * (h - 4)) - 2;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  } catch (e) {}
}
```

**Step 3: Implement stale issue badge**

```javascript
function updateStaleBadge() {
  var stale = allIssues.filter(function(i) {
    return (i.status_category || 'open') === 'wip' && i.updated_at &&
      (Date.now() - new Date(i.updated_at).getTime()) > 2 * 3600000;
  });
  var badge = document.getElementById('staleBadge');
  if (!badge) return;
  if (stale.length) {
    badge.textContent = stale.length + ' stale';
    badge.classList.remove('hidden');
  } else { badge.classList.add('hidden'); }
  window._staleIssues = stale;
}

function showStaleIssues() {
  var stale = window._staleIssues || [];
  if (!stale.length) return;
  // Show the first stale issue in detail
  openDetail(stale[0].id);
}
```

Wire `updateStaleBadge()` and `renderSparkline()` into `fetchData()`.

**Step 4: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: throughput sparkline and stale issue alerts (R27, R29)"
```

---

## Task 12: Dark/Light Theme Toggle (R30)

Add a theme toggle that switches between dark (current) and light mode. Uses Tailwind `dark:` utilities pattern and persists choice in localStorage.

**Files:**
- Modify: `src/filigree/static/dashboard.html:2` (html tag — add class for theme)
- Modify: `src/filigree/static/dashboard.html:11-30` (CSS — light theme overrides)
- Modify: `src/filigree/static/dashboard.html:80-85` (header — add toggle button)
- Modify: `src/filigree/static/dashboard.html` (script — `toggleTheme()`)

**Step 1: Add light theme CSS**

Since Tailwind CDN doesn't support compile-time `dark:` utilities easily, use a CSS class approach. Add to the `<style>` block:

```css
  body.light { background: #F8FAFC; color: #1E293B; }
  .light .bg-slate-800 { background: #FFFFFF !important; }
  .light .bg-slate-700 { background: #F1F5F9 !important; }
  .light .bg-slate-900 { background: #E2E8F0 !important; }
  .light .text-slate-200, .light .text-slate-100, .light .text-slate-300 { color: #1E293B !important; }
  .light .text-slate-400 { color: #64748B !important; }
  .light .border-slate-700, .light .border-slate-600 { border-color: #CBD5E1 !important; }
  .light .card:hover { background: #F1F5F9 !important; }
  .light #cy { background: #F8FAFC; }
```

**Step 2: Add toggle button to header**

In the header stats area (line 80), add:

```html
<button onclick="toggleTheme()" id="themeToggle" class="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600" title="Toggle theme">&#9788;</button>
```

**Step 3: Implement toggle function**

```javascript
function toggleTheme() {
  var isLight = document.body.classList.toggle('light');
  localStorage.setItem('filigree_theme', isLight ? 'light' : 'dark');
  document.getElementById('themeToggle').textContent = isLight ? '\u263E' : '\u2606';
}
// Init theme from localStorage
(function() {
  var saved = localStorage.getItem('filigree_theme');
  if (saved === 'light') { document.body.classList.add('light'); }
})();
```

**Step 4: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat: dark/light theme toggle with localStorage persistence (R30)"
```

---

## Task 13: UX Minor Fixes Batch

Address remaining UX minor issues: base font size, empty states, search box width, aria-expanded on epics, keyboard shortcut legend.

**Files:**
- Modify: `src/filigree/static/dashboard.html` (various locations)

**Step 1: Increase base font from `text-sm` to `text-sm` (13px)**

On line 41, the body has `text-sm`. Add to CSS:
```css
  body { font-size: 13px; }
```

**Step 2: Add empty states**

In `renderStandardKanban()`, when a column has 0 issues, render:
```html
<div class="text-xs text-slate-600 italic p-2">No issues</div>
```

In `loadMetrics()`, the empty state already exists. In `loadActivity()`, the empty state already exists.

**Step 3: Widen search box**

Change `w-40` on the search input (line 71) to `w-56`.

**Step 4: Add aria-expanded to epic clusters**

In `renderClusterCard()`, add `aria-expanded="${expanded}"` to the epic container div.

**Step 5: Add keyboard shortcut legend**

Add a `?` shortcut that shows a help modal listing all shortcuts:

```javascript
if (e.key === '?' && !e.shiftKey) {
  // Show help
  var existing = document.getElementById('helpModal');
  if (existing) { existing.remove(); return; }
  var modal = document.createElement('div');
  modal.id = 'helpModal';
  modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  modal.onclick = function(ev) { if (ev.target === modal) modal.remove(); };
  modal.innerHTML = '<div class="bg-slate-800 rounded-lg border border-slate-600 p-5 w-80 shadow-xl">' +
    '<div class="text-sm font-semibold text-slate-200 mb-3">Keyboard Shortcuts</div>' +
    '<div class="text-xs text-slate-300 space-y-1">' +
    '<div><kbd class="bg-slate-700 px-1 rounded">/</kbd> Focus search</div>' +
    '<div><kbd class="bg-slate-700 px-1 rounded">Esc</kbd> Close panel / clear search</div>' +
    '<div><kbd class="bg-slate-700 px-1 rounded">j</kbd> / <kbd class="bg-slate-700 px-1 rounded">k</kbd> Navigate cards</div>' +
    '<div><kbd class="bg-slate-700 px-1 rounded">Enter</kbd> Open issue detail</div>' +
    '<div><kbd class="bg-slate-700 px-1 rounded">c</kbd> Focus comment input</div>' +
    '<div><kbd class="bg-slate-700 px-1 rounded">x</kbd> Close current issue</div>' +
    '<div><kbd class="bg-slate-700 px-1 rounded">?</kbd> This help</div>' +
    '</div>' +
    '<button onclick="document.getElementById(\'helpModal\').remove()" class="text-xs text-slate-500 mt-3 hover:text-slate-300">Close</button>' +
    '</div>';
  document.body.appendChild(modal);
  return;
}
```

**Step 6: Verify and commit**

Run: `python -m pytest tests/test_dashboard.py -x -q`

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix: UX minor — empty states, search width, aria-expanded, keyboard legend"
```

---

## Task 14: Final Integration Test and Documentation

Update the dashboard module docstring, add dashboard section to CLI docs, run full test suite.

**Files:**
- Modify: `src/filigree/dashboard.py:1-9` (update module docstring)
- Modify: `docs/cli.md` (add dashboard section)
- Test: full suite

**Step 1: Update dashboard.py docstring**

Replace lines 1-9 with:

```python
"""Web dashboard for filigree — interactive project management UI.

Full-featured local web server: kanban board, dependency graph, metrics,
activity feed, workflow visualization. Supports issue management (create,
update, close, reopen, claim, dependency management), batch operations,
and real-time auto-refresh.

Usage:
    filigree dashboard                    # Opens browser at localhost:8377
    filigree dashboard --port 9000        # Custom port
    filigree dashboard --no-browser       # Skip auto-open
"""
```

**Step 2: Add dashboard section to docs/cli.md**

At the end of `docs/cli.md`, add a new section:

```markdown
## Dashboard

```bash
filigree dashboard                    # Opens browser at localhost:8377
filigree dashboard --port 9000        # Custom port
filigree dashboard --no-browser       # Skip auto-open
```

### `dashboard`

Launch an interactive web dashboard at `http://localhost:8377`. Features:

| View | Description |
|------|-------------|
| Kanban | Three-column (open/wip/done) board with cards. Cluster mode groups by epic. |
| Graph | Cytoscape.js dependency graph with critical path overlay |
| Metrics | Throughput, cycle time, lead time from `analytics.py` |
| Activity | Chronological event feed across all issues |
| Workflow | State machine visualization for any issue type |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--port` | int | 8377 | Port to serve on |
| `--no-browser` | flag | false | Don't auto-open browser |

The dashboard connects to the `.filigree/` database in the current directory (or nearest parent). All write operations (status changes, comments, etc.) record `"dashboard"` as the actor in the audit trail.
```

**Step 3: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: All tests pass

**Step 4: Commit**

```bash
git add src/filigree/dashboard.py docs/cli.md
git commit -m "docs: update dashboard documentation and module docstring"
```

---

## Summary

| Task | Description | Type | Files |
|------|-------------|------|-------|
| 1 | Touch targets, form labels, contrast | UX Critical Fix | dashboard.html |
| 2 | Toast notifications replace alert() | UX Major Fix | dashboard.html |
| 3 | Loading states and focus indicators | UX Major Fix | dashboard.html |
| 4 | Responsive layout and panel width | UX Major + R28 | dashboard.html |
| 5 | System health score (R21) | Feature | dashboard.html |
| 6 | Workflow state machine viz (R22) | Feature | dashboard.html |
| 7 | Issue creation form (R23) | Feature | dashboard.html |
| 8 | Claim/release endpoints + UI (R24) | Feature | dashboard.py, dashboard.html, tests |
| 9 | Dependency management (R25) | Feature | dashboard.py, dashboard.html, tests |
| 10 | Saved filter presets (R26) | Feature | dashboard.html |
| 11 | Sparkline + stale alerts (R27, R29) | Feature | dashboard.html |
| 12 | Dark/light theme toggle (R30) | Feature | dashboard.html |
| 13 | UX minor fixes batch | UX Minor Fix | dashboard.html |
| 14 | Documentation + integration test | Docs | dashboard.py, cli.md |
