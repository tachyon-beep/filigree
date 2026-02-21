// ---------------------------------------------------------------------------
// Files view — file list table, file detail panel, file timeline.
// ---------------------------------------------------------------------------

import {
  fetchFileDetail,
  fetchFileFindings,
  fetchFiles,
  fetchFileTimeline,
  postFileAssociation,
} from "../api.js";
import { SEVERITY_COLORS, state } from "../state.js";
import { escHtml, showToast } from "../ui.js";

// --- Severity helpers ---

function severityBadge(severity, count) {
  if (!count) return "";
  const c = SEVERITY_COLORS[severity] || SEVERITY_COLORS.info;
  return `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs ${c.bg} ${c.text} ${c.border}" style="border-width:1px">${count}</span>`;
}

function healthBorderClass(summary) {
  if (!summary) return "";
  if (summary.critical > 0) return "border-l-4 border-l-red-500";
  if (summary.high > 0) return "border-l-4 border-l-orange-500";
  if (summary.medium > 0) return "border-l-4 border-l-yellow-500";
  return "border-l-4 border-l-emerald-500";
}

// --- File List ---

let _searchTimeout = null;

export async function loadFiles() {
  const container = document.getElementById("filesContent");
  if (!container) return;

  // Wire up search input (once)
  const searchInput = document.getElementById("filesSearch");
  if (searchInput && !searchInput._wired) {
    searchInput._wired = true;
    searchInput.addEventListener("input", () => {
      clearTimeout(_searchTimeout);
      _searchTimeout = setTimeout(() => {
        state.filesSearch = searchInput.value.trim();
        state.filesPage.offset = 0;
        loadFiles();
      }, 300);
    });
  }

  // Wire up critical-only checkbox (once)
  const critBox = document.getElementById("filesCriticalOnly");
  if (critBox && !critBox._wired) {
    critBox._wired = true;
    critBox.addEventListener("change", () => {
      state.filesCriticalOnly = critBox.checked;
      state.filesPage.offset = 0;
      loadFiles();
    });
  }

  container.innerHTML = '<div style="color:var(--text-muted)">Loading...</div>';

  try {
    const params = {
      limit: state.filesPage.limit,
      offset: state.filesPage.offset,
      sort: state.filesSort,
    };
    if (state.filesSearch) params.path_prefix = state.filesSearch;
    if (state.filesCriticalOnly) params.min_findings = 1;

    const data = await fetchFiles(params);
    if (!data) {
      container.innerHTML = '<div class="text-red-400">Failed to load files.</div>';
      return;
    }

    state.filesData = data;

    if (!data.results.length) {
      container.innerHTML =
        '<div class="p-6 text-center" style="color:var(--text-muted)">' +
        '<div class="font-medium mb-2" style="color:var(--text-primary)">No files tracked yet</div>' +
        "<div>Ingest scan results via POST /api/v1/scan-results to start tracking files.</div></div>";
      return;
    }

    const sortArrow = (col) =>
      state.filesSort === col
        ? ' <span style="color:var(--accent)">&#9660;</span>'
        : "";

    const headerCols = [
      { key: "path", label: "Path", cls: "text-left" },
      { key: "language", label: "Lang", cls: "text-left" },
      { key: null, label: "Critical", cls: "text-center" },
      { key: null, label: "High", cls: "text-center" },
      { key: null, label: "Medium", cls: "text-center" },
      { key: null, label: "Low", cls: "text-center" },
      { key: null, label: "Issues", cls: "text-center" },
      { key: "updated_at", label: "Last Update", cls: "text-right" },
    ];

    const headHtml = headerCols
      .map((h) => {
        const sortAttr = h.key
          ? ` cursor-pointer" onclick="sortFiles('${h.key}')" role="button" tabindex="0"`
          : '"';
        return `<th class="${h.cls} py-2 px-3 font-medium${sortAttr} style="color:var(--text-muted)">${h.label}${h.key ? sortArrow(h.key) : ""}</th>`;
      })
      .join("");

    const rowsHtml = data.results
      .map((f) => {
        const s = f.summary || {};
        const border = healthBorderClass(s);
        const assocCount = f.associations_count || 0;
        const updated = f.updated_at
          ? new Date(f.updated_at).toLocaleDateString()
          : "\u2014";
        return (
          `<tr class="bg-overlay-hover cursor-pointer ${border}" onclick="openFileDetail('${escHtml(f.id)}')" role="button" tabindex="0">` +
          `<td class="py-2 px-3 text-accent truncate max-w-xs" title="${escHtml(f.path)}">${escHtml(f.path)}</td>` +
          `<td class="py-2 px-3">${escHtml(f.language || "\u2014")}</td>` +
          `<td class="py-2 px-3 text-center">${severityBadge("critical", s.critical)}</td>` +
          `<td class="py-2 px-3 text-center">${severityBadge("high", s.high)}</td>` +
          `<td class="py-2 px-3 text-center">${severityBadge("medium", s.medium)}</td>` +
          `<td class="py-2 px-3 text-center">${severityBadge("low", s.low)}</td>` +
          `<td class="py-2 px-3 text-center">${assocCount || "\u2014"}</td>` +
          `<td class="py-2 px-3 text-right" style="color:var(--text-muted)">${updated}</td>` +
          "</tr>"
        );
      })
      .join("");

    const paginationHtml = buildPagination(data);

    container.innerHTML =
      '<div class="rounded overflow-hidden" style="background:var(--surface-raised);border:1px solid var(--border-default)">' +
      '<table class="text-xs w-full">' +
      "<thead><tr>" +
      headHtml +
      "</tr></thead>" +
      "<tbody>" +
      rowsHtml +
      "</tbody>" +
      "</table></div>" +
      paginationHtml;
  } catch (_e) {
    container.innerHTML = '<div class="text-red-400">Failed to load files.</div>';
  }
}

