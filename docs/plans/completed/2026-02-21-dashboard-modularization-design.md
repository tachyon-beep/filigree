# Dashboard Modularization Design

**Issue:** filigree-1623b4 — Refactor dashboard.html to multi-file component architecture
**Date:** 2026-02-21
**Status:** Draft — awaiting review

---

## Problem

`dashboard.html` is 2686 lines — a monolithic single-file SPA containing all HTML, CSS, and JavaScript. Five expert reviewers independently flagged this as a maintainability cliff. Phase 5 would add 5 new views (file list, file detail, timeline, hotspot dashboard, scan feedback), pushing it to ~3500-5500 LOC.

The single-file architecture creates:
- Merge conflicts when multiple changes touch the same file
- Difficulty reasoning about which code belongs to which view
- No ability to test JavaScript modules in isolation
- Cognitive overload for new contributors

## Approach: ES Modules with StaticFiles Mount

**Selected approach:** Browser-native ES modules (`import`/`export`) served via FastAPI's `StaticFiles` mount.

**Why this over alternatives:**
- **vs. IIFE/namespace pattern:** ES modules give proper dependency declarations, no global namespace pollution, and the browser handles load ordering.
- **vs. build step (esbuild/vite):** Adding a bundler violates the "no build step" constraint and complicates the development workflow for a tool that ships as a pip package.
- **vs. dynamic `<script>` loading:** Manual load ordering is fragile and doesn't scale.

**Key trade-off:** ES modules require HTTP serving (they don't work via `file://` protocol). This is fine — the dashboard is always served via the FastAPI server.

## Target File Structure

```
src/filigree/static/
├── dashboard.html          (~250 lines: HTML structure + CSS custom properties)
└── js/
    ├── app.js              (entry point: init, project loading, auto-refresh)
    ├── state.js            (global state vars, constants, config)
    ├── api.js              (all API calls, shared fetch wrapper)
    ├── router.js           (hash-based routing, view switching)
    ├── filters.js          (filter logic, presets, search)
    ├── ui.js               (toast, popover, modals, tour, theme, batch bar)
    └── views/
        ├── kanban.js       (kanban board + drag-and-drop)
        ├── graph.js        (cytoscape graph + critical path)
        ├── detail.js       (detail panel + transitions + comments)
        ├── metrics.js      (metrics view)
        ├── activity.js     (activity view)
        └── workflow.js     (workflow diagram)
```

**12 JS files total** — each under 300 lines. Views are isolated; shared code is consolidated into 5 utility modules.

## State Management

The current code uses ~20 global `var` declarations. The modular approach:

1. **`state.js`** exports a single mutable `state` object and all constants:
   ```js
   export const state = {
     allIssues: [],
     allDeps: [],
     issueMap: {},
     currentView: 'kanban',
     // ... etc
   };
   export const PRIORITY_COLORS = { ... };
   ```

2. All modules `import { state } from '../state.js'` and read/write the shared object.

3. This works because ES module imports are **live bindings** — all modules see the same object reference.

## Backend Change: StaticFiles Mount

Currently `dashboard.py` reads `dashboard.html` and returns it inline:
```python
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (STATIC_DIR / "dashboard.html").read_text()
    return HTMLResponse(html)
```

The change:
```python
from starlette.staticfiles import StaticFiles

# Mount static files AFTER all API routes
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
```

The root `/` route stays — it continues to serve the HTML shell. The new `/static/` mount enables the browser to fetch `js/app.js`, `js/views/kanban.js`, etc.

## CDN Dependencies

Cytoscape, Dagre, and Cytoscape-Dagre load via CDN `<script>` tags in `dashboard.html`. Since CDN scripts execute before ES modules (which are deferred by default), these libraries are available as globals (`window.cytoscape`, `window.dagre`).

Only `graph.js` and `workflow.js` use Cytoscape — they access it via `window.cytoscape`. No change needed.

## What Stays in dashboard.html

- `<!DOCTYPE html>` and `<head>` (meta, CDN scripts)
- `<style>` block with CSS custom properties (dark/light theme vars, custom classes, animations, media queries) — ~120 lines
- HTML structure (nav bar, view containers, detail panel, footer, batch bar, toast container) — ~130 lines
- A single `<script type="module" src="/static/js/app.js"></script>` entry point

## Extraction Strategy

**Bottom-up order** — extract leaf dependencies first, then dependents:

1. `state.js` — no deps (constants + state variables)
2. `api.js` — depends only on state (for `API_BASE`)
3. `ui.js` — depends on state (for theme), standalone DOM utilities
4. `router.js` — depends on state
5. `filters.js` — depends on state, api
6. Views (each depends on state + api + ui):
   - `metrics.js` (simplest, ~80 lines)
   - `activity.js` (~40 lines)
   - `workflow.js` (~70 lines)
   - `graph.js` (~150 lines)
   - `detail.js` (~200 lines)
   - `kanban.js` (most complex, ~300 lines)
7. `app.js` — entry point, imports everything, wires init

Each extraction is independently verifiable: after extracting a module, the dashboard should work identically.

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Circular imports between modules | Bottom-up extraction order avoids this. State is the root; views are leaves. |
| CDN globals not available in ES modules | CDN `<script>` tags execute before `<script type="module">` (deferred). Verified by browser spec. |
| Breaking existing test suite | Tests hit API endpoints, not JS. The only HTML test checks `"Filigree" in resp.text` — still passes. |
| Load order bugs | ES modules handle dependency resolution automatically. No manual ordering. |
| Performance (more HTTP requests) | 12 small files, localhost only. HTTP/2 multiplexing handles this. Could add `modulepreload` hints if needed. |
