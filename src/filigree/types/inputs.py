# IMPORT CONSTRAINT: types/ modules must only import from typing, stdlib, and each other.
# NEVER import from core.py, db_base.py, or any mixin — this prevents circular imports.
"""TypedDict contracts for MCP tool handler input arguments.

Each TypedDict mirrors the JSON Schema ``inputSchema`` on the corresponding
``mcp.types.Tool`` definition.  The ``TOOL_ARGS_MAP`` registry maps tool names
to their TypedDict class so the sync test can verify structural agreement.

Safety note on cast():
    The MCP SDK validates argument presence/types against JSON Schema before
    handler invocation.  Core validates authoritatively.  The TypedDicts here
    are a *static-analysis* tool — ``cast()`` provides type narrowing only,
    not runtime validation.  Direct handler calls that bypass MCP SDK
    validation are unsafe — callers must pre-validate arguments.
"""

# NOTE: Do NOT add ``from __future__ import annotations`` to this module.
# It breaks TypedDict.__required_keys__ / __optional_keys__ introspection
# on Python <3.14, which the sync test in test_input_type_contracts.py
# depends on for verifying required/optional agreement with JSON Schema.

from typing import Any, NotRequired, TypedDict

# ---------------------------------------------------------------------------
# issues.py handlers
# ---------------------------------------------------------------------------


class GetIssueArgs(TypedDict):
    id: str
    include_transitions: NotRequired[bool]


class ListIssuesArgs(TypedDict):
    status: NotRequired[str]
    status_category: NotRequired[str]
    type: NotRequired[str]
    priority: NotRequired[int]
    parent_id: NotRequired[str]
    assignee: NotRequired[str]
    label: NotRequired[str]
    limit: NotRequired[int]
    offset: NotRequired[int]
    no_limit: NotRequired[bool]


class CreateIssueArgs(TypedDict):
    title: str
    type: NotRequired[str]
    priority: NotRequired[int]
    parent_id: NotRequired[str]
    description: NotRequired[str]
    notes: NotRequired[str]
    fields: NotRequired[dict[str, Any]]
    labels: NotRequired[list[str]]
    deps: NotRequired[list[str]]
    actor: NotRequired[str]


class UpdateIssueArgs(TypedDict):
    id: str
    status: NotRequired[str]
    priority: NotRequired[int]
    title: NotRequired[str]
    assignee: NotRequired[str]
    description: NotRequired[str]
    notes: NotRequired[str]
    parent_id: NotRequired[str]
    fields: NotRequired[dict[str, Any]]
    actor: NotRequired[str]


class CloseIssueArgs(TypedDict):
    id: str
    reason: NotRequired[str]
    actor: NotRequired[str]
    fields: NotRequired[dict[str, Any]]


class ReopenIssueArgs(TypedDict):
    id: str
    actor: NotRequired[str]


class SearchIssuesArgs(TypedDict):
    query: str
    limit: NotRequired[int]
    offset: NotRequired[int]
    no_limit: NotRequired[bool]


class ClaimIssueArgs(TypedDict):
    id: str
    assignee: str
    actor: NotRequired[str]


class ReleaseClaimArgs(TypedDict):
    id: str
    actor: NotRequired[str]


class ClaimNextArgs(TypedDict):
    assignee: str
    type: NotRequired[str]
    priority_min: NotRequired[int]
    priority_max: NotRequired[int]
    actor: NotRequired[str]


class BatchCloseArgs(TypedDict):
    ids: list[str]
    reason: NotRequired[str]
    actor: NotRequired[str]


class BatchUpdateArgs(TypedDict):
    ids: list[str]
    status: NotRequired[str]
    priority: NotRequired[int]
    assignee: NotRequired[str]
    fields: NotRequired[dict[str, Any]]
    actor: NotRequired[str]


# Registry: tool_name -> TypedDict class.
# Populated as TypedDicts are defined below.
# No-argument tools (empty inputSchema properties) are intentionally excluded.
TOOL_ARGS_MAP: dict[str, type] = {
    # issues.py
    "get_issue": GetIssueArgs,
    "list_issues": ListIssuesArgs,
    "create_issue": CreateIssueArgs,
    "update_issue": UpdateIssueArgs,
    "close_issue": CloseIssueArgs,
    "reopen_issue": ReopenIssueArgs,
    "search_issues": SearchIssuesArgs,
    "claim_issue": ClaimIssueArgs,
    "release_claim": ReleaseClaimArgs,
    "claim_next": ClaimNextArgs,
    "batch_close": BatchCloseArgs,
    "batch_update": BatchUpdateArgs,
}
