"""Foundational TypedDicts and Literal types for dataclass to_dict() returns."""

from __future__ import annotations

from typing import Any, Literal, NewType, TypedDict

ISOTimestamp = NewType("ISOTimestamp", str)

# Constrained-string Literal types — canonical definitions.
# core.py re-exports these; db_files.py derives frozensets via get_args().
Severity = Literal["critical", "high", "medium", "low", "info"]
FindingStatus = Literal["open", "acknowledged", "fixed", "false_positive", "unseen_in_latest"]
StatusCategory = Literal["open", "wip", "done"]


class _ProjectConfigRequired(TypedDict):
    """Required keys for ProjectConfig (needed for ID generation and migrations)."""

    prefix: str
    version: int


class ProjectConfig(_ProjectConfigRequired, total=False):
    """Shape of .filigree/config.json.

    ``prefix`` (issue ID generation) and ``version`` (schema migrations) are
    always required.  Other keys are optional.
    """

    name: str
    enabled_packs: list[str]
    mode: str


class PaginatedResult(TypedDict):
    """Envelope returned by paginated query methods."""

    results: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    has_more: bool


class IssueDict(TypedDict):
    """Shape of Issue.to_dict() return value."""

    id: str
    title: str
    status: str
    status_category: StatusCategory
    priority: int
    type: str
    parent_id: str | None
    assignee: str
    created_at: ISOTimestamp
    updated_at: ISOTimestamp
    closed_at: ISOTimestamp | None
    description: str
    notes: str
    fields: dict[str, Any]
    labels: list[str]
    blocks: list[str]
    blocked_by: list[str]
    is_ready: bool
    children: list[str]


class FileRecordDict(TypedDict):
    """Shape of FileRecord.to_dict() return value."""

    id: str
    path: str
    language: str
    file_type: str
    first_seen: ISOTimestamp
    updated_at: ISOTimestamp
    metadata: dict[str, Any]


class ScanFindingDict(TypedDict):
    """Shape of ScanFinding.to_dict() return value."""

    id: str
    file_id: str
    severity: Severity
    status: FindingStatus
    scan_source: str
    rule_id: str
    message: str
    suggestion: str
    scan_run_id: str
    line_start: int | None
    line_end: int | None
    issue_id: str | None
    seen_count: int
    first_seen: ISOTimestamp
    updated_at: ISOTimestamp
    last_seen_at: ISOTimestamp | None
    metadata: dict[str, Any]


class ObservationDict(TypedDict):
    """Shape contract for observation dict representations."""

    id: str
    summary: str
    detail: str
    file_id: str | None
    file_path: str
    line: int | None
    source_issue_id: str
    priority: int
    actor: str
    created_at: ISOTimestamp
    expires_at: ISOTimestamp


class ObservationStatsDict(TypedDict):
    """Shape contract for observation_stats() return value."""

    count: int
    stale_count: int
    oldest_hours: float
    expiring_soon_count: int
