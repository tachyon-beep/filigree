# Releases Tab UX Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all 17 UX issues (2 critical, 7 major, 8 minor) identified in the releases tab design review.

**Architecture:** All changes are in two files: `releases.js` (the view module) and `dashboard.html` (structural HTML + CSS). No backend changes needed. The fixes focus on focus management, keyboard accessibility, ARIA correctness, contrast, and interaction polish.

**Tech Stack:** Vanilla JS, Tailwind CSS CDN, CSS custom properties, ARIA 1.1 tree pattern.

---

### Task 1: Critical — Focus restoration after re-render

Every toggle operation calls `loadReleases()` which replaces `container.innerHTML`, destroying DOM and losing keyboard focus. This makes all keyboard navigation unusable.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:513-584` (loadReleases function)

**Step 1: Add focus capture/restore to loadReleases()**

Before the `innerHTML` replacement (line 576), capture the active element's identity. After rendering + keyboard setup, restore focus.

In `loadReleases()`, add focus capture before `container.innerHTML = html` and restore after `setupTreeKeyboard(container)`:

```javascript
// --- In loadReleases(), BEFORE container.innerHTML = html (currently line 576) ---

// Capture focused element identity for restoration after DOM replacement
const activeEl = document.activeElement;
const focusNodeId = activeEl?.dataset?.nodeId || null;
const focusCardId = activeEl?.closest?.('[id^="release-card-"]')?.id || null;

container.innerHTML = html;

// Restore scroll position
if (scrollParent) {
  scrollParent.scrollTop = scrollTop;
}

// Set up keyboard navigation on any rendered trees
setupTreeKeyboard(container);

// Restore focus to previously focused element
if (focusNodeId) {
  const restored = container.querySelector('[data-node-id="' + focusNodeId + '"]');
  if (restored) {
    restored.setAttribute("tabindex", "0");
    restored.focus();
  }
} else if (focusCardId) {
  const cardBtn = document.getElementById(focusCardId)?.querySelector("button");
  if (cardBtn) cardBtn.focus();
}
```

**Step 2: Verify manually**

Open the dashboard at http://localhost:8888, switch to Releases view. Expand a release, use keyboard to navigate the tree, toggle a node. Verify focus stays on the toggled node after re-render.

**Step 3: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): restore keyboard focus after DOM re-render"
```

---

### Task 2: Critical — Focus tree after async expand

When a release card is expanded via `_toggleReleaseExpand`, the tree loads asynchronously but focus is never moved to the new tree. Keyboard users can't enter it.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:438-462` (_toggleReleaseExpand)

**Step 1: Move focus to first treeitem after expand completes**

In `_toggleReleaseExpand`, after the `finally` block calls `loadReleases()`, wait for the render then focus the first treeitem in the expanded release's tree:

```javascript
window._toggleReleaseExpand = async function (releaseId) {
  if (expandedReleaseIds.has(releaseId)) {
    expandedReleaseIds.delete(releaseId);
    loadReleases();
    return;
  }

  expandedReleaseIds.add(releaseId);
  loadingReleaseIds.add(releaseId);
  loadReleases(); // Re-render to show loading state

  try {
    const tree = await fetchReleaseTree(releaseId);
    if (tree) {
      releaseTreeCache.set(releaseId, tree);
      errorReleaseIds.delete(releaseId);
      drainStaleExpandedIds(tree);
    }
  } catch (_e) {
    errorReleaseIds.add(releaseId);
  } finally {
    loadingReleaseIds.delete(releaseId);
    await loadReleases();

    // Focus the first treeitem in the newly expanded release
    const card = document.getElementById("release-card-" + releaseId);
    if (card) {
      const firstItem = card.querySelector('[role="treeitem"]');
      if (firstItem) {
        firstItem.setAttribute("tabindex", "0");
        firstItem.focus();
      }
    }
  }
};
```

Note: `loadReleases()` is already async and returns a promise — we just need to `await` it to ensure the DOM is ready before focusing.

**Step 2: Verify manually**

