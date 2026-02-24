# CLI Commands Split Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split 1,974-line `cli.py` (48 commands + 2 groups) into 6 domain-focused modules under `cli_commands/`, leaving `cli.py` as a thin shell (~30 lines).

**Architecture:** Each module defines standalone Click commands (using `@click.command()`) and exports a `register(cli)` function that attaches them to the main group. This mirrors the MCP tools split pattern (`mcp_tools/*.py`). Shared helpers already exist in `cli_common.py` (created during Task 0 of the module-architecture epic).

**Tech Stack:** Python 3.12, Click, `cli_common.py` for `get_db()`/`refresh_summary()`

**Parent epic:** `filigree-3045c2` (Module Architecture), Task 9 (`filigree-e91ec9`)

---

## Conventions

**Every module** follows this template:

```python
"""CLI commands for <domain>."""

from __future__ import annotations

import json as json_mod
import sys
# ... other imports as needed

import click

from filigree.cli_common import get_db, refresh_summary


# --- commands (standalone, using @click.command not @cli.command) ---

@click.command()
def example() -> None:
    ...


# --- registration ---

def register(cli: click.Group) -> None:
    """Attach <domain> commands to the CLI group."""
    cli.add_command(example)
```

**Key differences from the current `cli.py`:**
- `@click.command()` instead of `@cli.command()` (standalone commands)
- `get_db()` and `refresh_summary()` imported directly from `cli_common` (no underscore prefix wrappers)
- `with get_db() as db:` context manager pattern (same as current code — `_get_db()` was just a wrapper)
- `@click.group()` for `templates` and `server` subgroups (attached via `register()`)
- Named commands use the `name` parameter: `@click.command("add-dep")`, `@click.command("list")`

---

## Task 1: Create `cli_commands/__init__.py`

**Files:**
- Create: `src/filigree/cli_commands/__init__.py`

**Step 1: Create the package**

```python
"""CLI command modules grouped by domain."""
```

One-line docstring, matching `mcp_tools/__init__.py`.

**Step 2: Verify import**

Run: `uv run python -c "import filigree.cli_commands"`
Expected: No error

---

## Task 2: Create `cli_commands/issues.py`

**Files:**
- Create: `src/filigree/cli_commands/issues.py`

**Commands to move** (lines from `cli.py`):
- `create` (L135-206) — `@click.command()`
- `show` (L208-253) — `@click.command()`
- `list_issues` (L256-298) — `@click.command("list")`
- `update` (L301-375) — `@click.command()`
- `close` (L378-412) — `@click.command()`
- `reopen` (L415-444) — `@click.command()`
- `claim` (L1427-1454) — `@click.command()`
- `claim_next` (L1457-1498) — `@click.command("claim-next")`
- `release` (L1060-1075) — `@click.command("release")`
- `undo` (L1156-1182) — `@click.command()`

**Imports needed:**
```python
from __future__ import annotations

import json as json_mod
import sys

import click

from filigree.cli_common import get_db, refresh_summary
```

**Step 1: Create file with all 10 commands**

Copy each command function verbatim from `cli.py`, changing only:
- `@cli.command(...)` → `@click.command(...)` (for simple names like `create`, `show`, `update`, etc.)
- `@cli.command("list")` → `@click.command("list")` (preserve the explicit name)
- `@cli.command("claim-next")` → `@click.command("claim-next")`
- `@cli.command("release")` → `@click.command("release")`
- `_get_db()` → `get_db()` (4 fewer characters, no semantic change)
- `_refresh_summary(db)` → `refresh_summary(db)`

**Step 2: Add `register()` function at the bottom**

```python
def register(cli: click.Group) -> None:
    """Attach issue CRUD and lifecycle commands to the CLI group."""
    cli.add_command(create)
    cli.add_command(show)
    cli.add_command(list_issues, "list")
    cli.add_command(update)
    cli.add_command(close)
    cli.add_command(reopen)
    cli.add_command(claim)
    cli.add_command(claim_next, "claim-next")
    cli.add_command(release)
    cli.add_command(undo)
```

Note: `list_issues` gets registered as `"list"` (the CLI-facing name). `claim_next` as `"claim-next"`. For commands where the function name matches the CLI name (`create`, `show`, etc.), no explicit name is needed in `add_command`. However, since these commands already have `@click.command("claim-next")` etc., the name parameter in `add_command` is optional — Click reads it from the decorator. Include it for clarity on the renamed ones.

**Step 3: Verify import**

Run: `uv run python -c "from filigree.cli_commands.issues import register; print('OK')"`
Expected: `OK`

---

## Task 3: Create `cli_commands/planning.py`

**Files:**
- Create: `src/filigree/cli_commands/planning.py`

