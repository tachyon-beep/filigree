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

// Deprecated tab aliases — redirect old tab IDs to new destinations.
const ALIASES = { health: "files", activity: "insights" };

export function switchView(view) {
  if (ALIASES[view]) {
    console.warn(`[switchView] "${view}" is deprecated, redirecting to "${ALIASES[view]}"`);
    view = ALIASES[view];
  }

  state.currentView = view;

  // Data-driven: toggle visibility for all registered views
  for (const name of Object.keys(viewLoaders)) {
    const el = document.getElementById(`${name}View`);
    if (el) el.classList.toggle("hidden", name !== view);
    const btn = document.getElementById(`btn${name[0].toUpperCase()}${name.slice(1)}`);
    if (btn) btn.className = name === view ? ACTIVE_CLASS : INACTIVE_CLASS;
  }

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
  let view = parts[0] || "kanban";

  // Apply deprecation aliases
  if (ALIASES[view]) {
    console.warn(`[parseHash] Hash "#${view}" is deprecated, redirecting to "#${ALIASES[view]}"`);
    view = ALIASES[view];
  }

  if (view === "kanban-cluster") {
    state.currentView = "kanban";
    state.kanbanMode = "cluster";
  } else if (view === "kanban-list") {
    state.currentView = "kanban";
    state.kanbanMode = "list";
  } else if (viewLoaders[view]) {
    state.currentView = view;
  } else {
    // Unknown view falls through to default
    state.currentView = "kanban";
    state.kanbanMode = "board";
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
