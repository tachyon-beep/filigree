> **Note:** This planning document was written when the project was named "rebar" (previously "keel"). The project has since been renamed to **filigree**. This document is retained for historical reference.

# Rebar Rename & Release Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rename the project from `keel` to `rebar` across all files, harden CI/release, polish docs, add community infrastructure, and prepare for a professional 0.1.0 PyPI launch.

**Architecture:** Full find-and-replace rename across source, tests, config, and docs. CI hardening via workflow edits. Community files are new additions. No functional changes to the codebase — just identity and infrastructure.

**Tech Stack:** Python 3.11+, hatchling, GitHub Actions, ruff, mypy, pytest

---

## Task 1: Rename source directory and update all Python imports

This is the atomic core of the rename. Everything must change together or nothing works.

**Files:**
- Rename: `src/keel/` -> `src/rebar/` (entire directory, including `static/`)
- Modify: Every `.py` file under `src/rebar/` (imports)
- Modify: Every `.py` file under `tests/` (imports)
- Modify: `tests/conftest.py`

**Step 1: Rename the source directory**

```bash
git mv src/keel src/rebar
```

**Step 2: Rename `KeelDB` -> `RebarDB` across all source files**

In `src/rebar/core.py`, rename the class:
- `class KeelDB:` -> `class RebarDB:`
- All internal references to `KeelDB` within the file

In `src/rebar/__init__.py`:
- `from keel.core import Issue, KeelDB` -> `from rebar.core import Issue, RebarDB`
- `__all__ = ["Issue", "KeelDB", "__version__"]` -> `__all__ = ["Issue", "RebarDB", "__version__"]`

In every source file under `src/rebar/`, replace:
- `from keel.` -> `from rebar.`
- `import keel.` -> `import rebar.`
- `keel.` module references -> `rebar.`
- `KeelDB` -> `RebarDB`

Affected source files (all 13):
- `src/rebar/__init__.py`
- `src/rebar/core.py`
- `src/rebar/cli.py`
- `src/rebar/mcp_server.py`
- `src/rebar/templates.py`
- `src/rebar/templates_data.py`
- `src/rebar/summary.py`
- `src/rebar/analytics.py`
- `src/rebar/install.py`
- `src/rebar/migrate.py`
- `src/rebar/dashboard.py`
- `src/rebar/logging.py`
- `src/rebar/static/dashboard.html`

**Step 3: Update all test file imports**

In every test file under `tests/`, replace:
- `from keel.` -> `from rebar.`
- `import keel` -> `import rebar`
- `KeelDB` -> `RebarDB`

Affected test files (all 22):
- `tests/conftest.py`
- `tests/test_core.py`
- `tests/test_cli.py`
- `tests/test_mcp.py`
- `tests/test_workflow_behavior.py`
- `tests/test_migrate.py`
- `tests/test_core_gaps.py`
- `tests/test_v05_features.py`
- `tests/test_undo.py`
- `tests/test_backward_compat.py`
- `tests/test_templates.py`
- `tests/test_dashboard.py`
- `tests/test_summary.py`
- `tests/test_migration_v6.py`
- `tests/test_keeldb_templates.py`
- `tests/test_migration_v5.py`
- `tests/test_v10_features.py`
- `tests/test_analytics.py`
- `tests/test_config_packs.py`
- `tests/test_e2e_workflows.py`
- `tests/test_logging.py`
- `tests/test_install.py`

**Step 4: Update string literals and paths in source**

These are NOT import statements — they're runtime strings that reference `keel`:

In `src/rebar/core.py`:
- `.keel` directory references -> `.rebar`
- `keel.db` -> `rebar.db`
- `keel-` prefix defaults (if any) — check `_generate_id()` and `init()`
- `"keel"` in any user-facing strings

In `src/rebar/cli.py`:
- CLI group name/help text referencing `keel`
- Any hardcoded `.keel` path references

In `src/rebar/mcp_server.py`:
- `keel://context` -> `rebar://context`
- `keel-workflow` prompt name -> `rebar-workflow`
- Server name `"keel"` -> `"rebar"`
- Tool descriptions mentioning "keel"

