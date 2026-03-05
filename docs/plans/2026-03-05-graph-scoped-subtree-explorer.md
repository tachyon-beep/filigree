# Graph Tab Redesign: Scoped Subtree Explorer

**Date:** 2026-03-05
**Status:** Reviewed

## Problem

The current graph tab renders all issues at once, producing a dense hairball that
isn't useful to humans. Hundreds of nodes with crossing edges obscures the
relationships the graph is meant to reveal. Users need a way to control scope
so the graph shows only what matters to them.

## Design

### Layout

Split the graph tab into two regions:

- **Left sidebar** — item selector panel (similar width to kanban sidebar),
  collapsible at viewports below 1024px. Minimum canvas width: 600px.
- **Right canvas** — Cytoscape graph, starts blank

Blank state shows a prompt: *"Select items from the sidebar to explore their
dependency graph."*

**Empty sidebar state:** If no `parent_id: null` issues exist, show:
*"No top-level issues found. Issues must have no parent to appear here."*

### Sidebar: Item Selector

#### Type Filter

A pill row at the top of the sidebar. Filters which issue types appear in the
list below (milestone, epic, release, feature, task, bug, etc.). Multiple types
can be active simultaneously.

**Type filter does not clear selections.** Changing the type filter hides items
from the sidebar list but does not remove existing selections. A badge on the
filter control shows the count of active-but-hidden selections (e.g., "2 hidden").

#### Item List

Shows all issues with `parent_id: null`, grouped by type in this order:

1. Milestones
2. Epics
3. Releases
4. Remaining types (features, tasks, bugs, etc.)

Each group header shows an item count (e.g., "Epics (4)").

Within each group, sorted by priority (ascending) then title (alphabetical).

Each sidebar item shows a **cross-tree dependency count badge** — a small number
indicating how many dependency edges cross into other subtrees. This makes the
structural cost of cross-tree connectivity visible before the user selects an
item, and helps predict how complex its ghost frontier will be.

**Select all / Clear all** buttons at the top of the sidebar. "Select all" may
produce a dense graph; that's the user's choice, and the soft node cap (see
below) provides a safety valve.

#### Item Visual States

Each sidebar item has one of three states, each with a **distinct visual
treatment** (no two states should rely on the same background):

| State | Visual Treatment | How It Got There | Click Action |
|-------|-----------------|------------------|--------------|
| **Unselected** | Muted text, no background | Default | Toggle to *explicit* |
| **Explicit** | Accent blue background (`--accent`) | User clicked in sidebar | Toggle to *unselected* |
| **Clicked-in** | Amber/yellow tint background | Ghost node clicked in graph | Click promotes to *explicit* |

The amber tint for clicked-in items avoids icon ambiguity (the eye icon was
rejected — it reads as "show/hide" not "arrived via graph click"). The color
difference between accent blue and amber is immediately legible without
requiring icon literacy.

**Contextual help:** A small help icon or tooltip near the sidebar header
explains the two selection modes: *"Blue = you selected this. Amber = pulled
in via a dependency you explored in the graph. Click amber to pin it."*

#### Deselection Behavior

State tracks **causal linkage**: which explicit selection caused each clicked-in
item to appear.

```
sidebarSelections: Map<issueId, {
  state: "explicit" | "clicked-in",
  causedBy: Set<issueId>  // empty for explicit; set of explicit IDs for clicked-in
}>
```

- Deselecting an **explicit** item removes its full subtree from the graph,
  plus any clicked-in nodes whose `causedBy` set becomes empty after removal.
- On hover over a selected sidebar item, show a **removal preview**: "Removing
  this will also remove N explored items" (where N is the count of clicked-in
  nodes that would lose their last causal reference).
- Promoting a clicked-in item to explicit (by clicking it in the sidebar) makes
  it independent — it stays even if the tree that originally pulled it in is
  deselected, and its `causedBy` is cleared.
- **Explicit selections are the source of truth.**

### Graph Canvas

#### What Renders

When a top-level item is selected, the graph shows:

1. The item itself
2. Its full recursive subtree (all descendants via `children`)
3. All dependency edges between those nodes (`blocks`/`blocked_by`)
4. **Cross-tree dependency nodes** — shown as ghost nodes

Multiple selected items merge into one graph. Shared dependency edges between
selected subtrees render as normal (non-ghost) edges.

**Subtree walker must guard against circular dependencies** with a visited-nodes
set to prevent infinite loops.

#### Soft Node Cap

Retain a soft node count limit (default ~300). When a selection change would
push the canvas past this limit, show a warning: *"This would display N nodes.
Large graphs may be slow. Continue?"* The user can proceed or cancel. This
prevents accidental hairball reconstruction via "select all" or aggressive ghost
expansion, while respecting user agency.

A visible counter in the toolbar shows current node/edge count at all times
(e.g., "72 nodes, 94 edges").

#### Ghost Nodes

When a node in a selected subtree has a dependency on a node outside any
selected subtree:

- The foreign node renders as a **ghost**: semi-transparent background and
  border (dashed), but **full-opacity labels** to maintain text contrast and
  accessibility compliance
- Ghost nodes show enough info to identify them (title, type badge, ID)
- Ghost nodes have `cursor: pointer` and a **hover glow/border brightening**
  to signal interactivity — dashed + transparent alone looks decorative
- **Clicking a ghost node** brings in its top-level ancestor's full subtree
  and marks that ancestor as "clicked-in" in the sidebar. This is intentional:
  clicking is an active choice, so the system responds by giving full context.

