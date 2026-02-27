# IMPORT CONSTRAINT: types/ modules must only import from typing, stdlib, and each other.
# NEVER import from core.py, db_base.py, or any mixin — this prevents circular imports.
"""Typed return-value contracts for filigree core and API layers."""

from __future__ import annotations

from filigree.types.core import (
    FileRecordDict,
    ISOTimestamp,
    IssueDict,
    PaginatedResult,
    ProjectConfig,
    ScanFindingDict,
)
from filigree.types.events import (
    EventRecord,
    EventRecordWithTitle,
    UndoFailure,
    UndoResult,
    UndoSuccess,
)
from filigree.types.files import (
    CleanStaleResult,
    FileAssociation,
    FileDetail,
    FileHotspot,
    FindingsSummary,
    GlobalFindingsStats,
    HotspotFileRef,
    IssueFileAssociation,
    ScanIngestResult,
    ScanRunRecord,
    SeverityBreakdown,
)
from filigree.types.planning import (
    CommentRecord,
    CriticalPathNode,
    DependencyRecord,
    FlowMetrics,
    PlanPhase,
    PlanTree,
    StatsResult,
    TypeMetrics,
)
from filigree.types.workflow import (
    FieldSchemaInfo,
    StateInfo,
    TemplateInfo,
    TemplateListItem,
    TransitionInfo,
)

__all__ = [
    "CleanStaleResult",
    "CommentRecord",
    "CriticalPathNode",
    "DependencyRecord",
    "EventRecord",
    "EventRecordWithTitle",
    "FieldSchemaInfo",
    "FileAssociation",
    "FileDetail",
    "FileHotspot",
    "FileRecordDict",
    "FindingsSummary",
    "FlowMetrics",
    "GlobalFindingsStats",
    "HotspotFileRef",
    "ISOTimestamp",
    "IssueDict",
    "IssueFileAssociation",
    "PaginatedResult",
    "PlanPhase",
    "PlanTree",
    "ProjectConfig",
    "ScanFindingDict",
    "ScanIngestResult",
    "ScanRunRecord",
    "SeverityBreakdown",
    "StateInfo",
    "StatsResult",
    "TemplateInfo",
    "TemplateListItem",
    "TransitionInfo",
    "TypeMetrics",
    "UndoFailure",
    "UndoResult",
    "UndoSuccess",
]
