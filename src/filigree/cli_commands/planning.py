"""CLI commands for planning: ready, blocked, plan, deps, critical-path, create-plan, changes."""

from __future__ import annotations

import json as json_mod
import sys
from pathlib import Path

import click

from filigree.cli_common import get_db, refresh_summary


@click.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def ready(as_json: bool) -> None:
    """Show issues ready to work on (no blockers)."""
    with get_db() as db:
        issues = db.get_ready()

        if as_json:
            click.echo(json_mod.dumps([i.to_dict() for i in issues], indent=2, default=str))
            return

        for issue in issues:
            parent_ctx = ""
            if issue.parent_id:
                try:
                    parent = db.get_issue(issue.parent_id)
                    parent_ctx = f" ({parent.title})"
                except KeyError:
                    pass
            click.echo(f'P{issue.priority} {issue.id} [{issue.type}] "{issue.title}"{parent_ctx}')
        click.echo(f"\n{len(issues)} ready")


@click.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def blocked(as_json: bool) -> None:
    """Show blocked issues."""
    with get_db() as db:
        issues = db.get_blocked()

        if as_json:
            click.echo(json_mod.dumps([i.to_dict() for i in issues], indent=2, default=str))
            return

        for issue in issues:
            blockers = ", ".join(issue.blocked_by)
            click.echo(f'P{issue.priority} {issue.id} [{issue.type}] "{issue.title}" <- {blockers}')
        click.echo(f"\n{len(issues)} blocked")


