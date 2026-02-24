# Server Lifecycle Bug Cluster — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 4 unique bugs in `src/filigree/server.py` daemon lifecycle: config validation, startup race, PID ownership, and SIGKILL verification.

**Architecture:** All fixes are in `server.py` (265 lines). Tests go in `tests/test_server.py`. The existing code uses `fcntl.flock` for registration locking and delegates PID operations to `filigree.ephemeral`. We extend both patterns.

**Tech Stack:** Python 3.11+, pytest, monkeypatch, fcntl

**Design doc:** `docs/plans/2026-02-24-server-lifecycle-bugs-design.md`

**Tracker issues:** filigree-11862e, filigree-ddceff (dup of 11862e), filigree-f6c971, filigree-f56a78, filigree-186813

**Review:** `docs/plans/2026-02-24-server-lifecycle-bugs.review.json` — CHANGES_REQUESTED, 2 blockers fixed in rev2:
- B1: PID file cleanup on SIGKILL failure (was leaving system stuck)
- B2: Popen wrapped in try/except OSError (was breaking DaemonResult contract)
- W1: time.sleep(0.5) moved outside lock scope (was holding lock 500ms+)

---

## Task 1: Config Validation — Tests (filigree-11862e + filigree-ddceff)

**Files:**
- Test: `tests/test_server.py`

**Step 1: Write failing tests for `read_server_config()` validation**

Add a new test class after `TestVersionEnforcement` (~line 158):

```python
class TestConfigValidation:
    """Bugs filigree-11862e / filigree-ddceff: read_server_config schema validation."""

    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        return config_dir

    def test_non_dict_json_returns_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text('["a", "list"]')
        config = read_server_config()
        assert config.port == 8377
        assert config.projects == {}

    def test_string_port_coerced_to_int(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text('{"port": "9000"}')
        config = read_server_config()
        assert config.port == 9000

    def test_non_numeric_port_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text('{"port": "not-a-number"}')
        config = read_server_config()
        assert config.port == 8377

    def test_out_of_range_port_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text('{"port": 99999}')
        config = read_server_config()
        assert config.port == 8377

    def test_negative_port_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text('{"port": -1}')
        config = read_server_config()
        assert config.port == 8377

    def test_non_dict_projects_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text('{"projects": "not-a-dict"}')
        config = read_server_config()
        assert config.projects == {}

    def test_non_dict_project_values_dropped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text(
            '{"projects": {"/good": {"prefix": "a"}, "/bad": "string-value"}}'
        )
        config = read_server_config()
        assert "/good" in config.projects
        assert "/bad" not in config.projects

    def test_null_json_returns_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text("null")
        config = read_server_config()
        assert config.port == 8377
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::TestConfigValidation -v`
Expected: Multiple FAILs (non-dict JSON crashes with `AttributeError`, bad port types pass through)

---

## Task 2: Config Validation — Implementation

**Files:**
- Modify: `src/filigree/server.py:38-50`

**Step 3: Implement `read_server_config()` validation**

Replace lines 38-50 with:

```python
def read_server_config() -> ServerConfig:
    """Read server.json. Returns defaults if missing or invalid."""
    if not SERVER_CONFIG_FILE.exists():
        return ServerConfig()
    try:
        data = json.loads(SERVER_CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt server config %s: %s", SERVER_CONFIG_FILE, exc)
        return ServerConfig()

    if not isinstance(data, dict):
        logger.warning("Server config %s is not a JSON object; using defaults", SERVER_CONFIG_FILE)
        return ServerConfig()

    # Coerce port
    raw_port = data.get("port", DEFAULT_PORT)
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if not (1 <= port <= 65535):
        port = DEFAULT_PORT

    # Coerce projects — must be dict of dicts
    raw_projects = data.get("projects", {})
    if not isinstance(raw_projects, dict):
        raw_projects = {}
    projects = {str(k): v for k, v in raw_projects.items() if isinstance(v, dict)}

    return ServerConfig(port=port, projects=projects)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py::TestConfigValidation -v`
Expected: All PASS

**Step 5: Run full test_server.py to check for regressions**

Run: `uv run pytest tests/test_server.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/filigree/server.py tests/test_server.py
git commit -m "fix: validate JSON shape/types in read_server_config()

Fixes filigree-11862e, filigree-ddceff"
```

