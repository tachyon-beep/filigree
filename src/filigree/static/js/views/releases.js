// ---------------------------------------------------------------------------
// Releases view — release roadmap, progress tracking, and tree expansion.
// ---------------------------------------------------------------------------

import { fetchReleases, fetchReleaseTree } from "../api.js";
import { escHtml, escJsSingle } from "../ui.js";

// --- Module-level state ---

let expandedReleaseIds = new Set();
let releaseTreeCache = new Map();
let collapsedNodeIds = new Set();
let showReleased = false;
let loadingReleaseIds = new Set();
let errorReleaseIds = new Set();

// --- Color helpers ---

function statusBorderColor(status) {
  switch (status) {
    case "planning":
      return "var(--accent)";
    case "development":
    case "frozen":
    case "testing":
    case "staged":
      return "#F59E0B";
    case "released":
      return "#10B981";
    case "cancelled":
    case "rolled_back":
      return "#EF4444";
    default:
      return "var(--border-default)";
  }
}

function statusBadge(status) {
  let style;
  switch (status) {
    case "planning":
      style = "background:var(--accent);color:var(--surface-base)";
      break;
    case "development":
    case "frozen":
    case "testing":
    case "staged":
      style = "background:#F59E0B;color:#000";
      break;
    case "released":
      style = "background:#10B981;color:#fff";
      break;
    case "cancelled":
    case "rolled_back":
      style = "background:#EF4444;color:#fff";
      break;
    default:
      style = "background:var(--surface-overlay);color:var(--text-secondary)";
      break;
  }
  return '<span class="text-xs rounded px-1.5 py-0.5" style="' + style + '">' + escHtml(status || 'open') + '</span>';
}

// --- Progress bar rendering ---

function renderProgressBar(pct, name) {
  const safeName = escHtml(name);
  const clampedPct = Math.max(0, Math.min(100, pct));
  return (
    '<div role="progressbar" aria-valuenow="' + clampedPct + '" aria-valuemin="0" aria-valuemax="100" ' +
    'aria-label="' + safeName + ' progress: ' + clampedPct + '%" ' +
    'style="background:var(--surface-base);height:8px;border-radius:4px;flex:1;min-width:60px;max-width:120px">' +
    '<div style="width:' + clampedPct + '%;height:100%;background:var(--accent);border-radius:4px"></div>' +
    '</div>'
  );
}

// --- Tree rendering ---

function renderTreeNode(node, level, releaseId) {
  const maxLevel = Math.min(level, 3);
  const indent = maxLevel * 24; // 24px per level (ml-6 equivalent)
  const nodeId = node.id;
  const safeId = escJsSingle(nodeId);
  const isLeaf = !node.children || node.children.length === 0;
  const isCollapsed = collapsedNodeIds.has(nodeId);
  const hasChildren = !isLeaf;
  const pct = node.progress_pct != null ? node.progress_pct : 0;

  let html = '';
  html += '<li role="treeitem" aria-level="' + (level + 1) + '"';
  if (hasChildren) {
    html += ' aria-expanded="' + (!isCollapsed) + '"';
  }
  html += ' tabindex="-1"';
  html += ' data-node-id="' + escHtml(nodeId) + '"';
  html += ' data-release-id="' + escHtml(releaseId) + '"';
  html += ' data-has-children="' + hasChildren + '"';
  html += ' style="list-style:none;padding-left:' + indent + 'px;';
  if (level > 0) {
    html += 'border-left:1px solid var(--border-default);margin-left:' + ((maxLevel - 1) * 24) + 'px;';
  }
  html += '"';
  html += ' class="py-1 flex items-center gap-2">';

  // Toggle or leaf indicator
  if (hasChildren) {
    const arrow = isCollapsed ? '\u25B6' : '\u25BC';
    html += '<button class="text-xs flex items-center justify-center cursor-pointer" ' +
      'style="width:44px;height:44px;min-width:44px;min-height:44px;background:none;border:none;color:var(--text-secondary)" ' +
      'onclick="event.stopPropagation();window._toggleReleaseTreeNode(\'' + safeId + '\',\'' + escJsSingle(releaseId) + '\')" ' +
      'aria-label="' + (isCollapsed ? 'Expand' : 'Collapse') + ' ' + escHtml(node.title || nodeId) + '">' +
      arrow + '</button>';
  } else {
    // Leaf — status badge inline
    html += '<span style="width:44px;min-width:44px;display:inline-flex;align-items:center;justify-content:center">' +
      statusBadge(node.status || '') + '</span>';
  }

  // Title (clickable)
  html += '<span class="cursor-pointer hover:underline text-xs" style="color:var(--text-primary)" ' +
    'onclick="window.openDetail(\'' + safeId + '\')">' +
    escHtml(node.title || nodeId) + '</span>';

  // Progress bar for non-leaf nodes
  if (hasChildren) {
    html += ' ' + renderProgressBar(pct, node.title || nodeId);
    html += ' <span class="text-xs" style="color:var(--text-muted)">' + pct + '%</span>';
  }

  // Render children if expanded (INSIDE the li)
  if (hasChildren && !isCollapsed) {
    html += '<ul role="group">';
    for (const child of node.children) {
      html += renderTreeNode(child, level + 1, releaseId);
    }
    html += '</ul>';
  }

  html += '</li>';

  return html;
}