**Soft node cap** (see above) is the safety valve against expansion loops. If
bringing in a subtree would exceed the cap, the user sees a warning and can
choose to proceed or cancel. This preserves the "click brings it in" intent
while preventing accidental hairball reconstruction in highly connected projects.

**Ghost nodes that are themselves root issues** (`parent_id: null`): clicking
adds them directly as "clicked-in" in the sidebar with their full subtree.

**Ghost nodes and status pills:** Ghost nodes respect status pill filters. If
"done" is toggled off, a ghost node with status category "done" will not render.
This prevents ghosts from being a backdoor to seeing filtered-out content.

#### Controls Retained

Above the graph canvas, keep:

- **Layout algorithm selector** — user picks graph layout
- **Status pills** (open / active / done) — filter within selected subtrees
- **Search** — highlight matching nodes within the current view
- **Time window** — for filtering done items by recency
- **Node/edge count** — visible at all times (e.g., "72 nodes, 94 edges")

#### Controls Removed

These are superseded by sidebar selection:

- Focus mode (root + radius) — replaced by sidebar selection
- Hard node/edge limits — replaced by soft cap with warning
- Epics-only checkbox — replaced by sidebar type filter
- Ready-only / blocked-only checkboxes — may revisit as secondary filters later

### Accessibility

- **Keyboard navigation:** Sidebar items support Arrow keys to move between
  items, Space/Enter to toggle selection. Reuse existing `.card:focus` outline
  pattern for focus rings.
- **ARIA:** Sidebar items use `role="option"` within a `role="listbox"`.
  Clicked-in items include `aria-label` text explaining their state (e.g.,
  "Epic B — explored via dependency, click to pin").
- **Live region:** An `aria-live="polite"` status region in the sidebar
  announces state changes triggered by graph interaction (e.g., "Epic B added
  via dependency exploration").
- **Ghost node contrast:** Ghost styling dims background and border only.
  Labels maintain full opacity with the existing `text-outline-width: 2`
  technique to ensure contrast compliance against both dark and light themes.

### Data Flow

1. **Tab load**: Issues already cached in `state.allIssues`. Build parent-tree
   index: map each issue to its top-level ancestor, index children recursively.
   Use visited-nodes guard to handle circular parent references.
2. **Sidebar render**: Group `parent_id: null` issues by type, apply type filter.
   Show cross-tree dep count badge per item. Preserve scroll position across
   re-renders.
3. **Selection change**: Collect all node IDs in selected subtrees. Check against
   soft node cap. Resolve dependency edges. Identify cross-tree deps and mark
   those nodes as ghosts.
4. **Ghost click**: Look up clicked node's top-level ancestor. Check whether
   adding its full subtree would exceed the soft node cap — warn if so. Add
   ancestor as "clicked-in" in sidebar state with `causedBy` tracking. Re-render
   graph with expanded scope.
5. **Deselection**: Remove subtree nodes. Walk clicked-in items; remove any
   whose `causedBy` set is now empty. Show removal preview count on hover before
   the click. Re-render.
6. **Cytoscape render**: Pass filtered nodes + edges to Cytoscape. Ghost nodes
   get distinct CSS classes. Use existing `canReusePositions` logic to prevent
   disorienting layout jumps when selections change. Animate subtree additions
   with a brief layout transition.

### State Model

```
sidebarSelections: Map<issueId, {
  state: "explicit" | "clicked-in",
  causedBy: Set<issueId>
}>
sidebarTypeFilter: Set<string>   // active type filters
```

The graph derives entirely from these two pieces of state plus `state.allIssues`.

## Migration

The existing graph code (~1050 lines) has two rendering paths: v2 (server-side
query) and legacy (client-side filter). This redesign replaces both with a single
client-side approach driven by sidebar state. The v2 server endpoint can remain
available but is no longer the primary rendering path.

Existing URL hash routing (`#graph`) is preserved. Graph-specific localStorage
keys (time window, etc.) are preserved where controls are retained.

## Review Findings Incorporated

This design was reviewed by UX (lyra-ux-designer) and systems thinking
(yzmir-systems-thinking) specialists. Key findings addressed:

1. **Ghost-click expansion loop (Fixes that Fail archetype):** Mitigated by
   soft node cap with user warning. Full ancestor subtree expansion is preserved
   (clicking is an active choice) but the cap prevents accidental hairball
   reconstruction.
2. **Clicked-in state discoverability:** Replaced eye icon with amber background
   tint. Added contextual help tooltip explaining the two selection modes.
3. **Causal linkage for deselection:** State model now tracks `causedBy` set per
   clicked-in item, enabling removal preview and preventing silent disappearance.
4. **Ghost node affordance:** Added cursor pointer + hover glow to signal
   interactivity. Full-opacity labels for accessibility.
5. **Type filter interaction:** Explicitly defined — filter hides sidebar items
   but does not clear selections. Badge shows hidden selection count.
6. **Scope governor retained:** Soft node cap with warning replaces hard removal
   of limits.
7. **Cross-tree dep visibility:** Badge on sidebar items shows dependency count
   before selection, making structural cost visible (addresses Shifting the
   Burden concern).
8. **Edge cases:** Circular dep guard, empty sidebar state, root-level ghost
   click handling, single-node-no-deps rendering all specified.
9. **Accessibility:** Keyboard nav, ARIA roles, live region, contrast-safe ghost
   styling all specified.

## Resolved Questions

- **Ready-only / blocked-only:** Deferred. May revisit as secondary filters.
- **Issue count per type group:** Yes, show counts in group headers.
- **Select all / clear all:** Yes, include both. Soft node cap handles the
  "select all produces hairball" case.
