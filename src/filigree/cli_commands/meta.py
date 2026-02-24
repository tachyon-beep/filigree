"""CLI commands for metadata: comments, labels, stats, search, events, batch ops."""

from __future__ import annotations

import json as json_mod
import sys

import click

from filigree.cli_common import get_db, refresh_summary


@click.command("add-comment")
@click.argument("issue_id")
@click.argument("text")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def add_comment(ctx: click.Context, issue_id: str, text: str, as_json: bool) -> None:
    """Add a comment to an issue."""
    with get_db() as db:
        try:
            db.get_issue(issue_id)
        except KeyError:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
            else:
                click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        try:
            comment_id = db.add_comment(issue_id, text, author=ctx.obj["actor"])
        except ValueError as e:
            if as_json:
                click.echo(json_mod.dumps({"error": str(e)}))
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        if as_json:
            click.echo(json_mod.dumps({"comment_id": comment_id, "issue_id": issue_id}))
        else:
            click.echo(f"Added comment {comment_id} to {issue_id}")


@click.command("get-comments")
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def get_comments(issue_id: str, as_json: bool) -> None:
    """List comments on an issue."""
    with get_db() as db:
        try:
            db.get_issue(issue_id)
        except KeyError:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
            else:
                click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        result = db.get_comments(issue_id)
        if as_json:
            click.echo(json_mod.dumps(result, indent=2, default=str))
            return
        if not result:
            click.echo("No comments.")
            return
        for c in result:
            click.echo(f"[{c['created_at']}] {c['author']}: {c['text']}")


