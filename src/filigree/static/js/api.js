// ---------------------------------------------------------------------------
// Pure API client — all fetch calls to the Filigree backend.
// Returns data or { ok: false, error } objects. No DOM manipulation.
// ---------------------------------------------------------------------------

import { state } from "./state.js";

function apiUrl(path) {
  return state.API_BASE + path;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

/** Extract a human-readable message from structured or legacy error bodies. */
function extractError(body, fallback) {
  const e = body?.error;
  if (e && typeof e === "object") return e.message || fallback;
  return e || fallback;
}

// --- Read operations (return data or null) ---

export async function fetchIssues() {
  const resp = await fetch(apiUrl("/issues"));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchDeps() {
  const resp = await fetch(apiUrl("/dependencies"));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchStats() {
  const resp = await fetch(apiUrl("/stats"));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchAllData() {
  const results = await Promise.all([
    fetch(apiUrl("/issues")),
    fetch(apiUrl("/dependencies")),
    fetch(apiUrl("/stats")),
  ]);
  if (!results[0].ok || !results[1].ok || !results[2].ok) {
    return null;
  }
  return {
    issues: await results[0].json(),
    deps: await results[1].json(),
    stats: await results[2].json(),
  };
}

export async function fetchIssueDetail(issueId) {
  const resp = await fetch(apiUrl(`/issue/${issueId}`));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchTransitions(issueId) {
  try {
    const resp = await fetch(apiUrl(`/issue/${issueId}/transitions`));
    if (resp.ok) return resp.json();
  } catch (_e) {
    /* best-effort */
  }
  return [];
}

export async function fetchGraph() {
  const resp = await fetch(apiUrl("/graph"));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchMetrics(days) {
  const resp = await fetch(apiUrl(`/metrics?days=${days}`));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchActivity(limit, since) {
  let url = apiUrl("/activity");
  const params = [];
  if (limit) params.push(`limit=${limit}`);
  if (since) params.push(`since=${encodeURIComponent(since)}`);
  if (params.length) url += `?${params.join("&")}`;
  const resp = await fetch(url);
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchTypeInfo(typeName) {
  const resp = await fetch(apiUrl(`/type/${encodeURIComponent(typeName)}`));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchTypes() {
  const resp = await fetch(apiUrl("/types"));
  if (!resp.ok) return [];
  return resp.json();
}

export async function fetchPlan(milestoneId) {
  const resp = await fetch(apiUrl(`/plan/${milestoneId}`));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchCriticalPath() {
  const resp = await fetch(apiUrl("/critical-path"));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchSearch(query, limit) {
  let url = apiUrl(`/search?q=${encodeURIComponent(query)}`);
  if (limit) url += `&limit=${limit}`;
  const resp = await fetch(url);
  if (!resp.ok) return { results: [], total: 0 };
  return resp.json();
}

export async function fetchProjects(ttl) {
  const resp = await fetch(`/api/projects${ttl ? `?ttl=${ttl}` : ""}`);
  if (!resp.ok) return [];
  return resp.json();
}

// --- Write operations (return { ok, data?, error? }) ---

export async function patchIssue(issueId, body) {
  try {
    const resp = await fetch(apiUrl(`/issue/${issueId}`), {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Update failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postCloseIssue(issueId, reason) {
  try {
    const resp = await fetch(apiUrl(`/issue/${issueId}/close`), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ reason: reason || "" }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Close failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postReopenIssue(issueId) {
  try {
    const resp = await fetch(apiUrl(`/issue/${issueId}/reopen`), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({}),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Reopen failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postClaimIssue(issueId, assignee) {
  try {
    const resp = await fetch(apiUrl(`/issue/${issueId}/claim`), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ assignee }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Claim failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postReleaseIssue(issueId) {
  try {
    const resp = await fetch(apiUrl(`/issue/${issueId}/release`), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({}),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Release failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postAddDependency(issueId, dependsOnId) {
  try {
    const resp = await fetch(apiUrl(`/issue/${issueId}/dependencies`), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ depends_on: dependsOnId }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Add failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function deleteIssueDep(issueId, depId) {
  try {
    const resp = await fetch(apiUrl(`/issue/${issueId}/dependencies/${depId}`), {
      method: "DELETE",
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Remove failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postComment(issueId, text) {
  try {
    const resp = await fetch(apiUrl(`/issue/${issueId}/comments`), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ text }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Comment failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postCreateIssue(body) {
  try {
    const resp = await fetch(apiUrl("/issues"), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Create failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postBatchUpdate(issueIds, fields) {
  try {
    const resp = await fetch(apiUrl("/batch/update"), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ issue_ids: issueIds, ...fields }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Batch update failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postBatchClose(issueIds, reason, actor) {
  try {
    const body = { issue_ids: issueIds };
    if (reason) body.reason = reason;
    if (actor) body.actor = actor;
    const resp = await fetch(apiUrl("/batch/close"), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Batch close failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}

export async function postReload() {
  try {
    const resp = await fetch("/api/reload", { method: "POST" });
    return resp.json();
  } catch (_e) {
    return { ok: false };
  }
}

// --- File & Findings API ---

export async function fetchFiles(params) {
  const qs = params ? "?" + new URLSearchParams(params) : "";
  const resp = await fetch(apiUrl("/files" + qs));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchFileDetail(fileId) {
  const resp = await fetch(apiUrl(`/files/${encodeURIComponent(fileId)}`));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchFileFindings(fileId, params) {
  const qs = params ? "?" + new URLSearchParams(params) : "";
  const resp = await fetch(apiUrl(`/files/${encodeURIComponent(fileId)}/findings` + qs));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchFileTimeline(fileId, params) {
  const qs = params ? "?" + new URLSearchParams(params) : "";
  const resp = await fetch(apiUrl(`/files/${encodeURIComponent(fileId)}/timeline` + qs));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchHotspots(limit) {
  const qs = limit ? `?limit=${limit}` : "";
  const resp = await fetch(apiUrl("/files/hotspots" + qs));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchFileStats() {
  const resp = await fetch(apiUrl("/files/stats"));
  if (!resp.ok) return null;
  return resp.json();
}

export async function fetchFileSchema() {
  const resp = await fetch(apiUrl("/files/_schema"));
  if (!resp.ok) return null;
  return resp.json();
}

export async function postFileAssociation(fileId, body) {
  try {
    const resp = await fetch(apiUrl(`/files/${encodeURIComponent(fileId)}/associations`), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, error: extractError(err, "Association failed") };
    }
    return { ok: true, data: await resp.json() };
  } catch (_e) {
    return { ok: false, error: "Network error" };
  }
}
