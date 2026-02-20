# Dashboard UX Improvements Design

**Date:** 2026-02-20
**Status:** Approved
**Branch:** feat/multi-project-dashboard

## Context

The filigree web dashboard (`src/filigree/static/dashboard.html`, ~2300 lines, vanilla JS + Tailwind CDN) recently gained multi-project support. A UX review identified 1 critical, 8 major, and 9 minor issues. This design covers the structural improvements — color scheme work is deferred.

## 1. Kanban Column Equal Width

**Problem:** `.kanban-col` only has `min-width: 320px` with no fixed basis. Empty columns shrink while populated ones stretch, breaking the Kanban spatial metaphor.

**Design:** Use `flex: 1 1 0` with `min-width: 280px` so columns share available space equally. The standard 3-column board (Open/WIP/Done) will always fit a laptop screen. Type-filtered boards with 5+ columns scroll horizontally via `overflow-x-auto` on the container.

**Changes:**
- CSS line 23: `.kanban-col { min-width: 280px; flex: 1 1 0; }`
- Remove `shrink-0` class from column divs in `renderStandardKanban`, `renderClusterKanban`, `renderTypeKanban`
- Add `min-h-[200px]` to empty column card list containers
- Update 1200px breakpoint to match

## 2. Kanban Drag and Drop

**Problem:** Status changes require 4+ clicks (click card, open detail, find button, click). Drag-and-drop is the canonical Kanban interaction.

**Design:**
- `dragstart`: store issue ID in `dataTransfer`, fetch `/issue/{id}/transitions`, dim invalid columns
- `dragover`: `preventDefault()` only on valid columns, add blue dashed border highlight
- `drop`: `PATCH /issue/{id}` with target status, optimistic card move, toast confirmation
- `dragend`: clear all visual states

**Key decisions:**
- Validate on dragstart (pre-fetch transitions), dim invalid targets immediately
- Standard board: map category columns to first valid transition in that category
- Type-filtered board: columns map 1:1 to statuses, use directly
- Cluster mode: drag-and-drop disabled (nested cards are awkward)
- Accessibility: `m` keyboard shortcut opens "Move to..." dropdown of valid targets
- Invalid columns: `opacity-30 cursor-not-allowed` during drag
- Valid columns: `border-2 border-dashed border-blue-500` drop zone highlight
- API is already project-scoped via `API_BASE`, no special multi-project handling

**Estimated size:** ~100-120 lines vanilla JS.

## 3. Header Density Reduction

**Problem:** Header overflows at ~1400px due to nav tabs + filters + stats + presets in one row.

**Design:** Remove the duplicated stat spans (`statOpen`, `statActive`, `statReady` at lines 136-138) from the header. The footer already has the complete set (Open, Active, Ready, Blocked, Deps, sparkline). This reduces density enough to avoid overflow without restructuring the layout.

## 4. Type-Filter / Mode Toggle Conflict

**Problem:** Selecting a type filter silently replaces Standard/Cluster mode with no visual indication. `switchKanbanMode()` doesn't clear `typeTemplate`.

**Design:**
- When `typeTemplate` is set, dim Standard/Cluster buttons (`opacity-50 pointer-events-none`)
- Show "Filtered: [type] x" pill next to type dropdown; clicking x clears the filter
- `switchKanbanMode()`: add `typeTemplate = null`, reset type dropdown to ""
- `applyTypeFilter()`: dim mode buttons when type is selected

## 5. Open-Status Badge Contrast

**Problem:** `background:#64748B;color:white` fails WCAG 1.4.3 at small sizes (~3.0:1 ratio).

**Design:** Switch to tinted approach: `background: rgba(100,116,139,0.2); color: #94A3B8` (slate-400 text on transparent slate bg). Matches existing type badge style (`bg-slate-700 text-slate-400`). Blue (in-progress) and gray (done) badges pass as-is.

## 6. Priority Text Labels for P0/P1

**Problem:** Priority is a 2px color dot — invisible to color-blind users for critical priorities.

**Design:** P0 and P1 get text badges (`P0` in red-400, `P1` in orange-400). P2-P4 keep the color dot.

## 7. Minor Fixes

**7a. Stale badge click:** `showStaleIssues()` shows all stale issues in a list, not just the first.

**7b. Workflow empty state:** Auto-select first type in dropdown on initial load.

**7c. Disabled transitions:** Add inline `(missing: field)` text next to disabled buttons (keyboard-accessible).

**7d. Claim modal "not you?":** Add "Not you?" link when pre-filling from localStorage.

**7e. Light theme CSS vars:** Migrate `!important` overrides to CSS custom properties with `data-theme="light"` selector. Deferred to color scheme work.

## Implementation Order

1. Kanban column equal width (CSS + minor HTML)
2. Kanban drag and drop (JS, depends on #1)
3. Header density reduction (HTML removal)
4. Type-filter / mode toggle conflict (JS)
5. Status badge contrast (JS in renderCard)
6. Priority text labels (JS in renderCard)
7. Minor fixes (7a-7d; 7e deferred to color scheme work)

## Out of Scope

- Color scheme redesign (separate follow-up)
- Mobile/responsive improvements beyond what's needed for equal-width columns
- Light theme CSS variable migration (7e, deferred to color scheme work)
