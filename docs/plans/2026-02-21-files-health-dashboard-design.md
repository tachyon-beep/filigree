# Files & Code Health Dashboard Views — Design

## Summary

Add two new top-level dashboard tabs — **Files** and **Code Health** — providing UI for the file records, scan findings, and remediation features whose backend APIs are already complete.

Covers filigree steps: ca796b (File List), 7e1b95 (File Detail), cbc0cd (File Timeline), 078908 (Hotspot Dashboard), 64066c (Scan Ingestion Feedback).

## Navigation

Two new top-level tabs added to the dashboard header:

```
[Kanban] [Graph] [Metrics] [Activity] [Workflow] [Files] [Health]
```

- **Files tab**: File list table → click row → slide-in detail panel with Findings/Timeline tabs
- **Code Health tab**: 2x2 widget grid (hotspots, severity donut, scan coverage, recent scans)

## Architecture

### New files

| File | Purpose |
|------|---------|
| `static/js/views/files.js` | File List + File Detail panel + Timeline tab |
| `static/js/views/health.js` | Code Health: hotspots, findings donut, scan coverage, scan history |

### Modified files

| File | Changes |
|------|---------|
| `static/dashboard.html` | Add `#filesView`, `#healthView` containers + nav buttons |
| `static/js/app.js` | Import views, register with router, wire callbacks |
| `static/js/api.js` | Add file/findings/hotspot fetch functions |
| `static/js/router.js` | Add `files` and `health` to view switching |
| `static/js/state.js` | Add `files`, `fileDetail`, `hotspots`, `fileSchema` state fields |

No backend changes required.

## Files Tab

### File List (step ca796b)

Sortable table with columns: Path (clickable), Language, Critical, High, Medium, Low, Issues, Last Scan.

Health indicator via left border color:
- Red: any critical findings
- Orange: any high (no critical)
- Yellow: medium only
- Green: all clear

Interactions: column header sort, path search box, "critical only" toggle, prev/next pagination.

API: `GET /api/files?limit=25&offset=0&sort=updated_at`

### File Detail (step 7e1b95)

Reuses existing `#detailPanel` (500px slide-in). Contains:

1. File header: path, language, file_type, timestamps
2. Summary bar: colored severity badges with counts
3. Internal tabs: `[Findings]` `[Timeline]`
4. Findings tab (default): severity-sorted list, accordion expand for detail
5. Associated issues section: clickable, links to issue detail via `openDetail()`
6. Action buttons: "Link to Issue" modal, "Create Issue" per finding

APIs: `GET /api/files/{id}`, `GET /api/files/{id}/findings`

### File Timeline (step cbc0cd)

Tab within file detail panel. Vertical timeline with colored event dots:
- Red: finding_created (with severity badge)
- Blue: finding_updated (status change)
- Green: association_created

Collapsible events, "Load more" pagination, filter pills `[All] [Findings] [Associations]`.

API: `GET /api/files/{id}/timeline?limit=20`

## Code Health Tab

### Layout: 2x2 widget grid

```
+----------------------------+----------------------------+
| Top 10 Hotspot Files       | Findings by Severity       |
| (ranked list + bars)       | (CSS conic-gradient donut) |
+----------------------------+----------------------------+
| Scan Coverage              | Recent Scan Activity       |
| (progress bar)             | (last 5 ingest runs)       |
+----------------------------+----------------------------+
```

### Widget 1: Top 10 Hotspot Files
Ranked list from `GET /api/files/hotspots`. Each row: truncated path, stacked severity bar, total score. Click → opens file detail in Files tab.

### Widget 2: Findings by Severity
CSS-only donut (conic-gradient). Segments: critical/high/medium/low/info. Center: total count. Legend below.

### Widget 3: Scan Coverage
Progress bar: files with findings / total tracked files.

### Widget 4: Recent Scan Activity
Derived from file update timestamps (no scan_runs table). Shows recent scan-like activity with file counts and timestamps. Lightweight client-side approach per spec.

## Deferred

- Heatmap (files x severity matrix) — requires canvas/SVG library
- Trend chart (findings over time) — requires time-series endpoint
- Full scan_runs table — separate feature
