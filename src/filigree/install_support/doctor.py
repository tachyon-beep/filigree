"""Health-check system (``filigree doctor``).

Runs a battery of checks against the project's ``.filigree/`` directory,
MCP configuration, Claude Code hooks, skills, and more.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from filigree.core import (
    CONFIG_FILENAME,
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    find_filigree_root,
)
from filigree.db_schema import CURRENT_SCHEMA_VERSION
from filigree.install_support import (
    FILIGREE_INSTRUCTIONS_MARKER,
    SKILL_MARKER,
    SKILL_NAME,
)
from filigree.install_support.hooks import (
    SESSION_CONTEXT_COMMAND,
    _extract_hook_binary,
    _has_hook_command,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Result of a single doctor check."""

    name: str
    passed: bool
    message: str
    fix_hint: str = ""

    @property
    def icon(self) -> str:
        return "OK" if self.passed else "!!"


def _is_absolute_command_path(path: str) -> bool:
    """Return True when *path* looks like an absolute command path."""
    if not path:
        return False
    if Path(path).is_absolute():
        return True
    # Handle Windows absolute paths when running on non-Windows hosts.
    if path.startswith("\\\\"):
        return True
    return len(path) > 2 and path[0].isalpha() and path[1] == ":" and path[2] in ("/", "\\")


# ---------------------------------------------------------------------------
# Mode-specific checks
# ---------------------------------------------------------------------------


def _doctor_ethereal_checks(filigree_dir: Path) -> list[CheckResult]:
    """Ethereal mode health checks."""
    from filigree.ephemeral import is_pid_alive, read_pid_file, read_port_file

    results: list[CheckResult] = []
    pid_file = filigree_dir / "ephemeral.pid"
    port_file = filigree_dir / "ephemeral.port"

    if pid_file.exists():
        info = read_pid_file(pid_file)
        if info and is_pid_alive(info["pid"]):
            results.append(CheckResult("Ephemeral PID", True, f"Process {info['pid']} alive"))
        else:
            pid_val = info["pid"] if info else "unknown"
            results.append(
                CheckResult(
                    "Ephemeral PID",
                    False,
                    f"Stale PID file (pid {pid_val})",
                    fix_hint="Remove .filigree/ephemeral.pid or run: filigree ensure-dashboard",
                )
            )

    if port_file.exists():
        from filigree.hooks import _is_port_listening

        port = read_port_file(port_file)
        if port and _is_port_listening(port):
            results.append(CheckResult("Ephemeral port", True, f"Port {port} listening"))
        else:
            results.append(
                CheckResult(
                    "Ephemeral port",
                    False,
                    f"Port {port} not listening",
                    fix_hint="Dashboard may have crashed. Run: filigree ensure-dashboard",
                )
            )

    return results


def _doctor_server_checks(filigree_dir: Path) -> list[CheckResult]:
    """Server mode health checks."""
    from filigree.server import daemon_status, read_server_config

    results: list[CheckResult] = []
    status = daemon_status()
    if status.running:
        results.append(
            CheckResult(
                "Server daemon",
                True,
                f"Running (pid {status.pid}, port {status.port}, {status.project_count} projects)",
            )
        )
    else:
        results.append(
            CheckResult(
                "Server daemon",
                False,
                "Not running",
                fix_hint="Run: filigree server start",
            )
        )

    # Check registered projects health
    config = read_server_config()
    for path_str, info in config.projects.items():
        p = Path(path_str)
        if not p.is_dir():
            results.append(
                CheckResult(
                    f'Project "{info.get("prefix", "?")}"',
                    False,
                    f"Directory gone: {path_str}",
                    fix_hint=f"Run: filigree server unregister {p.parent}",
                )
            )

    return results


# ---------------------------------------------------------------------------
# Main doctor entry point
# ---------------------------------------------------------------------------


