# Deep Teal Color Theme Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate the dashboard from hardcoded Tailwind Slate colors to a "Deep Teal" palette using CSS custom properties, with proper dark and light theme support.

**Architecture:** All colors become CSS custom properties defined on `:root` (dark) and `[data-theme="light"]`. Static HTML elements use utility classes (`.bg-raised`, `.text-primary`, etc.). JS-generated HTML uses inline `style="var(--name)"`. Semantic colors (red/amber/emerald) stay hardcoded — they don't change between themes.

**Tech Stack:** Vanilla CSS custom properties, vanilla JS, Tailwind CSS (CDN, layout only)

**Design doc:** `docs/plans/2026-02-20-deep-teal-theme-design.md`

**Single file:** All changes in `src/filigree/static/dashboard.html` (~2545 lines)

---

### Task 1: CSS Custom Properties Foundation

**Files:**
- Modify: `src/filigree/static/dashboard.html:11-65` (entire `<style>` block)

**Step 1: Add CSS custom property definitions**

At the very top of the `<style>` block (after line 11, before the `@import`), add the custom property definitions:

```css
:root {
  --surface-base: #0B1215;
  --surface-raised: #131E24;
  --surface-overlay: #1A2B34;
  --surface-hover: #243A45;
  --border-default: #1E3340;
  --border-strong: #2A4454;
  --text-primary: #E2EEF2;
  --text-secondary: #8FAAB8;
  --text-muted: #5A7D8C;
  --accent: #38BDF8;
  --accent-hover: #0EA5E9;
  --accent-subtle: rgba(12,74,110,0.2);
  --scrollbar-track: #131E24;
  --scrollbar-thumb: #2A4454;
  --graph-text: #E2EEF2;
  --graph-outline: #0B1215;
  --graph-edge: #2A4454;
  --status-open: #64748B;
  --status-wip: #38BDF8;
  --status-done: #7B919C;
}
[data-theme="light"] {
  --surface-base: #F0F6F8;
  --surface-raised: #FFFFFF;
  --surface-overlay: #E8F1F4;
  --surface-hover: #DCE9EE;
  --border-default: #C5D8E0;
  --border-strong: #9BBBC8;
  --text-primary: #0F2027;
  --text-secondary: #3D6070;
  --text-muted: #6B8D9C;
  --accent: #0284C7;
  --accent-hover: #0369A1;
  --accent-subtle: rgba(2,132,199,0.2);
  --scrollbar-track: #E8F1F4;
  --scrollbar-thumb: #B0C9D2;
  --graph-text: #0F2027;
  --graph-outline: #F0F6F8;
  --graph-edge: #9BBBC8;
  --status-open: #64748B;
  --status-wip: #0284C7;
  --status-done: #7B919C;
}
```

**Step 2: Add utility classes for static HTML**

After the custom property blocks, add utility classes that map to the properties. These replace Tailwind color classes in static HTML:

```css
.bg-base { background: var(--surface-base); }
.bg-raised { background: var(--surface-raised); }
.bg-overlay { background: var(--surface-overlay); }
.bg-hover { background: var(--surface-hover); }
.border-default { border-color: var(--border-default); }
.border-strong { border-color: var(--border-strong); }
.text-primary { color: var(--text-primary); }
.text-secondary { color: var(--text-secondary); }
.text-muted { color: var(--text-muted); }
.bg-accent { background: var(--accent); }
.bg-accent-hover:hover { background: var(--accent-hover); }
.text-accent { color: var(--accent); }
.bg-overlay-hover:hover { background: var(--surface-hover); }
```

**Step 3: Update all hardcoded colors in existing CSS rules**

Replace every hardcoded hex in the `<style>` block with the corresponding custom property:

```
body background:   #0F172A  → var(--surface-base)
body color:        #F1F5F9  → var(--text-primary)
.card:hover bg:    #334155  → var(--surface-hover)
.card:focus:       #3B82F6  → var(--accent)
scrollbar-track:   #1E293B  → var(--scrollbar-track)
scrollbar-thumb:   #475569  → var(--scrollbar-thumb)
.drag-valid:       #3B82F6  → var(--accent), rgba → var(--accent-subtle)
focus-visible:     #60A5FA  → var(--accent)
.tour-highlight:   #3B82F6  → var(--accent)
@keyframes flash:  rgba(59,130,246,0.5) → keep (animation, semantic)
```

