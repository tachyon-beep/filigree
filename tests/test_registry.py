"""Tests for the ephemeral multi-project registry."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from filigree.core import FiligreeDB, write_config
from filigree.registry import ProjectManager, Registry


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Override the registry directory to a temp location."""
    reg_dir = tmp_path / ".filigree"
    monkeypatch.setattr("filigree.registry.REGISTRY_DIR", reg_dir)
    monkeypatch.setattr("filigree.registry.REGISTRY_FILE", reg_dir / "registry.json")
    monkeypatch.setattr("filigree.registry.REGISTRY_LOCK", reg_dir / "registry.lock")
    return reg_dir


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """Create a minimal .filigree/ project directory."""
    fdir = tmp_path / "myproject" / ".filigree"
    fdir.mkdir(parents=True)
    write_config(fdir, {"prefix": "myproj", "version": 1, "enabled_packs": ["core"]})
    db = FiligreeDB(fdir / "filigree.db", prefix="myproj")
    db.initialize()
    db.close()
    return fdir


class TestRegistryRegister:
    def test_creates_registry_dir(self, registry_dir: Path, fake_project: Path) -> None:
        reg = Registry()
        reg.register(fake_project)
        assert registry_dir.is_dir()
        assert (registry_dir / "registry.json").exists()

    def test_writes_entry(self, registry_dir: Path, fake_project: Path) -> None:
        reg = Registry()
        entry = reg.register(fake_project)
        assert entry.name == "myproj"
        assert entry.key == "myproj"
        assert entry.path == str(fake_project)

    def test_updates_last_seen(self, registry_dir: Path, fake_project: Path) -> None:
        reg = Registry()
        e1 = reg.register(fake_project)
        time.sleep(0.05)
        e2 = reg.register(fake_project)
        assert e2.last_seen >= e1.last_seen

    def test_two_projects_distinct_keys(self, registry_dir: Path, fake_project: Path, tmp_path: Path) -> None:
        fdir2 = tmp_path / "other" / ".filigree"
        fdir2.mkdir(parents=True)
        write_config(fdir2, {"prefix": "other", "version": 1, "enabled_packs": ["core"]})
        db = FiligreeDB(fdir2 / "filigree.db", prefix="other")
        db.initialize()
        db.close()

        reg = Registry()
        e1 = reg.register(fake_project)
        e2 = reg.register(fdir2)
        assert e1.key != e2.key

    def test_collision_appends_hash(self, registry_dir: Path, fake_project: Path, tmp_path: Path) -> None:
        """Two projects with the same prefix get disambiguated keys."""
        fdir2 = tmp_path / "other" / ".filigree"
        fdir2.mkdir(parents=True)
        write_config(fdir2, {"prefix": "myproj", "version": 1, "enabled_packs": ["core"]})
        db = FiligreeDB(fdir2 / "filigree.db", prefix="myproj")
        db.initialize()
        db.close()

        reg = Registry()
        e1 = reg.register(fake_project)
        e2 = reg.register(fdir2)
        assert e1.key == "myproj"  # first one gets the clean key
        assert e2.key.startswith("myproj-")
        assert len(e2.key) > len("myproj-")


class TestRegistryActiveProjects:
    def test_returns_recent(self, registry_dir: Path, fake_project: Path) -> None:
        reg = Registry()
        reg.register(fake_project)
        active = reg.active_projects(ttl_hours=1.0)
        assert len(active) == 1
        assert active[0].key == "myproj"

    def test_filters_expired(self, registry_dir: Path, fake_project: Path) -> None:
        reg = Registry()
        reg.register(fake_project)
        # Manually backdate the entry
        data = json.loads((registry_dir / "registry.json").read_text())
        path_key = str(fake_project)
        data[path_key]["last_seen"] = "2020-01-01T00:00:00+00:00"
        (registry_dir / "registry.json").write_text(json.dumps(data))
        active = reg.active_projects(ttl_hours=1.0)
        assert len(active) == 0


class TestRegistryCorruptFile:
    def test_corrupt_json_resets(self, registry_dir: Path, fake_project: Path) -> None:
        registry_dir.mkdir(parents=True, exist_ok=True)
        (registry_dir / "registry.json").write_text("NOT JSON{{{")
        reg = Registry()
        entry = reg.register(fake_project)
        assert entry.key == "myproj"


