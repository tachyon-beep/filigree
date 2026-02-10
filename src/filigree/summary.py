"""Pre-computed summary generator for agent context.

Reads the filigree DB and generates a compact markdown summary (~80-120 lines)
that agents can read in a single file read at session start.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filigree.core import FiligreeDB, Issue

STALE_THRESHOLD_DAYS = 3


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO timestamp, handling timezone-aware and naive formats."""
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.now(UTC)


def generate_summary(db: FiligreeDB) -> str:
    """Generate the context.md summary from current DB state."""
    now = datetime.now(UTC)
    now_iso = now.isoformat(timespec="seconds")
    stats = db.get_stats()
    ready = db.get_ready()
    blocked = db.get_blocked()
    # WFT-FR-061: Use wip category to capture all work-in-progress states (fixing, verifying, etc.)
    in_progress = db.list_issues(status="wip")
    recent = db.get_recent_events(limit=10)

    lines: list[str] = []
    lines.append(f"# Project Pulse (auto-generated {now_iso})")
    lines.append("")

    # WFT-FR-060: Vitals use category counts (open/wip/done) instead of literal status names
    by_cat = stats.get("by_category", {})
    open_count = by_cat.get("open", 0)
    wip_count = by_cat.get("wip", 0)
    done_count = by_cat.get("done", 0)
    ready_count = stats["ready_count"]
    blocked_count = stats["blocked_count"]

    lines.append("## Vitals")
    lines.append(
        f"Open: {open_count} | In Progress: {wip_count} | Done: {done_count}"
        f" | Ready: {ready_count} | Blocked: {blocked_count}"
    )
    lines.append("")

    # -- Active Plans (milestones)
    milestones = db.list_issues(type="milestone", status="open")
    milestones += db.list_issues(type="milestone", status="wip")
    if milestones:
        lines.append("## Active Plans")
        for ms in milestones:
            plan = db.get_plan(ms.id)
            total = plan["total_steps"]
            done = plan["completed_steps"]
            if total > 0:
                bar_filled = int((done / total) * 10)
                bar = "\u2588" * bar_filled + "\u2591" * (10 - bar_filled)
            else:
                bar = "\u2591" * 10
            lines.append(f"### {ms.title} [{bar}] {done}/{total} steps")

            for phase_data in plan["phases"]:
                phase = phase_data["phase"]
                p_total = phase_data["total"]
                p_done = phase_data["completed"]
                p_ready = phase_data["ready"]

                if p_done == p_total and p_total > 0:
                    marker = "\u2713"
                elif phase["status_category"] == "wip":
                    marker = "\u25b6"
                else:
                    marker = "\u25cb"

                ready_note = f", {p_ready} ready" if p_ready > 0 else ""
                lines.append(f"  {marker} {phase['title']} ({p_done}/{p_total} complete{ready_note})")

            lines.append("")

    # -- Ready to Work (WFT-NFR-010: limit 12)
    lines.append("## Ready to Work (no blockers, by priority)")
    if ready:
        for issue in ready[:12]:
            parent_ctx = ""
            if issue.parent_id:
                try:
                    parent = db.get_issue(issue.parent_id)
                    parent_ctx = f" ({parent.title})"
                except KeyError:
                    pass
            # WFT-FR-061: Show state in parens when it differs from the default "open"
            state_info = f" ({issue.status})" if issue.status != "open" else ""
            lines.append(f'- P{issue.priority} {issue.id} [{issue.type}] "{issue.title}"{state_info}{parent_ctx}')
        if len(ready) > 12:
            lines.append(f"  ...and {len(ready) - 12} more")
    else:
        lines.append("- (none)")
    lines.append("")

    # -- In Progress (all wip-category states)
    lines.append("## In Progress")
    if in_progress:
        for issue in in_progress:
            parent_ctx = ""
            if issue.parent_id:
                try:
                    parent = db.get_issue(issue.parent_id)
                    parent_ctx = f" ({parent.title})"
                except KeyError:
                    pass
            # WFT-FR-061: Show state in parens when it differs from the default "in_progress"
            state_info = f" ({issue.status})" if issue.status != "in_progress" else ""
            lines.append(f'- {issue.id} [{issue.type}] "{issue.title}"{state_info}{parent_ctx}')
    else:
        lines.append("- (none)")
    lines.append("")

    # WFT-FR-071 / WFT-SR-013: Needs Attention — wip issues with missing required fields
    needs_attention: list[tuple[Issue, list[str]]] = []
    for issue in in_progress:
        missing = db.templates.validate_fields_for_state(issue.type, issue.status, issue.fields)
        if missing:
            needs_attention.append((issue, missing))
    if needs_attention:
        lines.append("## Needs Attention")
        for attn_issue, missing_fields in needs_attention[:8]:
            lines.append(
                f'- {attn_issue.id} [{attn_issue.type}] "{attn_issue.title}" ({attn_issue.status})'
                f" — missing: {', '.join(missing_fields)}"
            )
        if len(needs_attention) > 8:
            lines.append(f"  ...and {len(needs_attention) - 8} more")
        lines.append("")

    # -- Stale (wip-category >3 days with no activity)
    stale_cutoff = now - timedelta(days=STALE_THRESHOLD_DAYS)
    stale = [i for i in in_progress if _parse_iso(i.updated_at).replace(tzinfo=UTC) < stale_cutoff.replace(tzinfo=UTC)]
    if stale:
        lines.append("## Stale (in_progress >3 days, no activity)")
        for issue in stale:
            updated = _parse_iso(issue.updated_at)
            days_ago = (now.replace(tzinfo=UTC) - updated.replace(tzinfo=UTC)).days
            line = f'- P{issue.priority} {issue.id} [{issue.type}] "{issue.title}" ({days_ago}d stale)'
            lines.append(line)
        lines.append("")

    # -- Blocked (top 10)
    lines.append("## Blocked (top 10 by priority)")
    if blocked:
        for issue in blocked[:10]:
            blocker_names = []
            for bid in issue.blocked_by:
                try:
                    b = db.get_issue(bid)
                    if b.status_category != "done":
                        blocker_names.append(f"{bid}")
                except KeyError:
                    blocker_names.append(bid)
            blockers_str = ", ".join(blocker_names) if blocker_names else "?"
            line = f'- P{issue.priority} {issue.id} [{issue.type}] "{issue.title}" \u2190 blocked by: {blockers_str}'
            lines.append(line)
        if len(blocked) > 10:
            lines.append(f"  ...and {len(blocked) - 10} more")
    else:
        lines.append("- (none)")
    lines.append("")

    # -- Epic Progress (WFT-NFR-010: limit 10; use status_category for done/open checks)
    epics = db.list_issues(type="epic")
    open_epics = [e for e in epics if e.status_category != "done"]
    if open_epics:
        lines.append("## Epic Progress")
        for epic in open_epics[:10]:
            children = db.list_issues(parent_id=epic.id)
            total = len(children)
            done = sum(1 for c in children if c.status_category == "done")
            ready_c = sum(1 for c in children if c.is_ready)
            blocked_c = sum(1 for c in children if not c.is_ready and c.status_category == "open")

            if total > 0:
                bar_filled = int((done / total) * 8)
                bar = "\u2588" * bar_filled + "\u2591" * (8 - bar_filled)
            else:
                bar = "\u2591" * 8

            extra = []
            if ready_c:
                extra.append(f"{ready_c} ready")
            if blocked_c:
                extra.append(f"{blocked_c} blocked")
            extra_str = f" ({', '.join(extra)})" if extra else ""

            lines.append(f"- {epic.title:<40} [{bar}] {done}/{total}{extra_str}")
        lines.append("")

    # -- Critical Path
    crit_path = db.get_critical_path()
    if crit_path:
        lines.append(f"## Critical Path ({len(crit_path)} issues)")
        for i, item in enumerate(crit_path):
            arrow = " -> " if i > 0 else ""
            lines.append(f'  {arrow}P{item["priority"]} {item["id"]} [{item["type"]}] "{item["title"]}"')
        lines.append("")

    # -- Recent Activity
    lines.append("## Recent Activity (last 10 events)")
    if recent:
        for evt in recent:
            evt_type = evt["event_type"].upper().replace("_", " ")
            title = evt.get("issue_title", evt["issue_id"])
            # Truncate long JSON values from beads migration
            old_v = evt["old_value"] or ""
            new_v = evt["new_value"] or ""
            if len(old_v) > 50:
                old_v = old_v[:47] + "..."
            if len(new_v) > 50:
                new_v = new_v[:47] + "..."
            detail = ""
            if old_v and new_v:
                detail = f" {old_v}\u2192{new_v}"
            elif new_v:
                detail = f" {new_v}"
            lines.append(f'- {evt_type} {evt["issue_id"]} "{title}"{detail}')
    else:
        lines.append("- (no recent activity)")
    lines.append("")

    return "\n".join(lines)


def write_summary(db: FiligreeDB, output_path: str | Path) -> None:
    """Generate and write the summary atomically (write-temp then rename)."""
    summary = generate_summary(db)
    output = Path(output_path)
    tmp_path = output.with_suffix(".tmp")
    tmp_path.write_text(summary)
    os.replace(str(tmp_path), str(output))