Semantic colors stay hardcoded: `#10B981` (ready), `#EF4444` (stale/critical), `#F59E0B` (aging), `#7F1D1D` (stale-pulse dark).

**Step 4: Remove the old `.light` theme overrides**

Delete lines 56-64 entirely (the `body.light`, `.light .bg-slate-800`, etc. block). These are replaced by the `[data-theme="light"]` custom property block.

**Step 5: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add Deep Teal CSS custom properties and utility classes

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Migrate Static HTML Elements

**Files:**
- Modify: `src/filigree/static/dashboard.html` — header (lines 76-141), views (lines 143-270), footer (lines 271-289)

This task replaces Tailwind color classes in static HTML elements with the new utility classes. Layout classes (`flex`, `gap-4`, `px-4`, `rounded`, etc.) stay untouched.

**Mapping rules for static HTML:**

| Old Tailwind Class | New Class/Style |
|---|---|
| `bg-slate-800` | `bg-raised` |
| `bg-slate-800/50` | `bg-raised` (close enough, no opacity needed) |
| `bg-slate-700` | `bg-overlay` |
| `bg-slate-900/95` | `bg-base` |
| `bg-blue-600` | `bg-accent` |
| `hover:bg-blue-700` | `bg-accent-hover` |
| `hover:bg-slate-600` | `bg-overlay-hover` |
| `text-blue-400` | `text-accent` |
| `text-slate-200`, `text-slate-100`, `text-slate-300` | `text-primary` |
| `text-slate-400` | `text-secondary` |
| `text-slate-500` | `text-muted` |
| `text-slate-600` | `text-muted` |
| `text-white` (on accent buttons) | `text-primary` |
| `border-slate-700` | `border-default` |
| `border-slate-600` | `border-strong` |
| `focus:border-blue-500` | `style` with `--accent` or keep (focus is accent) |
| `accent-blue-500` | `accent-color: var(--accent)` (checkbox accent) |

**Semantic classes that stay unchanged:**
- `bg-emerald-900/50`, `text-emerald-400`, `border-emerald-700` (ready badges)
- `bg-red-900/50`, `text-red-400`, `border-red-800` (blocked/stale badges)
- `text-emerald-400` (footer ready count)
- `text-red-400` (footer blocked count)

**Step 1: Migrate `<body>` tag**

Change line 76 from:
```html
<body class="h-screen flex flex-col overflow-hidden text-sm">
```
to:
```html
<body class="h-screen flex flex-col overflow-hidden text-sm bg-base">
```

(The body background was previously set via the CSS `body {}` rule which now uses `var(--surface-base)`, but adding the class makes it explicit.)

**Step 2: Migrate skip-to-content link (line 77)**

Change `focus:bg-blue-600 focus:text-white` to use accent styling. Since this is a focus-only element, we can use inline style:

```html
<a href="#kanbanBoard" class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:px-3 focus:py-1 focus:rounded focus:z-50" style="--skip-bg:var(--accent);--skip-fg:var(--text-primary)" onfocus="this.style.background='var(--accent)';this.style.color='var(--surface-base)'" onblur="this.style.background='';this.style.color=''">Skip to content</a>
```

Actually, simpler: just keep `focus:bg-blue-600 focus:text-white` for the skip link — it's an accessibility element that's almost never seen. Don't over-engineer this one.

**Step 3: Migrate header (lines 80-141)**

Apply the mapping rules to every element in the header. Work through each line:

- Line 80 `<header>`: `bg-slate-800` → `bg-raised`, `border-slate-700` → `border-default`
- Line 82 logo: `text-blue-400` → `text-accent`
- Line 85 project switcher: `bg-slate-700` → `bg-overlay`, `text-slate-200` → `text-primary`, `border-slate-600` → `border-strong`
- Line 89 "+ New" button: `bg-blue-600` → `bg-accent`, `hover:bg-blue-700` → `bg-accent-hover`, keep `text-white`
- Lines 101-103 Ready button: Keep as-is (semantic emerald)
- Lines 104, 108 help icons: `bg-slate-700` → `bg-overlay`, `text-slate-400` → `text-secondary`, `hover:text-slate-200` → `hover:text-primary` (but `hover:text-primary` won't work with utility class — use inline style or keep Tailwind hover)
- Lines 105-107 Blocked button: `bg-slate-700` → `bg-overlay`, `text-slate-400` → `text-secondary`, `border-slate-600` → `border-strong`
- Line 110 priority select: same pattern as project switcher
- Lines 118-119 search input: same pattern, `focus:border-blue-500` → add inline `style` for focus border
- Line 123 multi-select button: same pattern as blocked button
- Lines 125-127 checkboxes: `text-slate-400` → `text-secondary`, `accent-blue-500` → keep or add `style="accent-color:var(--accent)"`
- Lines 129-132 presets: same pattern
- Lines 135-139 theme/health area: `text-slate-400` → `text-secondary`, `bg-slate-700` → `bg-overlay`, `text-blue-400` → `text-accent`

**IMPORTANT for hover states:** Tailwind hover classes like `hover:bg-slate-600` won't work with custom properties. For buttons in static HTML, use the `bg-overlay-hover` utility class (which uses `:hover` pseudo-class). For text hover, you can either keep Tailwind hover classes or add a `.text-primary-hover:hover { color: var(--text-primary); }` utility class.

Add these extra hover utilities to the utility class block from Task 1:
```css
.text-primary-hover:hover { color: var(--text-primary); }
.text-secondary-hover:hover { color: var(--text-secondary); }
```

**Step 4: Migrate graph view sub-header (lines 147-153)**

Apply same mapping. `bg-slate-800/50` → `bg-raised`, buttons → `bg-overlay bg-overlay-hover`.

**Step 5: Migrate graph legend (lines 157-190)**

`bg-slate-900/95` → `bg-base` with `opacity: 0.95` or just `bg-base`. Text classes follow the mapping.

**Step 6: Migrate kanban view sub-header (lines 196-225)**

Same mapping. The type filter pill (`bg-blue-900/50`, `text-blue-400`, `border-blue-800`) should use accent-based custom properties: `style="background:var(--accent-subtle);color:var(--accent);border-color:var(--accent)"`.

**Step 7: Migrate metrics, activity, workflow views (lines 228-263)**

Same mapping for all `bg-slate-*` and `text-slate-*` classes.

**Step 8: Migrate detail panel (line 266)**

`bg-slate-800` → `bg-raised`, `border-slate-700` → `border-default`.

**Step 9: Migrate footer (lines 272-280)**

`bg-slate-800` → `bg-raised`, `border-slate-700` → `border-default`, `text-slate-400` → `text-secondary`, `text-slate-300` → `text-primary`. Keep semantic colors (emerald, red) untouched.

**Step 10: Migrate batch bar (lines 282-287)**

`bg-slate-800` → `bg-raised`, `border-slate-600` → `border-strong`, `text-slate-300` → `text-primary`, `bg-slate-700` → `bg-overlay`. Keep red semantic colors.

**Step 11: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): migrate static HTML elements to Deep Teal theme

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Migrate JS Constants and Theme Toggle

**Files:**
- Modify: `src/filigree/static/dashboard.html` — JS state block (lines ~309-314), `toggleTheme()` (~line 2493), initialization (~line 2510+)

**Step 1: Update `CATEGORY_COLORS`**

Change line ~309:
```js
var CATEGORY_COLORS = { open: '#64748B', wip: '#3B82F6', done: '#9CA3AF' };
```
to:
```js
var CATEGORY_COLORS = { open: '#64748B', wip: '#38BDF8', done: '#7B919C' };
```

Note: `wip` changes from `#3B82F6` (blue-500) to `#38BDF8` (sky-400, matches new accent). `done` changes from `#9CA3AF` (gray) to `#7B919C` (teal-tinted gray). `open` stays the same.

`PRIORITY_COLORS` stays unchanged — those are semantic colors.

