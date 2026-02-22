"""Project installation helpers for filigree.

Handles:
- MCP server configuration for Claude Code and Codex
- Workflow instructions injection into CLAUDE.md / AGENTS.md
- Health checks (doctor)
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.resources
import json
import logging
import shlex
import shutil
import sqlite3
import subprocess
import sys
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
    find_filigree_command,
    find_filigree_root,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workflow instructions (injected into CLAUDE.md / AGENTS.md)
# ---------------------------------------------------------------------------

# Detection prefix — matches both old "<!-- filigree:instructions -->" and
# new "<!-- filigree:instructions:v1.2.0:abc12345 -->" formats.
FILIGREE_INSTRUCTIONS_MARKER = "<!-- filigree:instructions"

_END_MARKER = "<!-- /filigree:instructions -->"


def _instructions_text() -> str:
    """Read the instructions template from the shipped data file."""
    ref = importlib.resources.files("filigree.data").joinpath("instructions.md")
    return ref.read_text(encoding="utf-8")


def _instructions_hash() -> str:
    """Return first 8 chars of SHA256 of the instructions content."""
    return hashlib.sha256(_instructions_text().encode()).hexdigest()[:8]


def _instructions_version() -> str:
    """Return the installed filigree package version."""
    return importlib.metadata.version("filigree")


def _build_instructions_block() -> str:
    """Build the full instructions block with versioned markers."""
    text = _instructions_text()
    version = _instructions_version()
    h = _instructions_hash()
    opening = f"<!-- filigree:instructions:v{version}:{h} -->"
    return f"{opening}\n{text}{_END_MARKER}"


FILIGREE_INSTRUCTIONS = _build_instructions_block()


# ---------------------------------------------------------------------------
# MCP configuration
# ---------------------------------------------------------------------------


def _find_filigree_mcp_command() -> str:
    """Find the filigree-mcp executable path.

    Resolution order:
    1. ``shutil.which("filigree-mcp")`` — absolute path if on PATH
    2. Sibling of the running Python interpreter (covers venv case)
    3. Sibling of the filigree binary if on PATH
    4. Bare ``"filigree-mcp"`` fallback
    """
    which = shutil.which("filigree-mcp")
    if which:
        return which
    # Check next to the running Python (works in venv even when not on PATH)
    candidate = Path(sys.executable).parent / "filigree-mcp"
    if candidate.exists():
        return str(candidate)
    # Fall back to looking in the same dir as filigree
    filigree_path = shutil.which("filigree")
    if filigree_path:
        filigree_dir = Path(filigree_path).parent
        candidate = filigree_dir / "filigree-mcp"
        if candidate.exists():
            return str(candidate)
    return "filigree-mcp"


def _read_mcp_json(mcp_json_path: Path) -> dict[str, Any]:
    """Read existing .mcp.json or return a default structure."""
    if mcp_json_path.exists():
        try:
            raw = json.loads(mcp_json_path.read_text())
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            mcp_config = raw
        except (json.JSONDecodeError, ValueError):
            # Back up the corrupt/non-object file and start fresh
            backup_path = mcp_json_path.parent / (mcp_json_path.name + ".bak")
            shutil.copy2(mcp_json_path, backup_path)
            logger.warning(
                "Malformed .mcp.json detected; backed up to %s and creating fresh config",
                backup_path,
            )
            mcp_config = {}
    else:
        mcp_config = {}

    if "mcpServers" not in mcp_config or not isinstance(mcp_config["mcpServers"], dict):
        mcp_config["mcpServers"] = {}

    return mcp_config


def _install_mcp_ethereal_mode(project_root: Path) -> tuple[bool, str]:
    """Existing stdio-based MCP install (current behavior).

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
    mcp_config = _read_mcp_json(mcp_json_path)

    mcp_config["mcpServers"]["filigree"] = {
        "type": "stdio",
        "command": filigree_mcp,
        "args": ["--project", str(project_root)],
    }

    mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n")
    return True, f"Wrote {mcp_json_path}"


def _install_mcp_server_mode(project_root: Path, port: int) -> tuple[bool, str]:
    """Write streamable-http MCP config pointing to the daemon."""
    mcp_json_path = project_root / ".mcp.json"
    mcp_config = _read_mcp_json(mcp_json_path)

    mcp_config["mcpServers"]["filigree"] = {
        "type": "streamable-http",
        "url": f"http://localhost:{port}/mcp/",
    }

    mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n")
    return True, f"Wrote {mcp_json_path} (streamable-http, port {port})"


