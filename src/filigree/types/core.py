"""Foundational TypedDicts for dataclass to_dict() returns."""

from __future__ import annotations

from typing import Any, NewType, TypedDict

ISOTimestamp = NewType("ISOTimestamp", str)


class ProjectConfig(TypedDict, total=False):
    """Shape of .filigree/config.json."""

    prefix: str
    name: str
    version: int
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
    id: str
    title: str
    status: str
    status_category: str
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
    id: str
    path: str
    language: str
    file_type: str
    first_seen: ISOTimestamp
    updated_at: ISOTimestamp
    metadata: dict[str, Any]


class ScanFindingDict(TypedDict):
    id: str
    file_id: str
    severity: str
    status: str
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
