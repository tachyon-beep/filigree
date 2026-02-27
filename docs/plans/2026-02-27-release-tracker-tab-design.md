# Release Tracker Tab — Dashboard Design

**Date:** 2026-02-27
**Status:** Draft (revised after UX review + architecture/python/test/systems review)
**Depends on:** Release pack (already enabled), dashboard modularization (already done)

## Problem Statement

Filigree tracks 9 releases with rich workflow states, dependencies, and nested children — but the dashboard has no release-centric view. Users must piece together release status from the Kanban board and Workflow tab's plan trees. There's no way to see the roadmap at a glance or drill into a specific release's progress.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Approach | Standalone tab + new API endpoints | Clean separation, follows existing tab patterns, server-side rollups keep frontend simple |
| Roadmap layout | Vertical card list | Information-dense, clean, no complex graph layout needed for ~10 releases |
| Drilldown | Nested tree with progress rollup | Full hierarchy visibility (release → epic → milestone → phase → step), collapsible |
| Closed releases | Hidden by default, toggle to show | Keeps the view focused on active work |
| Progress rollup | Server-side recursive descendant counting | Frontend doesn't need to walk the issue tree; backend has the primitives |
| Expand behavior | Multi-expand (multiple releases open at once) | Supports comparing releases side-by-side (e.g. "what's in v1.5.0 vs. what blocks it in v1.4.0") |
| Blocked card treatment | `blocked` badge + `--text-muted` text (no opacity reduction) | `opacity-70` fails WCAG 1.4.3 for secondary text (~2.7:1 contrast) |
| Tree semantics | ARIA treeview pattern | Screen readers need hierarchy information; retrofitting is expensive |
| Tree traversal | Single unified walk (`_build_tree`) computes both tree structure and progress | Avoids duplicate recursive traversal between summary and drilldown endpoints |
| Sort responsibility | Route handler sorts, DB method returns unsorted | Sort order is a UI concern; DB layer should not encode display policy |
| `rolled_back` releases | Shown in active view (status_category is `wip`, not `done`) | Accurate — a rolled-back release needs attention, it is not "finished" |
| Endpoint naming | `/api/releases` (list) + `/api/release/{id}/tree` (detail) | List uses plural, detail uses singular — matches existing `/api/issues` vs `/api/issue/{id}` convention |

## Architecture

```
User clicks "Releases" tab
  → switchView('releases')
  → loadReleases()
  → fetchReleases() → GET /api/releases
  → Render vertical card list

User clicks a release card's expand toggle (▶)
  → fetchReleaseTree(id) → GET /api/release/{id}/tree
  → Show loading skeleton in card body
  → On response: render collapsible nested tree inline below card header

User clicks release title text
  → Opens issue detail panel (existing openDetail(id) behavior)

User clicks issue title in tree
  → Opens issue detail panel (existing behavior)

URL hash:
  → #releases                          (tab only)
  → #releases&release=filigree-eaed4a  (tab + expanded release)
```

### Files to Change

| File | Change |
|------|--------|
| `src/filigree/static/dashboard.html` | Add `btnReleases` button + `releasesView` container div |
| `src/filigree/static/js/views/releases.js` | **New file** — view module with `loadReleases()` |
| `src/filigree/static/js/api.js` | Add `fetchReleases()` + `fetchReleaseTree()` |
| `src/filigree/static/js/app.js` | Import + `registerView("releases", loadReleases)` |
| `src/filigree/static/js/router.js` | Add `releasesView` to `switchView()` toggle list + `parseHash()` support for `&release=<id>` |
| `src/filigree/dashboard_routes/releases.py` | **New file** — `/api/releases` and `/api/release/{id}/tree` endpoints |
| `src/filigree/dashboard.py` | Import and register `releases.create_router()` in `_create_project_router()` |
| `src/filigree/db_planning.py` | Add `get_releases_summary()`, `get_release_tree()`, `_build_tree()`, `_progress_from_subtree()` |
| `tests/core/test_releases.py` | **New file** — DB method unit tests (~30 cases) |
| `tests/api/test_releases_api.py` | **New file** — API integration tests (~20 cases) |
| `tests/api/conftest.py` | Add `release_client` fixture with release pack enabled |

## Backend API

### `GET /api/releases`

Returns all releases with pre-computed progress rollups.

**Query params:**
- `include_released=true` — include done/cancelled releases (default: `false`)

**Response:**
```json
{
  "releases": [
    {
      "id": "filigree-eaed4a",
      "title": "v1.4.0 — Architectural Refactor",
      "status": "planning",
      "status_category": "open",
      "priority": 1,
      "version": "v1.4.0",
      "target_date": null,
      "labels": ["roadmap", "v1.4.0"],
      "blocks": [
        { "id": "filigree-0d43e044c8", "title": "v1.5.0 — Next Foundation" }
      ],
      "blocked_by": [],
      "progress": {
        "total": 47,
        "completed": 12,
        "in_progress": 5,
        "open": 30,
        "pct": 25
      },
      "child_summary": {
        "epics": 3,
        "milestones": 1,
        "tasks": 5,
        "bugs": 1,
        "other": 0,
        "total": 10
      }
    }
  ]
}
```

**Progress computation:** Recursively counts all leaf descendants (issues with no children). A "leaf" is any issue where `children == []`. The status category of each leaf determines which counter it increments. `pct` is `round(completed / total * 100)` (0 if total is 0).

**`child_summary`:** Counts direct children grouped by type. This replaces the old `children_count` field so the card can show "3 epics, 5 tasks, 1 bug" rather than a raw count that doesn't correspond to the progress bar's leaf-based percentage.

**`blocks`/`blocked_by`:** Each entry includes `id` and `title` so the frontend can render clickable links without extra API calls.

**Sort order** (applied in route handler, not DB layer — see Route Handler section): Unblocked releases first, then priority ASC, then `created_at` ASC. Prioritizes actionability — a P2 unblocked release appears before a P1 blocked release because the former has work that can be started today. ISO timestamps sort correctly as strings because `_now_iso()` produces consistent `datetime.now(UTC).isoformat()` format.

