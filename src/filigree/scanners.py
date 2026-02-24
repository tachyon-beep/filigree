"""Scanner TOML registry for filigree.

Reads scanner definitions from .filigree/scanners/*.toml.
Each TOML file defines one scanner with a command template.

Template variables substituted at invocation:
    {file}         — target file path
    {api_url}      — dashboard URL (default http://localhost:8377)
    {project_root} — filigree project root directory
    {scan_run_id}  — MCP-generated correlation ID for tracking results
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"^[\w-]+$")


@dataclass(frozen=True)
class ScannerConfig:
    """A scanner definition loaded from a TOML file."""

    name: str
    description: str
    command: str
    args: list[str] = field(default_factory=list)
    file_types: list[str] = field(default_factory=list)

    def build_command(
        self,
        *,
        file_path: str,
        api_url: str = "http://localhost:8377",
        project_root: str = ".",
        scan_run_id: str = "",
    ) -> list[str]:
        """Build the full command list with template variables substituted.

        Raises ValueError if the command string is malformed (e.g. unmatched quotes).
        """
        subs = {
            "{file}": str(file_path),
            "{api_url}": str(api_url),
            "{project_root}": str(project_root),
            "{scan_run_id}": str(scan_run_id),
        }
        try:
            base = shlex.split(self.command)
        except (TypeError, ValueError) as e:
            msg = f"Malformed command string in scanner {self.name!r}: {e}"
            raise ValueError(msg) from e
        expanded_base = []
        for token in base:
            for key, val in subs.items():
                token = token.replace(key, val)
            expanded_base.append(token)
        expanded_args = []
        for raw_arg in self.args:
            if not isinstance(raw_arg, str):
                msg = f"Malformed args in scanner {self.name!r}: expected string entries"
                raise ValueError(msg)
            arg = raw_arg
            for key, val in subs.items():
                arg = arg.replace(key, val)
            expanded_args.append(arg)
        return expanded_base + expanded_args

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "file_types": self.file_types,
        }


def _parse_toml(path: Path) -> ScannerConfig | None:
    """Parse a single scanner TOML file. Returns None on error."""
    import tomllib

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Failed to read scanner TOML: %s", path, exc_info=True)
        return None

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        logger.warning("Failed to parse scanner TOML: %s", path, exc_info=True)
        return None

    scanner = data.get("scanner")
    if not isinstance(scanner, dict):
        logger.warning("Invalid scanner TOML (missing [scanner] table): %s", path)
        return None

    name = scanner.get("name")
    command = scanner.get("command")
    description = scanner.get("description", "")
    args = scanner.get("args", [])
    file_types = scanner.get("file_types", [])

    if not isinstance(name, str) or not isinstance(command, str):
        logger.warning("Invalid scanner TOML ([scanner] name/command must be strings): %s", path)
        return None
    if not isinstance(description, str):
        logger.warning("Invalid scanner TOML ([scanner] description must be a string): %s", path)
        return None
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        logger.warning("Invalid scanner TOML ([scanner] args must be list[str]): %s", path)
        return None
    if not isinstance(file_types, list) or not all(isinstance(ext, str) for ext in file_types):
        logger.warning("Invalid scanner TOML ([scanner] file_types must be list[str]): %s", path)
        return None
    if name != path.stem:
        logger.warning(
            "Invalid scanner TOML ([scanner] name must match filename stem %r): %s",
            path.stem,
            path,
        )
        return None
    if not _SAFE_NAME_RE.match(name):
        logger.warning("Invalid scanner TOML ([scanner] name contains unsafe characters): %s", path)
        return None

    return ScannerConfig(
        name=name,
        description=description,
        command=command,
        args=args,
        file_types=file_types,
    )


def list_scanners(scanners_dir: Path) -> list[ScannerConfig]:
    """Read all *.toml files from the scanners directory.

    Skips .toml.example files, malformed files, and non-TOML files.
    Returns an empty list if the directory doesn't exist.
    """
    if not scanners_dir.is_dir():
        return []
    results = []
    for p in sorted(scanners_dir.iterdir()):
        if p.suffix != ".toml" or p.name.endswith(".toml.example"):
            continue
        cfg = _parse_toml(p)
        if cfg is not None:
            results.append(cfg)
    return results


def load_scanner(scanners_dir: Path, name: str) -> ScannerConfig | None:
    """Load a single scanner by name. Returns None if not found or name is invalid."""
    if not _SAFE_NAME_RE.match(name):
        return None  # Reject path traversal attempts
    toml_path = scanners_dir / f"{name}.toml"
    if not toml_path.is_file():
        return None
    return _parse_toml(toml_path)


def validate_scanner_command(
    command: str | Sequence[str],
    *,
    project_root: str | Path | None = None,
) -> str | None:
    """Check that the first token of a command is available on PATH.

    Accepts either a raw shell command string or a pre-tokenized command list.
    Returns None if valid, or an error message string if not found.

    When *project_root* is provided, relative executable paths such as
    ``./scripts/run_scan`` are validated relative to that project root.
    """
    tokens: list[str]
    if isinstance(command, str):
        try:
            tokens = shlex.split(command)
        except ValueError:
            return f"Malformed command string: {command!r}"
    else:
        try:
            tokens = [str(t) for t in command]
        except (TypeError, ValueError):
            return "Malformed command token list"
    if not tokens:
        return "Empty command"
    binary = tokens[0]

    # Path-like executable tokens (contains a separator or explicit relative
    # prefix) should be checked as files, optionally against project_root.
    if "/" in binary or "\\" in binary:
        candidate_paths: list[Path] = []
        binary_path = Path(binary)
        if binary_path.is_absolute():
            candidate_paths.append(binary_path)
        else:
            if project_root is not None:
                candidate_paths.append(Path(project_root) / binary_path)
            candidate_paths.append(binary_path)
        for candidate in candidate_paths:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return None

    if shutil.which(binary) is None:
        return f"Command {binary!r} not found on PATH"
    return None