**Commands to move** (lines from `cli.py`):
- `ready` (L447-467) — `@click.command()`
- `blocked` (L470-484) — `@click.command()`
- `plan` (L487-527) — `@click.command()`
- `add_dep` (L530-560) — `@click.command("add-dep")`
- `remove_dep` (L563-580) — `@click.command("remove-dep")`
- `critical_path` (L1039-1057) — `@click.command("critical-path")`
- `create_plan` (L1501-1563) — `@click.command("create-plan")`
- `changes` (L1732-1753) — `@click.command()`

**Imports needed:**
```python
from __future__ import annotations

import json as json_mod
import sys
from pathlib import Path

import click

from filigree.cli_common import get_db, refresh_summary
```

`Path` is needed by `create_plan` (for `Path(file_path).read_text()`).

**Step 1: Create file with all 8 commands**

Same mechanical transformation as Task 2.

**Step 2: Add `register()` function**

```python
def register(cli: click.Group) -> None:
    """Attach planning and dependency commands to the CLI group."""
    cli.add_command(ready)
    cli.add_command(blocked)
    cli.add_command(plan)
    cli.add_command(add_dep, "add-dep")
    cli.add_command(remove_dep, "remove-dep")
    cli.add_command(critical_path, "critical-path")
    cli.add_command(create_plan, "create-plan")
    cli.add_command(changes)
```

**Step 3: Verify import**

Run: `uv run python -c "from filigree.cli_commands.planning import register; print('OK')"`
Expected: `OK`

---

## Task 4: Create `cli_commands/meta.py`

**Files:**
- Create: `src/filigree/cli_commands/meta.py`

**Commands to move** (lines from `cli.py`):
- `add_comment` (L583-610) — `@click.command("add-comment")`
- `get_comments` (L613-635) — `@click.command("get-comments")`
- `add_label` (L638-668) — `@click.command("add-label")`
- `remove_label` (L672-696) — `@click.command("remove-label")`
- `stats` (L699-718) — `@click.command()`
- `search` (L721-737) — `@click.command()`
- `events_cmd` (L1756-1785) — `@click.command("events")`
- `batch_update` (L1566-1626) — `@click.command("batch-update")`
- `batch_close` (L1629-1663) — `@click.command("batch-close")`
- `batch_add_label` (L1666-1694) — `@click.command("batch-add-label")`
- `batch_add_comment` (L1698-1729) — `@click.command("batch-add-comment")`

**Imports needed:**
```python
from __future__ import annotations

import json as json_mod
import sys

import click

from filigree.cli_common import get_db, refresh_summary
```

**Step 1: Create file with all 11 commands**

Same mechanical transformation.

**Step 2: Add `register()` function**

```python
def register(cli: click.Group) -> None:
    """Attach metadata, batch, and event commands to the CLI group."""
    cli.add_command(add_comment, "add-comment")
    cli.add_command(get_comments, "get-comments")
    cli.add_command(add_label, "add-label")
    cli.add_command(remove_label, "remove-label")
    cli.add_command(stats)
    cli.add_command(search)
    cli.add_command(events_cmd, "events")
    cli.add_command(batch_update, "batch-update")
    cli.add_command(batch_close, "batch-close")
    cli.add_command(batch_add_label, "batch-add-label")
    cli.add_command(batch_add_comment, "batch-add-comment")
```

**Step 3: Verify import**

Run: `uv run python -c "from filigree.cli_commands.meta import register; print('OK')"`
Expected: `OK`

---

## Task 5: Create `cli_commands/workflow.py`

**Files:**
- Create: `src/filigree/cli_commands/workflow.py`

**Commands to move** (lines from `cli.py`):
- `templates` group (L740-761) — `@click.group(invoke_without_command=True)`
- `templates_reload` (L764-769) — `@templates.command("reload")` (subcommand of `templates` group)
- `workflow_states` (L1185-1197) — `@click.command("workflow-states")`
- `types_cmd` (L1200-1224) — `@click.command("types")`
- `type_info` (L1227-1274) — `@click.command("type-info")`
- `transitions_cmd` (L1277-1317) — `@click.command("transitions")`
- `packs_cmd` (L1320-1347) — `@click.command("packs")`
- `validate_cmd` (L1350-1384) — `@click.command("validate")`
- `guide_cmd` (L1387-1424) — `@click.command("guide")`
- `explain_state` (L1788-1849) — `@click.command("explain-state")`

**Special handling for `templates` group:**

`templates` is a `@click.group()`, not a `@click.command()`. It has one subcommand `templates_reload` which is defined with `@templates.command("reload")`. This means `templates_reload` must be defined **after** `templates` in the file, and it decorates with `@templates.command("reload")` (not standalone). Registration only needs to add the group — the subcommand is already attached.

