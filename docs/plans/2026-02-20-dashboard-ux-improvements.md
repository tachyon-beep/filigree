# Dashboard UX Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement 7 UX improvements to the filigree dashboard identified by UX review, focusing on Kanban column sizing, drag-and-drop, header density, and accessibility fixes.

**Architecture:** All changes are in one file: `src/filigree/static/dashboard.html` (~2300 lines, vanilla JS + Tailwind CDN). CSS changes in the `<style>` block (lines 11-61), HTML structure (lines 72-284), JS logic (lines 286+). No backend changes needed — all required API endpoints exist. Tests use httpx AsyncClient against the FastAPI app in `tests/test_dashboard.py`.

**Tech Stack:** Vanilla JS, Tailwind CSS (CDN), HTML5 Drag and Drop API, FastAPI (read-only for this work)

**Design doc:** `docs/plans/2026-02-20-dashboard-ux-improvements-design.md`

---

### Task 1: Kanban Column Equal Width

**Files:**
- Modify: `src/filigree/static/dashboard.html:23` (CSS `.kanban-col`)
- Modify: `src/filigree/static/dashboard.html:50` (1200px breakpoint)
- Modify: `src/filigree/static/dashboard.html:576,611,777` (remove `shrink-0` from column divs)
- Modify: `src/filigree/static/dashboard.html:582-587,617-618,783-784` (add min-height to card list containers)

**Step 1: Update `.kanban-col` CSS rule**

At line 23, change:
```css
.kanban-col { min-width: 320px; }
```
to:
```css
.kanban-col { min-width: 280px; flex: 1 1 0; }
```

This makes all columns share available space equally. The `flex: 1 1 0` means: grow equally (`1`), shrink equally (`1`), from a zero base (`0`), with `min-width` preventing collapse below 280px. The board container at line 220 already has `overflow-x-auto` for horizontal scroll when columns exceed viewport.

**Step 2: Update 1200px breakpoint**

At line 50, change:
```css
.kanban-col { min-width: 280px; }
```
to:
```css
.kanban-col { min-width: 260px; }
```

This gives slightly more breathing room on medium screens.

**Step 3: Remove `shrink-0` from column divs**

In `renderStandardKanban()` (line 576), change:
```js
return '<div class="kanban-col flex flex-col shrink-0">' +
```
to:
```js
return '<div class="kanban-col flex flex-col">' +
```

Repeat the same change in `renderClusterKanban()` (line 611) and `renderTypeKanban()` (line 777). All three render functions build the same column structure.

The `shrink-0` class conflicts with `flex: 1 1 0` — it prevents columns from sharing space because it sets `flex-shrink: 0`. Removing it lets the CSS rule control sizing.

**Step 4: Add minimum height to empty column containers**

In `renderStandardKanban()`, the card list `<div>` at line 582 needs a min-height class. Change:
```js
'<div class="flex flex-col gap-2 overflow-y-auto scrollbar-thin pr-1" style="max-height: calc(100vh - 160px);">' +
```
to:
```js
'<div class="flex flex-col gap-2 overflow-y-auto scrollbar-thin pr-1 min-h-[200px]" style="max-height: calc(100vh - 160px);">' +
```

Apply the same `min-h-[200px]` addition to the card list container in `renderClusterKanban()` (line 617) and `renderTypeKanban()` (line 783).

**Step 5: Verify visually**

Open `http://localhost:8377` in a browser. Confirm:
- All three Kanban columns (Open, In Progress, Done) are the same width
- Empty columns maintain their width and have visible vertical space
- Horizontal scroll appears when detail panel is open and columns are squeezed
- Type-filtered view with many columns scrolls horizontally