### `GET /api/release/{release_id}/tree`

Returns the full nested hierarchy for one release.

**Response:**
```json
{
  "release": { /* issue dict */ },
  "children": [
    {
      "issue": { /* epic dict */ },
      "progress": { "total": 9, "completed": 9, "in_progress": 0, "open": 0, "pct": 100 },
      "children": [
        {
          "issue": { /* milestone dict */ },
          "progress": { "total": 9, "completed": 9, "in_progress": 0, "open": 0, "pct": 100 },
          "children": [
            {
              "issue": { /* step dict — leaf node */ },
              "progress": null,
              "children": []
            }
          ]
        }
      ]
    },
    {
      "issue": { /* task — leaf node */ },
      "progress": null,
      "children": []
    }
  ]
}
```

**Tree structure:** Each node has `issue` (full issue dict), `progress` (null for leaves), and `children` (empty array for leaves). Progress at each level is computed from that node's leaf descendants only.

**Error handling:** Returns 404 if release_id doesn't exist or isn't type `release`.

## Backend DB Methods

Location: `db_planning.py` as new methods on `PlanningMixin`.

### Type definitions

```python
class ProgressDict(TypedDict):
    total: int
    completed: int
    in_progress: int
    open: int
    pct: int

class ChildSummary(TypedDict):
    epics: int
    milestones: int
    tasks: int
    bugs: int
    other: int
    total: int

class IssueRef(TypedDict):
    id: str
    title: str

class TreeNode(TypedDict):
    issue: dict[str, Any]
    progress: ProgressDict | None
    children: list[TreeNode]
```

### `get_releases_summary(*, include_released: bool = False) -> list[dict[str, Any]]`

Returns release data **unsorted** — sort order is applied in the route handler (see Route Handler section).

```python
def get_releases_summary(self, *, include_released: bool = False) -> list[dict[str, Any]]:
    releases = self.list_issues(type="release")
    if not include_released:
        # Note: rolled_back is category "wip", so it IS included (intentional —
        # a rolled-back release needs attention, it is not "finished")
        releases = [r for r in releases if r.status_category != "done"]

    result: list[dict[str, Any]] = []
    for release in releases:
        # Build the full tree once; extract progress from it
        subtree = self._build_tree(release.id)
        progress = self._progress_from_subtree(subtree)

        children = self.list_issues(parent_id=release.id)
        child_summary = self._summarize_children_by_type(children)

        blocks_resolved = self._resolve_issue_refs(release.blocks)
        blocked_by_resolved = self._resolve_issue_refs(release.blocked_by)

        # Build response dict explicitly — do not spread to_dict() and override keys
        data = release.to_dict()
        data["version"] = release.fields.get("version")
        data["target_date"] = release.fields.get("target_date")
        data["blocks"] = blocks_resolved
        data["blocked_by"] = blocked_by_resolved
        data["progress"] = progress
        data["child_summary"] = child_summary
        result.append(data)

    return result
```

### `get_release_tree(release_id: str) -> dict[str, Any]`

```python
def get_release_tree(self, release_id: str) -> dict[str, Any]:
    release = self.get_issue(release_id)  # raises KeyError if not found
    if release.type != "release":
        raise ValueError(f"Issue {release_id} is not a release")
    return {
        "release": release.to_dict(),
        "children": self._build_tree(release.id),
    }
```

### `_build_tree(parent_id: str) -> list[TreeNode]`

**Single unified tree walk** — builds the nested structure and computes progress at each node from its own subtree data. This eliminates the duplicate traversal that would occur if progress were computed separately.

Includes a **recursion depth guard** (max 10 levels) to prevent stack overflow from corrupted `parent_id` cycles.

```python
_MAX_TREE_DEPTH = 10

def _build_tree(self, parent_id: str, *, _depth: int = 0) -> list[TreeNode]:
    if _depth > _MAX_TREE_DEPTH:
        logger.warning("_build_tree: depth limit reached at parent_id=%s", parent_id)
        return []

    children = self.list_issues(parent_id=parent_id)
    nodes: list[TreeNode] = []
    for child in children:
        subtree = self._build_tree(child.id, _depth=_depth + 1)
        progress = self._progress_from_subtree(subtree) if subtree else None
        nodes.append({
            "issue": child.to_dict(),
            "progress": progress,
            "children": subtree,
        })
    return nodes
```

### `_progress_from_subtree(nodes: list[TreeNode]) -> ProgressDict`

Computes progress from an already-built subtree — **no extra SQL queries**. Walks the in-memory tree nodes and counts leaves by status category.

```python
def _progress_from_subtree(self, nodes: list[TreeNode]) -> ProgressDict:
    total = completed = in_progress = open_count = 0
    for node in nodes:
        if not node["children"]:  # leaf node
            cat = node["issue"].get("status_category", "open")
            total += 1
            if cat == "done":
                completed += 1
            elif cat == "wip":
                in_progress += 1
            else:
                open_count += 1
        else:  # recurse into non-leaf's children
            sub = self._progress_from_subtree(node["children"])
            total += sub["total"]
            completed += sub["completed"]
            in_progress += sub["in_progress"]
            open_count += sub["open"]
    pct = round(completed / total * 100) if total > 0 else 0
    return {"total": total, "completed": completed, "in_progress": in_progress, "open": open_count, "pct": pct}
```

### `_summarize_children_by_type(children: list[Issue]) -> ChildSummary`

```python
def _summarize_children_by_type(self, children: list[Issue]) -> ChildSummary:
    counts: ChildSummary = {"epics": 0, "milestones": 0, "tasks": 0, "bugs": 0, "other": 0, "total": len(children)}
    type_map = {"epic": "epics", "milestone": "milestones", "task": "tasks", "bug": "bugs"}
    for child in children:
        key = type_map.get(child.type, "other")
        counts[key] += 1  # type: ignore[literal-required]
    return counts
```

### `_resolve_issue_refs(ids: list[str]) -> list[IssueRef]`