function buildPagination(data) {
  const { total, limit, offset, has_more } = data;
  const page = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  if (totalPages <= 1) return "";

  return (
    '<div class="flex items-center justify-between mt-3">' +
    `<span class="text-xs" style="color:var(--text-muted)">${total} files</span>` +
    '<div class="flex gap-2">' +
    `<button onclick="filesPagePrev()" class="text-xs px-2 py-1 rounded bg-overlay bg-overlay-hover${offset === 0 ? " opacity-30 pointer-events-none" : ""}" ${offset === 0 ? "disabled" : ""}>&#8592; Prev</button>` +
    `<span class="text-xs py-1" style="color:var(--text-secondary)">Page ${page} of ${totalPages}</span>` +
    `<button onclick="filesPageNext()" class="text-xs px-2 py-1 rounded bg-overlay bg-overlay-hover${!has_more ? " opacity-30 pointer-events-none" : ""}" ${!has_more ? "disabled" : ""}>Next &#8594;</button>` +
    "</div></div>"
  );
}

export function sortFiles(column) {
  state.filesSort = column;
  state.filesPage.offset = 0;
  loadFiles();
}

export function filesPagePrev() {
  state.filesPage.offset = Math.max(
    0,
    state.filesPage.offset - state.filesPage.limit,
  );
  loadFiles();
}

export function filesPageNext() {
  if (state.filesData?.has_more) {
    state.filesPage.offset += state.filesPage.limit;
    loadFiles();
  }
}

// --- File Detail (in detail panel) ---

export async function openFileDetail(fileId) {
  state.selectedFile = fileId;
  state.fileDetailTab = "findings";

  const panel = document.getElementById("detailPanel");
  const content = document.getElementById("detailContent");

  content.innerHTML =
    '<div class="text-xs" style="color:var(--text-muted)">Loading...</div>';
  panel.classList.remove("translate-x-full");

  try {
    const data = await fetchFileDetail(fileId);
    if (!data) {
      content.innerHTML = '<div class="text-red-400">File not found.</div>';
      return;
    }

    state.fileDetailData = data;
    renderFileDetail(data);
  } catch (_e) {
    content.innerHTML = '<div class="text-red-400">Failed to load file details.</div>';
  }
}

