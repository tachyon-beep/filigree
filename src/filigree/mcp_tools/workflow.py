"""MCP tools for workflow templates, types, packs, transitions, and validation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.types import TextContent, Tool

from filigree.mcp_tools.common import _parse_args, _text
from filigree.types.api import (
    ErrorResponse,
    OutboundTransitionInfo,
    PackListItem,
    StateExplanation,
    ValidationResult,
    WorkflowGuideResponse,
    WorkflowStatesResponse,
)
from filigree.types.inputs import (
    ExplainStateArgs,
    GetTemplateArgs,
    GetTypeInfoArgs,
    GetValidTransitionsArgs,
    GetWorkflowGuideArgs,
    ValidateIssueArgs,
)
from filigree.types.workflow import (
    FieldSchemaInfo,
    StateInfo,
    TransitionInfo,
    TypeInfoResponse,
    TypeListItem,
)


def register() -> tuple[list[Tool], dict[str, Callable[..., Any]]]:
    """Return (tool_definitions, handler_map) for workflow-domain tools."""
    tools = [
        Tool(
            name="get_template",
            description="Get the field schema for an issue type (shows what fields to populate)",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Issue type (bug, task, feature, epic, milestone, phase, step, requirement)",
                    },
                },
                "required": ["type"],
            },
        ),
        Tool(
            name="get_workflow_states",
            description="Return workflow states by category (open/wip/done) from enabled templates.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_types",
            description="List all registered issue types with their workflow info (states, pack, description).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_type_info",
            description="Get full workflow definition for an issue type: states, transitions, fields, enforcement rules.",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Issue type name (e.g. 'bug', 'task', 'feature')"},
                },
                "required": ["type"],
            },
        ),
        Tool(
            name="list_packs",
            description="List all enabled workflow packs with their types and metadata.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_valid_transitions",
            description="Get valid next states for an issue with readiness indicators. Shows which fields are needed before each transition.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="validate_issue",
            description="Validate an issue against its type template. Returns warnings for missing recommended fields. Call get_valid_transitions first to see allowed state changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "Issue ID"},
                },
                "required": ["issue_id"],
            },
        ),
        Tool(
            name="get_workflow_guide",
            description="Get the workflow guide for a pack: state diagram, overview, tips, common mistakes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pack": {"type": "string", "description": "Pack name (e.g. 'core', 'planning', 'engineering')"},
                },
                "required": ["pack"],
            },
        ),
        Tool(
            name="explain_state",
            description="Explain a state within a type's workflow: its category, inbound/outbound transitions, and fields required at this state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Issue type name"},
                    "state": {"type": "string", "description": "State name to explain"},
                },
                "required": ["type", "state"],
            },
        ),
        Tool(
            name="reload_templates",
            description="Reload workflow templates from disk. Use after editing .filigree/templates/ or .filigree/packs/ files.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]

    handlers: dict[str, Callable[..., Any]] = {
        "get_template": _handle_get_template,
        "get_workflow_states": _handle_get_workflow_states,
        "list_types": _handle_list_types,
        "get_type_info": _handle_get_type_info,
        "list_packs": _handle_list_packs,
        "get_valid_transitions": _handle_get_valid_transitions,
        "validate_issue": _handle_validate_issue,
        "get_workflow_guide": _handle_get_workflow_guide,
        "explain_state": _handle_explain_state,
        "reload_templates": _handle_reload_templates,
    }

    return tools, handlers


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_get_template(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, GetTemplateArgs)
    tracker = _get_db()
    tpl = tracker.get_template(args["type"])
    if tpl is None:
        return _text(ErrorResponse(error=f"Unknown template: {args['type']}", code="not_found"))
    return _text(tpl)


async def _handle_get_workflow_states(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    return _text(
        WorkflowStatesResponse(
            states={
                "open": tracker._get_states_for_category("open"),
                "wip": tracker._get_states_for_category("wip"),
                "done": tracker._get_states_for_category("done"),
            }
        )
    )


async def _handle_list_types(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    types_list: list[TypeListItem] = []
    for tt in tracker.templates.list_types():
        types_list.append(
            TypeListItem(
                type=tt.type,
                display_name=tt.display_name,
                description=tt.description,
                pack=tt.pack,
                states=[StateInfo(name=s.name, category=s.category) for s in tt.states],
                initial_state=tt.initial_state,
            )
        )
    return _text(sorted(types_list, key=lambda t: str(t["type"])))


async def _handle_get_type_info(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, GetTypeInfoArgs)
    tracker = _get_db()
    type_tpl = tracker.templates.get_type(args["type"])
    if type_tpl is None:
        return _text(ErrorResponse(error=f"Unknown type: {args['type']}", code="not_found"))
    fields: list[FieldSchemaInfo] = []
    for fd in type_tpl.fields_schema:
        fsi = FieldSchemaInfo(name=fd.name, type=fd.type, description=fd.description)
        if fd.options:
            fsi["options"] = list(fd.options)
        if fd.default is not None:
            fsi["default"] = fd.default
        if fd.required_at:
            fsi["required_at"] = list(fd.required_at)
        if fd.pattern:
            fsi["pattern"] = fd.pattern
        if fd.unique:
            fsi["unique"] = fd.unique
        fields.append(fsi)
    return _text(
        TypeInfoResponse(
            type=type_tpl.type,
            display_name=type_tpl.display_name,
            description=type_tpl.description,
            pack=type_tpl.pack,
            states=[StateInfo(name=s.name, category=s.category) for s in type_tpl.states],
            initial_state=type_tpl.initial_state,
            transitions=[
                TransitionInfo(
                    {"from": td.from_state, "to": td.to_state, "enforcement": td.enforcement, "requires_fields": list(td.requires_fields)}
                )
                for td in type_tpl.transitions
            ],
            fields_schema=fields,
        )
    )


async def _handle_list_packs(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    packs_list: list[PackListItem] = []
    for pack in tracker.templates.list_packs():
        packs_list.append(
            PackListItem(
                pack=pack.pack,
                version=pack.version,
                display_name=pack.display_name,
                description=pack.description,
                types=sorted(pack.types.keys()),
                requires_packs=list(pack.requires_packs),
            )
        )
    return _text(sorted(packs_list, key=lambda p: str(p["pack"])))


async def _handle_get_valid_transitions(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, GetValidTransitionsArgs)
    tracker = _get_db()
    try:
        transitions = tracker.get_valid_transitions(args["issue_id"])
        issue = tracker.get_issue(args["issue_id"])
        tpl_data = tracker.get_template(issue.type)
        field_schemas = {f["name"]: f for f in tpl_data["fields_schema"]} if tpl_data else {}
        return _text(
            [
                {
                    "to": t.to,
                    "category": t.category,
                    "enforcement": t.enforcement,
                    "requires_fields": list(t.requires_fields),
                    "missing_fields": [
                        {
                            "name": f,
                            **{k: v for k, v in field_schemas.get(f, {}).items() if k != "name"},
                        }
                        for f in t.missing_fields
                    ],
                    "ready": t.ready,
                }
                for t in transitions
            ]
        )
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {args['issue_id']}", code="not_found"))


async def _handle_validate_issue(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, ValidateIssueArgs)
    tracker = _get_db()
    try:
        val_result = tracker.validate_issue(args["issue_id"])
        return _text(
            ValidationResult(
                valid=val_result.valid,
                warnings=list(val_result.warnings),
                errors=list(val_result.errors),
            )
        )
    except KeyError:
        return _text(ErrorResponse(error=f"Issue not found: {args['issue_id']}", code="not_found"))


async def _handle_get_workflow_guide(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, GetWorkflowGuideArgs)
    tracker = _get_db()
    wf_pack = tracker.templates.get_pack(args["pack"])
    if wf_pack is None:
        type_tpl = tracker.templates.get_type(args["pack"])
        if type_tpl is not None:
            wf_pack = tracker.templates.get_pack(type_tpl.pack)
            if wf_pack is not None:
                if wf_pack.guide is None:
                    return _text(WorkflowGuideResponse(pack=wf_pack.pack, guide=None, message="No guide available for this pack"))
                return _text(
                    WorkflowGuideResponse(
                        pack=wf_pack.pack,
                        guide=wf_pack.guide,
                        note=f"Resolved type '{args['pack']}' to pack '{wf_pack.pack}'",
                    )
                )
        return _text(
            ErrorResponse(
                error=f"Unknown pack: '{args['pack']}'. Use list_packs to see available packs, or list_types to see types.",
                code="not_found",
            )
        )
    if wf_pack.guide is None:
        return _text(WorkflowGuideResponse(pack=wf_pack.pack, guide=None, message="No guide available for this pack"))
    return _text(WorkflowGuideResponse(pack=wf_pack.pack, guide=wf_pack.guide))


async def _handle_explain_state(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    args = _parse_args(arguments, ExplainStateArgs)
    tracker = _get_db()
    state_tpl = tracker.templates.get_type(args["type"])
    if state_tpl is None:
        return _text(ErrorResponse(error=f"Unknown type: {args['type']}", code="not_found"))
    state_name = args["state"]
    state_def = None
    for s in state_tpl.states:
        if s.name == state_name:
            state_def = s
            break
    if state_def is None:
        return _text(ErrorResponse(error=f"Unknown state '{state_name}' for type '{args['type']}'", code="not_found"))
    inbound = [{"from": td.from_state, "enforcement": td.enforcement} for td in state_tpl.transitions if td.to_state == state_name]
    outbound: list[OutboundTransitionInfo] = [
        OutboundTransitionInfo(to=td.to_state, enforcement=td.enforcement, requires_fields=list(td.requires_fields))
        for td in state_tpl.transitions
        if td.from_state == state_name
    ]
    required_fields = [fd.name for fd in state_tpl.fields_schema if state_name in fd.required_at]
    return _text(
        StateExplanation(
            state=state_name,
            category=state_def.category,
            type=args["type"],
            inbound_transitions=inbound,
            outbound_transitions=outbound,
            required_fields=required_fields,
        )
    )


async def _handle_reload_templates(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db

    tracker = _get_db()
    tracker.reload_templates()
    return _text({"status": "ok"})