Tab to a release expand button, press Enter. Verify focus moves to the first tree item after loading completes.

**Step 3: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): focus first tree item after async expand"
```

---

### Task 3: Major — Make clickable titles keyboard-accessible

Both release card titles (line 358) and tree node titles (line 121) are `<span onclick>` — not focusable by keyboard. Replace with `<button>` elements.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:121-123` (tree node title)
- Modify: `src/filigree/static/js/views/releases.js:357-360` (release card title)

**Step 1: Replace tree node title span with button**

Change lines 121-123 from:
```javascript
html += '<span class="cursor-pointer hover:underline text-xs" style="color:var(--text-primary)" ' +
  'onclick="window.openDetail(\'' + safeId + '\')">' +
  escHtml(node.issue.title || nodeId) + '</span>';
```
To:
```javascript
html += '<button class="cursor-pointer hover:underline text-xs text-left" ' +
  'style="color:var(--text-primary);background:none;border:none;padding:0" ' +
  'onclick="window.openDetail(\'' + safeId + '\')">' +
  escHtml(node.issue.title || nodeId) + '</button>';
```

**Step 2: Replace release card title span with button**

Change lines 357-360 from:
```javascript
html += '<span class="cursor-pointer hover:underline text-sm font-medium flex-1" style="' + textColor + '" ' +
  'onclick="window.openDetail(\'' + safeId + '\')">' +
  escHtml(release.title || release.id) + '</span>';
```
To:
```javascript
html += '<button class="cursor-pointer hover:underline text-sm font-medium flex-1 text-left" style="' + textColor + ';background:none;border:none;padding:0" ' +
  'onclick="window.openDetail(\'' + safeId + '\')">' +
  escHtml(release.title || release.id) + '</button>';
```

**Step 3: Verify**

Tab through the releases view. Verify you can Tab to release titles and tree node titles. Press Enter to open detail panel.

**Step 4: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): make title elements keyboard-accessible buttons"
```

---

### Task 4: Major — Set tabindex="0" on first treeitem

All tree items start at `tabindex="-1"`, making the tree unreachable by Tab. The first treeitem in each tree needs `tabindex="0"`.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:189-194` (setupTreeKeyboard)

**Step 1: Initialize roving tabindex in setupTreeKeyboard**

After wiring up the keydown listener, set the first treeitem to `tabindex="0"`:

```javascript
function setupTreeKeyboard(container) {
  const treeRoots = container.querySelectorAll('[role="tree"]');
  treeRoots.forEach((tree) => {
    tree.addEventListener("keydown", handleTreeKeydown);
    // Initialize roving tabindex — first item is the tab entry point
    const firstItem = tree.querySelector('[role="treeitem"]');
    if (firstItem) {
      firstItem.setAttribute("tabindex", "0");
    }
  });
}
```

**Step 2: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): initialize roving tabindex on first treeitem"
```

---

### Task 5: Major — Fix blocked title contrast

Blocked release titles use `--text-muted` (~3.9:1 contrast) which fails WCAG 4.5:1 AA. Use `--text-secondary` (~6.1:1) instead.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:340`

**Step 1: Change textColor for blocked state**

Change line 340 from:
```javascript
const textColor = isBlocked ? "color:var(--text-muted)" : "color:var(--text-primary)";
```
To:
```javascript
const textColor = isBlocked ? "color:var(--text-secondary)" : "color:var(--text-primary)";
```

**Step 2: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): improve blocked title contrast to meet WCAG AA"
```

---

### Task 6: Major — Announce loading state to screen readers

The "Loading tree..." text has no ARIA live region, so screen readers don't announce it.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:409-411`

**Step 1: Add role="status" to the expanded tree container**

Change the expanded tree area div (line 409) to include an `aria-live` region. Add `role="status"` to the loading message:

Change line 411 from:
```javascript
html += '<div class="text-xs py-2" style="color:var(--text-muted)">Loading tree...</div>';
```
To:
```javascript
html += '<div class="text-xs py-2" style="color:var(--text-muted)" role="status">Loading tree...</div>';
```

