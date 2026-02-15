# Dashboard Feature Requirements

Synthesized from a three-agent brainstorming session (UX Critic, Systems Thinking Leverage Analyst, API Architect) reviewing the current filigree web dashboard.

## Key Insight

The data layer already supports every feature below. The bottleneck is purely at the API/presentation layer — `analytics.py` computes metrics, `core.py` has full CRUD, the events table tracks all actor activity, and the dependency DAG is already materialized. The dashboard currently surfaces only a fraction of available information and is entirely read-only.

## Requirements

### Phase 1: Must Have

These address fundamental gaps that make the dashboard a useful tool rather than a passive monitor.

---

**R1. Inline Status Transitions**

Enable users to change issue status directly from the detail panel. Show valid next states as a button group, with greyed-out states showing missing field tooltips. One-click state changes with immediate visual feedback.

| Aspect | Detail |
|--------|--------|
| Priority | Must Have |
| Sources | UX (#1), Systems (#6), API (#1) — unanimous |
| API | `GET /api/issue/{id}/transitions`, `PATCH /api/issue/{id}` |
| Backend | `templates.get_valid_transitions()`, `db.update_issue()` |
| Complexity | Low |
| Rationale | The #1 limitation. Supervisors see problems but must context-switch to CLI to act. Breaks the observe-act feedback loop. |

---

**R2. Activity Feed / What Changed**

A chronological feed of recent events across all issues — status changes, new comments, new issues, dependency changes. Filterable by time range, event type, and actor. Answers the supervisor's first question: "what happened while I was away?"

| Aspect | Detail |
|--------|--------|
| Priority | Must Have |
| Sources | UX (#2), Systems (#1) |
| API | `GET /api/activity?since=...&limit=...&actor=...` |
| Backend | `events` table, `get_events_since()` |
| Complexity | Medium |
| Rationale | Returning supervisors have no way to see recent changes without checking every issue. Also serves as agent heartbeat — shows which agents are active vs. silent. |

---

**R3. Flow Metrics Panel**

Surface existing `analytics.py` data: throughput (issues closed/period), average cycle time, average lead time, broken down by type. Show as a new "Metrics" tab with period selector (7d / 30d / 90d).

| Aspect | Detail |
|--------|--------|
| Priority | Must Have |
| Sources | UX (#15), Systems (#2), API (#5) — all three |
| API | `GET /api/metrics?days=30` |
| Backend | `get_flow_metrics()` in `analytics.py` — already implemented |
| Complexity | Low — backend code already exists |
| Rationale | Lowest-effort, highest-information-gain feature. The code is literally written but not exposed. Transforms dashboard from "snapshot viewer" to "system health monitor." |

---

**R4. WIP Aging Indicators**

Color-code in-progress issues by staleness. Fresh (< 1h) = default, aging (> 4h) = amber, stale (> 24h) = red pulse. Uses existing `updated_at` timestamps. Applied to kanban cards and detail panel.

| Aspect | Detail |
|--------|--------|
| Priority | Must Have |
| Sources | UX (#5), Systems (#4) |
| API | None — pure frontend using existing `updated_at` data |
| Complexity | Low |
| Rationale | In agent-driven work, stale WIP means the agent is stuck, looping, or crashed. Without age indicators, healthy progress and stalled work look identical. The key supervisor insight: "which agent might be stuck?" |

---

**R5. Reprioritize & Reassign**

Allow changing priority (P0-P4 selector) and assignee (text input) from cards or detail panel. These are the most common supervisor interventions alongside status changes.

| Aspect | Detail |
|--------|--------|
| Priority | Must Have |
| Sources | UX (#3), API (#1 — same PATCH endpoint) |
| API | `PATCH /api/issue/{id}` (same as R1) |
| Backend | `db.update_issue()` |
| Complexity | Low |
| Rationale | When an agent is stuck or priorities shift, the supervisor needs immediate intervention without CLI round-trip. |

---

**R6. Add Comment from Dashboard**

Text input in the detail panel to post comments. Comments are the primary communication channel between supervisor notes and agent logs.

| Aspect | Detail |
|--------|--------|
| Priority | Must Have |
| Sources | UX (#7), API (#3) |
| API | `POST /api/issue/{id}/comments` |
| Backend | `db.add_comment()` |
| Complexity | Low |
| Rationale | Quick win. Read support already exists; adding write is trivial. Supervisors annotate issues constantly. |

---

**R7. Server-Side Full-Text Search**

Replace client-side `indexOf()` with backend FTS5. Debounced input calls `/api/search`, returns ranked results across title, description, labels, and assignee. Show result count.

| Aspect | Detail |
|--------|--------|
| Priority | Must Have |
| Sources | UX (#5 mention), API (#2) |
| API | `GET /api/search?q=...&limit=50` |
| Backend | `db.search_issues()` — FTS5 with LIKE fallback, already implemented |
| Complexity | Low |
| Rationale | Current search is title-only substring matching on pre-loaded data. FTS5 supports prefix matching, ranking, and searches descriptions. Immediate UX improvement. |

---

**R8. Auto-Refresh with Change Highlighting**

Replace visibility-change-only refresh with configurable polling (10-30s) or SSE. Show "last updated: Xs ago" indicator. Briefly animate cards that changed since last refresh.

| Aspect | Detail |
|--------|--------|
| Priority | Must Have |
| Sources | UX (#6), Systems (#9), API (#13) |
| API | Polling: reuse existing endpoints. SSE: `GET /api/events/stream` |
| Backend | `get_events_since()` for incremental polling |
| Complexity | Low (polling) / High (SSE) |
| Rationale | Supervisors keep the dashboard open while agents work. Stale data is actively misleading. Even 30s polling dramatically improves situational awareness. Start with polling; SSE is a future optimization. |

---

**R9. Keyboard Navigation**

Full keyboard navigation: Tab through cards with visible focus rings, `j`/`k` for next/prev issue, Enter to open detail, `g+k`/`g+g` for view switching, `s` for status, `c` for comment, `p` for priority cycle. Cards need `tabindex="0"`.

| Aspect | Detail |
|--------|--------|
| Priority | Must Have |
| Sources | UX (#4), API (#15) |
| API | None — pure frontend, uses write endpoints from R1/R5/R6 |
| Complexity | Low |
| Rationale | Developer audience expects keyboard-first interaction. Current state (only `/` and `Escape`) is far below expectations for a dev tool. |

---

### Phase 2: Should Have

Significant improvements for power users and advanced workflows.

---

**R10. Critical Path Overlay**

Highlight the longest dependency chain on the graph view — thicker edges, pulsing nodes. Show as a linear chain in a sidebar. Issues on the critical path get distinctive treatment.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | Systems (#3), API (#8) |
| API | `GET /api/critical-path` |
| Backend | `db.get_critical_path()` — already implemented |
| Complexity | Medium |
| Rationale | The critical path determines minimum project duration. Currently the graph shows all edges equally. Highlighting "if this slips, the whole project slips" is the single most important structural insight. |

---

**R11. Cascade Impact Preview**

On hover/select of a blocked issue, highlight all transitively downstream issues. Show count: "Blocking 12 downstream issues." Dim everything except the affected subgraph.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | Systems (#5) |
| API | None — client-side BFS on `allDeps` |
| Complexity | Medium |
| Rationale | Supervisors see "blocked by 1" but not "this delays 12 others." Making cascade effects visible transforms prioritization from "fix what's loudest" to "fix what has the widest blast radius." |

---

**R12. Bottleneck Impact Score**

Compute per-issue "impact score" = number of transitive downstream dependents. Display as badge on kanban cards. Auto-sort "Ready" issues by impact score descending.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | Systems (#7) |
| API | None — client-side graph computation on `allDeps` |
| Complexity | Low |
| Rationale | Creates a self-correcting feedback loop: high-impact work surfaces automatically. Without this, the supervisor must mentally trace the dependency graph to prioritize. |

---

**R13. Agent Workload Balance View**

Horizontal bar chart showing active WIP count per agent (using `assignee` field). Highlights imbalance and idle agents.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | Systems (#8) |
| API | None — client-side grouping on `allIssues` by `assignee` |
| Complexity | Low |
| Rationale | Without visibility into per-agent load, supervisors can't detect imbalance, idle agents, or overloaded agents. Addresses the "tragedy of the commons" where agents grab easy work and hard work piles up. |

---

**R14. Blocked Issue Spotlight**

Dedicated filtered view or prominent section showing only blocked issues with inline dependency chain visualization. Each blocked card shows what it's waiting on and the blocker's status.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | UX (#9) |
| API | None — filter existing data |
| Complexity | Low |
| Rationale | Blocked work is the #1 source of wasted agent cycles. Making blockers immediately visible and actionable creates a tight feedback loop. |

---

**R15. Plan / Milestone Tree View**

New view showing Milestone --> Phase --> Step hierarchy. Nested accordion or tree with progress bars at each level. Steps clickable to open detail panel. Projected completion from throughput.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | UX (#8), API (#11), Systems (#13) |
| API | `GET /api/plan/{milestone_id}` |
| Backend | `db.get_plan()` — already implemented |
| Complexity | Medium |
| Rationale | Kanban shows task-level state but not goal-level progress. Supervisors need "are we on track for milestone X?" at a glance. |

---

**R16. Detail Panel Navigation Stack**

Back/forward navigation in the detail panel. Clicking dependency links pushes to a stack. Show breadcrumbs like `FIL-001 > FIL-003 > FIL-007`.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | UX (#10) |
| API | None — pure frontend state management |
| Complexity | Low |
| Rationale | Dependency exploration is a core workflow. Currently each click replaces context with no way back. |

---

**R17. Quick Close / Reopen**

Close button (with optional reason) for open/wip issues, Reopen button for done issues. Confirmation dialog.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | API (#4) |
| API | `POST /api/issue/{id}/close`, `POST /api/issue/{id}/reopen` |
| Backend | `db.close_issue()`, `db.reopen_issue()` |
| Complexity | Low |
| Rationale | Natural complement to status transitions. Common workflow action. |

---

**R18. Batch Operations**

Multi-select cards (checkbox or Shift+click) for bulk close, reprioritize, reassign. Floating action bar when items are selected.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | UX (#14), API (#9) |
| API | `POST /api/batch/update`, `POST /api/batch/close` |
| Backend | `db.batch_update()`, `db.batch_close()` |
| Complexity | Medium |
| Rationale | When milestones complete or priorities shift, updating one-by-one is tedious. |

---

**R19. Accessibility: ARIA Roles & Focus Management**

Add `role="region"` to kanban columns, `role="complementary"` to detail panel, `aria-live="polite"` for stats updates, focus trap in detail panel, skip-nav link. Fix contrast on slate-500 text (currently below AA).

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | UX (#12) |
| API | None — pure frontend |
| Complexity | Low |
| Rationale | Accessibility constraints improve the experience for all users, especially keyboard-heavy developers. |

---

**R20. Change Indicators / Notification Badges**

On refresh, highlight cards that changed since last view with subtle pulse animation or "CHANGED" dot. Track diff between current and previous fetch.

| Aspect | Detail |
|--------|--------|
| Priority | Should Have |
| Sources | UX (#11), Systems (#9) |
| API | None — frontend diff of issue snapshots |
| Complexity | Low |
| Rationale | Humans are bad at spotting changes without cues. On refresh, "what's different?" should be instantly visible. |

---

### Phase 3: Nice to Have

Polish, advanced visualizations, and power-user features.

---

**R21. System Health Score**

Composite 0-100 score combining: blocked ratio, WIP age, throughput trend, agent balance, stale issue count. Displayed in header as green/yellow/red indicator. Clickable to show contributing factors.

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | Systems (#14) |
| Complexity | Medium |
| Rationale | Answers the meta-question "does anything need my attention right now?" in one glance. Risk of oversimplification mitigated by drill-down. |

---

**R22. Workflow State Machine Visualization**

Render each type's state machine as a directed graph (Cytoscape.js already loaded). States as nodes colored by category, edges labeled with enforcement level. Overlay issue counts per state.

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | UX (#16), API (#14) |
| API | None — existing `/api/type/{type}` has the data |
| Complexity | Medium |
| Rationale | Helps new users and occasional supervisors understand valid workflows visually. |

---

**R23. Issue Creation Form**

Modal or slide-out form with type dropdown (from `/api/types`), priority selector, description textarea, parent assignment, label picker.

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | UX (#22), API (#6) |
| API | `POST /api/issues`, `GET /api/types` |
| Backend | `db.create_issue()` |
| Complexity | Medium |
| Rationale | Completes full CRUD from dashboard. Ranked lower because creation is less frequent than monitoring and intervention. |

---

**R24. Claim / Release Issue**

Claim button for unassigned ready issues, Release button for claimed issues. "Claim Next" power button in header.

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | API (#7) |
| API | `POST /api/issue/{id}/claim`, `POST /api/issue/{id}/release`, `POST /api/claim-next` |
| Backend | `db.claim_issue()`, `db.release_claim()`, `db.claim_next()` |
| Complexity | Low |
| Rationale | Useful when humans are also workers alongside agents. |

---

**R25. Dependency Management**

Add/remove dependencies from detail panel. "Add blocker" button opens searchable issue picker (reusing FTS5). Remove button on each dependency row.

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | API (#10) |
| API | `POST /api/issue/{id}/dependencies`, `DELETE /api/issue/{id}/dependencies/{dep_id}` |
| Backend | `db.add_dependency()` (with cycle detection), `db.remove_dependency()` |
| Complexity | Medium |
| Rationale | Completes the graph interaction story. |

---

**R26. Saved Filter Presets**

Save and name filter combinations (e.g., "My P0-P1 bugs"). Store in localStorage. Show as quick-switch buttons.

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | UX (#17) |
| Complexity | Low |
| Rationale | Supervisors repeatedly check the same filtered views. |

---

**R27. Throughput Trend Sparkline**

Inline chart in footer showing issues closed per day over last 14 days. Upward = acceleration, downward = deceleration.

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | Systems (#11) |
| Complexity | Low |
| Rationale | A single throughput number doesn't show direction. A trend line does. Maximum information per pixel. |

---

**R28. Responsive / Mobile Layout**

Stack kanban columns vertically on narrow screens. Collapse filter bar. Full-screen detail panel on mobile. Touch-optimized card sizes.

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | UX (#13) |
| Complexity | Medium |
| Rationale | Supervisors may check progress from phones/tablets. |

---

**R29. Stale Issue Alerts**

Persistent notification badge: "3 stale issues need attention." Configurable staleness threshold (e.g., > 2h for WIP).

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | Systems (#12) |
| Complexity | Low |
| Rationale | Self-correcting feedback loop — abandoned work surfaces automatically. |

---

**R30. Dark/Light Theme Toggle**

Theme switcher using Tailwind dark: utilities.

| Aspect | Detail |
|--------|--------|
| Priority | Nice to Have |
| Sources | UX (#19) |
| Complexity | Low |
| Rationale | Personal preference and bright-environment readability. |

---

## Cross-Cutting Concerns

### Security

- **XSS**: Current `onclick` handlers use string interpolation with issue IDs. Replace with `data-id` attributes and `addEventListener`. (Flagged by UX agent)
- **Write endpoints**: No auth model. Acceptable for localhost-bound dashboards. Add `actor` parameter (default `"dashboard"`) for audit trail.

### Error Handling Pattern

All write endpoints return consistent shapes:
```
Success: 200/201 → {...resource data}
Error: 400/404/409 → {error: "message", code: "CYCLE_DETECTED", details: {...}}
```

### Performance

- SQLite WAL mode supports concurrent reads with one writer
- Use `get_events_since()` with cursor for incremental polling, not full reload
- Paginate search results; keep the 10,000-issue full load for kanban/graph

## Summary

| Phase | Count | Theme |
|-------|-------|-------|
| Must Have | 9 | Break read-only barrier, expose existing data, real-time awareness |
| Should Have | 11 | Structural insights, navigation, batch ops, accessibility |
| Nice to Have | 10 | Full CRUD, polish, advanced visualizations |

**Total: 30 requirements** derived from 3 specialist perspectives (UX, systems thinking, API architecture). Every requirement is backed by existing data-layer capabilities — the implementation bottleneck is purely API endpoints and frontend rendering.
