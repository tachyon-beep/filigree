// ---------------------------------------------------------------------------
// Code Health view — hotspots, severity donut, scan coverage, recent scans.
// ---------------------------------------------------------------------------

import { fetchFiles, fetchHotspots } from "../api.js";
import { SEVERITY_COLORS, state } from "../state.js";
import { escHtml } from "../ui.js";

// --- Callbacks ---
export const callbacks = {};

// --- Main loader ---

export async function loadHealth() {
  const container = document.getElementById("healthContent");
  if (!container) return;
  container.innerHTML = '<div style="color:var(--text-muted)">Loading...</div>';

  try {
    // Fetch hotspots and file list in parallel
    const [hotspots, fileData] = await Promise.all([
      fetchHotspots(10),
      fetchFiles({ limit: 1, offset: 0 }),
    ]);

    if (!hotspots && !fileData) {
      container.innerHTML =
        '<div class="p-6 text-center" style="color:var(--text-muted)">' +
        '<div class="font-medium mb-2" style="color:var(--text-primary)">No file data yet</div>' +
        "<div>Ingest scan results to see code health metrics.</div></div>";
      return;
    }

    state.hotspots = hotspots;

    // Compute aggregate severity counts from hotspots
    const agg = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
    if (hotspots) {
      for (const h of hotspots) {
        const b = h.findings_breakdown || {};
        agg.critical += b.critical || 0;
        agg.high += b.high || 0;
        agg.medium += b.medium || 0;
        agg.low += b.low || 0;
        agg.info += b.info || 0;
      }
    }

    const totalFiles = fileData?.total || 0;
    const filesWithFindings = hotspots?.length || 0;

    // Build 2x2 grid
    container.innerHTML =
      '<div class="grid grid-cols-1 md:grid-cols-2 gap-4">' +
      renderHotspotsWidget(hotspots) +
      renderDonutWidget(agg) +
      renderCoverageWidget(filesWithFindings, totalFiles) +
      renderRecentScansWidget() +
      "</div>";
  } catch (_e) {
    container.innerHTML = '<div class="text-red-400">Failed to load health data.</div>';
  }
}

// --- Widget 1: Top 10 Hotspot Files ---

function renderHotspotsWidget(hotspots) {
  if (!hotspots || !hotspots.length) {
    return (
      '<div class="rounded p-4" style="background:var(--surface-raised);border:1px solid var(--border-default)">' +
      '<div class="text-xs font-medium mb-3" style="color:var(--text-secondary)">Top Hotspot Files</div>' +
      '<div style="color:var(--text-muted)" class="text-xs">No hotspots found.</div></div>'
    );
  }

  const maxScore = hotspots[0]?.score || 1;

  const rows = hotspots
    .map((h) => {
      const f = h.file || {};
      const b = h.findings_breakdown || {};
      const total =
        (b.critical || 0) +
        (b.high || 0) +
        (b.medium || 0) +
        (b.low || 0) +
        (b.info || 0);
      if (total === 0) return "";

      // Stacked bar segments
      const segments = ["critical", "high", "medium", "low", "info"]
        .filter((s) => b[s])
        .map((s) => {
          const pct = ((b[s] / total) * 100).toFixed(1);
          return `<div style="width:${pct}%;background:${SEVERITY_COLORS[s].hex}" class="h-full"></div>`;
        })
        .join("");

      const barWidth = ((h.score / maxScore) * 100).toFixed(1);

      return (
        `<div class="flex items-center gap-2 mb-2 cursor-pointer bg-overlay-hover rounded px-2 py-1" onclick="switchView('files');setTimeout(()=>openFileDetail('${escHtml(f.id)}'),100)" role="button" tabindex="0">` +
        `<span class="text-xs truncate w-48" style="color:var(--text-primary)" title="${escHtml(f.path)}">${escHtml(f.path)}</span>` +
        `<div class="flex-1 h-3 rounded overflow-hidden flex" style="background:var(--surface-base);max-width:${barWidth}%">` +
        segments +
        "</div>" +
        `<span class="text-xs w-8 text-right" style="color:var(--text-muted)">${escHtml(String(h.score))}</span>` +
        "</div>"
      );
    })
    .join("");

  return (
    '<div class="rounded p-4" style="background:var(--surface-raised);border:1px solid var(--border-default)">' +
    '<div class="text-xs font-medium mb-3" style="color:var(--text-secondary)">Top Hotspot Files</div>' +
    rows +
    "</div>"
  );
}

