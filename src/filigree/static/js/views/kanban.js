// ---------------------------------------------------------------------------
// Kanban view — standard, cluster, and type-filtered boards + drag-and-drop.
// ---------------------------------------------------------------------------

import { fetchTransitions, patchIssue } from "../api.js";
import { getFilteredIssues } from "../filters.js";
import { CATEGORY_COLORS, PRIORITY_COLORS, state, TYPE_ICONS } from "../state.js";
import { escHtml, escJsSingle, showToast } from "../ui.js";

// --- Callbacks for functions not yet available at import time ---

export const callbacks = {
  openDetail: null,
  fetchData: null,
  updateBatchBar: null,
  updateHash: null,
};

// ---------------------------------------------------------------------------
// renderKanban — main dispatcher based on kanbanMode / typeTemplate
// ---------------------------------------------------------------------------

export function renderKanban() {
  const board = document.getElementById("kanbanBoard");
  const items = getFilteredIssues();

  // Search no-results state
  if (!items.length && state.searchResults !== null) {
    board.innerHTML =
      '<div class="flex-1 flex items-center justify-center text-xs" style="color:var(--text-muted)">' +
      '<div class="text-center"><div class="mb-2" style="color:var(--text-primary)">No matches found</div>' +
      `<div>Try broader search terms or <button onclick="clearSearch()" style="color:var(--accent)" class="hover:underline">clear search</button></div></div></div>`;
    return;
  }

  // Type-filtered kanban: one column per type state, restricted to matching type
  if (state.typeTemplate) {
    const stateColumns = {};
    for (const s of state.typeTemplate.states) {
      stateColumns[s.name] = [];
    }
    for (const i of items) {
      if (i.type === state.typeTemplate.type && stateColumns[i.status]) {
        stateColumns[i.status].push(i);
      }
    }
    board.innerHTML = renderTypeKanban(stateColumns, state.typeTemplate);
    return;
  }

  // Default: 3-category columns (open/wip/done)
  const columns = { open: [], wip: [], done: [] };
  for (const i of items) {
    const cat = i.status_category || "open";
    if (columns[cat]) columns[cat].push(i);
  }

  if (state.kanbanMode === "cluster") {
    board.innerHTML = renderClusterKanban(columns);
  } else {
    board.innerHTML = renderStandardKanban(columns);
  }
}

// ---------------------------------------------------------------------------
// renderStandardKanban — 3-column Open/WIP/Done board
// ---------------------------------------------------------------------------

export function renderStandardKanban(columns) {
  const colDefs = [
    { key: "open", label: "Open", color: CATEGORY_COLORS.open },
    { key: "wip", label: "In Progress", color: CATEGORY_COLORS.wip },
    { key: "done", label: "Done", color: CATEGORY_COLORS.done },
  ];
  return colDefs
    .map((col) => {
      const issues = columns[col.key] || [];
      return (
        `<div class="kanban-col flex flex-col" data-status-category="${col.key}">` +
        '<div class="flex items-center gap-2 mb-2 px-1">' +
        `<span class="w-2 h-2 rounded-full" style="background:${col.color}"></span>` +
        `<span class="font-medium text-xs" style="color:var(--text-primary)">${col.label}</span>` +
        `<span class="text-xs" style="color:var(--text-muted)">${issues.length}</span>` +
        "</div>" +
        '<div class="flex flex-col gap-2 overflow-y-auto scrollbar-thin pr-1 min-h-[200px]" style="max-height: calc(100vh - 160px);">' +
        (issues.length
          ? issues.map((i) => renderCard(i)).join("")
          : '<div class="text-xs p-4 text-center" style="color:var(--text-muted)">' +
            (col.key === "open"
              ? '<div class="mb-2">No open issues</div><button onclick="showCreateForm()" style="color:var(--accent)" class="hover:underline">+ Create an issue</button>'
              : col.key === "wip"
                ? '<div>No work in progress</div><div style="color:var(--text-muted)" class="mt-1">Move an open issue to in-progress to start</div>'
                : "<div>No completed issues yet</div>") +
            "</div>") +
        "</div></div>"
      );
    })
    .join("");
}

// ---------------------------------------------------------------------------
// renderClusterKanban — grouped by epic with progress bars
// ---------------------------------------------------------------------------