In `src/rebar/install.py`:
- `.keel/` directory references -> `.rebar/`
- `keel.db` -> `rebar.db`
- MCP config template (`"keel"` server name, `keel-mcp` command)
- CLAUDE.md template content (all `keel` CLI references -> `rebar`)
- `<!-- keel:instructions -->` markers -> `<!-- rebar:instructions -->`

In `src/rebar/summary.py`:
- Any `keel` references in generated context.md content

In `src/rebar/migrate.py`:
- `.keel` references if any
- Beads migration references to keel

In `src/rebar/dashboard.py`:
- Title/branding strings
- Route prefixes if any

In `src/rebar/logging.py`:
- Logger name if it uses `keel`

In `src/rebar/static/dashboard.html`:
- Title, heading, or branding text

**Step 5: Update string literals in test files**

Test files that assert on output strings containing "keel" — these need updating to "rebar". Grep for string literals:
- `.keel` path references in test assertions
- `keel.db` in assertions
- `KeelDB` in string assertions (e.g., repr tests)
- `keel-` prefix in issue ID assertions

Note: `test_keeldb_templates.py` should be renamed to `test_rebardb_templates.py`:
```bash
git mv tests/test_keeldb_templates.py tests/test_rebardb_templates.py
```

**Step 6: Run tests to verify the rename**

```bash
uv run pytest -x -q
```

Expected: All tests pass. If any fail, fix the missed references.

**Step 7: Run lint and type check**

```bash
uv run ruff check src/ tests/
uv run mypy src/rebar/
```

Expected: Clean. Mypy config in pyproject.toml still says `src/keel/` — that gets fixed in Task 2.

**Step 8: Commit**

```bash
git add -A
git commit -m "refactor!: rename keel -> rebar across all source and test files

Full rename of source directory, class names (KeelDB -> RebarDB),
imports, string literals, paths, and MCP identifiers."
```

---

## Task 2: Update pyproject.toml and build configuration

**Files:**
- Modify: `pyproject.toml`

**Step 1: Update project metadata**

```toml
[project]
name = "rebar"
description = "Agent-native issue tracker with convention-based project discovery"
keywords = ["issue-tracker", "mcp", "agent", "sqlite", "cli"]
```

Update URLs:
```toml
[project.urls]
Homepage = "https://github.com/tachyon-beep/rebar"
Repository = "https://github.com/tachyon-beep/rebar"
Issues = "https://github.com/tachyon-beep/rebar/issues"
Changelog = "https://github.com/tachyon-beep/rebar/blob/main/CHANGELOG.md"
```

**Step 2: Update entry points**

```toml
[project.scripts]
rebar = "rebar.cli:cli"
rebar-mcp = "rebar.mcp_server:main"
rebar-dashboard = "rebar.dashboard:main"
```

**Step 3: Update build target**

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/rebar"]
```

**Step 4: Update dev dependencies**

```toml
[dependency-groups]
dev = [
    # ... same deps ...
    "rebar[mcp]",
    "rebar[dashboard]",
]
```

**Step 5: Update tool configs**

```toml
[tool.ruff]
src = ["src"]

[tool.ruff.lint.isort]
known-first-party = ["rebar"]

[tool.ruff.lint.per-file-ignores]
"src/rebar/core.py" = ["S608"]
"src/rebar/mcp_server.py" = ["E501"]
"src/rebar/dashboard.py" = ["E501", "S608"]

[tool.coverage.run]
source = ["rebar"]
```

Mypy overrides don't reference `keel` — no changes needed there.

**Step 6: Run full CI locally**

```bash
uv sync --group dev
uv run ruff check src/ tests/
uv run mypy src/rebar/
uv run pytest -x -q
```

Expected: All clean and passing.

**Step 7: Commit**

```bash
git add pyproject.toml
git commit -m "build: update pyproject.toml for rebar identity

