"""Tests for the scanner TOML registry."""

from __future__ import annotations

import tomllib
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
            '[scanner]\nname = "claude"\ndescription = "d"\ncommand = "python x.py"\nargs = []\nfile_types = []\n'
        )
        result = list_scanners(scanners_dir)
        assert len(result) == 1

    def test_skips_example_files(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        (scanners_dir / "claude.toml.example").write_text(
            '[scanner]\nname = "claude"\ndescription = "d"\ncommand = "python x.py"\nargs = []\nfile_types = []\n'
        )
        result = list_scanners(scanners_dir)
        assert result == []

    def test_skips_malformed_toml(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        (scanners_dir / "bad.toml").write_text("not valid toml [[")
        result = list_scanners(scanners_dir)
        assert result == []

    def test_skips_invalid_field_types(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        (scanners_dir / "bad.toml").write_text(
            '[scanner]\nname = "bad"\ndescription = "d"\ncommand = "python x.py"\nargs = "not-a-list"\nfile_types = [123]\n'
        )
        result = list_scanners(scanners_dir)
        assert result == []

    def test_skips_name_filename_mismatch(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir()
        (scanners_dir / "wrapper.toml").write_text(
            '[scanner]\nname = "different-name"\ndescription = "d"\ncommand = "python x.py"\nargs = []\nfile_types = []\n'
        )
        result = list_scanners(scanners_dir)
        assert result == []


# ── load_scanner ─────────────────────────────────────────────────────


class TestLoadScanner:
    def _write_scanner(self, scanners_dir: Path, name: str = "claude") -> None:
        scanners_dir.mkdir(exist_ok=True)
        (scanners_dir / f"{name}.toml").write_text(
            f'[scanner]\nname = "{name}"\ndescription = "desc"\n'
            f'command = "python scripts/{name}_bug_hunt.py"\n'
            f'args = ["--root", "{{file}}", "--api-url", "{{api_url}}", "--scan-run-id", "{{scan_run_id}}"]\n'
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

    def test_load_rejects_name_filename_mismatch(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        scanners_dir.mkdir(exist_ok=True)
        (scanners_dir / "wrapper.toml").write_text(
            '[scanner]\nname = "different-name"\ndescription = "desc"\n'
            'command = "python scripts/wrapper.py"\n'
            'args = ["--root", "{file}"]\nfile_types = ["py"]\n'
        )
        assert load_scanner(scanners_dir, "wrapper") is None

    def test_build_command_with_scan_run_id(self, tmp_path: Path) -> None:
        scanners_dir = tmp_path / "scanners"
        self._write_scanner(scanners_dir)
        cfg = load_scanner(scanners_dir, "claude")
        assert cfg is not None
        cmd = cfg.build_command(
            file_path="src/core.py",
            api_url="http://localhost:8377",
            project_root="/home/user/project",
            scan_run_id="claude-2026-02-22T10:00:00-abc123",
        )
        assert cmd[0] == "python"
        assert "src/core.py" in cmd
        assert "http://localhost:8377" in cmd
        assert "claude-2026-02-22T10:00:00-abc123" in cmd

    def test_build_command_malformed_quotes(self, tmp_path: Path) -> None:
        """Malformed command string should raise ValueError, not crash."""
        cfg = ScannerConfig(
            name="bad",
            description="bad command",
            command="python 'unclosed",
            args=[],
            file_types=[],
        )
        with pytest.raises(ValueError, match=r"[Mm]alformed"):
            cfg.build_command(file_path="x.py")

    def test_build_command_rejects_non_string_args(self) -> None:
        """Invalid arg types should produce ValueError, not AttributeError/TypeError."""
        cfg = ScannerConfig(
            name="bad",
            description="bad args",
            command="python scanner.py",
            args=["--file", "ok", 42],  # type: ignore[list-item]
            file_types=[],
        )
        with pytest.raises(ValueError, match=r"[Mm]alformed args"):
            cfg.build_command(file_path="x.py")


# ── validate_scanner_command ─────────────────────────────────────────


class TestValidateScannerCommand:
    def test_python_available(self) -> None:
        assert validate_scanner_command("python --version") is None

    def test_nonexistent_command(self) -> None:
        err = validate_scanner_command("nonexistent_cmd_xyz arg1")
        assert err is not None
        assert "not found" in err

    def test_tokenized_command_list(self) -> None:
        assert validate_scanner_command(["python", "--version"]) is None

    def test_empty_tokenized_command_list(self) -> None:
        assert validate_scanner_command([]) == "Empty command"

    def test_relative_executable_resolves_against_project_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        scanner_exec = project_root / "scanner_exec.sh"
        scanner_exec.write_text("#!/usr/bin/env bash\nexit 0\n")
        scanner_exec.chmod(0o755)

        monkeypatch.chdir(tmp_path)
        assert validate_scanner_command("./scanner_exec.sh", project_root=project_root) is None

    def test_relative_executable_fails_without_project_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        scanner_exec = project_root / "scanner_exec.sh"
        scanner_exec.write_text("#!/usr/bin/env bash\nexit 0\n")
        scanner_exec.chmod(0o755)

        monkeypatch.chdir(tmp_path)
        err = validate_scanner_command("./scanner_exec.sh")
        assert err is not None
        assert "not found" in err


class TestScannerExamples:
    @staticmethod
    def _read_example(example_name: str) -> dict[str, object]:
        repo_root = Path(__file__).resolve().parents[1]
        example_path = repo_root / "scripts" / "scanners" / example_name
        return tomllib.loads(example_path.read_text(encoding="utf-8"))

    def test_claude_example_uses_directory_root(self) -> None:
        data = self._read_example("claude.toml.example")
        scanner = data["scanner"]
        assert isinstance(scanner, dict)
        args = scanner["args"]
        assert isinstance(args, list)
        assert "--root" in args
        i = args.index("--root")
        assert i + 1 < len(args)
        assert args[i + 1] == "{project_root}"

    def test_codex_example_uses_directory_root_and_file_target(self) -> None:
        data = self._read_example("codex.toml.example")
        scanner = data["scanner"]
        assert isinstance(scanner, dict)
        args = scanner["args"]
        assert isinstance(args, list)
        assert "--root" in args
        i = args.index("--root")
        assert i + 1 < len(args)
        assert args[i + 1] == "{project_root}"
        assert "--file" in args
        j = args.index("--file")
        assert j + 1 < len(args)
        assert args[j + 1] == "{file}"
