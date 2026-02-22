// ---------------------------------------------------------------------------
// Graph view — Cytoscape dependency graph, critical path, health scoring.
// ---------------------------------------------------------------------------

import { fetchCriticalPath, fetchGraph } from "../api.js";
import { CATEGORY_COLORS, state, THEME_COLORS } from "../state.js";
import { showPopover, showToast } from "../ui.js";

// --- Callbacks for functions not yet available at import time ---

export const callbacks = { openDetail: null, fetchData: null };

let _graphFetchSeq = 0;
const INPUT_DEBOUNCE_MS = 300;
const FOCUS_ROOT_NOTICE = "Focus is enabled. Enter a root issue ID to apply scoped view.";
const EMPTY_STATUS_NOTICE = "No status categories selected. Enable at least one status filter.";
const GRAPH_DEFAULT_NOTICE_KEY = "filigree.graph.execution_default_notice.v1";
let _focusInputDebounceId = null;
let _assigneeInputDebounceId = null;
let _graphDefaultPresetNoticeShown = false;

function shouldUseGraphV2() {
  const cfg = state.graphConfig || {};
  return cfg.graph_v2_enabled || cfg.graph_api_mode === "v2";
}

function buildGraphQuery() {
  const preset = document.getElementById("graphPreset")?.value || "execution";
  const epicsOnly = document.getElementById("graphEpicsOnly").checked;
  const readyOnly = document.getElementById("graphReadyOnly").checked;
  const blockedOnlyControl = document.getElementById("graphBlockedOnly").checked;
  const assignee = document.getElementById("graphAssignee").value.trim();
  const focusMode = document.getElementById("graphFocusMode").checked;
  const focusRoot = document.getElementById("graphFocusRoot").value.trim();
  const focusRadiusRaw = document.getElementById("graphFocusRadius").value;
  const focusRadius = Number.parseInt(focusRadiusRaw || "2", 10);
  const nodeLimitRaw = document.getElementById("graphNodeLimit").value;
  const edgeLimitRaw = document.getElementById("graphEdgeLimit").value;
  const nodeLimit = Number.parseInt(nodeLimitRaw || "600", 10);
  const edgeLimit = Number.parseInt(edgeLimitRaw || "2000", 10);
  const showOpen = document.getElementById("filterOpen").checked;
  const showActive = document.getElementById("filterInProgress").checked;
  const showClosed = document.getElementById("filterClosed").checked;

  const statusCategories = [];
  if (showOpen) statusCategories.push("open");
  if (showActive) statusCategories.push("wip");
  if (showClosed) statusCategories.push("done");

  const query = {
    mode: "v2",
    status_categories: statusCategories,
    include_done: showClosed,
    ready_only: readyOnly,
    blocked_only: blockedOnlyControl || state.blockedFilter,
    node_limit: Number.isNaN(nodeLimit) ? 600 : nodeLimit,
    edge_limit: Number.isNaN(edgeLimit) ? 2000 : edgeLimit,
  };
  if (preset === "roadmap" || epicsOnly) query.types = ["epic", "milestone"];
  if (assignee) query.assignee = assignee;
  if (focusMode && focusRoot) {
    query.scope_root = focusRoot;
    query.scope_radius = Number.isNaN(focusRadius) ? 2 : Math.max(0, Math.min(6, focusRadius));
  }
  return query;
}

function setGraphNotice(text) {
  const el = document.getElementById("graphNotice");
  if (!el) return;
  if (text) {
    el.textContent = "";
    const icon = document.createElement("span");
    icon.textContent = "⚠ ";
    icon.setAttribute("aria-hidden", "true");
    const message = document.createElement("span");
    message.textContent = text;
    el.append(icon, message);
    el.classList.remove("hidden");
    return;
  }
  el.textContent = "";
  el.classList.add("hidden");
}

function scheduleDebouncedGraphRender(inputType) {
  if (inputType === "focusRoot") {
    if (_focusInputDebounceId) clearTimeout(_focusInputDebounceId);
    _focusInputDebounceId = setTimeout(() => {
      _focusInputDebounceId = null;
      renderGraph();
    }, INPUT_DEBOUNCE_MS);
    return;
  }
  if (_assigneeInputDebounceId) clearTimeout(_assigneeInputDebounceId);
  _assigneeInputDebounceId = setTimeout(() => {
    _assigneeInputDebounceId = null;
    renderGraph();
  }, INPUT_DEBOUNCE_MS);
}

function updateGraphFilterStateLabel(parts) {
  const el = document.getElementById("graphFilterState");
  if (!el) return;
  if (!parts.length) {
    el.textContent = "Filters: none";
    return;
  }
  el.textContent = `Filters: ${parts.join(" | ")}`;
}

function updateGraphSearchState(text) {
  const el = document.getElementById("graphSearchState");
  if (el) el.textContent = text;
}

function setGraphSearchButtonsEnabled(enabled) {
  ["graphSearchPrevBtn", "graphSearchNextBtn"].forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = !enabled;
    btn.classList.toggle("opacity-50", !enabled);
    btn.classList.toggle("cursor-not-allowed", !enabled);
  });
}

function setToolbarButtonEnabled(id, enabled) {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.disabled = !enabled;
  btn.classList.toggle("opacity-50", !enabled);
  btn.classList.toggle("cursor-not-allowed", !enabled);
}