function collectTreeNodeIds(node, ids) {
  ids.add(node.id);
  if (node.children) {
    for (const child of node.children) {
      collectTreeNodeIds(child, ids);
    }
  }
}

function drainStaleCollapsedIds(tree) {
  const validIds = new Set();
  if (tree.children) {
    for (const child of tree.children) {
      collectTreeNodeIds(child, validIds);
    }
  }
  for (const id of collapsedNodeIds) {
    if (!validIds.has(id)) collapsedNodeIds.delete(id);
  }
}

function renderTree(tree, releaseId) {
  if (!tree || !tree.children || tree.children.length === 0) {
    return '<div class="text-xs py-2" style="color:var(--text-muted)">No child items.</div>';
  }

  let html = '';
  html += '<div class="flex items-center gap-2 mb-2">';
  html += '<button class="text-xs px-2 py-1 rounded bg-overlay bg-overlay-hover" ' +
    'style="min-height:36px" ' +
    'onclick="window._collapseAllReleaseTree(\'' + escJsSingle(releaseId) + '\')">Collapse all</button>';
  html += '</div>';

  html += '<ul role="tree" data-release-tree="' + escHtml(releaseId) + '">';
  for (const child of tree.children) {
    html += renderTreeNode(child, 0, releaseId);
  }
  html += '</ul>';

  return html;
}

// --- Keyboard navigation for tree ---

function setupTreeKeyboard(container) {
  const treeRoots = container.querySelectorAll('[role="tree"]');
  treeRoots.forEach((tree) => {
    tree.addEventListener("keydown", handleTreeKeydown);
  });
}

function getVisibleTreeItems(tree) {
  return Array.from(tree.querySelectorAll('[role="treeitem"]')).filter(
    (el) => el.offsetParent !== null
  );
}