function renderFileDetail(data) {
  const content = document.getElementById("detailContent");
  const f = data.file;
  const s = data.summary || {};
  const assocs = data.associations || [];

  // Header
  let html =
    '<div class="flex items-center gap-2 mb-4">' +
    '<button onclick="closeFileDetail()" class="text-xs bg-overlay px-2 py-1 rounded bg-overlay-hover" title="Close">&times;</button>' +
    `<span class="text-sm font-semibold truncate" style="color:var(--text-primary)" title="${escHtml(f.path)}">${escHtml(f.path)}</span>` +
    "</div>";

  // Metadata row
  html +=
    '<div class="flex flex-wrap gap-3 mb-4 text-xs" style="color:var(--text-muted)">' +
    (f.language
      ? `<span>Language: <b style="color:var(--text-primary)">${escHtml(f.language)}</b></span>`
      : "") +
    (f.file_type
      ? `<span>Type: <b style="color:var(--text-primary)">${escHtml(f.file_type)}</b></span>`
      : "") +
    (f.first_seen
      ? `<span>First seen: ${new Date(f.first_seen).toLocaleDateString()}</span>`
      : "") +
    (f.updated_at
      ? `<span>Updated: ${new Date(f.updated_at).toLocaleDateString()}</span>`
      : "") +
    "</div>";

  // Summary bar
  html +=
    '<div class="flex gap-2 mb-4">' +
    severityBadge("critical", s.critical) +
    severityBadge("high", s.high) +
    severityBadge("medium", s.medium) +
    severityBadge("low", s.low) +
    severityBadge("info", s.info) +
    "</div>";

  // Tab buttons
  const findingsActive = state.fileDetailTab === "findings";
  const tabActive =
    "px-3 py-1 rounded text-xs font-medium bg-accent text-primary";
  const tabInactive =
    "px-3 py-1 rounded text-xs font-medium bg-overlay text-secondary bg-overlay-hover";

  html +=
    '<div class="flex gap-1 mb-4">' +
    `<button onclick="switchFileTab('findings')" class="${findingsActive ? tabActive : tabInactive}">Findings</button>` +
    `<button onclick="switchFileTab('timeline')" class="${!findingsActive ? tabActive : tabInactive}">Timeline</button>` +
    "</div>";

  // Tab content placeholder
  html += '<div id="fileTabContent"></div>';

  // Associated issues (always visible below tabs)
  if (assocs.length) {
    html +=
      '<div class="mt-4 pt-4" style="border-top:1px solid var(--border-default)">' +
      '<div class="text-xs font-medium mb-2" style="color:var(--text-secondary)">Associated Issues</div>';
    for (const a of assocs) {
      const statusColor =
        a.issue_status === "closed" || a.issue_status === "done"
          ? "var(--status-done)"
          : "var(--status-wip)";
      html +=
        `<div class="flex items-center gap-2 py-1 cursor-pointer bg-overlay-hover rounded px-2" onclick="openDetail('${escHtml(a.issue_id)}')" role="button" tabindex="0">` +
        `<span class="w-2 h-2 rounded-full" style="background:${statusColor}"></span>` +
        `<span class="text-xs truncate" style="color:var(--text-primary)">${escHtml(a.issue_title || a.issue_id)}</span>` +
        `<span class="text-xs" style="color:var(--text-muted)">${escHtml(a.assoc_type)}</span>` +
        "</div>";
    }
    html += "</div>";
  }

  // Link to Issue button
  html +=
    '<div class="mt-4 pt-3" style="border-top:1px solid var(--border-default)">' +
    `<button onclick="showLinkIssueModal('${escHtml(f.id)}')" class="text-xs bg-overlay px-3 py-1 rounded bg-overlay-hover" style="color:var(--text-primary)">Link to Issue</button>` +
    "</div>";

  content.innerHTML = html;

  // Load initial tab content
  if (findingsActive) loadFindingsTab(f.id);
  else loadTimelineTab(f.id);
}

// --- Findings Tab ---

async function loadFindingsTab(fileId, offset) {
  const container = document.getElementById("fileTabContent");
  if (!container) return;
  container.innerHTML =
    '<div style="color:var(--text-muted)">Loading findings...</div>';

  try {
    const data = await fetchFileFindings(fileId, {
      limit: 20,
      offset: offset || 0,
      sort: "severity",
    });
    if (!data) {
      container.innerHTML =
        '<div class="text-red-400">Failed to load findings.</div>';
      return;
    }

    if (!data.results.length) {
      container.innerHTML =
        '<div style="color:var(--text-muted)">No findings for this file.</div>';
      return;
    }

    let html = "";
    for (const f of data.results) {
      const c = SEVERITY_COLORS[f.severity] || SEVERITY_COLORS.info;
      const lines = f.line_start
        ? f.line_end && f.line_end !== f.line_start
          ? `L${f.line_start}-${f.line_end}`
          : `L${f.line_start}`
        : "";
      html +=
        '<details class="rounded mb-1" style="background:var(--surface-overlay);border:1px solid var(--border-default)">' +
        '<summary class="flex items-center gap-2 px-3 py-2 cursor-pointer text-xs bg-overlay-hover">' +
        `<span class="px-1.5 py-0.5 rounded ${c.bg} ${c.text}" style="border:1px solid;${c.border}">${escHtml(f.severity)}</span>` +
        `<span style="color:var(--text-primary)" class="truncate flex-1">${escHtml(f.rule_id)}</span>` +
        (lines ? `<span style="color:var(--text-muted)">${lines}</span>` : "") +
        `<span style="color:var(--text-muted)">seen:${f.seen_count || 1}</span>` +
        "</summary>" +
        '<div class="px-3 py-2 text-xs" style="color:var(--text-secondary)">' +
        `<div class="mb-1">${escHtml(f.message)}</div>` +
        `<div style="color:var(--text-muted)">Source: ${escHtml(f.scan_source || "\u2014")} | Status: ${escHtml(f.status)}</div>` +
        (f.first_seen
          ? `<div style="color:var(--text-muted)">First seen: ${new Date(f.first_seen).toLocaleDateString()}</div>`
          : "") +
        "</div></details>";
    }

    // Pagination for findings
    if (data.has_more) {
      const nextOffset = (offset || 0) + 20;
      html += `<button onclick="loadMoreFindings('${escHtml(fileId)}', ${nextOffset})" class="text-xs mt-2 px-3 py-1 rounded bg-overlay bg-overlay-hover" style="color:var(--accent)">Load more...</button>`;
    }

    container.innerHTML = html;
  } catch (_e) {
    container.innerHTML =
      '<div class="text-red-400">Failed to load findings.</div>';
  }
}

