"""TypedDicts for MCP tool handler and dashboard route API responses."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Generic, Literal, NotRequired, TypedDict, TypeVar

from filigree.types.core import ISOTimestamp, IssueDict, StatusCategory
from filigree.types.events import EventType
from filigree.types.planning import CommentRecord, CriticalPathNode, PlanTree, StatsResult

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


class TransitionDetail(TypedDict):
    """Full transition info returned by get_issue and get_valid_transitions.

    ``enforcement`` is always populated in practice because TransitionDetail
    is built from TransitionOption, which sources it from TransitionDefinition.
    The underlying type allows None for unregistered-type fallback paths.
    """

    to: str
    category: str
    enforcement: str
    requires_fields: list[str]
    missing_fields: list[str]
    ready: bool


class TransitionHint(TypedDict):
    """Abbreviated transition hint included in error responses."""

    to: str
    category: str
    ready: NotRequired[bool]


# InboundTransitionInfo uses "from" as a key at runtime (a Python keyword).
# TypedDict cannot express this with class syntax; we use functional form.
InboundTransitionInfo = TypedDict("InboundTransitionInfo", {"from": str, "enforcement": str})


class OutboundTransitionInfo(TypedDict):
    """Outbound transition info returned by explain_state."""

    to: str
    enforcement: str
    requires_fields: list[str]


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
    """Flat error envelope returned by MCP tool handlers.

    NOTE: Dashboard routes use a different nested format via _error_response()
    in dashboard_routes/common.py: ``{"error": {"message", "code", "details"}}``.
    """

    error: str
    code: str


class TransitionError(TypedDict):
    """Extended error for invalid status transitions.

    Includes valid_transitions hint to guide the caller toward correct states.
    """

    error: str
    code: Literal["invalid_transition"]
    valid_transitions: NotRequired[list[TransitionHint]]
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

    valid_transitions: NotRequired[list[TransitionDetail]]


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
    status_category: StatusCategory
    priority: int


class IssueDetailEvent(TypedDict):
    """Slim 5-column projection of EventRecord — only the columns selected by
    api_issue_detail's SQL query. Do NOT extend to full EventRecord; that is
    a separate type in types/events.py."""

    event_type: EventType
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
# ---------------------------------------------------------------------------


class IssueListResponse(TypedDict):
    """Paginated issue list (MCP list_issues).

    Does not use ``PaginatedResult`` because the list key is ``issues``
    (not ``results``) for wire-format compatibility, and ``total`` is
    omitted because counting all matching issues is expensive for large
    projects. File pagination uses ``PaginatedResult`` with ``total``
    because file counts are bounded.
    """

    issues: list[IssueDict]
    limit: int
    offset: int
    has_more: bool


class SearchResponse(TypedDict):
    """Paginated search results with slim issues (MCP search_issues).

    Same pagination divergence as ``IssueListResponse`` — see its docstring.
    """

    issues: list[SlimIssue]
    limit: int
    offset: int
    has_more: bool


class BatchFailureDetail(TypedDict):
    """Error detail for a single failed item in a batch operation.

    All batch failures share this {id, error, code} shape.  Batch update/close
    may also include valid_transitions when the failure is an invalid transition.
    """

    id: str
    error: str
    code: str
    valid_transitions: NotRequired[list[TransitionHint]]


class BatchUpdateResponse(TypedDict):
    """Batch update result with succeeded IDs and failures."""

    succeeded: list[str]
    failed: list[BatchFailureDetail]
    count: int


class BatchCloseResponse(TypedDict):
    """Batch close result with optional newly-unblocked list.

    ``succeeded``, ``failed``, and ``count`` are always present.
    ``newly_unblocked`` is only included when issues were actually unblocked.
    """

    succeeded: list[str]
    failed: list[BatchFailureDetail]
    count: int
    newly_unblocked: NotRequired[list[SlimIssue]]


class PlanResponse(PlanTree):
    """Plan tree with computed progress percentage (MCP get_plan)."""

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
    """Shared response shape for batch_add_label / batch_add_comment.

    ``results`` varies by operation (label adds: {id, status}, comment adds:
    {id, comment_id}), so it remains list[dict[str, Any]].
    """

    succeeded: list[str]
    results: list[dict[str, Any]]
    failed: list[BatchFailureDetail]
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
    skipped_types: NotRequired[dict[str, int]]


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


class WorkflowGuideResponse(TypedDict):
    """Response for get_workflow_guide MCP tool.

    ``pack`` and ``guide`` are always present.  ``message`` appears when
    no guide is available; ``note`` appears when the pack was resolved
    from a type name.
    """

    pack: str
    guide: dict[str, Any] | None
    message: NotRequired[str]
    note: NotRequired[str]


class StateExplanation(TypedDict):
    """Response for explain_state MCP tool."""

    state: str
    category: str
    type: str
    inbound_transitions: list[InboundTransitionInfo]
    outbound_transitions: list[OutboundTransitionInfo]
    required_fields: list[str]


# ---------------------------------------------------------------------------
# 2.0 response envelopes
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


class ErrorCode(StrEnum):
    """Closed set of error codes for MCP + dashboard + CLI.

    Consolidated from 15+ ad-hoc strings into 9 stable members. Callers
    branch on these values; retry / UX policies depend on them.
    """

    VALIDATION = "VALIDATION"  # replaces: invalid, validation_error, invalid_path, invalid_command, batch_all_failed
    NOT_FOUND = "NOT_FOUND"  # replaces: not_found, scanner_not_found, unknown_tool, command_not_found
    CONFLICT = "CONFLICT"  # claim races, optimistic-lock miss
    INVALID_TRANSITION = "INVALID_TRANSITION"
    PERMISSION = "PERMISSION"  # replaces: permission_error
    NOT_INITIALIZED = "NOT_INITIALIZED"  # replaces: not_initialized
    IO = "IO"  # replaces: io_error, db_error, database_error, import_error
    INVALID_API_URL = "INVALID_API_URL"
    STOP_FAILED = "STOP_FAILED"


class BatchFailure(TypedDict):
    """One failed item inside a BatchResponse.failed list.

    ``item_id`` is deliberately generic — batch operations exist for
    issues, findings, and observations; this field carries whichever id
    shape the specific batch tool operates on.
    """

    item_id: str
    error: str
    code: ErrorCode


class BatchResponse(TypedDict, Generic[_T]):
    """Unified response for batch mutation operations.

    Rules:
    - ``succeeded`` is a list of T — SlimIssue for issue-centric ops in
      slim mode, full records for others or in full mode.
    - ``failed`` is always present (empty list if no failures).
    - ``newly_unblocked`` is OMITTED entirely when the op cannot unblock
      (not present as ``[]``). Present only for close/transition ops.
    """

    succeeded: list[_T]
    failed: list[BatchFailure]
    newly_unblocked: NotRequired[list[SlimIssue]]


class ListResponse(TypedDict, Generic[_T]):
    """Unified response for list/query operations.

    - ``items`` is always present (empty list for no results).
    - ``has_more`` is always present (never omitted); callers can
      reliably distinguish "no more" from "field absent."
    - ``next_offset`` is present only when ``has_more=True``.
    """

    items: list[_T]
    has_more: bool
    next_offset: NotRequired[int]