@click.command("add-label")
@click.argument("issue_id")
@click.argument("label_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def add_label(issue_id: str, label_name: str, as_json: bool) -> None:
    """Add a label to an issue."""
    with get_db() as db:
        try:
            db.get_issue(issue_id)
        except KeyError:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
            else:
                click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        try:
            added = db.add_label(issue_id, label_name)
        except ValueError as e:
            if as_json:
                click.echo(json_mod.dumps({"error": str(e)}))
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        status = "added" if added else "already_exists"
        if as_json:
            click.echo(json_mod.dumps({"issue_id": issue_id, "label": label_name, "status": status}))
        else:
            if added:
                click.echo(f"Added label '{label_name}' to {issue_id}")
            else:
                click.echo(f"Label '{label_name}' already on {issue_id}")
        refresh_summary(db)


@click.command("remove-label")
@click.argument("issue_id")
@click.argument("label_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def remove_label(issue_id: str, label_name: str, as_json: bool) -> None:
    """Remove a label from an issue."""
    with get_db() as db:
        try:
            db.get_issue(issue_id)
        except KeyError:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
            else:
                click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        removed = db.remove_label(issue_id, label_name)
        status = "removed" if removed else "not_found"
        if as_json:
            click.echo(json_mod.dumps({"issue_id": issue_id, "label": label_name, "status": status}))
        else:
            if removed:
                click.echo(f"Removed label '{label_name}' from {issue_id}")
            else:
                click.echo(f"Label '{label_name}' not found on {issue_id}")
        refresh_summary(db)


@click.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def stats(as_json: bool) -> None:
    """Show project statistics."""
    with get_db() as db:
        s = db.get_stats()

        if as_json:
            click.echo(json_mod.dumps(s, indent=2, default=str))
            return

        click.echo("Status:")
        for status, count in sorted(s["by_status"].items()):
            click.echo(f"  {status}: {count}")
        click.echo("\nTypes:")
        for t, count in sorted(s["by_type"].items()):
            click.echo(f"  {t}: {count}")
        click.echo(f"\nReady: {s['ready_count']}")
        click.echo(f"Blocked: {s['blocked_count']}")
        click.echo(f"Dependencies: {s['total_dependencies']}")


@click.command()
@click.argument("query")
@click.option("--limit", default=100, type=int, help="Max results (default 100)")
@click.option("--offset", default=0, type=int, help="Skip first N results")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def search(query: str, limit: int, offset: int, as_json: bool) -> None:
    """Search issues by title/description."""
    with get_db() as db:
        issues = db.search_issues(query, limit=limit, offset=offset)

        if as_json:
            click.echo(json_mod.dumps([i.to_dict() for i in issues], indent=2, default=str))
            return

        for issue in issues:
            click.echo(f"P{issue.priority} {issue.id} [{issue.type}] {issue.status:<12} {issue.title}")
        click.echo(f"\n{len(issues)} results")


@click.command("events")
@click.argument("issue_id")
@click.option("--limit", default=50, type=int, help="Max events (default 50)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def events_cmd(issue_id: str, limit: int, as_json: bool) -> None:
    """Get event history for a specific issue, newest first."""
    with get_db() as db:
        try:
            event_list = db.get_issue_events(issue_id, limit=limit)
        except KeyError:
            click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json_mod.dumps(event_list, indent=2, default=str))
            return

        if not event_list:
            click.echo(f"No events for {issue_id}.")
            return

        for ev in event_list:
            old_val = ev.get("old_value", "")
            new_val = ev.get("new_value", "")
            detail = ""
            if old_val or new_val:
                detail = f" ({old_val} -> {new_val})" if old_val else f" ({new_val})"
            actor_str = f" by {ev['actor']}" if ev.get("actor") else ""
            click.echo(f"  #{ev['id']}  {ev['created_at']}  {ev['event_type']}{detail}{actor_str}")
        click.echo(f"\n{len(event_list)} events")


@click.command("batch-update")
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--status", default=None, help="New status")
@click.option("--priority", "-p", default=None, type=int, help="New priority")
@click.option("--assignee", default=None, help="New assignee")
@click.option("--field", "-f", multiple=True, help="Custom field as key=value (repeatable)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def batch_update(
    ctx: click.Context,
    issue_ids: tuple[str, ...],
    status: str | None,
    priority: int | None,
    assignee: str | None,
    field: tuple[str, ...],
    as_json: bool,
) -> None:
    """Update multiple issues with the same changes."""
    fields = None
    if field:
        fields = {}
        for f in field:
            if "=" not in f:
                if as_json:
                    click.echo(json_mod.dumps({"error": f"Invalid field format: {f} (expected key=value)"}))
                else:
                    click.echo(f"Invalid field format: {f}", err=True)
                sys.exit(1)
            k, v = f.split("=", 1)
            fields[k] = v

    with get_db() as db:
        results, errors = db.batch_update(
            list(issue_ids),
            status=status,
            priority=priority,
            assignee=assignee,
            fields=fields,
            actor=ctx.obj["actor"],
        )
        if as_json:
            click.echo(
                json_mod.dumps(
                    {
                        "updated": [i.to_dict() for i in results],
                        "errors": errors,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            for issue in results:
                click.echo(f"  Updated {issue.id}: {issue.title}")
            for err in errors:
                click.echo(f"  Error {err['id']}: {err['error']}", err=True)
                if "valid_transitions" in err:
                    valid = ", ".join(t["to"] for t in err["valid_transitions"])
                    click.echo(f"    Valid transitions: {valid}", err=True)
            click.echo(f"Updated {len(results)} issues")
        refresh_summary(db)


@click.command("batch-close")
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--reason", default="", help="Close reason")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def batch_close(ctx: click.Context, issue_ids: tuple[str, ...], reason: str, as_json: bool) -> None:
    """Close multiple issues with per-item error reporting."""
    with get_db() as db:
        closed, errors = db.batch_close(
            list(issue_ids),
            reason=reason,
            actor=ctx.obj["actor"],
        )

        if as_json:
            click.echo(
                json_mod.dumps(
                    {
                        "closed": [{"id": i.id, "title": i.title, "priority": i.priority, "type": i.type} for i in closed],
                        "errors": errors,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            for issue in closed:
                click.echo(f"  Closed {issue.id}: {issue.title}")
            for err in errors:
                click.echo(f"  Error {err['id']}: {err['error']}", err=True)
                if "valid_transitions" in err:
                    valid = ", ".join(t["to"] for t in err["valid_transitions"])
                    click.echo(f"    Valid transitions: {valid}", err=True)
            click.echo(f"Closed {len(closed)}/{len(issue_ids)} issues")
        refresh_summary(db)


@click.command("batch-add-label")
@click.argument("label_name")
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def batch_add_label(label_name: str, issue_ids: tuple[str, ...], as_json: bool) -> None:
    """Add the same label to multiple issues."""
    with get_db() as db:
        labeled, errors = db.batch_add_label(list(issue_ids), label=label_name)

        if as_json:
            click.echo(
                json_mod.dumps(
                    {
                        "labeled": labeled,
                        "errors": errors,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            for row in labeled:
                if row["status"] == "added":
                    click.echo(f"  Added label '{label_name}' to {row['id']}")
                else:
                    click.echo(f"  Label '{label_name}' already on {row['id']}")
            for err in errors:
                click.echo(f"  Error {err['id']}: {err['error']}", err=True)
            click.echo(f"Labeled {len(labeled)}/{len(issue_ids)} issues")
        refresh_summary(db)


@click.command("batch-add-comment")
@click.argument("text")
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def batch_add_comment(ctx: click.Context, text: str, issue_ids: tuple[str, ...], as_json: bool) -> None:
    """Add the same comment to multiple issues."""
    with get_db() as db:
        commented, errors = db.batch_add_comment(
            list(issue_ids),
            text=text,
            author=ctx.obj["actor"],
        )

        if as_json:
            click.echo(
                json_mod.dumps(
                    {
                        "commented": commented,
                        "errors": errors,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            for row in commented:
                click.echo(f"  Added comment {row['comment_id']} to {row['id']}")
            for err in errors:
                click.echo(f"  Error {err['id']}: {err['error']}", err=True)
            click.echo(f"Commented on {len(commented)}/{len(issue_ids)} issues")
        refresh_summary(db)


def register(cli: click.Group) -> None:
    """Register metadata commands with the CLI group."""
    cli.add_command(add_comment)
    cli.add_command(get_comments)
    cli.add_command(add_label)
    cli.add_command(remove_label)
    cli.add_command(stats)
    cli.add_command(search)
    cli.add_command(events_cmd)
    cli.add_command(batch_update)
    cli.add_command(batch_close)
    cli.add_command(batch_add_label)
    cli.add_command(batch_add_comment)