export function renderClusterKanban(columns) {
  const colDefs = [
    { key: "open", label: "Open", color: CATEGORY_COLORS.open },
    { key: "wip", label: "In Progress", color: CATEGORY_COLORS.wip },
    { key: "done", label: "Done", color: CATEGORY_COLORS.done },
  ];
  return colDefs
    .map((col) => {
      const issues = columns[col.key] || [];
      const epicIssues = issues.filter(
        (i) => (i.type === "epic" || i.type === "milestone") && i.children && i.children.length > 0,
      );
      const epicIds = new Set(epicIssues.map((i) => i.id));
      const childIds = new Set();
      for (const i of epicIssues) {
        for (const c of i.children || []) {
          childIds.add(c);
        }
      }
      const orphans = issues.filter((i) => !epicIds.has(i.id) && !childIds.has(i.id));

      const epicCards = epicIssues.map((epic) => renderClusterCard(epic)).join("");
      const orphanCards = orphans.map((i) => renderCard(i)).join("");

      return (
        `<div class="kanban-col flex flex-col" data-status-category="${col.key}">` +
        '<div class="flex items-center gap-2 mb-2 px-1">' +
        `<span class="w-2 h-2 rounded-full" style="background:${col.color}"></span>` +
        `<span class="font-medium text-xs" style="color:var(--text-primary)">${col.label}</span>` +
        `<span class="text-xs" style="color:var(--text-muted)">${issues.length}</span>` +
        "</div>" +
        '<div class="flex flex-col gap-2 overflow-y-auto scrollbar-thin pr-1 min-h-[200px]" style="max-height: calc(100vh - 160px);">' +
        (issues.length
          ? epicCards + orphanCards
          : '<div class="text-xs italic p-2" style="color:var(--text-muted)">No issues</div>') +
        "</div></div>"
      );
    })
    .join("");
}

// ---------------------------------------------------------------------------
// renderClusterCard — epic card with children and progress bar
// ---------------------------------------------------------------------------

export function renderClusterCard(epic) {
  const children = (epic.children || []).map((cid) => state.issueMap[cid]).filter(Boolean);
  const counts = { open: 0, wip: 0, done: 0 };
  for (const c of children) {
    const cat = c.status_category || "open";
    if (counts[cat] !== undefined) counts[cat]++;
  }
  const total = children.length;
  const expanded = state.expandedEpics.has(epic.id);

  const pctOpen = total ? (counts.open / total) * 100 : 0;
  const pctActive = total ? (counts.wip / total) * 100 : 0;
  const pctClosed = total ? (counts.done / total) * 100 : 0;

  let childHtml = "";
  if (expanded) {
    childHtml =
      '<div class="mt-2 ml-4 flex flex-col gap-1">' +
      children.map((c) => renderCard(c)).join("") +
      "</div>";
  }

  return (
    `<div class="rounded p-3 cursor-pointer ${epic.is_ready ? "ready-border" : ""}" style="background:var(--surface-raised);border:1px solid var(--border-default)" aria-expanded="${expanded}" onclick="toggleEpicExpand('${epic.id}')">` +
    '<div class="flex items-center justify-between mb-1">' +
    `<span class="text-xs">${TYPE_ICONS[epic.type] || ""} <span class="font-medium" style="color:var(--text-primary)">${escHtml(epic.title.slice(0, 40))}</span></span>` +
    `<span class="text-xs" style="color:var(--text-muted)">[${total}]</span>` +
    "</div>" +
    '<div class="w-full h-2 rounded-full flex overflow-hidden mb-1" style="background:var(--surface-base)">' +
    `<div style="width:${pctClosed}%;background:${CATEGORY_COLORS.done}"></div>` +
    `<div style="width:${pctActive}%;background:${CATEGORY_COLORS.wip}"></div>` +
    `<div style="width:${pctOpen}%;background:${CATEGORY_COLORS.open}"></div>` +
    "</div>" +
    `<div class="text-xs" style="color:var(--text-muted)">${counts.open} open &middot; ${counts.wip} active &middot; ${counts.done} done</div>` +
    (expanded
      ? '<div class="text-xs mt-1" style="color:var(--accent)">&#9660; expanded</div>'
      : '<div class="text-xs mt-1" style="color:var(--text-muted)">&#9654; click to expand</div>') +
    childHtml +
    "</div>"
  );
}

// ---------------------------------------------------------------------------
// renderCard — individual issue card HTML
// ---------------------------------------------------------------------------

