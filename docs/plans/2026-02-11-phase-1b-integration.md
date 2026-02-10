# Phase 1B: Integration (Core.py Wiring)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire the template engine into core.py — schema migration, lazy property, config enrichment, seed update. No behavior changes yet: existing status validation still runs the old way.

**PR Strategy:** Single PR. The schema migration (v4 to v5) is a one-way door — includes backup, error handling, and validation per review fix B2. After this PR, the new tables exist and templates load, but issue creation/update/close still uses the old 3-state model.

**Prerequisites:**
- Phase 1A merged (templates.py and templates_data.py exist)
- `make ci` passes clean

**Parent plan:** `2026-02-11-workflow-templates-implementation.md`
**Depends on:** `2026-02-11-phase-1a-template-engine.md`

**Key invariant:** After Phase 1B merges, ALL EXISTING TESTS MUST STILL PASS with NO behavior changes. The template system is wired in but not activated — old `_validate_status()` still checks against `workflow_states` config, not templates. `create_issue()` still hardcodes `'open'`. `claim_issue()` still hardcodes `'in_progress'`. `close_issue()` still hardcodes `'closed'`.

---

## Task Order and Dependencies

```
Task 1.14 (Config)     ─┐
                         ├──> Task 1.7 (Schema Migration) ──> Task 1.13 (Seed Update) ──> Task 1.8 (Lazy Property)
Task 1.6 (Loading)     ─┘
```

Rationale:
- Task 1.14 comes first because the migration and loading depend on `enabled_packs` existing in config.
- Task 1.6 comes first because `load()` is called by the lazy property and tested in the migration context.
- Task 1.7 depends on both: the migration seeds packs into the new tables using `BUILT_IN_PACKS` data, and the post-migration validation implicitly depends on config.
- Task 1.13 depends on 1.7 because `_seed_templates()` writes to the new `type_templates` table created by the migration.
- Task 1.8 depends on 1.13 because the lazy property calls `load()` which reads from the new tables.

Implementation order: **1.14 -> 1.6 -> 1.7 -> 1.13 -> 1.8**

---

## Task 1.14: Config Enrichment

Update `read_config()` to default `enabled_packs` to `["core", "planning"]` when missing. Update `from_project()` to pass `enabled_packs` through.

**Files:**
- Modify: `src/keel/core.py` (lines 45-51, 470-480)
- Test: `tests/test_config_packs.py` (new file — keeps config tests isolated from migration tests)

**Why first:** The schema migration and template loading both read `enabled_packs` from config. Establishing the default here means every downstream consumer gets consistent behavior.

### Step 1: Write the failing tests

```python
# tests/test_config_packs.py
"""Tests for config.json enabled_packs support."""

from __future__ import annotations

import json
from pathlib import Path

from keel.core import KeelDB, read_config, write_config


class TestConfigEnabledPacks:
    """Verify enabled_packs default and passthrough."""

    def test_read_config_missing_enabled_packs_gets_default(self, tmp_path: Path) -> None:
        """Config without enabled_packs should default to core + planning."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {"prefix": "test", "version": 1})

        config = read_config(keel_dir)
        assert config["enabled_packs"] == ["core", "planning"]

    def test_read_config_preserves_explicit_enabled_packs(self, tmp_path: Path) -> None:
        """Config with explicit enabled_packs should be preserved as-is."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {"prefix": "test", "version": 1, "enabled_packs": ["core"]})

        config = read_config(keel_dir)
        assert config["enabled_packs"] == ["core"]

    def test_read_config_empty_enabled_packs_preserved(self, tmp_path: Path) -> None:
        """Config with empty enabled_packs (feature flag off) should stay empty."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {"prefix": "test", "version": 1, "enabled_packs": []})

        config = read_config(keel_dir)
        assert config["enabled_packs"] == []

    def test_read_config_no_file_gets_defaults(self, tmp_path: Path) -> None:
        """Missing config.json should return defaults including enabled_packs."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        # No config file written

        config = read_config(keel_dir)
        assert config["prefix"] == "keel"
        assert config["enabled_packs"] == ["core", "planning"]

    def test_from_project_passes_enabled_packs(self, tmp_path: Path) -> None:
        """KeelDB.from_project() should read enabled_packs from config."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {
            "prefix": "proj",
            "version": 1,
            "enabled_packs": ["core", "planning", "risk"],
        })

        # Initialize the database so from_project can open it
        init_db = KeelDB(keel_dir / "keel.db", prefix="proj")
        init_db.initialize()
        init_db.close()

        db = KeelDB.from_project(tmp_path)
        assert db.enabled_packs == ["core", "planning", "risk"]
        db.close()

    def test_from_project_default_enabled_packs(self, tmp_path: Path) -> None:
        """KeelDB.from_project() with no enabled_packs in config gets default."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {"prefix": "proj", "version": 1})

        init_db = KeelDB(keel_dir / "keel.db", prefix="proj")
        init_db.initialize()
        init_db.close()

        db = KeelDB.from_project(tmp_path)
        assert db.enabled_packs == ["core", "planning"]
        db.close()
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_config_packs.py -v
```

Expected failures:
- `test_read_config_missing_enabled_packs_gets_default` — `KeyError: 'enabled_packs'` because `read_config()` does not inject the default.
- `test_from_project_passes_enabled_packs` — `AttributeError: 'KeelDB' object has no attribute 'enabled_packs'`

### Step 3: Implement

**Change 1: `read_config()` in `src/keel/core.py` (line 45)**

```python
def read_config(keel_dir: Path) -> dict[str, Any]:
    """Read .keel/config.json. Returns defaults if missing."""
    config_path = keel_dir / CONFIG_FILENAME
    if config_path.exists():
        result: dict[str, Any] = json.loads(config_path.read_text())
        # Default enabled_packs if absent (backward compat — WFT-FR-057)
        if "enabled_packs" not in result:
            result["enabled_packs"] = ["core", "planning"]
        return result
    return {"prefix": "keel", "version": 1, "enabled_packs": ["core", "planning"]}
```

**Change 2: `KeelDB.__init__()` in `src/keel/core.py` (line 457)**

Add `enabled_packs` parameter:

```python
def __init__(
    self,
    db_path: str | Path,
    *,
    prefix: str = "keel",
    workflow_states: list[str] | None = None,
    enabled_packs: list[str] | None = None,
) -> None:
    self.db_path = Path(db_path)
    self.prefix = prefix
    self.workflow_states = workflow_states or DEFAULT_WORKFLOW_STATES
    self.enabled_packs = enabled_packs if enabled_packs is not None else ["core", "planning"]
    self._conn: sqlite3.Connection | None = None
```

**Change 3: `KeelDB.from_project()` in `src/keel/core.py` (line 470)**

Pass `enabled_packs` through:

```python
@classmethod
def from_project(cls, project_path: Path | None = None) -> KeelDB:
    """Create a KeelDB by discovering .keel/ from project_path (or cwd)."""
    keel_dir = find_keel_root(project_path)
    config = read_config(keel_dir)
    db = cls(
        keel_dir / DB_FILENAME,
        prefix=config.get("prefix", "keel"),
        workflow_states=config.get("workflow_states"),
        enabled_packs=config.get("enabled_packs"),
    )
    db.initialize()
    return db
```

### Step 4: Run tests and CI

```bash
uv run pytest tests/test_config_packs.py -v
```

Expected: All 6 tests pass.

```bash
make ci
```

Expected: All existing tests still pass. The new `enabled_packs` parameter has a default value, so no existing callers break. `KeelDB(path, prefix="x")` still works — `enabled_packs` defaults to `["core", "planning"]`.

### Step 5: Commit

```
feat(core): config.json gains enabled_packs with backward-compatible default

- read_config() defaults enabled_packs to ["core", "planning"] when absent
- Explicit empty list [] is preserved (feature flag off)
- KeelDB.__init__() gains enabled_packs parameter with default
- KeelDB.from_project() passes enabled_packs from config
- No-file fallback also includes enabled_packs

Implements: WFT-FR-057
```

### Definition of Done
- [ ] `read_config()` returns `enabled_packs` key for all code paths (file exists, file missing)
- [ ] Explicit `enabled_packs: []` preserved (not overwritten with default)
- [ ] `KeelDB.enabled_packs` attribute exists with correct default
- [ ] `KeelDB.from_project()` passes `enabled_packs` through
- [ ] All existing tests still pass (no behavior changes)
- [ ] `make ci` passes clean

---

## Task 1.6: TemplateRegistry — Three-Layer Loading

Implement `load()` method that loads from: (1) built-in Python data, (2) `.keel/packs/*.json`, (3) `.keel/templates/*.json`. Also reads `config.json` for `enabled_packs`.

**Files:**
- Modify: `src/keel/templates.py`
- Test: `tests/test_templates.py` (add `TestTemplateLoading` class)

### Step 1: Write the failing tests

