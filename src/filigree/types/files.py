"""TypedDicts for db_files.py return types."""

from __future__ import annotations

from typing import Any, TypedDict

from filigree.types.core import (
    AssocType,
    FileRecordDict,
    ISOTimestamp,
    ScanFindingDict,
)


class FileAssociation(TypedDict):
    """Shape returned by ``get_file_associations()`` — file-to-issue direction."""

    id: int
    file_id: str
    issue_id: str
    assoc_type: AssocType
    created_at: ISOTimestamp
    issue_title: str | None
    issue_status: str | None


class IssueFileAssociation(TypedDict):
    """Shape returned by ``get_issue_files()`` — issue-to-file direction."""

    id: int
    file_id: str
    issue_id: str
    assoc_type: AssocType
    created_at: ISOTimestamp
    file_path: str
    file_language: str | None


class SeverityBreakdown(TypedDict):
    """Severity-bucketed finding counts (reused by several return shapes)."""

    critical: int
    high: int
    medium: int
    low: int
    info: int


class FindingsSummary(SeverityBreakdown):
    """Shape returned by ``get_file_findings_summary()``."""

    total_findings: int
    open_findings: int


class GlobalFindingsStats(FindingsSummary):
    """Shape returned by ``get_global_findings_stats()``."""

    files_with_findings: int


class HotspotFileRef(TypedDict):
    """Minimal file reference embedded in ``FileHotspot``."""

    id: str
    path: str
    language: str


class FileHotspot(TypedDict):
    """Shape returned by ``get_file_hotspots()``."""

    file: HotspotFileRef
    score: int
    findings_breakdown: SeverityBreakdown


class FileDetail(TypedDict):
    """Shape returned by ``get_file_detail()``."""

    file: FileRecordDict
    associations: list[FileAssociation]
    recent_findings: list[ScanFindingDict]
    summary: FindingsSummary
    observation_count: int


class ScanRunRecord(TypedDict):
    """Shape returned by ``get_scan_runs()``.

    Note: ``completed_at`` is ``MAX(updated_at)`` via SQL aggregate. The query
    uses ``GROUP BY scan_run_id`` so each group has at least one row, guaranteeing
    a non-NULL result. If the query changes to allow empty groups, this field
    should be widened to ``ISOTimestamp | None``.
    """

    scan_run_id: str
    scan_source: str
    started_at: ISOTimestamp
    completed_at: ISOTimestamp
    total_findings: int
    files_scanned: int


class ScanIngestResult(TypedDict):
    """Shape returned by ``process_scan_results()``."""

    files_created: int
    files_updated: int
    findings_created: int
    findings_updated: int
    new_finding_ids: list[str]
    issues_created: int
    issue_ids: list[str]
    warnings: list[str]


class EnrichedFileItem(FileRecordDict):
    """Shape of each item in ``list_files_paginated()`` results.

    Extends FileRecordDict with inline summary and counts.
    """

    summary: FindingsSummary
    associations_count: int
    observation_count: int


class TimelineEntry(TypedDict):
    """Shape of each item in ``get_file_timeline()`` results."""

    id: str
    type: str
    timestamp: ISOTimestamp
    source_id: str
    data: dict[str, Any]


class CleanStaleResult(TypedDict):
    """Shape returned by ``clean_stale_findings()``."""

    findings_fixed: int
