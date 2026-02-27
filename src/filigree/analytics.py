"""Flow metrics for filigree — cycle time, lead time, throughput.

Derives metrics from existing events data with zero schema changes.
Separate module from core, operates on FiligreeDB read-only.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from filigree.core import FiligreeDB
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

    events = db.conn.execute(
        "SELECT event_type, new_value, created_at FROM events "
        "WHERE issue_id = ? AND event_type = 'status_changed' ORDER BY created_at ASC, id ASC",
        (issue_id,),
    ).fetchall()
    return _cycle_time_from_events(
        events,
        lambda state: db._resolve_status_category(issue_type, state),
    )


def _cycle_time_from_events(
    events: list[Any],
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


def _fetch_status_events_by_issue(db: FiligreeDB, issue_ids: list[str]) -> dict[str, list[Any]]:
    """Batch-fetch ordered status_changed events for issues."""
    if not issue_ids:
        return {}

    by_issue: dict[str, list[Any]] = {}
    chunk_size = 500  # stay well below SQLite variable limits

    for i in range(0, len(issue_ids), chunk_size):
        chunk = issue_ids[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.conn.execute(
            (
                "SELECT issue_id, new_value, created_at "
                "FROM events "
                "WHERE event_type = 'status_changed' "
                f"AND issue_id IN ({placeholders}) "
                "ORDER BY issue_id ASC, created_at ASC, id ASC"
            ),
            tuple(chunk),
        ).fetchall()
        for row in rows:
            by_issue.setdefault(row["issue_id"], []).append(row)

    return by_issue


def lead_time(
    db: FiligreeDB,
    issue_id: str | None = None,
    *,
    issue: Any | None = None,
) -> float | None:
    """Lead time: hours from creation to done (any done-category state).

    Pass ``issue`` directly to avoid a redundant DB fetch when the caller
    already has the Issue object (e.g. inside get_flow_metrics loops).
    """
    if issue is None:
        if issue_id is None:
            return None
        issue = db.get_issue(issue_id)
    if issue.status_category != "done" or issue.closed_at is None:
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

    cutoff_dt = datetime.now(UTC) - timedelta(days=days)

    # Paginate through all done issues to avoid silent truncation.
    # Query both "closed" (template-defined done states) and "archived"
    # (synthetic status set by archive_closed()) to avoid undercounting.
    page_size = 1000
    recent_closed = []
    for status_filter in ("closed", "archived"):
        offset = 0
        while True:
            page = db.list_issues(status=status_filter, limit=page_size, offset=offset)
            for i in page:
                if i.closed_at:
                    closed_dt = _parse_iso(i.closed_at)
                    if closed_dt is not None and closed_dt >= cutoff_dt:
                        recent_closed.append(i)
            if len(page) < page_size:
                break
            offset += page_size

    cycle_times: list[float] = []
    lead_times: list[float] = []
    by_type: dict[str, list[float]] = {}
    status_events = _fetch_status_events_by_issue(db, [issue.id for issue in recent_closed])

    def _make_resolver(issue_type: str) -> Callable[[str], str]:
        return lambda state: db._resolve_status_category(issue_type, state)

    for issue in recent_closed:
        ct = _cycle_time_from_events(
            status_events.get(issue.id, []),
            _make_resolver(issue.type),
        )
        lt = lead_time(db, issue=issue)
        if ct is not None:
            cycle_times.append(ct)
            by_type.setdefault(issue.type, []).append(ct)
        if lt is not None:
            lead_times.append(lt)

    type_metrics: dict[str, TypeMetrics] = {}
    for issue_type, times in by_type.items():
        type_metrics[issue_type] = {
            "avg_cycle_time_hours": round(sum(times) / len(times), 1) if times else None,
            "count": len(times),
        }

    return {
        "period_days": days,
        "throughput": len(recent_closed),
        "avg_cycle_time_hours": round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else None,
        "avg_lead_time_hours": round(sum(lead_times) / len(lead_times), 1) if lead_times else None,
        "by_type": type_metrics,
    }