```python
# Add to tests/test_templates.py
import json
from pathlib import Path


class TestTemplateLoading:
    """Test three-layer template resolution."""

    @pytest.fixture()
    def keel_dir(self, tmp_path: Path) -> Path:
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}
        (keel_dir / "config.json").write_text(json.dumps(config))
        return keel_dir

    def test_load_built_ins(self, keel_dir: Path) -> None:
        reg = TemplateRegistry()
        reg.load(keel_dir)
        assert reg.get_type("task") is not None
        assert reg.get_type("bug") is not None
        assert reg.get_type("milestone") is not None

    def test_load_respects_enabled_packs(self, keel_dir: Path) -> None:
        """Only types from enabled packs should be available."""
        reg = TemplateRegistry()
        reg.load(keel_dir)
        # Core and planning enabled — their types exist
        assert reg.get_type("task") is not None
        assert reg.get_type("milestone") is not None
        # Risk pack not enabled — risk type should NOT be available
        # (risk pack is a stub with no types in Phase 1, but test the principle)

    def test_load_is_idempotent(self, keel_dir: Path) -> None:
        reg = TemplateRegistry()
        reg.load(keel_dir)
        types_count_1 = len(reg.list_types())
        reg.load(keel_dir)
        types_count_2 = len(reg.list_types())
        assert types_count_1 == types_count_2

    def test_load_project_override(self, keel_dir: Path) -> None:
        """Layer 3 (project-local) overrides built-in types."""
        templates_dir = keel_dir / "templates"
        templates_dir.mkdir()
        custom_task = {
            "type": "task",
            "display_name": "Custom Task",
            "description": "Overridden task",
            "pack": "core",
            "states": [
                {"name": "todo", "category": "open"},
                {"name": "doing", "category": "wip"},
                {"name": "done", "category": "done"},
            ],
            "initial_state": "todo",
            "transitions": [
                {"from": "todo", "to": "doing", "enforcement": "soft"},
                {"from": "doing", "to": "done", "enforcement": "soft"},
            ],
            "fields_schema": [],
        }
        (templates_dir / "task.json").write_text(json.dumps(custom_task))

        reg = TemplateRegistry()
        reg.load(keel_dir)
        task = reg.get_type("task")
        assert task is not None
        assert task.display_name == "Custom Task"
        assert task.initial_state == "todo"

    def test_load_skips_invalid_json(self, keel_dir: Path) -> None:
        """Invalid JSON files in templates/ should be skipped, not crash."""
        templates_dir = keel_dir / "templates"
        templates_dir.mkdir()
        (templates_dir / "broken.json").write_text("not valid json {{{")

        reg = TemplateRegistry()
        reg.load(keel_dir)  # Should not raise
        assert reg.get_type("task") is not None  # Built-ins still loaded

    def test_load_missing_enabled_packs_defaults(self, keel_dir: Path) -> None:
        """Config without enabled_packs defaults to core + planning."""
        config = {"prefix": "test", "version": 1}
        (keel_dir / "config.json").write_text(json.dumps(config))
        reg = TemplateRegistry()
        reg.load(keel_dir)
        assert reg.get_type("task") is not None

    def test_load_installed_pack_layer2(self, keel_dir: Path) -> None:
        """Layer 2: packs from .keel/packs/*.json are loaded."""
        packs_dir = keel_dir / "packs"
        packs_dir.mkdir()
        custom_pack = {
            "pack": "custom_pack",
            "version": "1.0",
            "display_name": "Custom",
            "description": "Custom installed pack",
            "requires_packs": [],
            "types": {
                "custom_type": {
                    "type": "custom_type",
                    "display_name": "Custom Type",
                    "description": "A custom type",
                    "pack": "custom_pack",
                    "states": [
                        {"name": "open", "category": "open"},
                        {"name": "closed", "category": "done"},
                    ],
                    "initial_state": "open",
                    "transitions": [
                        {"from": "open", "to": "closed", "enforcement": "soft"},
                    ],
                    "fields_schema": [],
                },
            },
            "relationships": [],
            "cross_pack_relationships": [],
            "guide": None,
        }
        (packs_dir / "custom_pack.json").write_text(json.dumps(custom_pack))

        # Enable the custom pack in config
        config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning", "custom_pack"]}
        (keel_dir / "config.json").write_text(json.dumps(config))

        reg = TemplateRegistry()
        reg.load(keel_dir)
        assert reg.get_type("custom_type") is not None

    def test_load_disabled_pack_not_loaded(self, keel_dir: Path) -> None:
        """Packs not in enabled_packs should not have their types loaded."""
        packs_dir = keel_dir / "packs"
        packs_dir.mkdir()
        extra_pack = {
            "pack": "extra",
            "version": "1.0",
            "display_name": "Extra",
            "description": "Not enabled",
            "requires_packs": [],
            "types": {
                "extra_type": {
                    "type": "extra_type",
                    "display_name": "Extra",
                    "description": "Extra type",
                    "pack": "extra",
                    "states": [{"name": "open", "category": "open"}],
                    "initial_state": "open",
                    "transitions": [],
                    "fields_schema": [],
                },
            },
            "relationships": [],
            "cross_pack_relationships": [],
            "guide": None,
        }
        (packs_dir / "extra.json").write_text(json.dumps(extra_pack))

        # Only core+planning enabled, NOT extra
        config = {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]}
        (keel_dir / "config.json").write_text(json.dumps(config))

        reg = TemplateRegistry()
        reg.load(keel_dir)
        assert reg.get_type("extra_type") is None
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_templates.py::TestTemplateLoading -v
```

Expected: `AttributeError: 'TemplateRegistry' object has no attribute 'load'`

### Step 3: Implement

Add the `load()` method to the `TemplateRegistry` class in `src/keel/templates.py`:

```python
    def load(self, keel_dir: Path) -> None:
        """Load templates from all three layers (WFT-FR-002 through WFT-FR-005).

        Layer 1: Built-in packs from templates_data.BUILT_IN_PACKS
        Layer 2: Installed packs from .keel/packs/*.json
        Layer 3: Project-local overrides from .keel/templates/*.json

        Idempotent: second call is a no-op.

        Args:
            keel_dir: Path to the .keel/ directory.
        """
        if self._loaded:
            return

        import json as _json

        from keel.templates_data import BUILT_IN_PACKS

        # Read enabled packs from config
        config_path = keel_dir / "config.json"
        enabled_packs: list[str] = ["core", "planning"]  # default
        if config_path.exists():
            try:
                config = _json.loads(config_path.read_text())
                enabled_packs = config.get("enabled_packs", ["core", "planning"])
            except (ValueError, KeyError):
                logger.warning("Could not read config.json — using default enabled_packs")

        logger.info("Loading templates: enabled_packs=%s", enabled_packs)

        # Layer 1: Built-in packs
        for pack_name, pack_data in BUILT_IN_PACKS.items():
            if pack_name not in enabled_packs:
                logger.debug("Skipping disabled built-in pack: %s", pack_name)
                continue
            self._load_pack_data(pack_data)

        # Layer 2: Installed packs from .keel/packs/*.json
        packs_dir = keel_dir / "packs"
        if packs_dir.is_dir():
            for pack_file in sorted(packs_dir.glob("*.json")):
                try:
                    pack_data = _json.loads(pack_file.read_text())
                    pack_name = pack_data.get("pack", pack_file.stem)
                    if pack_name not in enabled_packs:
                        logger.debug("Skipping disabled installed pack: %s", pack_name)
                        continue
                    self._load_pack_data(pack_data)
                    logger.info("Loaded installed pack: %s from %s", pack_name, pack_file.name)
                except (ValueError, KeyError) as exc:
                    logger.warning("Skipping invalid pack file %s: %s", pack_file.name, exc)

        # Layer 3: Project-local overrides from .keel/templates/*.json
        templates_dir = keel_dir / "templates"
        if templates_dir.is_dir():
            for tpl_file in sorted(templates_dir.glob("*.json")):
                try:
                    raw = _json.loads(tpl_file.read_text())
                    tpl = self.parse_type_template(raw)
                    errors = self.validate_type_template(tpl)
                    if errors:
                        logger.warning("Skipping invalid template %s: %s", tpl_file.name, errors)
                        continue
                    self._register_type(tpl)  # Overwrites built-in with same name
                    logger.info("Loaded project-local template override: %s", tpl.type)
                except (ValueError, KeyError) as exc:
                    logger.warning("Skipping invalid template file %s: %s", tpl_file.name, exc)

        self._loaded = True
        logger.info("Template loading complete: %d types from %d packs", len(self._types), len(self._packs))

    def _load_pack_data(self, pack_data: dict[str, Any]) -> None:
        """Load a pack dict: register its types and the pack itself."""
        pack_name = pack_data["pack"]

        # Parse and register each type in the pack
        types_dict: dict[str, TypeTemplate] = {}
        for type_name, type_data in pack_data.get("types", {}).items():
            try:
                tpl = self.parse_type_template(type_data)
                errors = self.validate_type_template(tpl)
                if errors:
                    logger.warning("Skipping invalid type %s in pack %s: %s", type_name, pack_name, errors)
                    continue
                self._register_type(tpl)
                types_dict[type_name] = tpl
            except (ValueError, KeyError) as exc:
                logger.warning("Skipping unparseable type %s in pack %s: %s", type_name, pack_name, exc)

        # Register the pack itself
        pack = WorkflowPack(
            pack=pack_name,
            version=pack_data.get("version", "1.0"),
            display_name=pack_data.get("display_name", pack_name),
            description=pack_data.get("description", ""),
            types=types_dict,
            requires_packs=tuple(pack_data.get("requires_packs", [])),
            relationships=tuple(pack_data.get("relationships", [])),
            cross_pack_relationships=tuple(pack_data.get("cross_pack_relationships", [])),
            guide=pack_data.get("guide"),
        )
        self._register_pack(pack)
        logger.debug("Registered pack: %s (%d types)", pack_name, len(types_dict))
```

