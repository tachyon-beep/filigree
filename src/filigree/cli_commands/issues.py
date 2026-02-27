"""CLI commands for issue CRUD: create, show, list, update, close, reopen, claim, undo."""

from __future__ import annotations

import json as json_mod
import sys
from typing import Any

import click

from filigree.cli_common import get_db, refresh_summary


@click.command()
@click.argument("title")
@click.option(
    "--type",
    "issue_type",
    default="task",
    help="Issue type (task, bug, feature, epic, milestone, phase, step, requirement)",
)
@click.option("--priority", "-p", default=2, type=int, help="Priority 0-4 (0=critical)")
@click.option("--parent", default=None, help="Parent issue ID")
@click.option("--assignee", default="", help="Assignee")
@click.option("--description", "-d", default="", help="Description")
@click.option("--notes", default="", help="Notes")
@click.option("--label", "-l", multiple=True, help="Labels (repeatable)")
@click.option("--dep", multiple=True, help="Depends on issue IDs (repeatable)")
@click.option("--field", "-f", multiple=True, help="Custom field as key=value (repeatable)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def create(
    ctx: click.Context,
    title: str,
    issue_type: str,
    priority: int,
    parent: str | None,
    assignee: str,
    description: str,
    notes: str,
    label: tuple[str, ...],
    dep: tuple[str, ...],
    field: tuple[str, ...],
    as_json: bool,
) -> None:
    """Create a new issue."""
    fields = {}
    for f in field:
        if "=" not in f:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Invalid field format: {f} (expected key=value)"}))
            else:
                click.echo(f"Invalid field format: {f} (expected key=value)", err=True)
            sys.exit(1)
        k, v = f.split("=", 1)
        fields[k] = v

    with get_db() as db:
        try:
            issue = db.create_issue(
                title,
                type=issue_type,
                priority=priority,
                parent_id=parent,
                assignee=assignee,
                description=description,
                notes=notes,
                labels=list(label) if label else None,
                deps=list(dep) if dep else None,
                fields=fields or None,
                actor=ctx.obj["actor"],
            )
        except ValueError as e:
            if as_json:
                click.echo(json_mod.dumps({"error": str(e)}))
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        if as_json:
            click.echo(json_mod.dumps(issue.to_dict(), indent=2, default=str))
        else:
            click.echo(f"Created {issue.id}: {issue.title}")
            click.echo("Next: filigree ready")
        refresh_summary(db)


@click.command()
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def show(issue_id: str, as_json: bool) -> None:
    """Show issue details."""
    with get_db() as db:
        try:
            issue = db.get_issue(issue_id)
        except KeyError:
            click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json_mod.dumps(issue.to_dict(), indent=2, default=str))
            return

        click.echo(f"ID:       {issue.id}")
        click.echo(f"Title:    {issue.title}")
        click.echo(f"Status:   {issue.status}")
        click.echo(f"Priority: P{issue.priority}")
        click.echo(f"Type:     {issue.type}")
        if issue.parent_id:
            click.echo(f"Parent:   {issue.parent_id}")
        if issue.assignee:
            click.echo(f"Assignee: {issue.assignee}")
        click.echo(f"Created:  {issue.created_at}")
        if issue.closed_at:
            click.echo(f"Closed:   {issue.closed_at}")
        if issue.labels:
            click.echo(f"Labels:   {', '.join(issue.labels)}")
        if issue.is_ready:
            click.echo("Ready:    YES (no blockers)")
        if issue.blocked_by:
            click.echo(f"Blocked by: {', '.join(issue.blocked_by)}")
        if issue.blocks:
            click.echo(f"Blocks:   {', '.join(issue.blocks)}")
        if issue.children:
            click.echo(f"Children: {', '.join(issue.children)}")
        if issue.description:
            click.echo(f"\n--- Description ---\n{issue.description}")
        if issue.notes:
            click.echo(f"\n--- Notes ---\n{issue.notes}")
        if issue.fields:
            click.echo("\n--- Fields ---")
            for k, v in issue.fields.items():
                click.echo(f"  {k}: {v}")


