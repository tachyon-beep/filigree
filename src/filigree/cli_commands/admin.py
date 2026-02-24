"""CLI commands for admin: init, install, doctor, migrate, dashboard, metrics, export/import, archive, compact."""

from __future__ import annotations

import json as json_mod
import logging
import os
import sqlite3
import sys
from pathlib import Path

import click

from filigree.cli_common import get_db, refresh_summary
from filigree.core import (
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
    find_filigree_root,
    get_mode,
    read_config,
    write_config,
)
from filigree.summary import write_summary


@click.command()
@click.option("--prefix", default=None, help="ID prefix for issues (default: directory name)")
@click.option(
    "--mode",
    type=click.Choice(["ethereal", "server"], case_sensitive=False),
    default=None,
    help="Installation mode (default: ethereal)",
)
def init(prefix: str | None, mode: str | None) -> None:
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
        (filigree_dir / "scanners").mkdir(exist_ok=True)
        # Update mode if explicitly provided
        if mode is not None:
            config["mode"] = mode
            write_config(filigree_dir, config)
            click.echo(f"  Mode: {mode}")
        return

    prefix = prefix or cwd.name
    mode = mode or "ethereal"
    filigree_dir.mkdir()
    (filigree_dir / "scanners").mkdir()

    config = {"prefix": prefix, "version": 1, "mode": mode}
    write_config(filigree_dir, config)

    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=prefix)
    db.initialize()
    write_summary(db, filigree_dir / SUMMARY_FILENAME)
    db.close()

    click.echo(f"Initialized {FILIGREE_DIR_NAME}/ in {cwd}")
    click.echo(f"  Prefix: {prefix}")
    click.echo(f"  Mode: {mode}")
    click.echo(f"  Database: {filigree_dir / DB_FILENAME}")
    click.echo(f"  Scanners: {filigree_dir / 'scanners'}/ (add .toml files to register scanners)")
    click.echo("\nNext: filigree install")


@click.command()
@click.option("--claude-code", is_flag=True, help="Install MCP for Claude Code only")
@click.option("--codex", is_flag=True, help="Install MCP for Codex only")
@click.option("--claude-md", is_flag=True, help="Inject instructions into CLAUDE.md only")
@click.option("--agents-md", is_flag=True, help="Inject instructions into AGENTS.md only")
@click.option("--gitignore", is_flag=True, help="Add .filigree/ to .gitignore only")
@click.option("--hooks", "hooks_only", is_flag=True, help="Install Claude Code hooks only")
@click.option("--skills", "skills_only", is_flag=True, help="Install Claude Code skills only")
@click.option("--codex-skills", "codex_skills_only", is_flag=True, help="Install Codex skills only")
@click.option(
    "--mode",
    type=click.Choice(["ethereal", "server"], case_sensitive=False),
    default=None,
    help="Installation mode (default: preserve existing or ethereal)",
)
def install(
    claude_code: bool,
    codex: bool,
    claude_md: bool,
    agents_md: bool,
    gitignore: bool,
    hooks_only: bool,
    skills_only: bool,
    codex_skills_only: bool,
    mode: str | None,
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
        install_codex_skills,
        install_skills,
    )

    try:
        filigree_dir = find_filigree_root()
    except FileNotFoundError:
        click.echo(f"No {FILIGREE_DIR_NAME}/ found. Run 'filigree init' first.", err=True)
        sys.exit(1)

    # Update mode in config if explicitly provided
    if mode is not None:
        config = read_config(filigree_dir)
        config["mode"] = mode
        write_config(filigree_dir, config)

    # Resolve effective mode (explicit flag > config > default)
    mode = mode or get_mode(filigree_dir)

    project_root = filigree_dir.parent
    install_all = not any([claude_code, codex, claude_md, agents_md, gitignore, hooks_only, skills_only, codex_skills_only])

    results: list[tuple[str, bool, str]] = []
    server_port = 8377
    if mode == "server":
        try:
            from filigree.server import read_server_config

            server_port = read_server_config().port
        except Exception:
            logging.getLogger(__name__).debug("Failed to read server config port; defaulting to 8377", exc_info=True)

    if install_all or claude_code:
        ok, msg = install_claude_code_mcp(project_root, mode=mode, server_port=server_port)
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

    if install_all or codex or codex_skills_only:
        ok, msg = install_codex_skills(project_root)
        results.append(("Codex skills", ok, msg))

    # Server mode: register project in server.json
    if mode == "server":
        try:
            from filigree.server import daemon_status, register_project

            register_project(filigree_dir)
            results.append(("Server registration", True, "Registered in server.json"))
            status = daemon_status()
            if not status.running:
                click.echo('\nNote: start the daemon with "filigree server start"')
        except Exception as e:
            results.append(("Server registration", False, str(e)))

    for name, ok, msg in results:
        icon = "OK" if ok else "!!"
        click.echo(f"  {icon}  {name}: {msg}")

    ok_count = sum(1 for _, ok, _ in results if ok)
    click.echo(f"\n{ok_count}/{len(results)} installed successfully")
    click.echo('Next: filigree create "My first issue"')


@click.command()
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
            with get_db() as db:
                _write_summary(db, filigree_dir / SUMMARY_FILENAME)
                click.echo("  OK  Regenerated context.md")
        except (FileNotFoundError, Exception) as e:
            click.echo(f"  !!  Fix failed: {e}", err=True)

    if failed == 0:
        click.echo("\nAll checks passed.")