def install_claude_code_mcp(
    project_root: Path,
    *,
    mode: str = "ethereal",
    server_port: int = 8377,
) -> tuple[bool, str]:
    """Install filigree MCP into Claude Code's config.

    In ethereal mode: stdio transport (per-session process).
    In server mode: streamable-http transport pointing to daemon.
    """
    if mode == "server":
        return _install_mcp_server_mode(project_root, server_port)
    return _install_mcp_ethereal_mode(project_root)


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
            return False, f"Existing {config_path} contains malformed TOML; fix or remove it before configuring"

    # Escape backslashes and double quotes in paths for TOML double-quoted strings
    safe_command = str(filigree_mcp).replace("\\", "\\\\").replace('"', '\\"')
    safe_project = str(project_root).replace("\\", "\\\\").replace('"', '\\"')

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
            end_pos = content.find(_END_MARKER, start)
            if end_pos != -1:
                end = end_pos + len(_END_MARKER)
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


def _hook_cmd_matches(hook_command: str, bare_command: str) -> bool:
    """Check whether *hook_command* is a bare, absolute-path, or module form of *bare_command*.

    Uses ``shlex.split`` so paths containing spaces (common on Windows)
    are handled correctly.

    Matches:
    - Exact: ``"filigree session-context"``
    - Path:  ``"/path/to/filigree session-context"``
    - Quoted path: ``"'/path with spaces/filigree' session-context"``
    - Module: ``"/path/to/python -m filigree session-context"``
    """
    if hook_command == bare_command:
        return True
    try:
        hook_tokens = shlex.split(hook_command)
        bare_tokens = shlex.split(bare_command)
    except ValueError:
        return False
    if not hook_tokens or not bare_tokens:
        return False
    n = len(bare_tokens)
    if len(hook_tokens) < n:
        return False
    # Subcommand tokens (everything after the binary) must match exactly
    if n > 1 and hook_tokens[-(n - 1) :] != bare_tokens[1:]:
        return False
    # Binary token: allow exact match or path-qualified match
    bare_bin = bare_tokens[0]  # e.g. "filigree"
    hook_bin = hook_tokens[-n]  # token in the matching position
    if hook_bin == bare_bin:
        return True
    return hook_bin.endswith(("/" + bare_bin, "\\" + bare_bin))


def _has_hook_command(settings: dict[str, Any], command: str) -> bool:
    """Check whether *command* already appears in SessionStart hooks."""
    if not isinstance(settings, dict):
        return False
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    session_start = hooks.get("SessionStart", [])
    if not isinstance(session_start, list):
        return False
    for matcher in session_start:
        if not isinstance(matcher, dict):
            continue
        hook_list = matcher.get("hooks", [])
        if not isinstance(hook_list, list):
            continue
        for hook in hook_list:
            if isinstance(hook, dict) and _hook_cmd_matches(hook.get("command", ""), command):
                return True
    return False


def _upgrade_hook_commands(settings: dict[str, Any], bare_command: str, new_command: str) -> bool:
    """Replace hook commands matching *bare_command* with *new_command*.

    Walks the settings structure and replaces hook commands that match
    the bare form (either bare or stale absolute path) with the current
    absolute-path command.  Returns ``True`` if anything was changed.
    """
    changed = False
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    session_start = hooks.get("SessionStart", [])
    if not isinstance(session_start, list):
        return False
    for matcher in session_start:
        if not isinstance(matcher, dict):
            continue
        hook_list = matcher.get("hooks", [])
        if not isinstance(hook_list, list):
            continue
        for hook in hook_list:
            if not isinstance(hook, dict):
                continue
            cmd = hook.get("command", "")
            if _hook_cmd_matches(cmd, bare_command) and cmd != new_command:
                hook["command"] = new_command
                changed = True
    return changed


def _extract_hook_binary(settings: dict[str, Any], bare_command: str) -> str | None:
    """Extract the binary path from the first hook matching *bare_command*.

    Uses ``shlex.split`` to correctly handle quoted paths that contain
    spaces.  Returns ``None`` if no matching hook is found.
    """
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return None
    session_start = hooks.get("SessionStart", [])
    if not isinstance(session_start, list):
        return None
    for matcher in session_start:
        if not isinstance(matcher, dict):
            continue
        hook_list = matcher.get("hooks", [])
        if not isinstance(hook_list, list):
            continue
        for hook in hook_list:
            if not isinstance(hook, dict):
                continue
            cmd = hook.get("command", "")
            if _hook_cmd_matches(cmd, bare_command):
                if not cmd:
                    return None
                try:
                    tokens = shlex.split(cmd)
                except ValueError:
                    tokens = cmd.split()
                return tokens[0] if tokens else None
    return None


