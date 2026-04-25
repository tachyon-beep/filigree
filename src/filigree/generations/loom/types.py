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

from typing import NotRequired, TypedDict

from filigree.types.api import BatchFailure, BatchResponse


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