Package name, entry points, build target, tool configs all updated."
```

---

## Task 3: Update config files and CI workflows

**Files:**
- Modify: `.mcp.json`
- Modify: `.gitignore`
- Modify: `Makefile`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `CLAUDE.md`

**Step 1: Update `.mcp.json`**

Change server name and command:
```json
{
  "mcpServers": {
    "rebar": {
      "type": "stdio",
      "command": "/home/john/keel/.venv/bin/rebar-mcp",
      "args": ["--project", "/home/john/keel"],
      "env": {}
    }
  }
}
```

Note: The path still says `/home/john/keel` because the local directory hasn't been renamed yet. That's fine — it's a local dev config.

**Step 2: Update `.gitignore`**

Change `.keel/` to `.rebar/`.

**Step 3: Update `Makefile`**

Change `uv run mypy src/keel/` to `uv run mypy src/rebar/`.

**Step 4: Update `.github/workflows/ci.yml`**

Change `uv run mypy src/keel/` to `uv run mypy src/rebar/`.

**Step 5: Update `CLAUDE.md`**

Replace all `keel` CLI references with `rebar`. Update the instruction markers:
- `<!-- keel:instructions -->` -> `<!-- rebar:instructions -->`
- `<!-- /keel:instructions -->` -> `<!-- /rebar:instructions -->`
- All command examples: `keel ready` -> `rebar ready`, etc.
- Header: `## Keel Issue Tracker` -> `## Rebar Issue Tracker`

**Step 6: Commit**

```bash
git add .mcp.json .gitignore Makefile .github/workflows/ci.yml CLAUDE.md
git commit -m "chore: update config files and CI for rebar rename"
```

---

## Task 4: Single-source version via importlib.metadata

**Files:**
- Modify: `src/rebar/__init__.py`

**Step 1: Update `__init__.py`**

Replace the hardcoded version with:

```python
"""Rebar - agent-native issue tracker with convention-based project discovery."""

from importlib.metadata import version

__version__ = version("rebar")

from rebar.core import Issue, RebarDB

__all__ = ["Issue", "RebarDB", "__version__"]
```

**Step 2: Verify it works**

```bash
uv run python -c "import rebar; print(rebar.__version__)"
```

Expected: `0.1.0`

**Step 3: Commit**

```bash
git add src/rebar/__init__.py
git commit -m "build: single-source version via importlib.metadata"
```

---

## Task 5: CI hardening

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`

**Step 1: Add coverage enforcement to CI**

In `ci.yml`, change the pytest step to:
```yaml
      - run: uv run pytest --cov --cov-report=term-missing --cov-fail-under=85
```

**Step 2: Add CI gate to release workflow**

In `release.yml`, add a `ci` job that runs before `build`:

```yaml
jobs:
  ci:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v6
      - uses: astral-sh/setup-uv@v7
        with:
          python-version: ${{ matrix.python-version }}
      - run: uv sync --group dev
      - run: uv run ruff check src/ tests/
      - run: uv run ruff format --check src/ tests/
      - run: uv run mypy src/rebar/
      - run: uv run pytest --cov --cov-report=term-missing --cov-fail-under=85

  build:
    needs: ci
    runs-on: ubuntu-latest
    # ... rest unchanged
```

**Step 3: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/release.yml
git commit -m "ci: enforce coverage threshold and gate releases on full CI"
```

---

## Task 6: Add py.typed marker

**Files:**
- Create: `src/rebar/py.typed`

**Step 1: Create the marker file**

```bash
touch src/rebar/py.typed
```

This is an empty file per PEP 561. Hatchling will include it automatically since it's inside the package directory.

**Step 2: Verify it gets included in the wheel**

```bash
uv build
unzip -l dist/rebar-0.1.0-py3-none-any.whl | grep py.typed
```

Expected: `rebar/py.typed` appears in the listing.

**Step 3: Clean up and commit**

```bash
rm -rf dist/
git add src/rebar/py.typed
git commit -m "build: add py.typed marker for PEP 561 type checking support"
```

---

## Task 7: Rewrite CHANGELOG for rebar 0.1.0

**Files:**
- Modify: `CHANGELOG.md`

**Step 1: Rewrite the changelog**

Replace the entire file with a comprehensive 0.1.0 entry that reflects the full feature set:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-02-15

### Added

