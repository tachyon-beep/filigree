"""Dashboard API tests — graph serialization, frontend contracts, and bounded int validation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from filigree.dashboard import STATIC_DIR
from tests.conftest import PopulatedDB


class TestGraphFrontendContracts:
    def test_issue_detail_fetches_issue_files_contract(self) -> None:
        detail_js = (STATIC_DIR / "js" / "views" / "detail.js").read_text()
        api_js = (STATIC_DIR / "js" / "api.js").read_text()
        assert "fetchIssueFiles" in api_js
        assert "fetchIssueFiles" in detail_js
        assert "Promise.all([" in detail_js

    def test_issue_detail_renders_associated_files_section(self) -> None:
        detail_js = (STATIC_DIR / "js" / "views" / "detail.js").read_text()
        assert "Associated Files" in detail_js
        assert "switchView('files');setTimeout(()=>openFileDetail(" in detail_js
        assert "issueFilesData.length" in detail_js

    def test_project_refresh_falls_back_when_selected_project_is_removed(self) -> None:
        app_js = (STATIC_DIR / "js" / "app.js").read_text()
        assert "const currentMissing =" in app_js
        assert "setProject(fallbackKey);" in app_js
        assert "setProject(fallbackKey, { keepDetail: true });" not in app_js
        assert "Selected project was removed. Switched to an available project." in app_js

    def test_graph_query_builder_includes_v2_filters(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        assert 'mode: "v2"' in graph_js
        assert "graphReadyOnly" in graph_js
        assert "graphBlockedOnly" in graph_js
        assert "graphAssignee" in graph_js
        assert "onGraphAssigneeInput" in graph_js
        assert "window_days" in graph_js
        assert "scope_root" in graph_js
        assert "scope_radius" in graph_js
        assert "node_limit" in graph_js
        assert "edge_limit" in graph_js
        assert "refreshGraphData" in graph_js

    def test_graph_zoom_floor_contract(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        assert "function computeGraphMinZoom(nodeCount)" in graph_js
        assert "function enforceReadableZoomBounds(nodeCount)" in graph_js
        assert "state.cy.minZoom(floor);" in graph_js
        assert "state.cy.maxZoom(GRAPH_MAX_ZOOM);" in graph_js
        assert "minZoom: graphMinZoom" in graph_js
        assert "minZoom: 0.1" not in graph_js
        assert "fitGraphWithCaps()" in graph_js
        assert "GRAPH_FIT_ZOOM_CAP = 1.5" in graph_js

    def test_graph_time_window_preference_contract(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        app_js = (STATIC_DIR / "js" / "app.js").read_text()
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert 'GRAPH_TIME_WINDOW_STORAGE_KEY = "filigree.graph.time_window_days.v1"' in graph_js
        assert "function ensureGraphTimeWindowControl()" in graph_js
        assert "persistGraphTimeWindowDays" in graph_js
        assert "export function onGraphTimeWindowChange()" in graph_js
        assert "window.onGraphTimeWindowChange = onGraphTimeWindowChange;" in app_js
        assert 'id="graphTimeWindow"' in html
        assert 'onchange="onGraphTimeWindowChange()"' in html
        assert 'value="7" selected' in html

    def test_graph_default_change_has_one_time_callout_contract(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        assert "function maybeShowGraphDefaultPresetNotice(preset)" in graph_js
        assert 'GRAPH_DEFAULT_NOTICE_KEY = "filigree.graph.execution_default_notice.v1"' in graph_js
        assert "window.localStorage?.getItem(GRAPH_DEFAULT_NOTICE_KEY)" in graph_js
        assert 'window.localStorage?.setItem(GRAPH_DEFAULT_NOTICE_KEY, "1")' in graph_js
        assert "Graph now defaults to Execution (all issue types)." in graph_js
        assert "maybeShowGraphDefaultPresetNotice(graphPreset);" in graph_js

    def test_files_finding_actions_contract(self) -> None:
        files_js = (STATIC_DIR / "js" / "views" / "files.js").read_text()
        ui_js = (STATIC_DIR / "js" / "ui.js").read_text()
        assert "Create Ticket" in files_js
        assert "Close Finding" in files_js
        assert "function closeFinding()" in files_js
        assert "dataset.findingFileId" in files_js
        assert "dataset.findingId" in files_js
        assert "patchFileFinding(" in files_js
        assert "patchFileFinding(" in ui_js

    def test_graph_legacy_fallback_notice_present(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        assert "Graph v2 unavailable; showing legacy graph." in graph_js
        assert "traceGraphPath" in graph_js
        assert "clearGraphPath" in graph_js

    def test_graph_overlay_hierarchy_contract(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        assert "applyCriticalPathStyles();" in graph_js
        assert "applyPathTraceStyles();" in graph_js
        assert "applySearchFocus(search);" in graph_js

    def test_focus_controls_coupled_and_tap_no_longer_mutates_root(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        app_js = (STATIC_DIR / "js" / "app.js").read_text()
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert "export function onGraphFocusModeChange()" in graph_js
        assert "export function onGraphFocusRootInput()" in graph_js
        assert 'scheduleDebouncedGraphRender("focusRoot")' in graph_js
        assert "window.onGraphAssigneeInput = onGraphAssigneeInput;" in app_js
        assert "window.onGraphTimeWindowChange = onGraphTimeWindowChange;" in app_js
        assert "focusRoot.value = nodeId" not in graph_js
        assert 'onchange="onGraphFocusModeChange()"' in html
        assert 'oninput="onGraphFocusRootInput()"' in html
        assert 'oninput="onGraphAssigneeInput()"' in html
        assert 'onchange="onGraphTimeWindowChange()"' in html

    def test_graph_inputs_use_debounced_render(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        assert "const INPUT_DEBOUNCE_MS = 300;" in graph_js
        assert "function scheduleDebouncedGraphRender(inputType)" in graph_js
        assert "setTimeout(() => {" in graph_js
        assert 'scheduleDebouncedGraphRender("focusRoot")' in graph_js
        assert 'scheduleDebouncedGraphRender("assignee")' in graph_js

    def test_trace_button_disabled_until_both_path_inputs_present(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        app_js = (STATIC_DIR / "js" / "app.js").read_text()
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert "export function onGraphPathInput()" in graph_js
        assert "traceBtn.disabled = !ready;" in graph_js
        assert "window.onGraphPathInput = onGraphPathInput;" in app_js
        assert 'id="graphPathSource"' in html
        assert 'id="graphPathTarget"' in html
        assert 'placeholder="issue ID"' in html
        assert 'id="graphTraceBtn"' in html
        assert "disabled" in html

    def test_preset_and_epics_toggle_stay_in_sync(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        app_js = (STATIC_DIR / "js" / "app.js").read_text()
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert "export function onGraphEpicsOnlyChange()" in graph_js
        assert 'preset.value === "roadmap" && !epicsOnly.checked' in graph_js
        assert 'preset.value = "execution"' in graph_js
        assert "window.onGraphEpicsOnlyChange = onGraphEpicsOnlyChange;" in app_js
        assert 'onchange="onGraphEpicsOnlyChange()"' in html

    def test_graph_toolbar_progressive_disclosure_groups_present(self) -> None:
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert 'id="graphFiltersGroup"' in html
        assert 'id="graphAdvancedGroup"' in html
        assert "Trace path between issues:" in html
        assert "Node cap:" in html
        assert "Edge cap:" in html

    def test_graph_notice_uses_icon_not_color_only(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert 'id="graphNotice"' in html
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html
        assert 'icon.textContent = "⚠ "' in graph_js
        assert 'icon.setAttribute("aria-hidden", "true")' in graph_js

    def test_graph_toolbar_touch_target_contract(self) -> None:
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert ".graph-toolbar label { min-height: 44px;" in html
        assert '.graph-toolbar button, .graph-toolbar select, .graph-toolbar input[type="text"], .graph-toolbar summary {' in html
        assert ".graph-toolbar summary { display: inline-flex; align-items: center; line-height: 1; }" in html
        assert ".graph-toolbar .graph-disclosure[open] { flex: 1 0 100%; }" in html
        assert ".graph-toolbar .graph-disclosure[open] > .graph-disclosure-panel { width: 100%; min-width: 0; }" in html
        assert ".graph-toolbar .graph-inline-controls { display: inline-flex;" in html
        assert 'class="graph-toolbar' in html
        assert "items-center leading-none" in html
        assert "graph-disclosure" in html
        assert "graph-disclosure-panel" in html
        assert "graph-inline-controls" in html

    def test_graph_clear_buttons_disable_when_inactive(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert "function setToolbarButtonEnabled(id, enabled)" in graph_js
        assert "function updateGraphClearButtons()" in graph_js
        assert 'setToolbarButtonEnabled("graphClearFocusBtn", focusActive);' in graph_js
        assert 'setToolbarButtonEnabled("graphClearPathBtn", state.graphPathNodes.size > 0);' in graph_js
        assert 'if (document.getElementById("graphClearFocusBtn")?.disabled) return;' in graph_js
        assert 'if (document.getElementById("graphClearPathBtn")?.disabled) return;' in graph_js
        assert graph_js.count("updateGraphClearButtons();") >= 6
        assert 'id="graphClearFocusBtn"' in html
        assert 'id="graphClearPathBtn"' in html

    def test_graph_perf_state_is_in_bottom_diagnostics_bar(self) -> None:
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert 'id="graphDiagnosticsBar"' in html
        diagnostics_idx = html.index('id="graphDiagnosticsBar"')
        perf_idx = html.index('id="graphPerfState"')
        cy_idx = html.index('id="cy"')
        assert cy_idx < diagnostics_idx
        assert diagnostics_idx < perf_idx

    def test_graph_caps_are_within_advanced_disclosure_group(self) -> None:
        html = (STATIC_DIR / "dashboard.html").read_text()
        start = html.index('id="graphAdvancedGroup"')
        end = html.index("</details>", start)
        advanced_block = html[start:end]
        assert 'id="graphNodeLimit"' in advanced_block
        assert 'id="graphEdgeLimit"' in advanced_block

    def test_hover_traversal_uses_outgoers_not_full_edge_scan(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        start = graph_js.index('state.cy.on("mouseover", "node"')
        end = graph_js.index('state.cy.on("mouseout", "node"', start)
        hover_block = graph_js[start:end]
        assert 'curNode.outgoers("edge")' in hover_block
        assert "state.cy.edges().forEach" not in hover_block

    def test_path_tracing_uses_outgoers_not_full_edge_scan(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        start = graph_js.index("export function traceGraphPath()")
        end = graph_js.index("export async function refreshGraphData", start)
        path_block = graph_js[start:end]
        assert 'curNode.outgoers("edge")' in path_block
        assert 'curNode.incomers("edge")' in path_block
        assert 'direction === "upstream"' in path_block
        assert "state.cy.edges().forEach" not in path_block

    def test_topology_change_reuses_positions_only_when_all_nodes_have_positions(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        assert "const canReusePositions =" in graph_js
        assert "cyNodes.every((n) => Object.prototype.hasOwnProperty.call(previousPositions, n.data.id))" in graph_js
        assert "positions: (node) => previousPositions[node.id()]," in graph_js
        assert "previousPositions[node.id()] || { x: 0, y: 0 }" not in graph_js

    def test_search_nav_buttons_have_disabled_state_logic(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert "function setGraphSearchButtonsEnabled(enabled)" in graph_js
        assert '["graphSearchPrevBtn", "graphSearchNextBtn"]' in graph_js
        assert "btn.disabled = !enabled;" in graph_js
        assert 'id="graphSearchPrevBtn"' in html
        assert 'id="graphSearchNextBtn"' in html
        assert 'aria-label="Previous search match"' in html
        assert 'aria-label="Next search match"' in html
        assert 'aria-label="Filter graph by assignee"' in html
        assert 'aria-label="Focus root issue ID"' in html
        assert 'aria-label="Path source issue ID"' in html
        assert 'aria-label="Path target issue ID"' in html

    def test_graph_search_idle_state_uses_plain_language(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        html = (STATIC_DIR / "dashboard.html").read_text()
        assert "No active search." in graph_js
        assert "Search: n/a" not in graph_js
        assert "No active search." in html

    def test_graph_perf_state_user_facing_text_and_tooltip_timings(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        # Node/edge count moved to dedicated diagnostics element
        assert 'document.getElementById("graphNodeEdgeCount")' in graph_js
        assert "countEl.textContent = `${nodeCount} nodes, ${edgeCount} edges`;" in graph_js
        # Timing now visible text, not tooltip
        assert "el.textContent = `Query ${queryMs}ms | Render ${renderMs}ms`;" in graph_js
        assert "Perf q:" not in graph_js

    def test_v2_empty_status_categories_guard_returns_empty_graph(self) -> None:
        graph_js = (STATIC_DIR / "js" / "views" / "graph.js").read_text()
        assert "query.status_categories.length === 0" in graph_js
        assert "No status categories selected. Enable at least one status filter." in graph_js
        assert "status_categories: []" in graph_js


class TestSafeBoundedInt:
    """Bug filigree-2c3119: _safe_bounded_int must pass through _safe_int's error response."""

    def test_non_integer_returns_safe_int_error_code(self) -> None:
        """When _safe_int fails, its error response (VALIDATION_ERROR) must propagate, not be replaced."""
        import json

        from starlette.responses import JSONResponse

        from filigree.dashboard import _safe_bounded_int

        result = _safe_bounded_int("abc", name="window_days", min_value=1, max_value=365)
        assert isinstance(result, JSONResponse)

        assert isinstance(result.body, bytes)
        body = json.loads(result.body.decode())
        # Must use _safe_int's VALIDATION_ERROR, not the replaced GRAPH_INVALID_PARAM
        assert body["error"]["code"] == "VALIDATION_ERROR"