```python
def _resolve_issue_refs(self, ids: list[str]) -> list[IssueRef]:
    refs: list[IssueRef] = []
    for issue_id in ids:
        try:
            issue = self.get_issue(issue_id)
            refs.append({"id": issue.id, "title": issue.title})
        except KeyError:
            logger.warning("_resolve_issue_refs: dangling reference %s", issue_id)
            refs.append({"id": issue_id, "title": "(deleted)"})
    return refs
```

### Performance Notes

**Query pattern:** `_build_tree` calls `list_issues(parent_id=X)` per non-leaf node. Each `list_issues` call goes through `_build_issues_batch` which issues ~7 SQL queries. For a release with 47 descendants across 5 levels, this is ~70-105 SQL queries per tree build. For the summary endpoint (9 active releases), this totals ~600-900 queries.

**Why this is acceptable now:** SQLite with WAL mode on a local machine handles 1000+ queries in <100ms. The summary endpoint is called on tab switch and on auto-refresh interval.

**Refresh amplification:** The global `REFRESH_INTERVAL` is 15 seconds. When the releases tab is active, `loadReleases()` fires every 15 seconds via the `render()` → view loader cycle, producing ~576 queries per refresh for 9 active releases. This is the primary load driver — not the one-time tab-switch cost. The tree re-fetch for expanded releases adds to this. For now this is acceptable (SQLite handles it in <100ms), but if it becomes perceptible, consider: (a) skipping tree re-fetch on auto-refresh when no releases are expanded, or (b) using a longer per-view refresh interval for the releases tab.

**Optimization threshold:** If the project exceeds ~200 release-descendant issues or the summary endpoint latency exceeds 500ms, consider:
1. A single recursive CTE to count descendants in one SQL query
2. Materializing progress counts (cache invalidation on issue status change)

**`get_releases_summary` calls `_build_tree` per release**, which is the same walk that `get_release_tree` performs for drilldown. This means expanding a release after viewing the summary does not repeat work that could have been cached. If this becomes a concern, a shared in-memory cache keyed by `(release_id, max_updated_at)` could avoid the second walk. YAGNI for now.

## Route Handler

`src/filigree/dashboard_routes/releases.py` — follows the `create_router()` factory pattern with the async-over-sync docstring convention from existing route modules.

### `GET /api/releases`

```python
@router.get("/releases")
async def api_releases(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
    """List releases with progress rollups."""
    include_released = _get_bool_param(request.query_params, "include_released", False)
    if not isinstance(include_released, bool):
        return include_released  # propagate the 400 error response

    try:
        releases = db.get_releases_summary(include_released=include_released)
    except Exception:
        logger.exception("Failed to load releases summary")
        return _error_response("Internal error loading releases", "RELEASES_LOAD_ERROR", 500)

    # Sort is a UI concern — applied here, not in the DB layer
    # Unblocked first (actionability), then priority ASC, then created_at ASC
    releases.sort(key=lambda r: (len(r["blocked_by"]) > 0, r["priority"], r["created_at"]))

    return JSONResponse({"releases": releases})
```

### `GET /api/release/{release_id}/tree`

Two exception branches: `KeyError` (not found) and `ValueError` (wrong type) — both return 404.

```python
@router.get("/release/{release_id}/tree")
async def api_release_tree(release_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
    """Release hierarchy tree with progress rollups."""
    try:
        tree = db.get_release_tree(release_id)
    except KeyError:
        return _error_response(f"Release not found: {release_id}", "RELEASE_NOT_FOUND", 404)
    except ValueError as e:
        return _error_response(str(e), "NOT_A_RELEASE", 404)
    return JSONResponse(tree)
```

### Router registration in `dashboard.py`

```python
# In _create_project_router():
from filigree.dashboard_routes import analytics, files, issues, releases

router.include_router(releases.create_router())
```

## Frontend — View Module

### `src/filigree/static/js/views/releases.js`

```
Module-level state:
  expandedReleaseIds = new Set()        // which release cards are expanded (multi-expand)
  releaseTreeCache = new Map()          // Map<releaseId, treeData> — cached trees for expanded releases
  collapsedNodeIds = new Set()          // Set<issueId> — which tree nodes the user has collapsed
  showReleased = false                  // "Show released" toggle state
  loadingReleaseIds = new Set()         // which releases are currently fetching their tree

exports:
  loadReleases()  — registered as view loader

Idempotency: loadReleases() is called on every render() cycle (including global
auto-refresh every 15s and mutations from other views). This is expected and
acceptable — re-fetching GET /api/releases is cheap (<100ms). The function
should not debounce or skip calls; it re-renders the card list from fresh data
each time, preserving expandedReleaseIds and collapsedNodeIds across re-renders.
```

### Layout — Roadmap Overview

```
+----------------------------------------------------------------+
|  Releases                                                      |
|  <label><input type="checkbox" id="showReleased">              |
|    Show released</label>                                       |
+----------------------------------------------------------------+
|                                                                |
|  ┃ [▶] v1.4.0 — Architectural Refactor          [planning]    |
|  ┃ P1  |  3 epics, 5 tasks, 1 bug  |  ████████░░░░ 25%       |
|  ┃ Target: 2026-03-15                                          |
|  ┃ Blocks: v1.5.0, v1.6.0                                     |
|                                                                |
|  ┃ [▶] v1.5.0 — Templated Web UX                [planning]    |
|  ┃ P2  |  1 epic, 3 features, 2 tasks  |  ░░░░░░░░░░░░ 0%    |
|  ┃ Blocked by: v1.4.0                  [blocked]               |
|                                                                |
|  ┃ [▶] v1.6.0 — Extensibility & Agent Context   [planning]    |
|  ┃ P2  |  1 feature, 3 tasks  |  ░░░░░░░░░░░░ 0%             |
|  ┃ Blocked by: v1.4.0                  [blocked]               |
|                                                                |
+----------------------------------------------------------------+

Empty state (when no active releases and toggle is off):

+----------------------------------------------------------------+
|  Releases                                                      |
|  <label><input type="checkbox" id="showReleased">              |
|    Show released</label>                                       |
+----------------------------------------------------------------+
|                                                                |
|    No active releases.                                         |
|    Show completed releases to see release history.             |
|                                                                |
+----------------------------------------------------------------+
```

