// ---------------------------------------------------------------------------
// Activity feed view — chronological event timeline.
// ---------------------------------------------------------------------------

import { fetchActivity } from "../api.js";
import { escHtml, escJsSingle } from "../ui.js";

/**
 * Fetch recent activity events and render them as a day-grouped timeline
 * inside #activityContent.
 */
export async function loadActivity() {
  const container = document.getElementById("activityContent");
  container.innerHTML = '<div style="color:var(--text-muted)">Loading...</div>';
  try {
    const events = await fetchActivity(50);
    if (!events || !events.length) {
      container.innerHTML =
        '<div class="p-6 text-center" style="color:var(--text-muted)">' +
        '<div class="font-medium mb-2" style="color:var(--text-primary)">No recent activity</div>' +
        "<div>Events appear here when issues are created, updated, or closed.</div></div>";
      return;
    }
    let lastDay = "";
    container.innerHTML = events
      .map((e) => {
        const time = e.created_at ? e.created_at.slice(5, 16) : "";
        const day = e.created_at ? e.created_at.slice(0, 10) : "";
        let separator = "";
        if (day && day !== lastDay) {
          lastDay = day;
          separator =
            '<div class="flex items-center gap-2 py-2 mt-2 first:mt-0">' +
            '<div class="flex-1" style="border-top:1px solid var(--border-default)"></div>' +
            '<span class="text-xs font-medium shrink-0" style="color:var(--text-muted)">' +
            day +
            "</span>" +
            '<div class="flex-1" style="border-top:1px solid var(--border-default)"></div>' +
            "</div>";
        }
        const title = e.issue_title ? escHtml(e.issue_title.slice(0, 50)) : e.issue_id;
        let detail = "";
        if (e.event_type === "status_changed") detail = `${e.old_value} \u2192 ${e.new_value}`;
        else if (e.new_value) detail = e.new_value;
        return (
          separator +
          '<div class="flex items-start gap-3 py-2 cursor-pointer bg-overlay-hover" style="border-bottom:1px solid var(--surface-raised)" onclick="openDetail(\'' +
          escJsSingle(e.issue_id) +
          "')" +
          ">" +
          '<span class="shrink-0 w-24" style="color:var(--text-muted)">' +
          time +
          "</span>" +
          '<span class="shrink-0 w-32" style="color:var(--text-secondary)">' +
          escHtml(e.event_type) +
          "</span>" +
          '<span class="truncate" style="color:var(--text-primary)">' +
          title +
          "</span>" +
          (detail
            ? '<span class="shrink-0" style="color:var(--text-muted)">' +
              escHtml(detail) +
              "</span>"
            : "") +
          (e.actor
            ? '<span class="shrink-0" style="color:var(--text-muted)">' +
              escHtml(e.actor) +
              "</span>"
            : "") +
          "</div>"
        );
      })
      .join("");
  } catch (_e) {
    container.innerHTML = '<div class="text-red-400">Failed to load activity.</div>';
  }
}
