# MCP Scan Trigger Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `list_scanners` and `trigger_scan` MCP tools backed by a TOML scanner registry in `.filigree/scanners/`.

**Architecture:** Scanner definitions are TOML files in `.filigree/scanners/`. Two new MCP tools read the registry and spawn detached scanner subprocesses. The `trigger_scan` tool validates the file path via `_safe_path()`, registers the target file in `file_records`, and returns a `file_id` for the agent to poll later. No new DB tables, API endpoints, or in-memory state.

**Tech Stack:** Python stdlib (`tomllib`, `subprocess`, `pathlib`, `secrets`), existing MCP server (`mcp` package), existing `FiligreeDB.register_file()`.

**Trust Model:** Scanner TOML files in `.filigree/scanners/` are project-local configuration editable only by users with filesystem access. They are not writable via MCP tools. The `trigger_scan` tool spawns commands defined in these files — this is equivalent to running a script from the project directory.

**Limitations:** `trigger_scan` is fire-and-forget. Scanner results are delivered via POST to the dashboard API (`/api/v1/scan-results`). If the dashboard is not running when the scanner completes, results are silently lost. The agent should verify dashboard availability before triggering scans. Spawned scanner processes are fully detached (`start_new_session=True`) and will continue running even if the MCP server exits.

---

### Task 1: Scanner Registry Module

**Files:**
- Create: `src/filigree/scanners.py`
- Test: `tests/test_scanners.py`

**Step 1: Write the failing tests**

Create `tests/test_scanners.py`:

```python
"""Tests for the scanner TOML registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from filigree.scanners import ScannerConfig, list_scanners, load_scanner, validate_scanner_command


# ── list_scanners ────────────────────────────────────────────────────


class TestListScanners:
    def test_empty_dir(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        result = list_scanners(scanners_dir)
        assert result == []

    def test_missing_dir(self, tmp_path: Path) -> None:
        result = list_scanners(tmp_path / "no-such-dir")
        assert result == []

    def test_reads_toml_files(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        (scanners_dir / "claude.toml").write_text(
            '[scanner]\nname = "claude"\ndescription = "Bug hunt"\n'
            'command = "python scripts/claude_bug_hunt.py"\n'
            'args = ["--root", "{file}"]\nfile_types = ["py"]\n'
        )
        result = list_scanners(scanners_dir)
        assert len(result) == 1
        assert result[0].name == "claude"
        assert result[0].description == "Bug hunt"
        assert result[0].file_types == ["py"]

    def test_skips_non_toml(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        (scanners_dir / "readme.md").write_text("# Not a scanner\n")
        (scanners_dir / "claude.toml").write_text(
            '[scanner]\nname = "claude"\ndescription = "d"\n'
            'command = "python x.py"\nargs = []\nfile_types = []\n'
        )
        result = list_scanners(scanners_dir)
        assert len(result) == 1

    def test_skips_example_files(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        (scanners_dir / "claude.toml.example").write_text(
            '[scanner]\nname = "claude"\ndescription = "d"\n'
            'command = "python x.py"\nargs = []\nfile_types = []\n'
        )
        result = list_scanners(scanners_dir)
        assert result == []

    def test_skips_malformed_toml(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        (scanners_dir / "bad.toml").write_text("not valid toml [[")
        result = list_scanners(scanners_dir)
        assert result == []


# ── load_scanner ─────────────────────────────────────────────────────


class TestLoadScanner:
    def _write_scanner(self, scanners_dir: Path, name: str = "claude") -> None:
        scanners_dir.mkdir(exist_ok=True)
        (scanners_dir / f"{name}.toml").write_text(
            f'[scanner]\nname = "{name}"\ndescription = "desc"\n'
            f'command = "python scripts/{name}_bug_hunt.py"\n'
            f'args = ["--root", "{{file}}", "--api-url", "{{api_url}}"]\n'
            f'file_types = ["py"]\n'
        )

    def test_load_by_name(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        self._write_scanner(scanners_dir)
        cfg = load_scanner(scanners_dir, "claude")
        assert cfg is not None
        assert cfg.name == "claude"
        assert cfg.command == "python scripts/claude_bug_hunt.py"
        assert "{file}" in cfg.args

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        assert load_scanner(scanners_dir, "nonexistent") is None

    def test_load_rejects_path_traversal(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        assert load_scanner(scanners_dir, "../../../etc/passwd") is None
        assert load_scanner(scanners_dir, "foo/bar") is None
        assert load_scanner(scanners_dir, "..") is None

    def test_build_command(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        self._write_scanner(scanners_dir)
        cfg = load_scanner(scanners_dir, "claude")
        assert cfg is not None
        cmd = cfg.build_command(
            file_path="src/core.py",
            api_url="http://localhost:8377",
            project_root="/home/user/project",
        )
        assert cmd[0] == "python"
        assert "src/core.py" in cmd
        assert "http://localhost:8377" in cmd


# ── validate_scanner_command ─────────────────────────────────────────


class TestValidateScannerCommand:
    def test_python_available(self) -> None:
        # python should always be available in test env
        assert validate_scanner_command("python --version") is None

    def test_nonexistent_command(self) -> None:
        err = validate_scanner_command("nonexistent_cmd_xyz arg1")
        assert err is not None
        assert "not found" in err
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scanners.py -v --tb=short`
Expected: FAIL — `ModuleNotFoundError: No module named 'filigree.scanners'`