The method also requires adding `from pathlib import Path` at the top of `templates.py` (add alongside the existing imports).

### Step 4: Run tests and CI

```bash
uv run pytest tests/test_templates.py::TestTemplateLoading -v
```

Expected: All 9 tests pass.

```bash
make ci
```

Expected: All existing tests still pass. `load()` is a new method; nothing calls it yet.

### Step 5: Commit

```
feat(templates): three-layer template loading with enabled_packs filtering

- load() reads config.json for enabled_packs (default: core, planning)
- Layer 1: Built-in packs from templates_data.py
- Layer 2: Installed packs from .keel/packs/*.json
- Layer 3: Project-local overrides from .keel/templates/*.json
- Idempotent: second load() call is no-op
- Skips invalid JSON with warning log
- _load_pack_data() helper parses and registers pack + its types

Implements: WFT-FR-002, WFT-FR-003, WFT-FR-004, WFT-FR-005, WFT-FR-057, WFT-AR-009
```

### Definition of Done
- [ ] `load()` reads config and loads all three layers
- [ ] Only types from enabled packs are registered
- [ ] Layer 3 overrides Layer 1 (whole-document replacement)
- [ ] Invalid JSON files are skipped with warning
- [ ] Missing `enabled_packs` defaults to `["core", "planning"]`
- [ ] Idempotent (second call is no-op)
- [ ] Installed packs not in `enabled_packs` are skipped
- [ ] `make ci` passes clean

---

## Task 1.7: Schema Migration v4 to v5

Add the v5 migration: create `type_templates` and `packs` tables, back up old `templates` data, migrate old rows, seed built-ins, validate, drop old table. **This is a one-way door** — includes backup, error handling, and post-migration validation per review fix B2.

**Files:**
- Modify: `src/keel/core.py` (CURRENT_SCHEMA_VERSION, MIGRATIONS list, new migration function, SCHEMA_SQL)
- Test: `tests/test_migration_v5.py` (new file)

### Step 1: Write the failing tests