**Step 2: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): announce loading state to screen readers"
```

---

### Task 7: Major — Add landmark role to release cards

Release cards are generic divs with no ARIA role. Add `role="article"` with `aria-label`.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:342-343`

**Step 1: Add role and aria-label to card container**

Change lines 342-343 from:
```javascript
let html = '';
html += '<div class="rounded mb-3" style="background:var(--surface-raised);border:1px solid var(--border-default);border-left:4px solid ' + borderColor + '" id="release-card-' + escHtml(release.id) + '">';
```
To:
```javascript
let html = '';
html += '<div class="rounded mb-3" role="article" aria-label="' + escHtml(release.title || release.id) + '" style="background:var(--surface-raised);border:1px solid var(--border-default);border-left:4px solid ' + borderColor + '" id="release-card-' + escHtml(release.id) + '">';
```

**Step 2: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): add landmark role to release cards for screen readers"
```

---

### Task 8: Major — Dynamic skip link target

The skip link always points to `#kanbanBoard` regardless of the active view. Update `switchView()` to set the skip link's `href` dynamically.

**Files:**
- Modify: `src/filigree/static/js/router.js:32-71` (switchView function)

**Step 1: Add skip link update to switchView()**

The view content container IDs follow a pattern: `kanbanView`, `graphView`, `metricsView`, etc. Update the skip link `href` at the end of `switchView()`:

After line 63 (`updateHash();`), before the loader dispatch, add:

```javascript
// Update skip link to target current view
const skipLink = document.querySelector('a[href^="#"][class*="sr-only"]');
if (skipLink) {
  skipLink.href = "#" + view + "View";
}
```

**Step 2: Commit**

```bash
git add src/filigree/static/js/router.js
git commit -m "fix(a11y): update skip link target when view changes"
```

---

### Task 9: Major — Format target date with relative time

Target dates display as raw ISO strings. Format them as human-readable dates with relative time and overdue indicators.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:385-388`

**Step 1: Add date formatting helper**

Add a `formatTargetDate` function near the other helpers (after `formatChildSummary`):

```javascript
function formatTargetDate(isoDate) {
  if (!isoDate) return '';
  const target = new Date(isoDate);
  if (isNaN(target.getTime())) return escHtml(isoDate);
  const now = new Date();
  // Reset hours for day-level comparison
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const targetStart = new Date(target.getFullYear(), target.getMonth(), target.getDate());
  const diffDays = Math.round((targetStart - todayStart) / (1000 * 60 * 60 * 24));

  const formatted = target.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  let relative;
  let color = 'var(--text-muted)';

  if (diffDays < 0) {
    const absDays = Math.abs(diffDays);
    relative = absDays === 1 ? '1 day overdue' : absDays + ' days overdue';
    color = '#EF4444';
  } else if (diffDays === 0) {
    relative = 'today';
    color = '#F59E0B';
  } else if (diffDays <= 7) {
    relative = diffDays === 1 ? 'tomorrow' : 'in ' + diffDays + ' days';
    color = '#F59E0B';
  } else {
    relative = 'in ' + diffDays + ' days';
  }

  return '<span style="color:' + color + '">Target: ' + escHtml(formatted) + ' (' + escHtml(relative) + ')</span>';
}
```

**Step 2: Replace raw date rendering**

Change lines 385-388 from:
```javascript
if (release.target_date) {
  html += '<div class="text-xs mt-1" style="color:var(--text-muted)">Target: ' + escHtml(release.target_date) + '</div>';
}
```
To:
```javascript
if (release.target_date) {
  html += '<div class="text-xs mt-1">' + formatTargetDate(release.target_date) + '</div>';
}
```

**Step 3: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "feat(releases): format target dates with relative time and overdue indicator"
```

---

### Task 10: Minor — Use heading element for view title

The "Releases" title is a `<span>`, invisible to screen reader heading navigation. Replace with `<h2>`.

**Files:**
- Modify: `src/filigree/static/dashboard.html:496`