### Layout — Expanded Release (Drilldown)

When a release card's expand toggle is clicked, the tree renders inline below the card header. The release title remains clickable to open the detail panel.

**Loading state** (while fetching tree):
```
+----------------------------------------------------------+
| ┃ [▼] v1.4.0 — Architectural Refactor       [planning]   |
| ┃ P1  |  3 epics, 5 tasks, 1 bug  |  ████████░░░░ 25%   |
| ┃ Target: 2026-03-15                                      |
| ┃ Blocks: v1.5.0, v1.6.0                                 |
| ┃                                                         |
| ┃  Loading release tree...                                |
| ┃  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  (pulsing skeleton)    |
+----------------------------------------------------------+
```

**Error state** (tree fetch failed):
```
+----------------------------------------------------------+
| ┃ [▼] v1.4.0 — Architectural Refactor       [planning]   |
| ┃ ...                                                     |
| ┃                                                         |
| ┃  Failed to load release tree.  [Retry]                  |
+----------------------------------------------------------+
```

**Loaded state:**
```
+----------------------------------------------------------+
| ┃ [▼] v1.4.0 — Architectural Refactor       [planning]   |
| ┃ P1  |  3 epics, 5 tasks, 1 bug  |  ████████░░░░ 25%   |
| ┃ Target: 2026-03-15                                      |
| ┃ Blocks: v1.5.0, v1.6.0                                 |
| ┃                                              [Collapse all] |
| ┃                                                         |
| ┃  [▼] [epic] Test Suite Reboot       ████████████ 100%  |
| ┃     [▼] [milestone] Phase 1-9       ████████████ 100%  |
| ┃        [step] Phase 1 — fixtures        done            |
| ┃        [step] Phase 2 — core tests      done            |
| ┃        [step] Phase 3 — ...             done            |
| ┃                                                         |
| ┃  [▶] [epic] Type Safety & Contracts  ░░░░░░░░░░░░  0%  |
| ┃  [▶] [epic] File Decomposition       ████████████ 100%  |
| ┃                                                         |
| ┃    [task] Dashboard auth                open             |
| ┃    [task] Define TypedDicts             open             |
| ┃    [task] Add integration test          open             |
+----------------------------------------------------------+
```

### Interaction

| Action | Behavior |
|--------|----------|
| Click release card expand toggle (`[▶]`/`[▼]`) | Toggle expand/collapse for that release. Multi-expand: other cards stay as they are. On expand: show loading skeleton, fetch tree, render inline. On collapse: hide tree (keep cached data). |
| Click release title text | Open issue detail panel for the release itself (reuse existing `openDetail(id)`) |
| Click `[▶]`/`[▼]` arrow on tree node | Toggle child visibility for that node. State stored in `collapsedNodeIds` by issue ID. |
| Click issue title in tree | Open issue detail panel (reuse existing `openDetail(id)`) |
| Click "Collapse all" button | Collapse all expanded tree nodes within that release (add all non-leaf node IDs to `collapsedNodeIds`) |
| Click release name in "Blocks"/"Blocked by" | Scroll to that release card and expand it (if not already expanded). If the target release is hidden (done/cancelled), enable "Show released" first. |
| Toggle "Show released" checkbox | Re-fetch with `?include_released=true`, re-render card list. Preserve `expandedReleaseIds` — any previously-expanded release stays expanded using cached tree data. |
| Global refresh (interval) | Re-fetch summary. For each expanded release, re-fetch its tree. Preserve `collapsedNodeIds` across the data replacement so user's tree position is maintained. |

### URL Hash Routing

Extends the existing `parseHash()` in `router.js`:

```
#releases                          → switch to releases view
#releases&release=filigree-eaed4a  → switch to releases view + auto-expand that release
```

When `switchView('releases')` is called with a `release` param in the hash, the view auto-expands that card and scrolls to it. When the user manually expands/collapses cards, the hash is updated to reflect the first expanded release (or cleared if none are expanded).

### Styling

**Card structure:**
- **Card container:** `bg-[var(--surface-raised)] rounded border-l-4` with status-colored left border
- **Left border color by status:**
  - `planning` → `var(--accent)` (blue)
  - `development`/`frozen`/`testing`/`staged` → amber (`#F59E0B`)
  - `released` → emerald (`#10B981`)
  - `cancelled`/`rolled_back` → red (`#EF4444`)
- **Left border on blocked cards:** Full opacity — the border color signal is preserved even when the card is visually muted

**Blocked release cards:**
- Text color: `color: var(--text-muted)` at full opacity (no `opacity-70` on the card)
- A `[blocked]` badge in `--text-muted` with `border border-[var(--border)]` appears after the "Blocked by:" line
- Left border color remains at full opacity

**Progress bars:**
- Element: `<div role="progressbar" aria-valuenow="25" aria-valuemin="0" aria-valuemax="100" aria-label="Release progress: 25%">`
- Fill color: `var(--accent)` (blue) for the filled portion
- Background: `var(--surface-base)` for the unfilled portion
- Same rounded style as existing plan tree bars in `workflow.js`