```python
# tests/test_migration_v5.py
"""Tests for v4 -> v5 schema migration (workflow templates tables)."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from keel.core import (
    BUILT_IN_TEMPLATES,
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    KeelDB,
)


def _create_v4_database(db_path: Path) -> sqlite3.Connection:
    """Create a v4-schema database with the old templates table and some data.

    Returns the connection (caller must close).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Create the v4 schema (issues + old templates table + indexes)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS issues (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'open',
            priority    INTEGER NOT NULL DEFAULT 2,
            type        TEXT NOT NULL DEFAULT 'task',
            parent_id   TEXT,
            assignee    TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            closed_at   TEXT,
            description TEXT DEFAULT '',
            notes       TEXT DEFAULT '',
            fields      TEXT DEFAULT '{}',
            CHECK (priority BETWEEN 0 AND 4)
        );

        CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
        CREATE INDEX IF NOT EXISTS idx_issues_type ON issues(type);
        CREATE INDEX IF NOT EXISTS idx_issues_parent ON issues(parent_id);
        CREATE INDEX IF NOT EXISTS idx_issues_priority ON issues(priority);

        CREATE TABLE IF NOT EXISTS dependencies (
            issue_id       TEXT NOT NULL REFERENCES issues(id),
            depends_on_id  TEXT NOT NULL REFERENCES issues(id),
            type           TEXT NOT NULL DEFAULT 'blocks',
            created_at     TEXT NOT NULL,
            PRIMARY KEY (issue_id, depends_on_id)
        );

        CREATE INDEX IF NOT EXISTS idx_deps_depends_on ON dependencies(depends_on_id);

        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id   TEXT NOT NULL REFERENCES issues(id),
            event_type TEXT NOT NULL,
            actor      TEXT DEFAULT '',
            old_value  TEXT,
            new_value  TEXT,
            comment    TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_issue ON events(issue_id);
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

        CREATE TABLE IF NOT EXISTS comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id   TEXT NOT NULL REFERENCES issues(id),
            author     TEXT DEFAULT '',
            text       TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS labels (
            issue_id TEXT NOT NULL REFERENCES issues(id),
            label    TEXT NOT NULL,
            PRIMARY KEY (issue_id, label)
        );

        CREATE TABLE IF NOT EXISTS templates (
            type         TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            description  TEXT DEFAULT '',
            fields_schema TEXT NOT NULL
        );

        -- v2: FTS
        CREATE VIRTUAL TABLE IF NOT EXISTS issues_fts USING fts5(
            title, description, content='issues', content_rowid='rowid'
        );

        -- v4: Performance indexes
        CREATE INDEX IF NOT EXISTS idx_issues_status_priority ON issues(status, priority, created_at);
        CREATE INDEX IF NOT EXISTS idx_deps_issue_depends ON dependencies(issue_id, depends_on_id);
        CREATE INDEX IF NOT EXISTS idx_events_issue_time ON events(issue_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_id, created_at);
    """)

    # Seed old templates (mimics what _seed_templates did in v4)
    old_templates = [
        ("task", "Task", "General-purpose work item", json.dumps([
            {"name": "context", "type": "text"},
            {"name": "done_definition", "type": "text"},
        ])),
        ("bug", "Bug Report", "Defects and regressions", json.dumps([
            {"name": "severity", "type": "enum", "options": ["critical", "major", "minor"]},
            {"name": "component", "type": "text"},
        ])),
        ("epic", "Epic", "Large body of work", json.dumps([
            {"name": "scope_summary", "type": "text"},
        ])),
    ]
    for t_type, t_name, t_desc, t_schema in old_templates:
        conn.execute(
            "INSERT OR IGNORE INTO templates (type, display_name, description, fields_schema) VALUES (?, ?, ?, ?)",
            (t_type, t_name, t_desc, t_schema),
        )

    # Insert some issues to verify they survive migration
    now = "2026-02-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test-aaa111", "Existing task", "open", 2, "task", now, now),
    )
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test-bbb222", "In-progress bug", "in_progress", 1, "bug", now, now),
    )
    conn.execute(
        "INSERT INTO issues (id, title, status, priority, type, created_at, updated_at, closed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("test-ccc333", "Closed epic", "closed", 3, "epic", now, now, now),
    )

    # Set to v4
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    return conn


class TestMigrationV5Fresh:
    """Test fresh database creation at v5."""

    def test_fresh_db_creates_v5_schema(self, tmp_path: Path) -> None:
        """A brand-new KeelDB should create v5 schema directly."""
        db = KeelDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()

        assert db.get_schema_version() == 5

        # type_templates table should exist
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='type_templates'"
        ).fetchone()
        assert row is not None

        # packs table should exist
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='packs'"
        ).fetchone()
        assert row is not None

        db.close()

    def test_fresh_db_has_builtin_packs(self, tmp_path: Path) -> None:
        """Fresh DB should have all 9 built-in packs seeded."""
        db = KeelDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()

        packs = db.conn.execute("SELECT name, is_builtin, enabled FROM packs ORDER BY name").fetchall()
        pack_names = [p["name"] for p in packs]

        # All 9 built-in packs should be present
        assert "core" in pack_names
        assert "planning" in pack_names
        assert "risk" in pack_names
        assert "spike" in pack_names
        assert len(pack_names) == 9

        # All should be marked as builtin
        for p in packs:
            assert p["is_builtin"] == 1

        # Only core and planning enabled by default
        enabled = {p["name"] for p in packs if p["enabled"]}
        assert "core" in enabled
        assert "planning" in enabled

        db.close()

    def test_fresh_db_has_builtin_type_templates(self, tmp_path: Path) -> None:
        """Fresh DB should have type templates from enabled packs."""
        db = KeelDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()

        templates = db.conn.execute(
            "SELECT type, pack, is_builtin FROM type_templates ORDER BY type"
        ).fetchall()
        type_names = [t["type"] for t in templates]

        # Core pack types
        assert "task" in type_names
        assert "bug" in type_names
        assert "feature" in type_names
        assert "epic" in type_names

        # Planning pack types
        assert "milestone" in type_names
        assert "phase" in type_names
        assert "step" in type_names

        # All should be builtin
        for t in templates:
            assert t["is_builtin"] == 1

        db.close()

    def test_fresh_db_old_templates_table_not_present(self, tmp_path: Path) -> None:
        """Fresh v5 DB should not have the old templates table."""
        db = KeelDB(tmp_path / "fresh.db", prefix="test")
        db.initialize()

        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='templates'"
        ).fetchone()
        # In a fresh DB, SCHEMA_SQL still creates 'templates' but then migration
        # replaces it with type_templates. OR: SCHEMA_SQL is updated to create
        # type_templates directly. The important thing: type_templates exists.
        # We check that type_templates works, not whether old templates was dropped.
        assert db.conn.execute("SELECT COUNT(*) FROM type_templates").fetchone()[0] >= 9

        db.close()


class TestMigrationV4ToV5:
    """Test upgrade from v4 to v5."""

    def test_v4_to_v5_upgrade_succeeds(self, tmp_path: Path) -> None:
        """v4 database should upgrade to v5 cleanly."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        assert db.get_schema_version() == 5
        db.close()

    def test_v4_to_v5_creates_new_tables(self, tmp_path: Path) -> None:
        """Migration creates type_templates and packs tables."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        # type_templates exists
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='type_templates'"
        ).fetchone()
        assert row is not None

        # packs exists
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='packs'"
        ).fetchone()
        assert row is not None

        db.close()

    def test_v4_to_v5_backup_created(self, tmp_path: Path) -> None:
        """Migration creates _templates_v4_backup table (review B2)."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_templates_v4_backup'"
        ).fetchone()
        assert row is not None

        # Backup should have the 3 old templates
        backup_count = db.conn.execute("SELECT COUNT(*) FROM _templates_v4_backup").fetchone()[0]
        assert backup_count == 3

        db.close()

    def test_v4_to_v5_old_templates_migrated(self, tmp_path: Path) -> None:
        """Old templates rows should appear in type_templates with enriched definitions."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        # task from old templates should be in type_templates
        row = db.conn.execute(
            "SELECT type, pack, definition FROM type_templates WHERE type = 'task'"
        ).fetchone()
        assert row is not None
        # Should have a definition with states
        defn = json.loads(row["definition"])
        assert "states" in defn
        assert "initial_state" in defn

        db.close()

    def test_v4_to_v5_builtin_packs_seeded(self, tmp_path: Path) -> None:
        """Migration seeds all 9 built-in packs."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        pack_count = db.conn.execute("SELECT COUNT(*) FROM packs WHERE is_builtin = 1").fetchone()[0]
        assert pack_count == 9

        db.close()

    def test_v4_to_v5_issues_untouched(self, tmp_path: Path) -> None:
        """Existing issues must survive migration completely untouched."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        # All 3 issues should exist with original data
        task = db.get_issue("test-aaa111")
        assert task.title == "Existing task"
        assert task.status == "open"
        assert task.type == "task"

        bug = db.get_issue("test-bbb222")
        assert bug.title == "In-progress bug"
        assert bug.status == "in_progress"

        epic = db.get_issue("test-ccc333")
        assert epic.title == "Closed epic"
        assert epic.status == "closed"

        db.close()

    def test_v4_to_v5_old_templates_table_dropped(self, tmp_path: Path) -> None:
        """Old templates table should be dropped after successful migration."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='templates'"
        ).fetchone()
        assert row is None  # Old table should be gone

        db.close()

    def test_v4_to_v5_post_migration_validation(self, tmp_path: Path) -> None:
        """Post-migration validation checks row counts."""
        db_path = tmp_path / "upgrade.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        # type_templates should have at least the built-in types from core + planning packs
        # Core: task, bug, feature, epic (4)
        # Planning: milestone, phase, step, work_package, deliverable (5)
        # = 9 minimum
        template_count = db.conn.execute("SELECT COUNT(*) FROM type_templates").fetchone()[0]
        assert template_count >= 9

        db.close()


class TestMigrationV5FailureRecovery:
    """Test migration failure behavior (review B2)."""

    def test_migration_failure_preserves_backup(self, tmp_path: Path) -> None:
        """If migration fails during seeding, backup table should survive."""
        db_path = tmp_path / "fail.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        # Monkeypatch BUILT_IN_PACKS to trigger an error during seeding
        with patch("keel.core._seed_builtin_packs_v5", side_effect=RuntimeError("Simulated seeding failure")):
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # Manually try to run the migration
            from keel.core import _migrate_v5_workflow_templates

            with pytest.raises(RuntimeError, match="Simulated seeding failure"):
                _migrate_v5_workflow_templates(conn)

            # Backup table should still exist with original data
            backup_count = conn.execute("SELECT COUNT(*) FROM _templates_v4_backup").fetchone()[0]
            assert backup_count == 3

            conn.close()

    def test_migration_failure_does_not_drop_old_table(self, tmp_path: Path) -> None:
        """On failure, old templates table should NOT be dropped."""
        db_path = tmp_path / "fail2.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        with patch("keel.core._seed_builtin_packs_v5", side_effect=RuntimeError("Boom")):
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            from keel.core import _migrate_v5_workflow_templates

            with pytest.raises(RuntimeError):
                _migrate_v5_workflow_templates(conn)

            # Old templates table should still exist
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='templates'"
            ).fetchone()
            assert row is not None

            # And still has its data
            count = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
            assert count == 3

            conn.close()


class TestMigrationV5CustomStates:
    """Test migration preserves custom template states (W8)."""

    def test_custom_template_states_in_backup(self, tmp_path: Path) -> None:
        """v4 databases with custom template states should preserve them in backup."""
        db_path = tmp_path / "custom.db"
        v4_conn = _create_v4_database(db_path)

        # Add a custom template type with non-standard fields
        custom_schema = json.dumps([
            {"name": "review_notes", "type": "text"},
            {"name": "approved_by", "type": "text"},
        ])
        v4_conn.execute(
            "INSERT INTO templates (type, display_name, description, fields_schema) VALUES (?, ?, ?, ?)",
            ("custom_review", "Custom Review", "A custom review type", custom_schema),
        )
        v4_conn.commit()
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        # Custom template should appear in backup
        backup_row = db.conn.execute(
            "SELECT * FROM _templates_v4_backup WHERE type = 'custom_review'"
        ).fetchone()
        assert backup_row is not None
        assert backup_row["display_name"] == "Custom Review"

        # Custom template should also appear in type_templates with a default
        # state machine (migrated with open/in_progress/closed)
        tpl_row = db.conn.execute(
            "SELECT definition FROM type_templates WHERE type = 'custom_review'"
        ).fetchone()
        assert tpl_row is not None
        defn = json.loads(tpl_row["definition"])
        assert "states" in defn
        # The custom fields should be preserved in the definition
        field_names = [f["name"] for f in defn.get("fields_schema", [])]
        assert "review_notes" in field_names
        assert "approved_by" in field_names

        db.close()

    def test_custom_template_assigned_to_custom_pack(self, tmp_path: Path) -> None:
        """Migrated custom templates should be assigned to 'custom' pack."""
        db_path = tmp_path / "custom2.db"
        v4_conn = _create_v4_database(db_path)

        custom_schema = json.dumps([{"name": "my_field", "type": "text"}])
        v4_conn.execute(
            "INSERT INTO templates (type, display_name, description, fields_schema) VALUES (?, ?, ?, ?)",
            ("my_type", "My Type", "Custom", custom_schema),
        )
        v4_conn.commit()
        v4_conn.close()

        db = KeelDB(db_path, prefix="test")
        db.initialize()

        row = db.conn.execute(
            "SELECT pack, is_builtin FROM type_templates WHERE type = 'my_type'"
        ).fetchone()
        assert row is not None
        assert row["pack"] == "custom"
        assert row["is_builtin"] == 0

        db.close()


class TestMigrationV5Logging:
    """Test migration logging levels (review B4)."""

    def test_migration_logs_at_info_level(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Migration should log start and completion at INFO level."""
        db_path = tmp_path / "log.db"
        v4_conn = _create_v4_database(db_path)
        v4_conn.close()

        with caplog.at_level(logging.DEBUG, logger="keel.core"):
            db = KeelDB(db_path, prefix="test")
            db.initialize()
            db.close()

        # INFO: start message
        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("v4" in m and "v5" in m for m in info_messages), f"Expected v4->v5 in INFO logs, got: {info_messages}"

        # DEBUG: step-level messages
        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("backup" in m.lower() for m in debug_messages), f"Expected 'backup' in DEBUG logs, got: {debug_messages}"
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_migration_v5.py -v
```

Expected failures:
- `CURRENT_SCHEMA_VERSION` is still 4, so comparisons fail.
- `_migrate_v5_workflow_templates` does not exist.
- `type_templates` and `packs` tables do not exist.

