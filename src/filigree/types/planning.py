"""TypedDicts for db_planning.py, db_meta.py, and analytics.py return types."""

from __future__ import annotations

from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# db_planning.py types
# ---------------------------------------------------------------------------


class CriticalPathNode(TypedDict):
    """Single node in the critical-path chain from ``get_critical_path()``."""

    id: str
    title: str
    priority: int
    type: str


# DependencyRecord uses "from" as a key at runtime (a Python keyword).
# TypedDict cannot express this with class syntax; we use functional form.
DependencyRecord = TypedDict("DependencyRecord", {"from": str, "to": str, "type": str})


class PlanPhase(TypedDict):
    """A single phase entry inside a PlanTree."""

    phase: dict[str, Any]
    steps: list[dict[str, Any]]
    total: int
    completed: int
    ready: int


class PlanTree(TypedDict):
    """Plan tree returned by ``get_plan()`` and ``create_plan()``."""

    milestone: dict[str, Any]
    phases: list[PlanPhase]
    total_steps: int
    completed_steps: int


# ---------------------------------------------------------------------------
# db_meta.py types
# ---------------------------------------------------------------------------


class CommentRecord(TypedDict):
    """Row from the comments table returned by ``get_comments()``."""

    id: int
    author: str
    text: str
    created_at: str


class StatsResult(TypedDict):
    """Aggregate stats returned by ``get_stats()``."""

    by_status: dict[str, int]
    by_category: dict[str, int]
    by_type: dict[str, int]
    ready_count: int
    blocked_count: int
    total_dependencies: int


# ---------------------------------------------------------------------------
# analytics.py types
# ---------------------------------------------------------------------------


class TypeMetrics(TypedDict):
    """Per-type cycle time metrics nested inside ``FlowMetrics``."""

    avg_cycle_time_hours: float | None
    count: int


class FlowMetrics(TypedDict):
    """Aggregate flow metrics returned by ``get_flow_metrics()``."""

    period_days: int
    throughput: int
    avg_cycle_time_hours: float | None
    avg_lead_time_hours: float | None
    by_type: dict[str, TypeMetrics]
