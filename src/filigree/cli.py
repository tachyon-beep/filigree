"""CLI for the filigree issue tracker.

Convention-based: discovers .filigree/ by walking up from cwd.

Usage:
    filigree init                                # Initialize .filigree/ in cwd
    filigree install                             # Install MCP + instructions
    filigree doctor                              # Health check
    filigree create "Fix the bug" --type=bug     # Create issue
    filigree show <id>                           # Show issue details
    filigree list --status=open                  # List issues
    filigree update <id> --status=in_progress    # Update issue
    filigree close <id>                          # Close issue
    filigree reopen <id>                         # Reopen closed issue
    filigree ready                               # Show ready issues
    filigree add-comment <id> "text"             # Add comment
    filigree get-comments <id>                   # List comments
    filigree add-label <id> <label>              # Add label
    filigree remove-label <id> <label>           # Remove label
    filigree stats                               # Project statistics
    filigree search "query"                      # Search issues
    filigree migrate --from-beads                # Migrate from beads
"""

from __future__ import annotations

import json as json_mod
import sys
from pathlib import Path
from typing import Any

import click

from filigree import __version__
from filigree.core import (
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
    find_filigree_root,
    read_config,
    write_config,
)
from filigree.summary import write_summary


def _get_db() -> FiligreeDB:
    """Discover .filigree/ and return an initialized FiligreeDB."""
    try:
        filigree_dir = find_filigree_root()
    except FileNotFoundError:
        click.echo(f"No {FILIGREE_DIR_NAME}/ found. Run 'filigree init' first.", err=True)
        sys.exit(1)
    config = read_config(filigree_dir)
    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=config.get("prefix", "filigree"))
    db.initialize()
    return db


def _refresh_summary(db: FiligreeDB) -> None:
    """Regenerate context.md after mutations."""
    try:
        filigree_dir = find_filigree_root()
        write_summary(db, filigree_dir / SUMMARY_FILENAME)
    except FileNotFoundError:
        pass  # No .filigree/ dir — skip summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="filigree")
@click.option("--actor", default="cli", help="Actor identity for audit trail (default: cli)")
@click.pass_context
def cli(ctx: click.Context, actor: str) -> None:
    """Filigree — agent-native issue tracker."""
    ctx.ensure_object(dict)
    ctx.obj["actor"] = actor


@cli.command()
@click.option("--prefix", default=None, help="ID prefix for issues (default: directory name)")
def init(prefix: str | None) -> None:
    """Initialize .filigree/ in the current directory."""
    cwd = Path.cwd()
    filigree_dir = cwd / FILIGREE_DIR_NAME

    if filigree_dir.exists():
        click.echo(f"{FILIGREE_DIR_NAME}/ already exists in {cwd}")
        # Still ensure DB is initialized
        config = read_config(filigree_dir)
        db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=config.get("prefix", "filigree"))
        db.initialize()
        db.close()
        return

    prefix = prefix or cwd.name
    filigree_dir.mkdir()

    config = {"prefix": prefix, "version": 1}
    write_config(filigree_dir, config)

    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=prefix)
    db.initialize()
    write_summary(db, filigree_dir / SUMMARY_FILENAME)
    db.close()

    click.echo(f"Initialized {FILIGREE_DIR_NAME}/ in {cwd}")
    click.echo(f"  Prefix: {prefix}")
    click.echo(f"  Database: {filigree_dir / DB_FILENAME}")
    click.echo("\nNext: filigree install")


@cli.command()
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

    with _get_db() as db:
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
        _refresh_summary(db)


@cli.command()
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def show(issue_id: str, as_json: bool) -> None:
    """Show issue details."""
    with _get_db() as db:
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


@cli.command("list")
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
    with _get_db() as db:
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


@cli.command()
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

    with _get_db() as db:
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

        _refresh_summary(db)


@cli.command()
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--reason", default="", help="Close reason")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def close(ctx: click.Context, issue_ids: tuple[str, ...], reason: str, as_json: bool) -> None:
    """Close one or more issues."""
    with _get_db() as db:
        closed: list[dict[str, object]] = []
        for issue_id in issue_ids:
            try:
                issue = db.close_issue(issue_id, reason=reason, actor=ctx.obj["actor"])
                if as_json:
                    closed.append(issue.to_dict())
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
            unblocked = [
                {"id": i.id, "title": i.title, "priority": i.priority, "type": i.type}
                for i in ready
                if i.id not in closed_ids
            ]
            click.echo(json_mod.dumps({"closed": closed, "unblocked": unblocked}, indent=2, default=str))
        _refresh_summary(db)