export function renderCard(issue) {
  const typeIcon = TYPE_ICONS[issue.type] || "";
  const prioColor = PRIORITY_COLORS[issue.priority] || "#6B7280";
  const cat = issue.status_category || "open";
  const catColor = CATEGORY_COLORS[cat] || "#64748B";
  const blockedCount = (issue.blocked_by || []).filter((bid) => {
    const b = state.issueMap[bid];
    return b && (b.status_category || "open") !== "done";
  }).length;
  const readyClass = issue.is_ready && cat === "open" ? "ready-border" : "";

  let agingClass = "";
  if (cat === "wip" && issue.updated_at) {
    const ageMs = Date.now() - new Date(issue.updated_at).getTime();
    const ageHours = ageMs / 3600000;
    if (ageHours > 24) agingClass = "stale-border";
    else if (ageHours > 4) agingClass = "aging-border";
  }

  const changedClass = state.changedIds.has(issue.id) ? "changed-flash" : "";
  const safeIssueId = escJsSingle(issue.id);

  const checkbox = state.multiSelectMode
    ? `<input type="checkbox" ${state.selectedCards.has(issue.id) ? "checked" : ""} onclick="toggleCardSelect(event,'${safeIssueId}')" class="mr-1" style="accent-color:var(--accent)">`
    : "";

  const isDraggable = state.kanbanMode !== "cluster" && !state.multiSelectMode;

  let ageLabel = "";
  if (cat === "wip" && issue.updated_at) {
    const mins = Math.floor((Date.now() - new Date(issue.updated_at).getTime()) / 60000);
    if (mins < 60) {
      ageLabel = `<span style="color:var(--text-muted)">${mins}m</span>`;
    } else {
      const hrs = Math.floor(mins / 60);
      if (hrs < 24) {
        ageLabel =
          hrs > 4
            ? `<span class="text-amber-400">${hrs}h</span>`
            : `<span style="color:var(--text-muted)">${hrs}h</span>`;
      } else {
        ageLabel = `<span class="text-red-400">${Math.floor(hrs / 24)}d</span>`;
      }
    }
  }

  return (
    `<div class="card rounded p-3 cursor-pointer ${readyClass} ${agingClass} ${changedClass}"` +
    ' style="background:var(--surface-raised);border:1px solid var(--border-default)"' +
    (isDraggable ? ' draggable="true"' : "") +
    ` tabindex="0" data-id="${escHtml(issue.id)}" onclick="openDetail('${safeIssueId}')">` +
    '<div class="flex items-center gap-2 mb-1">' +
    checkbox +
    `<span>${typeIcon}</span>` +
    (issue.priority <= 1
      ? `<span class="text-xs font-bold shrink-0" style="color:${prioColor}" title="Priority ${issue.priority} (${["Critical", "High", "Medium", "Low", "Backlog"][issue.priority]})">P${issue.priority}</span>`
      : `<span class="w-2 h-2 rounded-full shrink-0" style="background:${prioColor}" title="Priority ${issue.priority} (${["Critical", "High", "Medium", "Low", "Backlog"][issue.priority]})"></span>`) +
    `<span class="font-medium truncate" style="color:var(--text-primary)">${escHtml(issue.title.slice(0, 50))}</span>` +
    "</div>" +
    '<div class="flex items-center gap-2 text-xs" style="color:var(--text-muted)">' +
    `<span>${escHtml(issue.id)}</span>` +
    `<span class="rounded px-1" style="background:var(--surface-overlay);color:var(--text-secondary)">${escHtml(issue.type.replace(/_/g, " "))}</span>` +
    `<span class="rounded px-1" style="background:${catColor}33;color:${catColor}">${escHtml(issue.status || "")}</span>` +
    (blockedCount > 0
      ? `<span class="text-red-400">\u{1F517} blocked by ${blockedCount}</span>`
      : "") +
    (state.impactScores[issue.id] > 0
      ? `<span class="text-amber-400" title="Impact: blocks ${state.impactScores[issue.id]} downstream issue${state.impactScores[issue.id] !== 1 ? "s" : ""} \u2014 resolve this to unblock work">\u26A1${state.impactScores[issue.id]}</span>`
      : "") +
    (issue.assignee
      ? `<span style="color:var(--text-secondary)">\u{1F464} ${escHtml(issue.assignee)}</span>`
      : "") +
    ageLabel +
    "</div>" +
    "</div>"
  );
}

// ---------------------------------------------------------------------------
// toggleEpicExpand — toggle epic card expansion
// ---------------------------------------------------------------------------

export function toggleEpicExpand(epicId) {
  if (state.expandedEpics.has(epicId)) state.expandedEpics.delete(epicId);
  else state.expandedEpics.add(epicId);
  renderKanban();
}