**Status badges:** Create a `statusBadge(status, category)` helper in `releases.js` that returns a styled `<span>` with the status text and appropriate color class. No shared utility exists — other views render status inline. Use the existing CSS variables (`--status-open`, `--status-done`, etc.) for colors. **Note:** Verify contrast of `--status-open` (#64748B) and `--status-done` (#7B919C) against `--surface-raised` in browser accessibility panel at `text-xs` size. If below 4.5:1, switch to outline-style badges where text uses `--text-primary` and the badge has a `border` only.

**Priority:** Reuse existing priority color scheme (P0=red, P1=orange, P2=default, P3=muted)

**Target date:** Rendered in `--text-muted` after the stats row, only when `target_date` is non-null. No placeholder when absent.

**Tree indentation:**
- `ml-6` (24px) per level, with `border-l border-[var(--border)]` connector line
- **Depth cap:** At level 4+ (relative to the release), indentation stops increasing. A path breadcrumb (e.g. `… / Phase 3 /`) replaces deeper nesting to prevent titles from being squeezed at 5+ levels. Alternatively, titles use `truncate` with a `title` attribute for the full text.
- Maximum visual indent: `ml-[72px]` (3 levels × 24px)

**Stats row wrapping:** The row `P1 | 3 epics, 5 tasks | ████ 25%` uses `flex flex-wrap gap-2` so it wraps gracefully at narrow widths instead of overflowing.

**Leaf nodes:** No toggle arrow, inline status badge
**Non-leaf nodes:** `<button>` toggle arrow, progress bar inline

### Accessibility

#### ARIA Tree Pattern

The collapsible hierarchy uses the WAI-ARIA treeview pattern:

```html
<!-- Tree container -->
<ul role="tree" aria-label="Release v1.4.0 children">

  <!-- Non-leaf node (expandable) -->
  <li role="treeitem" aria-expanded="true" aria-level="1">
    <div class="flex items-center">
      <button aria-label="Collapse Test Suite Reboot"
              class="min-h-[44px] min-w-[44px] flex items-center justify-center">
        ▼
      </button>
      <button class="text-left hover:underline" onclick="openDetail('...')">
        [epic] Test Suite Reboot
      </button>
      <!-- progress bar with ARIA -->
      <div role="progressbar" aria-valuenow="100" aria-valuemin="0"
           aria-valuemax="100" aria-label="Test Suite Reboot progress: 100%">
        ...
      </div>
    </div>

    <!-- Child group -->
    <ul role="group">
      <li role="treeitem" aria-level="2">
        <!-- leaf node: no toggle button, just title + status -->
        <button class="text-left hover:underline ml-6" onclick="openDetail('...')">
          [step] Phase 1 — fixtures
        </button>
        <span>done</span>
      </li>
    </ul>
  </li>

  <!-- Leaf node (no children) -->
  <li role="treeitem" aria-level="1">
    <button class="text-left hover:underline" onclick="openDetail('...')">
      [task] Dashboard auth
    </button>
    <span>open</span>
  </li>
</ul>
```

**Key ARIA attributes:**
- `role="tree"` on the outermost `<ul>`
- `role="treeitem"` on each `<li>`
- `aria-expanded="true|false"` on expandable treeitems
- `aria-level="N"` on each treeitem (1-based, relative to tree root)
- `role="group"` on child `<ul>` elements
- `aria-label` on toggle buttons ("Collapse X" / "Expand X")

#### Keyboard Navigation

Tree nodes support keyboard navigation following the ARIA treeview spec:

| Key | Behavior |
|-----|----------|
| `Tab` | Moves focus into the tree (first visible node). Next `Tab` exits the tree. |
| `↓` | Move focus to next visible treeitem |
| `↑` | Move focus to previous visible treeitem |
| `→` | If collapsed: expand. If expanded: move to first child. If leaf: no-op. |
| `←` | If expanded: collapse. If collapsed/leaf: move to parent. |
| `Enter` / `Space` | On toggle button: expand/collapse. On title: open detail panel. |
| `Home` | Move focus to first treeitem |
| `End` | Move focus to last visible treeitem |

Implementation: a single `keydown` handler on the `role="tree"` container that manages `tabindex="-1"` on all treeitems except the currently focused one (`tabindex="0"`), using roving tabindex pattern.

#### Touch Targets

Toggle buttons (`[▶]`/`[▼]`) use:
```css
min-height: 44px;
min-width: 44px;
display: flex;
align-items: center;
justify-content: center;
```

This satisfies WCAG 2.5.8 (Target Size) at Level AA. The existing `@media (pointer: coarse)` rule in `dashboard.html` (line 102) already handles touch-device sizing; the new buttons inherit this.

#### Progress Bar Accessibility

Every progress bar element includes:
```html
<div role="progressbar"
     aria-valuenow="25"
     aria-valuemin="0"
     aria-valuemax="100"
     aria-label="Release progress: 25%">
```

For tree node progress bars, the label includes the node name: `aria-label="Test Suite Reboot progress: 100%"`.

#### Checkbox Label

The "Show released" toggle follows the existing dashboard pattern:
```html
<label for="showReleased" class="flex items-center gap-1 text-xs cursor-pointer">
  <input type="checkbox" id="showReleased"
         style="accent-color: var(--accent)"
         aria-label="Show released and cancelled releases">
  Show released
</label>
```

### State Preservation Across Refresh

When the global auto-refresh fires:

1. Save current `expandedReleaseIds`, `collapsedNodeIds`, and scroll position
2. Re-fetch `GET /api/releases` (summary)
3. For each release in `expandedReleaseIds`, re-fetch `GET /api/release/{id}/tree`
4. Re-render the card list
5. **Drain stale IDs from `collapsedNodeIds`:** collect all issue IDs present in the newly-fetched tree data, then remove any ID from `collapsedNodeIds` that no longer appears. This prevents unbounded accumulation of IDs from deleted/moved issues.
6. For each expanded release, re-render the tree using the new data but the preserved (and drained) `collapsedNodeIds`
7. Restore scroll position

```javascript
// Drain stale collapsed node IDs
function collectActiveIds(treeNodes, activeIds = new Set()) {
  for (const node of treeNodes) {
    activeIds.add(node.issue.id);
    if (node.children.length) collectActiveIds(node.children, activeIds);
  }
  return activeIds;
}

// After re-fetching all expanded trees:
const activeIds = new Set();
for (const [, tree] of releaseTreeCache) collectActiveIds(tree.children, activeIds);
for (const id of collapsedNodeIds) {
  if (!activeIds.has(id)) collapsedNodeIds.delete(id);
}
```

This ensures a user who has drilled down to a specific phase does not lose their place on refresh, while preventing `collapsedNodeIds` from growing unboundedly.

## Frontend — Filter Bar Integration

The existing header filter controls (priority filter, search box, status checkboxes) have **no effect** on the Releases view. The releases view has its own "Show released" toggle as its only filter. This matches the pattern of the Metrics and Activity views, which also ignore the header filters.

If the search box is non-empty when switching to Releases, it is visually present but functionally ignored. This is consistent with existing behavior — the Metrics tab also ignores the search box.

## MCP Tool Queries

Agents interacting with the release tracker via the Filigree MCP server can use these existing tools:

### Discovering releases

```
# List all open releases
mcp__filigree__list_issues(type="release", status_category="open")

# List all releases including closed
mcp__filigree__list_issues(type="release")

# Get a specific release with full details
mcp__filigree__get_issue(id="filigree-eaed4a", include_transitions=true)

# Search releases by keyword
mcp__filigree__search_issues(query="v1.5.0")
```

### Release progress and hierarchy

```
# Get a release's plan tree (works for releases with milestone children)
mcp__filigree__get_plan(milestone_id="filigree-eaed4a")

# List direct children of a release
mcp__filigree__list_issues(parent_id="filigree-eaed4a")

# Find what's ready to work on within a release (filter by label)
mcp__filigree__list_issues(label="v1.4.0", status_category="open")

# Get all blocked issues (then filter by release context)
mcp__filigree__get_blocked()

# Get all ready issues (then filter by release context)
mcp__filigree__get_ready()
```

### Release workflow management

```
# See valid state transitions for a release
mcp__filigree__get_valid_transitions(issue_id="filigree-eaed4a")

# Advance a release to development
mcp__filigree__update_issue(id="filigree-eaed4a", status="development")

# Freeze a release (requires version field to be set)
mcp__filigree__update_issue(id="filigree-eaed4a", status="frozen")

# Close/release
mcp__filigree__close_issue(id="filigree-eaed4a", reason="Shipped successfully")
```

### Release planning

```
# Create a new release
mcp__filigree__create_issue(
    title="v1.8.0 — Feature Name",
    type="release",
    priority=2,
    fields={"version": "v1.8.0", "target_date": "2026-04-01"},
    labels=["roadmap", "v1.8.0"]
)

# Add a dependency (v1.8.0 depends on v1.7.0)
mcp__filigree__add_dependency(from_id="<v1.8.0-id>", to_id="<v1.7.0-id>")

# Reparent an epic under a release
mcp__filigree__update_issue(id="<epic-id>", parent_id="<release-id>")

# Create a full plan under a release
mcp__filigree__create_plan(
    milestone={"title": "Feature Milestone", "priority": 2},
    phases=[
        {"title": "Phase 1", "steps": [
            {"title": "Step 1A"},
            {"title": "Step 1B", "deps": [0]}
        ]}
    ]
)
# Then reparent the milestone under the release
```

### Release metrics and status

```
# Get project-wide stats (includes counts by type)
mcp__filigree__get_stats()

# Get flow metrics (cycle time, throughput)
mcp__filigree__get_metrics(days=30)

# Get critical path (longest dependency chain)
mcp__filigree__get_critical_path()

# Get the project summary
mcp__filigree__get_summary()
```

### Useful compound queries for release context

```
# "What's blocking v1.5.0?"
1. mcp__filigree__get_issue(id="filigree-0d43e044c8")  → check blocked_by
2. For each blocker: mcp__filigree__get_issue(id=blocker_id) → check status

# "How much of v1.4.0 is done?"
1. mcp__filigree__get_plan(milestone_id="filigree-eaed4a")
   → returns total_steps / completed_steps
2. For deeper analysis: mcp__filigree__list_issues(parent_id="filigree-eaed4a")
   → then recursively check each child's children

# "What can I work on right now for v1.4.0?"
1. mcp__filigree__get_ready()
2. Filter results where labels include "v1.4.0" or parent chain leads to release
```

### Progress metric note

**Two progress metrics exist and they measure different things:**

- **`get_plan(milestone_id)`** counts **steps only** in a fixed 3-level hierarchy (milestone → phase → step). Returns `total_steps` / `completed_steps`.
- **The release tab** counts **all leaf descendants** at any depth (release → epic → milestone → phase → step). Returns `progress.total` / `progress.completed`.

For a release that contains milestones (which contain phases, which contain steps), these two methods will return different numbers. `get_plan` is the right tool for milestone-specific progress. The release tab's `GET /api/releases` is the right tool for release-wide progress across all children regardless of type or depth.

## Scope Exclusions (YAGNI)

- **No drag-and-drop reordering** of releases
- **No inline editing** of release fields (use issue detail panel)
- **No Gantt chart** or timeline visualization
- **No release notes generation** (future feature)
- **No release comparison** (diff between releases)
- **No caching** of progress rollups (premature optimization for ~80 issues)

## Testing Strategy

### Fixtures required

**`release_db` fixture** (for DB-layer tests in `tests/core/test_releases.py`):

The `release_db` fixture already exists in `tests/workflows/conftest.py`, but pytest fixtures are not importable across sibling conftest directories. **Duplicate** the fixture in `tests/core/conftest.py` with a comment noting the canonical source:

```python
# Duplicated from tests/workflows/conftest.py — keep in sync
@pytest.fixture
def release_db(tmp_path: Path) -> FiligreeDB:
    return make_db(tmp_path, packs=["core", "planning", "release"])
```

**`release_client` fixture** (for API tests in `tests/api/test_releases_api.py`):

The existing `client` fixture uses `populated_db` (no release pack), so `type="release"` issues cannot be created. A new fixture is required:

```python
@pytest.fixture
def release_dashboard_db(tmp_path: Path) -> FiligreeDB:
    return make_db(tmp_path, packs=["core", "planning", "release"], check_same_thread=False)

@pytest.fixture
async def release_client(release_dashboard_db: FiligreeDB) -> AsyncIterator[AsyncClient]:
    dash_module._db = release_dashboard_db
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._db = None
```

**`make_release_hierarchy` helper** (reduces duplication across test classes):

```python
def make_release_hierarchy(db, *, include_done=False):
    """Returns (release, epic, task) for standard 3-level test hierarchy."""
    release = db.create_issue("v1.0.0", type="release", fields={"version": "v1.0.0"})
    epic = db.create_issue("Epic A", type="epic", parent_id=release.id)
    task = db.create_issue("Task A", type="task", parent_id=epic.id)
    if include_done:
        db.close_issue(task.id)
    return release, epic, task
```

### DB method tests — `tests/core/test_releases.py`

#### `TestGetReleasesSummary` (~16 cases)

| Test case | Asserts |
|-----------|---------|
| `test_returns_only_active_releases_by_default` | 2 planning + 1 released → len == 2 |
| `test_include_released_flag_returns_all` | include_released=True → len == 3 |
| `test_rolled_back_release_is_included_in_active` | status=rolled_back (category=wip) IS returned by default |
| `test_cancelled_release_excluded_by_default` | status=cancelled (category=done) NOT returned |
| `test_empty_release_no_children` | progress == {total:0, completed:0, ...}, child_summary all zeros |
| `test_progress_counts_only_leaf_descendants` | release → epic → task: total==1 (task, not epic) |
| `test_progress_pct_calculation` | 1/3 done → pct==33 |
| `test_progress_pct_zero_when_no_leaves` | No children → pct==0 (not ZeroDivisionError) |
| `test_progress_pct_100_when_all_complete` | 2/2 done → pct==100 |
| `test_intermediate_nodes_not_counted_as_leaves` | release → epic → 2 tasks: total==2 |
| `test_child_summary_counts_by_type` | 2 epics, 1 task, 1 bug → correct ChildSummary |
| `test_child_summary_other_bucket` | release_item child → other==1 |
| `test_blocks_resolved_to_id_and_title` | dependency → blocks == [{id, title}] |
| `test_blocked_by_resolved_to_id_and_title` | blocked_by == [{id, title}] |
| `test_resolve_refs_handles_deleted_issue` | Dangling dep → title=="(deleted)" |
| `test_version_and_target_date_from_fields` | fields.version + fields.target_date present |
| `test_version_null_when_absent` | No fields → version is None |

#### `TestGetReleaseTree` (~10 cases)

| Test case | Asserts |
|-----------|---------|
| `test_returns_release_and_children_keys` | result has "release" and "children" |
| `test_flat_release_with_leaf_children` | release → 2 tasks: all progress==None |
| `test_nested_tree_structure` | release → epic → milestone → step: correct nesting |
| `test_progress_on_non_leaf_nodes` | epic with 2 tasks (1 done, 1 open) → pct==50 |
| `test_progress_null_on_leaf_nodes` | task → progress is None |
| `test_empty_release` | No children → children==[] |
| `test_raises_keyerror_for_nonexistent_id` | KeyError raised |
| `test_raises_valueerror_for_non_release_type` | epic ID → ValueError |
| `test_deeply_nested_five_levels` | Completes without recursion error |
| `test_mixed_leaf_and_nonleaf_at_same_level` | epic (has children) + task (leaf) at same level |

#### `TestProgressFromSubtree` (~5 cases)

| Test case | Asserts |
|-----------|---------|
| `test_single_leaf_done` | {total:1, completed:1, pct:100} |
| `test_wip_increments_in_progress` | status_category=wip → in_progress==1 |
| `test_open_increments_open` | status_category=open → open==1 |
| `test_rounding_at_boundary` | 1 of 3 → pct==33 |
| `test_empty_nodes_list` | [] → {total:0, pct:0} |

#### `TestBuildTree` (~3 cases)

| Test case | Asserts |
|-----------|---------|
| `test_depth_guard_at_10_levels` | 11-deep chain → returns [] at depth 11, no crash |
| `test_returns_empty_for_no_children` | parent with no children → [] |
| `test_sort_order_follows_list_issues` | Children returned in priority/created_at order |

### API tests — `tests/api/test_releases_api.py`

#### `TestGetReleasesEndpoint` (~10 cases)

| Test case | Asserts |
|-----------|---------|
| `test_returns_200_with_releases_key` | status==200, "releases" in json |
| `test_excludes_done_releases_by_default` | 1 planning + 1 released → len==1 |
| `test_include_released_shows_all` | ?include_released=true → len==2 |
| `test_include_released_false_is_default` | ?include_released=false → same as no param |
| `test_invalid_include_released_returns_400` | ?include_released=maybe → status==400 |
| `test_response_shape` | Each release has: id, title, status, progress, child_summary, blocks, blocked_by |
| `test_progress_shape` | progress has: total, completed, in_progress, open, pct (all ints) |
| `test_blocks_are_id_title_objects` | Not bare string list |
| `test_empty_releases` | No release issues → releases==[] |
| `test_sort_order_unblocked_before_blocked` | Unblocked P2 before blocked P1 |

#### `TestGetReleaseTreeEndpoint` (~8 cases)

| Test case | Asserts |
|-----------|---------|
| `test_returns_200_with_tree` | status==200, "release" and "children" in json |
| `test_nonexistent_id_returns_404` | status==404, RELEASE_NOT_FOUND |
| `test_non_release_type_returns_404` | epic ID → status==404, NOT_A_RELEASE |
| `test_tree_structure_shape` | Nested children with correct types |
| `test_leaf_has_null_progress` | Leaf → progress==null |
| `test_non_leaf_has_progress_dict` | Non-leaf → progress has total/completed/pct |
| `test_empty_release_returns_empty_children` | children==[] |
| `test_release_with_only_direct_tasks` | 3 direct tasks → len==3, all progress==null |

### Accessibility — manual verification checklist

- [ ] Keyboard-only navigation through tree (Tab, arrows, Enter/Space)
- [ ] Screen reader announces tree structure (VoiceOver / NVDA)
- [ ] Progress bars announced with percentage
- [ ] Toggle buttons have descriptive labels
- [ ] Status badge contrast ≥ 4.5:1 at `text-xs` size
- [ ] Touch targets ≥ 44px on coarse-pointer devices

## Review Changelog

### UX Review (2026-02-27) — 2 critical, 8 major, 7 minor

| # | Severity | Issue | Resolution |
|---|----------|-------|------------|
| 1 | Critical | No ARIA roles for collapsible tree | Added full ARIA treeview spec: `role="tree"`, `role="treeitem"`, `aria-expanded`, `aria-level`, `role="group"` with HTML examples |
| 2 | Critical | Toggle arrows are bare Unicode, not buttons | Specified `<button>` elements with `min-h-[44px] min-w-[44px]` and `aria-label` |
| 3 | Major | No loading state for tree fetch | Added loading skeleton wireframe and `loadingReleaseIds` state |
| 4 | Major | Accordion vs multi-expand not specified | Decided multi-expand with `expandedReleaseIds: Set` |
| 5 | Major | `children_count` vs progress bar measure different things | Replaced `children_count` with `child_summary` (type breakdown: "3 epics, 5 tasks") |
| 6 | Major | Blocks/blocked-by not clickable | Made links: click scrolls to target card and expands it. API returns `{id, title}` objects |
| 7 | Major | Card title click ambiguous | Split affordances: toggle button for expand/collapse, title text for detail panel |
| 8 | Major | Progress bars lack ARIA | Added `role="progressbar"` with `aria-valuenow/min/max` and descriptive `aria-label` |
| 9 | Major | `opacity-70` on blocked cards fails WCAG contrast | Replaced with `--text-muted` at full opacity + `[blocked]` badge |
| 10 | Major | Per-node collapse state lost on refresh | Added `collapsedNodeIds: Set<string>` preserved across refresh cycles |
| 11 | Minor | `target_date` not shown on cards | Added to card layout, rendered in `--text-muted` when non-null |
| 12 | Minor | No empty state | Added "No active releases" message with prompt to show completed |
| 13 | Minor | Sort prioritizes priority over actionability | Changed to: unblocked first, then priority ASC, then created_at |
| 14 | Minor | No "collapse all" for deep trees | Added per-card "Collapse all" button in expanded view |
| 15 | Minor | Stats row overflow at narrow widths | Specified `flex flex-wrap gap-2` for graceful wrapping |
| 16 | Minor | Status badge contrast unverified | Added verification checklist item; noted outline-badge fallback if below 4.5:1 |
| 17 | Minor | No URL hash routing for expanded release | Added `#releases&release=<id>` to hash scheme with `parseHash()` integration |

### Architecture + Python + Test + Systems Review (2026-02-27) — 5 critical, 9 high

| # | Severity | Source | Issue | Resolution |
|---|----------|--------|-------|------------|
| 18 | Critical | Python | `_compute_progress()` called but never defined | Replaced with `_progress_from_subtree()` that walks already-built in-memory tree nodes (no extra SQL). Full implementation provided. |
| 19 | Critical | Python | Dict spread + key override is fragile | Changed to explicit `data = release.to_dict(); data["key"] = value` pattern matching `api_issue_detail` convention |
| 20 | Critical | Python | Missing type hints on all new methods | Added `TypedDict` definitions (`ProgressDict`, `ChildSummary`, `IssueRef`, `TreeNode`) and full type annotations on every method signature |
| 21 | Critical | Test | `rolled_back` status ambiguity — wip not done | Explicitly documented in Decisions table: `rolled_back` IS shown (category=wip). Added `test_rolled_back_release_is_included_in_active` test case. |
| 22 | Critical | Test | `release_client` fixture needed — existing fixture has no release pack | Added fixture definitions with release pack + `make_release_hierarchy` helper in Testing Strategy section |
| 23 | High | Architect | N+1 query amplification (~1000+ queries on summary) | Documented in Performance Notes with query count analysis, acceptable threshold, and optimization strategies for when it matters |
| 24 | High | Architect+Systems | Duplicate tree traversal between summary and drilldown | Unified: `_build_tree` is the single recursive walk. `_progress_from_subtree` computes stats from its in-memory output. `_count_descendants` removed entirely. |
| 25 | High | Architect | Endpoint path inconsistency (plural vs singular) | Changed detail to `/api/release/{id}/tree` (singular) matching existing `/api/issue/{id}` convention. List stays `/api/releases` (plural). |
| 26 | High | Architect+Python | `dashboard.py` missing from Files to Change | Added to table with explicit change: `router.include_router(releases.create_router())` |
| 27 | High | Test | Testing strategy is a placeholder | Expanded to ~52 specific test cases across 6 test classes with fixture definitions and helper functions |
| 28 | High | Architect+Systems | Sort order is UI intent embedded in DB layer | Moved sort to route handler. `get_releases_summary()` returns unsorted. Route handler applies `(unblocked, priority, created_at)` sort. |
| 29 | High | Systems | `collapsedNodeIds` has no drain — stale IDs accumulate | Added `collectActiveIds` drain function to State Preservation section with implementation code |
| 30 | High | Systems | Two competing progress metrics (get_plan vs release tab) | Added "Progress metric note" to MCP section clarifying that `get_plan` counts steps (3-level) and release tab counts leaves (any depth) |
| — | Warning | Python | `_resolve_issue_refs` silently swallows KeyError | Added `logger.warning()` before suppressing |
| — | Warning | Python | Route needs `_get_bool_param` for boolean parsing | Added to route handler pseudocode with 400 response propagation |
| — | Warning | Python | Route needs two exception branches (KeyError + ValueError) | Added explicit two-branch handling in route handler pseudocode |
| — | Suggestion | Python | Add recursion depth guard to `_build_tree` | Added `_MAX_TREE_DEPTH = 10` with `logger.warning()` on breach |
| — | Suggestion | Python | Use `TypedDict` for `child_summary` | Added `ChildSummary` TypedDict definition |
| — | Suggestion | Systems | Document `_build_tree` sort semantics | Noted in Performance Notes: children inherit `list_issues` sort order (priority, created_at) |