class TestProjectManager:
    def test_register_and_get_db(self, registry_dir: Path, fake_project: Path) -> None:
        reg = Registry()
        pm = ProjectManager(reg)
        entry = pm.register(fake_project)
        db = pm.get_db(entry.key)
        assert db is not None
        issues = db.list_issues()
        assert isinstance(issues, list)

    def test_get_db_cached(self, registry_dir: Path, fake_project: Path) -> None:
        reg = Registry()
        pm = ProjectManager(reg)
        entry = pm.register(fake_project)
        db1 = pm.get_db(entry.key)
        db2 = pm.get_db(entry.key)
        assert db1 is db2

    def test_get_db_unknown_returns_none(self, registry_dir: Path) -> None:
        reg = Registry()
        pm = ProjectManager(reg)
        assert pm.get_db("nonexistent") is None

    def test_close_all(self, registry_dir: Path, fake_project: Path) -> None:
        reg = Registry()
        pm = ProjectManager(reg)
        entry = pm.register(fake_project)
        pm.get_db(entry.key)
        pm.close_all()
        assert len(pm._connections) == 0

    def test_active_projects(self, registry_dir: Path, fake_project: Path) -> None:
        reg = Registry()
        pm = ProjectManager(reg)
        pm.register(fake_project)
        projects = pm.get_active_projects()
        assert len(projects) == 1
        assert projects[0].key == "myproj"

    def test_get_db_stale_path_returns_none(self, registry_dir: Path, fake_project: Path) -> None:
        """Stale registry entry pointing to deleted directory returns None, not 500."""
        import shutil

        reg = Registry()
        pm = ProjectManager(reg)
        entry = pm.register(fake_project)
        # Delete the project directory
        shutil.rmtree(fake_project)
        # Evict from connection cache so get_db re-opens
        pm._connections.pop(entry.key, None)
        assert pm.get_db(entry.key) is None

    def test_get_db_missing_db_file_returns_none(self, registry_dir: Path, fake_project: Path) -> None:
        """Registry entry where .filigree/ exists but DB file was deleted returns None."""
        reg = Registry()
        pm = ProjectManager(reg)
        entry = pm.register(fake_project)
        # Delete just the database file
        (fake_project / "filigree.db").unlink()
        pm._connections.pop(entry.key, None)
        assert pm.get_db(entry.key) is None

    def test_register_local_fallback(self, registry_dir: Path, fake_project: Path) -> None:
        """register_local derives a ProjectEntry without writing to the registry."""
        reg = Registry()
        pm = ProjectManager(reg)
        entry = pm.register_local(fake_project)
        assert entry.key == "myproj"
        assert entry.name == "myproj"
        assert entry.path == str(fake_project.resolve())
        # Path is cached — get_db should work
        db = pm.get_db(entry.key)
        assert db is not None

    def test_register_local_when_registry_unwritable(self, registry_dir: Path, fake_project: Path) -> None:
        """register_local works even when the registry directory doesn't exist."""
        reg = Registry()
        pm = ProjectManager(reg)
        # Don't create the registry dir — register_local shouldn't need it
        entry = pm.register_local(fake_project)
        assert entry.key == "myproj"
        assert not registry_dir.exists(), "register_local should not create the registry dir"

    def test_register_local_collision_gets_unique_key(
        self, registry_dir: Path, fake_project: Path, tmp_path: Path
    ) -> None:
        """Two local-only projects with the same prefix get distinct keys."""
        fdir2 = tmp_path / "other" / ".filigree"
        fdir2.mkdir(parents=True)
        write_config(fdir2, {"prefix": "myproj", "version": 1, "enabled_packs": ["core"]})
        db = FiligreeDB(fdir2 / "filigree.db", prefix="myproj")
        db.initialize()
        db.close()

        reg = Registry()
        pm = ProjectManager(reg)
        e1 = pm.register_local(fake_project)
        e2 = pm.register_local(fdir2)

        assert e1.key != e2.key, "Same-prefix projects must get distinct keys"
        assert e1.key == "myproj"
        assert e2.key.startswith("myproj-")
        # Both projects must remain accessible
        assert pm.get_db(e1.key) is not None
        assert pm.get_db(e2.key) is not None