### Step 3: Implement

**Change 1: Add `import logging` near top of `src/keel/core.py`** (after existing imports, before `KEEL_DIR_NAME`):

```python
import logging

logger = logging.getLogger(__name__)
```

**Change 2: Update `SCHEMA_SQL` in `src/keel/core.py`** (add new tables after the existing `templates` table definition):

Add the new table DDL to `SCHEMA_SQL` so fresh databases get the v5 schema directly. Keep the old `templates` table in `SCHEMA_SQL` so the migration can find it when upgrading from v4. The migration will drop it.

```python
# Add after the existing templates table in SCHEMA_SQL (around line 133):

CREATE TABLE IF NOT EXISTS type_templates (
    type          TEXT PRIMARY KEY,
    pack          TEXT NOT NULL DEFAULT 'core',
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packs (
    name          TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    enabled       BOOLEAN NOT NULL DEFAULT 1
);
```

**Change 3: Update `CURRENT_SCHEMA_VERSION`** (line 136):

```python
CURRENT_SCHEMA_VERSION = 5
```

**Change 4: Add the migration function** (after `_migrate_v4_perf_indexes`, before `MIGRATIONS`):

```python
def _seed_builtin_packs_v5(conn: sqlite3.Connection, now: str) -> int:
    """Seed built-in packs and type templates into v5 tables.

    Separated from migration for testability (can be monkeypatched for failure tests).
    Returns the number of type templates seeded.
    """
    from keel.templates_data import BUILT_IN_PACKS

    count = 0
    default_enabled = {"core", "planning"}

    for pack_name, pack_data in BUILT_IN_PACKS.items():
        enabled = 1 if pack_name in default_enabled else 0
        conn.execute(
            "INSERT OR IGNORE INTO packs (name, version, definition, is_builtin, enabled) "
            "VALUES (?, ?, ?, 1, ?)",
            (pack_name, pack_data.get("version", "1.0"), json.dumps(pack_data), enabled),
        )
        logger.debug("Seeded pack: %s (enabled=%d)", pack_name, enabled)

        # Seed type templates from this pack
        for type_name, type_data in pack_data.get("types", {}).items():
            conn.execute(
                "INSERT OR IGNORE INTO type_templates (type, pack, definition, is_builtin, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (type_name, pack_name, json.dumps(type_data), now, now),
            )
            count += 1
            logger.debug("Seeded type template: %s (pack=%s)", type_name, pack_name)

    return count


def _migrate_v5_workflow_templates(conn: sqlite3.Connection) -> None:
    """v4 -> v5: Create type_templates and packs tables, migrate old templates data.

    Steps:
    1. Back up old templates table to _templates_v4_backup (review B2)
    2. Create type_templates and packs tables (IF NOT EXISTS for idempotency)
    3. Migrate old templates rows into type_templates with default 3-state machines
    4. Seed built-in packs and type templates
    5. Post-migration validation (row count check)
    6. Drop old templates table (only after validation passes)

    On failure: backup table is preserved, old templates table is NOT dropped,
    and the exception propagates to the caller.
    """
    logger.info("Starting v4 -> v5 migration: workflow templates")
    now = datetime.now(UTC).isoformat()

    # Step 1: Back up old templates table
    conn.execute("CREATE TABLE IF NOT EXISTS _templates_v4_backup AS SELECT * FROM templates")
    backup_count = conn.execute("SELECT COUNT(*) FROM _templates_v4_backup").fetchone()[0]
    logger.debug("Backed up %d templates to _templates_v4_backup", backup_count)

    # Step 2: Create new tables (IF NOT EXISTS for safe re-run after partial failure)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS type_templates (
            type          TEXT PRIMARY KEY,
            pack          TEXT NOT NULL DEFAULT 'core',
            definition    TEXT NOT NULL,
            is_builtin    BOOLEAN NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS packs (
            name          TEXT PRIMARY KEY,
            version       TEXT NOT NULL,
            definition    TEXT NOT NULL,
            is_builtin    BOOLEAN NOT NULL DEFAULT 0,
            enabled       BOOLEAN NOT NULL DEFAULT 1
        )
    """)
    logger.debug("Created type_templates and packs tables")

    # Step 3: Migrate old templates rows with default 3-state definitions
    try:
        old_templates = conn.execute(
            "SELECT type, display_name, description, fields_schema FROM templates"
        ).fetchall()

        for row in old_templates:
            old_type = row[0]
            old_display_name = row[1]
            old_description = row[2]
            old_fields_raw = row[3]

            try:
                old_fields = json.loads(old_fields_raw) if old_fields_raw else []
            except (json.JSONDecodeError, TypeError):
                old_fields = []

            # Enrich with default 3-state machine (open/in_progress/closed)
            enriched_definition = {
                "type": old_type,
                "display_name": old_display_name,
                "description": old_description or "",
                "pack": "custom",
                "states": [
                    {"name": "open", "category": "open"},
                    {"name": "in_progress", "category": "wip"},
                    {"name": "closed", "category": "done"},
                ],
                "initial_state": "open",
                "transitions": [
                    {"from": "open", "to": "in_progress", "enforcement": "soft"},
                    {"from": "in_progress", "to": "closed", "enforcement": "soft"},
                    {"from": "open", "to": "closed", "enforcement": "soft"},
                ],
                "fields_schema": old_fields,
            }

            conn.execute(
                "INSERT OR IGNORE INTO type_templates (type, pack, definition, is_builtin, created_at, updated_at) "
                "VALUES (?, 'custom', ?, 0, ?, ?)",
                (old_type, json.dumps(enriched_definition), now, now),
            )
            logger.debug("Migrated old template: %s", old_type)

        logger.debug("Migrated %d old templates to type_templates", len(old_templates))

        # Step 4: Seed built-in packs and type templates (overwrites custom for built-in types)
        seed_count = _seed_builtin_packs_v5(conn, now)
        logger.debug("Seeded %d built-in type templates", seed_count)

    except Exception:
        logger.error(
            "v4 -> v5 migration failed during data migration/seeding. "
            "Backup preserved in _templates_v4_backup table. Old templates table NOT dropped."
        )
        raise

    # Step 5: Post-migration validation
    type_count = conn.execute("SELECT COUNT(*) FROM type_templates").fetchone()[0]
    pack_count = conn.execute("SELECT COUNT(*) FROM packs").fetchone()[0]

    # Must have at least 9 built-in types (4 core + 5 planning) and 9 packs
    min_types = 9
    min_packs = 9
    if type_count < min_types:
        msg = f"Migration validation failed: expected >= {min_types} type_templates rows, got {type_count}"
        logger.error(msg)
        raise RuntimeError(msg)
    if pack_count < min_packs:
        msg = f"Migration validation failed: expected >= {min_packs} packs rows, got {pack_count}"
        logger.error(msg)
        raise RuntimeError(msg)

    logger.debug("Post-migration validation passed: %d types, %d packs", type_count, pack_count)

    # Step 6: Drop old templates table (only after validation passes)
    conn.execute("DROP TABLE IF EXISTS templates")
    logger.info(
        "v4 -> v5 migration complete: %d type_templates, %d packs, backup in _templates_v4_backup",
        type_count, pack_count,
    )
```

**Change 5: Update `MIGRATIONS` list** (line 255):

```python
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (2, _migrate_v2_fts5),
    (3, _migrate_v3_custom_workflow),
    (4, _migrate_v4_perf_indexes),
    (5, _migrate_v5_workflow_templates),
]
```

### Step 4: Run tests and CI

```bash
uv run pytest tests/test_migration_v5.py -v
```

Expected: All tests pass. Check specific counts:
- `TestMigrationV5Fresh`: 4 tests pass (fresh DB at v5 with all tables)
- `TestMigrationV4ToV5`: 8 tests pass (upgrade path with data preservation)
- `TestMigrationV5FailureRecovery`: 2 tests pass (monkeypatched failure, backup preserved)
- `TestMigrationV5CustomStates`: 2 tests pass (custom template fields preserved)
- `TestMigrationV5Logging`: 1 test passes (INFO + DEBUG log checks)

```bash
make ci
```

Expected: All existing tests still pass. The key verification: existing tests in `test_migrate.py` (beads migration) and the `db` fixture in `conftest.py` work because:
- `KeelDB.initialize()` now runs migration to v5
- The old `templates` table is still created by `SCHEMA_SQL` (for CREATE IF NOT EXISTS), then the migration drops it
- `_seed_templates()` still inserts into the old `templates` table (updated in Task 1.13)
- But since we are now at v5, the migration creates `type_templates` and drops `templates`

**Important sequencing note:** The `_seed_templates()` method currently inserts into `templates`, which the migration drops. This means `_seed_templates()` will fail after migration. Task 1.13 must fix this. To keep things working in the interim, the v5 migration should handle the case where `_seed_templates` is called but the `templates` table no longer exists.

