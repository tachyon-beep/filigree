"""Pure data models — Issue, FileRecord, ScanFinding.

These dataclasses represent database rows as typed Python objects. They depend
only on ``filigree.types.core`` (TypedDicts and Literal types), so any module
in the package can import them without circular-dependency risk.

Extracted from ``core.py`` to break the cycle:
    types/core.py  <--  models.py  <--  core.py / db_*.py mixins
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, get_args

from filigree.types.core import (
    FileRecordDict,
    FindingStatus,
    ISOTimestamp,
    IssueDict,
    ScanFindingDict,
    Severity,
    StatusCategory,
)

_EMPTY_TS: ISOTimestamp = ISOTimestamp("")

# Derive valid sets from Literal types (avoids importing from db_files)
_VALID_STATUS_CATEGORIES: frozenset[str] = frozenset(get_args(StatusCategory))
_VALID_SEVERITIES: frozenset[str] = frozenset(get_args(Severity))
_VALID_FINDING_STATUSES: frozenset[str] = frozenset(get_args(FindingStatus))


@dataclass
class Issue:
    id: str
    title: str
    status: str = "open"
    priority: int = 2
    type: str = "task"
    parent_id: str | None = None
    assignee: str = ""
    created_at: ISOTimestamp = _EMPTY_TS
    updated_at: ISOTimestamp = _EMPTY_TS
    closed_at: ISOTimestamp | None = None
    description: str = ""
    notes: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    # Computed (not stored directly)
    labels: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    is_ready: bool = False
    children: list[str] = field(default_factory=list)
    status_category: StatusCategory = "open"

    def __post_init__(self) -> None:
        if self.status_category not in _VALID_STATUS_CATEGORIES:
            raise ValueError(f"Invalid status_category {self.status_category!r}, expected one of {sorted(_VALID_STATUS_CATEGORIES)}")
        if not isinstance(self.priority, int) or not (0 <= self.priority <= 4):
            raise ValueError(f"Invalid priority {self.priority!r}, expected int 0-4")

    def to_dict(self) -> IssueDict:
        fields = self.fields
        warnings: list[str] = []
        if fields.get("_fields_error"):
            fields = {k: v for k, v in fields.items() if k != "_fields_error"}
            warnings.append("fields data was corrupt and could not be parsed")
        return IssueDict(
            id=self.id,
            title=self.title,
            status=self.status,
            status_category=self.status_category,
            priority=self.priority,
            type=self.type,
            parent_id=self.parent_id,
            assignee=self.assignee,
            created_at=self.created_at,
            updated_at=self.updated_at,
            closed_at=self.closed_at,
            description=self.description,
            notes=self.notes,
            fields=fields,
            labels=self.labels,
            blocks=self.blocks,
            blocked_by=self.blocked_by,
            is_ready=self.is_ready,
            children=self.children,
            data_warnings=warnings,
        )


@dataclass
class FileRecord:
    id: str
    path: str
    language: str = ""
    file_type: str = ""
    first_seen: ISOTimestamp = _EMPTY_TS
    updated_at: ISOTimestamp = _EMPTY_TS
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> FileRecordDict:
        metadata = self.metadata
        warnings: list[str] = []
        if metadata.get("_metadata_error"):
            metadata = {k: v for k, v in metadata.items() if k != "_metadata_error"}
            warnings.append("metadata was corrupt and could not be parsed")
        return FileRecordDict(
            id=self.id,
            path=self.path,
            language=self.language,
            file_type=self.file_type,
            first_seen=self.first_seen,
            updated_at=self.updated_at,
            metadata=metadata,
            data_warnings=warnings,
        )


@dataclass
class ScanFinding:
    id: str
    file_id: str
    severity: Severity = "info"
    status: FindingStatus = "open"
    scan_source: str = ""
    rule_id: str = ""
    message: str = ""
    suggestion: str = ""
    scan_run_id: str = ""
    line_start: int | None = None
    line_end: int | None = None
    issue_id: str | None = None
    seen_count: int = 1
    first_seen: ISOTimestamp = _EMPTY_TS
    updated_at: ISOTimestamp = _EMPTY_TS
    last_seen_at: ISOTimestamp | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in _VALID_SEVERITIES:
            raise ValueError(f"Invalid severity {self.severity!r}, expected one of {sorted(_VALID_SEVERITIES)}")
        if self.status not in _VALID_FINDING_STATUSES:
            raise ValueError(f"Invalid finding status {self.status!r}, expected one of {sorted(_VALID_FINDING_STATUSES)}")

    def to_dict(self) -> ScanFindingDict:
        metadata = self.metadata
        warnings: list[str] = []
        if metadata.get("_metadata_error"):
            metadata = {k: v for k, v in metadata.items() if k != "_metadata_error"}
            warnings.append("metadata was corrupt and could not be parsed")
        return ScanFindingDict(
            id=self.id,
            file_id=self.file_id,
            severity=self.severity,
            status=self.status,
            scan_source=self.scan_source,
            rule_id=self.rule_id,
            message=self.message,
            suggestion=self.suggestion,
            scan_run_id=self.scan_run_id,
            line_start=self.line_start,
            line_end=self.line_end,
            issue_id=self.issue_id,
            seen_count=self.seen_count,
            first_seen=self.first_seen,
            updated_at=self.updated_at,
            last_seen_at=self.last_seen_at,
            metadata=metadata,
            data_warnings=warnings,
        )
