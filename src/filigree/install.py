"""Project installation helpers for filigree.

Handles:
- MCP server configuration for Claude Code and Codex
- Workflow instructions injection into CLAUDE.md / AGENTS.md
- Health checks (doctor)
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filigree.core import (
    CONFIG_FILENAME,
    CURRENT_SCHEMA_VERSION,
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    find_filigree_root,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workflow instructions (injected into CLAUDE.md / AGENTS.md)
# ---------------------------------------------------------------------------

FILIGREE_INSTRUCTIONS_MARKER = "<!-- filigree:instructions -->"

FILIGREE_INSTRUCTIONS = """\
<!-- filigree:instructions -->
## Filigree Issue Tracker

Use `filigree` for all task tracking in this project. Data lives in `.filigree/`.

### Quick Reference

```bash
# Finding work
filigree ready                              # Show issues ready to work (no blockers)
filigree list --status=open                 # All open issues
filigree list --status=in_progress          # Active work
filigree show <id>                          # Detailed issue view

# Creating & updating
filigree create "Title" --type=task --priority=2          # New issue
filigree update <id> --status=in_progress                # Claim work
filigree close <id>                                      # Mark complete
filigree close <id> --reason="explanation"               # Close with reason

# Dependencies
filigree add-dep <issue> <depends-on>       # Add dependency
filigree remove-dep <issue> <depends-on>    # Remove dependency
filigree blocked                            # Show blocked issues

# Comments & labels
filigree add-comment <id> "text"            # Add comment
filigree get-comments <id>                  # List comments
filigree add-label <id> <label>             # Add label
filigree remove-label <id> <label>          # Remove label

# Workflow templates
filigree types                              # List registered types with state flows
filigree type-info <type>                   # Full workflow definition for a type
filigree transitions <id>                   # Valid next states for an issue
filigree packs                              # List enabled workflow packs
filigree validate <id>                      # Validate issue against template
filigree guide <pack>                       # Display workflow guide for a pack

# Atomic claiming
filigree claim <id> --assignee <name>            # Claim issue (optimistic lock)
filigree claim-next --assignee <name>            # Claim highest-priority ready issue

# Batch operations
filigree batch-update <ids...> --priority=0      # Update multiple issues
filigree batch-close <ids...>                    # Close multiple with error reporting

# Planning
filigree create-plan --file plan.json            # Create milestone/phase/step hierarchy

# Event history
filigree changes --since 2026-01-01T00:00:00    # Events since timestamp
filigree events <id>                             # Event history for issue
filigree explain-state <type> <state>            # Explain a workflow state

# All commands support --json and --actor flags
filigree --actor bot-1 create "Title"            # Specify actor identity
filigree list --json                             # Machine-readable output

# Project health
filigree stats                              # Project statistics
filigree search "query"                     # Search issues
filigree doctor                             # Health check
```

### Workflow
1. `filigree ready` to find available work
2. `filigree show <id>` to review details
3. `filigree transitions <id>` to see valid state changes
4. `filigree update <id> --status=in_progress` to claim it
5. Do the work, commit code
6. `filigree close <id>` when done