def install_claude_code_hooks(project_root: Path) -> tuple[bool, str]:
    """Register ``filigree session-context`` and ``filigree ensure-dashboard``
    as Claude Code SessionStart hooks in ``.claude/settings.json``.

    Uses absolute paths for the filigree binary so hooks work even when
    filigree is installed in a project-local venv that isn't on PATH.

    Idempotent — won't duplicate existing entries.  Re-running upgrades
    bare or stale absolute-path commands to the current binary location.
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

    # Resolve the filigree command tokens to build hook command strings.
    # shlex.join properly quotes tokens containing spaces so the resulting
    # shell command is safe on all platforms (e.g. Windows paths with spaces).
    filigree_tokens = find_filigree_command()
    filigree_prefix = shlex.join(filigree_tokens)
    session_context_cmd = f"{filigree_prefix} session-context"
    ensure_dashboard_cmd = f"{filigree_prefix} ensure-dashboard"

    # Upgrade existing bare/stale commands to current absolute paths
    upgraded: list[str] = []
    if _upgrade_hook_commands(settings, SESSION_CONTEXT_COMMAND, session_context_cmd):
        upgraded.append(SESSION_CONTEXT_COMMAND)
    try:
        import filigree.dashboard

        if _upgrade_hook_commands(settings, ENSURE_DASHBOARD_COMMAND, ensure_dashboard_cmd):
            upgraded.append(ENSURE_DASHBOARD_COMMAND)
    except ImportError:
        pass

    # Build list of commands to add (those not already present)
    commands_to_add: list[str] = []
    if not _has_hook_command(settings, SESSION_CONTEXT_COMMAND):
        commands_to_add.append(session_context_cmd)

    # Only add dashboard hook if the [dashboard] extra is available
    try:
        import filigree.dashboard  # noqa: F401

        if not _has_hook_command(settings, ENSURE_DASHBOARD_COMMAND):
            commands_to_add.append(ensure_dashboard_cmd)
    except ImportError:
        pass

    if not commands_to_add and not upgraded:
        return True, "Hooks already registered in .claude/settings.json"

    if not commands_to_add and upgraded:
        # Only upgrades, no new hooks needed
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        return True, f"Upgraded hook commands in .claude/settings.json to use {filigree_prefix}"

    # Ensure structure exists (replace non-dict/non-list values)
    if "hooks" not in settings or not isinstance(settings.get("hooks"), dict):
        settings["hooks"] = {}
    if "SessionStart" not in settings["hooks"] or not isinstance(settings["hooks"].get("SessionStart"), list):
        settings["hooks"]["SessionStart"] = []

    # Find or create the matcher block for filigree hooks
    filigree_hooks: list[dict[str, Any]] = []
    matcher_block = None
    for matcher in settings["hooks"]["SessionStart"]:
        if not isinstance(matcher, dict):
            continue
        hook_list = matcher.get("hooks", [])
        if not isinstance(hook_list, list):
            continue
        for hook in hook_list:
            if not isinstance(hook, dict):
                continue
            cmd = hook.get("command", "")
            if "filigree" in cmd:
                matcher_block = matcher
                filigree_hooks = hook_list
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


def install_codex_skills(project_root: Path) -> tuple[bool, str]:
    """Copy filigree skill pack into ``.agents/skills/`` for Codex.

    Codex discovers skills at ``.agents/skills/<name>/SKILL.md``.
    Uses the same skill content as Claude Code.

    Idempotent — overwrites existing skill files to keep them up-to-date
    with the installed filigree version.
    """
    source_dir = _get_skills_source_dir()
    skill_source = source_dir / SKILL_NAME
    if not skill_source.is_dir():
        return False, f"Skill source not found at {skill_source}"

    target_dir = project_root / ".agents" / "skills" / SKILL_NAME
    target_dir.parent.mkdir(parents=True, exist_ok=True)

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
            filigree_mcp_entry = mcp.get("mcpServers", {}).get("filigree")
            if filigree_mcp_entry:
                # Validate binary path if it's an absolute path
                mcp_command = filigree_mcp_entry.get("command", "") if isinstance(filigree_mcp_entry, dict) else ""
                if "/" in mcp_command and not Path(mcp_command).exists():
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
                # Validate binary path if it's an absolute path
                hook_binary = _extract_hook_binary(s, SESSION_CONTEXT_COMMAND)
                if hook_binary and "/" in hook_binary and not Path(hook_binary).exists():
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
    except (json.JSONDecodeError, OSError):
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
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Not in a git repo or git not available

    return results
