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

from filigree.generations.loom.types import ScanIngestResponseLoom, ScanStats
from filigree.types.files import ScanIngestResult


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