### Priority Scale
- P0: Critical (drop everything)
- P1: High (do next)
- P2: Medium (default)
- P3: Low
- P4: Backlog
<!-- /filigree:instructions -->"""


# ---------------------------------------------------------------------------
# MCP configuration
# ---------------------------------------------------------------------------


def _find_filigree_mcp_command() -> str:
    """Find the filigree-mcp executable path."""
    # Check if filigree-mcp is on PATH
    which = shutil.which("filigree-mcp")
    if which:
        return which
    # Fall back to looking in the same venv as filigree
    filigree_path = shutil.which("filigree")
    if filigree_path:
        filigree_dir = Path(filigree_path).parent
        candidate = filigree_dir / "filigree-mcp"
        if candidate.exists():
            return str(candidate)
    return "filigree-mcp"


def install_claude_code_mcp(project_root: Path) -> tuple[bool, str]:
    """Install filigree-mcp into Claude Code's MCP config.

    Uses `claude mcp add` if available, otherwise writes .mcp.json directly.
    """
    filigree_mcp = _find_filigree_mcp_command()

    # Try using `claude mcp add` first
    claude_bin = shutil.which("claude")
    if claude_bin:
        try:
            result = subprocess.run(
                [
                    claude_bin,
                    "mcp",
                    "add",
                    "--transport",
                    "stdio",
                    "--scope",
                    "project",
                    "filigree",
                    "--",
                    filigree_mcp,
                    "--project",
                    str(project_root),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True, "Installed via `claude mcp add` (project scope)"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Fall back to writing .mcp.json directly
    mcp_json_path = project_root / ".mcp.json"
    mcp_config: dict[str, Any] = {}
    if mcp_json_path.exists():
        try:
            mcp_config = json.loads(mcp_json_path.read_text())
        except json.JSONDecodeError:
            # Back up the corrupt file and start fresh
            backup_path = mcp_json_path.parent / (mcp_json_path.name + ".bak")
            shutil.copy2(mcp_json_path, backup_path)
            logger.warning(
                "Malformed .mcp.json detected; backed up to %s and creating fresh config",
                backup_path,
            )
            mcp_config = {}

    if "mcpServers" not in mcp_config:
        mcp_config["mcpServers"] = {}

    mcp_config["mcpServers"]["filigree"] = {
        "type": "stdio",
        "command": filigree_mcp,
        "args": ["--project", str(project_root)],
    }

    mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n")
    return True, f"Wrote {mcp_json_path}"


def install_codex_mcp(project_root: Path) -> tuple[bool, str]:
    """Install filigree-mcp into Codex's MCP config.

    Writes to project-scoped .codex/config.toml if project is trusted,
    otherwise to ~/.codex/config.toml.
    """
    filigree_mcp = _find_filigree_mcp_command()

    # Try project-scoped first
    codex_dir = project_root / ".codex"
    if not codex_dir.exists():
        codex_dir.mkdir(exist_ok=True)

    config_path = codex_dir / "config.toml"

    # Read existing config if present
    existing = ""
    if config_path.exists():
        existing = config_path.read_text()

    # Check if already configured using proper TOML parsing
    if existing.strip():
        try:
            parsed = tomllib.loads(existing)
            if "filigree" in parsed.get("mcp_servers", {}):
                return True, "Already configured in .codex/config.toml"
        except tomllib.TOMLDecodeError:
            # Existing config is malformed; we'll append anyway
            pass

    # Escape backslashes in paths for TOML double-quoted strings
    safe_command = str(filigree_mcp).replace("\\", "\\\\")
    safe_project = str(project_root).replace("\\", "\\\\")

    # Append MCP server config
    toml_block = f"""