**Step 1: Replace span with h2**

Change line 496 from:
```html
<span class="text-base font-semibold text-primary">Releases</span>
```
To:
```html
<h2 class="text-base font-semibold text-primary m-0">Releases</h2>
```

**Step 2: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix(releases): use h2 heading for screen reader navigation"
```

---

### Task 11: Minor — Remove redundant aria-label from checkbox

The "Show released" checkbox has both a `<label>` wrapper and an `aria-label` attribute with different text. Remove the redundant `aria-label`.

**Files:**
- Modify: `src/filigree/static/dashboard.html:498`

**Step 1: Remove aria-label from checkbox**

Change line 498 from:
```html
<input type="checkbox" id="showReleased" style="accent-color:var(--accent)" aria-label="Show released and cancelled releases">
```
To:
```html
<input type="checkbox" id="showReleased" style="accent-color:var(--accent)">
```

And update the label text to be more descriptive. Change line 497-500 from:
```html
<label for="showReleased" class="flex items-center gap-1 text-xs cursor-pointer" style="color:var(--text-secondary)">
  <input type="checkbox" id="showReleased" style="accent-color:var(--accent)" aria-label="Show released and cancelled releases">
  Show released
</label>
```
To:
```html
<label for="showReleased" class="flex items-center gap-1 text-xs cursor-pointer" style="color:var(--text-secondary)">
  <input type="checkbox" id="showReleased" style="accent-color:var(--accent)">
  Show released &amp; cancelled
</label>
```

This makes the visible label match the accessible name, and covers cancelled releases in the label.

**Step 2: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "fix(releases): remove redundant aria-label, align checkbox label text"
```

---

### Task 12: Minor — Increase "Collapse all" button min-height

The "Collapse all" button uses `min-height:36px`, below the 44px touch target minimum.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:173-174`

**Step 1: Change min-height to 44px**

Change line 173-174 from:
```javascript
html += '<button class="text-xs px-2 py-1 rounded bg-overlay bg-overlay-hover" ' +
  'style="min-height:36px" ' +
```
To:
```javascript
html += '<button class="text-xs px-2 py-1 rounded bg-overlay bg-overlay-hover" ' +
  'style="min-height:44px" ' +
```

**Step 2: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): increase collapse-all button to 44px touch target"
```

---

### Task 13: Minor — Fix empty state terminology mismatch

Empty state says "Show completed releases" but the checkbox says "Show released". Align the wording.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:543-548`

**Step 1: Update empty state text**

Change lines 544-548 from:
```javascript
container.innerHTML =
  '<div class="p-6 text-center" style="color:var(--text-muted)">' +
  '<div class="font-medium mb-2" style="color:var(--text-primary)">No active releases.</div>' +
  '<div>Show completed releases to see release history.</div></div>';
```
To:
```javascript
container.innerHTML =
  '<div class="p-6 text-center" style="color:var(--text-muted)">' +
  '<div class="font-medium mb-2" style="color:var(--text-primary)">No active releases.</div>' +
  '<div>Use the \u201CShow released &amp; cancelled\u201D checkbox above to view release history.</div></div>';
```

**Step 2: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): align empty state text with checkbox label"
```

---

### Task 14: Minor — Fix [blocked] badge screen reader text

Screen readers announce "left bracket blocked right bracket". Use `aria-label` to clean this up.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:367`

**Step 1: Add aria-label to blocked badge**

Change line 367 from:
```javascript
html += ' <span class="text-xs rounded px-1.5 py-0.5 shrink-0" style="background:#EF4444;color:#fff">[blocked]</span>';
```
To:
```javascript
html += ' <span class="text-xs rounded px-1.5 py-0.5 shrink-0" style="background:#EF4444;color:#fff" aria-label="blocked">blocked</span>';
```

This removes the brackets entirely (the red badge provides sufficient visual distinctiveness) and adds a clean `aria-label`.

**Step 2: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): clean up blocked badge for screen readers"
```

---