```python
@click.group(invoke_without_command=True)
@click.option("--type", "issue_type", default=None, help="Show specific template")
@click.pass_context
def templates(ctx: click.Context, issue_type: str | None) -> None:
    """Show available issue templates."""
    ...

@templates.command("reload")
def templates_reload() -> None:
    """Reload workflow templates from disk."""
    ...
```

**Imports needed:**
```python
from __future__ import annotations

import json as json_mod
import sys
from typing import Any

import click

from filigree.cli_common import get_db
```

`Any` is needed by `explain_state` (for `outbound: list[dict[str, Any]]`). No `refresh_summary` needed — workflow commands are read-only.

**Step 1: Create file with all commands**

**Step 2: Add `register()` function**

```python
def register(cli: click.Group) -> None:
    """Attach workflow, template, and type introspection commands to the CLI group."""
    cli.add_command(templates)
    # templates_reload is already a subcommand of templates group
    cli.add_command(workflow_states, "workflow-states")
    cli.add_command(types_cmd, "types")
    cli.add_command(type_info, "type-info")
    cli.add_command(transitions_cmd, "transitions")
    cli.add_command(packs_cmd, "packs")
    cli.add_command(validate_cmd, "validate")
    cli.add_command(guide_cmd, "guide")
    cli.add_command(explain_state, "explain-state")
```

**Step 3: Verify import**

Run: `uv run python -c "from filigree.cli_commands.workflow import register; print('OK')"`
Expected: `OK`

---

## Task 6: Create `cli_commands/admin.py`

**Files:**
- Create: `src/filigree/cli_commands/admin.py`

**Commands to move** (lines from `cli.py`):
- `init` (L86-132) — `@click.command()`
- `install` (L794-914) — `@click.command()`
- `doctor` (L917-953) — `@click.command()`
- `migrate` (L772-791) — `@click.command()`
- `dashboard` (L983-1007) — `@click.command()`
- `ensure_dashboard_cmd` (L1024-1036) — `@click.command("ensure-dashboard")`
- `session_context` (L1010-1021) — `@click.command("session-context")`
- `metrics` (L956-980) — `@click.command()`
- `export_data` (L1078-1084) — `@click.command("export")`
- `import_data` (L1087-1099) — `@click.command("import")`
- `archive` (L1102-1119) — `@click.command()`
- `clean_stale_findings` (L1122-1136) — `@click.command("clean-stale-findings")`
- `compact` (L1139-1153) — `@click.command()`

**Imports needed:**
```python
from __future__ import annotations

import json as json_mod
import logging
import os
import sqlite3
import sys
from pathlib import Path

import click

from filigree.cli_common import get_db, refresh_summary
from filigree.core import (
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    SUMMARY_FILENAME,
    FiligreeDB,
    find_filigree_root,
    get_mode,
    read_config,
    write_config,
)
from filigree.summary import write_summary
```

These are the heaviest imports because `init` and `install` use `core` constants directly (`DB_FILENAME`, `FILIGREE_DIR_NAME`, etc.) and `write_summary`. Other modules only need `get_db`/`refresh_summary` from `cli_common`.

**Step 1: Create file with all 13 commands**

Same mechanical transformation. Note: `install` has a lazy import of `filigree.install` functions — preserve that pattern. Same for `doctor` (`from filigree.install import run_doctor`), `dashboard` (`from filigree.dashboard import main`), `metrics` (`from filigree.analytics import get_flow_metrics`), `session_context` (`from filigree.hooks import generate_session_context`), `ensure_dashboard_cmd` (`from filigree.hooks import ensure_dashboard_running`), and `migrate` (`from filigree.migrate import migrate_from_beads`).

**Step 2: Add `register()` function**

```python
def register(cli: click.Group) -> None:
    """Attach setup, data management, and admin commands to the CLI group."""
    cli.add_command(init)
    cli.add_command(install)
    cli.add_command(doctor)
    cli.add_command(migrate)
    cli.add_command(dashboard)
    cli.add_command(ensure_dashboard_cmd, "ensure-dashboard")
    cli.add_command(session_context, "session-context")
    cli.add_command(metrics)
    cli.add_command(export_data, "export")
    cli.add_command(import_data, "import")
    cli.add_command(archive)
    cli.add_command(clean_stale_findings, "clean-stale-findings")
    cli.add_command(compact)
```

**Step 3: Verify import**

Run: `uv run python -c "from filigree.cli_commands.admin import register; print('OK')"`
Expected: `OK`

---

## Task 7: Create `cli_commands/server.py`

**Files:**
- Create: `src/filigree/cli_commands/server.py`