[mcp_servers.filigree]
command = "{safe_command}"
args = ["--project", "{safe_project}"]
"""

    with config_path.open("a") as f:
        f.write(toml_block)

    return True, f"Wrote {config_path}"


# ---------------------------------------------------------------------------
# Instruction file injection
# ---------------------------------------------------------------------------


def inject_instructions(file_path: Path) -> tuple[bool, str]:
    """Inject filigree workflow instructions into a markdown file.

    If the file doesn't exist, creates it with just the instructions.
    If it exists and already has the marker, replaces the block.
    If it exists without the marker, appends the block.
    """
    if file_path.exists():
        content = file_path.read_text()
        if FILIGREE_INSTRUCTIONS_MARKER in content:
            # Replace existing block
            start = content.index(FILIGREE_INSTRUCTIONS_MARKER)
            end_marker = "<!-- /filigree:instructions -->"
            if end_marker in content:
                end = content.index(end_marker) + len(end_marker)
                content = content[:start] + FILIGREE_INSTRUCTIONS + content[end:]
            else:
                # Malformed — just replace from marker to end
                content = content[:start] + FILIGREE_INSTRUCTIONS
            file_path.write_text(content)
            return True, f"Updated instructions in {file_path}"
        else:
            # Append
            if not content.endswith("\n"):
                content += "\n"
            content += "\n" + FILIGREE_INSTRUCTIONS + "\n"
            file_path.write_text(content)
            return True, f"Appended instructions to {file_path}"
    else:
        file_path.write_text(FILIGREE_INSTRUCTIONS + "\n")
        return True, f"Created {file_path}"


# ---------------------------------------------------------------------------
# .gitignore
# ---------------------------------------------------------------------------


def ensure_gitignore(project_root: Path) -> tuple[bool, str]:
    """Ensure .filigree/ is in .gitignore."""
    gitignore = project_root / ".gitignore"
    filigree_pattern = ".filigree/"

    if gitignore.exists():
        content = gitignore.read_text()
        if filigree_pattern in content:
            return True, ".filigree/ already in .gitignore"
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# Filigree issue tracker\n{filigree_pattern}\n"
        gitignore.write_text(content)
        return True, f"Added {filigree_pattern} to .gitignore"
    else:
        gitignore.write_text(f"# Filigree issue tracker\n{filigree_pattern}\n")
        return True, f"Created .gitignore with {filigree_pattern}"


# ---------------------------------------------------------------------------
# Claude Code hooks
# ---------------------------------------------------------------------------

SESSION_CONTEXT_COMMAND = "filigree session-context"
ENSURE_DASHBOARD_COMMAND = "filigree ensure-dashboard"


def _has_hook_command(settings: dict[str, Any], command: str) -> bool:
    """Check whether *command* already appears in SessionStart hooks."""
    for matcher in settings.get("hooks", {}).get("SessionStart", []):
        for hook in matcher.get("hooks", []):
            if hook.get("command") == command:
                return True
    return False


def install_claude_code_hooks(project_root: Path) -> tuple[bool, str]:
    """Register ``filigree session-context`` and ``filigree ensure-dashboard``
    as Claude Code SessionStart hooks in ``.claude/settings.json``.

    Idempotent — won't duplicate existing entries.
    """
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_path = claude_dir / "settings.json"

    settings: dict[str, Any] = {}
    if settings_path.exists():
        try:
            raw = settings_path.read_text()
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                settings = parsed
            else:
                # Non-object JSON — back up and start fresh
                backup = settings_path.with_suffix(".json.bak")
                import shutil as _shutil

                _shutil.copy2(settings_path, backup)
                logger.warning("Non-object settings.json; backed up to %s", backup)
        except json.JSONDecodeError:
            backup = settings_path.with_suffix(".json.bak")
            import shutil as _shutil

            _shutil.copy2(settings_path, backup)
            logger.warning("Corrupt settings.json; backed up to %s", backup)

    # Build list of commands to add
    commands_to_add: list[str] = []
    if not _has_hook_command(settings, SESSION_CONTEXT_COMMAND):
        commands_to_add.append(SESSION_CONTEXT_COMMAND)

    # Only add dashboard hook if the [dashboard] extra is available
    try:
        import filigree.dashboard  # noqa: F401

        if not _has_hook_command(settings, ENSURE_DASHBOARD_COMMAND):
            commands_to_add.append(ENSURE_DASHBOARD_COMMAND)
    except ImportError:
        pass

    if not commands_to_add:
        return True, "Hooks already registered in .claude/settings.json"

    # Ensure structure exists
    if "hooks" not in settings:
        settings["hooks"] = {}
    if "SessionStart" not in settings["hooks"]:
        settings["hooks"]["SessionStart"] = []

    # Find or create the matcher block for filigree hooks
    filigree_hooks: list[dict[str, Any]] = []
    matcher_block = None
    for matcher in settings["hooks"]["SessionStart"]:
        for hook in matcher.get("hooks", []):
            cmd = hook.get("command", "")
            if "filigree" in cmd:
                matcher_block = matcher
                filigree_hooks = matcher.get("hooks", [])
                break
        if matcher_block is not None:
            break

    if matcher_block is None:
        matcher_block = {"hooks": []}
        settings["hooks"]["SessionStart"].append(matcher_block)
        filigree_hooks = matcher_block["hooks"]

    for cmd in commands_to_add:
        filigree_hooks.append({"type": "command", "command": cmd, "timeout": 5000})
    matcher_block["hooks"] = filigree_hooks

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    added = ", ".join(commands_to_add)
    return True, f"Registered hooks in .claude/settings.json: {added}"


# ---------------------------------------------------------------------------
# Claude Code skills
# ---------------------------------------------------------------------------

SKILL_NAME = "filigree-workflow"
SKILL_MARKER = "SKILL.md"


def _get_skills_source_dir() -> Path:
    """Return the path to the bundled skills directory inside the package."""
    return Path(__file__).parent / "skills"


def install_skills(project_root: Path) -> tuple[bool, str]:
    """Copy filigree skill pack into ``.claude/skills/`` for the project.

    Idempotent — overwrites existing skill files to keep them up-to-date
    with the installed filigree version.
    """
    source_dir = _get_skills_source_dir()
    skill_source = source_dir / SKILL_NAME
    if not skill_source.is_dir():
        return False, f"Skill source not found at {skill_source}"

    target_dir = project_root / ".claude" / "skills" / SKILL_NAME
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    # Copy the skill, overwriting to pick up version upgrades
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(skill_source, target_dir)

    return True, f"Installed skill pack to {target_dir}"


# ---------------------------------------------------------------------------
# Doctor checks
# ---------------------------------------------------------------------------


class CheckResult:
    """Result of a single doctor check."""

    def __init__(self, name: str, passed: bool, message: str, *, fix_hint: str = "") -> None:
        self.name = name
        self.passed = passed
        self.message = message
        self.fix_hint = fix_hint

    @property
    def icon(self) -> str:
        return "OK" if self.passed else "!!"


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
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("SELECT COUNT(*) FROM issues")
            count = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
            # 3b. Check schema version
            schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.close()
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
            if "filigree" in mcp.get("mcpServers", {}):
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
        except json.JSONDecodeError:
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
        content = codex_config.read_text()
        if "[mcp_servers.filigree]" in content:
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

    # 12. Check git working tree status
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
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Not in a git repo or git not available

    return results
