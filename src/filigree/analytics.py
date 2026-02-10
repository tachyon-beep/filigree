"""Flow metrics for filigree — cycle time, lead time, throughput.

Derives metrics from existing events data with zero schema changes.
Separate module from core, operates on FiligreeDB read-only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from filigree.core import FiligreeDB


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO timestamp, handling timezone-aware and naive formats."""
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return datetime.now(UTC)


def cycle_time(db: FiligreeDB, issue_id: str) -> float | None:
    """Cycle time: hours from first in_progress to closed.

    Returns None if the issue hasn't been through in_progress→closed.
    """
    events = db.conn.execute(
        "SELECT event_type, new_value, created_at FROM events "
        "WHERE issue_id = ? AND event_type = 'status_changed' "
        "ORDER BY created_at ASC",
        (issue_id,),
    ).fetchall()

    start: datetime | None = None
    end: datetime | None = None
    for evt in events:
        if evt["new_value"] == "in_progress" and start is None:
            start = _parse_iso(evt["created_at"])
        if evt["new_value"] == "closed":
            end = _parse_iso(evt["created_at"])

    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 3600


def lead_time(db: FiligreeDB, issue_id: str) -> float | None:
    """Lead time: hours from creation to closed."""
    issue = db.get_issue(issue_id)
    if issue.status != "closed" or issue.closed_at is None:
        return None
    created = _parse_iso(issue.created_at)
    closed = _parse_iso(issue.closed_at)
    return (closed - created).total_seconds() / 3600


def get_flow_metrics(db: FiligreeDB, *, days: int = 30) -> dict[str, Any]:
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
    cutoff_iso = cutoff_dt.isoformat()

    closed = db.list_issues(status="closed")
    # Filter to issues closed within the lookback window
    recent_closed = [i for i in closed if i.closed_at and i.closed_at >= cutoff_iso]

    cycle_times: list[float] = []
    lead_times: list[float] = []
    by_type: dict[str, list[float]] = {}

    for issue in recent_closed:
        ct = cycle_time(db, issue.id)
        lt = lead_time(db, issue.id)
        if ct is not None:
            cycle_times.append(ct)
            by_type.setdefault(issue.type, []).append(ct)
        if lt is not None:
            lead_times.append(lt)

    type_metrics: dict[str, dict[str, Any]] = {}
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