function handleTreeKeydown(e) {
  const tree = e.currentTarget;
  const items = getVisibleTreeItems(tree);
  if (!items.length) return;

  const current = document.activeElement;
  let idx = items.indexOf(current);
  if (idx < 0) idx = 0;

  switch (e.key) {
    case "ArrowDown": {
      e.preventDefault();
      const next = Math.min(idx + 1, items.length - 1);
      setTreeFocus(items, next);
      break;
    }
    case "ArrowUp": {
      e.preventDefault();
      const prev = Math.max(idx - 1, 0);
      setTreeFocus(items, prev);
      break;
    }
    case "ArrowRight": {
      e.preventDefault();
      const item = items[idx];
      if (!item) break;
      const hasChildren = item.dataset.hasChildren === "true";
      const expanded = item.getAttribute("aria-expanded") === "true";
      if (hasChildren && !expanded) {
        // Expand
        const nodeId = item.dataset.nodeId;
        const releaseId = item.dataset.releaseId;
        if (nodeId && releaseId) {
          window._toggleReleaseTreeNode(nodeId, releaseId);
        }
      } else if (hasChildren && expanded) {
        // Move to first child
        const group = item.querySelector('[role="group"]');
        if (group) {
          const firstChild = group.querySelector('[role="treeitem"]');
          if (firstChild) {
            const newItems = getVisibleTreeItems(tree);
            const newIdx = newItems.indexOf(firstChild);
            if (newIdx >= 0) setTreeFocus(newItems, newIdx);
          }
        }
      }
      break;
    }
    case "ArrowLeft": {
      e.preventDefault();
      const item = items[idx];
      if (!item) break;
      const hasChildren = item.dataset.hasChildren === "true";
      const expanded = item.getAttribute("aria-expanded") === "true";
      if (hasChildren && expanded) {
        // Collapse
        const nodeId = item.dataset.nodeId;
        const releaseId = item.dataset.releaseId;
        if (nodeId && releaseId) {
          window._toggleReleaseTreeNode(nodeId, releaseId);
        }
      } else {
        // Go to parent
        const level = parseInt(item.getAttribute("aria-level"), 10);
        if (level > 1) {
          for (let i = idx - 1; i >= 0; i--) {
            const parentLevel = parseInt(items[i].getAttribute("aria-level"), 10);
            if (parentLevel < level) {
              setTreeFocus(items, i);
              break;
            }
          }
        }
      }
      break;
    }
    case "Home": {
      e.preventDefault();
      setTreeFocus(items, 0);
      break;
    }
    case "End": {
      e.preventDefault();
      setTreeFocus(items, items.length - 1);
      break;
    }
    case "Enter":
    case " ": {
      e.preventDefault();
      const item = items[idx];
      if (!item) break;
      const hasChildren = item.dataset.hasChildren === "true";
      if (hasChildren) {
        const nodeId = item.dataset.nodeId;
        const releaseId = item.dataset.releaseId;
        if (nodeId && releaseId) {
          window._toggleReleaseTreeNode(nodeId, releaseId);
        }
      } else {
        const nodeId = item.dataset.nodeId;
        if (nodeId) window.openDetail(nodeId);
      }
      break;
    }
  }
}

function setTreeFocus(items, idx) {
  items.forEach((el) => el.setAttribute("tabindex", "-1"));
  if (items[idx]) {
    items[idx].setAttribute("tabindex", "0");
    items[idx].focus();
  }
}

// --- Card rendering ---