**Step 2: Update `toggleTheme()`**

Change from `classList.toggle('light')` to `dataset.theme`:

```js
function toggleTheme() {
  var current = document.body.dataset.theme;
  var next = current === 'light' ? 'dark' : 'light';
  document.body.dataset.theme = next;
  localStorage.setItem('filigree_theme', next);
  document.getElementById('themeToggle').textContent = next === 'light' ? '\u263E' : '\u2606';
  // Update CATEGORY_COLORS for light theme (wip uses darker accent)
  CATEGORY_COLORS.wip = next === 'light' ? '#0284C7' : '#38BDF8';
}
```

**Step 3: Update theme initialization on page load**

Find where the saved theme is restored from localStorage (search for `filigree_theme` in the init section near the bottom). It currently does `document.body.classList.add('light')`. Change to:

```js
var savedTheme = localStorage.getItem('filigree_theme');
if (savedTheme === 'light') {
  document.body.dataset.theme = 'light';
  document.getElementById('themeToggle').textContent = '\u263E';
  CATEGORY_COLORS.wip = '#0284C7';
}
```

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): update JS color constants and theme toggle for Deep Teal

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Migrate JS Render Functions

**Files:**
- Modify: `src/filigree/static/dashboard.html` — all JS functions that generate HTML with color classes

This is the largest task. Every JS function that builds HTML strings with Tailwind color classes or inline hex colors needs updating. The same mapping rules from Task 2 apply, but using inline `style="..."` with `var(--name)` since these are string-concatenated HTML.

**Mapping for JS-generated HTML:**

| Old Pattern | New Pattern |
|---|---|
| `bg-slate-800` in class | `style="background:var(--surface-raised)"` |
| `bg-slate-700` in class | `style="background:var(--surface-overlay)"` |
| `bg-slate-900` in class | `style="background:var(--surface-base)"` |
| `text-slate-200` / `text-slate-300` | `style="color:var(--text-primary)"` |
| `text-slate-400` | `style="color:var(--text-secondary)"` |
| `text-slate-500` / `text-slate-600` | `style="color:var(--text-muted)"` |
| `border-slate-700` | `style="border-color:var(--border-default)"` |
| `border-slate-600` | `style="border-color:var(--border-strong)"` |
| `bg-blue-600` | `style="background:var(--accent)"` |
| `hover:bg-blue-700` | Add `onmouseenter/onmouseleave` or just keep (hover in JS HTML is tricky — see note below) |
| `text-blue-400` | `style="color:var(--accent)"` |
| `bg-slate-800/90` (toasts) | `style="background:var(--surface-raised);opacity:0.95"` |
| `bg-slate-800/50` | `style="background:var(--surface-raised)"` |

**IMPORTANT — hover states in JS-generated HTML:**
For buttons generated in JS, hover states via Tailwind classes won't theme properly. Two options:
1. Use the utility classes (`.bg-overlay.bg-overlay-hover`) — these work because they reference CSS variables
2. For one-off buttons, keep `hover:bg-slate-600` — it won't perfectly match the teal theme in dark mode, but it's close enough. The critical colors are the resting state, text, and borders.

**Recommended approach:** Use the utility classes for common patterns. For complex modals where there are many hover states, prioritize getting the resting-state colors right and leave hover as a minor inconsistency.

**Step 1: Migrate `renderCard()` (~line 671)**

The card div itself: replace `bg-slate-800` with inline style `background:var(--surface-raised)`, `border-slate-700` with `border-color:var(--border-default)`.

The card's inner text spans: replace `text-slate-200` with inline `color:var(--text-primary)`, `text-slate-500` with `color:var(--text-muted)`, `text-slate-400` with `color:var(--text-secondary)`.

The type badge: `bg-slate-700 text-slate-400` → `background:var(--surface-overlay);color:var(--text-secondary)`.

Status badge: already uses `catColor` from `CATEGORY_COLORS` — no change needed (constants updated in Task 3).

Keep semantic colors: `text-red-400` (blocked), `text-amber-400` (impact), etc.

**Step 2: Migrate `renderClusterCard()` (~line 632)**