// ---------------------------------------------------------------------------
// renderTypeKanban — type-filtered kanban (WFT-FR-064)
// ---------------------------------------------------------------------------

export function renderTypeKanban(stateColumns, template) {
  return template.states
    .map((s) => {
      const issues = stateColumns[s.name] || [];
      const catColor = CATEGORY_COLORS[s.category] || "#64748B";
      return (
        `<div class="kanban-col flex flex-col" data-status="${s.name}" data-status-category="${s.category}">` +
        '<div class="flex items-center gap-2 mb-2 px-1">' +
        `<span class="w-2 h-2 rounded-full" style="background:${catColor}"></span>` +
        `<span class="font-medium text-xs" style="color:var(--text-primary)">${s.name}</span>` +
        `<span class="text-xs" style="color:var(--text-muted)">${issues.length}</span>` +
        "</div>" +
        '<div class="flex flex-col gap-2 overflow-y-auto scrollbar-thin pr-1 min-h-[200px]" style="max-height: calc(100vh - 160px);">' +
        (issues.length
          ? issues.map((i) => renderCard(i)).join("")
          : '<div class="text-xs italic p-2" style="color:var(--text-muted)">No issues</div>') +
        "</div></div>"
      );
    })
    .join("");
}

// ---------------------------------------------------------------------------
// initDragAndDrop — drag-and-drop event handlers for kanban
// ---------------------------------------------------------------------------

export function initDragAndDrop() {
  const board = document.getElementById("kanbanBoard");
  if (!board) return;

  board.addEventListener("dragstart", (e) => {
    const card = e.target.closest('.card[draggable="true"]');
    if (!card) return;
    state._dragIssueId = card.getAttribute("data-id");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", state._dragIssueId);
    card.style.opacity = "0.5";

    // Fetch valid transitions, then mark columns
    state._transitionsLoaded = false;
    const dragToken = state._dragIssueId;
    fetchTransitions(state._dragIssueId).then((transitions) => {
      if (state._dragIssueId !== dragToken) return; // drag ended before response
      state._dragTransitions = transitions;
      const validCategories = new Set();
      const validStatuses = new Set();
      for (const t of transitions) {
        if (t.ready) {
          validStatuses.add(t.to);
          validCategories.add(t.category);
        }
      }
      const cols = board.querySelectorAll(".kanban-col");
      for (const col of cols) {
        const colStatus = col.getAttribute("data-status");
        const colCat = col.getAttribute("data-status-category");
        const isValid = colStatus ? validStatuses.has(colStatus) : validCategories.has(colCat);
        // Don't mark the source column
        const sourceIssue = state.issueMap[state._dragIssueId];
        const isSameCol = colStatus
          ? colStatus === sourceIssue?.status
          : colCat === sourceIssue?.status_category;
        if (isSameCol) continue;
        col.classList.add(isValid ? "drag-valid" : "drag-invalid");
      }
      state._transitionsLoaded = true;
    });
  });

  board.addEventListener("dragover", (e) => {
    if (!state._transitionsLoaded) return;
    const col = e.target.closest(".kanban-col");
    if (!col || col.classList.contains("drag-invalid")) return;
    if (col.classList.contains("drag-valid")) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    }
  });

  board.addEventListener("drop", (e) => {
    e.preventDefault();
    const col = e.target.closest(".kanban-col");
    if (!col || !col.classList.contains("drag-valid") || !state._dragIssueId) return;

    let targetStatus = col.getAttribute("data-status");
    if (!targetStatus) {
      // Standard board: find first ready transition matching this category
      const targetCat = col.getAttribute("data-status-category");
      const match = state._dragTransitions.find((t) => t.ready && t.category === targetCat);
      if (match) targetStatus = match.to;
    }
    if (!targetStatus) return;

    const issueId = state._dragIssueId;
    showToast(`Moving to ${targetStatus}...`, "info");

    patchIssue(issueId, { status: targetStatus }).then((result) => {
      if (result.ok) {
        showToast(`Moved to ${targetStatus}`, "success");
        if (callbacks.fetchData) callbacks.fetchData();
      } else {
        showToast(`Error: ${result.error || "Move failed"}`, "error");
      }
    });
  });

  board.addEventListener("dragend", (e) => {
    // Clean up all drag visual states
    const card = e.target.closest(".card");
    if (card) card.style.opacity = "";
    state._dragIssueId = null;
    state._dragTransitions = [];
    state._transitionsLoaded = false;
    for (const col of board.querySelectorAll(".kanban-col")) {
      col.classList.remove("drag-valid", "drag-invalid");
    }
  });
}