**Commands and helpers to move** (lines from `cli.py`):
- `_reload_server_daemon_if_running` (L1857-1882) — private helper
- `server` group (L1885-1887) — `@click.group()`
- `server_start` (L1890-1899) — `@server.command("start")`
- `server_stop` (L1902-1910) — `@server.command("stop")`
- `server_status_cmd` (L1913-1923) — `@server.command("status")`
- `server_register` (L1926-1948) — `@server.command("register")`
- `server_unregister` (L1951-1970) — `@server.command("unregister")`

**Special handling:** `server` is a `@click.group()` with 5 subcommands, same pattern as `templates`. All subcommands use `@server.command(...)`. The helper `_reload_server_daemon_if_running()` stays in this module (used only by `server_register` and `server_unregister`).

**Imports needed:**
```python
from __future__ import annotations

import sys
from pathlib import Path

import click
```

All heavy imports (`filigree.server.*`) are lazy (inside function bodies) — preserve that.

**Step 1: Create file with the helper and all server commands**

**Step 2: Add `register()` function**

```python
def register(cli: click.Group) -> None:
    """Attach server daemon management group to the CLI."""
    cli.add_command(server)
```

Only the group itself needs registering — subcommands are already attached via `@server.command()`.

**Step 3: Verify import**

Run: `uv run python -c "from filigree.cli_commands.server import register; print('OK')"`
Expected: `OK`

---

## Task 8: Rewrite `cli.py` as thin shell

**Files:**
- Modify: `src/filigree/cli.py`

**Step 1: Replace cli.py contents**

The new `cli.py` keeps only:
- Module docstring (shortened)
- The `cli` group definition
- Registration loop
- `__main__` guard

```python
"""CLI for the filigree issue tracker.

Convention-based: discovers .filigree/ by walking up from cwd.
Commands are defined in cli_commands/ subpackage modules.
"""

from __future__ import annotations

import click

from filigree import __version__
from filigree.cli_commands import admin, issues, meta, planning, server, workflow


@click.group()
@click.version_option(version=__version__, prog_name="filigree")
@click.option("--actor", default="cli", help="Actor identity for audit trail (default: cli)")
@click.pass_context
def cli(ctx: click.Context, actor: str) -> None:
    """Filigree — agent-native issue tracker."""
    ctx.ensure_object(dict)
    ctx.obj["actor"] = actor


# Register domain command modules
for _mod in (issues, planning, meta, workflow, admin, server):
    _mod.register(cli)


if __name__ == "__main__":
    cli()
```

**Step 2: Run full CLI test suite**

Run: `uv run pytest tests/test_cli.py -v --tb=short`
Expected: All tests pass (same count as before refactor)

**Step 3: Verify `filigree --help` shows all commands**

Run: `uv run filigree --help`
Expected: All 48+ commands listed

**Step 4: Spot-check a few commands**

```bash
uv run filigree ready
uv run filigree stats
uv run filigree types
uv run filigree --help | grep -c "  "  # Should match pre-refactor count
```

---

## Task 9: Lint, type-check, and full test suite

**Files:**
- All new `cli_commands/*.py` files
- Modified `cli.py`

**Step 1: Ruff lint**

Run: `uv run ruff check src/filigree/cli_commands/ src/filigree/cli.py`
Expected: No errors (fix any that appear)

**Step 2: Ruff format**

Run: `uv run ruff format --check src/filigree/cli_commands/ src/filigree/cli.py`
Expected: No formatting issues (fix any that appear)

**Step 3: Mypy type-check**

Run: `uv run mypy src/filigree/cli_commands/ src/filigree/cli.py`
Expected: No errors

**Step 4: Full test suite**

Run: `uv run pytest --tb=short`
Expected: All tests pass (not just CLI tests — everything)

---

## Task 10: Commit

**Step 1: Stage and commit**

```bash
git add src/filigree/cli_commands/ src/filigree/cli.py
git commit -m "refactor: split CLI commands into cli_commands/ subpackage"
```

---

## Command-to-Module Reference

| Module | Commands | Count |
|--------|----------|-------|
| `issues.py` | create, show, list, update, close, reopen, claim, claim-next, release, undo | 10 |
| `planning.py` | ready, blocked, plan, add-dep, remove-dep, critical-path, create-plan, changes | 8 |
| `meta.py` | add-comment, get-comments, add-label, remove-label, stats, search, events, batch-update, batch-close, batch-add-label, batch-add-comment | 11 |
| `workflow.py` | templates (group+reload), workflow-states, types, type-info, transitions, packs, validate, guide, explain-state | 10 |
| `admin.py` | init, install, doctor, migrate, dashboard, ensure-dashboard, session-context, metrics, export, import, archive, clean-stale-findings, compact | 13 |
| `server.py` | server (group: start, stop, status, register, unregister) | 6 |
| **Total** | | **58** |