---

## Task 3: PID Ownership in start_daemon + daemon_status — Tests (filigree-f56a78)

**Files:**
- Test: `tests/test_server.py`

**Step 7: Write failing tests for PID ownership checks**

Add to a new class after `TestConfigValidation`:

```python
class TestPidOwnership:
    """Bug filigree-f56a78: start_daemon/daemon_status must verify PID ownership."""

    def test_start_daemon_clears_stale_foreign_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If PID file points to a live non-filigree process, start should proceed."""
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 99999, "cmd": "filigree"}))

        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        # PID is alive but NOT a filigree process
        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: True)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: False)

        def mock_popen(cmd, **kwargs):  # type: ignore[no-untyped-def]
            mock = MagicMock()
            mock.pid = 11111
            mock.poll.return_value = None
            return mock

        monkeypatch.setattr("filigree.server.subprocess.Popen", mock_popen)

        from filigree.server import start_daemon

        result = start_daemon()
        assert result.success
        assert "11111" in result.message

    def test_daemon_status_not_running_for_foreign_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If PID file points to a live non-filigree process, status should be not running."""
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 99999, "cmd": "filigree"}))

        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)
        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: True)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: False)

        from filigree.server import daemon_status

        status = daemon_status()
        assert not status.running
```

**Step 8: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::TestPidOwnership -v`
Expected: FAIL — `start_daemon` returns "already running", `daemon_status` returns `running=True`

---

## Task 4: PID Ownership — Implementation

**Files:**
- Modify: `src/filigree/server.py:139-142` (start_daemon) and `src/filigree/server.py:213-217` (daemon_status)

**Step 9: Fix start_daemon to verify ownership**

Replace lines 139-142:

```python
    # Check if already running
    info = read_pid_file(SERVER_PID_FILE)
    if info and is_pid_alive(info["pid"]):
        return DaemonResult(False, f"Daemon already running (pid {info['pid']})")
```

With:

```python
    # Check if already running — verify PID is actually a filigree process
    info = read_pid_file(SERVER_PID_FILE)
    if info and is_pid_alive(info["pid"]):
        if verify_pid_ownership(SERVER_PID_FILE, expected_cmd="filigree"):
            return DaemonResult(False, f"Daemon already running (pid {info['pid']})")
        # Stale PID from a reused process — clean up and proceed
        logger.warning("Stale PID file (pid %d is not filigree); cleaning up", info["pid"])
        SERVER_PID_FILE.unlink(missing_ok=True)
```

**Step 10: Fix daemon_status to verify ownership**

Replace lines 213-225:

```python
def daemon_status() -> DaemonStatus:
    """Check daemon status."""
    info = read_pid_file(SERVER_PID_FILE)
    if info is None or not is_pid_alive(info["pid"]):
        return DaemonStatus(running=False)

    # Verify PID belongs to filigree before reporting as running
    if not verify_pid_ownership(SERVER_PID_FILE, expected_cmd="filigree"):
        return DaemonStatus(running=False)

    config = read_server_config()
    return DaemonStatus(
        running=True,
        pid=info["pid"],
        port=config.port,
        project_count=len(config.projects),
    )
```

**Step 11: Run tests**

Run: `uv run pytest tests/test_server.py -v`
Expected: All PASS

**Step 12: Commit**

```bash
git add src/filigree/server.py tests/test_server.py
git commit -m "fix: verify PID ownership in start_daemon and daemon_status

Stale/reused PIDs no longer block startup or misreport status.

Fixes filigree-f56a78"
```

---

## Task 5: Start Daemon Race Condition — Tests (filigree-f6c971)

**Files:**
- Test: `tests/test_server.py`

**Step 13: Write failing test for startup locking**

Add to a new class:

```python
class TestStartDaemonLocking:
    """Bug filigree-f6c971: start_daemon must serialize with fcntl.flock."""

    def test_start_daemon_acquires_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        lock_ops: list[int] = []
        original_flock = fcntl.flock

        def tracking_flock(fd: object, op: int) -> None:
            lock_ops.append(op)
            original_flock(fd, op)  # type: ignore[arg-type]

        monkeypatch.setattr("filigree.server.fcntl.flock", tracking_flock)

        def mock_popen(cmd, **kwargs):  # type: ignore[no-untyped-def]
            mock = MagicMock()
            mock.pid = 77777
            mock.poll.return_value = None
            return mock

        monkeypatch.setattr("filigree.server.subprocess.Popen", mock_popen)

        from filigree.server import start_daemon

        result = start_daemon()
        assert result.success
        assert fcntl.LOCK_EX in lock_ops