function updateGraphClearButtons() {
  const focusMode = document.getElementById("graphFocusMode");
  const focusRoot = document.getElementById("graphFocusRoot");
  const focusActive = Boolean(focusMode?.checked || focusRoot?.value.trim());
  setToolbarButtonEnabled("graphClearFocusBtn", focusActive);
  setToolbarButtonEnabled("graphClearPathBtn", state.graphPathNodes.size > 0);
}

function updateGraphPerfState() {
  const el = document.getElementById("graphPerfState");
  if (!el) return;
  const t = state.graphTelemetry || {};
  const queryMs = t.query_ms ?? "-";
  const renderMs = t.render_ms ?? "-";
  const nodeCount = state.cy ? state.cy.nodes().length : 0;
  const edgeCount = state.cy ? state.cy.edges().length : 0;
  el.textContent = `${nodeCount} nodes, ${edgeCount} edges`;
  if (queryMs === "-" && renderMs === "-") {
    el.title = "";
    return;
  }
  el.title = `Query ${queryMs}ms | Render ${renderMs}ms`;
}

function maybeShowGraphDefaultPresetNotice(preset) {
  if (preset !== "execution" || _graphDefaultPresetNoticeShown) return;
  let seen = false;
  try {
    seen = window.localStorage?.getItem(GRAPH_DEFAULT_NOTICE_KEY) === "1";
  } catch (_e) {
    seen = false;
  }
  if (seen) {
    _graphDefaultPresetNoticeShown = true;
    return;
  }
  showToast("Graph now defaults to Execution (all issue types). Use Roadmap or Epics only for epic-focused views.");
  _graphDefaultPresetNoticeShown = true;
  try {
    window.localStorage?.setItem(GRAPH_DEFAULT_NOTICE_KEY, "1");
  } catch (_e) {
    // noop: localStorage might be unavailable in hardened contexts.
  }
}

function applySearchFocus(search) {
  if (!state.cy || state.criticalPathActive || state.graphPathNodes.size) {
    setGraphSearchButtonsEnabled(false);
    if (!search) updateGraphSearchState("Search: n/a");
    return;
  }

  if (!search) {
    state.graphSearchQuery = "";
    state.graphSearchIndex = 0;
    state.cy.nodes().forEach((n) => {
      n.style("opacity", n.data("opacity"));
      n.style("border-width", n.data("isReady") ? 3 : 0);
      n.style("border-color", "#10B981");
    });
    setGraphSearchButtonsEnabled(false);
    updateGraphSearchState("Search: n/a");
    return;
  }

  if (state.graphSearchQuery !== search) {
    state.graphSearchQuery = search;
    state.graphSearchIndex = 0;
  }

  const matches = state.cy
    .nodes()
    .filter((n) => n.data("opacity") >= 1)
    .toArray();
  if (!matches.length) {
    setGraphSearchButtonsEnabled(false);
    updateGraphSearchState("Search: 0 results");
    return;
  }
  setGraphSearchButtonsEnabled(matches.length > 1);

  state.graphSearchIndex =
    ((state.graphSearchIndex % matches.length) + matches.length) % matches.length;
  const active = matches[state.graphSearchIndex];
  const neighborhood = active.closedNeighborhood().nodes();
  const contextIds = new Set(neighborhood.map((n) => n.id()));

  state.cy.nodes().forEach((n) => {
    if (n.id() === active.id()) {
      n.style("opacity", 1);
      n.style("border-width", 4);
      n.style("border-color", THEME_COLORS.accent);
      return;
    }
    if (contextIds.has(n.id())) {
      n.style("opacity", 0.45);
    } else {
      n.style("opacity", 0.1);
    }
  });

  state.cy.center(active);
  updateGraphSearchState(`Search: ${state.graphSearchIndex + 1}/${matches.length}`);
}

export function setGraphPreset(value) {
  const preset = value || "execution";
  const epicsOnly = document.getElementById("graphEpicsOnly");
  if (epicsOnly) epicsOnly.checked = preset === "roadmap";
  refreshGraphData(true).then(() => {
    if (state.currentView === "graph") renderGraph();
  });
}

export function onGraphEpicsOnlyChange() {
  const preset = document.getElementById("graphPreset");
  const epicsOnly = document.getElementById("graphEpicsOnly");
  if (preset && epicsOnly && preset.value === "roadmap" && !epicsOnly.checked) {
    preset.value = "execution";
  }
  refreshGraphData(true).then(() => {
    if (state.currentView === "graph") renderGraph();
  });
}

export function clearGraphFocus() {
  if (document.getElementById("graphClearFocusBtn")?.disabled) return;
  const mode = document.getElementById("graphFocusMode");
  const root = document.getElementById("graphFocusRoot");
  const radius = document.getElementById("graphFocusRadius");
  if (mode) mode.checked = false;
  if (root) root.value = "";
  if (radius) radius.value = "2";
  setGraphNotice(state.graphFallbackNotice || "");
  updateGraphClearButtons();
  refreshGraphData(true).then(() => {
    if (state.currentView === "graph") renderGraph();
  });
}

