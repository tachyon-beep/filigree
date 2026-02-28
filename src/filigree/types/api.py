"""TypedDicts for MCP tool handler and dashboard route API responses."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from filigree.types.core import ISOTimestamp, IssueDict
from filigree.types.planning import CommentRecord, CriticalPathNode, PlanPhase, StatsResult

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


class SlimIssue(TypedDict):
    """Reduced 5-key issue shape for search results and unblocked lists."""

    id: str
    title: str
    status: str
    priority: int
    type: str


class BlockedIssue(SlimIssue):
    """Slim issue with blocked_by list for get_blocked responses."""

    blocked_by: list[str]


class ErrorResponse(TypedDict):
    """Standard error envelope returned by MCP/dashboard error paths."""

    error: str
    code: str


class TransitionError(TypedDict):
    """Extended error for invalid status transitions.

    Includes valid_transitions hint to guide the caller toward correct states.
    """

    error: str
    code: Literal["invalid_transition"]
    valid_transitions: NotRequired[list[dict[str, Any]]]
    hint: NotRequired[str]


# ---------------------------------------------------------------------------
# Flat inheritance — IssueDict + extra keys (preserves wire format)
#
# IMPORTANT: Never use `class Foo(IssueDict, total=False)` — this makes ALL
# inherited IssueDict keys optional to mypy, defeating type safety. Instead
# use `NotRequired` on individual optional fields.
#
# NOTE on **spread: `Foo(**issue.to_dict(), extra=val)` silently passes through
# any extra keys that to_dict() might add in the future. The shape contract
# tests in test_type_contracts.py catch drift by asserting exact key-set equality.
#
# RESERVED EXTENSION KEYS — these names must never be added to IssueDict:
# valid_transitions, changed_fields, newly_unblocked, selection_reason,
# dep_details, events, comments
# ---------------------------------------------------------------------------


class IssueWithTransitions(IssueDict):
    """Issue detail with optional valid_transitions (MCP get_issue)."""

    valid_transitions: NotRequired[list[dict[str, Any]]]


class IssueWithChangedFields(IssueDict):
    """Issue update response with list of changed field names."""

    changed_fields: list[str]


class IssueWithUnblocked(IssueDict):
    """Issue close response with optional newly-unblocked issues."""

    newly_unblocked: NotRequired[list[SlimIssue]]


class ClaimNextResponse(IssueDict):
    """Claimed issue with human-readable selection reason."""

    selection_reason: str


# ---------------------------------------------------------------------------
# Flat inheritance — StatsResult + prefix
# ---------------------------------------------------------------------------


class StatsWithPrefix(StatsResult):
    """Project stats with project prefix for dashboard display."""

    prefix: str


# ---------------------------------------------------------------------------
# Dashboard detail — IssueDict + dep/event/comment data
# ---------------------------------------------------------------------------


class DepDetail(TypedDict):
    """Minimal dependency info for dep_details lookup in issue detail."""

    title: str
    status: str
    status_category: str
    priority: int


class IssueDetailEvent(TypedDict):
    """Slim 5-column projection of EventRecord — only the columns selected by
    api_issue_detail's SQL query. Do NOT extend to full EventRecord; that is
    a separate type in types/events.py."""

    event_type: str
    actor: str
    old_value: str | None
    new_value: str | None
    created_at: ISOTimestamp


class EnrichedIssueDetail(IssueDict):
    """Full issue detail with dependency info, events, and comments."""

    dep_details: dict[str, DepDetail]
    events: list[IssueDetailEvent]
    comments: list[CommentRecord]


# ---------------------------------------------------------------------------
# True envelopes — list / search / batch wrappers
#
# For types with mixed required/optional keys, use the split-base pattern
# matching the convention in types/workflow.py (FieldSchemaInfo).
# ---------------------------------------------------------------------------


class IssueListResponse(TypedDict):
    """Paginated issue list (MCP list_issues)."""

    issues: list[IssueDict]
    limit: int
    offset: int
    has_more: bool


class SearchResponse(TypedDict):
    """Paginated search results with slim issues (MCP search_issues)."""

    issues: list[SlimIssue]
    limit: int
    offset: int
    has_more: bool


class BatchUpdateResponse(TypedDict):
    """Batch update result with succeeded IDs and failures."""

    succeeded: list[str]
    failed: list[dict[str, Any]]
    count: int


class _BatchCloseRequired(TypedDict):
    """Required keys for BatchCloseResponse (always present)."""

    succeeded: list[str]
    failed: list[dict[str, Any]]
    count: int


class BatchCloseResponse(_BatchCloseRequired, total=False):
    """Batch close result with optional newly-unblocked list.

    ``succeeded``, ``failed``, and ``count`` are always present (enforced
    by ``_BatchCloseRequired``). ``newly_unblocked`` is only included when
    issues were actually unblocked.
    """

    newly_unblocked: list[SlimIssue]


class PlanResponse(TypedDict):
    """Plan tree with computed progress percentage (MCP get_plan)."""

    milestone: IssueDict
    phases: list[PlanPhase]
    total_steps: int
    completed_steps: int
    progress_pct: float


# ---------------------------------------------------------------------------
# MCP planning / meta response shapes
# ---------------------------------------------------------------------------


class DependencyActionResponse(TypedDict):
    """Response for add_dependency / remove_dependency MCP tools."""

    status: str
    from_id: str
    to_id: str


class CriticalPathResponse(TypedDict):
    """Response for get_critical_path MCP tool."""

    path: list[CriticalPathNode]
    length: int


class BatchActionResponse(TypedDict):
    """Shared response shape for batch_add_label / batch_add_comment."""

    succeeded: list[str]
    results: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    count: int


# ---------------------------------------------------------------------------
# MCP meta handler responses
# ---------------------------------------------------------------------------


class AddCommentResult(TypedDict):
    """Response for add_comment MCP tool."""

    status: str
    comment_id: int


class LabelActionResponse(TypedDict):
    """Response for add_label / remove_label MCP tools."""

    status: str
    issue_id: str
    label: str


class JsonlTransferResponse(TypedDict):
    """Response for export_jsonl / import_jsonl MCP tools."""

    status: str
    records: int
    path: str


class ArchiveClosedResponse(TypedDict):
    """Response for archive_closed MCP tool."""

    status: str
    archived_count: int
    archived_ids: list[str]


class CompactEventsResponse(TypedDict):
    """Response for compact_events MCP tool."""

    status: str
    events_deleted: int


class ClaimNextEmptyResponse(TypedDict):
    """Response when claim_next finds no matching issues."""

    status: str
    reason: str


# ---------------------------------------------------------------------------
# MCP workflow handler responses
# ---------------------------------------------------------------------------


class WorkflowStatesResponse(TypedDict):
    """Response for get_workflow_states MCP tool."""

    states: dict[str, list[str]]


class PackListItem(TypedDict):
    """Single pack entry returned by list_packs MCP tool."""

    pack: str
    version: str
    display_name: str
    description: str
    types: list[str]
    requires_packs: list[str]


class ValidationResult(TypedDict):
    """Response for validate_issue MCP tool."""

    valid: bool
    warnings: list[str]
    errors: list[str]


class _WorkflowGuideRequired(TypedDict):
    """Required keys for WorkflowGuideResponse."""

    pack: str
    guide: dict[str, Any] | None


class WorkflowGuideResponse(_WorkflowGuideRequired, total=False):
    """Response for get_workflow_guide MCP tool.

    ``pack`` and ``guide`` are always present.  ``message`` appears when
    no guide is available; ``note`` appears when the pack was resolved
    from a type name.
    """

    message: str
    note: str


class StateExplanation(TypedDict):
    """Response for explain_state MCP tool."""

    state: str
    category: str
    type: str
    inbound_transitions: list[dict[str, str]]
    outbound_transitions: list[dict[str, Any]]
    required_fields: list[str]