export function loadMoreFindings(fileId, offset) {
  loadFindingsTab(fileId, offset);
}

// --- Timeline Tab ---

async function loadTimelineTab(fileId, offset) {
  const container = document.getElementById("fileTabContent");
  if (!container) return;
  container.innerHTML =
    '<div style="color:var(--text-muted)">Loading timeline...</div>';

  try {
    const data = await fetchFileTimeline(fileId, {
      limit: 20,
      offset: offset || 0,
    });
    if (!data) {
      container.innerHTML =
        '<div class="text-red-400">Failed to load timeline.</div>';
      return;
    }

    if (!data.results.length) {
      container.innerHTML =
        '<div style="color:var(--text-muted)">No events for this file yet.</div>';
      return;
    }

    // Filter pills
    let html =
      '<div class="flex gap-1 mb-3">' +
      '<button onclick="filterTimeline(\'all\')" class="text-xs px-2 py-1 rounded bg-accent text-primary" id="tlFilterAll">All</button>' +
      '<button onclick="filterTimeline(\'finding\')" class="text-xs px-2 py-1 rounded bg-overlay text-secondary bg-overlay-hover" id="tlFilterFinding">Findings</button>' +
      '<button onclick="filterTimeline(\'association\')" class="text-xs px-2 py-1 rounded bg-overlay text-secondary bg-overlay-hover" id="tlFilterAssoc">Associations</button>' +
      "</div>";

    html += '<div id="timelineEvents">';
    html += renderTimelineEvents(data.results);
    html += "</div>";

    if (data.has_more) {
      const nextOffset = (offset || 0) + 20;
      html += `<button onclick="loadMoreTimeline('${escHtml(fileId)}', ${nextOffset})" class="text-xs mt-2 px-3 py-1 rounded bg-overlay bg-overlay-hover" style="color:var(--accent)">Load more...</button>`;
    }

    container.innerHTML = html;
    // Store events for client-side filtering
    window._timelineEvents = data.results;
  } catch (_e) {
    container.innerHTML =
      '<div class="text-red-400">Failed to load timeline.</div>';
  }
}

function renderTimelineEvents(events) {
  let html = "";
  for (const ev of events) {
    const dotColor =
      ev.type === "finding_created"
        ? "#EF4444"
        : ev.type === "finding_updated"
          ? "#3B82F6"
          : "#10B981";
    const time = ev.timestamp
      ? new Date(ev.timestamp).toLocaleString()
      : "";
    const evData = ev.data || {};

    let detail = "";
    if (ev.type === "finding_created") {
      const sev = evData.severity || "info";
      const c = SEVERITY_COLORS[sev] || SEVERITY_COLORS.info;
      detail = `<span class="px-1 py-0.5 rounded ${c.bg} ${c.text}" style="border:1px solid;${c.border}">${escHtml(sev)}</span> ${escHtml(evData.rule_id || "")} \u2014 ${escHtml(evData.message || "New finding")}`;
    } else if (ev.type === "finding_updated") {
      detail = `${escHtml(evData.rule_id || "Finding")} status: ${escHtml(evData.old_status || "?")} \u2192 ${escHtml(evData.new_status || evData.status || "?")}`;
    } else if (ev.type === "association_created") {
      detail = `Linked to issue ${escHtml(evData.issue_id || "")} (${escHtml(evData.assoc_type || "")})`;
    } else {
      detail = escHtml(ev.type);
    }

    html +=
      '<div class="flex gap-3 mb-2 timeline-event" data-type="' +
      escHtml(ev.type) +
      '">' +
      '<div class="flex flex-col items-center">' +
      `<div class="w-2.5 h-2.5 rounded-full mt-1 shrink-0" style="background:${dotColor}"></div>` +
      '<div class="w-px flex-1" style="background:var(--border-default)"></div>' +
      "</div>" +
      '<div class="flex-1 pb-3">' +
      `<div class="text-xs" style="color:var(--text-muted)">${time}</div>` +
      `<div class="text-xs mt-0.5" style="color:var(--text-primary)">${detail}</div>` +
      "</div></div>";
  }
  return html;
}