- SQLite-backed issue database with WAL mode and convention-based `.rebar/` discovery
- 42 MCP tools for native AI agent interaction (read, write, claim, batch, workflow, data management)
- Full CLI with 30+ commands, `--json` output, and `--actor` audit trail
- 9 workflow templates across 2 packs (core: task/bug/feature/epic; planning: milestone/phase/step/work_package/deliverable)
- Enforced workflow state machines with transition validation
- Dependency graph with cycle detection, ready queue, and critical path analysis
- Hierarchical planning (milestone/phase/step) with automatic unblocking
- Atomic claiming with optimistic locking for multi-agent coordination
- Pre-computed `context.md` summary regenerated on every mutation
- Flow analytics: cycle time, lead time, throughput metrics
- Comments, labels, and full event audit trail
- Session resumption via `get_changes --since <timestamp>`
- `rebar install` for MCP config, CLAUDE.md injection, and .gitignore setup
- `rebar doctor` health checks with auto-fix support
- Web dashboard (`rebar-dashboard`) via FastAPI
- Beads migration support (`rebar migrate --from-beads`)
- PEP 561 `py.typed` marker for downstream type checking
```

**Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: rewrite CHANGELOG for rebar 0.1.0 launch"
```

---

## Task 8: Update README and CONTRIBUTING

**Files:**
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`

**Step 1: Update README.md**

Global replacements:
- `keel` -> `rebar` (CLI commands, pip install, package refs)
- `keel-mcp` -> `rebar-mcp`
- `keel-dashboard` -> `rebar-dashboard`
- `Keel` -> `Rebar` (title, prose)
- `.keel/` -> `.rebar/`
- `keel.db` -> `rebar.db`
- `keel://context` -> `rebar://context`
- `keel-workflow` -> `rebar-workflow`
- `KeelDB` -> `RebarDB`
- GitHub URLs: `tachyon-beep/keel` -> `tachyon-beep/rebar`
- `src/keel/` -> `src/rebar/`

Update badges to live versions:
```markdown
![Python 3.11+](https://img.shields.io/pypi/pyversions/rebar)
![License: MIT](https://img.shields.io/pypi/l/rebar)
![PyPI](https://img.shields.io/pypi/v/rebar)
![CI](https://github.com/tachyon-beep/rebar/actions/workflows/ci.yml/badge.svg)
```

**Step 2: Update CONTRIBUTING.md**

Replace `keel` references with `rebar`:
- Clone URL
- `src/keel/` -> `src/rebar/`
- `KeelDB` -> `RebarDB`

**Step 3: Verify no keel references remain**

```bash
grep -ri "keel" README.md CONTRIBUTING.md
```

Expected: No matches (or only in historical context like "renamed from keel").

**Step 4: Commit**

```bash
git add README.md CONTRIBUTING.md
git commit -m "docs: update README and CONTRIBUTING for rebar identity"
```

---

## Task 9: CLI help text audit

**Files:**
- Modify: `src/rebar/cli.py`

**Step 1: Update the CLI group docstring**

The top-level `@click.group()` should have a clear description:
```python
@click.group()
@click.version_option(version=__version__, prog_name="rebar")
@click.option("--actor", default="cli", help="Identity for the audit trail.")
@click.pass_context
def cli(ctx: click.Context, actor: str) -> None:
    """Rebar — agent-native issue tracker.

    Manage issues, workflows, and dependencies from the command line.
    All commands support --json for machine-readable output.
    """
```

**Step 2: Audit all command docstrings**

Review every `@cli.command()` function docstring. Each should:
- Be one clear sentence describing what the command does
- Not reference "keel" anywhere

Specific ones to check:
- `init` — should say "Initialize a .rebar/ directory"
- `install` — should reference rebar, not keel
- `migrate` — should reference rebar
- `doctor` — should reference .rebar/

**Step 3: Run `rebar --help` and spot-check subcommands**

```bash
uv run rebar --help
uv run rebar create --help
uv run rebar ready --help
```

Verify output looks clean and professional.

**Step 4: Commit**

```bash
git add src/rebar/cli.py
git commit -m "docs: audit and polish CLI help text"
```

---

## Task 10: Add CODE_OF_CONDUCT.md

