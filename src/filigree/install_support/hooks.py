"""Claude Code hook installation and management.

Handles registration, upgrade, and introspection of filigree hooks
in ``.claude/settings.json`` SessionStart entries.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
from pathlib import Path
from typing import Any

from filigree.core import find_filigree_command

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_CONTEXT_COMMAND = "filigree session-context"
ENSURE_DASHBOARD_COMMAND = "filigree ensure-dashboard"

# ---------------------------------------------------------------------------
# Hook command matching
# ---------------------------------------------------------------------------


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
    hook_base = hook_bin.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    hook_base_lower = hook_base.lower()
    bare_bin_lower = bare_bin.lower()
    return hook_base_lower in {bare_bin_lower, f"{bare_bin_lower}.exe"}


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


# ---------------------------------------------------------------------------
# Hook installation
# ---------------------------------------------------------------------------


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
            if not isinstance(parsed, dict):
                raise ValueError("settings.json is not a JSON object")
            settings = parsed
        except (json.JSONDecodeError, ValueError):
            backup = settings_path.with_suffix(".json.bak")
            shutil.copy2(settings_path, backup)
            logger.warning("Malformed settings.json; backed up to %s", backup)

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