@cli.command()
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def reopen(ctx: click.Context, issue_ids: tuple[str, ...], as_json: bool) -> None:
    """Reopen one or more closed issues."""
    with _get_db() as db:
        reopened: list[dict[str, object]] = []
        for issue_id in issue_ids:
            try:
                issue = db.reopen_issue(issue_id, actor=ctx.obj["actor"])
                if as_json:
                    reopened.append(issue.to_dict())
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
        _refresh_summary(db)


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def ready(as_json: bool) -> None:
    """Show issues ready to work on (no blockers)."""
    with _get_db() as db:
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


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def blocked(as_json: bool) -> None:
    """Show blocked issues."""
    with _get_db() as db:
        issues = db.get_blocked()

        if as_json:
            click.echo(json_mod.dumps([i.to_dict() for i in issues], indent=2, default=str))
            return

        for issue in issues:
            blockers = ", ".join(issue.blocked_by)
            click.echo(f'P{issue.priority} {issue.id} [{issue.type}] "{issue.title}" <- {blockers}')
        click.echo(f"\n{len(issues)} blocked")


@cli.command()
@click.argument("milestone_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def plan(milestone_id: str, as_json: bool) -> None:
    """Show milestone plan tree with progress."""
    with _get_db() as db:
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


@cli.command("add-dep")
@click.argument("issue_id")
@click.argument("depends_on_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def add_dep(ctx: click.Context, issue_id: str, depends_on_id: str, as_json: bool) -> None:
    """Add dependency: issue_id depends on depends_on_id."""
    with _get_db() as db:
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
        _refresh_summary(db)


@cli.command("remove-dep")
@click.argument("issue_id")
@click.argument("depends_on_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def remove_dep(ctx: click.Context, issue_id: str, depends_on_id: str, as_json: bool) -> None:
    """Remove dependency."""
    with _get_db() as db:
        removed = db.remove_dependency(issue_id, depends_on_id, actor=ctx.obj["actor"])
        status = "removed" if removed else "not_found"
        if as_json:
            click.echo(json_mod.dumps({"from_id": issue_id, "to_id": depends_on_id, "status": status}))
        else:
            if removed:
                click.echo(f"Removed: {issue_id} no longer depends on {depends_on_id}")
            else:
                click.echo(f"No dependency found: {issue_id} -> {depends_on_id}")
        _refresh_summary(db)


@cli.command("add-comment")
@click.argument("issue_id")
@click.argument("text")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def add_comment(ctx: click.Context, issue_id: str, text: str, as_json: bool) -> None:
    """Add a comment to an issue."""
    with _get_db() as db:
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


@cli.command("get-comments")
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def get_comments(issue_id: str, as_json: bool) -> None:
    """List comments on an issue."""
    with _get_db() as db:
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


@cli.command("add-label")
@click.argument("issue_id")
@click.argument("label_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def add_label(issue_id: str, label_name: str, as_json: bool) -> None:
    """Add a label to an issue."""
    with _get_db() as db:
        try:
            db.get_issue(issue_id)
        except KeyError:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
            else:
                click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        added = db.add_label(issue_id, label_name)
        status = "added" if added else "already_exists"
        if as_json:
            click.echo(json_mod.dumps({"issue_id": issue_id, "label": label_name, "status": status}))
        else:
            if added:
                click.echo(f"Added label '{label_name}' to {issue_id}")
            else:
                click.echo(f"Label '{label_name}' already on {issue_id}")
        _refresh_summary(db)


@cli.command("remove-label")
@click.argument("issue_id")
@click.argument("label_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def remove_label(issue_id: str, label_name: str, as_json: bool) -> None:
    """Remove a label from an issue."""
    with _get_db() as db:
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
        _refresh_summary(db)


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def stats(as_json: bool) -> None:
    """Show project statistics."""
    with _get_db() as db:
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


@cli.command()
@click.argument("query")
@click.option("--limit", default=100, type=int, help="Max results (default 100)")
@click.option("--offset", default=0, type=int, help="Skip first N results")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def search(query: str, limit: int, offset: int, as_json: bool) -> None:
    """Search issues by title/description."""
    with _get_db() as db:
        issues = db.search_issues(query, limit=limit, offset=offset)

        if as_json:
            click.echo(json_mod.dumps([i.to_dict() for i in issues], indent=2, default=str))
            return

        for issue in issues:
            click.echo(f"P{issue.priority} {issue.id} [{issue.type}] {issue.status:<12} {issue.title}")
        click.echo(f"\n{len(issues)} results")


@cli.group(invoke_without_command=True)
@click.option("--type", "issue_type", default=None, help="Show specific template")
@click.pass_context
def templates(ctx: click.Context, issue_type: str | None) -> None:
    """Show available issue templates."""
    if ctx.invoked_subcommand is not None:
        return
    with _get_db() as db:
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
    with _get_db() as db:
        db.reload_templates()
        click.echo("Templates reloaded")


@cli.command()
@click.option("--from-beads", is_flag=True, help="Migrate from .beads database")
@click.option("--beads-db", default=None, help="Path to beads.db (default: .beads/beads.db)")
def migrate(from_beads: bool, beads_db: str | None) -> None:
    """Migrate issues from another system."""
    if not from_beads:
        click.echo("Only --from-beads is supported currently.", err=True)
        sys.exit(1)

    from filigree.migrate import migrate_from_beads

    beads_path = beads_db or str(Path.cwd() / ".beads" / "beads.db")
    if not Path(beads_path).exists():
        click.echo(f"Beads DB not found: {beads_path}", err=True)
        sys.exit(1)

    with _get_db() as db:
        count = migrate_from_beads(beads_path, db)
        _refresh_summary(db)
        click.echo(f"Migrated {count} issues from beads")


@cli.command()
@click.option("--claude-code", is_flag=True, help="Install MCP for Claude Code only")
@click.option("--codex", is_flag=True, help="Install MCP for Codex only")
@click.option("--claude-md", is_flag=True, help="Inject instructions into CLAUDE.md only")
@click.option("--agents-md", is_flag=True, help="Inject instructions into AGENTS.md only")
@click.option("--gitignore", is_flag=True, help="Add .filigree/ to .gitignore only")
@click.option("--hooks", "hooks_only", is_flag=True, help="Install Claude Code hooks only")
@click.option("--skills", "skills_only", is_flag=True, help="Install Claude Code skills only")
def install(
    claude_code: bool,
    codex: bool,
    claude_md: bool,
    agents_md: bool,
    gitignore: bool,
    hooks_only: bool,
    skills_only: bool,
) -> None:
    """Install filigree into the current project.

    With no flags, installs everything: MCP servers, instructions, gitignore, hooks, skills.
    With specific flags, installs only the selected components.
    """
    from filigree.install import (
        ensure_gitignore,
        inject_instructions,
        install_claude_code_hooks,
        install_claude_code_mcp,
        install_codex_mcp,
        install_skills,
    )

    try:
        filigree_dir = find_filigree_root()
    except FileNotFoundError:
        click.echo(f"No {FILIGREE_DIR_NAME}/ found. Run 'filigree init' first.", err=True)
        sys.exit(1)

    project_root = filigree_dir.parent
    install_all = not any([claude_code, codex, claude_md, agents_md, gitignore, hooks_only, skills_only])

    results: list[tuple[str, bool, str]] = []

    if install_all or claude_code:
        ok, msg = install_claude_code_mcp(project_root)
        results.append(("Claude Code MCP", ok, msg))

    if install_all or codex:
        ok, msg = install_codex_mcp(project_root)
        results.append(("Codex MCP", ok, msg))

    if install_all or claude_md:
        ok, msg = inject_instructions(project_root / "CLAUDE.md")
        results.append(("CLAUDE.md", ok, msg))

    if install_all or agents_md:
        ok, msg = inject_instructions(project_root / "AGENTS.md")
        results.append(("AGENTS.md", ok, msg))

    if install_all or gitignore:
        ok, msg = ensure_gitignore(project_root)
        results.append((".gitignore", ok, msg))

    if install_all or claude_code or hooks_only:
        ok, msg = install_claude_code_hooks(project_root)
        results.append(("Claude Code hooks", ok, msg))

    if install_all or claude_code or skills_only:
        ok, msg = install_skills(project_root)
        results.append(("Claude Code skills", ok, msg))

    for name, ok, msg in results:
        icon = "OK" if ok else "!!"
        click.echo(f"  {icon}  {name}: {msg}")

    ok_count = sum(1 for _, ok, _ in results if ok)
    click.echo(f"\n{ok_count}/{len(results)} installed successfully")
    click.echo('Next: filigree create "My first issue"')


@cli.command()
@click.option("--fix", is_flag=True, help="Auto-fix issues where possible")
@click.option("--verbose", is_flag=True, help="Show all checks including passed")
def doctor(fix: bool, verbose: bool) -> None:
    """Run health checks on the filigree installation."""
    from filigree.install import run_doctor
    from filigree.summary import write_summary as _write_summary

    results = run_doctor()

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)

    click.echo(f"filigree doctor  ──  {passed} passed  {failed} issues")
    click.echo()

    for r in results:
        if r.passed and not verbose:
            continue
        icon = "OK" if r.passed else "!!"
        click.echo(f"  {icon}  {r.name}: {r.message}")
        if not r.passed and r.fix_hint:
            click.echo(f"       -> {r.fix_hint}")

    if fix and failed > 0:
        click.echo("\nApplying fixes...")
        try:
            filigree_dir = find_filigree_root()
            # Refresh context.md
            with _get_db() as db:
                _write_summary(db, filigree_dir / SUMMARY_FILENAME)
                click.echo("  OK  Regenerated context.md")
        except (FileNotFoundError, Exception) as e:
            click.echo(f"  !!  Fix failed: {e}", err=True)

    if failed == 0:
        click.echo("\nAll checks passed.")


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.option("--days", default=30, help="Lookback window in days")
def metrics(as_json: bool, days: int) -> None:
    """Show flow metrics: cycle time, lead time, throughput."""
    from filigree.analytics import get_flow_metrics

    with _get_db() as db:
        data = get_flow_metrics(db, days=days)

    if as_json:
        click.echo(json_mod.dumps(data, indent=2, default=str))
        return

    click.echo(f"Flow Metrics (last {data['period_days']} days)")
    click.echo(f"  Throughput:     {data['throughput']} closed")
    avg_ct = data["avg_cycle_time_hours"]
    avg_lt = data["avg_lead_time_hours"]
    click.echo(f"  Avg cycle time: {f'{avg_ct}h' if avg_ct is not None else 'n/a'}")
    click.echo(f"  Avg lead time:  {f'{avg_lt}h' if avg_lt is not None else 'n/a'}")
    if data["by_type"]:
        click.echo("\n  By type:")
        for t, m in sorted(data["by_type"].items()):
            ct_str = f"{m['avg_cycle_time_hours']}h" if m["avg_cycle_time_hours"] is not None else "n/a"
            click.echo(f"    {t:<12} {m['count']} closed, avg cycle: {ct_str}")


@cli.command()
@click.option("--port", default=8377, type=int, help="Server port (default 8377)")
@click.option("--no-browser", is_flag=True, help="Don't auto-open browser")
def dashboard(port: int, no_browser: bool) -> None:
    """Launch the web dashboard (requires filigree[dashboard])."""
    try:
        from filigree.dashboard import main as dashboard_main
    except ImportError:
        click.echo('Dashboard requires extra dependencies. Install with: pip install "filigree[dashboard]"', err=True)
        sys.exit(1)
    dashboard_main(port=port, no_browser=no_browser)


@cli.command("session-context")
def session_context() -> None:
    """Output project snapshot for Claude Code session context."""
    from filigree.hooks import generate_session_context

    context = generate_session_context()
    if context:
        click.echo(context)


@cli.command("ensure-dashboard")
@click.option("--port", default=8377, type=int, help="Dashboard port (default 8377)")
def ensure_dashboard_cmd(port: int) -> None:
    """Ensure the filigree dashboard is running."""
    from filigree.hooks import ensure_dashboard_running

    message = ensure_dashboard_running(port=port)
    if message:
        click.echo(message)


@cli.command("critical-path")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def critical_path(as_json: bool) -> None:
    """Show the longest dependency chain among open issues."""
    with _get_db() as db:
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


@cli.command("release")
@click.argument("issue_id")
@click.pass_context
def release(ctx: click.Context, issue_id: str) -> None:
    """Release a claimed issue by clearing its assignee."""
    with _get_db() as db:
        try:
            issue = db.release_claim(issue_id, actor=ctx.obj["actor"])
            click.echo(f"Released {issue.id}: {issue.title} [{issue.status}]")
        except KeyError:
            click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        _refresh_summary(db)


@cli.command("export")
@click.argument("output", type=click.Path())
def export_data(output: str) -> None:
    """Export all issues to JSONL file."""
    with _get_db() as db:
        count = db.export_jsonl(output)
        click.echo(f"Exported {count} records to {output}")


@cli.command("import")
@click.argument("input_file", type=click.Path(exists=True))
@click.option("--merge", is_flag=True, help="Skip existing records instead of failing on conflict")
def import_data(input_file: str, merge: bool) -> None:
    """Import issues from JSONL file."""
    with _get_db() as db:
        try:
            count = db.import_jsonl(input_file, merge=merge)
        except (json_mod.JSONDecodeError, KeyError, ValueError) as e:
            click.echo(f"Import failed: {e}", err=True)
            sys.exit(1)
        _refresh_summary(db)
        click.echo(f"Imported {count} records from {input_file}")


@cli.command("archive")
@click.option("--days", default=30, type=int, help="Archive issues closed more than N days ago (default: 30)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def archive(ctx: click.Context, days: int, as_json: bool) -> None:
    """Archive old closed issues to reduce active issue count."""
    with _get_db() as db:
        archived = db.archive_closed(days_old=days, actor=ctx.obj["actor"])
        if as_json:
            click.echo(json_mod.dumps({"archived": archived, "count": len(archived)}, indent=2, default=str))
        else:
            if archived:
                click.echo(f"Archived {len(archived)} issues (closed > {days} days)")
                for aid in archived:
                    click.echo(f"  {aid}")
            else:
                click.echo("No issues to archive")
        _refresh_summary(db)


@cli.command("compact")
@click.option("--keep", default=50, type=int, help="Keep N most recent events per archived issue (default: 50)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def compact(keep: int, as_json: bool) -> None:
    """Compact event history for archived issues."""
    with _get_db() as db:
        deleted = db.compact_events(keep_recent=keep)
        if as_json:
            click.echo(json_mod.dumps({"deleted_events": deleted}))
        else:
            click.echo(f"Compacted {deleted} events")
        if deleted > 0:
            db.vacuum()
            if not as_json:
                click.echo("Vacuumed database")


@cli.command()
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def undo(ctx: click.Context, issue_id: str, as_json: bool) -> None:
    """Undo the most recent reversible action on an issue."""
    with _get_db() as db:
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
        _refresh_summary(db)


@cli.command("workflow-states")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def workflow_states(as_json: bool) -> None:
    """Show workflow states by category from enabled templates."""
    with _get_db() as db:
        data = {}
        for category in ("open", "wip", "done"):
            data[category] = list(db._get_states_for_category(category))
        if as_json:
            click.echo(json_mod.dumps(data, indent=2))
            return
        for category, states in data.items():
            click.echo(f"{category}: {', '.join(states) if states else '(none)'}")


@cli.command("types")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def types_cmd(as_json: bool) -> None:
    """List all registered issue types with pack info."""
    with _get_db() as db:
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


@cli.command("type-info")
@click.argument("type_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def type_info(type_name: str, as_json: bool) -> None:
    """Show full workflow definition for an issue type."""
    with _get_db() as db:
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
                "fields_schema": [
                    {"name": f.name, "type": f.type, "description": f.description} for f in tpl.fields_schema
                ],
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


@cli.command("transitions")
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def transitions_cmd(issue_id: str, as_json: bool) -> None:
    """Show valid next states for an issue."""
    with _get_db() as db:
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


@cli.command("packs")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def packs_cmd(as_json: bool) -> None:
    """List enabled workflow packs."""
    with _get_db() as db:
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


@cli.command("validate")
@click.argument("issue_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def validate_cmd(issue_id: str, as_json: bool) -> None:
    """Validate an issue against its type template."""
    with _get_db() as db:
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


@cli.command("guide")
@click.argument("pack_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def guide_cmd(pack_name: str, as_json: bool) -> None:
    """Display workflow guide for a pack."""
    with _get_db() as db:
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


@cli.command()
@click.argument("issue_id")
@click.option("--assignee", required=True, help="Who is claiming (agent name)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def claim(ctx: click.Context, issue_id: str, assignee: str, as_json: bool) -> None:
    """Atomically claim an open issue (optimistic locking)."""
    with _get_db() as db:
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
        _refresh_summary(db)


@cli.command("claim-next")
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
    with _get_db() as db:
        issue = db.claim_next(
            assignee,
            type_filter=type_filter,
            priority_min=priority_min,
            priority_max=priority_max,
            actor=ctx.obj["actor"],
        )
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
        _refresh_summary(db)


@cli.command("create-plan")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="JSON file (stdin if omitted)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def create_plan(ctx: click.Context, file_path: str | None, as_json: bool) -> None:
    """Create a milestone/phase/step hierarchy from JSON.

    Reads JSON from --file or stdin. Structure:
    {"milestone": {"title": "..."}, "phases": [{"title": "...", "steps": [...]}]}
    """
    raw = Path(file_path).read_text() if file_path else click.get_text_stream("stdin").read()

    try:
        data = json_mod.loads(raw)
    except json_mod.JSONDecodeError as e:
        click.echo(f"Invalid JSON: {e}", err=True)
        sys.exit(1)

    if "milestone" not in data or "phases" not in data:
        click.echo("JSON must contain 'milestone' and 'phases' keys", err=True)
        sys.exit(1)

    with _get_db() as db:
        try:
            result = db.create_plan(data["milestone"], data["phases"], actor=ctx.obj["actor"])
        except (ValueError, IndexError) as e:
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
        _refresh_summary(db)


@cli.command("batch-update")
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
                click.echo(f"Invalid field format: {f}", err=True)
                sys.exit(1)
            k, v = f.split("=", 1)
            fields[k] = v

    with _get_db() as db:
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
            click.echo(f"Updated {len(results)} issues")
        _refresh_summary(db)


@cli.command("batch-close")
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--reason", default="", help="Close reason")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def batch_close(ctx: click.Context, issue_ids: tuple[str, ...], reason: str, as_json: bool) -> None:
    """Close multiple issues with per-item error reporting."""
    closed = []
    errors = []
    with _get_db() as db:
        for issue_id in issue_ids:
            try:
                issue = db.close_issue(issue_id, reason=reason, actor=ctx.obj["actor"])
                closed.append(issue)
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}"})
            except ValueError as e:
                errors.append({"id": issue_id, "error": str(e)})

        if as_json:
            click.echo(
                json_mod.dumps(
                    {
                        "closed": [
                            {"id": i.id, "title": i.title, "priority": i.priority, "type": i.type} for i in closed
                        ],
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
            click.echo(f"Closed {len(closed)}/{len(issue_ids)} issues")
        _refresh_summary(db)


@cli.command("changes")
@click.option("--since", required=True, help="ISO timestamp to get events after")
@click.option("--limit", default=100, type=int, help="Max events (default 100)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def changes(since: str, limit: int, as_json: bool) -> None:
    """Get events since a timestamp (for session resumption)."""
    with _get_db() as db:
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


@cli.command("events")
@click.argument("issue_id")
@click.option("--limit", default=50, type=int, help="Max events (default 50)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def events_cmd(issue_id: str, limit: int, as_json: bool) -> None:
    """Get event history for a specific issue, newest first."""
    with _get_db() as db:
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


@cli.command("explain-state")
@click.argument("type_name")
@click.argument("state_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def explain_state(type_name: str, state_name: str, as_json: bool) -> None:
    """Explain a state's transitions and required fields."""
    with _get_db() as db:
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

        inbound = [
            {"from": t.from_state, "enforcement": t.enforcement} for t in tpl.transitions if t.to_state == state_name
        ]
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


if __name__ == "__main__":
    cli()
