// ---------------------------------------------------------------------------
// Shared analytics — health score + impact score computation.
// Extracted from graph.js to decouple graph visualization from scoring.
// ---------------------------------------------------------------------------

import { state } from "./state.js";

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
  let balanceScore = 25;
  if (maxWip > 5) balanceScore = 10;
  else if (maxWip > 3) balanceScore = 18;
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

  state._healthBreakdown = {
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