Same as renderCard for the card div. The progress bar colors use `#9CA3AF`, `#3B82F6`, `#64748B` — update to use `CATEGORY_COLORS` values or the CSS variable equivalents:
- `#9CA3AF` (done) → `var(--status-done)` or use JS `CATEGORY_COLORS.done`
- `#3B82F6` (wip) → use `CATEGORY_COLORS.wip`
- `#64748B` (open) → use `CATEGORY_COLORS.open`

Replace the hardcoded hex with `CATEGORY_COLORS.done`, `CATEGORY_COLORS.wip`, `CATEGORY_COLORS.open` references.

Text classes: same mapping as renderCard.

**Step 3: Migrate `renderStandardKanban()` and `renderClusterKanban()` (~lines 577, 601)**

Column header text: `text-slate-300` → `var(--text-primary)`, `text-slate-500` → `var(--text-muted)`.

Empty column text: `text-slate-500` / `text-slate-600` → `var(--text-muted)`, `text-blue-400` → `var(--accent)`.

**Step 4: Migrate `renderTypeKanban()` (~line 812)**

Same as standard kanban. `text-slate-600` → `var(--text-muted)`.

**Step 5: Migrate `openDetail()` (~line 1083)**

This is a large function (~180 lines) with many color references. Key changes:
- Panel background: already handled in static HTML (Task 2)
- Issue title: `text-slate-200` → `var(--text-primary)`
- Labels/metadata: `text-slate-400` → `var(--text-secondary)`
- Status badge: uses `statusColor` from `CATEGORY_COLORS` — already updated
- Input/textarea: `bg-slate-700 text-slate-200 border-slate-600` → `var(--surface-overlay)`, `var(--text-primary)`, `var(--border-strong)`
- Transition buttons: `bg-blue-600` → `var(--accent)`, `bg-slate-700 text-slate-400 cursor-not-allowed` → `var(--surface-overlay)`, `var(--text-muted)`
- Comment section: same input styling
- Events list: `text-slate-500` → `var(--text-muted)`
- Dependency links: `text-blue-400` → `var(--accent)`
- Action buttons (close, reopen, claim, release): Keep semantic colors (red/green/emerald)

**Step 6: Migrate `showToast()` (~line 1545)**

Change default toast colors:
```js
: 'bg-slate-800/90 border-slate-600 text-slate-200';
```
to use utility classes:
```js
: 'bg-raised border-strong text-primary';
```

Actually — since toast uses Tailwind classes with opacity modifiers, and we want the toast to be slightly transparent, use inline style:

For the default (info) toast, change the class string to use the utility classes plus a small opacity override. Or simpler: keep the semantic toast colors (error=red, success=emerald) and just update the default:

```js
var bg = type === 'error' ? 'bg-red-900/90 border-red-700 text-red-200'
       : type === 'success' ? 'bg-emerald-900/90 border-emerald-700 text-emerald-200'
       : 'border text-primary';
// Add inline style for default toast background
if (type !== 'error' && type !== 'success') {
  toast.style.background = 'var(--surface-raised)';
  toast.style.borderColor = 'var(--border-strong)';
  toast.style.opacity = '0.95';
}
```

**Step 7: Migrate all modal functions**

These functions all build HTML with `bg-slate-800`, `border-slate-600`, `text-slate-200`, etc.:
- `claimIssue()` (~line 1621)
- `showAddBlocker()` (~line 1696)
- `showCreateForm()` (~line 2277)
- `batchSetPriority()` (~line 2215)
- `savePreset()` / `confirmSavePreset()` (~lines 2372, 2390)
- `showStaleIssues()` (~line 2467)
- `showHealthBreakdown()` (~line 2021)
- Help modals: keyboard help (~line 1306), popover helpers (~line 1437)
- Move modal from drag-and-drop (`m` shortcut, ~line 1362)

