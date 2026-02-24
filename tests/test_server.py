from __future__ import annotations

import fcntl
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from filigree.server import (
    ServerConfig,
    read_server_config,
    register_project,
    unregister_project,
    write_server_config,
)


class TestServerConfig:
    def test_default_config(self) -> None:
        config = ServerConfig()
        assert config.port == 8377
        assert config.projects == {}

    def test_write_and_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        config = ServerConfig(port=9000)
        write_server_config(config)
        loaded = read_server_config()
        assert loaded.port == 9000

    def test_read_missing_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        config = read_server_config()
        assert config.port == 8377

    def test_roundtrip_with_special_chars_in_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Paths with quotes, spaces, and unicode survive serialization."""
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        weird_path = '/home/alice/my "quoted" project/.filigree'
        config = ServerConfig(projects={weird_path: {"prefix": 'weird"prefix'}})
        write_server_config(config)
        loaded = read_server_config()
        assert weird_path in loaded.projects
        assert loaded.projects[weird_path]["prefix"] == 'weird"prefix'


class TestProjectRegistration:
    def test_register_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        filigree_dir = tmp_path / "myproject" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text('{"prefix": "myproject"}')

        register_project(filigree_dir)
        config = read_server_config()
        assert str(filigree_dir.resolve()) in config.projects

    def test_unregister_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        filigree_dir = tmp_path / "myproject" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text('{"prefix": "myproject"}')

        register_project(filigree_dir)
        unregister_project(filigree_dir)
        config = read_server_config()
        assert str(filigree_dir.resolve()) not in config.projects

    def test_unregister_project_uses_exclusive_lock(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        filigree_dir = tmp_path / "myproject" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text('{"prefix": "myproject"}')
        register_project(filigree_dir)

        lock_ops: list[int] = []

        def _fake_flock(_fd: object, op: int) -> None:
            lock_ops.append(op)

        monkeypatch.setattr("filigree.server.fcntl.flock", _fake_flock)
        unregister_project(filigree_dir)

        assert lock_ops
        assert lock_ops[0] == fcntl.LOCK_EX

    def test_register_project_rejects_prefix_collision(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        first = tmp_path / "project-a" / ".filigree"
        second = tmp_path / "project-b" / ".filigree"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        (first / "config.json").write_text('{"prefix": "filigree"}')
        (second / "config.json").write_text('{"prefix": "filigree"}')

        register_project(first)
        with pytest.raises(ValueError, match="Prefix collision"):
            register_project(second)

    def test_register_project_is_idempotent_for_same_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        filigree_dir = tmp_path / "myproject" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text('{"prefix": "myproject"}')

        register_project(filigree_dir)
        register_project(filigree_dir)
        config = read_server_config()
        assert list(config.projects.keys()) == [str(filigree_dir.resolve())]


class TestVersionEnforcement:
    def test_register_rejects_incompatible_schema(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Projects with newer schema versions are rejected."""
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        filigree_dir = tmp_path / "future-project" / ".filigree"
        filigree_dir.mkdir(parents=True)
        (filigree_dir / "config.json").write_text(
            json.dumps(
                {
                    "prefix": "future",
                    "version": 999,
                }
            )
        )

        with pytest.raises(ValueError, match="schema version"):
            register_project(filigree_dir)


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

    def test_port_zero_returns_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text('{"port": 0}')
        config = read_server_config()
        assert config.port == 8377

    def test_non_dict_projects_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text('{"projects": "not-a-dict"}')
        config = read_server_config()
        assert config.projects == {}

    def test_non_dict_project_values_dropped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text('{"projects": {"/good": {"prefix": "a"}, "/bad": "string-value"}}')
        config = read_server_config()
        assert "/good" in config.projects
        assert "/bad" not in config.projects

    def test_null_json_returns_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text("null")
        config = read_server_config()
        assert config.port == 8377

    def test_empty_config_file_returns_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = self._setup(tmp_path, monkeypatch)
        (config_dir / "server.json").write_text("")
        config = read_server_config()
        assert config.port == 8377
        assert config.projects == {}