**Interim fix:** Add a guard in `_seed_templates()` that checks whether the `templates` table exists before inserting:

```python
def _seed_templates(self) -> None:
    """Seed built-in templates. Handles both v4 (templates table) and v5+ (type_templates)."""
    # Check if old templates table still exists (v4) or has been replaced (v5+)
    has_old_table = self.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='templates'"
    ).fetchone()
    if has_old_table:
        for tpl in BUILT_IN_TEMPLATES:
            self.conn.execute(
                "INSERT OR IGNORE INTO templates (type, display_name, description, fields_schema) VALUES (?, ?, ?, ?)",
                (tpl["type"], tpl["display_name"], tpl["description"], json.dumps(tpl["fields_schema"])),
            )
    # v5+ seeding is handled by the migration itself (_seed_builtin_packs_v5)
```

This interim approach keeps the `db` fixture and all existing tests working. Task 1.13 replaces this entirely.

### Step 5: Commit

```
feat(core): schema migration v4 -> v5 for workflow templates

- Backs up old templates table to _templates_v4_backup before migration
- Creates type_templates and packs tables (design Section 6.1)
- Migrates old template rows with enriched 3-state definitions
- Assigns custom templates to "custom" pack with is_builtin=0
- Seeds 9 built-in packs (core+planning enabled by default)
- Seeds 9+ built-in type templates from templates_data
- Post-migration validation: verifies minimum row counts
- Error recovery: backup table preserved on failure, old table NOT dropped
- Drops old templates table only after validation passes
- CURRENT_SCHEMA_VERSION bumped to 5
- Logging at INFO (start/complete), DEBUG (steps), ERROR (failures)
- Interim _seed_templates() guard for v4/v5 coexistence

Implements: WFT-FR-051 through WFT-FR-058, WFT-NFR-006
```

### Definition of Done
- [ ] Fresh DB creates v5 schema directly with type_templates and packs tables
- [ ] v4 DB upgrades to v5 with all data preserved
- [ ] Old templates backed up to `_templates_v4_backup` before drop
- [ ] Old templates migrated to type_templates with enriched default state machines
- [ ] Custom template fields_schema preserved in migration
- [ ] Custom templates assigned to "custom" pack with is_builtin=0
- [ ] 9 built-in packs seeded (core and planning enabled)
- [ ] 9+ built-in type templates seeded from templates_data
- [ ] Post-migration validation checks row counts
- [ ] Migration failure preserves backup and raises clear error
- [ ] Migration failure does NOT drop old templates table
- [ ] Old templates table dropped only after validation passes
- [ ] Existing issues completely untouched by migration
- [ ] Migration logs at INFO/DEBUG/ERROR levels
- [ ] All existing tests still pass (no behavior changes)
- [ ] `make ci` passes clean

---

## Task 1.13: Update _seed_templates() and Template-Related Methods

Update `_seed_templates()` to seed from `templates_data.BUILT_IN_PACKS` into the new `type_templates` table. Update `get_template()` and `list_templates()` to read from `type_templates`. Remove the old `BUILT_IN_TEMPLATES` list.

**Files:**
- Modify: `src/keel/core.py` (lines 265-565: `BUILT_IN_TEMPLATES`, `_seed_templates()`, `get_template()`, `list_templates()`)
- Test: `tests/test_seed_templates.py` (new file)

**Why this order:** After Task 1.7, the `type_templates` and `packs` tables exist. Now we update the methods that read/write them. This task must run before Task 1.8 because the lazy property depends on `list_templates()` working correctly.

### Step 1: Write the failing tests

```python
# tests/test_seed_templates.py
"""Tests for updated _seed_templates() and template query methods."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keel.core import KeelDB


class TestSeedTemplatesV5:
    """Test _seed_templates() writes to type_templates table."""

    def test_seed_creates_type_templates(self, tmp_path: Path) -> None:
        """After initialize(), type_templates should be populated."""
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()

        count = db.conn.execute("SELECT COUNT(*) FROM type_templates").fetchone()[0]
        assert count >= 9  # 4 core + 5 planning at minimum

        db.close()

    def test_seed_idempotent(self, tmp_path: Path) -> None:
        """Calling initialize() twice should not duplicate templates."""
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()
        count_1 = db.conn.execute("SELECT COUNT(*) FROM type_templates").fetchone()[0]

        # Re-seed (simulates restart)
        db._seed_templates()
        db.conn.commit()
        count_2 = db.conn.execute("SELECT COUNT(*) FROM type_templates").fetchone()[0]

        assert count_1 == count_2
        db.close()

    def test_seed_marks_builtin(self, tmp_path: Path) -> None:
        """Built-in templates should have is_builtin=1."""
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()

        builtins = db.conn.execute(
            "SELECT COUNT(*) FROM type_templates WHERE is_builtin = 1"
        ).fetchone()[0]
        assert builtins >= 9

        db.close()

    def test_seed_all_packs_present(self, tmp_path: Path) -> None:
        """All 9 built-in packs should be in the packs table."""
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()

        packs = db.conn.execute("SELECT name FROM packs ORDER BY name").fetchall()
        pack_names = sorted([p["name"] for p in packs])
        expected = sorted(["core", "planning", "requirements", "risk", "roadmap", "incident", "debt", "spike", "release"])
        assert pack_names == expected

        db.close()


class TestGetTemplateV5:
    """Test get_template() reads from type_templates."""

    def test_get_template_returns_enriched_data(self, tmp_path: Path) -> None:
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()

        tpl = db.get_template("bug")
        assert tpl is not None
        assert tpl["type"] == "bug"
        assert tpl["display_name"] == "Bug Report"
        # Should have full definition including states
        assert "states" in tpl
        assert "transitions" in tpl
        assert "fields_schema" in tpl

        db.close()

    def test_get_template_not_found(self, tmp_path: Path) -> None:
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()

        tpl = db.get_template("nonexistent")
        assert tpl is None

        db.close()

    def test_get_template_has_pack_info(self, tmp_path: Path) -> None:
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()

        tpl = db.get_template("task")
        assert tpl is not None
        assert tpl["pack"] == "core"

        tpl = db.get_template("milestone")
        assert tpl is not None
        assert tpl["pack"] == "planning"

        db.close()


class TestListTemplatesV5:
    """Test list_templates() reads from type_templates."""

    def test_list_templates_returns_all(self, tmp_path: Path) -> None:
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()

        templates = db.list_templates()
        type_names = [t["type"] for t in templates]

        assert "task" in type_names
        assert "bug" in type_names
        assert "milestone" in type_names
        assert len(templates) >= 9

        db.close()

    def test_list_templates_sorted_by_type(self, tmp_path: Path) -> None:
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()

        templates = db.list_templates()
        type_names = [t["type"] for t in templates]
        assert type_names == sorted(type_names)

        db.close()

    def test_list_templates_includes_definition_fields(self, tmp_path: Path) -> None:
        """Each template should have states and transitions in the result."""
        db = KeelDB(tmp_path / "test.db", prefix="test")
        db.initialize()

        templates = db.list_templates()
        for tpl in templates:
            assert "type" in tpl
            assert "display_name" in tpl
            assert "description" in tpl
            assert "pack" in tpl
            assert "fields_schema" in tpl

        db.close()
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_seed_templates.py -v
```

Expected failures: `get_template()` still reads from old `templates` table (which has been dropped by v5 migration), and the return format does not include `states`/`transitions`.

### Step 3: Implement

**Change 1: Remove `BUILT_IN_TEMPLATES` list** (lines 265-377 in `src/keel/core.py`)

Delete the entire `BUILT_IN_TEMPLATES` list. It is replaced by `templates_data.BUILT_IN_PACKS`.

**Change 2: Update `_seed_templates()`**

```python
def _seed_templates(self) -> None:
    """Seed built-in packs and type templates into v5 tables.

    Uses INSERT OR IGNORE for idempotency — safe to call on every initialize().
    """
    from keel.templates_data import BUILT_IN_PACKS

    now = _now_iso()
    default_enabled = {"core", "planning"}

    for pack_name, pack_data in BUILT_IN_PACKS.items():
        enabled = 1 if pack_name in default_enabled else 0
        self.conn.execute(
            "INSERT OR IGNORE INTO packs (name, version, definition, is_builtin, enabled) "
            "VALUES (?, ?, ?, 1, ?)",
            (pack_name, pack_data.get("version", "1.0"), json.dumps(pack_data), enabled),
        )

        for type_name, type_data in pack_data.get("types", {}).items():
            self.conn.execute(
                "INSERT OR IGNORE INTO type_templates (type, pack, definition, is_builtin, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (type_name, pack_name, json.dumps(type_data), now, now),
            )
```

**Change 3: Update `get_template()`**

