"""Shared utilities for filigree example scanner scripts.

These are helper functions for external scanner integrations — NOT part of
the filigree core package. The API (POST /api/v1/scan-results) is the
first-class product; these utilities are documentation-by-code.

Functions:
    find_files      — Walk directory tree collecting source files
    load_context    — Load repo context files (CLAUDE.md, ARCHITECTURE.md)
    parse_findings  — Parse structured markdown output into finding dicts
    severity_map    — Map scanner-native severities to filigree severities
    post_to_api     — POST findings to filigree scan API with error handling
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

EXCLUDE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    ".eggs",
    "htmlcov",
    "node_modules",
    ".filigree",
}

BINARY_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".bmp",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".whl",
    ".egg",
    ".tar",
    ".gz",
    ".zip",
    ".bz2",
    ".xz",
    ".pdf",
    ".doc",
    ".docx",
    ".bin",
    ".dat",
    ".pickle",
    ".pkl",
}

# Closed vocabulary for rule_id — maps free-text categories to canonical IDs
VALID_RULE_IDS = frozenset(
    {
        "logic-error",
        "resource-leak",
        "race-condition",
        "type-error",
        "error-handling",
        "injection",
        "performance",
        "api-misuse",
        "other",
    }
)

# Severity mapping from scanner-native terms to filigree severities
_SEVERITY_MAP = {
    "critical": "critical",
    "major": "high",
    "minor": "medium",
    "trivial": "low",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "info",
}

# Category keywords → rule_id mapping
_CATEGORY_KEYWORDS = {
    "logic": "logic-error",
    "off-by-one": "logic-error",
    "unreachable": "logic-error",
    "resource": "resource-leak",
    "leak": "resource-leak",
    "unclosed": "resource-leak",
    "race": "race-condition",
    "concurren": "race-condition",
    "lock": "race-condition",
    "type": "type-error",
    "contract": "type-error",
    "schema": "type-error",
    "error handling": "error-handling",
    "exception": "error-handling",
    "except": "error-handling",
    "inject": "injection",
    "sql": "injection",
    "saniti": "injection",
    "xss": "injection",
    "performance": "performance",
    "o(n": "performance",
    "blocking": "performance",
    "api": "api-misuse",
    "deprecated": "api-misuse",
    "misuse": "api-misuse",
}


# ── File discovery ──────────────────────────────────────────────────────


def _is_python(path: Path) -> bool:
    return path.suffix == ".py" and not path.name.startswith("test_")


def find_files(
    root: Path,
    *,
    file_type: str = "python",
    exclude_dirs: set[Path] | None = None,
    max_files: int = 0,
) -> list[Path]:
    """Walk *root* collecting files, pruning EXCLUDE_DIRS.

    Args:
        root: Directory to scan.
        file_type: "python" to filter .py files, "all" for everything non-binary.
        exclude_dirs: Additional directories to skip.
        max_files: If > 0, truncate the result list to this many files.

    Returns:
        Sorted list of file paths.
    """
    extra_exclude = {p.resolve() for p in exclude_dirs} if exclude_dirs else set()
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and (Path(dirpath) / d).resolve() not in extra_exclude]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            if fpath.suffix in {".pyc", ".pyo"}:
                continue
            candidates.append(fpath)

    candidates.sort()
    if file_type == "python":
        candidates = [p for p in candidates if _is_python(p)]
    else:
        candidates = [p for p in candidates if p.suffix not in BINARY_EXTENSIONS]

    if max_files > 0:
        candidates = candidates[:max_files]
    return candidates


# ── Context loader ──────────────────────────────────────────────────────


def load_context(repo_root: Path) -> str:
    """Load repo context files (CLAUDE.md, ARCHITECTURE.md) for prompt injection."""
    parts: list[str] = []
    for name in ("CLAUDE.md", "ARCHITECTURE.md"):
        path = repo_root / name
        if path.exists():
            parts.append(f"--- {name} ---\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


# ── Severity mapping ────────────────────────────────────────────────────


def severity_map(severity: str) -> str:
    """Map scanner-native severity to filigree severity.

    Returns one of: critical, high, medium, low, info.
    Unknown values map to "info" with a warning log.
    """
    key = severity.strip().lower()
    mapped = _SEVERITY_MAP.get(key)
    if mapped is not None:
        return mapped
    logger.warning("Unknown severity %r, mapping to 'info'", severity)
    return "info"


# ── Rule ID mapping ─────────────────────────────────────────────────────


def _infer_rule_id(summary: str) -> str:
    """Infer a canonical rule_id from a finding's summary text."""
    lower = summary.lower()
    for keyword, rule_id in _CATEGORY_KEYWORDS.items():
        if keyword in lower:
            return rule_id
    return "other"