def run_doctor(project_root: Path | None = None) -> list[CheckResult]:
    """Run all health checks. Returns list of CheckResult."""
    results: list[CheckResult] = []
    cwd = project_root or Path.cwd()

    # 1. Check .filigree/ exists
    filigree_dir = cwd / FILIGREE_DIR_NAME
    if not filigree_dir.is_dir():
        # Try walking up
        try:
            filigree_dir = find_filigree_root(cwd)
        except FileNotFoundError:
            results.append(
                CheckResult(
                    ".filigree/ directory",
                    False,
                    f"No {FILIGREE_DIR_NAME}/ found in {cwd} or parents",
                    fix_hint="Run: filigree init",
                )
            )
            return results  # Can't proceed without .filigree/
    results.append(CheckResult(".filigree/ directory", True, f"Found at {filigree_dir}"))

    # 2. Check config.json
    config_path = filigree_dir / CONFIG_FILENAME
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            if not isinstance(config, dict):
                raise ValueError("config.json must be a JSON object")
            prefix = config.get("prefix", "?")
            results.append(CheckResult("config.json", True, f"Prefix: {prefix}"))
        except json.JSONDecodeError as e:
            results.append(
                CheckResult(
                    "config.json",
                    False,
                    f"Invalid JSON: {e}",
                    fix_hint="Fix or regenerate .filigree/config.json",
                )
            )
        except ValueError:
            results.append(
                CheckResult(
                    "config.json",
                    False,
                    "Invalid JSON shape: expected an object",
                    fix_hint="Fix or regenerate .filigree/config.json",
                )
            )
    else:
        results.append(
            CheckResult(
                "config.json",
                False,
                "Missing",
                fix_hint="Run: filigree init",
            )
        )

    # 3. Check filigree.db exists and is accessible
    db_path = filigree_dir / DB_FILENAME
    if db_path.exists():
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("SELECT COUNT(*) FROM issues")
            count = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
            # 3b. Check schema version
            schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
            results.append(CheckResult("filigree.db", True, f"{count} issues"))
            if schema_version < CURRENT_SCHEMA_VERSION:
                results.append(
                    CheckResult(
                        "Schema version",
                        False,
                        f"v{schema_version} (current: v{CURRENT_SCHEMA_VERSION})",
                        fix_hint="Database schema is outdated. Run: filigree init (applies migrations automatically)",
                    )
                )
            else:
                results.append(CheckResult("Schema version", True, f"v{schema_version}"))
        except sqlite3.Error as e:
            results.append(
                CheckResult(
                    "filigree.db",
                    False,
                    f"Database error: {e}",
                    fix_hint="Database may be corrupted. Restore from backup.",
                )
            )
        finally:
            if conn is not None:
                conn.close()
    else:
        results.append(
            CheckResult(
                "filigree.db",
                False,
                "Missing",
                fix_hint="Run: filigree init",
            )
        )

    # 4. Check context.md freshness
    summary_path = filigree_dir / SUMMARY_FILENAME
    if summary_path.exists():
        mtime = datetime.fromtimestamp(summary_path.stat().st_mtime, tz=UTC)
        age_minutes = (datetime.now(UTC) - mtime).total_seconds() / 60
        if age_minutes > 60:
            results.append(
                CheckResult(
                    "context.md",
                    False,
                    f"Stale ({int(age_minutes)} minutes old)",
                    fix_hint="Run any filigree mutation command to refresh, or: filigree doctor --fix",
                )
            )
        else:
            results.append(CheckResult("context.md", True, f"Fresh ({int(age_minutes)}m old)"))
    else:
        results.append(
            CheckResult(
                "context.md",
                False,
                "Missing",
                fix_hint="Run: filigree doctor --fix",
            )
        )

    # 5. Check .gitignore includes .filigree/
    gitignore = (filigree_dir.parent) / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".filigree/" in content or ".filigree" in content:
            results.append(CheckResult(".gitignore", True, ".filigree/ is ignored"))
        else:
            results.append(
                CheckResult(
                    ".gitignore",
                    False,
                    ".filigree/ not in .gitignore",
                    fix_hint="Run: filigree install --gitignore",
                )
            )
    else:
        results.append(
            CheckResult(
                ".gitignore",
                False,
                "No .gitignore found",
                fix_hint="Create .gitignore with .filigree/ entry",
            )
        )

    # 6. Check MCP configuration — Claude Code
    mcp_json = (filigree_dir.parent) / ".mcp.json"
    if mcp_json.exists():
        try:
            mcp = json.loads(mcp_json.read_text())
            if not isinstance(mcp, dict):
                raise ValueError("not a JSON object")
            servers = mcp.get("mcpServers", {})
            if not isinstance(servers, dict):
                raise ValueError("mcpServers must be a JSON object")
            filigree_mcp_entry = servers.get("filigree")
            if filigree_mcp_entry:
                # Validate binary path if it's an absolute path
                mcp_command = filigree_mcp_entry.get("command", "") if isinstance(filigree_mcp_entry, dict) else ""
                if _is_absolute_command_path(mcp_command) and not Path(mcp_command).exists():
                    results.append(
                        CheckResult(
                            "Claude Code MCP",
                            False,
                            f"Binary not found at {mcp_command}",
                            fix_hint="Run: filigree install --claude-code",
                        )
                    )
                else:
                    results.append(CheckResult("Claude Code MCP", True, "Configured in .mcp.json"))
            else:
                results.append(
                    CheckResult(
                        "Claude Code MCP",
                        False,
                        "filigree not in .mcp.json",
                        fix_hint="Run: filigree install --claude-code",
                    )
                )
        except (json.JSONDecodeError, ValueError):
            results.append(
                CheckResult(
                    "Claude Code MCP",
                    False,
                    "Invalid .mcp.json",
                    fix_hint="Fix .mcp.json or run: filigree install --claude-code",
                )
            )
    else:
        results.append(
            CheckResult(
                "Claude Code MCP",
                False,
                "No .mcp.json found",
                fix_hint="Run: filigree install --claude-code",
            )
        )

    # 7. Check MCP configuration — Codex
    codex_config = (filigree_dir.parent) / ".codex" / "config.toml"
    if codex_config.exists():
        try:
            parsed = tomllib.loads(codex_config.read_text())
            mcp_servers = parsed.get("mcp_servers", {})
            filigree_server = mcp_servers.get("filigree") if isinstance(mcp_servers, dict) else None
            if isinstance(filigree_server, dict):
                results.append(CheckResult("Codex MCP", True, "Configured in .codex/config.toml"))
            else:
                results.append(
                    CheckResult(
                        "Codex MCP",
                        False,
                        "filigree not in .codex/config.toml",
                        fix_hint="Run: filigree install --codex",
                    )
                )
        except tomllib.TOMLDecodeError:
            results.append(
                CheckResult(
                    "Codex MCP",
                    False,
                    "Invalid .codex/config.toml",
                    fix_hint="Fix .codex/config.toml or run: filigree install --codex",
                )
            )
    else:
        results.append(
            CheckResult(
                "Codex MCP",
                False,
                "No .codex/config.toml found",
                fix_hint="Run: filigree install --codex",
            )
        )

    # 8. Check Claude Code hooks
    settings_json = (filigree_dir.parent) / ".claude" / "settings.json"
    if settings_json.exists():
        try:
            s = json.loads(settings_json.read_text())
            if _has_hook_command(s, SESSION_CONTEXT_COMMAND):
                # Validate binary path if it's an absolute path
                hook_binary = _extract_hook_binary(s, SESSION_CONTEXT_COMMAND)
                if hook_binary and _is_absolute_command_path(hook_binary) and not Path(hook_binary).exists():
                    results.append(
                        CheckResult(
                            "Claude Code hooks",
                            False,
                            f"Binary not found at {hook_binary}",
                            fix_hint="Run: filigree install --hooks",
                        )
                    )
                else:
                    results.append(CheckResult("Claude Code hooks", True, "session-context hook registered"))
            else:
                results.append(
                    CheckResult(
                        "Claude Code hooks",
                        False,
                        "session-context hook not found in .claude/settings.json",
                        fix_hint="Run: filigree install --hooks",
                    )
                )
        except json.JSONDecodeError:
            results.append(
                CheckResult(
                    "Claude Code hooks",
                    False,
                    "Invalid .claude/settings.json",
                    fix_hint="Fix .claude/settings.json or run: filigree install --hooks",
                )
            )
    else:
        results.append(
            CheckResult(
                "Claude Code hooks",
                False,
                "No .claude/settings.json found",
                fix_hint="Run: filigree install --hooks",
            )
        )

    # 9. Check Claude Code skills
    skill_md = (filigree_dir.parent) / ".claude" / "skills" / SKILL_NAME / SKILL_MARKER
    if skill_md.exists():
        results.append(CheckResult("Claude Code skills", True, f"{SKILL_NAME} skill installed"))
    else:
        results.append(
            CheckResult(
                "Claude Code skills",
                False,
                f"{SKILL_NAME} skill not found in .claude/skills/",
                fix_hint="Run: filigree install --skills",
            )
        )

    # 9b. Check Codex skills
    codex_skill_md = (filigree_dir.parent) / ".agents" / "skills" / SKILL_NAME / SKILL_MARKER
    if codex_skill_md.exists():
        results.append(CheckResult("Codex skills", True, f"{SKILL_NAME} skill installed"))
    else:
        results.append(
            CheckResult(
                "Codex skills",
                False,
                f"{SKILL_NAME} skill not found in .agents/skills/",
                fix_hint="Run: filigree install --codex-skills",
            )
        )

    # 10. Check CLAUDE.md has instructions
    claude_md = (filigree_dir.parent) / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text()
        if FILIGREE_INSTRUCTIONS_MARKER in content:
            results.append(CheckResult("CLAUDE.md", True, "Filigree instructions present"))
        else:
            results.append(
                CheckResult(
                    "CLAUDE.md",
                    False,
                    "No filigree instructions",
                    fix_hint="Run: filigree install --claude-md",
                )
            )
    else:
        results.append(
            CheckResult(
                "CLAUDE.md",
                False,
                "File not found",
                fix_hint="Run: filigree install --claude-md",
            )
        )

    # 11. Check AGENTS.md has instructions
    agents_md = (filigree_dir.parent) / "AGENTS.md"
    if agents_md.exists():
        content = agents_md.read_text()
        if FILIGREE_INSTRUCTIONS_MARKER in content:
            results.append(CheckResult("AGENTS.md", True, "Filigree instructions present"))
        else:
            results.append(
                CheckResult(
                    "AGENTS.md",
                    False,
                    "No filigree instructions",
                    fix_hint="Run: filigree install --agents-md",
                )
            )
    # AGENTS.md is optional — don't warn if it doesn't exist

    # 12. Mode-specific checks
    from filigree.core import get_mode

    try:
        mode = get_mode(filigree_dir)
    except (AttributeError, ValueError, json.JSONDecodeError, OSError):
        mode = "ethereal"  # Fall back to default if config is unreadable

    if mode == "ethereal":
        results.extend(_doctor_ethereal_checks(filigree_dir))
    elif mode == "server":
        results.extend(_doctor_server_checks(filigree_dir))

    # 13. Check git working tree status
    try:
        result = subprocess.run(
            ["git", "-C", str(filigree_dir.parent), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            changes = result.stdout.strip()
            if changes:
                line_count = len(changes.splitlines())
                results.append(
                    CheckResult(
                        "Git working tree",
                        False,
                        f"{line_count} uncommitted change(s)",
                        fix_hint="Commit or stash changes",
                    )
                )
            else:
                results.append(CheckResult("Git working tree", True, "Clean"))
    except FileNotFoundError:
        pass  # git not installed — not an error
    except subprocess.TimeoutExpired:
        results.append(
            CheckResult(
                "Git working tree",
                False,
                "git status timed out (5s)",
                fix_hint="Check for .git/index.lock or repository corruption",
            )
        )

    return results