**Step 3: Write the implementation**

Create `src/filigree/scanners.py`:

```python
"""Scanner TOML registry for filigree.

Reads scanner definitions from .filigree/scanners/*.toml.
Each TOML file defines one scanner with a command template.

Template variables substituted at invocation:
    {file}         — target file path
    {api_url}      — dashboard URL (default http://localhost:8377)
    {project_root} — filigree project root directory
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


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
    ) -> list[str]:
        """Build the full command list with template variables substituted."""
        subs = {
            "{file}": file_path,
            "{api_url}": api_url,
            "{project_root}": project_root,
        }
        base = shlex.split(self.command)
        expanded_args = []
        for arg in self.args:
            for key, val in subs.items():
                arg = arg.replace(key, val)
            expanded_args.append(arg)
        return base + expanded_args

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
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to parse scanner TOML: %s", path)
        return None

    scanner = data.get("scanner")
    if not isinstance(scanner, dict) or "name" not in scanner or "command" not in scanner:
        logger.warning("Invalid scanner TOML (missing [scanner] name/command): %s", path)
        return None

    return ScannerConfig(
        name=scanner["name"],
        description=scanner.get("description", ""),
        command=scanner["command"],
        args=scanner.get("args", []),
        file_types=scanner.get("file_types", []),
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


_SAFE_NAME_RE = re.compile(r"^[\w-]+$")


def load_scanner(scanners_dir: Path, name: str) -> ScannerConfig | None:
    """Load a single scanner by name. Returns None if not found or name is invalid."""
    if not _SAFE_NAME_RE.match(name):
        return None  # Reject path traversal attempts
    toml_path = scanners_dir / f"{name}.toml"
    if not toml_path.is_file():
        return None
    return _parse_toml(toml_path)


def validate_scanner_command(command: str) -> str | None:
    """Check that the first token of a command is available on PATH.

    Returns None if valid, or an error message string if not found.
    """
    tokens = shlex.split(command)
    if not tokens:
        return "Empty command"
    binary = tokens[0]
    if shutil.which(binary) is None:
        return f"Command {binary!r} not found on PATH"
    return None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scanners.py -v --tb=short`
Expected: all 12 tests PASS

**Step 5: Lint and commit**

```bash
uv run ruff check src/filigree/scanners.py tests/test_scanners.py
uv run ruff format src/filigree/scanners.py tests/test_scanners.py
git add src/filigree/scanners.py tests/test_scanners.py
git commit -m "feat(scanners): add TOML scanner registry module"
```

---

### Task 2: MCP Tools — list_scanners and trigger_scan

**Files:**
- Modify: `src/filigree/mcp_server.py` (add 2 Tool entries to `list_tools()` at ~line 252, add 2 cases to `_dispatch()` before the `case _:` fallthrough at line 1592)
- Test: `tests/test_mcp.py` (add new test class)

Note: `_dispatch` starts at line 893 and spans to line 1593. The `case _:` fallthrough is at line 1592. Insert new cases before that line.

**Step 1: Write the failing tests**

Add to `tests/test_mcp.py`:

```python
class TestScannerTools:
    """Tests for list_scanners and trigger_scan MCP tools."""

    def _write_scanner_toml(self, mcp_db: FiligreeDB, name: str = "test-scanner") -> None:
        """Helper: write a scanner TOML into the test .filigree/scanners/ dir."""
        import filigree.mcp_server as mcp_mod

        scanners_dir = mcp_mod._filigree_dir / "scanners"
        scanners_dir.mkdir(exist_ok=True)
        (scanners_dir / f"{name}.toml").write_text(
            f'[scanner]\nname = "{name}"\ndescription = "Test scanner"\n'
            # Use 'echo' as the command — it exists on all systems, exits immediately
            f'command = "echo"\nargs = ["scan", "{{file}}"]\nfile_types = ["py"]\n'
        )

    async def test_list_scanners_empty(self, mcp_db: FiligreeDB) -> None:
        result = _parse(await call_tool("list_scanners", {}))
        assert result["scanners"] == []

    async def test_list_scanners_with_registry(self, mcp_db: FiligreeDB) -> None:
        self._write_scanner_toml(mcp_db)
        result = _parse(await call_tool("list_scanners", {}))
        assert len(result["scanners"]) == 1
        assert result["scanners"][0]["name"] == "test-scanner"

    async def test_trigger_scan_scanner_not_found(self, mcp_db: FiligreeDB) -> None:
        result = _parse(await call_tool("trigger_scan", {
            "scanner": "nonexistent",
            "file_path": "src/foo.py",
        }))
        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_trigger_scan_path_traversal_rejected(self, mcp_db: FiligreeDB) -> None:
        self._write_scanner_toml(mcp_db)
        result = _parse(await call_tool("trigger_scan", {
            "scanner": "test-scanner",
            "file_path": "../../etc/passwd",
        }))
        assert "error" in result
        assert result["code"] == "invalid_path"

    async def test_trigger_scan_scanner_name_traversal_rejected(self, mcp_db: FiligreeDB) -> None:
        result = _parse(await call_tool("trigger_scan", {
            "scanner": "../../../etc/crontab",
            "file_path": "src/foo.py",
        }))
        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_trigger_scan_file_not_found(self, mcp_db: FiligreeDB) -> None:
        self._write_scanner_toml(mcp_db)
        result = _parse(await call_tool("trigger_scan", {
            "scanner": "test-scanner",
            "file_path": "nonexistent/file.py",
        }))
        assert "error" in result
        assert "not found" in result["error"].lower() or "not exist" in result["error"].lower()

    async def test_trigger_scan_success(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        # Create a real file to scan
        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "test_target.py"
        target.write_text("x = 1\n")

        self._write_scanner_toml(mcp_db)
        result = _parse(await call_tool("trigger_scan", {
            "scanner": "test-scanner",
            "file_path": "test_target.py",
        }))
        assert "error" not in result
        assert result["scanner"] == "test-scanner"
        assert result["file_path"] == "test_target.py"
        assert "file_id" in result
        assert "scan_run_id" in result
        assert result["file_id"] != ""

        # Verify the file was registered in file_records
        f = mcp_db.get_file_by_path("test_target.py")
        assert f is not None

    async def test_trigger_scan_registers_file_idempotent(self, mcp_db: FiligreeDB) -> None:
        import filigree.mcp_server as mcp_mod

        project_root = mcp_mod._filigree_dir.parent
        target = project_root / "existing.py"
        target.write_text("y = 2\n")

        # Register the file first
        existing = mcp_db.register_file("existing.py", language="python")

        self._write_scanner_toml(mcp_db)
        result = _parse(await call_tool("trigger_scan", {
            "scanner": "test-scanner",
            "file_path": "existing.py",
        }))
        # Should reuse existing file_id
        assert result["file_id"] == existing.id
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp.py::TestScannerTools -v --tb=short`
Expected: FAIL — unknown tool "list_scanners"

**Step 3: Implement the MCP tools**

In `src/filigree/mcp_server.py`:

**3a. Add imports at top** (after existing imports, near line 41):

```python
import secrets
import subprocess

from filigree.scanners import list_scanners as _list_scanners, load_scanner, validate_scanner_command
```

Note: `subprocess` and `secrets` may already be imported — add only if not present. These are module-level imports (not deferred inside the case block) to follow the established pattern in mcp_server.py where only circular-import-prone modules are deferred.

**3b. Add Tool definitions** to the `list_tools()` return list (before the closing `]` at roughly the end of the tool list). Add after the last Tool entry (currently `claim_next`):

```python
        Tool(
            name="list_scanners",
            description="List registered scanners from .filigree/scanners/*.toml. Returns available scanner names, descriptions, and supported file types.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="trigger_scan",
            description="Trigger an async bug scan on a file. Registers the file, spawns a detached scanner process, and returns immediately. Check file findings later for results. Note: results are POSTed to the dashboard API — ensure the dashboard is running at the target api_url.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scanner": {"type": "string", "description": "Scanner name (from list_scanners)"},
                    "file_path": {"type": "string", "description": "File path to scan (relative to project root)"},
                    "api_url": {
                        "type": "string",
                        "default": "http://localhost:8377",
                        "description": "Dashboard URL where scanner POSTs results",
                    },
                },
                "required": ["scanner", "file_path"],
            },
        ),
```