function renderReleaseCard(release) {
  const borderColor = statusBorderColor(release.status);
  const isExpanded = expandedReleaseIds.has(release.id);
  const isLoading = loadingReleaseIds.has(release.id);
  const isBlocked = release.blocked_by && release.blocked_by.length > 0;
  const safeId = escJsSingle(release.id);
  const pct = release.progress_pct != null ? release.progress_pct : 0;
  const textColor = isBlocked ? "color:var(--text-muted)" : "color:var(--text-primary)";

  let html = '';
  html += '<div class="rounded mb-3" style="background:var(--surface-raised);border:1px solid var(--border-default);border-left:4px solid ' + borderColor + '" id="release-card-' + escHtml(release.id) + '">';
  html += '<div class="p-4">';

  // Header row: toggle + title + status badge
  html += '<div class="flex items-center gap-2 mb-2">';

  // Expand toggle
  const arrow = isExpanded ? '\u25BC' : '\u25B6';
  html += '<button class="text-xs flex items-center justify-center cursor-pointer" ' +
    'style="width:44px;height:44px;min-width:44px;min-height:44px;background:none;border:none;color:var(--text-secondary)" ' +
    'onclick="window._toggleReleaseExpand(\'' + safeId + '\')" ' +
    'aria-label="' + (isExpanded ? 'Collapse' : 'Expand') + ' release ' + escHtml(release.title || release.id) + '">' +
    arrow + '</button>';

  // Title (clickable)
  html += '<span class="cursor-pointer hover:underline text-sm font-medium flex-1" style="' + textColor + '" ' +
    'onclick="window.openDetail(\'' + safeId + '\')">' +
    escHtml(release.title || release.id) + '</span>';

  // Status badge
  html += statusBadge(release.status);

  // Blocked badge
  if (isBlocked) {
    html += ' <span class="text-xs rounded px-1.5 py-0.5 shrink-0" style="background:#EF4444;color:#fff">[blocked]</span>';
  }

  html += '</div>';

  // Stats row
  html += '<div class="flex flex-wrap gap-2 items-center text-xs" style="color:var(--text-muted)">';
  html += '<span>P' + (release.priority != null ? release.priority : '?') + '</span>';

  if (release.child_summary) {
    html += '<span>' + escHtml(release.child_summary) + '</span>';
  }

  html += renderProgressBar(pct, release.title || release.id);
  html += '<span>' + pct + '%</span>';
  html += '</div>';

  // Target date
  if (release.target_date) {
    html += '<div class="text-xs mt-1" style="color:var(--text-muted)">Target: ' + escHtml(release.target_date) + '</div>';
  }

  // Blocks / Blocked by links
  if (release.blocks && release.blocks.length > 0) {
    html += '<div class="text-xs mt-1" style="color:var(--text-muted)">Blocks: ';
    html += release.blocks.map((b) =>
      '<a href="#" class="hover:underline" style="color:var(--accent)" onclick="event.preventDefault();document.getElementById(\'release-card-' + escJsSingle(b) + '\')?.scrollIntoView({behavior:\'smooth\',block:\'center\'})">' + escHtml(b) + '</a>'
    ).join(', ');
    html += '</div>';
  }

  if (release.blocked_by && release.blocked_by.length > 0) {
    html += '<div class="text-xs mt-1" style="color:var(--text-muted)">Blocked by: ';
    html += release.blocked_by.map((b) =>
      '<a href="#" class="hover:underline" style="color:var(--accent)" onclick="event.preventDefault();document.getElementById(\'release-card-' + escJsSingle(b) + '\')?.scrollIntoView({behavior:\'smooth\',block:\'center\'})">' + escHtml(b) + '</a>'
    ).join(', ');
    html += '</div>';
  }

  // Expanded tree area
  if (isExpanded) {
    html += '<div class="mt-3 pt-3" style="border-top:1px solid var(--border-default)">';
    if (isLoading) {
      html += '<div class="text-xs py-2" style="color:var(--text-muted)">Loading tree...</div>';
    } else if (errorReleaseIds.has(release.id)) {
      html += '<div class="text-xs py-2 flex items-center gap-2" style="color:var(--text-muted)">' +
        'Failed to load release tree.' +
        ' <button class="text-xs px-2 py-1 rounded cursor-pointer" ' +
        'style="background:var(--surface-overlay);color:var(--accent);border:1px solid var(--border-default);min-height:28px" ' +
        'onclick="window._retryReleaseTree(\'' + safeId + '\')">Retry</button>' +
        '</div>';
    } else {
      const tree = releaseTreeCache.get(release.id);
      if (tree) {
        html += renderTree(tree, release.id);
      } else {
        html += '<div class="text-xs py-2" style="color:var(--text-muted)">No tree data available.</div>';
      }
    }
    html += '</div>';
  }

  html += '</div>';
  html += '</div>';

  return html;
}

// --- Expand/collapse handlers (exposed on window) ---