class TestPidOwnership:
    """Bug filigree-f56a78: start_daemon/daemon_status must verify PID ownership."""

    def test_start_daemon_clears_stale_foreign_pid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_daemon_status_not_running_for_foreign_pid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestDaemonLifecycle:
    def test_start_writes_pid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        spawned: list = []

        def mock_popen(cmd, **kwargs):  # type: ignore[no-untyped-def]
            mock = MagicMock()
            mock.pid = 54321
            mock.poll.return_value = None
            spawned.append(cmd)
            return mock

        monkeypatch.setattr("filigree.server.subprocess.Popen", mock_popen)
        from filigree.server import start_daemon

        result = start_daemon()
        assert result.success
        pid_data = json.loads((config_dir / "server.pid").read_text())
        assert pid_data["pid"] == 54321
        assert pid_data["cmd"] == "filigree"

    def test_stop_kills_process(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        killed: list[int] = []
        monkeypatch.setattr("os.kill", lambda pid, sig: killed.append(pid))

        # Mock is_pid_alive: first call returns True (so stop proceeds to kill),
        # subsequent calls return False (process exited after SIGTERM)
        alive_calls: list[int] = []

        def mock_is_pid_alive(pid: int) -> bool:
            alive_calls.append(pid)
            return len(alive_calls) == 1  # True first time only

        monkeypatch.setattr("filigree.server.is_pid_alive", mock_is_pid_alive)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)

        from filigree.server import stop_daemon

        result = stop_daemon()
        assert result.success
        assert 54321 in killed

    def test_start_daemon_passes_server_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """start_daemon() must include --server-mode in the spawned command."""
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        spawned: list[list[str]] = []

        def mock_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            mock = MagicMock()
            mock.pid = 99999
            mock.poll.return_value = None
            spawned.append(cmd)
            return mock

        monkeypatch.setattr("filigree.server.subprocess.Popen", mock_popen)
        from filigree.server import start_daemon

        result = start_daemon()
        assert result.success
        assert len(spawned) == 1
        assert "--server-mode" in spawned[0]

    def test_start_daemon_persists_port_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        def mock_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            mock = MagicMock()
            mock.pid = 88888
            mock.poll.return_value = None
            return mock

        monkeypatch.setattr("filigree.server.subprocess.Popen", mock_popen)
        from filigree.server import start_daemon

        result = start_daemon(port=9911)
        assert result.success
        cfg = read_server_config()
        assert cfg.port == 9911

    def test_status_reports_not_running(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        from filigree.server import daemon_status

        status = daemon_status()
        assert not status.running

    def test_claim_current_process_as_daemon_writes_pid_and_port(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", config_dir / "server.pid")

        from filigree.server import claim_current_process_as_daemon, read_server_config

        assert claim_current_process_as_daemon(port=9911)
        pid_data = json.loads((config_dir / "server.pid").read_text())
        assert pid_data["pid"] > 0
        assert pid_data["cmd"] == "filigree"
        assert read_server_config().port == 9911

    def test_claim_succeeds_when_tracked_pid_is_foreign_process(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """PID file exists, PID alive, but NOT filigree → should claim successfully."""
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        # PID alive but NOT a filigree process
        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: pid == 54321)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: False)

        from filigree.server import claim_current_process_as_daemon

        assert claim_current_process_as_daemon(port=9911)

    def test_claim_current_process_as_daemon_refuses_live_filigree_pid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)
        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: pid == 54321)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)

        from filigree.server import claim_current_process_as_daemon

        assert not claim_current_process_as_daemon(port=9911)
        pid_data = json.loads(pid_file.read_text())
        assert pid_data["pid"] == 54321

    def test_release_daemon_pid_if_owned(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        from filigree.server import release_daemon_pid_if_owned

        pid_file.write_text(json.dumps({"pid": 123, "cmd": "filigree"}))
        release_daemon_pid_if_owned(999)
        assert pid_file.exists()

        release_daemon_pid_if_owned(123)
        assert not pid_file.exists()


class TestStartDaemonLocking:
    """Bug filigree-f6c971: start_daemon must serialize with fcntl.flock."""

    def test_start_daemon_acquires_lock(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestStartDaemonPopenFailure:
    """B2 from plan review: Popen OSError must return DaemonResult, not raise."""

    def test_start_returns_failure_when_popen_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestStopDaemonSigkill:
    """Bug filigree-186813: stop_daemon must verify kill succeeded after SIGKILL."""

    def test_stop_succeeds_when_process_dies_before_sigterm(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """os.kill raises ProcessLookupError on SIGTERM → success + PID cleanup."""
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: True)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)

        def mock_kill(pid: int, sig: int) -> None:
            raise ProcessLookupError("No such process")

        monkeypatch.setattr("os.kill", mock_kill)

        from filigree.server import stop_daemon

        result = stop_daemon()
        assert result.success
        assert not pid_file.exists()

    def test_stop_returns_failure_when_sigkill_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_stop_succeeds_when_sigkill_kills_process(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            # First call (stop_daemon liveness check) + 50 SIGTERM waits = 51 calls alive
            # After SIGKILL (call 52+) = dead
            return alive_count <= 51

        monkeypatch.setattr("filigree.server.is_pid_alive", mock_alive)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)
        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from filigree.server import stop_daemon

        result = stop_daemon()
        assert result.success
        assert "Force-killed" in result.message

    def test_stop_handles_sigkill_permission_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_stop_returns_failure_on_sigterm_permission_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """PermissionError on initial SIGTERM (e.g., daemon owned by different user)."""
        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        pid_file = config_dir / "server.pid"
        pid_file.write_text(json.dumps({"pid": 54321, "cmd": "filigree"}))
        monkeypatch.setattr("filigree.server.SERVER_PID_FILE", pid_file)

        monkeypatch.setattr("filigree.server.is_pid_alive", lambda pid: True)
        monkeypatch.setattr("filigree.server.verify_pid_ownership", lambda *a, **kw: True)

        def mock_kill(pid: int, sig: int) -> None:
            raise PermissionError("Operation not permitted")

        monkeypatch.setattr("os.kill", mock_kill)

        from filigree.server import stop_daemon

        result = stop_daemon()
        assert not result.success
        assert "Permission denied" in result.message

    def test_sigkill_failure_still_cleans_pid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