For each modal, apply the same mapping:
- Modal wrapper: `bg-slate-800` → inline `background:var(--surface-raised)`
- Border: `border-slate-600` → inline `border-color:var(--border-strong)`
- Heading: `text-slate-200` → inline `color:var(--text-primary)`
- Body text: `text-slate-400` → inline `color:var(--text-secondary)`, `text-slate-300` → inline `color:var(--text-primary)`, `text-slate-500` → inline `color:var(--text-muted)`
- Inputs: `bg-slate-700 text-slate-200 border-slate-600` → `background:var(--surface-overlay);color:var(--text-primary);border-color:var(--border-strong)`
- Primary button: `bg-blue-600 text-white` → `background:var(--accent);color:var(--surface-base)`
- Cancel button: `bg-slate-700` → use utility classes `bg-overlay bg-overlay-hover`
- Cancel text: `text-slate-500 hover:text-slate-300` → `color:var(--text-muted)`

**Step 8: Migrate `switchView()` active/inactive button styling**

Search for where `switchView` sets button classes (active tab = blue, inactive = slate). Update:
- Active: `bg-blue-600 text-white` → use `bg-accent text-primary` or inline accent colors
- Inactive: `bg-slate-700 text-slate-400` → use `bg-overlay text-secondary`

Also check `switchKanbanMode()` for the same pattern.

**Step 9: Migrate `computeHealthScore()` / health badge**

The health badge dynamically sets classes based on score. The emerald/amber/red semantic colors stay. But the score display might use slate colors for the badge background — update those to use custom property classes.

**Step 10: Migrate filter preset rendering**

`loadPreset()` generates option elements — these should use theme colors.

**Step 11: Migrate `renderSparkline()`**

The sparkline canvas draws with `ctx.strokeStyle` and `ctx.fillStyle`. Update:
- Line color: use accent color `#38BDF8`
- Area fill: use accent at low opacity

Find the sparkline function and update the hardcoded colors.

**Step 12: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): migrate all JS render functions to Deep Teal theme

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Migrate Cytoscape Graph and Workflow Styles

**Files:**
- Modify: `src/filigree/static/dashboard.html` — `renderGraph()` (~line 933) and `renderWorkflowGraph()` (~line 2166)

**Step 1: Update dependency graph node styles**

In `renderGraph()`, find the Cytoscape style block. It has node styles with hardcoded colors:

```js
'color': '#F1F5F9'           → read CSS var: getComputedStyle(document.documentElement).getPropertyValue('--text-primary').trim() — BUT this is complex. Simpler: just use the new hex values directly.
'text-outline-color': '#0F172A'  → '#0B1215'
'background-color': uses CATEGORY_COLORS → already updated in Task 3
'border-color': '#10B981'        → keep (semantic: ready)
'line-color': '#475569'          → '#2A4454'
'target-arrow-color': '#475569'  → '#2A4454'
'border-color': '#3B82F6' (selected) → '#38BDF8'
```

For the critical path edges (red), keep unchanged.

Since Cytoscape doesn't support CSS custom properties, use the hardcoded new hex values. Create a helper object at the top of the function or near `CATEGORY_COLORS`:

```js
var THEME_COLORS = {
  textPrimary: '#E2EEF2',
  graphOutline: '#0B1215',
  graphEdge: '#2A4454',
  accent: '#38BDF8',
};
```

Then use `THEME_COLORS.textPrimary` etc. in the Cytoscape style blocks.

**Note on theme switching:** When the user toggles to light theme, the Cytoscape graph should also update. To handle this, `toggleTheme()` should update `THEME_COLORS` and re-render the graph if it's visible. Add to `toggleTheme()`:

```js
THEME_COLORS.textPrimary = next === 'light' ? '#0F2027' : '#E2EEF2';
THEME_COLORS.graphOutline = next === 'light' ? '#F0F6F8' : '#0B1215';
THEME_COLORS.graphEdge = next === 'light' ? '#9BBBC8' : '#2A4454';
THEME_COLORS.accent = next === 'light' ? '#0284C7' : '#38BDF8';
if (currentView === 'graph') renderGraph();
if (currentView === 'workflow') loadWorkflow();
```

**Step 2: Update workflow graph styles**