export function onGraphFocusModeChange() {
  const mode = document.getElementById("graphFocusMode");
  const root = document.getElementById("graphFocusRoot");
  if (!mode || !root) return;
  const rootValue = root.value.trim();
  if (!mode.checked) {
    root.value = "";
    if (!state.graphPathNodes.size) setGraphNotice(state.graphFallbackNotice || "");
  } else if (!rootValue && !state.graphPathNodes.size) {
    setGraphNotice(FOCUS_ROOT_NOTICE);
  }
  updateGraphClearButtons();
  renderGraph();
}

export function onGraphFocusRootInput() {
  const mode = document.getElementById("graphFocusMode");
  const root = document.getElementById("graphFocusRoot");
  if (!mode || !root) return;
  const rootValue = root.value.trim();
  mode.checked = rootValue.length > 0;
  if (!rootValue && !state.graphPathNodes.size) setGraphNotice(state.graphFallbackNotice || "");
  updateGraphClearButtons();
  scheduleDebouncedGraphRender("focusRoot");
}

export function onGraphAssigneeInput() {
  scheduleDebouncedGraphRender("assignee");
}

export function onGraphPathInput() {
  const source = document.getElementById("graphPathSource");
  const target = document.getElementById("graphPathTarget");
  const traceBtn = document.getElementById("graphTraceBtn");
  if (!source || !target || !traceBtn) return;
  const ready = source.value.trim().length > 0 && target.value.trim().length > 0;
  traceBtn.disabled = !ready;
  traceBtn.classList.toggle("opacity-50", !ready);
  traceBtn.classList.toggle("cursor-not-allowed", !ready);
}

export function graphSearchNext() {
  if (document.getElementById("graphSearchNextBtn")?.disabled) return;
  state.graphSearchIndex += 1;
  renderGraph();
}

export function graphSearchPrev() {
  if (document.getElementById("graphSearchPrevBtn")?.disabled) return;
  state.graphSearchIndex -= 1;
  renderGraph();
}

export function clearGraphPath() {
  if (document.getElementById("graphClearPathBtn")?.disabled) return;
  state.graphPathNodes.clear();
  state.graphPathEdges.clear();
  setGraphNotice(state.graphFallbackNotice || "");
  updateGraphClearButtons();
  renderGraph();
}

export function traceGraphPath() {
  if (!state.cy) return;
  const source = document.getElementById("graphPathSource").value.trim();
  const directionEl = document.getElementById("graphPathDirection");
  const direction = directionEl?.value === "downstream" ? "downstream" : "upstream";
  const target = document.getElementById("graphPathTarget").value.trim();
  if (!source || !target) {
    setGraphNotice("Enter both source and target issue ids for path tracing.");
    return;
  }
  if (!state.cy.$id(source).length || !state.cy.$id(target).length) {
    setGraphNotice("Source or target is not visible in current graph scope.");
    return;
  }

  const prevByNode = new Map([[source, null]]);
  const visited = new Set([source]);
  const queue = [state.cy.$id(source)[0]];

  for (let i = 0; i < queue.length; i += 1) {
    const curNode = queue[i];
    const curId = curNode.id();
    if (curId === target) break;
    const neighboringEdges =
      direction === "upstream" ? curNode.incomers("edge") : curNode.outgoers("edge");
    neighboringEdges.forEach((edge) => {
      const nextNode =
        direction === "upstream" ? edge.source() : edge.target();
      const nextId = nextNode.id();
      if (visited.has(nextId)) return;
      visited.add(nextId);
      prevByNode.set(nextId, curId);
      queue.push(nextNode);
    });
  }

  if (!prevByNode.has(target)) {
    state.graphPathNodes.clear();
    state.graphPathEdges.clear();
    setGraphNotice(`No dependency path found from ${source} to ${target}.`);
    updateGraphClearButtons();
    renderGraph();
    return;
  }

  const pathNodes = [];
  let cur = target;
  while (cur !== null) {
    pathNodes.push(cur);
    cur = prevByNode.get(cur) ?? null;
  }
  pathNodes.reverse();

  state.graphPathNodes = new Set(pathNodes);
  state.graphPathEdges = new Set();
  for (let i = 0; i + 1 < pathNodes.length; i += 1) {
    if (direction === "upstream") {
      state.graphPathEdges.add(`${pathNodes[i + 1]}->${pathNodes[i]}`);
    } else {
      state.graphPathEdges.add(`${pathNodes[i]}->${pathNodes[i + 1]}`);
    }
  }

  setGraphNotice(`Path traced (${direction}): ${pathNodes.length} nodes from ${source} to ${target}.`);
  updateGraphClearButtons();
  renderGraph();
}

