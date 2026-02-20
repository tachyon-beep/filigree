// ---------------------------------------------------------------------------
// View routing, hash management, and render dispatch.
// ---------------------------------------------------------------------------

import { state } from "./state.js";

// --- View registry (avoids circular imports) ---
// Each view registers a loader function; switchView / render dispatch to it.

const viewLoaders = {};

export function registerView(name, loader) {
  viewLoaders[name] = loader;
}

// --- Callbacks for functions not available at import time ---

export const callbacks = {
  openDetail: null,
  closeDetail: null,
  updateBatchBar: null,
  updateTypeFilterUI: null,
  renderKanban: null,
};

// --- View switching ---

const ACTIVE_CLASS = "px-3 py-1 rounded text-xs font-medium bg-accent text-primary";
const INACTIVE_CLASS =
  "px-3 py-1 rounded text-xs font-medium bg-overlay text-secondary bg-overlay-hover";

export function switchView(view) {
  state.currentView = view;

  document.getElementById("graphView").classList.toggle("hidden", view !== "graph");
  document.getElementById("kanbanView").classList.toggle("hidden", view !== "kanban");
  document.getElementById("metricsView").classList.toggle("hidden", view !== "metrics");
  document.getElementById("activityView").classList.toggle("hidden", view !== "activity");
  document.getElementById("workflowView").classList.toggle("hidden", view !== "workflow");

  document.getElementById("btnGraph").className = view === "graph" ? ACTIVE_CLASS : INACTIVE_CLASS;
  document.getElementById("btnKanban").className =
    view === "kanban" ? ACTIVE_CLASS : INACTIVE_CLASS;
  document.getElementById("btnMetrics").className =
    view === "metrics" ? ACTIVE_CLASS : INACTIVE_CLASS;
  document.getElementById("btnActivity").className =
    view === "activity" ? ACTIVE_CLASS : INACTIVE_CLASS;
  document.getElementById("btnWorkflow").className =
    view === "workflow" ? ACTIVE_CLASS : INACTIVE_CLASS;

  updateHash();

  const loader = viewLoaders[view];
  if (loader) {
    loader();
  } else if (view === "kanban" && viewLoaders.kanban) {
    viewLoaders.kanban();
  }
}

export function switchKanbanMode(mode) {
  state.typeTemplate = null;
  const typeSelect = document.getElementById("filterType");
  if (typeSelect) typeSelect.value = "";
  if (callbacks.updateTypeFilterUI) callbacks.updateTypeFilterUI(false);

  state.kanbanMode = mode;

  document.getElementById("btnStandard").className =
    mode === "standard"
      ? "px-2 py-0.5 rounded bg-accent text-primary"
      : "px-2 py-0.5 rounded bg-overlay bg-overlay-hover";
  document.getElementById("btnCluster").className =
    mode === "cluster"
      ? "px-2 py-0.5 rounded bg-accent text-primary"
      : "px-2 py-0.5 rounded bg-overlay bg-overlay-hover";

  updateHash();

  if (callbacks.renderKanban) callbacks.renderKanban();
  else if (viewLoaders.kanban) viewLoaders.kanban();
}

// --- Render dispatch ---

export function render() {
  const loader = viewLoaders[state.currentView];
  if (loader) {
    loader();
  } else if (state.currentView === "graph" && viewLoaders.graph) {
    viewLoaders.graph();
  } else if (viewLoaders.kanban) {
    viewLoaders.kanban();
  }
  if (callbacks.updateBatchBar) callbacks.updateBatchBar();
}

// --- Hash management ---

export function updateHash() {
  let hash = `#${state.currentView}`;
  if (state.currentView === "kanban" && state.kanbanMode === "cluster") {
    hash = "#kanban-cluster";
  }
  if (state.currentProjectKey) {
    hash += `&project=${encodeURIComponent(state.currentProjectKey)}`;
  }
  if (state.selectedIssue) {
    hash += `&issue=${state.selectedIssue}`;
  }
  history.replaceState(null, "", hash);
}

export function parseHash() {
  const hash = location.hash.slice(1);
  const parts = hash.split("&");
  const view = parts[0] || "kanban";

  if (view === "graph") {
    state.currentView = "graph";
  } else if (view === "metrics") {
    state.currentView = "metrics";
  } else if (view === "activity") {
    state.currentView = "activity";
  } else if (view === "workflow") {
    state.currentView = "workflow";
  } else if (view === "kanban-cluster") {
    state.currentView = "kanban";
    state.kanbanMode = "cluster";
  } else {
    state.currentView = "kanban";
    state.kanbanMode = "standard";
  }

  const issuePart = parts.find((p) => p.indexOf("issue=") === 0);
  if (issuePart) {
    state.selectedIssue = issuePart.split("=")[1];
  }
}