**3c. Add dispatch cases** in `_dispatch()` before the `case _:` fallthrough (at ~line 1592):

```python
        case "list_scanners":
            scanners_dir = _filigree_dir / "scanners" if _filigree_dir else None
            if scanners_dir is None:
                return _text({"scanners": [], "hint": "Project directory not initialized"})
            scanners = _list_scanners(scanners_dir)
            result_data: dict[str, Any] = {"scanners": [s.to_dict() for s in scanners]}
            if not scanners:
                result_data["hint"] = "No scanners registered. Add TOML files to .filigree/scanners/"
            return _text(result_data)

        case "trigger_scan":
            from datetime import datetime, timezone

            if _filigree_dir is None:
                return _text({"error": "Project directory not initialized", "code": "not_initialized"})

            scanner_name = arguments["scanner"]
            file_path = arguments["file_path"]
            api_url = arguments.get("api_url", "http://localhost:8377")

            # Validate file path — prevent path traversal
            try:
                target = _safe_path(file_path)
            except ValueError as e:
                return _text({"error": str(e), "code": "invalid_path"})

            # Load scanner config (name is validated inside load_scanner)
            scanners_dir = _filigree_dir / "scanners"
            cfg = load_scanner(scanners_dir, scanner_name)
            if cfg is None:
                available = [s.name for s in _list_scanners(scanners_dir)]
                return _text({
                    "error": f"Scanner {scanner_name!r} not found in {scanners_dir}",
                    "code": "scanner_not_found",
                    "available_scanners": available,
                })

            # Validate file exists
            if not target.is_file():
                return _text({
                    "error": f"File not found: {file_path}",
                    "code": "file_not_found",
                })

            # Validate command is available
            cmd_err = validate_scanner_command(cfg.command)
            if cmd_err is not None:
                return _text({"error": cmd_err, "code": "command_not_found"})

            # Register file in file_records
            file_record = tracker.register_file(file_path)

            # Generate scan_run_id with random suffix to avoid collisions
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            scan_run_id = f"{scanner_name}-{ts}-{secrets.token_hex(3)}"

            # Build and spawn detached command
            project_root = _filigree_dir.parent
            cmd = cfg.build_command(
                file_path=file_path,
                api_url=api_url,
                project_root=str(project_root),
            )
            try:
                # Scanner TOML files are project-local config editable only by
                # users with filesystem access (not via MCP). S603 is acceptable.
                proc = subprocess.Popen(  # noqa: S603
                    cmd,
                    cwd=str(project_root),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as e:
                return _text({
                    "error": f"Failed to spawn scanner process: {e}",
                    "code": "spawn_failed",
                    "scanner": scanner_name,
                    "file_id": file_record.id,
                })

            return _text({
                "status": "triggered",
                "scanner": scanner_name,
                "file_path": file_path,
                "file_id": file_record.id,
                "scan_run_id": scan_run_id,
                "pid": proc.pid,
                "message": f"Scan triggered. Results will be POSTed to {api_url}. Check findings for file {file_path!r} (file_id={file_record.id!r}) later.",
            })
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp.py::TestScannerTools -v --tb=short`
Expected: all 8 tests PASS

**Step 5: Run full MCP test suite**

Run: `uv run pytest tests/test_mcp.py -v --tb=short`
Expected: all tests PASS (existing + new)

**Step 6: Lint and commit**

```bash
uv run ruff check src/filigree/mcp_server.py tests/test_mcp.py
uv run ruff format src/filigree/mcp_server.py tests/test_mcp.py
uv run mypy src/filigree/mcp_server.py src/filigree/scanners.py
git add src/filigree/mcp_server.py tests/test_mcp.py
git commit -m "feat(mcp): add list_scanners and trigger_scan tools"
```

---

### Task 3: CLI — create scanners dir in `filigree init`

**Files:**
- Modify: `src/filigree/cli.py:88-116` (the `init` command)

**Step 1: Add scanners dir creation**

In the `init()` function in `cli.py`, after `filigree_dir.mkdir()` (line 103) and before the config write, add:

```python
    (filigree_dir / "scanners").mkdir()
```

And in the already-exists branch (line 93-100), add:

```python
        (filigree_dir / "scanners").mkdir(exist_ok=True)
```

Also update the output to mention it:

After `click.echo(f"  Database: {filigree_dir / DB_FILENAME}")` add:
```python
    click.echo(f"  Scanners: {filigree_dir / 'scanners'}/ (add .toml files to register scanners)")
```