export async function refreshGraphData(force = false) {
  if (!shouldUseGraphV2()) {
    state.graphMode = "legacy";
    state.graphData = null;
    state.graphQueryKey = "";
    state.graphFallbackNotice = "";
    setGraphNotice("");
    return;
  }

  const query = buildGraphQuery();
  const key = JSON.stringify(query);
  if (!force && state.graphQueryKey === key && state.graphData) return;

  if (Array.isArray(query.status_categories) && query.status_categories.length === 0) {
    state.graphMode = "v2";
    state.graphData = {
      mode: "v2",
      query: {
        scope_root: query.scope_root || null,
        scope_radius: query.scope_root ? query.scope_radius : null,
        include_done: query.include_done,
        types: query.types || [],
        status_categories: [],
        assignee: query.assignee || null,
        blocked_only: query.blocked_only,
        ready_only: query.ready_only,
        critical_path_only: false,
      },
      limits: {
        node_limit: query.node_limit,
        edge_limit: query.edge_limit,
        truncated: false,
      },
      telemetry: {
        query_ms: 0,
        total_nodes_before_limit: 0,
        total_edges_before_limit: 0,
      },
      nodes: [],
      edges: [],
    };
    state.graphQuery = query;
    state.graphQueryKey = key;
    state.graphTelemetry = state.graphData.telemetry;
    state.graphFallbackNotice = EMPTY_STATUS_NOTICE;
    setGraphNotice(state.graphFallbackNotice);
    return;
  }

  const seq = ++_graphFetchSeq;
  const data = await fetchGraph(query);
  if (seq !== _graphFetchSeq) return;

  if (data && data.mode === "v2" && Array.isArray(data.nodes) && Array.isArray(data.edges)) {
    state.graphMode = "v2";
    state.graphData = data;
    state.graphQuery = query;
    state.graphQueryKey = key;
    state.graphTelemetry = data.telemetry || null;
    state.graphFallbackNotice = data.limits?.truncated
      ? `Graph limited for performance (${data.nodes.length} nodes/${data.edges.length} edges shown). Narrow scope or filters for more detail.`
      : "";
    setGraphNotice(state.graphFallbackNotice);
    return;
  }

  // Safety fallback: if v2 fetch fails or returns unexpected payload, use legacy graph.
  state.graphMode = "legacy";
  state.graphData = null;
  state.graphQuery = {};
  state.graphQueryKey = "";
  state.graphFallbackNotice = "Graph v2 unavailable; showing legacy graph.";
  setGraphNotice(state.graphFallbackNotice);
}

function graphStyles() {
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-size": "11px",
        "font-family": "JetBrains Mono, monospace",
        "text-valign": "center",
        "text-halign": "center",
        "text-wrap": "wrap",
        "text-max-width": "120px",
        color: THEME_COLORS.textPrimary,
        "text-outline-color": THEME_COLORS.graphOutline,
        "text-outline-width": 2,
        width: "mapData(priority, 0, 4, 60, 35)",
        height: "mapData(priority, 0, 4, 60, 35)",
        opacity: "data(opacity)",
        "background-color": (ele) => CATEGORY_COLORS[ele.data("statusCategory")] || "#64748B",
        "border-width": (ele) => (ele.data("isReady") ? 3 : 0),
        "border-color": "#10B981",
        shape: (ele) => {
          const t = ele.data("type");
          if (t === "epic" || t === "milestone") return "hexagon";
          if (t === "bug") return "diamond";
          if (t === "feature") return "star";
          return "round-rectangle";
        },
      },
    },
    {
      selector: "edge",
      style: {
        width: 1.5,
        "line-color": THEME_COLORS.graphEdge,
        "target-arrow-color": THEME_COLORS.graphEdge,
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        "arrow-scale": 0.8,
      },
    },
    {
      selector: "node:selected",
      style: { "border-width": 3, "border-color": THEME_COLORS.accent },
    },
  ];
}

function applyCriticalPathStyles() {
  if (!state.cy) return;
  if (state.criticalPathActive && state.criticalPathIds.size) {
    state.cy.nodes().forEach((n) => {
      if (!state.criticalPathIds.has(n.id())) n.style("opacity", 0.2);
    });
    state.cy.edges().forEach((e) => {
      if (
        state.criticalPathIds.has(e.source().id()) &&
        state.criticalPathIds.has(e.target().id())
      ) {
        e.style({
          width: 3,
          "line-color": "#EF4444",
          "target-arrow-color": "#EF4444",
        });
      } else {
        e.style("opacity", 0.1);
      }
    });
  } else {
    state.cy.nodes().forEach((n) => n.style("opacity", n.data("opacity")));
    state.cy.edges().forEach((e) => {
      e.style({
        width: 1.5,
        "line-color": THEME_COLORS.graphEdge,
        "target-arrow-color": THEME_COLORS.graphEdge,
        opacity: 1,
      });
    });
  }
}

function applyPathTraceStyles() {
  if (!state.cy || !state.graphPathNodes.size) return;
  state.cy.nodes().forEach((n) => {
    if (state.graphPathNodes.has(n.id())) {
      n.style("opacity", 1);
      n.style("border-width", 4);
      n.style("border-color", "#F97316");
    } else {
      n.style("opacity", 0.12);
    }
  });
  state.cy.edges().forEach((e) => {
    const key = `${e.source().id()}->${e.target().id()}`;
    if (state.graphPathEdges.has(key)) {
      e.style({
        width: 4,
        "line-color": "#F97316",
        "target-arrow-color": "#F97316",
        opacity: 1,
      });
    } else {
      e.style("opacity", 0.08);
    }
  });
}