@click.command()
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

    with get_db() as db:
        count = migrate_from_beads(beads_path, db)
        refresh_summary(db)
        click.echo(f"Migrated {count} issues from beads")


@click.command()
@click.option("--port", default=8377, type=int, help="Server port (default 8377)")
@click.option("--no-browser", is_flag=True, help="Don't auto-open browser")
@click.option("--server-mode", is_flag=True, help="Multi-project server mode (reads server.json)")
def dashboard(port: int, no_browser: bool, server_mode: bool) -> None:
    """Launch the web dashboard (requires filigree[dashboard])."""
    try:
        from filigree.dashboard import main as dashboard_main
    except ImportError:
        click.echo('Dashboard requires extra dependencies. Install with: pip install "filigree[dashboard]"', err=True)
        sys.exit(1)

    pid_claimed = False
    current_pid = os.getpid()
    if server_mode:
        from filigree.server import claim_current_process_as_daemon

        pid_claimed = claim_current_process_as_daemon(port=port)
    try:
        dashboard_main(port=port, no_browser=no_browser, server_mode=server_mode)
    finally:
        if server_mode and pid_claimed:
            from filigree.server import release_daemon_pid_if_owned

            release_daemon_pid_if_owned(current_pid)


@click.command("session-context")
def session_context() -> None:
    """Output project snapshot for Claude Code session context."""
    try:
        from filigree.hooks import generate_session_context

        context = generate_session_context()
        if context:
            click.echo(context)
    except Exception:
        logging.getLogger(__name__).warning("session-context hook failed", exc_info=True)
        click.echo("Warning: session-context hook failed (run with -v for details)", err=True)


@click.command("ensure-dashboard")
@click.option("--port", default=None, type=int, help="Dashboard port override (server mode)")
def ensure_dashboard_cmd(port: int | None) -> None:
    """Ensure the filigree dashboard is running."""
    try:
        from filigree.hooks import ensure_dashboard_running

        message = ensure_dashboard_running(port=port)
        if message:
            click.echo(message)
    except Exception:
        logging.getLogger(__name__).warning("ensure-dashboard hook failed", exc_info=True)
        click.echo("Warning: ensure-dashboard hook failed (run with -v for details)", err=True)


@click.command()
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.option("--days", default=30, help="Lookback window in days")
def metrics(as_json: bool, days: int) -> None:
    """Show flow metrics: cycle time, lead time, throughput."""
    from filigree.analytics import get_flow_metrics

    with get_db() as db:
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


@click.command("export")
@click.argument("output", type=click.Path())
def export_data(output: str) -> None:
    """Export all issues to JSONL file."""
    with get_db() as db:
        count = db.export_jsonl(output)
        click.echo(f"Exported {count} records to {output}")


@click.command("import")
@click.argument("input_file", type=click.Path(exists=True))
@click.option("--merge", is_flag=True, help="Skip existing records instead of failing on conflict")
def import_data(input_file: str, merge: bool) -> None:
    """Import issues from JSONL file."""
    with get_db() as db:
        try:
            count = db.import_jsonl(input_file, merge=merge)
        except (json_mod.JSONDecodeError, KeyError, ValueError, sqlite3.IntegrityError, OSError) as e:
            click.echo(f"Import failed: {e}", err=True)
            sys.exit(1)
        refresh_summary(db)
        click.echo(f"Imported {count} records from {input_file}")


@click.command("archive")
@click.option("--days", default=30, type=click.IntRange(min=0), help="Archive issues closed more than N days ago (default: 30)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def archive(ctx: click.Context, days: int, as_json: bool) -> None:
    """Archive old closed issues to reduce active issue count."""
    with get_db() as db:
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
        refresh_summary(db)


@click.command("clean-stale-findings")
@click.option("--days", default=30, type=click.IntRange(min=0), help="Mark as fixed if unseen for more than N days (default: 30)")
@click.option("--scan-source", default=None, type=str, help="Only clean findings from this scan source")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def clean_stale_findings(ctx: click.Context, days: int, scan_source: str | None, as_json: bool) -> None:
    """Move stale unseen_in_latest findings to fixed status."""
    with get_db() as db:
        result = db.clean_stale_findings(days=days, scan_source=scan_source, actor=ctx.obj["actor"])
        if as_json:
            click.echo(json_mod.dumps(result))
        elif result["findings_fixed"] > 0:
            click.echo(f"Fixed {result['findings_fixed']} stale findings (unseen > {days} days)")
        else:
            click.echo("No stale findings to clean")


@click.command("compact")
@click.option("--keep", default=50, type=click.IntRange(min=0), help="Keep N most recent events per archived issue (default: 50)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def compact(keep: int, as_json: bool) -> None:
    """Compact event history for archived issues."""
    with get_db() as db:
        deleted = db.compact_events(keep_recent=keep)
        if as_json:
            click.echo(json_mod.dumps({"deleted_events": deleted}))
        else:
            click.echo(f"Compacted {deleted} events")
        if deleted > 0:
            db.vacuum()
            if not as_json:
                click.echo("Vacuumed database")


def register(cli: click.Group) -> None:
    """Register admin commands with the CLI group."""
    cli.add_command(init)
    cli.add_command(install)
    cli.add_command(doctor)
    cli.add_command(migrate)
    cli.add_command(dashboard)
    cli.add_command(ensure_dashboard_cmd)
    cli.add_command(session_context)
    cli.add_command(metrics)
    cli.add_command(export_data)
    cli.add_command(import_data)
    cli.add_command(archive)
    cli.add_command(clean_stale_findings)
    cli.add_command(compact)
