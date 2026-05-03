"""Flow metrics for filigree — cycle time, lead time, throughput.

Derives metrics from existing events data with zero schema changes.
Separate module from core, operates on FiligreeDB read-only.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from filigree.core import FiligreeDB
from filigree.models import Issue
from filigree.types.planning import FlowMetrics, TypeMetrics


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO timestamp, handling timezone-aware and naive formats.

    Returns None if the timestamp cannot be parsed (instead of datetime.now(UTC),
    which would silently corrupt metric calculations).
    """
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def cycle_time(db: FiligreeDB, issue_id: str) -> float | None:
    """Cycle time: hours from first WIP-category state to first done-category state.

    Uses the workflow template system to determine which states are WIP/done,
    so this works correctly for all issue types (bugs, features, risks, etc.),
    not just tasks with literal "in_progress"/"closed" states.

    Returns None if the issue hasn't been through a WIP->done transition.
    """
    issue = db.get_issue(issue_id)
    issue_type = issue.type

    rows = db.conn.execute(
        "SELECT id, event_type, new_value, created_at FROM events WHERE issue_id = ? AND event_type = 'status_changed'",
        (issue_id,),
    ).fetchall()
    return _cycle_time_from_events(
        _sort_events_chronologically(rows),
        lambda state: db._resolve_status_category(issue_type, state),
    )


def _sort_events_chronologically(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Sort event rows by parsed UTC timestamp then by id.

    SQLite ORDER BY on raw ISO text produces wrong results when rows carry
    mixed timezone offsets (e.g. +00:00 vs +10:00 sort lexically but not
    chronologically). Unparseable timestamps are dropped — callers already
    handle missing events by returning None metrics.
    """
    ordered: list[tuple[datetime, int, sqlite3.Row]] = []
    for r in rows:
        dt = _parse_iso(r["created_at"])
        if dt is None:
            continue
        ordered.append((dt, r["id"], r))
    ordered.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in ordered]


def _cycle_time_from_events(
    events: list[sqlite3.Row],
    resolve_category: Callable[[str], str],
) -> float | None:
    """Compute cycle time from ordered status-change event rows."""
    start: datetime | None = None
    end: datetime | None = None
    for evt in events:
        category = resolve_category(evt["new_value"])
        if category == "wip" and start is None:
            start = _parse_iso(evt["created_at"])
        elif category == "done" and start is not None:
            end = _parse_iso(evt["created_at"])
            if end is not None:
                break  # Use first parseable done event after WIP start

    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 3600


def _fetch_status_events_by_issue(db: FiligreeDB, issue_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
    """Batch-fetch ordered status_changed events for issues."""
    if not issue_ids:
        return {}

    by_issue: dict[str, list[sqlite3.Row]] = {}
    chunk_size = 500  # stay well below SQLite variable limits

    for i in range(0, len(issue_ids), chunk_size):
        chunk = issue_ids[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.conn.execute(
            (
                "SELECT id, issue_id, new_value, created_at "
                "FROM events "
                "WHERE event_type = 'status_changed' "
                f"AND issue_id IN ({placeholders})"
            ),
            tuple(chunk),
        ).fetchall()
        for row in rows:
            by_issue.setdefault(row["issue_id"], []).append(row)

    # Sort each issue's events chronologically (by parsed UTC), not by raw text.
    return {issue_id: _sort_events_chronologically(evts) for issue_id, evts in by_issue.items()}


def lead_time(
    db: FiligreeDB,
    issue_id: str | None = None,
    *,
    issue: Issue | None = None,
) -> float | None:
    """Lead time: hours from creation to done (any done-category state).

    Pass ``issue`` directly to avoid a redundant DB fetch when the caller
    already has the Issue object (e.g. inside get_flow_metrics loops).
    """
    if issue is None:
        if issue_id is None:
            return None
        issue = db.get_issue(issue_id)
    if issue.closed_at is None:
        return None
    # Treat archive_closed()'s synthetic status='archived' as completed.
    # It preserves closed_at but strips the done-category, so a strict
    # status_category=='done' check would silently drop archived issues
    # from averages even though throughput includes them.
    if issue.status_category != "done" and issue.status != "archived":
        return None
    created = _parse_iso(issue.created_at)
    closed = _parse_iso(issue.closed_at)
    if created is None or closed is None:
        return None
    return (closed - created).total_seconds() / 3600


def get_flow_metrics(db: FiligreeDB, *, days: int = 30) -> FlowMetrics:
    """Compute aggregate flow metrics for issues closed within the last N days.

    Args:
        days: Lookback window — only issues closed within this period are analyzed.

    Returns:
        {
            "period_days": int,
            "throughput": int,
            "avg_cycle_time_hours": float | None,
            "avg_lead_time_hours": float | None,
            "by_type": {type: {"avg_cycle_time_hours": float, "count": int}},
        }
    """
    from datetime import timedelta

    if days < 1:
        msg = f"days must be >= 1, got {days}"
        raise ValueError(msg)

    cutoff_dt = datetime.now(UTC) - timedelta(days=days)

    # Paginate through all done issues to avoid silent truncation.
    # Query both "closed" (template-defined done states) and "archived"
    # (synthetic status set by archive_closed()) to avoid undercounting.
    # Key by id to dedupe: templates may define an "archived" done state,
    # which overlaps the literal "archived" status bucket; a concurrent
    # archive_closed() can also shift an issue between buckets mid-scan.
    page_size = 1000
    recent_by_id: dict[str, Issue] = {}
    for status_filter in ("closed", "archived"):
        offset = 0
        while True:
            page = db.list_issues(status=status_filter, limit=page_size, offset=offset)
            for i in page:
                if i.closed_at and i.id not in recent_by_id:
                    closed_dt = _parse_iso(i.closed_at)
                    if closed_dt is not None and closed_dt >= cutoff_dt:
                        recent_by_id[i.id] = i
            if len(page) < page_size:
                break
            offset += page_size
    recent_closed = list(recent_by_id.values())

    cycle_times: list[float] = []
    lead_times: list[float] = []
    # Track per-type closed counts independently from cycle-time samples:
    # an issue closed without a WIP transition (allowed by the task workflow)
    # is real throughput but yields no cycle-time sample. Conflating them
    # under-reports the per-type "closed" count the CLI/dashboard display.
    type_counts: dict[str, int] = {}
    type_cycle_times: dict[str, list[float]] = {}
    status_events = _fetch_status_events_by_issue(db, [issue.id for issue in recent_closed])

    def _make_resolver(issue_type: str) -> Callable[[str], str]:
        return lambda state: db._resolve_status_category(issue_type, state)

    for issue in recent_closed:
        ct = _cycle_time_from_events(
            status_events.get(issue.id, []),
            _make_resolver(issue.type),
        )
        lt = lead_time(db, issue=issue)
        type_counts[issue.type] = type_counts.get(issue.type, 0) + 1
        if ct is not None:
            cycle_times.append(ct)
            type_cycle_times.setdefault(issue.type, []).append(ct)
        if lt is not None:
            lead_times.append(lt)

    type_metrics: dict[str, TypeMetrics] = {}
    for issue_type, count in type_counts.items():
        times = type_cycle_times.get(issue_type, [])
        type_metrics[issue_type] = {
            "avg_cycle_time_hours": round(sum(times) / len(times), 1) if times else None,
            "count": count,
        }

    return {
        "period_days": days,
        "throughput": len(recent_closed),
        "avg_cycle_time_hours": round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else None,
        "avg_lead_time_hours": round(sum(lead_times) / len(lead_times), 1) if lead_times else None,
        "by_type": type_metrics,
    }
