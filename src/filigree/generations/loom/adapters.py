"""Loom-generation shape adapters.

Adapters take internal domain results (from the ``filigree.db_*`` core
layer) and produce the loom-generation wire shape. Per ADR-002 §6,
adapters are **thin, data-only transformations**: they rename keys, move
fields between nesting levels, and wrap results in the unified
envelopes. They do not contain business logic.

If a loom handler would need to branch on generation state (``if
generation == "loom": ...``), the branch belongs in the handler layer,
not here.
"""

from __future__ import annotations

from filigree.generations.loom.types import (
    CommentRecordLoom,
    IssueLoom,
    ScanIngestResponseLoom,
    ScanStats,
    SlimIssueLoom,
)
from filigree.models import Issue
from filigree.types.core import ISOTimestamp
from filigree.types.files import ScanIngestResult
from filigree.types.planning import CommentRecord


def slim_issue_to_loom(issue: Issue) -> SlimIssueLoom:
    """Project an ``Issue`` into the loom slim shape.

    Renames ``id`` to ``issue_id`` per the Phase D vocabulary shift and
    keeps the same five-field projection as classic ``SlimIssue`` (title,
    status, priority, type). Used by every loom batch handler whose
    ``succeeded`` and ``newly_unblocked`` lists return slim issues.
    """
    return SlimIssueLoom(
        issue_id=issue.id,
        title=issue.title,
        status=issue.status,
        priority=issue.priority,
        type=issue.type,
    )


def issue_to_loom(issue: Issue) -> IssueLoom:
    """Project an ``Issue`` into the full loom-vocab issue shape.

    Mirrors ``Issue.to_dict()`` (returning ``IssueDict``) except the
    issue's own primary key is renamed ``id`` → ``issue_id``. Reference
    fields holding other issues' ids (``parent_id``, ``blocks``,
    ``blocked_by``, ``children``) keep their existing names per the
    loom-vocabulary scope (only the entity's own primary key is renamed).

    Used by every single-issue loom endpoint that returns a full issue
    projection (GET, PATCH, close, reopen, claim, release, claim-next,
    create).
    """
    classic = issue.to_dict()
    return IssueLoom(
        issue_id=classic["id"],
        title=classic["title"],
        status=classic["status"],
        status_category=classic["status_category"],
        priority=classic["priority"],
        type=classic["type"],
        parent_id=classic["parent_id"],
        assignee=classic["assignee"],
        created_at=classic["created_at"],
        updated_at=classic["updated_at"],
        closed_at=classic["closed_at"],
        description=classic["description"],
        notes=classic["notes"],
        fields=classic["fields"],
        labels=classic["labels"],
        blocks=classic["blocks"],
        blocked_by=classic["blocked_by"],
        is_ready=classic["is_ready"],
        children=classic["children"],
        data_warnings=classic["data_warnings"],
    )


def comment_record_to_loom(record: CommentRecord, *, created_at: ISOTimestamp | None = None) -> CommentRecordLoom:
    """Project a classic ``CommentRecord`` (``id``) into the loom shape
    (``comment_id``).

    The ``created_at`` override exists because the dashboard's
    ``add_comment`` handler fetches the timestamp separately after
    insert (rather than reading it from a CommentRecord); callers can
    pass the freshly-fetched timestamp through this argument.
    """
    return CommentRecordLoom(
        comment_id=record["id"],
        author=record["author"],
        text=record["text"],
        created_at=created_at if created_at is not None else record["created_at"],
    )


def scan_ingest_result_to_loom(result: ScanIngestResult) -> ScanIngestResponseLoom:
    """Transform an internal ``ScanIngestResult`` into the loom response shape.

    - ``new_finding_ids`` → ``succeeded`` (the generic batch wrapper's
      success list; loom's succeeded type is ``list[str]``).
    - ``files_created`` / ``files_updated`` / ``findings_created`` /
      ``findings_updated`` / ``observations_created`` /
      ``observations_failed`` → ``stats`` sibling.
    - ``warnings`` → top-level (kept at top level so consumers that only
      care about operator warnings do not have to reach into ``stats``).
    - ``failed`` is ``[]`` until per-finding ingest failure tracking
      lands (non-breaking addition per ADR-002 §3).
    """
    return ScanIngestResponseLoom(
        succeeded=list(result["new_finding_ids"]),
        failed=[],
        stats=ScanStats(
            files_created=result["files_created"],
            files_updated=result["files_updated"],
            findings_created=result["findings_created"],
            findings_updated=result["findings_updated"],
            observations_created=result["observations_created"],
            observations_failed=result["observations_failed"],
        ),
        warnings=list(result["warnings"]),
    )