export function filterTimeline(type) {
  const events = window._timelineEvents || [];
  const filtered =
    type === "all"
      ? events
      : events.filter((e) => e.type.startsWith(type));
  const container = document.getElementById("timelineEvents");
  if (container) container.innerHTML = renderTimelineEvents(filtered);

  // Update active pill
  const pills = ["tlFilterAll", "tlFilterFinding", "tlFilterAssoc"];
  const activeMap = {
    all: "tlFilterAll",
    finding: "tlFilterFinding",
    association: "tlFilterAssoc",
  };
  for (const id of pills) {
    const el = document.getElementById(id);
    if (el) {
      el.className =
        id === activeMap[type]
          ? "text-xs px-2 py-1 rounded bg-accent text-primary"
          : "text-xs px-2 py-1 rounded bg-overlay text-secondary bg-overlay-hover";
    }
  }
}

export function loadMoreTimeline(fileId, offset) {
  loadTimelineTab(fileId, offset);
}

// --- Tab switching ---

export function switchFileTab(tab) {
  state.fileDetailTab = tab;
  if (state.fileDetailData) renderFileDetail(state.fileDetailData);
}

// --- Close file detail ---

export function closeFileDetail() {
  state.selectedFile = null;
  state.fileDetailData = null;
  const panel = document.getElementById("detailPanel");
  if (panel) panel.classList.add("translate-x-full");
}

// --- Link to Issue modal ---

export function showLinkIssueModal(fileId) {
  const modal = document.createElement("div");
  modal.id = "linkIssueModal";
  modal.className =
    "fixed inset-0 bg-black/50 flex items-center justify-center z-50";
  modal.onclick = (ev) => {
    if (ev.target === modal) modal.remove();
  };
  modal.innerHTML =
    '<div class="rounded-lg p-4 w-80 shadow-xl" style="background:var(--surface-raised);border:1px solid var(--border-strong)">' +
    '<div class="text-sm mb-3" style="color:var(--text-primary)">Link File to Issue</div>' +
    '<div class="mb-2"><label class="text-xs block mb-1" style="color:var(--text-muted)">Issue ID</label>' +
    '<input id="linkIssueId" type="text" class="w-full bg-overlay text-primary text-xs rounded px-3 py-1 border border-strong focus:outline-none focus-accent" placeholder="e.g. filigree-abc123"></div>' +
    '<div class="mb-3"><label class="text-xs block mb-1" style="color:var(--text-muted)">Association Type</label>' +
    '<select id="linkAssocType" class="w-full bg-overlay text-primary text-xs rounded px-2 py-1 border border-strong">' +
    '<option value="scan_finding">scan_finding</option>' +
    '<option value="bug_in">bug_in</option>' +
    '<option value="task_for">task_for</option>' +
    '<option value="mentioned_in">mentioned_in</option>' +
    "</select></div>" +
    `<button onclick="submitLinkIssue('${escHtml(fileId)}')" class="text-xs bg-accent text-white px-3 py-1 rounded bg-accent-hover">Link</button>` +
    ' <button onclick="document.getElementById(\'linkIssueModal\').remove()" class="text-xs text-muted text-primary-hover">Cancel</button>' +
    "</div>";
  document.body.appendChild(modal);
  document.getElementById("linkIssueId").focus();
}

export async function submitLinkIssue(fileId) {
  const issueId = document.getElementById("linkIssueId").value.trim();
  const assocType = document.getElementById("linkAssocType").value;
  if (!issueId) return;

  const result = await postFileAssociation(fileId, {
    issue_id: issueId,
    assoc_type: assocType,
  });
  const modal = document.getElementById("linkIssueModal");
  if (modal) modal.remove();

  if (result.ok) {
    showToast("Association created", "success");
    openFileDetail(fileId); // Refresh
  } else {
    showToast(result.error || "Failed to create association", "error");
  }
}