function bindGraphEvents() {
  if (!state.cy) return;
  state.cy.on("tap", "node", (evt) => {
    const nodeId = evt.target.id();
    if (callbacks.openDetail) callbacks.openDetail(nodeId);
  });
  state.cy.on("mouseover", "node", (evt) => {
    if (state.criticalPathActive) return;
    const hoverNode = evt.target;
    const nodeId = hoverNode.id();
    const downstream = new Set();
    const visited = new Set([nodeId]);
    const queue = [hoverNode];
    for (let i = 0; i < queue.length; i += 1) {
      const curNode = queue[i];
      curNode.outgoers("edge").forEach((edge) => {
        const nextNode = edge.target();
        const nextId = nextNode.id();
        if (visited.has(nextId)) return;
        visited.add(nextId);
        downstream.add(nextId);
        queue.push(nextNode);
      });
    }
    if (downstream.size) {
      state.cy.nodes().forEach((n) => {
        if (n.id() !== nodeId && !downstream.has(n.id())) n.style("opacity", 0.15);
      });
    }
  });
  state.cy.on("mouseout", "node", () => {
    if (state.criticalPathActive) return;
    state.cy.nodes().forEach((n) => {
      n.style("opacity", n.data("opacity"));
    });
  });
}

// ---------------------------------------------------------------------------
// renderGraph — build Cytoscape graph from issues/deps
// ---------------------------------------------------------------------------

