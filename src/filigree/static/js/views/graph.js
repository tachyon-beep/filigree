// ---------------------------------------------------------------------------
// Graph view — Cytoscape dependency graph, critical path, health scoring.
// ---------------------------------------------------------------------------

import { fetchCriticalPath } from "../api.js";
import { CATEGORY_COLORS, state, THEME_COLORS } from "../state.js";
import { showPopover } from "../ui.js";

// --- Callbacks for functions not yet available at import time ---

export const callbacks = { openDetail: null, fetchData: null };

// ---------------------------------------------------------------------------
// renderGraph — build Cytoscape graph from issues/deps
// ---------------------------------------------------------------------------

export function renderGraph() {
  if (!state.allIssues.length) return;
  const container = document.getElementById("cy");
  const epicsOnly = document.getElementById("graphEpicsOnly").checked;

  const showOpen = document.getElementById("filterOpen").checked;
  const showActive = document.getElementById("filterInProgress").checked;
  const showClosed = document.getElementById("filterClosed").checked;
  const search = document.getElementById("filterSearch").value.toLowerCase().trim();

  const visibleIds = new Set();
  for (const n of state.allIssues) {
    let show = true;
    const cat = n.status_category || "open";
    if (cat === "open" && !showOpen) show = false;
    if (cat === "wip" && !showActive) show = false;
    if (cat === "done" && !showClosed) show = false;
    if (epicsOnly && n.type !== "epic" && n.type !== "milestone") show = false;
    if (show) visibleIds.add(n.id);
  }

  const cyNodes = state.allIssues
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
  const cyEdges = state.allDeps
    .filter((e) => visibleIds.has(e.from) && visibleIds.has(e.to))
    .map((e, i) => ({
      data: { id: `e${i}`, source: e.from, target: e.to },
    }));

  if (state.cy) state.cy.destroy();

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
    style: [
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
    ],
    minZoom: 0.1,
    maxZoom: 4,
  });

  state.cy.on("tap", "node", (evt) => {
    if (callbacks.openDetail) callbacks.openDetail(evt.target.id());
  });
  state.cy.fit(undefined, 30);
  if (state.cy.zoom() > 1.5) {
    state.cy.zoom(1.5);
    state.cy.center();
  }

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
  }

  state.cy.on("mouseover", "node", (evt) => {
    if (state.criticalPathActive) return;
    const nodeId = evt.target.id();
    const downstream = new Set();
    const queue = [nodeId];
    while (queue.length) {
      const cur = queue.shift();
      state.cy.edges().forEach((e) => {
        if (e.source().id() === cur && !downstream.has(e.target().id())) {
          downstream.add(e.target().id());
          queue.push(e.target().id());
        }
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