```

**Step 14: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py::TestStartDaemonLocking -v`
Expected: FAIL — `lock_ops` is empty

---

## Task 6: Start Daemon Race Condition — Implementation

**Files:**
- Modify: `src/filigree/server.py:135-173`

**Step 15: Wrap start_daemon in fcntl.flock**

Replace `start_daemon` (lines 135-173) with:

```python
def start_daemon(port: int | None = None) -> DaemonResult:
    """Start the filigree server daemon."""
    from filigree.core import find_filigree_command

    SERVER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = SERVER_CONFIG_DIR / "server.lock"
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Check if already running — verify PID is actually a filigree process
        info = read_pid_file(SERVER_PID_FILE)
        if info and is_pid_alive(info["pid"]):
            if verify_pid_ownership(SERVER_PID_FILE, expected_cmd="filigree"):
                return DaemonResult(False, f"Daemon already running (pid {info['pid']})")
            # Stale PID from a reused process — clean up and proceed
            logger.warning("Stale PID file (pid %d is not filigree); cleaning up", info["pid"])
            SERVER_PID_FILE.unlink(missing_ok=True)

        config = read_server_config()
        daemon_port = port or config.port
        # Persist the effective daemon port so status/hooks/install agree.
        if config.port != daemon_port:
            config.port = daemon_port
            write_server_config(config)

        filigree_cmd = find_filigree_command()
        log_file = SERVER_CONFIG_DIR / "server.log"

        try:
            with open(log_file, "w") as log_fd:
                proc = subprocess.Popen(
                    [*filigree_cmd, "dashboard", "--no-browser", "--server-mode", "--port", str(daemon_port)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log_fd,
                    start_new_session=True,
                )
        except OSError as exc:
            return DaemonResult(False, f"Failed to start daemon: {exc}")

        write_pid_file(SERVER_PID_FILE, proc.pid, cmd="filigree")
        # Lock released here — critical section is complete

    # Post-startup health check runs outside the lock to avoid blocking
    # concurrent register_project() calls (W1 from plan review)
    time.sleep(0.5)
    exit_code = proc.poll()
    if exit_code is not None:
        SERVER_PID_FILE.unlink(missing_ok=True)
        stderr = log_file.read_text().strip()
        return DaemonResult(False, f"Daemon exited immediately (code {exit_code}): {stderr}")

    return DaemonResult(True, f"Started filigree daemon (pid {proc.pid}) on port {daemon_port}")
```

**Step 16: Run tests**

Run: `uv run pytest tests/test_server.py -v`
Expected: All PASS

**Step 17: Commit**

```bash
git add src/filigree/server.py tests/test_server.py
git commit -m "fix: serialize start_daemon with fcntl.flock to prevent race

Fixes filigree-f6c971"
```

---

## Task 7: SIGKILL Verification — Tests (filigree-186813)

**Files:**
- Test: `tests/test_server.py`

**Step 18: Write failing test for SIGKILL verification**

Add to a new class:

```python
class TestStopDaemonSigkill:
    """Bug filigree-186813: stop_daemon must verify kill succeeded after SIGKILL."""

    def test_stop_returns_failure_when_sigkill_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        # Process is always alive — survives both SIGTERM and SIGKILL
        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: True)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)
        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from filigree.server import stop_daemon

        result = stop_daemon()
        assert not result.success
        assert "SIGKILL" in result.message or "Failed" in result.message

    def test_stop_succeeds_when_sigkill_kills_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        # Process survives SIGTERM (50 checks), but dies after SIGKILL
        alive_count = 0

        def mock_alive(pid: int) -> bool:
            nonlocal alive_count
            alive_count += 1
            # First call (stop_daemon liveness check) + 50 SIGTERM waits = alive
            # After SIGKILL (call 52+) = dead
            return alive_count <= 52

        monkeypatch.setattr("filigree.server.is_pid_alive", mock_alive)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)
        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from filigree.server import stop_daemon

        result = stop_daemon()
        assert result.success
        assert "Force-killed" in result.message

    def test_stop_handles_sigkill_permission_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: True)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)
        monkeypatch.setattr("time.sleep", lambda _: None)

        kill_count = 0

        def mock_kill(pid: int, sig: int) -> None:
            nonlocal kill_count
            kill_count += 1
            if kill_count > 1:  # SIGKILL (second kill call)
                raise PermissionError("Operation not permitted")

        monkeypatch.setattr("os.kill", mock_kill)

        from filigree.server import stop_daemon

        result = stop_daemon()
        assert not result.success
        assert "Permission denied" in result.message

    def test_sigkill_failure_still_cleans_pid_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B1 from plan review: PID file must be cleaned up even when SIGKILL fails."""
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: True)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)
        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from filigree.server import stop_daemon

        result = stop_daemon()
        assert not result.success
        # Critical: PID file must be removed to prevent stuck state
        assert not pid_file.exists()


class TestStartDaemonPopenFailure:
    """B2 from plan review: Popen OSError must return DaemonResult, not raise."""

    def test_start_returns_failure_when_popen_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        def raising_popen(cmd, **kwargs):  # type: ignore[no-untyped-def]
            raise FileNotFoundError("filigree command not found")

        monkeypatch.setattr("filigree.server.subprocess.Popen", raising_popen)

        from filigree.server import start_daemon

        result = start_daemon()
        assert not result.success
        assert "Failed to start" in result.message

    def test_empty_config_file_returns_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """W4 from plan review: empty (0-byte) config file."""
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        (config_dir / "server.json").write_text("")
        config = read_server_config()
        assert config.port == 8377
        assert config.projects == {}
```

**Step 19: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::TestStopDaemonSigkill -v`
Expected: `test_stop_returns_failure_when_sigkill_fails` FAILs (currently returns success)

---

## Task 8: SIGKILL Verification — Implementation

**Files:**
- Modify: `src/filigree/server.py:202-210`

**Step 20: Fix stop_daemon SIGKILL path**

Replace lines 202-210:

```python
    # Escalate to SIGKILL
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
    except (PermissionError, ProcessLookupError):
        pass

    SERVER_PID_FILE.unlink(missing_ok=True)
    return DaemonResult(True, f"Force-killed filigree daemon (pid {pid})")
```

With:

```python
    # Escalate to SIGKILL
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
    except ProcessLookupError:
        # Process died between last check and SIGKILL — success
        SERVER_PID_FILE.unlink(missing_ok=True)
        return DaemonResult(True, f"Force-killed filigree daemon (pid {pid})")
    except PermissionError:
        return DaemonResult(False, f"Permission denied sending SIGKILL to pid {pid}")

    # Verify the process is actually dead
    if is_pid_alive(pid):
        # B1 fix: still clean up PID file to prevent permanent stuck state
        SERVER_PID_FILE.unlink(missing_ok=True)
        logger.warning("Failed to kill daemon (pid %d) even with SIGKILL", pid)
        return DaemonResult(False, f"Failed to kill daemon (pid {pid}) even with SIGKILL")

    SERVER_PID_FILE.unlink(missing_ok=True)
    return DaemonResult(True, f"Force-killed filigree daemon (pid {pid})")
```

**Step 21: Run tests**

Run: `uv run pytest tests/test_server.py -v`
Expected: All PASS

**Step 22: Commit**

```bash
git add src/filigree/server.py tests/test_server.py
git commit -m "fix: verify process death after SIGKILL in stop_daemon

Fixes filigree-186813"
```

---

## Task 9: Full CI Check + Issue Closure

**Step 23: Run full CI pipeline**

```bash
uv run ruff check src/filigree/server.py tests/test_server.py
uv run ruff format --check src/filigree/server.py tests/test_server.py
uv run mypy src/filigree/server.py
uv run pytest tests/test_server.py -v
```

Expected: All pass with no warnings.

**Step 24: Close tracker issues**

Close these filigree issues with appropriate reasons:
- `filigree-11862e` — fixed: config validation added
- `filigree-ddceff` — closed as duplicate of filigree-11862e
- `filigree-f6c971` — fixed: fcntl.flock serialization added
- `filigree-f56a78` — fixed: verify_pid_ownership used in start/status
- `filigree-186813` — fixed: SIGKILL liveness re-check added
