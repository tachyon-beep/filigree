"""Project installation helpers for filigree.

Handles:
- MCP server configuration for Claude Code and Codex
- Workflow instructions injection into CLAUDE.md / AGENTS.md
- Health checks (doctor)

Implementation is split across ``install_support/`` submodules;
this module re-exports all public symbols for backward compatibility.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.resources
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Re-exports from install_support subpackage
# ---------------------------------------------------------------------------
# These maintain backward compatibility for all existing callers:
#   - tests/test_install.py
#   - tests/test_hooks.py
#   - tests/test_peripheral_fixes.py
#   - tests/test_mcp.py
#   - src/filigree/hooks.py
#   - src/filigree/cli_commands/admin.py
from filigree.install_support import (
    FILIGREE_INSTRUCTIONS_MARKER,
    SKILL_MARKER,
    SKILL_NAME,
)
from filigree.install_support.doctor import (
    CheckResult,
    run_doctor,
)
from filigree.install_support.hooks import (
    ENSURE_DASHBOARD_COMMAND,
    SESSION_CONTEXT_COMMAND,
    _extract_hook_binary,
    _has_hook_command,
    _hook_cmd_matches,
    _upgrade_hook_commands,
    install_claude_code_hooks,
)
from filigree.install_support.integrations import (
    _find_filigree_mcp_command,
    _read_mcp_json,
    install_claude_code_mcp,
    install_codex_mcp,
)

__all__ = [
    # Constants
    "ENSURE_DASHBOARD_COMMAND",
    "FILIGREE_INSTRUCTIONS",
    "FILIGREE_INSTRUCTIONS_MARKER",
    "SESSION_CONTEXT_COMMAND",
    "SKILL_MARKER",
    "SKILL_NAME",
    # Doctor
    "CheckResult",
    # Local
    "_build_instructions_block",
    # Hooks
    "_extract_hook_binary",
    # Integrations
    "_find_filigree_mcp_command",
    "_get_skills_source_dir",
    "_has_hook_command",
    "_hook_cmd_matches",
    "_install_skill_to",
    "_instructions_hash",
    "_instructions_text",
    "_instructions_version",
    "_read_mcp_json",
    "_upgrade_hook_commands",
    "ensure_gitignore",
    "inject_instructions",
    "install_claude_code_hooks",
    "install_claude_code_mcp",
    "install_codex_mcp",
    "install_codex_skills",
    "install_skills",
    "run_doctor",
]

# ---------------------------------------------------------------------------
# Workflow instructions (injected into CLAUDE.md / AGENTS.md)
# ---------------------------------------------------------------------------

_END_MARKER = "<!-- /filigree:instructions -->"


def _instructions_text() -> str:
    """Read the instructions template from the shipped data file."""
    ref = importlib.resources.files("filigree.data").joinpath("instructions.md")
    return ref.read_text(encoding="utf-8")


def _instructions_hash() -> str:
    """Return first 8 hex characters of SHA256 of the instructions content."""
    return hashlib.sha256(_instructions_text().encode()).hexdigest()[:8]


def _instructions_version() -> str:
    """Return a sensible filigree version for instructions markers.

    Falls back to the package ``__version__`` (which itself handles
    source-checkout cases) when distribution metadata is unavailable.
    """
    try:
        return importlib.metadata.version("filigree")
    except importlib.metadata.PackageNotFoundError:
        from filigree import __version__

        return __version__ or "0.0.0-dev"


def _build_instructions_block() -> str:
    """Build the full instructions block with versioned markers."""
    text = _instructions_text()
    version = _instructions_version()
    h = _instructions_hash()
    opening = f"<!-- filigree:instructions:v{version}:{h} -->"
    return f"{opening}\n{text}{_END_MARKER}"


FILIGREE_INSTRUCTIONS = _build_instructions_block()


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
# Claude Code skills
# ---------------------------------------------------------------------------


def _get_skills_source_dir() -> Path:
    """Return the path to the bundled skills directory inside the package."""
    return Path(__file__).parent / "skills"


def _install_skill_to(project_root: Path, target_subpath: Path) -> tuple[bool, str]:
    """Copy the filigree skill pack into *target_subpath* under *project_root*.

    Idempotent — overwrites existing skill files to keep them up-to-date
    with the installed filigree version.
    """
    source_dir = _get_skills_source_dir()
    skill_source = source_dir / SKILL_NAME
    if not skill_source.is_dir():
        return False, f"Skill source not found at {skill_source}"

    target_dir = project_root / target_subpath / SKILL_NAME
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(skill_source, target_dir)

    return True, f"Installed skill pack to {target_dir}"


def install_skills(project_root: Path) -> tuple[bool, str]:
    """Copy filigree skill pack into ``.claude/skills/`` for the project."""
    return _install_skill_to(project_root, Path(".claude") / "skills")


def install_codex_skills(project_root: Path) -> tuple[bool, str]:
    """Copy filigree skill pack into ``.agents/skills/`` for Codex."""
    return _install_skill_to(project_root, Path(".agents") / "skills")
