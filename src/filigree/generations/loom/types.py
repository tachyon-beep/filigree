"""Loom-generation response types.

Per ADR-002 §6, loom wraps internal results in the unified envelopes
defined in ``filigree.types.api``. Endpoint-specific response types that
extend those envelopes (e.g. scan-results, which adds a counts sibling
and top-level warnings on top of the batch wrapper) are declared here.

Kept intentionally small: each TypedDict maps 1:1 to a fixture in
``tests/fixtures/contracts/loom/`` and is the single source of truth for
that endpoint's loom wire shape. If a type has no fixture companion, it
does not belong here.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from filigree.types.api import BatchFailure, BatchResponse
from filigree.types.core import ISOTimestamp, StatusCategory


class SlimIssueLoom(TypedDict):
    """Slim issue projection for the loom generation.

    Key difference from ``filigree.types.api.SlimIssue``: ``issue_id``
    replaces ``id`` per the Phase D vocabulary shift. Populated by the
    loom issue adapters in Phase C3 and later.
    """

    issue_id: str
    title: str
    status: str
    priority: int
    type: str


class ScanStats(TypedDict):
    """Per-ingest counts surfaced alongside succeeded / failed.

    Mirrors the integer counters in ``filigree.types.files.ScanIngestResult``
    except ``new_finding_ids`` (moved up to ``ScanIngestResponseLoom.succeeded``)
    and ``warnings`` (moved up to ``ScanIngestResponseLoom.warnings``).
    """

    files_created: int
    files_updated: int
    findings_created: int
    findings_updated: int
    observations_created: int
    observations_failed: int


class BatchCloseResponseLoom(TypedDict):
    """Response shape for ``POST /api/loom/batch/close``.

    Functionally a ``BatchResponse[SlimIssueLoom]`` except
    ``newly_unblocked`` carries ``SlimIssueLoom`` rather than the
    classic ``SlimIssue`` that ``BatchResponse[_T]``'s definition
    hard-codes — newly-unblocked issues use the loom vocabulary
    (``issue_id``) too.

    Pinned by ``tests/fixtures/contracts/loom/batch-close.json``.
    """

    succeeded: list[SlimIssueLoom]
    failed: list[BatchFailure]
    newly_unblocked: NotRequired[list[SlimIssueLoom]]


class IssueLoom(TypedDict):
    """Loom-vocab issue projection.

    Same field shape as ``IssueDict`` (``filigree.types.core``) except
    the issue's own primary key is named ``issue_id`` rather than
    ``id``. Reference fields that hold *other* issues' ids
    (``parent_id``, ``blocks``, ``blocked_by``, ``children``) keep
    their existing names — only the entity's own primary key is
    renamed per the loom vocabulary.

    Used as the canonical response shape for every single-issue loom
    endpoint in Phase C3 (GET, PATCH, close, reopen, claim, release,
    claim-next, create). Endpoints with optional enrichment use the
    ``WithFiles`` / ``WithUnblocked`` subtypes below; otherwise
    consumers see exactly this 20-field projection.

    Pinned by ``tests/fixtures/contracts/loom/issues-*.json``.
    """

    issue_id: str
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
    data_warnings: list[str]


class IssueLoomWithFiles(IssueLoom):
    """``IssueLoom`` + ``files``: the response shape returned when
    ``GET /api/loom/issues/{issue_id}?include_files=true``.

    The ``files`` payload mirrors what ``db.get_issue_files()`` returns —
    a list of file-association rows. Loom does not yet declare a
    dedicated ``FileAssocLoom`` TypedDict; phase D may tighten this
    when the file surface is loom-ified.
    """

    files: list[dict[str, Any]]


class IssueLoomWithUnblocked(IssueLoom):
    """``IssueLoom`` + ``newly_unblocked``: returned by close-issue
    when at least one issue became ready as a result. Mirrors MCP's
    ``IssueWithUnblocked`` semantics but uses ``SlimIssueLoom``
    rather than the classic ``SlimIssue``.
    """

    newly_unblocked: list[SlimIssueLoom]


class CommentRecordLoom(TypedDict):
    """Comment row in the loom vocabulary.

    Classic ``CommentRecord`` uses ``id`` for the comment's own primary
    key; loom renames it to ``comment_id`` for the same reason
    ``SlimIssue.id`` becomes ``SlimIssueLoom.issue_id`` — entity
    primary keys are typed by entity name.
    """

    comment_id: int
    author: str
    text: str
    created_at: ISOTimestamp


class ScanIngestResponseLoom(BatchResponse[str]):
    """Response shape for ``POST /api/loom/scan-results``.

    ``succeeded`` contains server-generated finding ids for newly-created
    findings (classic called this ``new_finding_ids``). ``failed`` is
    always present as an empty list in 2.0; populated once per-finding
    ingest failure tracking lands (non-breaking addition). ``stats`` and
    ``warnings`` are loom-specific additions on top of the generic batch
    envelope.

    Pinned by ``tests/fixtures/contracts/loom/scan-results.json``.
    """

    stats: ScanStats
    warnings: list[str]
