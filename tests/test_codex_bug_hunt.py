from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_codex_bug_hunt() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "codex_bug_hunt.py"
    spec = importlib.util.spec_from_file_location("codex_bug_hunt_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestResolveTargetFile:
    def test_resolve_relative_target_file(self, tmp_path: Path) -> None:
        mod = _load_codex_bug_hunt()
        repo_root = tmp_path / "repo"
        root_dir = repo_root / "src"
        root_dir.mkdir(parents=True)
        target = root_dir / "a.py"
        target.write_text("x = 1\n")

        resolved = mod._resolve_target_file(repo_root=repo_root, root_dir=root_dir, file_arg="src/a.py")
        assert resolved == target.resolve()

    def test_reject_target_outside_root(self, tmp_path: Path) -> None:
        mod = _load_codex_bug_hunt()
        repo_root = tmp_path / "repo"
        root_dir = repo_root / "src"
        root_dir.mkdir(parents=True)
        outside = repo_root / "outside.py"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("x = 1\n")

        with pytest.raises(ValueError, match="outside scan root"):
            mod._resolve_target_file(repo_root=repo_root, root_dir=root_dir, file_arg="outside.py")

    def test_reject_missing_target(self, tmp_path: Path) -> None:
        mod = _load_codex_bug_hunt()
        repo_root = tmp_path / "repo"
        root_dir = repo_root / "src"
        root_dir.mkdir(parents=True)

        with pytest.raises(ValueError, match="does not exist"):
            mod._resolve_target_file(repo_root=repo_root, root_dir=root_dir, file_arg="src/missing.py")
