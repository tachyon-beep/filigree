# Server Install + Ethereal Mode — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the brittle hybrid registration system with two clean installation modes: ethereal (default, session-scoped) and server (opt-in, persistent daemon).

**Architecture:** Mode is stored in `.filigree/config.json` as `"mode": "ethereal"|"server"`. Ethereal mode spawns a per-project dashboard on a deterministic port via SessionStart hook. Server mode runs a persistent daemon with streamable HTTP MCP. Both modes eliminate the shared `~/.filigree/registry.json`.

**Tech Stack:** Python 3.11+, Click (CLI), FastAPI/uvicorn (dashboard), MCP SDK (streamable HTTP), TOML (server config), fcntl (locking).

**Design doc:** `docs/plans/2026-02-22-server-ethereal-modes-design.md`

**Filigree issues:**
- Epic: `filigree-a7f852`
- Ethereal mode: `filigree-19acff`
- Server mode: `filigree-876888`
- Remove hybrid: `filigree-4b4a68`

---

## Phase 1: Mode Configuration Foundation

### Task 1: Add `get_mode()` helper to core.py

**Files:**
- Modify: `src/filigree/core.py:71-83` (near `read_config`/`write_config`)
- Test: `tests/test_core.py`

**Step 1: Write the failing test**

```python
# tests/test_core.py — append to existing file

class TestGetMode:
    def test_default_mode_is_ethereal(self, tmp_path: Path) -> None:
        """Projects without a mode field default to ethereal."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == "ethereal"

    def test_explicit_ethereal(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "ethereal"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == "ethereal"

    def test_explicit_server(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "server"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        assert get_mode(filigree_dir) == "server"

    def test_missing_config_defaults_to_ethereal(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        assert get_mode(filigree_dir) == "ethereal"
```

Add import: `from filigree.core import get_mode` at top of test file.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_core.py::TestGetMode -v`
Expected: FAIL — `ImportError: cannot import name 'get_mode'`

**Step 3: Write minimal implementation**

In `src/filigree/core.py`, after `write_config()` (~line 84):

```python
VALID_MODES = ("ethereal", "server")


def get_mode(filigree_dir: Path) -> str:
    """Return the installation mode for a project. Defaults to 'ethereal'."""
    config = read_config(filigree_dir)
    mode = config.get("mode", "ethereal")
    if mode not in VALID_MODES:
        logger.warning("Unknown mode '%s' in config, falling back to 'ethereal'", mode)
        return "ethereal"
    return mode
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_core.py::TestGetMode -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/core.py tests/test_core.py
git commit -m "feat(core): add get_mode() helper for installation mode"
```

---

### Task 2: Add `--mode` flag to `filigree init`

**Files:**
- Modify: `src/filigree/cli.py:86-119` (init command)
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

```python
# tests/test_cli.py — add to existing CLI tests

