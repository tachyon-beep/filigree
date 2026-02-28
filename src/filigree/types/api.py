"""TypedDicts for MCP tool handler and dashboard route API responses."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from filigree.types.core import IssueDict
from filigree.types.planning import CommentRecord, PlanPhase, StatsResult

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
    created_at: str


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

    milestone: dict[str, Any]
    phases: list[PlanPhase]
    total_steps: int
    completed_steps: int
    progress_pct: float
