"""CLI commands for workflow: templates, types, transitions, packs, validate, guide, explain-state."""

from __future__ import annotations

import json as json_mod
import sys
from typing import Any

import click

from filigree.cli_common import get_db


@click.group(invoke_without_command=True)
@click.option("--type", "issue_type", default=None, help="Show specific template")
@click.pass_context
def templates(ctx: click.Context, issue_type: str | None) -> None:
    """Show available issue templates."""
    if ctx.invoked_subcommand is not None:
        return
    with get_db() as db:
        if issue_type:
            tpl = db.get_template(issue_type)
            if not tpl:
                click.echo(f"Unknown template: {issue_type}", err=True)
                sys.exit(1)
            click.echo(f"{tpl['display_name']} ({tpl['type']})")
            click.echo(f"  {tpl['description']}")
            click.echo("\n  Fields:")
            for f in tpl["fields_schema"]:
                req = " (required)" if f.get("required") else ""
                click.echo(f"    {f['name']}: {f['type']}{req} — {f['description']}")
        else:
            for tpl in db.list_templates():
                click.echo(f"  {tpl['type']:<15} {tpl['display_name']}")


@templates.command("reload")
def templates_reload() -> None:
    """Reload workflow templates from disk."""
    with get_db() as db:
        db.reload_templates()
        click.echo("Templates reloaded")


@click.command("workflow-states")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def workflow_states(as_json: bool) -> None:
    """Show workflow states by category from enabled templates."""
    with get_db() as db:
        data = {}
        for category in ("open", "wip", "done"):
            data[category] = list(db._get_states_for_category(category))
        if as_json:
            click.echo(json_mod.dumps(data, indent=2))
            return
        for category, states in data.items():
            click.echo(f"{category}: {', '.join(states) if states else '(none)'}")


@click.command("types")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def types_cmd(as_json: bool) -> None:
    """List all registered issue types with pack info."""
    with get_db() as db:
        types_list = []
        for tpl in db.templates.list_types():
            types_list.append(
                {
                    "type": tpl.type,
                    "display_name": tpl.display_name,
                    "description": tpl.description,
                    "pack": tpl.pack,
                    "states": [s.name for s in tpl.states],
                }
            )
        types_list.sort(key=lambda t: str(t["type"]))

        if as_json:
            click.echo(json_mod.dumps(types_list, indent=2))
            return

        for t in types_list:
            states = " → ".join(t["states"])
            click.echo(f"  {t['type']:<15} [{t['pack']}] {states}")