**Step 6: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix(dashboard): equal-width Kanban columns regardless of content"
```

---

### Task 2: Kanban Drag and Drop — CSS and Data Attributes

**Files:**
- Modify: `src/filigree/static/dashboard.html:23` (add drag CSS classes)
- Modify: `src/filigree/static/dashboard.html:576,611,777` (add `data-status-category` / `data-status` to columns)
- Modify: `src/filigree/static/dashboard.html:688` (add `draggable` to cards)

**Step 1: Add drag-and-drop CSS classes**

After the `.kanban-col` rule at line 23, add these new CSS rules:

```css
.kanban-col.drag-valid { outline: 2px dashed #3B82F6; outline-offset: -2px; background: rgba(59,130,246,0.05); }
.kanban-col.drag-invalid { opacity: 0.3; cursor: not-allowed; }
.card[draggable="true"] { cursor: grab; }
.card[draggable="true"]:active { cursor: grabbing; }
```

**Step 2: Add `data-status-category` to standard/cluster column divs**

In `renderStandardKanban()` (line 576), change:
```js
return '<div class="kanban-col flex flex-col">' +
```
to:
```js
return '<div class="kanban-col flex flex-col" data-status-category="' + col.key + '">' +
```

Apply the same in `renderClusterKanban()` (line 611).

**Step 3: Add `data-status` to type-filtered column divs**

In `renderTypeKanban()` (line 777), change:
```js
return '<div class="kanban-col flex flex-col">' +
```
to:
```js
return '<div class="kanban-col flex flex-col" data-status="' + state.name + '" data-status-category="' + state.category + '">' +
```

**Step 4: Add `draggable` to cards in standard and type-filtered modes**

In `renderCard()` (line 688), change:
```js
return '<div class="card bg-slate-800 rounded border border-slate-700 p-3 cursor-pointer ' +
    readyClass + ' ' + agingClass + ' ' + changedClass + '" tabindex="0" data-id="' + issue.id + '" onclick="openDetail(\'' + issue.id + '\')">' +
```
to:
```js
var isDraggable = kanbanMode !== 'cluster' && !multiSelectMode;
return '<div class="card bg-slate-800 rounded border border-slate-700 p-3 cursor-pointer ' +
    readyClass + ' ' + agingClass + ' ' + changedClass + '"' +
    (isDraggable ? ' draggable="true"' : '') +
    ' tabindex="0" data-id="' + issue.id + '" onclick="openDetail(\'' + issue.id + '\')">' +
```

Cards are draggable in Standard and Type-filtered modes, but not in Cluster mode (nested cards) or multi-select mode (batch operations).

**Step 5: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add drag-and-drop data attributes and CSS for Kanban"
```

---

### Task 3: Kanban Drag and Drop — Event Handlers

**Files:**
- Modify: `src/filigree/static/dashboard.html` — add new JS section after the `renderTypeKanban()` function (after line 787)

**Step 1: Add drag state variables**

After the `typeTemplate` variable declaration (line 307), add:
```js
var _dragIssueId = null;       // Issue being dragged
var _dragTransitions = [];     // Valid transitions for dragged issue
```

**Step 2: Add drag-and-drop handler functions**

After the `renderTypeKanban()` function closing brace (after line 787), add a new section:

```js
// ---------------------------------------------------------------------------
// Drag and Drop (Kanban)
// ---------------------------------------------------------------------------
function initDragAndDrop() {
  var board = document.getElementById('kanbanBoard');
  if (!board) return;

  board.addEventListener('dragstart', function(e) {
    var card = e.target.closest('.card[draggable="true"]');
    if (!card) return;
    _dragIssueId = card.getAttribute('data-id');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', _dragIssueId);
    card.style.opacity = '0.5';

    // Fetch valid transitions, then mark columns
    loadTransitions(_dragIssueId).then(function(transitions) {
      _dragTransitions = transitions;
      var validCategories = new Set();
      var validStatuses = new Set();
      transitions.forEach(function(t) {
        if (t.ready) {
          validStatuses.add(t.to);
          validCategories.add(t.category);
        }
      });
      var cols = board.querySelectorAll('.kanban-col');
      cols.forEach(function(col) {
        var colStatus = col.getAttribute('data-status');
        var colCat = col.getAttribute('data-status-category');
        var isValid = colStatus ? validStatuses.has(colStatus) : validCategories.has(colCat);
        // Don't mark the source column
        var sourceIssue = issueMap[_dragIssueId];
        var isSameCol = colStatus
          ? colStatus === (sourceIssue && sourceIssue.status)
          : colCat === (sourceIssue && sourceIssue.status_category);
        if (isSameCol) return;
        col.classList.add(isValid ? 'drag-valid' : 'drag-invalid');
      });
    });
  });

  board.addEventListener('dragover', function(e) {
    var col = e.target.closest('.kanban-col');
    if (!col || col.classList.contains('drag-invalid')) return;
    if (col.classList.contains('drag-valid')) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
    }
  });

  board.addEventListener('drop', function(e) {
    e.preventDefault();
    var col = e.target.closest('.kanban-col');
    if (!col || !col.classList.contains('drag-valid') || !_dragIssueId) return;

    var targetStatus = col.getAttribute('data-status');
    if (!targetStatus) {
      // Standard board: find first ready transition matching this category
      var targetCat = col.getAttribute('data-status-category');
      var match = _dragTransitions.find(function(t) { return t.ready && t.category === targetCat; });
      if (match) targetStatus = match.to;
    }
    if (!targetStatus) return;

    var issueId = _dragIssueId;
    showToast('Moving to ' + targetStatus + '...', 'info');

    fetch(API_BASE + '/issue/' + issueId, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({status: targetStatus}),
    }).then(function(resp) {
      if (resp.ok) {
        showToast('Moved to ' + targetStatus, 'success');
        fetchData();
      } else {
        return resp.json().then(function(err) {
          showToast('Error: ' + (err.error || 'Move failed'), 'error');
        });
      }
    }).catch(function() {
      showToast('Network error', 'error');
    });
  });

  board.addEventListener('dragend', function(e) {
    // Clean up all drag visual states
    var card = e.target.closest('.card');
    if (card) card.style.opacity = '';
    _dragIssueId = null;
    _dragTransitions = [];
    board.querySelectorAll('.kanban-col').forEach(function(col) {
      col.classList.remove('drag-valid', 'drag-invalid');
    });
  });
}
```

**Step 3: Call `initDragAndDrop()` after each render**

In the `renderKanban()` function (line 531), add `initDragAndDrop();` at the end. Find the function and add the call right before the closing brace. The function is safe to call repeatedly — it uses event delegation on the board container, so we only need to add it once. Actually, since `renderKanban()` replaces `innerHTML`, the column elements change on each render. But we use event delegation on `#kanbanBoard` which persists. So we should call `initDragAndDrop()` once on page load, not on every render.

Change approach: call `initDragAndDrop()` once during initialization. Find the `init()` or `DOMContentLoaded` section. Look for where `fetchData()` is first called.

Find the initialization section (search for `fetchData()` call outside of functions). Add `initDragAndDrop();` right after the initial `fetchData()` call.

**Step 4: Add `m` keyboard shortcut for accessible move**

In the keyboard handler (line 1244, the `if (selectedIssue)` block), add after the `x` shortcut:

```js
    if (e.key === 'm') {
      e.preventDefault();
      loadTransitions(selectedIssue).then(function(transitions) {
        var ready = transitions.filter(function(t) { return t.ready; });
        if (!ready.length) { showToast('No valid transitions', 'info'); return; }
        var modal = document.createElement('div');
        modal.id = 'moveModal';
        modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
        modal.onclick = function(ev) { if (ev.target === modal) modal.remove(); };
        modal.innerHTML = '<div class="bg-slate-800 rounded-lg border border-slate-600 p-4 w-64 shadow-xl">' +
          '<div class="text-sm text-slate-200 mb-3">Move to...</div>' +
          '<div class="flex flex-col gap-2">' +
          ready.map(function(t) {
            return '<button onclick="moveIssueTo(\'' + selectedIssue + '\',\'' + t.to + '\')" class="text-xs text-left bg-slate-700 px-3 py-2 rounded hover:bg-slate-600 text-slate-200">' + t.to + '</button>';
          }).join('') +
          '</div>' +
          '<button onclick="document.getElementById(\'moveModal\').remove()" class="text-xs text-slate-500 mt-3 hover:text-slate-300">Cancel (Esc)</button>' +
        '</div>';
        document.body.appendChild(modal);
        modal.querySelector('button').focus();
        modal.addEventListener('keydown', function(ev) { if (ev.key === 'Escape') modal.remove(); });
      });
    }
```

**Step 5: Add `moveIssueTo()` helper function**

Add this near the other issue-action functions (after `confirmClaim`, around line 1488):

```js
async function moveIssueTo(issueId, targetStatus) {
  var modal = document.getElementById('moveModal');
  if (modal) modal.remove();
  var resp = await fetch(API_BASE + '/issue/' + issueId, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: targetStatus}),
  });
  if (!resp.ok) { var err = await resp.json(); showToast('Error: ' + (err.error || 'Move failed'), 'error'); return; }
  showToast('Moved to ' + targetStatus, 'success');
  await fetchData();
  if (selectedIssue === issueId) openDetail(issueId);
}
```

**Step 6: Update keyboard help modal**

In the help modal HTML (around line 1213), add a new shortcut line after the `x` shortcut:
```js
'<div><kbd class="bg-slate-700 px-1 rounded">m</kbd> Move issue to status (in detail)</div>' +
```

**Step 7: Verify visually**

Open `http://localhost:8377`. Test:
- Drag a card from Open → In Progress column. Confirm toast "Moved to in_progress" appears and card moves.
- Drag a card to an invalid column (e.g., Open → Done if transition isn't allowed). Confirm the Done column is dimmed and won't accept the drop.
- In Cluster mode, confirm cards are NOT draggable.
- Open a card with `Enter`, press `m`, confirm move modal appears with valid transitions.

**Step 8: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add drag-and-drop between Kanban columns with validation"
```

---

### Task 4: Header Density Reduction

**Files:**
- Modify: `src/filigree/static/dashboard.html:131-138` (remove header stats container)
- Modify: `src/filigree/static/dashboard.html:377-385` (remove `updateStats` header references)

**Step 1: Remove header stats container**

Remove the entire div at lines 131-139. This is the `<div class="flex items-center gap-3 text-xs text-slate-400">` that contains the theme toggle, refresh indicator, health badge, and the three stat spans. BUT — keep the theme toggle, refresh indicator, and health badge. Only remove the three stat spans.

Change lines 136-138 from:
```html
    <span>Open: <b id="statOpen" class="text-slate-200" aria-live="polite">0</b></span>
    <span>Active: <b id="statActive" class="text-slate-200" aria-live="polite">0</b></span>
    <span>Ready: <b id="statReady" class="text-emerald-400" aria-live="polite">0</b></span>
```
to nothing (delete these three lines).

**Step 2: Remove JS references to removed elements**

In `updateStats()` (lines 383-385), remove:
```js
  document.getElementById('statOpen').textContent = open;
  document.getElementById('statActive').textContent = active;
  document.getElementById('statReady').textContent = s.ready_count;
```

**Step 3: Verify**

Reload dashboard. Confirm:
- Header is noticeably less dense
- Footer still shows Open, Active, Ready, Blocked, Deps counts
- No JS console errors from missing `statOpen`/`statActive`/`statReady` elements

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix(dashboard): remove duplicate stats from header — footer has the full set"
```

---

### Task 5: Type-Filter / Mode Toggle Conflict

**Files:**
- Modify: `src/filigree/static/dashboard.html:510-519` (`switchKanbanMode`)
- Modify: `src/filigree/static/dashboard.html:750-771` (`applyTypeFilter`)
- Modify: `src/filigree/static/dashboard.html:195-201` (Kanban sub-header HTML)

**Step 1: Add "Filtered: [type] x" pill to Kanban sub-header**

At line 201, after the `</select>` for `filterType`, add:
```html
<span id="typeFilterPill" class="hidden text-xs bg-blue-900/50 text-blue-400 px-2 py-0.5 rounded border border-blue-800">
  <span id="typeFilterLabel"></span>
  <button onclick="clearTypeFilter()" class="ml-1 text-blue-300 hover:text-white" title="Clear type filter" aria-label="Clear type filter">&times;</button>
</span>
```

**Step 2: Update `switchKanbanMode()` to clear type filter**

In `switchKanbanMode()` (line 510), add at the top of the function, before `kanbanMode = mode;`:
```js
  typeTemplate = null;
  var typeSelect = document.getElementById('filterType');
  if (typeSelect) typeSelect.value = '';
  updateTypeFilterUI(false);
```

**Step 3: Update `applyTypeFilter()` to dim mode buttons**

At the end of `applyTypeFilter()` (after `renderKanban();` at line 770), add:
```js
  updateTypeFilterUI(!!typeTemplate);
```

**Step 4: Add `updateTypeFilterUI()` and `clearTypeFilter()` helpers**

Add after `applyTypeFilter()`:
```js
function updateTypeFilterUI(isFiltered) {
  var btnStd = document.getElementById('btnStandard');
  var btnClust = document.getElementById('btnCluster');
  var pill = document.getElementById('typeFilterPill');
  var label = document.getElementById('typeFilterLabel');
  if (isFiltered && typeTemplate) {
    btnStd.classList.add('opacity-50', 'pointer-events-none');
    btnClust.classList.add('opacity-50', 'pointer-events-none');
    pill.classList.remove('hidden');
    label.textContent = typeTemplate.type;
  } else {
    btnStd.classList.remove('opacity-50', 'pointer-events-none');
    btnClust.classList.remove('opacity-50', 'pointer-events-none');
    pill.classList.add('hidden');
  }
}

function clearTypeFilter() {
  typeTemplate = null;
  var typeSelect = document.getElementById('filterType');
  if (typeSelect) typeSelect.value = '';
  updateTypeFilterUI(false);
  renderKanban();
}
```

**Step 5: Verify**

- Select a type in the dropdown → Standard/Cluster buttons should dim, pill appears
- Click the x on the pill → filter clears, buttons re-enable
- Click Standard or Cluster while type-filtered → type filter clears, mode switches

**Step 6: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix(dashboard): resolve type-filter and mode toggle conflict with visual feedback"
```

---

### Task 6: Status Badge Contrast and Priority Labels

**Files:**
- Modify: `src/filigree/static/dashboard.html:662-714` (`renderCard()`)

**Step 1: Fix status badge contrast in `renderCard()`**

In `renderCard()`, line 700, change:
```js
'<span class="rounded px-1" style="background:' + catColor + ';color:white">' + issue.status + '</span>' +
```
to:
```js
'<span class="rounded px-1" style="background:' + catColor + '33;color:' + catColor + '">' + issue.status + '</span>' +
```

The `33` suffix on the hex color adds 20% opacity (0x33 = 51/255 ≈ 20%). This creates a tinted background with the category color as text — much better contrast than white-on-color at small sizes.

**Step 2: Replace priority dot with text for P0/P1**

In `renderCard()`, lines 693-694, change:
```js
'<span class="w-2 h-2 rounded-full shrink-0" style="background:' + prioColor +
    '" title="Priority ' + issue.priority + ' (' + ['Critical','High','Medium','Low','Backlog'][issue.priority] + ')"></span>' +
```
to:
```js
(issue.priority <= 1
  ? '<span class="text-xs font-bold shrink-0" style="color:' + prioColor + '" title="Priority ' + issue.priority + ' (' + ['Critical','High','Medium','Low','Backlog'][issue.priority] + ')">P' + issue.priority + '</span>'
  : '<span class="w-2 h-2 rounded-full shrink-0" style="background:' + prioColor + '" title="Priority ' + issue.priority + ' (' + ['Critical','High','Medium','Low','Backlog'][issue.priority] + ')"></span>') +
```

P0 shows as red `P0` text, P1 as orange `P1` text. P2-P4 keep the small color dot.

**Step 3: Verify**

- Open status badges should show tinted background with colored text (not white on solid)
- P0 issues should show `P0` in red text
- P1 issues should show `P1` in orange text
- P2+ issues should show the small dot as before

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix(dashboard): improve status badge contrast and add P0/P1 text labels"
```

---

### Task 7: Minor Fixes — Stale Issues, Workflow Empty State, Transitions, Claim Modal

**Files:**
- Modify: `src/filigree/static/dashboard.html:2276-2280` (`showStaleIssues`)
- Modify: `src/filigree/static/dashboard.html:1948-1962` (`loadWorkflow`)
- Modify: `src/filigree/static/dashboard.html:1117-1125` (transition buttons)
- Modify: `src/filigree/static/dashboard.html:1453-1471` (`claimIssue` modal)

**Step 1: Fix `showStaleIssues()` to show all stale issues**

Replace the function at lines 2276-2280:
```js
function showStaleIssues() {
  var stale = window._staleIssues || [];
  if (!stale.length) return;
  openDetail(stale[0].id);
}
```
with:
```js
function showStaleIssues() {
  var stale = window._staleIssues || [];
  if (!stale.length) return;
  if (stale.length === 1) { openDetail(stale[0].id); return; }
  var modal = document.createElement('div');
  modal.id = 'staleModal';
  modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  modal.onclick = function(ev) { if (ev.target === modal) modal.remove(); };
  modal.innerHTML = '<div class="bg-slate-800 rounded-lg border border-slate-600 p-4 w-80 shadow-xl max-h-96 overflow-y-auto">' +
    '<div class="text-sm text-slate-200 mb-3">' + stale.length + ' stale issues</div>' +
    '<div class="flex flex-col gap-2">' +
    stale.map(function(i) {
      var hrs = Math.floor((Date.now() - new Date(i.updated_at).getTime()) / 3600000);
      return '<button onclick="document.getElementById(\'staleModal\').remove();openDetail(\'' + i.id + '\')" ' +
        'class="text-xs text-left bg-slate-700 px-3 py-2 rounded hover:bg-slate-600 text-slate-200">' +
        escHtml(i.title.slice(0, 40)) + ' <span class="text-red-400">(' + hrs + 'h)</span></button>';
    }).join('') +
    '</div>' +
    '<button onclick="document.getElementById(\'staleModal\').remove()" class="text-xs text-slate-500 mt-3 hover:text-slate-300">Close</button>' +
  '</div>';
  document.body.appendChild(modal);
}
```

**Step 2: Auto-select first type in workflow view**

In `loadWorkflow()` (line 1948), after the `forEach` that populates the dropdown (after line 1958, inside the `if (wfSelect && wfSelect.options.length <= 1)` block), add:
```js
      // Auto-select first type if none selected
      if (!wfSelect.value && wfSelect.options.length > 1) {
        wfSelect.value = wfSelect.options[1].value;
      }
```

This selects the first real type (index 1, since index 0 is the "Select type..." placeholder) when the dropdown is first populated.

**Step 3: Add inline missing-field text to disabled transition buttons**

In the transition button rendering (line 1117-1125), change:
```js
    container.innerHTML = transitions.map(function(t) {
      var cls = t.ready
        ? 'bg-blue-600 text-white hover:bg-blue-700'
        : 'bg-slate-700 text-slate-400 cursor-not-allowed';
      var title = t.missing_fields.length ? 'Missing: ' + t.missing_fields.join(', ') : '';
      return '<button ' + (t.ready ? 'onclick="updateIssue(\'' + issueId + '\',{status:\'' + t.to + '\'},this)"' : 'disabled') +
        ' class="text-xs px-2 py-1 rounded ' + cls + '" title="' + escHtml(title) + '">' +
        t.to + '</button>';
    }).join('');
```
to:
```js
    container.innerHTML = transitions.map(function(t) {
      var cls = t.ready
        ? 'bg-blue-600 text-white hover:bg-blue-700'
        : 'bg-slate-700 text-slate-400 cursor-not-allowed';
      var missingText = t.missing_fields.length ? ' <span class="text-slate-500">(missing: ' + t.missing_fields.join(', ') + ')</span>' : '';
      return '<button ' + (t.ready ? 'onclick="updateIssue(\'' + issueId + '\',{status:\'' + t.to + '\'},this)"' : 'disabled') +
        ' class="text-xs px-2 py-1 rounded ' + cls + '">' +
        t.to + missingText + '</button>';
    }).join('');
```

This shows "(missing: field_name)" inline next to the button text, visible to keyboard users without needing `title` hover.

**Step 4: Add "Not you?" to claim modal**

In `claimIssue()` (line 1460), update the modal innerHTML. Change:
```js
  modal.innerHTML = '<div class="bg-slate-800 rounded-lg border border-slate-600 p-4 w-72 shadow-xl">' +
    '<div class="text-sm text-slate-200 mb-2">Claim issue</div>' +
    '<input id="claimNameInput" type="text" value="' + escHtml(saved) + '" placeholder="Your name..." class="w-full bg-slate-700 text-slate-200 text-xs rounded px-3 py-2 border border-slate-600 mb-3 focus:outline-none focus:border-blue-500">' +
```
to:
```js
  modal.innerHTML = '<div class="bg-slate-800 rounded-lg border border-slate-600 p-4 w-72 shadow-xl">' +
    '<div class="text-sm text-slate-200 mb-2">Claim issue</div>' +
    '<input id="claimNameInput" type="text" value="' + escHtml(saved) + '" placeholder="Your name..." class="w-full bg-slate-700 text-slate-200 text-xs rounded px-3 py-2 border border-slate-600 mb-1 focus:outline-none focus:border-blue-500">' +
    (saved ? '<div class="text-xs text-slate-500 mb-2">Remembered as "' + escHtml(saved) + '" \u2014 <button onclick="document.getElementById(\'claimNameInput\').value=\'\';localStorage.removeItem(\'filigree_claim_name\');this.parentElement.remove();" class="text-blue-400 hover:underline">not you?</button></div>' : '<div class="mb-2"></div>') +
```

When a saved name exists, shows: `Remembered as "name" — not you?`. Clicking "not you?" clears the input, removes from localStorage, and removes the hint.

**Step 5: Verify all minor fixes**

- Click stale badge when >1 stale issue → modal shows full list
- Switch to Workflow tab → first type auto-selected, graph renders immediately
- Open issue detail with missing-field transitions → see "(missing: field)" inline
- Open claim modal with saved name → see "Not you?" link, clicking it clears the name

**Step 6: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix(dashboard): stale list, workflow default, transition hints, claim modal UX"
```

---

### Task 8: Run CI and Verify

**Files:** None (verification only)

**Step 1: Run linters**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: No errors. The dashboard HTML file is not linted by ruff (it only checks Python).

**Step 2: Run type checker**

```bash
uv run mypy src/filigree/
```

Expected: No new errors. We didn't change any Python files.

**Step 3: Run tests**

```bash
uv run pytest --tb=short
```

Expected: All existing tests pass. No Python backend changes were made.

**Step 4: Manual verification checklist**

Open `http://localhost:8377` and verify:
- [ ] Kanban columns are equal width (empty or populated)
- [ ] Drag card from Open to In Progress works
- [ ] Invalid drop targets are dimmed during drag
- [ ] Drop on dimmed column is rejected (card stays put)
- [ ] `m` key opens move modal in detail view
- [ ] Header has no Open/Active/Ready stats (footer does)
- [ ] Type filter shows "Filtered: [type] x" pill
- [ ] Standard/Cluster buttons dim when type-filtered
- [ ] Clicking Standard/Cluster clears type filter
- [ ] Status badges use tinted backgrounds (not white-on-color)
- [ ] P0 shows red "P0" text, P1 shows orange "P1" text
- [ ] Stale badge click shows list when >1 stale issue
- [ ] Workflow view auto-selects first type
- [ ] Disabled transitions show "(missing: field)" inline
- [ ] Claim modal shows "Not you?" when name is saved