export function renderGraph() {
  const renderStarted = performance.now();
  if (!state.allIssues.length && !(state.graphData && state.graphData.nodes)) {
    updateGraphClearButtons();
    return;
  }
  if (shouldUseGraphV2()) {
    const desiredKey = JSON.stringify(buildGraphQuery());
    if (state.graphQueryKey !== desiredKey) {
      refreshGraphData().then(() => {
        if (state.currentView === "graph") renderGraph();
      });
    }
  } else {
    state.graphMode = "legacy";
    setGraphNotice("");
  }

  const container = document.getElementById("cy");
  const epicsOnly = document.getElementById("graphEpicsOnly").checked;
  const graphPreset = document.getElementById("graphPreset")?.value || "execution";
  maybeShowGraphDefaultPresetNotice(graphPreset);
  const graphReadyOnly = document.getElementById("graphReadyOnly").checked;
  const graphBlockedOnly = document.getElementById("graphBlockedOnly").checked;
  const graphAssignee = document.getElementById("graphAssignee").value.trim();
  const graphFocusMode = document.getElementById("graphFocusMode").checked;
  const graphFocusRoot = document.getElementById("graphFocusRoot").value.trim();
  const graphFocusRadius = Number.parseInt(document.getElementById("graphFocusRadius").value || "2", 10);
  const graphNodeLimit = Number.parseInt(document.getElementById("graphNodeLimit").value || "600", 10);
  const graphEdgeLimit = Number.parseInt(document.getElementById("graphEdgeLimit").value || "2000", 10);

  const showOpen = document.getElementById("filterOpen").checked;
  const showActive = document.getElementById("filterInProgress").checked;
  const showClosed = document.getElementById("filterClosed").checked;
  const search = document.getElementById("filterSearch").value.toLowerCase().trim();

  let cyNodes = [];
  let cyEdges = [];
  const filterParts = [];
  if (graphPreset === "roadmap") filterParts.push("preset=roadmap");
  if (epicsOnly && graphPreset !== "roadmap") filterParts.push("types=epics,milestones");
  if (graphReadyOnly) filterParts.push("ready_only");
  if (graphBlockedOnly || state.blockedFilter) filterParts.push("blocked_only");
  if (graphAssignee) filterParts.push(`assignee=${graphAssignee}`);
  if (graphFocusMode && graphFocusRoot) filterParts.push(`focus=${graphFocusRoot}:${Number.isNaN(graphFocusRadius) ? 2 : graphFocusRadius}`);
  updateGraphFilterStateLabel(filterParts);

  if (state.graphMode === "v2" && state.graphData && Array.isArray(state.graphData.nodes)) {
    const nodes = state.graphData.nodes;
    const edges = Array.isArray(state.graphData.edges) ? state.graphData.edges : [];
    cyNodes = nodes.map((n) => {
      const title = n.title || n.id;
      const matchesSearch =
        !search ||
        title.toLowerCase().indexOf(search) >= 0 ||
        String(n.id).toLowerCase().indexOf(search) >= 0;
      return {
        data: {
          id: n.id,
          label: title.length > 30 ? `${title.slice(0, 28)}..` : title,
          status: n.status,
          statusCategory: n.status_category || "open",
          priority: n.priority,
          type: n.type,
          isReady: !!n.is_ready,
          childCount: n.child_count || 0,
          opacity: matchesSearch ? 1 : 0.2,
        },
      };
    });
    cyEdges = edges.map((e, i) => ({
      data: { id: e.id || `e${i}`, source: e.source, target: e.target },
    }));
  } else {
    const visibleIds = new Set();
    for (const n of state.allIssues) {
      let show = true;
      const cat = n.status_category || "open";
      const blockedByOpen = (n.blocked_by || []).some((bid) => {
        const blocker = state.issueMap[bid];
        return blocker && (blocker.status_category || "open") !== "done";
      });
      if (cat === "open" && !showOpen) show = false;
      if (cat === "wip" && !showActive) show = false;
      if (cat === "done" && !showClosed) show = false;
      if ((graphPreset === "roadmap" || epicsOnly) && n.type !== "epic" && n.type !== "milestone") show = false;
      if (graphReadyOnly && !n.is_ready) show = false;
      if ((graphBlockedOnly || state.blockedFilter) && !blockedByOpen) show = false;
      if (graphAssignee && n.assignee !== graphAssignee) show = false;
      if (show) visibleIds.add(n.id);
    }

    if (graphFocusMode && graphFocusRoot && visibleIds.has(graphFocusRoot)) {
      const radius = Number.isNaN(graphFocusRadius) ? 2 : Math.max(0, Math.min(6, graphFocusRadius));
      const neighbors = {};
      for (const dep of state.allDeps) {
        const blocked = dep.from;
        const blocker = dep.to;
        if (!neighbors[blocked]) neighbors[blocked] = new Set();
        if (!neighbors[blocker]) neighbors[blocker] = new Set();
        neighbors[blocked].add(blocker);
        neighbors[blocker].add(blocked);
      }
      const scoped = new Set([graphFocusRoot]);
      const queue = [{ id: graphFocusRoot, depth: 0 }];
      while (queue.length) {
        const cur = queue.shift();
        if (!cur || cur.depth >= radius) continue;
        for (const nxt of neighbors[cur.id] || []) {
          if (scoped.has(nxt) || !visibleIds.has(nxt)) continue;
          scoped.add(nxt);
          queue.push({ id: nxt, depth: cur.depth + 1 });
        }
      }
      Array.from(visibleIds).forEach((id) => {
        if (!scoped.has(id)) visibleIds.delete(id);
      });
    }

    cyNodes = state.allIssues
      .filter((n) => visibleIds.has(n.id))
      .map((n) => {
        const matchesSearch =
          !search ||
          n.title.toLowerCase().indexOf(search) >= 0 ||
          n.id.toLowerCase().indexOf(search) >= 0;
        return {
          data: {
            id: n.id,
            label: n.title.length > 30 ? `${n.title.slice(0, 28)}..` : n.title,
            status: n.status,
            statusCategory: n.status_category || "open",
            priority: n.priority,
            type: n.type,
            isReady: n.is_ready,
            childCount: n.children ? n.children.length : 0,
            opacity: matchesSearch ? 1 : 0.2,
          },
        };
      });

    // allDeps: [{from: blocker_id, to: blocked_id, type}]
    // edge direction: from (blocker) -> to (blocked) i.e. source blocks target
    cyEdges = state.allDeps
      .filter((e) => visibleIds.has(e.from) && visibleIds.has(e.to))
      .map((e, i) => ({
        data: { id: `e${i}`, source: e.from, target: e.to },
      }));

    let truncated = false;
    if (cyNodes.length > graphNodeLimit) {
      cyNodes = cyNodes.slice(0, graphNodeLimit);
      const keep = new Set(cyNodes.map((n) => n.data.id));
      cyEdges = cyEdges.filter((e) => keep.has(e.data.source) && keep.has(e.data.target));
      truncated = true;
    }
    if (cyEdges.length > graphEdgeLimit) {
      cyEdges = cyEdges.slice(0, graphEdgeLimit);
      truncated = true;
    }
    if (truncated && !state.graphPathNodes.size) {
      setGraphNotice(
        `Legacy graph limited for performance (${cyNodes.length} nodes/${cyEdges.length} edges shown).`,
      );
    } else if (!state.graphFallbackNotice && !state.graphPathNodes.size) {
      setGraphNotice("");
    }
  }

  const nextNodesById = new Map(cyNodes.map((n) => [n.data.id, n]));
  const nextEdgesById = new Map(cyEdges.map((e) => [e.data.id, e]));
  let created = false;

  if (!state.cy) {
    /* global cytoscape */
    state.cy = cytoscape({
      container,
      elements: cyNodes.concat(cyEdges),
      layout: {
        name: "dagre",
        rankDir: "TB",
        rankSep: 80,
        nodeSep: 40,
        padding: 20,
      },
      style: graphStyles(),
      minZoom: 0.1,
      maxZoom: 4,
    });
    created = true;
    state.cy.fit(undefined, 30);
    if (state.cy.zoom() > 1.5) {
      state.cy.zoom(1.5);
      state.cy.center();
    }
  } else {
    const currentNodeIds = new Set(state.cy.nodes().map((n) => n.id()));
    const currentEdgeIds = new Set(state.cy.edges().map((e) => e.id()));
    const topologyChanged =
      currentNodeIds.size !== nextNodesById.size ||
      currentEdgeIds.size !== nextEdgesById.size ||
      Array.from(nextNodesById.keys()).some((id) => !currentNodeIds.has(id)) ||
      Array.from(nextEdgesById.keys()).some((id) => !currentEdgeIds.has(id));

    if (!topologyChanged) {
      state.cy.batch(() => {
        state.cy.nodes().forEach((n) => {
          const next = nextNodesById.get(n.id());
          if (next) n.data(next.data);
        });
        state.cy.edges().forEach((e) => {
          const next = nextEdgesById.get(e.id());
          if (next) e.data(next.data);
        });
      });
    } else {
      const previousPositions = {};
      const selectedNodeId = state.cy.$("node:selected").id();
      const previousZoom = state.cy.zoom();
      const previousPan = state.cy.pan();
      state.cy.nodes().forEach((n) => {
        previousPositions[n.id()] = n.position();
      });
      state.cy.destroy();

      const hasPreviousPositions = Object.keys(previousPositions).length > 0;
      state.cy = cytoscape({
        container,
        elements: cyNodes.concat(cyEdges),
        layout: hasPreviousPositions
          ? {
              name: "preset",
              fit: false,
              padding: 20,
              positions: (node) => previousPositions[node.id()] || { x: 0, y: 0 },
            }
          : {
              name: "dagre",
              rankDir: "TB",
              rankSep: 80,
              nodeSep: 40,
              padding: 20,
            },
        style: graphStyles(),
        minZoom: 0.1,
        maxZoom: 4,
      });
      created = true;
      if (selectedNodeId && state.cy.$id(selectedNodeId).length) {
        state.cy.$id(selectedNodeId).select();
      }
      if (hasPreviousPositions) {
        state.cy.zoom(previousZoom);
        state.cy.pan(previousPan);
      } else {
        state.cy.fit(undefined, 30);
        if (state.cy.zoom() > 1.5) {
          state.cy.zoom(1.5);
          state.cy.center();
        }
      }
    }
  }

  if (created) bindGraphEvents();
  applyCriticalPathStyles();
  applyPathTraceStyles();
  applySearchFocus(search);

  const renderMs = Math.round(performance.now() - renderStarted);
  onGraphPathInput();
  if (!state.graphPathNodes.size) {
    if (graphFocusMode && !graphFocusRoot) {
      setGraphNotice(FOCUS_ROOT_NOTICE);
    } else {
      const notice = document.getElementById("graphNotice")?.textContent || "";
      if (notice === FOCUS_ROOT_NOTICE) setGraphNotice(state.graphFallbackNotice || "");
    }
  }
  state.graphTelemetry = { ...(state.graphTelemetry || {}), render_ms: renderMs };
  updateGraphPerfState();
  updateGraphClearButtons();
}