// --- Widget 2: Findings by Severity (CSS donut) ---

function renderDonutWidget(agg) {
  const total =
    agg.critical + agg.high + agg.medium + agg.low + agg.info;
  if (total === 0) {
    return (
      '<div class="rounded p-4" style="background:var(--surface-raised);border:1px solid var(--border-default)">' +
      '<div class="text-xs font-medium mb-3" style="color:var(--text-secondary)">Findings by Severity</div>' +
      '<div style="color:var(--text-muted)" class="text-xs">No findings to display.</div></div>'
    );
  }

  // Build conic-gradient segments
  const segments = [];
  let cumPct = 0;
  for (const sev of ["critical", "high", "medium", "low", "info"]) {
    if (agg[sev]) {
      const pct = (agg[sev] / total) * 100;
      segments.push(
        `${SEVERITY_COLORS[sev].hex} ${cumPct}% ${cumPct + pct}%`,
      );
      cumPct += pct;
    }
  }
  const gradient = `conic-gradient(${segments.join(", ")})`;

  const legend = ["critical", "high", "medium", "low", "info"]
    .filter((s) => agg[s])
    .map(
      (s) =>
        `<span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full" style="background:${SEVERITY_COLORS[s].hex}"></span>${s}: ${agg[s]}</span>`,
    )
    .join("");

  return (
    '<div class="rounded p-4" style="background:var(--surface-raised);border:1px solid var(--border-default)">' +
    '<div class="text-xs font-medium mb-3" style="color:var(--text-secondary)">Findings by Severity</div>' +
    '<div class="flex items-center gap-6">' +
    '<div class="relative" style="width:120px;height:120px">' +
    `<div style="width:100%;height:100%;border-radius:50%;background:${gradient}"></div>` +
    '<div class="absolute inset-0 flex items-center justify-center">' +
    '<div class="rounded-full flex items-center justify-center" style="width:64px;height:64px;background:var(--surface-raised)">' +
    `<span class="text-lg font-bold" style="color:var(--text-primary)">${total}</span>` +
    "</div></div></div>" +
    `<div class="flex flex-col gap-1 text-xs" style="color:var(--text-secondary)">${legend}</div>` +
    "</div></div>"
  );
}

// --- Widget 3: Scan Coverage ---

function renderCoverageWidget(withFindings, total) {
  const pct = total > 0 ? ((withFindings / total) * 100).toFixed(0) : 0;

  return (
    '<div class="rounded p-4" style="background:var(--surface-raised);border:1px solid var(--border-default)">' +
    '<div class="text-xs font-medium mb-3" style="color:var(--text-secondary)">Scan Coverage</div>' +
    `<div class="text-2xl font-bold mb-2" style="color:var(--accent)">${pct}%</div>` +
    '<div class="h-3 rounded overflow-hidden mb-2" style="background:var(--surface-base)">' +
    `<div class="h-full rounded" style="width:${pct}%;background:var(--accent)"></div>` +
    "</div>" +
    `<div class="text-xs" style="color:var(--text-muted)">${withFindings} files with findings out of ${total} tracked</div>` +
    "</div>"
  );
}

// --- Widget 4: Recent Scan Activity ---

function renderRecentScansWidget() {
  return (
    '<div class="rounded p-4" style="background:var(--surface-raised);border:1px solid var(--border-default)">' +
    '<div class="text-xs font-medium mb-3" style="color:var(--text-secondary)">Recent Scan Activity</div>' +
    '<div class="text-xs" style="color:var(--text-muted)">' +
    "<div class=\"mb-2\">Scan ingestion stats appear here after posting results.</div>" +
    '<div class="mb-1">Ingest scans via:</div>' +
    '<code class="block rounded px-2 py-1 text-xs" style="background:var(--surface-base);color:var(--text-secondary)">POST /api/v1/scan-results</code>' +
    '<div class="mt-2">Returns: files_created, files_updated, findings_created, findings_updated</div>' +
    "</div></div>"
  );
}
