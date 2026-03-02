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
  const filesEl = document.getElementById("filesView");
  if (filesEl) filesEl.classList.toggle("hidden", view !== "files");
  const healthEl = document.getElementById("healthView");
  if (healthEl) healthEl.classList.toggle("hidden", view !== "health");
  const releasesEl = document.getElementById("releasesView");
  if (releasesEl) releasesEl.classList.toggle("hidden", view !== "releases");

  document.getElementById("btnGraph").className = view === "graph" ? ACTIVE_CLASS : INACTIVE_CLASS;
  document.getElementById("btnKanban").className =
    view === "kanban" ? ACTIVE_CLASS : INACTIVE_CLASS;
  document.getElementById("btnMetrics").className =
    view === "metrics" ? ACTIVE_CLASS : INACTIVE_CLASS;
  document.getElementById("btnActivity").className =
    view === "activity" ? ACTIVE_CLASS : INACTIVE_CLASS;
  document.getElementById("btnWorkflow").className =
    view === "workflow" ? ACTIVE_CLASS : INACTIVE_CLASS;
  const btnFiles = document.getElementById("btnFiles");
  if (btnFiles) btnFiles.className = view === "files" ? ACTIVE_CLASS : INACTIVE_CLASS;
  const btnHealth = document.getElementById("btnHealth");
  if (btnHealth) btnHealth.className = view === "health" ? ACTIVE_CLASS : INACTIVE_CLASS;
  const btnReleases = document.getElementById("btnReleases");
  if (btnReleases) btnReleases.className = view === "releases" ? ACTIVE_CLASS : INACTIVE_CLASS;

  updateHash();

  // Update skip link to target current view
  const skipLink = document.querySelector('a[href^="#"][class*="sr-only"]');
  if (skipLink) {
    skipLink.href = "#" + view + "View";
  }

  const loader = viewLoaders[view];
  if (loader) {
    try {
      loader();
    } catch (err) {
      console.error(`[switchView] Failed to load "${view}" view:`, err);
      const container = document.getElementById(view + "View");
      if (container) {
        container.innerHTML =
          '<div class="p-4 text-xs" style="color:var(--text-muted)">' +
          `Failed to load the ${view} view. ` +
          '<button class="underline" style="color:var(--accent)" ' +
          `onclick="switchView('${view}')">Retry</button>` +
          "</div>";
      }
    }
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
  } else if (view === "files") {
    state.currentView = "files";
  } else if (view === "health") {
    state.currentView = "health";
  } else if (view === "releases") {
    state.currentView = "releases";
  } else if (view === "kanban-cluster") {
    state.currentView = "kanban";
    state.kanbanMode = "cluster";
  } else {
    state.currentView = "kanban";
    state.kanbanMode = "standard";
  }

  const result = { project: null, issue: null };

  const issuePart = parts.find((p) => p.indexOf("issue=") === 0);
  if (issuePart) {
    state.selectedIssue = issuePart.split("=")[1];
    result.issue = state.selectedIssue;
  }

  const projectPart = parts.find((p) => p.indexOf("project=") === 0);
  if (projectPart) {
    result.project = decodeURIComponent(projectPart.split("=")[1]);
  }

  return result;
}