// ---------------------------------------------------------------------------
// graphFit — reset zoom and center
// ---------------------------------------------------------------------------

export function graphFit() {
  if (state.cy) {
    state.cy.fit(undefined, 30);
    if (state.cy.zoom() > 1.5) {
      state.cy.zoom(1.5);
      state.cy.center();
    }
  }
}

// ---------------------------------------------------------------------------
// toggleCriticalPath — toggle critical path highlight
// ---------------------------------------------------------------------------

export async function toggleCriticalPath() {
  state.criticalPathActive = !state.criticalPathActive;
  const btn = document.getElementById("btnCritPath");
  if (state.criticalPathActive) {
    btn.className = "px-2 py-0.5 rounded bg-red-600 text-white";
    const data = await fetchCriticalPath();
    state.criticalPathIds = new Set(data?.path ? data.path.map((p) => p.id) : []);
  } else {
    btn.className = "px-2 py-0.5 rounded bg-overlay bg-overlay-hover";
    state.criticalPathIds.clear();
  }
  renderGraph();
}

// ---------------------------------------------------------------------------
// computeImpactScores — BFS downstream count
// ---------------------------------------------------------------------------

export function computeImpactScores() {
  const forward = {};
  for (const d of state.allDeps) {
    if (!forward[d.to]) forward[d.to] = [];
    forward[d.to].push(d.from);
  }
  state.impactScores = {};
  for (const i of state.allIssues) {
    const visited = new Set();
    const queue = [i.id];
    while (queue.length) {
      const cur = queue.shift();
      for (const next of forward[cur] || []) {
        if (!visited.has(next)) {
          visited.add(next);
          queue.push(next);
        }
      }
    }
    state.impactScores[i.id] = visited.size;
  }
}

// ---------------------------------------------------------------------------
// computeHealthScore — weighted project health scoring
// ---------------------------------------------------------------------------

export function computeHealthScore() {
  if (!state.allIssues.length) return;
  const openIssues = state.allIssues.filter((i) => (i.status_category || "open") !== "done");
  const blockedCount = openIssues.filter((i) =>
    (i.blocked_by || []).some((bid) => {
      const b = state.issueMap[bid];
      return b && (b.status_category || "open") !== "done";
    }),
  ).length;
  const blockedRatio = openIssues.length ? blockedCount / openIssues.length : 0;
  const blockedScore = Math.round(25 * (1 - blockedRatio));
  const wipIssues = state.allIssues.filter((i) => (i.status_category || "open") === "wip");
  const staleWip = wipIssues.filter(
    (i) => i.updated_at && Date.now() - new Date(i.updated_at).getTime() > 24 * 3600000,
  ).length;
  const freshScore = wipIssues.length ? Math.round(25 * (1 - staleWip / wipIssues.length)) : 25;
  const readyCount = state.allIssues.filter((i) => i.is_ready).length;
  const readyScore = openIssues.length
    ? Math.min(25, Math.round((25 * readyCount) / Math.max(openIssues.length * 0.3, 1)))
    : 25;
  const agentWip = {};
  for (const i of wipIssues) {
    if (i.assignee) agentWip[i.assignee] = (agentWip[i.assignee] || 0) + 1;
  }
  const vals = Object.values(agentWip);
  const maxWip = vals.length ? Math.max(...vals) : 0;
  const balanceScore = maxWip > 5 ? 10 : maxWip > 3 ? 18 : 25;
  const score = blockedScore + freshScore + readyScore + balanceScore;

  const badge = document.getElementById("healthBadge");
  if (!badge) return;
  badge.textContent = score;
  badge.title = `Health: ${score}/100`;
  if (score >= 75)
    badge.className =
      "cursor-pointer px-2 py-0.5 rounded text-xs font-bold bg-emerald-900/50 text-emerald-400 border border-emerald-700";
  else if (score >= 50)
    badge.className =
      "cursor-pointer px-2 py-0.5 rounded text-xs font-bold bg-amber-900/50 text-amber-400 border border-amber-700";
  else
    badge.className =
      "cursor-pointer px-2 py-0.5 rounded text-xs font-bold bg-red-900/50 text-red-400 border border-red-700";

  window._healthBreakdown = {
    score,
    blocked: {
      score: blockedScore,
      max: 25,
      detail: `${blockedCount} blocked of ${openIssues.length} open`,
    },
    freshness: {
      score: freshScore,
      max: 25,
      detail: `${staleWip} stale WIP of ${wipIssues.length}`,
    },
    ready: {
      score: readyScore,
      max: 25,
      detail: `${readyCount} ready issues`,
    },
    balance: {
      score: balanceScore,
      max: 25,
      detail: `Max agent WIP: ${maxWip}`,
    },
  };
}