```python
def get_template(self, issue_type: str) -> dict[str, Any] | None:
    """Get a type template by type name. Returns enriched definition from type_templates."""
    row = self.conn.execute(
        "SELECT type, pack, definition, is_builtin FROM type_templates WHERE type = ?",
        (issue_type,),
    ).fetchone()
    if row is None:
        return None
    defn: dict[str, Any] = json.loads(row["definition"])
    return {
        "type": row["type"],
        "pack": row["pack"],
        "display_name": defn.get("display_name", row["type"]),
        "description": defn.get("description", ""),
        "states": defn.get("states", []),
        "transitions": defn.get("transitions", []),
        "fields_schema": defn.get("fields_schema", []),
        "initial_state": defn.get("initial_state", "open"),
        "is_builtin": bool(row["is_builtin"]),
    }
```

**Change 4: Update `list_templates()`**

```python
def list_templates(self) -> list[dict[str, Any]]:
    """List all type templates, ordered by type name."""
    rows = self.conn.execute(
        "SELECT type, pack, definition, is_builtin FROM type_templates ORDER BY type"
    ).fetchall()
    result: list[dict[str, Any]] = []
    for r in rows:
        defn: dict[str, Any] = json.loads(r["definition"])
        result.append({
            "type": r["type"],
            "pack": r["pack"],
            "display_name": defn.get("display_name", r["type"]),
            "description": defn.get("description", ""),
            "states": defn.get("states", []),
            "transitions": defn.get("transitions", []),
            "fields_schema": defn.get("fields_schema", []),
            "initial_state": defn.get("initial_state", "open"),
            "is_builtin": bool(r["is_builtin"]),
        })
    return result
```

**Change 5: Remove old `templates` table from `SCHEMA_SQL`**

Since v5 is now the current version, update `SCHEMA_SQL` to use `type_templates` and `packs` instead of the old `templates` table. This way fresh databases never create the old table at all.

Replace:
```sql
CREATE TABLE IF NOT EXISTS templates (
    type         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description  TEXT DEFAULT '',
    fields_schema TEXT NOT NULL
);
```

With:
```sql
CREATE TABLE IF NOT EXISTS type_templates (
    type          TEXT PRIMARY KEY,
    pack          TEXT NOT NULL DEFAULT 'core',
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packs (
    name          TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    definition    TEXT NOT NULL,
    is_builtin    BOOLEAN NOT NULL DEFAULT 0,
    enabled       BOOLEAN NOT NULL DEFAULT 1
);
```

**Important:** The v5 migration function (`_migrate_v5_workflow_templates`) still references `templates` via `SELECT * FROM templates`. For v4 upgrades this is fine (the table exists). For fresh v5 databases, the migration is skipped entirely because `user_version` starts at 0 and gets set to 5 after all migrations run. Actually, fresh databases run ALL migrations, but since the `templates` table no longer exists in `SCHEMA_SQL`, the v5 migration would fail on fresh DBs.

**Solution:** The v5 migration must handle the case where the old `templates` table does not exist (fresh v5 DB). Add a guard:

```python
def _migrate_v5_workflow_templates(conn: sqlite3.Connection) -> None:
    # ... existing code ...

    # Check if old templates table exists (absent on fresh v5 databases)
    has_old_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='templates'"
    ).fetchone()

    if has_old_table:
        # Step 1: Back up old templates table
        conn.execute("CREATE TABLE IF NOT EXISTS _templates_v4_backup AS SELECT * FROM templates")
        # ... rest of migration for old templates ...
    else:
        logger.debug("No old templates table found — fresh database, skipping backup/migration")

    # Step 2: Create new tables (IF NOT EXISTS — may already exist from SCHEMA_SQL)
    # ... create type_templates and packs ...

    # Steps 3-4: seed (always runs)
    # ...

    # Step 6: Drop old templates table (if it existed)
    if has_old_table:
        conn.execute("DROP TABLE IF EXISTS templates")
    # ...
```

### Step 4: Run tests and CI

```bash
uv run pytest tests/test_seed_templates.py -v
```

Expected: All 11 tests pass.

```bash
make ci
```

Expected: All existing tests pass. Key verification points:

- `conftest.py` `db` fixture calls `KeelDB.initialize()` which now seeds `type_templates` instead of `templates`.
- `test_migrate.py` still works because it tests beads migration, not schema migration.
- Any test that calls `db.get_template()` or `db.list_templates()` now gets the enriched format from `type_templates`. If existing tests check the return format, they may need attention.

**Check for existing tests that use `get_template()` or `list_templates()`:**

Search the test files for these method calls. If existing tests expect the old format (e.g., `tpl["fields_schema"]` as a list), the new format is backward-compatible because `fields_schema` is still a list in the definition JSON. The new format adds `states`, `transitions`, `pack`, `is_builtin` fields that old tests would not check for.

If any existing test does `assert tpl.keys() == {...}` or similar strict shape checking, it may need an update. But since we are not supposed to have behavior changes, and the return type is `dict[str, Any]`, adding new keys should not break callers.

### Step 5: Commit

```
refactor(core): update template methods for new type_templates table

- _seed_templates() now seeds from templates_data.BUILT_IN_PACKS
- _seed_templates() writes to type_templates and packs tables
- get_template() reads from type_templates with enriched definition
- list_templates() reads from type_templates with enriched definition
- SCHEMA_SQL updated: type_templates + packs replace old templates table
- Remove old BUILT_IN_TEMPLATES list (replaced by templates_data.py)
- Return format is backward-compatible: existing fields preserved, new fields added
- Migration handles both fresh (no old table) and upgrade (old table exists) paths

Implements: WFT-FR-054, WFT-FR-066
```

### Definition of Done
- [ ] `BUILT_IN_TEMPLATES` list removed from core.py
- [ ] `_seed_templates()` writes to `type_templates` and `packs` tables
- [ ] `_seed_templates()` is idempotent (INSERT OR IGNORE)
- [ ] All 9 packs seeded (core+planning enabled, others disabled)
- [ ] All 9+ type templates seeded with is_builtin=1
- [ ] `get_template()` returns enriched definition from `type_templates`
- [ ] `list_templates()` returns all templates sorted by type
- [ ] Return format includes `pack`, `states`, `transitions`, `is_builtin`
- [ ] `SCHEMA_SQL` uses `type_templates` + `packs` (no old `templates` table for fresh DBs)
- [ ] Migration handles both fresh and upgrade paths
- [ ] All existing tests still pass (no behavior changes)
- [ ] `make ci` passes clean

---

## Task 1.8: KeelDB Integration — Lazy TemplateRegistry

Wire `TemplateRegistry` into `KeelDB` as a lazy property, resolving the circular dependency (WFT-AR-001).

**Files:**
- Modify: `src/keel/core.py` (KeelDB class: `__init__`, new `templates` property)
- Test: `tests/test_keeldb_templates.py` (new file)

**Why last:** The lazy property calls `TemplateRegistry.load()`, which reads from the `type_templates` table and `config.json`. Both must be working before this task.

### Step 1: Write the failing tests

```python
# tests/test_keeldb_templates.py
"""Tests for KeelDB.templates lazy property integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keel.core import KeelDB, write_config


class TestKeelDBTemplatesProperty:
    """Test lazy TemplateRegistry property on KeelDB."""

    def test_templates_property_returns_registry(self, tmp_path: Path) -> None:
        """db.templates should return a TemplateRegistry instance."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]})

        db = KeelDB(keel_dir / "keel.db", prefix="test")
        db.initialize()

        from keel.templates import TemplateRegistry

        assert isinstance(db.templates, TemplateRegistry)
        db.close()

    def test_templates_property_lazy(self, tmp_path: Path) -> None:
        """Registry should not be created until first access."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {"prefix": "test", "version": 1})

        db = KeelDB(keel_dir / "keel.db", prefix="test")
        db.initialize()

        # Before first access, internal attribute should be None
        assert db._template_registry is None

        # First access creates the registry
        reg = db.templates
        assert reg is not None
        assert db._template_registry is reg

        # Second access returns same instance (cached)
        assert db.templates is reg

        db.close()

    def test_templates_property_has_types(self, tmp_path: Path) -> None:
        """Loaded registry should have types from enabled packs."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {"prefix": "test", "version": 1, "enabled_packs": ["core", "planning"]})

        db = KeelDB(keel_dir / "keel.db", prefix="test")
        db.initialize()

        reg = db.templates
        assert reg.get_type("task") is not None
        assert reg.get_type("bug") is not None
        assert reg.get_type("milestone") is not None

        db.close()

    def test_templates_injectable(self, tmp_path: Path) -> None:
        """TemplateRegistry should be injectable via constructor for testing."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {"prefix": "test", "version": 1})

        from keel.templates import TemplateRegistry

        custom_reg = TemplateRegistry()
        db = KeelDB(keel_dir / "keel.db", prefix="test", template_registry=custom_reg)
        db.initialize()

        # Should use the injected registry, not create a new one
        assert db.templates is custom_reg
        # Injected registry is empty (no load called), so no types
        assert db.templates.get_type("task") is None

        db.close()

    def test_templates_no_circular_import(self) -> None:
        """Importing core should not import templates at module load time."""
        import sys

        # If keel.templates is already imported, remove it temporarily
        had_templates = "keel.templates" in sys.modules
        if had_templates:
            saved = sys.modules.pop("keel.templates")

        try:
            # Force re-import of core
            if "keel.core" in sys.modules:
                # Just verify that core can be imported without templates
                # The TYPE_CHECKING guard means the import is deferred
                import keel.core

                assert hasattr(keel.core.KeelDB, "templates")
        finally:
            if had_templates:
                sys.modules["keel.templates"] = saved

    def test_templates_property_uses_keel_dir(self, tmp_path: Path) -> None:
        """The registry should load from the correct .keel directory."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {"prefix": "test", "version": 1, "enabled_packs": ["core"]})

        db = KeelDB(keel_dir / "keel.db", prefix="test")
        db.initialize()

        reg = db.templates
        # Only core enabled, so planning types should not be loaded
        assert reg.get_type("task") is not None
        # milestone is in planning pack — should NOT be available if only core enabled
        assert reg.get_type("milestone") is None

        db.close()

    def test_templates_with_from_project(self, tmp_path: Path) -> None:
        """KeelDB.from_project() should have working templates property."""
        keel_dir = tmp_path / ".keel"
        keel_dir.mkdir()
        write_config(keel_dir, {
            "prefix": "proj",
            "version": 1,
            "enabled_packs": ["core", "planning"],
        })

        init_db = KeelDB(keel_dir / "keel.db", prefix="proj")
        init_db.initialize()
        init_db.close()

        db = KeelDB.from_project(tmp_path)
        assert db.templates.get_type("task") is not None
        assert db.templates.get_type("milestone") is not None
        db.close()
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_keeldb_templates.py -v
```