class TestInitMode:
    def test_init_default_mode_is_ethereal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "ethereal"

    def test_init_with_server_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--mode", "server"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "server"

    def test_init_with_explicit_ethereal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--mode", "ethereal"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "ethereal"

    def test_init_invalid_mode_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--mode", "bogus"])
        assert result.exit_code != 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::TestInitMode -v`
Expected: FAIL — `config["mode"]` KeyError (mode not written yet)

**Step 3: Write minimal implementation**

Modify `cli.py` `init` command:

```python
@cli.command()
@click.option("--prefix", default=None, help="ID prefix for issues (default: directory name)")
@click.option(
    "--mode",
    type=click.Choice(["ethereal", "server"], case_sensitive=False),
    default="ethereal",
    help="Installation mode (default: ethereal)",
)
def init(prefix: str | None, mode: str) -> None:
    """Initialize .filigree/ in the current directory."""
    cwd = Path.cwd()
    filigree_dir = cwd / FILIGREE_DIR_NAME

    if filigree_dir.exists():
        click.echo(f"{FILIGREE_DIR_NAME}/ already exists in {cwd}")
        config = read_config(filigree_dir)
        db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=config.get("prefix", "filigree"))
        db.initialize()
        db.close()
        (filigree_dir / "scanners").mkdir(exist_ok=True)
        return

    prefix = prefix or cwd.name
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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py::TestInitMode -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/cli.py tests/test_cli.py
git commit -m "feat(cli): add --mode flag to filigree init"
```

---

### Task 3: Add `--mode` flag to `filigree install`

**Files:**
- Modify: `src/filigree/cli.py:775-850` (install command)
- Modify: `src/filigree/core.py` (write mode to config)
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

```python
class TestInstallMode:
    def test_install_writes_mode_to_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """install --mode=server persists the mode to config.json."""
        monkeypatch.chdir(tmp_path)
        # Set up a minimal project
        runner.invoke(cli, ["init"])
        result = runner.invoke(cli, ["install", "--mode", "server"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "server"

    def test_install_preserves_existing_mode_when_no_flag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """install without --mode keeps the existing mode."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--mode", "server"])
        result = runner.invoke(cli, ["install"])
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".filigree" / "config.json").read_text())
        assert config["mode"] == "server"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::TestInstallMode -v`
Expected: FAIL — no `--mode` option on install

**Step 3: Write minimal implementation**

Add `--mode` option to the install command. When provided, update `config.json`. When omitted, preserve whatever is already there.

```python
@cli.command()
@click.option("--claude-code", is_flag=True, help="Install MCP for Claude Code only")
@click.option("--codex", is_flag=True, help="Install MCP for Codex only")
@click.option("--claude-md", is_flag=True, help="Inject instructions into CLAUDE.md only")
@click.option("--agents-md", is_flag=True, help="Inject instructions into AGENTS.md only")
@click.option("--gitignore", is_flag=True, help="Add .filigree/ to .gitignore only")
@click.option("--hooks", "hooks_only", is_flag=True, help="Install Claude Code hooks only")
@click.option("--skills", "skills_only", is_flag=True, help="Install Claude Code skills only")
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
    mode: str | None,
) -> None:
    # ... existing discovery ...

    # Update mode in config if explicitly provided
    if mode is not None:
        config = read_config(filigree_dir)
        config["mode"] = mode
        write_config(filigree_dir, config)

    # ... rest of existing install logic ...
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py::TestInstallMode -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/cli.py tests/test_cli.py
git commit -m "feat(cli): add --mode flag to filigree install"
```

---

## Phase 2: Ethereal Mode

### Task 4: Port selection utility

**Files:**
- Create: `src/filigree/ephemeral.py`
- Test: `tests/test_ephemeral.py`

**Step 1: Write the failing test**

```python
# tests/test_ephemeral.py

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

from filigree.ephemeral import compute_port, find_available_port


class TestComputePort:
    def test_deterministic_for_same_path(self) -> None:
        """Same path always produces same port."""
        p = Path("/home/john/myproject/.filigree")
        assert compute_port(p) == compute_port(p)

    def test_in_valid_range(self) -> None:
        """Port is between 8400 and 9399."""
        p = Path("/home/john/myproject/.filigree")
        port = compute_port(p)
        assert 8400 <= port <= 9399

    def test_different_paths_likely_different_ports(self) -> None:
        """Different paths are unlikely to produce the same port."""
        ports = {compute_port(Path(f"/project-{i}/.filigree")) for i in range(20)}
        # With 1000-slot range and 20 samples, collisions are possible but
        # getting fewer than 15 unique ports would be suspicious
        assert len(ports) >= 15


class TestFindAvailablePort:
    def test_returns_deterministic_port_when_free(self) -> None:
        """When the deterministic port is free, use it."""
        p = Path("/home/john/myproject/.filigree")
        expected = compute_port(p)
        port = find_available_port(p)
        assert port == expected

    def test_skips_occupied_port(self) -> None:
        """When deterministic port is occupied, tries next ones."""
        p = Path("/home/john/myproject/.filigree")
        base = compute_port(p)
        # Occupy the base port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", base))
        sock.listen(1)
        try:
            port = find_available_port(p)
            assert port != base
            assert port > base  # should try sequential ports next
        finally:
            sock.close()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ephemeral.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'filigree.ephemeral'`

**Step 3: Write minimal implementation**

```python
# src/filigree/ephemeral.py
"""Ephemeral (session-scoped) dashboard lifecycle.

Handles deterministic port selection, PID tracking, and stale process cleanup
for the ethereal installation mode.
"""

from __future__ import annotations

import hashlib
import logging
import socket
from pathlib import Path

logger = logging.getLogger(__name__)

PORT_BASE = 8400
PORT_RANGE = 1000
PORT_RETRIES = 5


def compute_port(filigree_dir: Path) -> int:
    """Deterministic port from project path: 8400 + hash(path) % 1000."""
    h = hashlib.sha256(str(filigree_dir.resolve()).encode()).hexdigest()
    return PORT_BASE + (int(h, 16) % PORT_RANGE)


def _is_port_free(port: int) -> bool:
    """Check whether a port is available for binding."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def find_available_port(filigree_dir: Path) -> int:
    """Find an available port, starting with the deterministic one.

    Tries the deterministic port first, then up to PORT_RETRIES sequential
    ports, then falls back to OS-assigned (port 0).
    """
    base = compute_port(filigree_dir)
    for offset in range(PORT_RETRIES):
        candidate = base + offset
        if candidate >= 65536:
            break
        if _is_port_free(candidate):
            return candidate

    # Fallback: OS-assigned
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ephemeral.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/ephemeral.py tests/test_ephemeral.py
git commit -m "feat(ephemeral): add deterministic port selection utility"
```

---

### Task 5: PID lifecycle management

**Files:**
- Modify: `src/filigree/ephemeral.py`
- Test: `tests/test_ephemeral.py`

**Step 1: Write the failing test**

```python
# tests/test_ephemeral.py — append

import os
import signal

from filigree.ephemeral import (
    read_pid,
    write_pid,
    is_process_alive,
    cleanup_stale_pid,
    read_port_file,
    write_port_file,
)


class TestPidLifecycle:
    def test_write_and_read_pid(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid(pid_file, 12345)
        assert read_pid(pid_file) == 12345

    def test_read_missing_pid_returns_none(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        assert read_pid(pid_file) is None

    def test_read_corrupt_pid_returns_none(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        pid_file.write_text("not-a-number")
        assert read_pid(pid_file) is None

    def test_is_process_alive_for_self(self) -> None:
        assert is_process_alive(os.getpid()) is True

    def test_is_process_alive_for_dead(self) -> None:
        # PID 99999999 is extremely unlikely to be alive
        assert is_process_alive(99999999) is False

    def test_cleanup_stale_pid_removes_dead(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid(pid_file, 99999999)
        cleanup_stale_pid(pid_file)
        assert not pid_file.exists()

    def test_cleanup_stale_pid_keeps_alive(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "ephemeral.pid"
        write_pid(pid_file, os.getpid())
        cleanup_stale_pid(pid_file)
        assert pid_file.exists()


class TestPortFile:
    def test_write_and_read_port(self, tmp_path: Path) -> None:
        port_file = tmp_path / "ephemeral.port"
        write_port_file(port_file, 9173)
        assert read_port_file(port_file) == 9173

    def test_read_missing_port_returns_none(self, tmp_path: Path) -> None:
        port_file = tmp_path / "ephemeral.port"
        assert read_port_file(port_file) is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ephemeral.py::TestPidLifecycle tests/test_ephemeral.py::TestPortFile -v`
Expected: FAIL — `ImportError`

**Step 3: Write minimal implementation**

Append to `src/filigree/ephemeral.py`:

```python
def write_pid(pid_file: Path, pid: int) -> None:
    """Write a PID to file."""
    pid_file.write_text(str(pid))


def read_pid(pid_file: Path) -> int | None:
    """Read a PID from file. Returns None if missing or corrupt."""
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def is_process_alive(pid: int) -> bool:
    """Check if a process is running (via kill signal 0)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def cleanup_stale_pid(pid_file: Path) -> bool:
    """Remove PID file if the process is dead. Returns True if cleaned."""
    pid = read_pid(pid_file)
    if pid is None:
        return False
    if not is_process_alive(pid):
        pid_file.unlink(missing_ok=True)
        logger.info("Cleaned stale PID file %s (pid %d)", pid_file, pid)
        return True
    return False


def write_port_file(port_file: Path, port: int) -> None:
    """Write the active dashboard port to file."""
    port_file.write_text(str(port))


def read_port_file(port_file: Path) -> int | None:
    """Read the dashboard port from file. Returns None if missing/corrupt."""
    if not port_file.exists():
        return None
    try:
        return int(port_file.read_text().strip())
    except (ValueError, OSError):
        return None
```

Add `import os` to the imports at top.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ephemeral.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/ephemeral.py tests/test_ephemeral.py
git commit -m "feat(ephemeral): add PID lifecycle and port file management"
```

---

### Task 6: Rewrite `ensure_dashboard_running` for ethereal mode

**Files:**
- Modify: `src/filigree/hooks.py:197-287` (replace `_try_register_with_server` and `ensure_dashboard_running`)
- Modify: `tests/test_hooks.py`

**Step 1: Write the failing test**

```python
# tests/test_hooks.py — add/modify existing tests

class TestEnsureDashboardEthereal:
    def test_starts_dashboard_on_deterministic_port(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In ethereal mode, dashboard starts on project-specific port."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "ethereal"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        db = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
        db.initialize()
        db.close()

        monkeypatch.chdir(tmp_path)

        spawned_cmds: list[list[str]] = []
        def mock_popen(cmd, **kwargs):
            spawned_cmds.append(cmd)
            mock = MagicMock()
            mock.pid = 12345
            mock.poll.return_value = None
            return mock

        monkeypatch.setattr("filigree.hooks.subprocess.Popen", mock_popen)
        # Make sure port appears free
        monkeypatch.setattr("filigree.hooks._is_port_listening", lambda *a: False)

        result = ensure_dashboard_running()
        assert "http://localhost:" in result
        assert "12345" in result
        # Should have written PID and port files in .filigree/
        assert (filigree_dir / "ephemeral.pid").exists()
        assert (filigree_dir / "ephemeral.port").exists()

    def test_reuses_running_dashboard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If PID is alive and port is listening, reuse it."""
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "ethereal"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        db = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
        db.initialize()
        db.close()

        # Fake existing ephemeral state
        (filigree_dir / "ephemeral.pid").write_text(str(os.getpid()))
        (filigree_dir / "ephemeral.port").write_text("9173")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("filigree.hooks._is_port_listening", lambda *a: True)

        result = ensure_dashboard_running()
        assert "running on http://localhost:9173" in result.lower() or "9173" in result
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hooks.py::TestEnsureDashboardEthereal -v`
Expected: FAIL — existing ensure_dashboard_running doesn't write ephemeral files

**Step 3: Write implementation**

Rewrite `ensure_dashboard_running` in `hooks.py` to branch on mode:

```python
def ensure_dashboard_running(port: int = 8377) -> str:
    """Ensure the filigree dashboard is running.

    In ethereal mode (default): spawns a single-project dashboard on a
    deterministic port, with PID/port files in .filigree/.
    In server mode: just verifies the daemon is reachable.
    """
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        import filigree.dashboard  # noqa: F401
    except ImportError:
        return 'Dashboard requires extra dependencies. Install with: pip install "filigree[dashboard]"'

    try:
        filigree_dir = find_filigree_root()
    except FileNotFoundError:
        return ""

    mode = get_mode(filigree_dir)

    if mode == "server":
        return _ensure_dashboard_server_mode(filigree_dir, port)
    return _ensure_dashboard_ethereal_mode(filigree_dir)


def _ensure_dashboard_ethereal_mode(filigree_dir: Path) -> str:
    """Ethereal mode: session-scoped dashboard on a deterministic port."""
    from filigree.ephemeral import (
        cleanup_stale_pid,
        find_available_port,
        is_process_alive,
        read_pid,
        read_port_file,
        write_pid,
        write_port_file,
    )

    pid_file = filigree_dir / "ephemeral.pid"
    port_file = filigree_dir / "ephemeral.port"
    lock_file = filigree_dir / "ephemeral.lock"

    # Check if already running from a previous session
    existing_pid = read_pid(pid_file)
    existing_port = read_port_file(port_file)
    if existing_pid and existing_port and is_process_alive(existing_pid):
        if _is_port_listening(existing_port):
            return f"Filigree dashboard running on http://localhost:{existing_port}"

    # Clean up stale state
    cleanup_stale_pid(pid_file)

    # Atomic start with lock
    lock_fd = None
    try:
        lock_fd = open(lock_file, "w")  # noqa: SIM115
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "Filigree dashboard: another session is starting it, skipping"

        # Re-check after acquiring lock (another session may have started it)
        existing_pid = read_pid(pid_file)
        existing_port = read_port_file(port_file)
        if existing_pid and existing_port and is_process_alive(existing_pid):
            if _is_port_listening(existing_port):
                return f"Filigree dashboard running on http://localhost:{existing_port}"

        port = find_available_port(filigree_dir)
        filigree_cmd = _find_filigree_command()

        log_file = filigree_dir / "ephemeral.log"
        with open(log_file, "w") as log_fd:
            proc = subprocess.Popen(
                [*filigree_cmd, "dashboard", "--no-browser", "--port", str(port)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_fd,
                start_new_session=True,
            )

        time.sleep(0.5)
        exit_code = proc.poll()
        if exit_code is not None:
            stderr_output = log_file.read_text().strip()
            detail = f": {stderr_output}" if stderr_output else ""
            return f"Dashboard process exited immediately (pid {proc.pid}, code {exit_code}){detail}"

        write_pid(pid_file, proc.pid)
        write_port_file(port_file, port)
        return f"Started Filigree dashboard on http://localhost:{port}"
    finally:
        if lock_fd is not None:
            lock_fd.close()


def _ensure_dashboard_server_mode(filigree_dir: Path, port: int) -> str:
    """Server mode: just verify the daemon is reachable."""
    if _is_port_listening(port):
        return f"Filigree server running on http://localhost:{port}"
    return f"Filigree server not running on port {port}. Start it with: filigree server start"
```

Add import at top of hooks.py: `from filigree.core import get_mode`

Remove `_try_register_with_server()` function entirely.

Remove the registry import and `Registry().register()` call from the old `ensure_dashboard_running`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: PASS (both new and existing tests)

**Step 5: Commit**

```bash
git add src/filigree/hooks.py tests/test_hooks.py
git commit -m "feat(hooks): rewrite ensure_dashboard for ethereal mode"
```

---

### Task 7: Simplify dashboard for single-project mode

**Files:**
- Modify: `src/filigree/dashboard.py` (remove multi-project scaffolding, add single-project `main()`)
- Modify: `tests/test_dashboard.py`

**Step 1: Write the failing test**

```python
# tests/test_dashboard.py — replace the client fixture and add test

@pytest.fixture
async def client(dashboard_db: FiligreeDB, tmp_path: Path) -> AsyncClient:
    """Create a test client backed by a single-project DB (ethereal mode)."""
    import filigree.dashboard as dash_module

    dash_module._db = dashboard_db
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._db = None


class TestEtherealDashboard:
    @pytest.mark.anyio
    async def test_no_register_endpoint(self, client: AsyncClient) -> None:
        """Ethereal mode should not have /api/register."""
        resp = await client.post("/api/register", json={"path": "/foo"})
        assert resp.status_code == 404 or resp.status_code == 405

    @pytest.mark.anyio
    async def test_no_projects_endpoint(self, client: AsyncClient) -> None:
        """Ethereal mode should not have /api/projects."""
        resp = await client.get("/api/projects")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_issues_at_root_api(self, client: AsyncClient) -> None:
        """Issues served at /api/issues (no project key prefix)."""
        resp = await client.get("/api/issues")
        assert resp.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dashboard.py::TestEtherealDashboard -v`
Expected: FAIL — `/api/register` still exists, `_db` attribute doesn't exist

**Step 3: Write implementation**

Modify `dashboard.py`:
- Replace `_project_manager` / `_default_project_key` with a simple `_db: FiligreeDB | None = None`
- Remove the `from filigree.registry import ProjectManager, Registry` import
- Remove `_get_project_db()` multi-project function; replace with simple `_get_db()` that returns `_db`
- Mount router only at `/api/` (remove `/api/p/{project_key}` mount)
- Remove `/api/register`, `/api/projects`, `/api/reload` endpoints
- Update `main()` to open a single DB connection directly:

```python
def main(port: int = DEFAULT_PORT, *, no_browser: bool = False) -> None:
    """Start the dashboard server."""
    import threading
    import uvicorn

    global _db

    filigree_dir = find_filigree_root()
    config = read_config(filigree_dir)
    _db = FiligreeDB(
        filigree_dir / DB_FILENAME,
        prefix=config.get("prefix", "filigree"),
        check_same_thread=False,
    )
    _db.initialize()

    app = create_app()

    if not no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    print(f"Filigree Dashboard: http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
```

Note: The multi-project endpoints will be re-added differently in Phase 3 (server mode) with a separate code path. For now the dashboard is single-project only.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/dashboard.py tests/test_dashboard.py
git commit -m "refactor(dashboard): simplify to single-project mode for ethereal"
```

---

### Task 8: Update `session-context` hook to include dashboard URL

**Files:**
- Modify: `src/filigree/hooks.py:42-94` (`_build_context`) and `generate_session_context`
- Test: `tests/test_hooks.py`

**Step 1: Write the failing test**

```python
class TestSessionContextDashboardUrl:
    def test_includes_dashboard_url_when_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "version": 1, "mode": "ethereal"}
        (filigree_dir / "config.json").write_text(json.dumps(config))
        (filigree_dir / "ephemeral.port").write_text("9173")
        (filigree_dir / "ephemeral.pid").write_text(str(os.getpid()))

        db = FiligreeDB(filigree_dir / "filigree.db", prefix="test")
        db.initialize()

        monkeypatch.setattr("filigree.hooks._is_port_listening", lambda *a: True)
        context = _build_context(db, filigree_dir)
        db.close()

        assert "http://localhost:9173" in context

    def test_no_url_when_no_port_file(self, db: FiligreeDB, tmp_path: Path) -> None:
        filigree_dir = tmp_path / ".filigree"
        filigree_dir.mkdir()
        context = _build_context(db, filigree_dir)
        assert "localhost" not in context
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hooks.py::TestSessionContextDashboardUrl -v`
Expected: FAIL — `_build_context` doesn't accept `filigree_dir` parameter

**Step 3: Write implementation**

Update `_build_context` to accept an optional `filigree_dir` parameter and append the dashboard URL if the ephemeral port file exists and the port is listening:

```python
def _build_context(db: FiligreeDB, filigree_dir: Path | None = None) -> str:
    """Assemble the project snapshot string from a live DB handle."""
    lines: list[str] = []
    lines.append("=== Filigree Project Snapshot ===")
    lines.append("")

    # Dashboard URL (if running)
    if filigree_dir is not None:
        from filigree.ephemeral import read_port_file, read_pid, is_process_alive
        port_file = filigree_dir / "ephemeral.port"
        pid_file = filigree_dir / "ephemeral.pid"
        port = read_port_file(port_file)
        pid = read_pid(pid_file)
        if port and pid and is_process_alive(pid) and _is_port_listening(port):
            lines.append(f"DASHBOARD: http://localhost:{port}")
            lines.append("")

    # ... rest of existing _build_context unchanged ...
```

Update `generate_session_context` to pass `filigree_dir` to `_build_context`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/hooks.py tests/test_hooks.py
git commit -m "feat(hooks): include dashboard URL in session context"
```

---

## Phase 3: Server Mode

### Task 9: Server config module (`server.py`)

**Files:**
- Create: `src/filigree/server.py`
- Test: `tests/test_server.py`

**Step 1: Write the failing test**

```python
# tests/test_server.py

from __future__ import annotations

import tomllib
from pathlib import Path

from filigree.server import (
    ServerConfig,
    read_server_config,
    write_server_config,
    register_project,
    unregister_project,
)


class TestServerConfig:
    def test_default_config(self) -> None:
        config = ServerConfig()
        assert config.port == 8377
        assert config.projects == {}

    def test_write_and_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.toml")

        config = ServerConfig(port=9000)
        write_server_config(config)
        loaded = read_server_config()
        assert loaded.port == 9000

    def test_read_missing_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.toml")
        config = read_server_config()
        assert config.port == 8377


class TestProjectRegistration:
    def test_register_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.toml")

        filigree_dir = tmp_path / "myproject" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text('{"prefix": "myproject"}')

        register_project(filigree_dir)
        config = read_server_config()
        assert str(filigree_dir) in config.projects

    def test_unregister_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.toml")

        filigree_dir = tmp_path / "myproject" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text('{"prefix": "myproject"}')

        register_project(filigree_dir)
        unregister_project(filigree_dir)
        config = read_server_config()
        assert str(filigree_dir) not in config.projects
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/filigree/server.py
"""Server mode configuration and daemon management.

Handles the persistent multi-project daemon for server installation mode.
Config lives at ~/.config/filigree/server.toml.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filigree.core import read_config

logger = logging.getLogger(__name__)

SERVER_CONFIG_DIR = Path.home() / ".config" / "filigree"
SERVER_CONFIG_FILE = SERVER_CONFIG_DIR / "server.toml"
SERVER_PID_FILE = SERVER_CONFIG_DIR / "server.pid"

DEFAULT_PORT = 8377


@dataclass
class ServerConfig:
    port: int = DEFAULT_PORT
    projects: dict[str, dict[str, str]] = field(default_factory=dict)


def read_server_config() -> ServerConfig:
    """Read server.toml. Returns defaults if missing."""
    if not SERVER_CONFIG_FILE.exists():
        return ServerConfig()
    try:
        data = tomllib.loads(SERVER_CONFIG_FILE.read_text())
        return ServerConfig(
            port=data.get("port", DEFAULT_PORT),
            projects=data.get("projects", {}),
        )
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning("Corrupt server config %s: %s", SERVER_CONFIG_FILE, exc)
        return ServerConfig()


def write_server_config(config: ServerConfig) -> None:
    """Write server.toml."""
    SERVER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"port = {config.port}", "", "[projects]"]
    for path, info in sorted(config.projects.items()):
        prefix = info.get("prefix", "unknown")
        # TOML requires quoting keys with special chars
        lines.append(f'"{path}" = {{ prefix = "{prefix}" }}')
    SERVER_CONFIG_FILE.write_text("\n".join(lines) + "\n")


def register_project(filigree_dir: Path) -> None:
    """Register a project in server.toml."""
    filigree_dir = filigree_dir.resolve()
    config = read_server_config()
    project_config = read_config(filigree_dir)
    config.projects[str(filigree_dir)] = {
        "prefix": project_config.get("prefix", "filigree"),
    }
    write_server_config(config)


def unregister_project(filigree_dir: Path) -> None:
    """Remove a project from server.toml."""
    filigree_dir = filigree_dir.resolve()
    config = read_server_config()
    config.projects.pop(str(filigree_dir), None)
    write_server_config(config)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/server.py tests/test_server.py
git commit -m "feat(server): add server config module with project registration"
```

---

### Task 10: Daemon lifecycle (`filigree server start/stop/status`)

**Files:**
- Modify: `src/filigree/server.py` (add daemon functions)
- Modify: `src/filigree/cli.py` (add `server` command group)
- Test: `tests/test_server.py`

**Step 1: Write the failing test**

```python
class TestDaemonLifecycle:
    def test_start_writes_pid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.toml")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        spawned: list = []
        def mock_popen(cmd, **kwargs):
            mock = MagicMock()
            mock.pid = 54321
            mock.poll.return_value = None
            spawned.append(cmd)
            return mock

        monkeypatch.setattr("filigree.server.subprocess.Popen", mock_popen)
        from filigree.server import start_daemon
        result = start_daemon()
        assert result.success
        assert (config_dir / "server.pid").read_text().strip() == "54321"

    def test_stop_kills_process(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text("54321")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        killed: list[int] = []
        monkeypatch.setattr("os.kill", lambda pid, sig: killed.append(pid))

        from filigree.server import stop_daemon
        result = stop_daemon()
        assert result.success
        assert 54321 in killed

    def test_status_reports_not_running(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        from filigree.server import daemon_status
        status = daemon_status()
        assert not status.running
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py::TestDaemonLifecycle -v`
Expected: FAIL — functions don't exist

**Step 3: Write implementation**

Add to `src/filigree/server.py`:

```python
import os
import signal
import subprocess
import time
from dataclasses import dataclass


@dataclass
class DaemonResult:
    success: bool
    message: str


@dataclass
class DaemonStatus:
    running: bool
    pid: int | None = None
    port: int | None = None
    project_count: int = 0


def start_daemon(port: int | None = None) -> DaemonResult:
    """Start the filigree server daemon."""
    from filigree.install import _find_filigree_command
    from filigree.ephemeral import read_pid, is_process_alive

    # Check if already running
    existing_pid = read_pid(SERVER_PID_FILE)
    if existing_pid and is_process_alive(existing_pid):
        return DaemonResult(False, f"Daemon already running (pid {existing_pid})")

    config = read_server_config()
    daemon_port = port or config.port

    SERVER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    filigree_cmd = _find_filigree_command()
    log_file = SERVER_CONFIG_DIR / "server.log"

    with open(log_file, "w") as log_fd:
        proc = subprocess.Popen(
            [*filigree_cmd, "dashboard", "--no-browser", "--port", str(daemon_port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            start_new_session=True,
        )

    time.sleep(0.5)
    exit_code = proc.poll()
    if exit_code is not None:
        stderr = log_file.read_text().strip()
        return DaemonResult(False, f"Daemon exited immediately (code {exit_code}): {stderr}")

    SERVER_PID_FILE.write_text(str(proc.pid))
    return DaemonResult(True, f"Started filigree daemon (pid {proc.pid}) on port {daemon_port}")


def stop_daemon() -> DaemonResult:
    """Stop the filigree server daemon."""
    from filigree.ephemeral import read_pid, is_process_alive

    pid = read_pid(SERVER_PID_FILE)
    if pid is None:
        return DaemonResult(False, "No PID file found — daemon may not be running")

    if not is_process_alive(pid):
        SERVER_PID_FILE.unlink(missing_ok=True)
        return DaemonResult(True, f"Daemon (pid {pid}) was not running; cleaned up PID file")

    os.kill(pid, signal.SIGTERM)
    SERVER_PID_FILE.unlink(missing_ok=True)
    return DaemonResult(True, f"Stopped filigree daemon (pid {pid})")


def daemon_status() -> DaemonStatus:
    """Check daemon status."""
    from filigree.ephemeral import read_pid, is_process_alive

    pid = read_pid(SERVER_PID_FILE)
    if pid is None or not is_process_alive(pid):
        return DaemonStatus(running=False)

    config = read_server_config()
    return DaemonStatus(
        running=True,
        pid=pid,
        port=config.port,
        project_count=len(config.projects),
    )
```

Add CLI commands in `cli.py`:

```python
@cli.group()
def server() -> None:
    """Manage the filigree server daemon."""

@server.command("start")
@click.option("--port", default=None, type=int, help="Override port")
def server_start(port: int | None) -> None:
    """Start the filigree daemon."""
    from filigree.server import start_daemon
    result = start_daemon(port=port)
    click.echo(result.message)
    if not result.success:
        sys.exit(1)

@server.command("stop")
def server_stop() -> None:
    """Stop the filigree daemon."""
    from filigree.server import stop_daemon
    result = stop_daemon()
    click.echo(result.message)
    if not result.success:
        sys.exit(1)

@server.command("status")
def server_status_cmd() -> None:
    """Show daemon status."""
    from filigree.server import daemon_status
    status = daemon_status()
    if status.running:
        click.echo(f"Filigree daemon running (pid {status.pid}) on port {status.port}")
        click.echo(f"  Projects: {status.project_count}")
    else:
        click.echo("Filigree daemon is not running")

@server.command("register")
@click.argument("path", default=".", type=click.Path(exists=True))
def server_register(path: str) -> None:
    """Register a project with the server."""
    from filigree.server import register_project
    project_path = Path(path).resolve()
    filigree_dir = project_path / ".filigree" if project_path.name != ".filigree" else project_path
    if not filigree_dir.is_dir():
        click.echo(f"No .filigree/ found at {project_path}", err=True)
        sys.exit(1)
    register_project(filigree_dir)
    click.echo(f"Registered {filigree_dir}")

@server.command("unregister")
@click.argument("path", default=".", type=click.Path())
def server_unregister(path: str) -> None:
    """Unregister a project from the server."""
    from filigree.server import unregister_project
    project_path = Path(path).resolve()
    filigree_dir = project_path / ".filigree" if project_path.name != ".filigree" else project_path
    unregister_project(filigree_dir)
    click.echo(f"Unregistered {filigree_dir}")
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/server.py src/filigree/cli.py tests/test_server.py
git commit -m "feat(server): add daemon lifecycle and CLI commands"
```

---

### Task 11: Server mode MCP config generation

**Files:**
- Modify: `src/filigree/install.py:124-188` (`install_claude_code_mcp`)
- Test: `tests/test_install.py`

**Step 1: Write the failing test**

```python
class TestInstallMcpServerMode:
    def test_server_mode_writes_streamable_http(self, tmp_path: Path) -> None:
        project_root = tmp_path
        filigree_dir = project_root / ".filigree"
        filigree_dir.mkdir()
        config = {"prefix": "test", "mode": "server"}
        (filigree_dir / "config.json").write_text(json.dumps(config))

        ok, msg = install_claude_code_mcp(project_root, mode="server", server_port=8377)
        assert ok
        mcp = json.loads((project_root / ".mcp.json").read_text())
        server_config = mcp["mcpServers"]["filigree"]
        assert server_config["type"] == "streamable-http"
        assert "8377" in server_config["url"]

    def test_ethereal_mode_writes_stdio(self, tmp_path: Path) -> None:
        project_root = tmp_path
        ok, msg = install_claude_code_mcp(project_root, mode="ethereal")
        assert ok
        mcp = json.loads((project_root / ".mcp.json").read_text())
        server_config = mcp["mcpServers"]["filigree"]
        assert server_config.get("type") == "stdio" or "command" in server_config
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_install.py::TestInstallMcpServerMode -v`
Expected: FAIL — `install_claude_code_mcp` doesn't accept `mode` parameter

**Step 3: Write implementation**

Add `mode` and `server_port` parameters to `install_claude_code_mcp`:

```python
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


def _install_mcp_ethereal_mode(project_root: Path) -> tuple[bool, str]:
    """Existing stdio-based MCP install (current behavior)."""
    # ... existing code from install_claude_code_mcp ...


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
```

Extract common `.mcp.json` reading logic into a `_read_mcp_json` helper to avoid duplication.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/install.py tests/test_install.py
git commit -m "feat(install): support server mode MCP config generation"
```

---

### Task 12: Wire mode into the install command

**Files:**
- Modify: `src/filigree/cli.py:782-850` (install command body)
- Modify: `src/filigree/install.py` (pass mode through)
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

```python
class TestInstallModeIntegration:
    def test_install_server_mode_registers_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"])

        config_dir = tmp_path / ".server-config"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.toml")

        result = runner.invoke(cli, ["install", "--mode", "server"])
        assert result.exit_code == 0

        from filigree.server import read_server_config
        sc = read_server_config()
        assert len(sc.projects) == 1
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::TestInstallModeIntegration -v`
Expected: FAIL

**Step 3: Write implementation**

Update the install command body to branch on mode:

```python
    # ... after mode is persisted to config ...

    mode = mode or get_mode(filigree_dir)

    if install_all or claude_code:
        ok, msg = install_claude_code_mcp(project_root, mode=mode)
        results.append(("Claude Code MCP", ok, msg))

    # ... existing codex, claude_md, etc. ...

    # Server mode: register project in server.toml
    if mode == "server":
        try:
            from filigree.server import register_project, daemon_status
            register_project(filigree_dir)
            results.append(("Server registration", True, f"Registered in server.toml"))
            status = daemon_status()
            if not status.running:
                click.echo('\nNote: start the daemon with "filigree server start"')
        except Exception as e:
            results.append(("Server registration", False, str(e)))
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/cli.py tests/test_cli.py
git commit -m "feat(cli): wire mode into install command with server registration"
```

---

### Task 13: Mode-aware doctor checks

**Files:**
- Modify: `src/filigree/install.py:577-924` (`run_doctor`)
- Test: `tests/test_install.py`

**Step 1: Write the failing test**

```python
class TestDoctorModeChecks:
    def test_ethereal_checks_pid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Doctor in ethereal mode should check ephemeral.pid."""
        filigree_dir = _setup_project(tmp_path, mode="ethereal")
        # Write a stale PID
        (filigree_dir / "ephemeral.pid").write_text("99999999")
        monkeypatch.chdir(tmp_path)

        results = run_doctor(project_root=tmp_path)
        names = [r.name for r in results]
        assert "Ephemeral PID" in names
        pid_result = next(r for r in results if r.name == "Ephemeral PID")
        assert not pid_result.passed  # stale PID

    def test_server_checks_daemon(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Doctor in server mode should check daemon health."""
        filigree_dir = _setup_project(tmp_path, mode="server")
        monkeypatch.chdir(tmp_path)

        config_dir = tmp_path / ".server-config"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.toml")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        results = run_doctor(project_root=tmp_path)
        names = [r.name for r in results]
        assert "Server daemon" in names
        daemon_result = next(r for r in results if r.name == "Server daemon")
        assert not daemon_result.passed  # not running


def _setup_project(tmp_path: Path, mode: str = "ethereal") -> Path:
    """Helper to create a minimal filigree project."""
    filigree_dir = tmp_path / ".filigree"
    filigree_dir.mkdir()
    config = {"prefix": "test", "version": 1, "mode": mode}
    (filigree_dir / "config.json").write_text(json.dumps(config))
    from filigree.core import FiligreeDB, DB_FILENAME
    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix="test")
    db.initialize()
    db.close()
    return filigree_dir
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_install.py::TestDoctorModeChecks -v`
Expected: FAIL — doctor doesn't produce "Ephemeral PID" or "Server daemon" checks

**Step 3: Write implementation**

Add mode-aware checks to `run_doctor()` after the existing checks:

```python
    # Mode-specific checks
    mode = get_mode(filigree_dir)

    if mode == "ethereal":
        results.extend(_doctor_ethereal_checks(filigree_dir))
    elif mode == "server":
        results.extend(_doctor_server_checks(filigree_dir))


def _doctor_ethereal_checks(filigree_dir: Path) -> list[CheckResult]:
    """Ethereal mode health checks."""
    from filigree.ephemeral import read_pid, is_process_alive, read_port_file

    results = []
    pid_file = filigree_dir / "ephemeral.pid"
    port_file = filigree_dir / "ephemeral.port"

    if pid_file.exists():
        pid = read_pid(pid_file)
        if pid and is_process_alive(pid):
            results.append(CheckResult("Ephemeral PID", True, f"Process {pid} alive"))
        else:
            results.append(CheckResult(
                "Ephemeral PID", False,
                f"Stale PID file (pid {pid})",
                fix_hint="Remove .filigree/ephemeral.pid or run: filigree ensure-dashboard",
            ))

    if port_file.exists():
        from filigree.hooks import _is_port_listening
        port = read_port_file(port_file)
        if port and _is_port_listening(port):
            results.append(CheckResult("Ephemeral port", True, f"Port {port} listening"))
        else:
            results.append(CheckResult(
                "Ephemeral port", False,
                f"Port {port} not listening",
                fix_hint="Dashboard may have crashed. Run: filigree ensure-dashboard",
            ))

    return results


def _doctor_server_checks(filigree_dir: Path) -> list[CheckResult]:
    """Server mode health checks."""
    from filigree.server import daemon_status, read_server_config

    results = []
    status = daemon_status()
    if status.running:
        results.append(CheckResult(
            "Server daemon", True,
            f"Running (pid {status.pid}, port {status.port}, {status.project_count} projects)",
        ))
    else:
        results.append(CheckResult(
            "Server daemon", False, "Not running",
            fix_hint='Run: filigree server start',
        ))

    # Check registered projects health
    config = read_server_config()
    for path_str, info in config.projects.items():
        p = Path(path_str)
        if not p.is_dir():
            results.append(CheckResult(
                f'Project "{info.get("prefix", "?")}"', False,
                f"Directory gone: {path_str}",
                fix_hint=f"Run: filigree server unregister {p.parent}",
            ))

    return results
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/install.py tests/test_install.py
git commit -m "feat(doctor): add mode-aware health checks for ethereal and server"
```

---

## Phase 4: Remove Hybrid Registration

### Task 14: Delete registry.py and remove all imports

**Files:**
- Delete: `src/filigree/registry.py`
- Delete: `tests/test_registry.py`
- Modify: `src/filigree/dashboard.py` (remove Registry/ProjectManager import — already done in Task 7)
- Modify: `src/filigree/hooks.py` (remove registry imports — already done in Task 6)
- Modify: `tests/test_hooks.py` (remove `_isolate_registry` fixture)
- Modify: `tests/test_dashboard.py` (remove registry fixtures)

**Step 1: Verify no remaining references**

Run: `uv run ruff check src/ tests/` and `grep -r "registry" src/ tests/ --include="*.py" -l`

Confirm only `registry.py` and `test_registry.py` reference registry, plus any leftover imports from Tasks 6-7.

**Step 2: Delete files and clean up imports**

```bash
rm src/filigree/registry.py tests/test_registry.py
```

Remove the `_isolate_registry` fixture from `tests/test_hooks.py`.
Remove registry imports from `tests/test_dashboard.py`.

**Step 3: Run full test suite**

Run: `uv run pytest --tb=short`
Expected: PASS — no broken imports

**Step 4: Run linters**

Run: `uv run ruff check src/ tests/ && uv run mypy src/filigree/`
Expected: Clean

**Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove hybrid registration system (registry.py)"
```

---

### Task 15: Clean up /tmp file conventions

**Files:**
- Verify: no references to `/tmp/filigree-dashboard.*` remain in code
- Modify: any documentation referencing old conventions

**Step 1: Search for stale references**

Run: `grep -r "filigree-dashboard" src/ tests/ docs/ --include="*.py" --include="*.md" -l`

**Step 2: Remove any found references**

Clean up any remaining references to the old `/tmp/filigree-dashboard.lock`, `/tmp/filigree-dashboard.pid`, `/tmp/filigree-dashboard.log` patterns.

**Step 3: Run full CI pipeline**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short
```

Expected: All pass

**Step 4: Commit**

```bash
git add -A
git commit -m "chore: clean up /tmp file conventions from hybrid mode"
```

---

### Task 16: Final integration test and update filigree issues

**Step 1: Run full CI pipeline**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short
```

**Step 2: Manual smoke test**

```bash
# Test ethereal mode (default)
cd /tmp && mkdir test-ethereal && cd test-ethereal
filigree init
filigree install
filigree doctor
filigree ensure-dashboard  # should start on deterministic port

# Test server mode
cd /tmp && mkdir test-server && cd test-server
filigree init --mode=server
filigree install --mode=server
filigree server start
filigree server status
filigree doctor
filigree server stop
```

**Step 3: Close filigree issues**

```bash
filigree close filigree-19acff --reason="Ethereal mode implemented"
filigree close filigree-876888 --reason="Server mode implemented"
filigree close filigree-4b4a68 --reason="Hybrid registration removed"
filigree close filigree-a7f852 --reason="All child issues complete"
```

**Step 4: Final commit if any cleanup needed**

```bash
git add -A
git commit -m "feat: server install + ethereal mode complete"
```