// ---------------------------------------------------------------------------
// showHealthBreakdown — health breakdown modal in detail panel
// ---------------------------------------------------------------------------

export function showHealthBreakdown() {
  const b = window._healthBreakdown;
  if (!b) return;
  const panel = document.getElementById("detailContent");
  const dp = document.getElementById("detailPanel");
  dp.classList.remove("translate-x-full");
  state.selectedIssue = null;
  panel.innerHTML =
    '<div class="flex items-center justify-between mb-3">' +
    `<span class="text-base font-semibold" style="color:var(--text-primary)">System Health: ${b.score}/100</span>` +
    '<button onclick="closeDetail()" class="text-muted text-primary-hover text-lg">&times;</button></div>' +
    ["blocked", "freshness", "ready", "balance"]
      .map((k) => {
        const f = b[k];
        const pct = Math.round((f.score / f.max) * 100);
        return (
          '<div class="mb-3"><div class="flex justify-between text-xs mb-1">' +
          `<span class="capitalize" style="color:var(--text-primary)">${k}</span>` +
          `<span style="color:var(--text-secondary)">${f.score}/${f.max}</span></div>` +
          '<div class="w-full h-2 rounded-full overflow-hidden" style="background:var(--surface-base)">' +
          `<div class="h-full rounded-full ${pct >= 75 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500"}" style="width:${pct}%"></div></div>` +
          `<div class="text-xs mt-0.5" style="color:var(--text-muted)">${f.detail}</div></div>`
        );
      })
      .join("");
}

// ---------------------------------------------------------------------------
// Contextual help popovers
// ---------------------------------------------------------------------------

export function showHealthHelp(btn) {
  showPopover(
    btn,
    '<div class="font-medium mb-2" style="color:var(--text-primary)">Health Score (0\u2013100)</div>' +
      '<div style="color:var(--text-secondary)" class="space-y-1">' +
      '<div><span class="text-emerald-400 font-medium">Blocked</span> (25 pts) \u2014 Fewer blocked issues = higher score</div>' +
      '<div><span class="font-medium" style="color:var(--accent)">Freshness</span> (25 pts) \u2014 WIP items updated recently, not stale</div>' +
      '<div><span class="text-amber-400 font-medium">Ready</span> (25 pts) \u2014 Enough unblocked work available</div>' +
      '<div><span class="font-medium" style="color:var(--text-primary)">Balance</span> (25 pts) \u2014 No agent overloaded with WIP</div>' +
      "</div>" +
      '<div style="color:var(--text-muted);border-top:1px solid var(--border-default)" class="mt-2 pt-2">Click the badge number for a detailed breakdown.</div>',
  );
}

export function showReadyHelp(btn) {
  showPopover(
    btn,
    '<div class="font-medium mb-2" style="color:var(--text-primary)">Ready Issues</div>' +
      '<div style="color:var(--text-secondary)" class="space-y-1">' +
      '<div>Issues with <span class="text-emerald-400">no open blockers</span> that can be worked on immediately.</div>' +
      '<div class="mt-1"><span class="text-emerald-400">&#9679;</span> Green left border on cards = ready</div>' +
      '<div class="mt-1">Toggle this button to sort ready issues to the top.</div>' +
      "</div>",
  );
}

export function showBlockedHelp(btn) {
  showPopover(
    btn,
    '<div class="font-medium mb-2" style="color:var(--text-primary)">Blocked Issues</div>' +
      '<div style="color:var(--text-secondary)" class="space-y-1">' +
      '<div>Issues that <span class="text-red-400">depend on other incomplete work</span>.</div>' +
      '<div class="mt-1"><span class="text-red-400">&#128279;</span> Shows "blocked by N" on cards</div>' +
      '<div class="mt-1">Toggle to filter to only blocked issues \u2014 useful for identifying bottlenecks.</div>' +
      "</div>",
  );
}
