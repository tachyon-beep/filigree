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


# ---------------------------------------------------------------------------
# meta.py handlers
# ---------------------------------------------------------------------------


class AddCommentArgs(TypedDict):
    issue_id: str
    text: str
    actor: NotRequired[str]


class GetCommentsArgs(TypedDict):
    issue_id: str


class AddLabelArgs(TypedDict):
    issue_id: str
    label: str


class RemoveLabelArgs(TypedDict):
    issue_id: str
    label: str


class BatchAddLabelArgs(TypedDict):
    ids: list[str]
    label: str
    actor: NotRequired[str]


class BatchAddCommentArgs(TypedDict):
    ids: list[str]
    text: str
    actor: NotRequired[str]


class GetChangesArgs(TypedDict):
    since: str
    limit: NotRequired[int]


class GetMetricsArgs(TypedDict):
    days: NotRequired[int]


class ExportJsonlArgs(TypedDict):
    output_path: str


class ImportJsonlArgs(TypedDict):
    input_path: str
    merge: NotRequired[bool]


class ArchiveClosedArgs(TypedDict):
    days_old: NotRequired[int]
    actor: NotRequired[str]


class CompactEventsArgs(TypedDict):
    keep_recent: NotRequired[int]


class UndoLastArgs(TypedDict):
    id: str
    actor: NotRequired[str]


class GetIssueEventsArgs(TypedDict):
    issue_id: str
    limit: NotRequired[int]


# ---------------------------------------------------------------------------
# planning.py handlers
# ---------------------------------------------------------------------------


class AddDependencyArgs(TypedDict):
    from_id: str
    to_id: str
    actor: NotRequired[str]


class RemoveDependencyArgs(TypedDict):
    from_id: str
    to_id: str
    actor: NotRequired[str]


class GetPlanArgs(TypedDict):
    milestone_id: str


class StepInput(TypedDict):
    title: str
    priority: NotRequired[int]
    description: NotRequired[str]
    deps: NotRequired[list[Any]]


class PhaseInput(TypedDict):
    title: str
    priority: NotRequired[int]
    description: NotRequired[str]
    steps: NotRequired[list[StepInput]]


class MilestoneInput(TypedDict):
    title: str
    priority: NotRequired[int]
    description: NotRequired[str]


class CreatePlanArgs(TypedDict):
    milestone: MilestoneInput
    phases: list[PhaseInput]
    actor: NotRequired[str]


# ---------------------------------------------------------------------------
# workflow.py handlers
# ---------------------------------------------------------------------------


class GetTemplateArgs(TypedDict):
    type: str


class GetTypeInfoArgs(TypedDict):
    type: str


class GetValidTransitionsArgs(TypedDict):
    issue_id: str


class ValidateIssueArgs(TypedDict):
    issue_id: str


class GetWorkflowGuideArgs(TypedDict):
    pack: str


class ExplainStateArgs(TypedDict):
    type: str
    state: str


# ---------------------------------------------------------------------------
# files.py handlers
# ---------------------------------------------------------------------------


class ListFilesArgs(TypedDict):
    limit: NotRequired[int]
    offset: NotRequired[int]
    language: NotRequired[str]
    path_prefix: NotRequired[str]
    min_findings: NotRequired[int]
    has_severity: NotRequired[str]
    scan_source: NotRequired[str]
    sort: NotRequired[str]
    direction: NotRequired[str]


class GetFileArgs(TypedDict):
    file_id: str


class GetFileTimelineArgs(TypedDict):
    file_id: str
    limit: NotRequired[int]
    offset: NotRequired[int]
    event_type: NotRequired[str]


class GetIssueFilesArgs(TypedDict):
    issue_id: str


class AddFileAssociationArgs(TypedDict):
    file_id: str
    issue_id: str
    assoc_type: str


class RegisterFileArgs(TypedDict):
    path: str
    language: NotRequired[str]
    file_type: NotRequired[str]
    metadata: NotRequired[dict[str, Any]]


class TriggerScanArgs(TypedDict):
    scanner: str
    file_path: str
    api_url: NotRequired[str]


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
    # meta.py
    "add_comment": AddCommentArgs,
    "get_comments": GetCommentsArgs,
    "add_label": AddLabelArgs,
    "remove_label": RemoveLabelArgs,
    "batch_add_label": BatchAddLabelArgs,
    "batch_add_comment": BatchAddCommentArgs,
    "get_changes": GetChangesArgs,
    "get_metrics": GetMetricsArgs,
    "export_jsonl": ExportJsonlArgs,
    "import_jsonl": ImportJsonlArgs,
    "archive_closed": ArchiveClosedArgs,
    "compact_events": CompactEventsArgs,
    "undo_last": UndoLastArgs,
    "get_issue_events": GetIssueEventsArgs,
    # planning.py
    "add_dependency": AddDependencyArgs,
    "remove_dependency": RemoveDependencyArgs,
    "get_plan": GetPlanArgs,
    "create_plan": CreatePlanArgs,
    # workflow.py
    "get_template": GetTemplateArgs,
    "get_type_info": GetTypeInfoArgs,
    "get_valid_transitions": GetValidTransitionsArgs,
    "validate_issue": ValidateIssueArgs,
    "get_workflow_guide": GetWorkflowGuideArgs,
    "explain_state": ExplainStateArgs,
    # files.py
    "list_files": ListFilesArgs,
    "get_file": GetFileArgs,
    "get_file_timeline": GetFileTimelineArgs,
    "get_issue_files": GetIssueFilesArgs,
    "add_file_association": AddFileAssociationArgs,
    "register_file": RegisterFileArgs,
    "trigger_scan": TriggerScanArgs,
}
