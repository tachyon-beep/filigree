# Graph Tab v2: Interaction + API Contract

Epic: `filigree-7cc838`

This document is the implementation source-of-truth for:
- `filigree-45ef28` (interaction model + acceptance criteria)
- `filigree-02cbde` (v2 API contract + query model + error matrix)
- `filigree-9ea22f` (compatibility/deprecation strategy)
- `filigree-a54d0c` (performance budgets + telemetry definition)

## 1) Interaction Model

### Primary user journeys
1. Investigate blocker chain: pick a blocked issue, trace upstream blockers, inspect path details, open issue detail without leaving Graph tab.
2. Find executable work: show ready-only set, keep blocking context visible, quickly pivot to assignee/status slices.
3. Inspect bottlenecks: overlay critical path and blocked-only views, then drill into local neighborhoods.

### Graph modes
1. `execution` mode (default): issue-level graph with all issue types, open+wip+done category toggles respected.
2. `roadmap` mode: epics/milestones emphasis for high-level planning.
3. `focus` mode: center on selected issue with configurable neighborhood radius.
4. `path` mode: source/target path trace with no-path empty state.

### Interaction contracts
1. Node click:
   - Select node.
   - Open issue detail panel.
   - In focus mode, recenters graph and applies neighborhood query.
2. Filter change:
   - Re-query through `/api/graph` (v2 mode) when Graph v2 is enabled.
   - Legacy mode keeps current client-side filtering behavior.
3. Critical path toggle:
   - Overlay style layer only (does not mutate query defaults).
   - Can coexist with filters/focus; reset returns previous visual state.
4. Layout behavior:
   - Incremental updates preserve node positions where possible.
   - Full relayout only on explicit user request (`Fit`) or incompatible topology change.

### Input surface
1. Preset selector (`execution`, `roadmap`).
2. Status category toggles (`open`, `wip`, `done`).
3. Type/status/assignee controls.
4. `ready_only` and `blocked_only`.
5. Focus root + radius.
6. Path source + target.

### Acceptance criteria
1. Users can complete blocker-chain, ready-work, and bottleneck workflows without leaving Graph tab.
2. Interaction states are deterministic:
   - same controls => same query => same render state.
3. Mode changes and overlays are reversible with a single reset action.
4. Legacy mode remains available via compatibility switch + feature flag.

## 2) `/api/graph` v2 Contract

### Endpoint
- `GET /api/graph`

### Selection semantics
- `mode=legacy|v2`
  - `legacy`: current payload semantics.
  - `v2`: graph-native contract below.
  - missing `mode`: resolved by runtime compatibility switch and feature flag defaults.

### Query params (v2)
1. `scope_root`: issue id for neighborhood/focus scope.
2. `scope_radius`: integer `0..6`, default `2` when `scope_root` provided.
3. `include_done`: boolean (`true|false`, default `true`).
4. `types`: comma-separated issue types.
5. `status_categories`: comma-separated from `{open,wip,done}`.
6. `assignee`: exact assignee match.
7. `blocked_only`: boolean.
8. `ready_only`: boolean.
9. `critical_path_only`: boolean.
10. `node_limit`: integer (`50..2000`), default `600`.
11. `edge_limit`: integer (`50..5000`), default `2000`.

### Response shape (v2)
```json
{
  "mode": "v2",
  "compatibility_mode": "legacy|v2",
  "query": {
    "scope_root": "filigree-xxxxxx",
    "scope_radius": 2,
    "include_done": false
  },
  "limits": {
    "node_limit": 600,
    "edge_limit": 2000,
    "truncated": false
  },
  "telemetry": {
    "query_ms": 14,
    "total_nodes_before_limit": 188,
    "total_edges_before_limit": 240
  },
  "nodes": [
    {
      "id": "filigree-xxxxxx",
      "title": "Issue title",
      "status": "open",
      "status_category": "open",
      "priority": 1,
      "type": "task",
      "assignee": "",
      "is_ready": true,
      "blocked_by_open_count": 1,
      "blocks_open_count": 3
    }
  ],
  "edges": [
    {
      "id": "dep-1",
      "source": "blocker-id",
      "target": "blocked-id",
      "kind": "blocks",
      "is_critical_path": false
    }
  ]
}
```

### Edge semantics
- Direction is always `blocker -> blocked`.
- Equivalent to dependency record `issue_id depends_on_id` represented as:
  - `source = depends_on_id` (blocker)
  - `target = issue_id` (blocked)

## 3) Invalid-Parameter Error Matrix

All validation errors return:
```json
{
  "error": {
    "code": "GRAPH_INVALID_PARAM",
    "message": "Human-readable detail",
    "details": { "param": "scope_radius", "value": "abc" }
  }
}
```

Status codes:
1. `400` malformed value: invalid boolean/int/list format.
2. `404` unknown `scope_root` id.
3. `422` semantically incompatible param combination.

Cases:
1. `scope_radius` non-int or outside range -> `400`.
2. invalid boolean (`ready_only=maybe`) -> `400`.
3. unknown status category (`status_categories=foo`) -> `400`.
4. unknown type in `types` -> `400`.
5. `scope_root` missing issue -> `404`.
6. `ready_only=true` and `blocked_only=true` -> `422`.
7. `mode` not in `{legacy,v2}` -> `400`.

## 4) Compatibility + Deprecation Strategy

### Runtime controls
1. Feature flag: `FILIGREE_GRAPH_V2_ENABLED` (default `0`).
2. Compatibility switch: `FILIGREE_GRAPH_API_MODE` (`legacy` default, `v2` optional).
3. Request override: `mode=` query param.

Resolution order:
1. explicit request `mode`.
2. compatibility switch.
3. feature-flag default (`legacy` when disabled).

### Guarantees
1. Legacy shape remains available for existing consumers.
2. Existing frontend behavior preserved when flag is disabled.
3. v2 can roll out incrementally by environment config.

### Deprecation checkpoints
1. Phase 1: both legacy and v2 supported.
2. Phase 2: v2 default, legacy opt-in.
3. Phase 3: legacy removal only after explicit release decision and migration notice.

## 5) Performance Budgets + Telemetry

### Budgets
1. Backend query: p50 <= 30ms, p95 <= 120ms for medium projects.
2. Graph payload size: default <= 600 nodes / 2000 edges.
3. Frontend render/update: p50 <= 120ms; incremental update target <= 80ms.

### Telemetry points
1. Backend:
   - query duration (`query_ms`)
   - total candidate nodes/edges before truncation
   - truncation flag
2. Frontend:
   - render duration
   - update duration
   - fallback reason (`node_limit`, `edge_limit`, `empty_scope`)

### Large-graph fallback requirements
1. Always return `limits.truncated=true` when capping.
2. UI must display a visible fallback notice with next actions:
   - narrow by scope root/radius
   - enable preset filtering
   - reduce include_done.