@click.command("list")
@click.option("--status", default=None, help="Filter by status")
@click.option("--type", "issue_type", default=None, help="Filter by type")
@click.option("--priority", "-p", default=None, type=int, help="Filter by priority")
@click.option("--parent", default=None, help="Filter by parent ID")
@click.option("--assignee", default=None, help="Filter by assignee")
@click.option("--label", default=None, help="Filter by label")
@click.option("--limit", default=100, type=int, help="Max results (default 100)")
@click.option("--offset", default=0, type=int, help="Skip first N results")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_issues(
    status: str | None,
    issue_type: str | None,
    priority: int | None,
    parent: str | None,
    assignee: str | None,
    label: str | None,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    """List issues with optional filters."""
    with get_db() as db:
        issues = db.list_issues(
            status=status,
            type=issue_type,
            priority=priority,
            parent_id=parent,
            assignee=assignee,
            label=label,
            limit=limit,
            offset=offset,
        )

        if as_json:
            click.echo(json_mod.dumps([i.to_dict() for i in issues], indent=2, default=str))
            return

        for issue in issues:
            ready_marker = " *" if issue.is_ready else ""
            click.echo(f"P{issue.priority} {issue.id} [{issue.type}] {issue.status:<12} {issue.title}{ready_marker}")

        click.echo(f"\n{len(issues)} issues")


@click.command()
@click.argument("issue_id")
@click.option("--status", default=None, help="New status")
@click.option("--priority", "-p", default=None, type=int, help="New priority")
@click.option("--title", default=None, help="New title")
@click.option("--assignee", default=None, help="New assignee")
@click.option("--description", "-d", default=None, help="New description")
@click.option("--notes", default=None, help="New notes")
@click.option("--parent", default=None, help="New parent issue ID (empty string to clear)")
@click.option("--design", default=None, help="New design field")
@click.option("--field", "-f", multiple=True, help="Custom field as key=value (repeatable)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def update(
    ctx: click.Context,
    issue_id: str,
    status: str | None,
    priority: int | None,
    title: str | None,
    assignee: str | None,
    description: str | None,
    notes: str | None,
    parent: str | None,
    design: str | None,
    field: tuple[str, ...],
    as_json: bool,
) -> None:
    """Update an issue."""
    fields = None
    if field or design:
        fields = {}
        for f in field:
            if "=" not in f:
                if as_json:
                    click.echo(json_mod.dumps({"error": f"Invalid field format: {f}"}))
                else:
                    click.echo(f"Invalid field format: {f}", err=True)
                sys.exit(1)
            k, v = f.split("=", 1)
            fields[k] = v
        if design:
            fields["design"] = design

    with get_db() as db:
        try:
            issue = db.update_issue(
                issue_id,
                status=status,
                priority=priority,
                title=title,
                assignee=assignee,
                description=description,
                notes=notes,
                parent_id=parent,
                fields=fields,
                actor=ctx.obj["actor"],
            )
            if as_json:
                click.echo(json_mod.dumps(issue.to_dict(), indent=2, default=str))
            else:
                click.echo(f"Updated {issue.id}: {issue.title} [{issue.status}]")
        except KeyError:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
            else:
                click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        except ValueError as e:
            if as_json:
                click.echo(json_mod.dumps({"error": str(e)}))
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        refresh_summary(db)


@click.command()
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--reason", default="", help="Close reason")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def close(ctx: click.Context, issue_ids: tuple[str, ...], reason: str, as_json: bool) -> None:
    """Close one or more issues."""
    with get_db() as db:
        closed: list[dict[str, Any]] = []
        for issue_id in issue_ids:
            try:
                issue = db.close_issue(issue_id, reason=reason, actor=ctx.obj["actor"])
                if as_json:
                    closed.append(dict(issue.to_dict()))
                else:
                    click.echo(f"Closed {issue.id}: {issue.title}")
            except KeyError:
                if as_json:
                    click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
                else:
                    click.echo(f"Not found: {issue_id}", err=True)
                sys.exit(1)
            except ValueError as e:
                if as_json:
                    click.echo(json_mod.dumps({"error": str(e)}))
                else:
                    click.echo(str(e), err=True)
                sys.exit(1)
        if as_json:
            # Include newly-unblocked issues (minimal fields to save tokens)
            closed_ids = {d["id"] for d in closed}
            ready = db.get_ready()
            unblocked = [{"id": i.id, "title": i.title, "priority": i.priority, "type": i.type} for i in ready if i.id not in closed_ids]
            click.echo(json_mod.dumps({"closed": closed, "unblocked": unblocked}, indent=2, default=str))
        refresh_summary(db)


@click.command()
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def reopen(ctx: click.Context, issue_ids: tuple[str, ...], as_json: bool) -> None:
    """Reopen one or more closed issues."""
    with get_db() as db:
        reopened: list[dict[str, Any]] = []
        for issue_id in issue_ids:
            try:
                issue = db.reopen_issue(issue_id, actor=ctx.obj["actor"])
                if as_json:
                    reopened.append(dict(issue.to_dict()))
                else:
                    click.echo(f"Reopened {issue.id}: {issue.title} [{issue.status}]")
            except KeyError:
                if as_json:
                    click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
                else:
                    click.echo(f"Not found: {issue_id}", err=True)
                sys.exit(1)
            except ValueError as e:
                if as_json:
                    click.echo(json_mod.dumps({"error": str(e)}))
                else:
                    click.echo(f"Error reopening {issue_id}: {e}", err=True)
                sys.exit(1)
        if as_json:
            click.echo(json_mod.dumps(reopened, indent=2, default=str))
        refresh_summary(db)


@click.command()
@click.argument("issue_id")
@click.option("--assignee", required=True, help="Who is claiming (agent name)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def claim(ctx: click.Context, issue_id: str, assignee: str, as_json: bool) -> None:
    """Atomically claim an open issue (optimistic locking)."""
    with get_db() as db:
        try:
            issue = db.claim_issue(issue_id, assignee=assignee, actor=ctx.obj["actor"])
        except KeyError:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
            else:
                click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        except ValueError as e:
            if as_json:
                click.echo(json_mod.dumps({"error": str(e)}))
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json_mod.dumps(issue.to_dict(), indent=2, default=str))
        else:
            click.echo(f"Claimed {issue.id}: {issue.title} [{issue.status}] -> {assignee}")
        refresh_summary(db)


@click.command("claim-next")
@click.option("--assignee", required=True, help="Who is claiming")
@click.option("--type", "type_filter", default=None, help="Filter by issue type")
@click.option("--priority-min", default=None, type=int, help="Minimum priority (0=critical)")
@click.option("--priority-max", default=None, type=int, help="Maximum priority")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def claim_next(
    ctx: click.Context,
    assignee: str,
    type_filter: str | None,
    priority_min: int | None,
    priority_max: int | None,
    as_json: bool,
) -> None:
    """Claim the highest-priority ready issue matching filters."""
    with get_db() as db:
        try:
            issue = db.claim_next(
                assignee,
                type_filter=type_filter,
                priority_min=priority_min,
                priority_max=priority_max,
                actor=ctx.obj["actor"],
            )
        except ValueError as e:
            if as_json:
                click.echo(json_mod.dumps({"error": str(e)}))
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        if issue is None:
            if as_json:
                click.echo(json_mod.dumps({"status": "empty"}))
            else:
                click.echo("No issues available")
        else:
            if as_json:
                click.echo(json_mod.dumps(issue.to_dict(), indent=2, default=str))
            else:
                click.echo(f"Claimed {issue.id}: {issue.title} [{issue.status}] -> {assignee}")
        refresh_summary(db)


@click.command("release")
@click.argument("issue_id")
@click.pass_context
def release(ctx: click.Context, issue_id: str) -> None:
    """Release a claimed issue by clearing its assignee."""
    with get_db() as db:
        try:
            issue = db.release_claim(issue_id, actor=ctx.obj["actor"])
            click.echo(f"Released {issue.id}: {issue.title} [{issue.status}]")
        except KeyError:
            click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        refresh_summary(db)


@click.command()
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def undo(ctx: click.Context, issue_id: str, as_json: bool) -> None:
    """Undo the most recent reversible action on an issue."""
    with get_db() as db:
        try:
            result = db.undo_last(issue_id, actor=ctx.obj["actor"])
        except KeyError:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
            else:
                click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json_mod.dumps(result, indent=2, default=str))
            if not result["undone"]:
                sys.exit(1)
        else:
            if result["undone"]:
                click.echo(f"Undone {result['event_type']} (event #{result['event_id']}) on {issue_id}")
            else:
                click.echo(f"Cannot undo: {result['reason']}", err=True)
                sys.exit(1)
        refresh_summary(db)


def register(cli: click.Group) -> None:
    """Register issue commands with the CLI group."""
    cli.add_command(create)
    cli.add_command(show)
    cli.add_command(list_issues, "list")
    cli.add_command(update)
    cli.add_command(close)
    cli.add_command(reopen)
    cli.add_command(claim)
    cli.add_command(claim_next)
    cli.add_command(release)
    cli.add_command(undo)