class TestGraphAdvancedAPI:
    async def test_graph_combined_filters(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/graph?mode=v2&include_done=false&status_categories=open,wip&types=task&ready_only=false&blocked_only=false",
        )
        assert resp.status_code == 200
        data = resp.json()
        for node in data["nodes"]:
            assert node["status_category"] in {"open", "wip"}
            assert node["type"] == "task"

    async def test_graph_v2_and_legacy_edge_direction_consistency(
        self,
        client: AsyncClient,
        dashboard_db: PopulatedDB,
    ) -> None:
        ids = dashboard_db.ids
        legacy = (await client.get("/api/graph?mode=legacy")).json()
        v2 = (await client.get("/api/graph?mode=v2")).json()
        expected_source = ids["b"]  # blocker
        expected_target = ids["a"]  # blocked
        assert any(e["source"] == expected_source and e["target"] == expected_target for e in legacy["edges"])
        assert any(e["source"] == expected_source and e["target"] == expected_target for e in v2["edges"])

    async def test_graph_critical_path_only_subset(self, client: AsyncClient) -> None:
        full = await client.get("/api/graph?mode=v2")
        crit = await client.get("/api/graph?mode=v2&critical_path_only=true")
        assert full.status_code == 200
        assert crit.status_code == 200
        full_nodes = full.json()["nodes"]
        crit_nodes = crit.json()["nodes"]
        assert len(crit_nodes) <= len(full_nodes)
        assert all(n["id"] for n in crit_nodes)

    async def test_graph_truncation_semantics_metadata(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        for i in range(80):
            dashboard_db.db.create_issue(title=f"Graph cap issue {i}", type="task", priority=2)
        resp = await client.get("/api/graph?mode=v2&node_limit=50")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limits"]["truncated"] is True
        assert data["telemetry"]["total_nodes_before_limit"] >= len(data["nodes"])
        assert data["telemetry"]["total_nodes_before_limit"] > 50

    async def test_graph_blocked_only_returns_only_currently_blocked_nodes(
        self,
        client: AsyncClient,
        dashboard_db: PopulatedDB,
    ) -> None:
        ids = dashboard_db.ids
        resp = await client.get("/api/graph?mode=v2&blocked_only=true")
        assert resp.status_code == 200
        data = resp.json()
        node_ids = {n["id"] for n in data["nodes"]}
        assert ids["a"] in node_ids
        assert all(n["blocked_by_open_count"] > 0 for n in data["nodes"])
        assert data["query"]["blocked_only"] is True

    async def test_graph_assignee_filter_behavior(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        ids = dashboard_db.ids
        dashboard_db.db.update_issue(ids["a"], assignee="alice")
        resp = await client.get("/api/graph?mode=v2&assignee=alice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"]["assignee"] == "alice"
        assert len(data["nodes"]) >= 1
        assert all(n["assignee"] == "alice" for n in data["nodes"])
        assert ids["a"] in {n["id"] for n in data["nodes"]}

    async def test_graph_scope_radius_behavior(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        ids = dashboard_db.ids
        root = ids["a"]
        near = ids["b"]

        r0 = await client.get(f"/api/graph?mode=v2&scope_root={root}&scope_radius=0")
        r1 = await client.get(f"/api/graph?mode=v2&scope_root={root}&scope_radius=1")
        assert r0.status_code == 200
        assert r1.status_code == 200

        r0_data = r0.json()
        r1_data = r1.json()
        r0_nodes = {n["id"] for n in r0_data["nodes"]}
        r1_nodes = {n["id"] for n in r1_data["nodes"]}
        assert r0_nodes == {root}
        assert root in r1_nodes
        assert near in r1_nodes
        assert len(r1_nodes) >= len(r0_nodes)

    async def test_graph_edge_limit_trimming_behavior(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        target = dashboard_db.db.create_issue("Edge fan-in target", type="task", priority=2)
        blockers: list[str] = []
        for i in range(60):
            blocker = dashboard_db.db.create_issue(f"Edge blocker {i}", type="task", priority=2)
            blockers.append(blocker.id)
            dashboard_db.db.add_dependency(target.id, blocker.id)

        resp = await client.get("/api/graph?mode=v2&edge_limit=50")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limits"]["edge_limit"] == 50
        assert data["limits"]["truncated"] is True
        assert len(data["edges"]) == 50
        assert data["telemetry"]["total_edges_before_limit"] > 50

    async def test_graph_window_days_filters_stale_nodes(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        stale = dashboard_db.db.create_issue("Old graph issue", type="task", priority=2)
        fresh = dashboard_db.db.create_issue("Fresh graph issue", type="task", priority=2)
        dashboard_db.db.conn.execute(
            "UPDATE issues SET updated_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", stale.id),
        )
        dashboard_db.db.conn.commit()

        resp = await client.get("/api/graph?mode=v2&types=task&window_days=7")
        assert resp.status_code == 200
        data = resp.json()
        node_ids = {n["id"] for n in data["nodes"]}
        assert stale.id not in node_ids
        assert fresh.id in node_ids
        assert data["query"]["window_days"] == 7


class TestGraphAPI:
    async def test_graph_structure(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)

    async def test_graph_nodes_have_required_fields(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph")
        data = resp.json()
        for node in data["nodes"]:
            assert "id" in node
            assert "title" in node
            assert "status" in node
            assert "priority" in node
            assert "type" in node

    async def test_graph_edges_from_dependencies(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph")
        data = resp.json()
        # populated_db has A depends on B, so there should be an edge
        assert len(data["edges"]) >= 1

    async def test_graph_v2_mode_shape(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "v2"
        assert "query" in data
        assert "limits" in data
        assert "telemetry" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)
        if data["nodes"]:
            node = data["nodes"][0]
            assert "blocked_by_open_count" in node
            assert "blocks_open_count" in node
            assert "is_ready" in node

    async def test_graph_invalid_mode(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=nope")
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "GRAPH_INVALID_PARAM"

    async def test_graph_invalid_ready_blocked_combo(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2&ready_only=true&blocked_only=true")
        assert resp.status_code == 422
        err = resp.json()["error"]
        assert err["code"] == "GRAPH_INVALID_PARAM"

    async def test_graph_scope_root_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2&scope_root=missing")
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert err["code"] == "GRAPH_INVALID_PARAM"

    async def test_graph_include_done_false_excludes_done_nodes(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2&include_done=false")
        assert resp.status_code == 200
        data = resp.json()
        assert all(node["status_category"] != "done" for node in data["nodes"])

    async def test_graph_mode_defaults_to_v2_when_enabled(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FILIGREE_GRAPH_V2_ENABLED", "1")
        resp = await client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "v2"

    async def test_graph_mode_legacy_shape_when_requested(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=legacy")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert "mode" not in data

    async def test_graph_invalid_boolean_param(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2&include_done=maybe")
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "VALIDATION_ERROR"
        assert err["details"]["param"] == "include_done"

    async def test_graph_invalid_status_category(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2&status_categories=open,wat")
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "GRAPH_INVALID_PARAM"
        assert err["details"]["param"] == "status_categories"

    async def test_graph_invalid_type_filter(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2&types=task,notatype")
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "GRAPH_INVALID_PARAM"
        assert err["details"]["param"] == "types"

    async def test_graph_scope_radius_requires_scope_root(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2&scope_radius=2")
        assert resp.status_code == 422
        err = resp.json()["error"]
        assert err["code"] == "GRAPH_INVALID_PARAM"
        assert err["details"]["param"] == "scope_radius"

    async def test_graph_limit_validation(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2&node_limit=10")
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "VALIDATION_ERROR"
        assert err["details"]["param"] == "node_limit"

    async def test_graph_window_days_validation(self, client: AsyncClient) -> None:
        resp = await client.get("/api/graph?mode=v2&window_days=-1")
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "VALIDATION_ERROR"
        assert err["details"]["param"] == "window_days"

    async def test_graph_window_days_zero_is_noop(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        """window_days=0 should not filter any nodes — it's a no-op."""
        stale = dashboard_db.db.create_issue("Very old issue", type="task")
        dashboard_db.db.conn.execute(
            "UPDATE issues SET updated_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", stale.id),
        )
        dashboard_db.db.conn.commit()

        resp = await client.get("/api/graph?mode=v2&window_days=0")
        assert resp.status_code == 200
        data = resp.json()
        node_ids = {n["id"] for n in data["nodes"]}
        assert stale.id in node_ids
        assert data["query"]["window_days"] == 0

    async def test_graph_mode_query_override_beats_compat_mode(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FILIGREE_GRAPH_API_MODE", "legacy")
        resp = await client.get("/api/graph?mode=v2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "v2"

    async def test_graph_compat_mode_env_legacy_default(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FILIGREE_GRAPH_V2_ENABLED", "1")
        monkeypatch.setenv("FILIGREE_GRAPH_API_MODE", "legacy")
        resp = await client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" not in data

    async def test_graph_v2_node_limit_truncation(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        for i in range(60):
            dashboard_db.db.create_issue(title=f"Graph load issue {i}", type="task", priority=2)
        resp = await client.get("/api/graph?mode=v2&node_limit=50")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limits"]["truncated"] is True
        assert len(data["nodes"]) == 50
        assert "query_ms" in data["telemetry"]