@click.command()
@click.argument("milestone_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def plan(milestone_id: str, as_json: bool) -> None:
    """Show milestone plan tree with progress."""
    with get_db() as db:
        try:
            p = db.get_plan(milestone_id)
        except KeyError:
            click.echo(f"Not found: {milestone_id}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json_mod.dumps(p, indent=2, default=str))
            return

        ms = p["milestone"]
        total = p["total_steps"]
        done = p["completed_steps"]
        click.echo(f"Milestone: {ms['title']} ({done}/{total} steps complete)")
        click.echo()

        for phase_data in p["phases"]:
            phase = phase_data["phase"]
            p_total = phase_data["total"]
            p_done = phase_data["completed"]

            if p_done == p_total and p_total > 0:
                marker = "[DONE]"
            elif phase["status"] == "in_progress":
                marker = "[WIP] "
            else:
                marker = "[    ]"

            click.echo(f"  {marker} {phase['title']} ({p_done}/{p_total})")

            for step_dict in phase_data["steps"]:
                status_icon = {"open": " ", "in_progress": ">", "closed": "x"}
                icon = status_icon.get(step_dict["status"], "?")
                ready_mark = " *" if step_dict["is_ready"] else ""
                click.echo(f"    [{icon}] {step_dict['id']} {step_dict['title']}{ready_mark}")


@click.command("add-dep")
@click.argument("issue_id")
@click.argument("depends_on_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def add_dep(ctx: click.Context, issue_id: str, depends_on_id: str, as_json: bool) -> None:
    """Add dependency: issue_id depends on depends_on_id."""
    with get_db() as db:
        try:
            added = db.add_dependency(issue_id, depends_on_id, actor=ctx.obj["actor"])
            status = "added" if added else "already_exists"
            if as_json:
                click.echo(json_mod.dumps({"from_id": issue_id, "to_id": depends_on_id, "status": status}))
            else:
                if added:
                    click.echo(f"Added: {issue_id} depends on {depends_on_id}")
                else:
                    click.echo(f"Already exists: {issue_id} depends on {depends_on_id}")
        except KeyError as e:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {e}"}))
            else:
                click.echo(f"Not found: {e}", err=True)
            sys.exit(1)
        except ValueError as e:
            if as_json:
                click.echo(json_mod.dumps({"error": str(e)}))
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        refresh_summary(db)


@click.command("remove-dep")
@click.argument("issue_id")
@click.argument("depends_on_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def remove_dep(ctx: click.Context, issue_id: str, depends_on_id: str, as_json: bool) -> None:
    """Remove dependency."""
    with get_db() as db:
        removed = db.remove_dependency(issue_id, depends_on_id, actor=ctx.obj["actor"])
        status = "removed" if removed else "not_found"
        if as_json:
            click.echo(json_mod.dumps({"from_id": issue_id, "to_id": depends_on_id, "status": status}))
        else:
            if removed:
                click.echo(f"Removed: {issue_id} no longer depends on {depends_on_id}")
            else:
                click.echo(f"No dependency found: {issue_id} -> {depends_on_id}")
        refresh_summary(db)


@click.command("critical-path")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def critical_path(as_json: bool) -> None:
    """Show the longest dependency chain among open issues."""
    with get_db() as db:
        path = db.get_critical_path()

    if as_json:
        click.echo(json_mod.dumps({"path": path, "length": len(path)}, indent=2))
        return

    if not path:
        click.echo("No dependency chains found.")
        return

    click.echo(f"Critical path ({len(path)} issues):")
    for i, item in enumerate(path):
        prefix = "  -> " if i > 0 else "  "
        click.echo(f'{prefix}P{item["priority"]} {item["id"]} [{item["type"]}] "{item["title"]}"')


@click.command("create-plan")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="JSON file (stdin if omitted)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def create_plan(ctx: click.Context, file_path: str | None, as_json: bool) -> None:
    """Create a milestone/phase/step hierarchy from JSON.

    Reads JSON from --file or stdin. Structure:
    {"milestone": {"title": "..."}, "phases": [{"title": "...", "steps": [...]}]}
    """
    try:
        raw = Path(file_path).read_text() if file_path else click.get_text_stream("stdin").read()
    except (OSError, UnicodeDecodeError) as e:
        click.echo(f"Error reading file: {e}", err=True)
        sys.exit(1)

    try:
        data = json_mod.loads(raw)
    except json_mod.JSONDecodeError as e:
        click.echo(f"Invalid JSON: {e}", err=True)
        sys.exit(1)

    if not isinstance(data, dict):
        click.echo("JSON must be an object, not a list or scalar", err=True)
        sys.exit(1)

    if "milestone" not in data or "phases" not in data:
        click.echo("JSON must contain 'milestone' and 'phases' keys", err=True)
        sys.exit(1)

    if not isinstance(data["milestone"], dict):
        click.echo("'milestone' must be an object with at least a 'title' key", err=True)
        sys.exit(1)

    if not isinstance(data["phases"], list):
        click.echo("'phases' must be a list of phase objects", err=True)
        sys.exit(1)

    for i, phase in enumerate(data["phases"]):
        if not isinstance(phase, dict):
            click.echo(f"Phase {i + 1} must be an object, got {type(phase).__name__}", err=True)
            sys.exit(1)

    with get_db() as db:
        try:
            result = db.create_plan(data["milestone"], data["phases"], actor=ctx.obj["actor"])
        except (ValueError, IndexError, TypeError, AttributeError) as e:
            if as_json:
                click.echo(json_mod.dumps({"error": str(e)}))
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json_mod.dumps(result, indent=2, default=str))
        else:
            ms = result["milestone"]
            click.echo(f"Created plan: {ms['title']} ({ms['id']})")
            for phase_data in result["phases"]:
                phase = phase_data["phase"]
                step_count = len(phase_data["steps"])
                click.echo(f"  Phase: {phase['title']} ({step_count} steps)")
        refresh_summary(db)


@click.command("changes")
@click.option("--since", required=True, help="ISO timestamp to get events after")
@click.option("--limit", default=100, type=int, help="Max events (default 100)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def changes(since: str, limit: int, as_json: bool) -> None:
    """Get events since a timestamp (for session resumption)."""
    with get_db() as db:
        events = db.get_events_since(since, limit=limit)

        if as_json:
            click.echo(json_mod.dumps(events, indent=2, default=str))
            return

        if not events:
            click.echo("No events since that timestamp.")
            return

        for ev in events:
            title = ev.get("issue_title", "")
            actor_str = f" by {ev['actor']}" if ev.get("actor") else ""
            click.echo(f"  {ev['created_at']}  {ev['event_type']:<12} {ev['issue_id']}  {title}{actor_str}")
        click.echo(f"\n{len(events)} events")


def register(cli: click.Group) -> None:
    """Register planning commands with the CLI group."""
    cli.add_command(ready)
    cli.add_command(blocked)
    cli.add_command(plan)
    cli.add_command(add_dep)
    cli.add_command(remove_dep)
    cli.add_command(critical_path)
    cli.add_command(create_plan)
    cli.add_command(changes)