**Step 2: Run existing CLI tests**

Run: `uv run pytest tests/test_cli.py -v --tb=short -k init`
Expected: PASS (existing tests should still pass; the extra dir is harmless)

**Step 3: Commit**

```bash
uv run ruff check src/filigree/cli.py
git add src/filigree/cli.py
git commit -m "feat(cli): create scanners dir during filigree init"
```

---

### Task 4: Example Scanner TOML Files

**Files:**
- Create: `scripts/scanners/claude.toml.example`
- Create: `scripts/scanners/codex.toml.example`

**Step 1: Create the directory and example files**

```bash
mkdir -p scripts/scanners
```

`scripts/scanners/claude.toml.example`:
```toml
# Claude CLI bug scanner for filigree.
#
# To activate: copy this file to .filigree/scanners/claude.toml
#   cp scripts/scanners/claude.toml.example .filigree/scanners/claude.toml
#
# Requires: `claude` CLI on PATH
# See: scripts/claude_bug_hunt.py for the scanner implementation

[scanner]
name = "claude"
description = "Per-file bug hunt using Claude CLI"
command = "python scripts/claude_bug_hunt.py"
args = ["--root", "{file}", "--max-files", "1", "--api-url", "{api_url}"]
file_types = ["py"]
```

`scripts/scanners/codex.toml.example`:
```toml
# Codex bug scanner for filigree.
#
# To activate: copy this file to .filigree/scanners/codex.toml
#   cp scripts/scanners/codex.toml.example .filigree/scanners/codex.toml
#
# Requires: `codex` CLI on PATH
# See: scripts/codex_bug_hunt.py for the scanner implementation

[scanner]
name = "codex"
description = "Per-file bug hunt using Codex CLI"
command = "python scripts/codex_bug_hunt.py"
args = ["--root", "{file}", "--max-files", "1", "--api-url", "{api_url}"]
file_types = ["py"]
```

**Step 2: Commit**

```bash
git add scripts/scanners/
git commit -m "docs: add example scanner TOML configs"
```

---

### Task 5: Documentation Updates

**Files:**
- Modify: `docs/mcp.md`
- Modify: `CLAUDE.md`

**Step 1: Update docs/mcp.md**

Add to the Contents list (after `- [Data Management](#data-management)`):
```markdown
  - [Scanning](#scanning)
```

Add a new section at the end of the file:

```markdown

### Scanning

| Tool | Description |
|------|-------------|
| `list_scanners` | List registered scanners |
| `trigger_scan` | Trigger async file scan |

#### `list_scanners`

No parameters. Returns scanners registered in `.filigree/scanners/*.toml`.

Response: `{scanners: [{name, description, file_types}]}`

#### `trigger_scan`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `scanner` | string | yes | Scanner name (from list_scanners) |
| `file_path` | string | yes | File path to scan (relative to project root) |
| `api_url` | string | no | Dashboard URL (default http://localhost:8377) |

Response: `{status, scanner, file_path, file_id, scan_run_id, pid, message}`

**Workflow:**
1. `list_scanners` — discover available scanners
2. `trigger_scan` — fire-and-forget scan, get `file_id`
3. Check results later via `GET /api/files/{file_id}/findings`

**Important:** Results are POSTed to the dashboard API. Ensure the dashboard is running at the target `api_url` before triggering scans — if unreachable, results are silently lost.

**Scanner registration:** Add TOML files to `.filigree/scanners/`. See `scripts/scanners/*.toml.example` for templates.
```

Update the tool count in the opening paragraph from "43 tools" to "45 tools".

**Step 2: Update CLAUDE.md**

Add a new section after "File Records & Scan Findings (API)" and before "### Workflow":

```markdown

### Scanner Integration (MCP)

Register scanners in `.filigree/scanners/*.toml` (see `scripts/scanners/*.toml.example`).

MCP workflow:
- `list_scanners` — discover registered scanners
- `trigger_scan scanner=<name> file_path=<path>` — trigger async scan, returns `file_id`
- Check `GET /api/files/{file_id}/findings` for results
```

**Step 3: Commit**

```bash
git add docs/mcp.md CLAUDE.md
git commit -m "docs: add scanner MCP tools to mcp.md and CLAUDE.md"
```

---

### Task 6: Full CI Verification

**Step 1: Run the full CI pipeline**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short -q
```

Expected: all checks pass, all tests pass.

**Step 2: Final commit if any formatting fixes needed**

If ruff format made changes:
```bash
git add -u
git commit -m "style: format"
```