window._toggleReleaseExpand = async function (releaseId) {
  if (expandedReleaseIds.has(releaseId)) {
    expandedReleaseIds.delete(releaseId);
    loadReleases();
    return;
  }

  expandedReleaseIds.add(releaseId);
  loadingReleaseIds.add(releaseId);
  loadReleases(); // Re-render to show loading state

  try {
    const tree = await fetchReleaseTree(releaseId);
    if (tree) {
      releaseTreeCache.set(releaseId, tree);
      errorReleaseIds.delete(releaseId);
      drainStaleCollapsedIds(tree);
    }
  } catch (_e) {
    errorReleaseIds.add(releaseId);
  } finally {
    loadingReleaseIds.delete(releaseId);
    loadReleases();
  }
};

window._retryReleaseTree = async function (releaseId) {
  errorReleaseIds.delete(releaseId);
  loadingReleaseIds.add(releaseId);
  loadReleases(); // Re-render to show loading state

  try {
    const tree = await fetchReleaseTree(releaseId);
    if (tree) {
      releaseTreeCache.set(releaseId, tree);
      drainStaleCollapsedIds(tree);
    }
  } catch (_e) {
    errorReleaseIds.add(releaseId);
  } finally {
    loadingReleaseIds.delete(releaseId);
    loadReleases();
  }
};

window._toggleReleaseTreeNode = function (nodeId, releaseId) {
  if (collapsedNodeIds.has(nodeId)) {
    collapsedNodeIds.delete(nodeId);
  } else {
    collapsedNodeIds.add(nodeId);
  }
  loadReleases();
};

window._collapseAllReleaseTree = function (releaseId) {
  const tree = releaseTreeCache.get(releaseId);
  if (!tree || !tree.children) return;

  function collapseAll(node) {
    if (node.children && node.children.length > 0) {
      collapsedNodeIds.add(node.id);
      for (const child of node.children) {
        collapseAll(child);
      }
    }
  }

  for (const child of tree.children) {
    collapseAll(child);
  }
  loadReleases();
};

// --- Main loader ---

export async function loadReleases() {
  const container = document.getElementById("releasesContent");
  if (!container) return;

  // Sync checkbox state
  const checkbox = document.getElementById("showReleased");
  if (checkbox) {
    checkbox.checked = showReleased;
    // Wire up onchange (idempotent)
    checkbox.onchange = function () {
      showReleased = this.checked;
      loadReleases();
    };
  }

  // Save scroll position
  const scrollParent = container.closest(".overflow-y-auto");
  const scrollTop = scrollParent ? scrollParent.scrollTop : 0;

  // If we already have data in the cache for expanded releases, render immediately
  // but still fetch fresh data in the background
  const releases = await fetchReleases(showReleased);

  if (!releases) {
    container.innerHTML = '<div class="text-red-400">Failed to load releases.</div>';
    return;
  }

  if (!releases.length) {
    container.innerHTML =
      '<div class="p-6 text-center" style="color:var(--text-muted)">' +
      '<div class="font-medium mb-2" style="color:var(--text-primary)">No active releases.</div>' +
      '<div>Show completed releases to see release history.</div></div>';
    return;
  }

  // Re-fetch trees for expanded releases (in parallel)
  const expandedFetches = [];
  for (const id of expandedReleaseIds) {
    if (!loadingReleaseIds.has(id)) {
      expandedFetches.push(
        fetchReleaseTree(id).then((tree) => {
          if (tree) {
            releaseTreeCache.set(id, tree);
            drainStaleCollapsedIds(tree);
          }
        }).catch(() => { /* best-effort */ })
      );
    }
  }

  if (expandedFetches.length > 0) {
    await Promise.all(expandedFetches);
  }

  // Render cards
  let html = '';
  for (const release of releases) {
    html += renderReleaseCard(release);
  }

  container.innerHTML = html;

  // Restore scroll position
  if (scrollParent) {
    scrollParent.scrollTop = scrollTop;
  }

  // Set up keyboard navigation on any rendered trees
  setupTreeKeyboard(container);
}