**Files:**
- Create: `CODE_OF_CONDUCT.md`

**Step 1: Write the file**

Use Contributor Covenant v2.1 (the standard). Set enforcement contact to the repo maintainer.

**Step 2: Commit**

```bash
git add CODE_OF_CONDUCT.md
git commit -m "docs: add Contributor Covenant code of conduct"
```

---

## Task 11: Add SECURITY.md

**Files:**
- Create: `SECURITY.md`

**Step 1: Write the file**

Cover:
- Supported versions (only latest 0.x)
- How to report (email or GitHub private vulnerability reporting)
- Response timeline expectation
- Note that rebar is local-only (no network surface) so the attack surface is limited to local file access and SQLite

**Step 2: Commit**

```bash
git add SECURITY.md
git commit -m "docs: add security policy"
```

---

## Task 12: Add GitHub issue and PR templates

**Files:**
- Create: `.github/ISSUE_TEMPLATE/bug_report.md`
- Create: `.github/ISSUE_TEMPLATE/feature_request.md`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`

**Step 1: Write bug report template**

Standard fields: description, steps to reproduce, expected vs actual, environment (Python version, OS), rebar version.

**Step 2: Write feature request template**

Fields: problem statement, proposed solution, alternatives considered.

**Step 3: Write PR template**

Checklist:
- [ ] Tests pass (`make ci`)
- [ ] New tests added for new functionality
- [ ] CHANGELOG.md updated
- [ ] No `keel` references introduced (during rename transition period)

**Step 4: Commit**

```bash
git add .github/ISSUE_TEMPLATE/ .github/PULL_REQUEST_TEMPLATE.md
git commit -m "docs: add GitHub issue and PR templates"
```

---

## Task 13: Add ROADMAP.md

**Files:**
- Create: `ROADMAP.md`

**Step 1: Write the roadmap**

Brief post-0.1.0 direction. Possible items:
- Documentation site (mkdocs)
- GitHub integration (sync issues bidirectionally)
- Team/multi-agent coordination features
- Plugin system for custom workflow packs
- `rebar watch` for file-system triggered actions

Keep it short — 10-15 bullet points max, grouped by near-term / future.

**Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: add post-0.1.0 roadmap"
```

---

## Task 14: Update historical docs (optional, low priority)

**Files:**
- Modify: all files under `docs/plans/` and `docs/arch-analysis-*/`

**Step 1: Decide whether to update**

These are historical design documents from the `keel` era. Options:
- Leave them as-is (they're historical records)
- Add a note at the top: "Note: This document was written when the project was named 'keel'. It has since been renamed to 'rebar'."

Recommendation: Leave as-is. They're internal planning docs, not user-facing.

**Step 2: Commit (if any changes)**

```bash
git add docs/
git commit -m "docs: add historical note to pre-rename planning docs"
```

---

## Task 15: Final verification and tag

**Step 1: Run full CI locally**

```bash
make ci
uv run pytest --cov --cov-report=term-missing --cov-fail-under=85
```

Expected: All green.

**Step 2: Grep for any remaining `keel` references in live code**

```bash
grep -ri "keel" src/rebar/ tests/ pyproject.toml Makefile .github/ CLAUDE.md README.md CONTRIBUTING.md .mcp.json .gitignore --include="*.py" --include="*.toml" --include="*.yml" --include="*.md" --include="*.json"
```

Expected: No matches in live code (historical docs are fine).

**Step 3: Test a clean install**

```bash
uv build
pip install dist/rebar-0.1.0-py3-none-any.whl --force-reinstall
rebar --version
rebar --help
```

Expected: `rebar, version 0.1.0` and clean help output.

**Step 4: Tag and push**

```bash
git tag v0.1.0
git push origin main --tags
```

This triggers the release workflow which publishes to PyPI and creates a GitHub Release.

**Step 5: Rename GitHub repo**

Manual step: Go to github.com/tachyon-beep/keel -> Settings -> rename to `rebar`. GitHub auto-redirects the old URL.

**Step 6: Verify PyPI**

```bash
pip install rebar
rebar --version
```

Expected: `rebar, version 0.1.0`