# ── Finding parser ──────────────────────────────────────────────────────


def parse_findings(text: str, *, file_path: str = "") -> list[dict[str, Any]]:
    """Parse structured markdown output into finding dicts.

    Expects sections separated by '---' with ## Summary, ## Severity,
    ## Evidence, ## Root Cause Hypothesis, ## Suggested Fix subsections.

    Returns:
        List of finding dicts ready for POST to /api/v1/scan-results.
    """
    if not text or not text.strip():
        return []

    # Split on --- separator (must be on its own line)
    sections = re.split(r"\n---+\n", text)
    findings: list[dict[str, Any]] = []

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Skip "No concrete bug found" sentinel
        if "No concrete bug found" in section:
            continue

        # Extract ## Summary
        summary_m = re.search(r"##\s*Summary\s*\n(.+?)(?=\n##|\Z)", section, re.DOTALL)
        if not summary_m:
            continue
        summary = summary_m.group(1).strip()

        # Extract ## Severity
        sev_m = re.search(r"Severity:\s*(\S+)", section, re.IGNORECASE)
        raw_severity = sev_m.group(1) if sev_m else "info"

        # Extract ## Evidence
        evidence_m = re.search(r"##\s*Evidence\s*\n(.+?)(?=\n##|\Z)", section, re.DOTALL)
        evidence = evidence_m.group(1).strip() if evidence_m else ""

        # Extract ## Root Cause Hypothesis
        root_m = re.search(r"##\s*Root Cause Hypothesis\s*\n(.+?)(?=\n##|\Z)", section, re.DOTALL)
        root_cause = root_m.group(1).strip() if root_m else ""

        # Extract ## Suggested Fix
        fix_m = re.search(r"##\s*Suggested Fix\s*\n(.+?)(?=\n##|\Z)", section, re.DOTALL)
        suggestion = fix_m.group(1).strip() if fix_m else ""

        # Infer rule_id from summary text — strip any newlines
        rule_id = _infer_rule_id(summary).strip().replace("\n", "")

        # Extract line number from evidence if possible
        line_m = re.search(r":(\d+)", evidence)
        line_start = int(line_m.group(1)) if line_m else None

        message = summary
        if root_cause:
            message += f"\n\nRoot cause: {root_cause}"

        finding: dict[str, Any] = {
            "path": file_path,
            "rule_id": rule_id,
            "severity": severity_map(raw_severity),
            "message": message,
            "suggestion": suggestion,
        }
        if line_start is not None:
            finding["line_start"] = line_start

        findings.append(finding)

    return findings


# ── API posting ─────────────────────────────────────────────────────────


def post_to_api(
    *,
    api_url: str,
    scan_source: str,
    scan_run_id: str,
    findings: list[dict[str, Any]],
) -> bool:
    """POST findings to filigree's scan API.

    Args:
        api_url: Base URL (e.g., "http://localhost:8377").
        scan_source: Scanner identifier (e.g., "codex", "claude").
        scan_run_id: Unique run identifier.
        findings: List of finding dicts.

    Returns:
        True on success, False on failure.
    """
    import urllib.error
    import urllib.request

    endpoint = f"{api_url}/api/v1/scan-results"
    payload = {
        "scan_source": scan_source,
        "scan_run_id": scan_run_id,
        "findings": findings,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        endpoint,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
            # Log any severity coercion warnings from the API [B2]
            for w in body.get("warnings", []):
                logger.warning("API warning: %s", w)
            return True
    except urllib.error.HTTPError as e:
        body_text = ""
        with contextlib.suppress(Exception):
            body_text = e.read().decode("utf-8", errors="replace")[:500]
        logger.warning(
            "API POST failed: HTTP %d for %s — %s (endpoint: %s)",
            e.code,
            scan_source,
            body_text,
            endpoint,
        )
        return False
    except (urllib.error.URLError, OSError) as e:
        logger.warning(
            "API unreachable for %s: %s (endpoint: %s)",
            scan_source,
            e,
            endpoint,
        )
        return False


# ── Token estimation ────────────────────────────────────────────────────


def estimate_tokens(files: list[Path], context_overhead: int = 2000) -> int:
    """Estimate total tokens for scanning a list of files.

    Uses chars/4 as a rough token estimate per file, plus context overhead per file.
    """
    total = 0
    for f in files:
        try:
            size = f.stat().st_size
            total += (size // 4) + context_overhead
        except OSError:
            total += context_overhead
    return total