Same pattern as dependency graph. Find `renderWorkflowGraph()` and update:
- Node text color → `THEME_COLORS.textPrimary`
- Text outline → `THEME_COLORS.graphOutline`
- Edge colors → `THEME_COLORS.graphEdge`
- Label color: `#94A3B8` → `THEME_COLORS.textPrimary` (or a slightly muted version)

**Step 3: Update graph background**

The `#cy` and `#workflowCy` divs need their background to match the theme. Since these are static HTML elements, they inherit from the body `var(--surface-base)`. The old `.light #cy` rule was removed in Task 1. Add to the `<style>` block:

```css
#cy, #workflowCy { background: var(--surface-base); }
```

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): migrate Cytoscape graph styles to Deep Teal theme

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Migrate Plan View and Remaining Functions

**Files:**
- Modify: `src/filigree/static/dashboard.html` — `loadPlanView()` (~line 2086), popover functions (~line 1437), tour (~line 1475)

**Step 1: Migrate plan view**

In `loadPlanView()`, update:
- Progress bar backgrounds: `bg-emerald-500` → keep (semantic), `bg-blue-500` → use accent
- Text colors: apply standard mapping
- Status dots: already use `CATEGORY_COLORS` — updated in Task 3

**Step 2: Migrate popover/help functions**

`showPopover()`, `showReadyHelp()`, `showBlockedHelp()`, `showHealthHelp()` — these create popover elements with `bg-slate-900` backgrounds. Update:
- `bg-slate-900` → inline `background:var(--surface-base)`
- Text colors: standard mapping

**Step 3: Migrate guided tour**

`showTourStep()` creates tooltip elements. Update:
- `bg-slate-800` → inline `background:var(--surface-raised)`
- Text colors: standard mapping

**Step 4: Final sweep**

Do a search for any remaining `slate` references in the file:
```
grep -n 'slate' src/filigree/static/dashboard.html
```

Also search for any remaining old hex colors:
```
grep -n '#0F172A\|#1E293B\|#334155\|#475569\|#3B82F6\|#F1F5F9\|#9CA3AF' src/filigree/static/dashboard.html
```

Fix any remaining references found. The only `slate` references that should remain are semantic Tailwind classes on elements that don't change between themes (none should remain after this migration).

**Step 5: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): migrate remaining views and complete Deep Teal color sweep

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Run CI and Verify Both Themes

**Files:** None (verification only)

**Step 1: Run linters**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: No errors (HTML file is not linted by ruff).

**Step 2: Run type checker**

```bash
uv run mypy src/filigree/
```

Expected: No new errors. No Python changes.

**Step 3: Run tests**

```bash
uv run pytest --tb=short
```

Expected: All tests pass.

**Step 4: Visual verification — dark theme**

Open `http://localhost:8377`. Verify:
- [ ] Body background is deep teal (#0B1215), not slate
- [ ] Header and footer are slightly lighter teal (#131E24)
- [ ] Cards have teal-tinted backgrounds
- [ ] Accent color is sky-blue (#38BDF8) — buttons, active tabs, links
- [ ] Status badges use tinted backgrounds (from earlier UX fix)
- [ ] Kanban columns have consistent teal styling
- [ ] Graph nodes have correct category colors
- [ ] Detail panel matches the teal theme
- [ ] Modals (create issue, claim, keyboard help) match the theme
- [ ] Toasts appear with correct colors
- [ ] Scrollbar thumb/track are teal-tinted

**Step 5: Visual verification — light theme**

Click the theme toggle (sun/moon icon). Verify:
- [ ] Body background is teal-tinted off-white (#F0F6F8)
- [ ] Cards are white (#FFFFFF)
- [ ] Text is dark teal (#0F2027)
- [ ] Accent color is darker sky (#0284C7)
- [ ] Graph re-renders with light theme colors
- [ ] Toggle back to dark — everything reverts correctly

**Step 6: Check for remaining slate/old-hex references**

```bash
grep -c 'bg-slate\|text-slate\|border-slate' src/filigree/static/dashboard.html
```

Expected: 0 (or only in comments/the skip-to-content link if kept).

```bash
grep -c '#0F172A\|#F8FAFC\|#334155' src/filigree/static/dashboard.html
```

Expected: 0 references to old palette colors.