### Task 15: Minor — Add flash highlight on scroll-to dependency link

When clicking a "Blocks" or "Blocked by" link, the destination card scrolls into view but has no visual indicator. Add the existing `changed-flash` animation.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:391-405` (blocks/blocked_by link handlers)

**Step 1: Add a shared scroll-and-flash helper**

Add a helper function near the top of the file (after the module-level state declarations):

```javascript
function scrollToReleaseCard(cardId) {
  const el = document.getElementById(cardId);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.remove('changed-flash');
  // Force reflow so re-adding the class restarts the animation
  void el.offsetWidth;
  el.classList.add('changed-flash');
}
```

**Step 2: Update blocks/blocked_by link onclick handlers**

Change the blocks links (line 394) from:
```javascript
'onclick="event.preventDefault();document.getElementById(\'release-card-' + escJsSingle(b.id) + '\')?.scrollIntoView({behavior:\'smooth\',block:\'center\'})">'
```
To:
```javascript
'onclick="event.preventDefault();window._scrollToReleaseCard(\'release-card-' + escJsSingle(b.id) + '\')">'
```

Same change for the blocked_by links (line 402).

Expose the helper on window:
```javascript
window._scrollToReleaseCard = scrollToReleaseCard;
```

**Step 3: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "feat(releases): flash highlight on scroll-to dependency link"
```

---

### Task 16: Minor — Fix amber border contrast on light theme

The amber left-border color `#F59E0B` has only ~2.4:1 contrast against `--surface-raised: #FFFFFF` in light theme (fails WCAG 1.4.11's 3:1 for UI components). Use a darker amber for the border.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:19-36` (statusBorderColor)

**Step 1: Use theme-aware amber for border**

The border color needs to be darker on light theme. Since the code doesn't currently have access to the theme, use CSS variable with a fallback. The simplest approach: use a darker amber (`#B45309`) that works on both themes — it's 3.9:1 on white and still clearly amber.

Change lines 23-27 from:
```javascript
case "development":
case "frozen":
case "testing":
case "staged":
  return "#F59E0B";
```
To:
```javascript
case "development":
case "frozen":
case "testing":
case "staged":
  return document.body.dataset.theme === "light" ? "#B45309" : "#F59E0B";
```

**Step 2: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): darken amber border on light theme for WCAG 1.4.11"
```

---

### Task 17: Minor — Align aria-level with visual indent cap

Visual depth is capped at 3 levels (`maxLevel = Math.min(level, 3)`) but `aria-level` goes to the actual depth. This can confuse screen reader users. Cap `aria-level` to match visual depth.

**Files:**
- Modify: `src/filigree/static/js/views/releases.js:81,91`

**Step 1: Use capped level for aria-level**

Change line 91 from:
```javascript
html += '<li role="treeitem" aria-level="' + (level + 1) + '"';
```
To:
```javascript
html += '<li role="treeitem" aria-level="' + (maxLevel + 1) + '"';
```

This ensures that `aria-level` matches the visual indentation, capped at level 4 (since maxLevel is capped at 3 and we add 1).

**Step 2: Commit**

```bash
git add src/filigree/static/js/views/releases.js
git commit -m "fix(releases): cap aria-level to match visual indent depth"
```

---

### Task 18: Final verification

**Step 1: Run the linter/formatter checks**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

**Step 2: Run tests**

```bash
uv run pytest --tb=short
```

**Step 3: Manual smoke test**

Open http://localhost:8888, switch to Releases tab:
1. Verify keyboard navigation: Tab into view, expand release, arrow keys in tree
2. Verify focus preservation: toggle a tree node, confirm focus doesn't jump
3. Verify screen reader: titles are buttons, tree has tabindex="0" entry, loading announced
4. Verify light theme: toggle theme, check amber border contrast
5. Verify blocked badge: no brackets, red badge still clear
6. Verify target date: shows "Mar 15, 2026 (in 16 days)" format
7. Verify dependency scroll: click a "Blocks" link, see flash animation
8. Verify empty state: matches checkbox label text