Expected failures:
- `AttributeError: 'KeelDB' object has no attribute 'templates'`
- `AttributeError: 'KeelDB' object has no attribute '_template_registry'`

### Step 3: Implement

**Change 1: Add TYPE_CHECKING import guard** at the top of `src/keel/core.py` (after `from typing import Any`):

```python
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from keel.templates import TemplateRegistry
```

**Change 2: Update `KeelDB.__init__()`** to accept optional `template_registry`:

```python
def __init__(
    self,
    db_path: str | Path,
    *,
    prefix: str = "keel",
    workflow_states: list[str] | None = None,
    enabled_packs: list[str] | None = None,
    template_registry: TemplateRegistry | None = None,
) -> None:
    self.db_path = Path(db_path)
    self.prefix = prefix
    self.workflow_states = workflow_states or DEFAULT_WORKFLOW_STATES
    self.enabled_packs = enabled_packs if enabled_packs is not None else ["core", "planning"]
    self._conn: sqlite3.Connection | None = None
    self._template_registry: TemplateRegistry | None = template_registry
```

**Change 3: Add `templates` property** to `KeelDB` class (after `close()`, before the `-- Templates` section):

```python
@property
def templates(self) -> TemplateRegistry:
    """Lazy-loaded TemplateRegistry.

    Created on first access. Uses runtime import to avoid circular dependency
    (WFT-AR-001). The registry loads from the .keel directory that contains
    the database file.

    Can be overridden via constructor injection for testing.
    """
    if self._template_registry is None:
        from keel.templates import TemplateRegistry

        self._template_registry = TemplateRegistry()
        keel_dir = self.db_path.parent
        self._template_registry.load(keel_dir)
    return self._template_registry
```

**Note on the TYPE_CHECKING pattern:** The `from typing import TYPE_CHECKING` is already present via `from __future__ import annotations` (PEP 604 style). However, the explicit `TYPE_CHECKING` guard is needed for the runtime import deferral. At module load time, `keel.templates` is NOT imported. The `TemplateRegistry` type hint in the `__init__` signature is safe because `from __future__ import annotations` makes all annotations strings (PEP 563). The runtime import happens only inside the property body.

### Step 4: Run tests and CI

```bash
uv run pytest tests/test_keeldb_templates.py -v
```

Expected: All 7 tests pass.

```bash
make ci
```

Expected: All existing tests pass. The `templates` property is never accessed by existing code paths (it is opt-in via `db.templates`). The `_template_registry` attribute defaults to `None`, which has no effect on existing behavior. The `template_registry` parameter is optional with default `None`.

### Step 5: Commit

```
feat(core): lazy TemplateRegistry property on KeelDB

- KeelDB.templates property with lazy initialization on first access
- Runtime import inside property body to avoid circular dependency
- TYPE_CHECKING guard for type hints at module level
- Optional template_registry constructor parameter for DI (testing)
- load() called with the .keel directory containing the database
- Second access returns cached instance (same object)
- No existing behavior changes — property is not called by existing code

Implements: WFT-AR-001, WFT-NFR-012
```

### Definition of Done
- [ ] `db.templates` returns a `TemplateRegistry` instance
- [ ] Registry is created lazily on first access
- [ ] Second access returns the same cached instance
- [ ] No circular import at module load time
- [ ] `TYPE_CHECKING` guard for type hints
- [ ] Runtime import inside property body only
- [ ] `template_registry` parameter injectable via constructor
- [ ] Injected registry is used as-is (no `load()` called)
- [ ] Registry loads from the correct `.keel` directory
- [ ] `enabled_packs` in config controls which types are available
- [ ] Works with `from_project()` class method
- [ ] All existing tests still pass (no behavior changes)
- [ ] `make ci` passes clean
- [ ] Coverage >= 90% on new code

---

## Phase 1B Integration Test

After all 5 tasks are implemented, run a final integration check:

### Full CI

```bash
make ci
```

Expected: All tests pass, ruff clean, mypy clean.

### Coverage Check

```bash
uv run pytest --cov=keel --cov-report=term-missing tests/
```

Expected: New code in `core.py` (migration function, seed update, lazy property, config changes) at >= 90% coverage. New test files (`test_migration_v5.py`, `test_seed_templates.py`, `test_keeldb_templates.py`, `test_config_packs.py`) provide targeted coverage.

### Behavioral Verification

After Phase 1B, verify these invariants:

1. **`create_issue()` still hardcodes `'open'`** — the template system does not change issue creation.
2. **`_validate_status()` still checks `self.workflow_states`** — the template system does not change status validation.
3. **`claim_issue()` still hardcodes `'in_progress'`** — the template system does not change claiming.
4. **`close_issue()` still hardcodes `'closed'`** — the template system does not change closing.
5. **`is_ready` still checks `status == "open"`** — category-aware queries come in Phase 1C.
6. **The `templates` property exists but is never called by any existing code path** — it is wired in but dormant.

These invariants are verified by the fact that all existing tests pass without modification.

---

## Definition of Done (Phase 1B)

- [ ] v5 schema migration works (fresh + upgrade from v4)
- [ ] Migration backup and recovery tested
- [ ] Custom template states preserved in backup and migration (W8)
- [ ] KeelDB.templates returns a loaded TemplateRegistry
- [ ] _seed_templates() seeds from new pack data
- [ ] Config backward compatible (missing enabled_packs defaults to core+planning)
- [ ] Old BUILT_IN_TEMPLATES removed
- [ ] get_template() and list_templates() read from type_templates
- [ ] All existing tests still pass (no behavior changes)
- [ ] `make ci` passes clean
- [ ] Coverage >= 90% on new code
- [ ] Migration logs at INFO/DEBUG/ERROR levels
- [ ] No circular imports at module load time

---

## File Change Summary

| File | Change Type | What Changes |
|------|-------------|--------------|
| `src/keel/core.py` | Modify | Add `logging`, `TYPE_CHECKING` import; update `read_config()` with `enabled_packs` default; update `SCHEMA_SQL` (type_templates + packs); bump `CURRENT_SCHEMA_VERSION` to 5; add `_seed_builtin_packs_v5()` and `_migrate_v5_workflow_templates()`; update `MIGRATIONS` list; update `KeelDB.__init__()` (enabled_packs, template_registry params); add `KeelDB.templates` property; update `_seed_templates()`, `get_template()`, `list_templates()`; remove `BUILT_IN_TEMPLATES` |
| `src/keel/templates.py` | Modify | Add `load()` method and `_load_pack_data()` helper to `TemplateRegistry` |
| `tests/test_config_packs.py` | Create | Config enabled_packs tests (6 tests) |
| `tests/test_migration_v5.py` | Create | v5 migration tests: fresh, upgrade, failure recovery, custom states, logging (17 tests) |
| `tests/test_seed_templates.py` | Create | Seed and query tests for type_templates table (11 tests) |
| `tests/test_keeldb_templates.py` | Create | Lazy property integration tests (7 tests) |

**Total new tests:** ~41
**Files modified:** 2 (core.py, templates.py)
**Files created:** 4 (test files only)
**Files deleted:** 0
