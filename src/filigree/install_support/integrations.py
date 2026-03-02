"""MCP server installation for Claude Code and Codex.

Handles writing ``.mcp.json`` (Claude Code) and ``.codex/config.toml``
(Codex) entries that point to the ``filigree-mcp`` binary.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import quote

from filigree.core import FILIGREE_DIR_NAME, read_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command discovery
# ---------------------------------------------------------------------------


def _find_filigree_mcp_command() -> str:
    """Find the filigree-mcp executable path.

    Resolution order:
    1. ``shutil.which("filigree-mcp")`` — absolute path if on PATH
    2. Sibling of the running Python interpreter (covers venv case),
       probing ``filigree-mcp`` and ``filigree-mcp.exe``
    3. Sibling of the filigree binary if on PATH, probing the same names
    4. Bare ``"filigree-mcp"`` fallback
    """
    which = shutil.which("filigree-mcp")
    if which:
        return which
    # Check next to the running Python (works in venv even when not on PATH)
    for name in ("filigree-mcp", "filigree-mcp.exe"):
        candidate = Path(sys.executable).parent / name
        if candidate.is_file():
            return str(candidate)
    # Fall back to looking in the same dir as filigree
    filigree_path = shutil.which("filigree")
    if filigree_path:
        filigree_dir = Path(filigree_path).parent
        for name in ("filigree-mcp", "filigree-mcp.exe"):
            candidate = filigree_dir / name
            if candidate.is_file():
                return str(candidate)
    return "filigree-mcp"


# ---------------------------------------------------------------------------
# MCP JSON helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Claude Code MCP installation
# ---------------------------------------------------------------------------


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
            logger.warning(
                "`claude mcp add` failed (exit %d): %s",
                result.returncode,
                (result.stderr or "").strip(),
            )
        except subprocess.TimeoutExpired:
            logger.warning("`claude mcp add` timed out after 10s")
        except FileNotFoundError:
            logger.warning("claude binary disappeared between which() and run()")

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
    project_key = "filigree"

    # Scope server-mode MCP requests to this project's configured key.
    try:
        config = read_config(project_root / FILIGREE_DIR_NAME)
        prefix = config.get("prefix")
        if isinstance(prefix, str) and prefix.strip():
            project_key = prefix.strip()
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Unable to read project prefix for server-mode MCP install: %s", exc)

    encoded_key = quote(project_key, safe="")

    mcp_config["mcpServers"]["filigree"] = {
        "type": "streamable-http",
        "url": f"http://localhost:{port}/mcp/?project={encoded_key}",
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


# ---------------------------------------------------------------------------
# Codex MCP installation
# ---------------------------------------------------------------------------


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