@click.command("type-info")
@click.argument("type_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def type_info(type_name: str, as_json: bool) -> None:
    """Show full workflow definition for an issue type."""
    with get_db() as db:
        tpl = db.templates.get_type(type_name)
        if tpl is None:
            click.echo(f"Unknown type: {type_name}", err=True)
            sys.exit(1)

        if as_json:
            data = {
                "type": tpl.type,
                "display_name": tpl.display_name,
                "description": tpl.description,
                "pack": tpl.pack,
                "states": [{"name": s.name, "category": s.category} for s in tpl.states],
                "initial_state": tpl.initial_state,
                "transitions": [
                    {
                        "from": t.from_state,
                        "to": t.to_state,
                        "enforcement": t.enforcement,
                        "requires_fields": list(t.requires_fields),
                    }
                    for t in tpl.transitions
                ],
                "fields_schema": [{"name": f.name, "type": f.type, "description": f.description} for f in tpl.fields_schema],
            }
            click.echo(json_mod.dumps(data, indent=2))
            return

        click.echo(f"{tpl.display_name} ({tpl.type}) — {tpl.pack} pack")
        click.echo(f"  {tpl.description}")
        click.echo("\n  States:")
        for s in tpl.states:
            initial = " (initial)" if s.name == tpl.initial_state else ""
            click.echo(f"    {s.name:<20} [{s.category}]{initial}")
        click.echo("\n  Transitions:")
        for t in tpl.transitions:
            fields_note = f" (requires: {', '.join(t.requires_fields)})" if t.requires_fields else ""
            click.echo(f"    {t.from_state} → {t.to_state}  [{t.enforcement}]{fields_note}")
        if tpl.fields_schema:
            click.echo("\n  Fields:")
            for f in tpl.fields_schema:
                req_at = f" (required at: {', '.join(f.required_at)})" if f.required_at else ""
                click.echo(f"    {f.name}: {f.type} — {f.description}{req_at}")


@click.command("transitions")
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def transitions_cmd(issue_id: str, as_json: bool) -> None:
    """Show valid next states for an issue."""
    with get_db() as db:
        try:
            transitions = db.get_valid_transitions(issue_id)
        except KeyError:
            click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(
                json_mod.dumps(
                    [
                        {
                            "to": t.to,
                            "category": t.category,
                            "enforcement": t.enforcement,
                            "requires_fields": list(t.requires_fields),
                            "missing_fields": list(t.missing_fields),
                            "ready": t.ready,
                        }
                        for t in transitions
                    ],
                    indent=2,
                )
            )
            return

        if not transitions:
            click.echo("No transitions available (unknown type or terminal state)")
            return

        issue = db.get_issue(issue_id)
        click.echo(f"Transitions from '{issue.status}' ({issue.type}):")
        for t in transitions:
            ready_mark = " READY" if t.ready else ""
            missing = f" (missing: {', '.join(t.missing_fields)})" if t.missing_fields else ""
            click.echo(f"  → {t.to:<20} [{t.category}] {t.enforcement}{missing}{ready_mark}")


@click.command("packs")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def packs_cmd(as_json: bool) -> None:
    """List enabled workflow packs."""
    with get_db() as db:
        packs = db.templates.list_packs()

        if as_json:
            click.echo(
                json_mod.dumps(
                    [
                        {
                            "pack": p.pack,
                            "version": p.version,
                            "display_name": p.display_name,
                            "description": p.description,
                            "types": sorted(p.types.keys()),
                        }
                        for p in sorted(packs, key=lambda p: p.pack)
                    ],
                    indent=2,
                )
            )
            return

        for p in sorted(packs, key=lambda p: p.pack):
            type_names = ", ".join(sorted(p.types.keys()))
            click.echo(f"  {p.pack:<15} v{p.version}  {type_names}")


@click.command("validate")
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def validate_cmd(issue_id: str, as_json: bool) -> None:
    """Validate an issue against its type template."""
    with get_db() as db:
        try:
            result = db.validate_issue(issue_id)
        except KeyError:
            click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(
                json_mod.dumps(
                    {
                        "valid": result.valid,
                        "warnings": list(result.warnings),
                        "errors": list(result.errors),
                    },
                    indent=2,
                )
            )
            return

        if result.valid and not result.warnings:
            click.echo(f"{issue_id}: valid (no warnings)")
        elif result.valid:
            click.echo(f"{issue_id}: valid with warnings:")
            for w in result.warnings:
                click.echo(f"  ! {w}")
        else:
            click.echo(f"{issue_id}: INVALID")
            for e in result.errors:
                click.echo(f"  X {e}")


@click.command("guide")
@click.argument("pack_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def guide_cmd(pack_name: str, as_json: bool) -> None:
    """Display workflow guide for a pack."""
    with get_db() as db:
        pack = db.templates.get_pack(pack_name)
        if pack is None:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Unknown pack: {pack_name}"}))
            else:
                click.echo(f"Unknown pack: {pack_name}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json_mod.dumps({"pack": pack_name, "guide": pack.guide}, indent=2, default=str))
            return

        if pack.guide is None:
            click.echo(f"No guide available for pack '{pack_name}'")
            return

        guide = pack.guide
        if "overview" in guide:
            click.echo(f"# {pack.display_name} Guide\n")
            click.echo(guide["overview"])
        if "state_diagram" in guide:
            click.echo(f"\n## State Diagram\n{guide['state_diagram']}")
        if "when_to_use" in guide:
            click.echo(f"\n## When to Use\n{guide['when_to_use']}")
        if "tips" in guide:
            click.echo("\n## Tips")
            for tip in guide["tips"]:
                click.echo(f"  - {tip}")
        if "common_mistakes" in guide:
            click.echo("\n## Common Mistakes")
            for mistake in guide["common_mistakes"]:
                click.echo(f"  - {mistake}")


@click.command("explain-state")
@click.argument("type_name")
@click.argument("state_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def explain_state(type_name: str, state_name: str, as_json: bool) -> None:
    """Explain a state's transitions and required fields."""
    with get_db() as db:
        tpl = db.templates.get_type(type_name)
        if tpl is None:
            click.echo(f"Unknown type: {type_name}", err=True)
            sys.exit(1)

        state_def = None
        for s in tpl.states:
            if s.name == state_name:
                state_def = s
                break
        if state_def is None:
            click.echo(f"Unknown state '{state_name}' for type '{type_name}'", err=True)
            sys.exit(1)

        inbound = [{"from": t.from_state, "enforcement": t.enforcement} for t in tpl.transitions if t.to_state == state_name]
        outbound: list[dict[str, Any]] = [
            {"to": t.to_state, "enforcement": t.enforcement, "requires_fields": list(t.requires_fields)}
            for t in tpl.transitions
            if t.from_state == state_name
        ]
        required_fields = [f.name for f in tpl.fields_schema if state_name in f.required_at]

        if as_json:
            click.echo(
                json_mod.dumps(
                    {
                        "state": state_name,
                        "category": state_def.category,
                        "type": type_name,
                        "inbound_transitions": inbound,
                        "outbound_transitions": outbound,
                        "required_fields": required_fields,
                    },
                    indent=2,
                )
            )
            return

        click.echo(f"State: {state_name} [{state_def.category}] (type: {type_name})")
        if inbound:
            click.echo("\nInbound transitions:")
            for t in inbound:
                click.echo(f"  <- {t['from']} [{t['enforcement']}]")
        else:
            click.echo("\nNo inbound transitions (initial state)")
        if outbound:
            click.echo("\nOutbound transitions:")
            for ot in outbound:
                req_fields = ot["requires_fields"]
                fields_note = f" (requires: {', '.join(req_fields)})" if req_fields else ""
                click.echo(f"  -> {ot['to']} [{ot['enforcement']}]{fields_note}")
        else:
            click.echo("\nNo outbound transitions (terminal state)")
        if required_fields:
            click.echo(f"\nRequired fields at this state: {', '.join(required_fields)}")


def register(cli: click.Group) -> None:
    """Register workflow commands with the CLI group."""
    cli.add_command(templates)
    cli.add_command(workflow_states)
    cli.add_command(types_cmd)
    cli.add_command(type_info)
    cli.add_command(transitions_cmd)
    cli.add_command(packs_cmd)
    cli.add_command(validate_cmd)
    cli.add_command(guide_cmd)
    cli.add_command(explain_state)
