> **Note:** This planning document was written when the project was named "rebar" (previously "keel"). The project has since been renamed to **filigree**. This document is retained for historical reference.

# Rebar: Rename & Release Professionalisation

**Date**: 2026-02-15
**Status**: Approved

## Summary

Rename the project from `keel` to `rebar` (PyPI-available), harden CI/release infrastructure, polish docs, and add community files for a professional 0.1.0 open-source launch.

## Decisions

| Decision | Choice |
|----------|--------|
| Package name | `rebar` |
| Rename scope | Full (PyPI, CLI, imports, data dir, repo) |
| First release version | 0.1.0 |
| Version source of truth | `pyproject.toml` (read via `importlib.metadata` at runtime) |
| GitHub repo | Rename to `tachyon-beep/rebar` (manual, post-merge) |
| Migration from keel | None (no existing users; local DB moved by hand) |
| Docs site (mkdocs) | Deferred to post-launch |

## Phase 1: Full Rename (`keel` -> `rebar`)

### Scope

| What | From | To |
|------|------|----|
| PyPI package name | `keel` | `rebar` |
| CLI entry points | `keel`, `keel-mcp`, `keel-dashboard` | `rebar`, `rebar-mcp`, `rebar-dashboard` |
| Python import | `from keel.core import ...` | `from rebar.core import ...` |
| Source directory | `src/keel/` | `src/rebar/` |
| Data directory | `.keel/` | `.rebar/` |
| Config file | `.keel/config.json` | `.rebar/config.json` |
| Database | `.keel/keel.db` | `.rebar/rebar.db` |
| Context summary | `.keel/context.md` | `.rebar/context.md` |
| MCP server name | `keel` in `.mcp.json` | `rebar` |
| MCP resource URI | `keel://context` | `rebar://context` |
| MCP prompt name | `keel-workflow` | `rebar-workflow` |
| CLAUDE.md markers | `<!-- keel:instructions -->` | `<!-- rebar:instructions -->` |
| Class name | `KeelDB` | `RebarDB` |
| All doc references | `keel` | `rebar` |

### What does NOT change

- Issue IDs (user-configurable prefix, not tied to project name)
- SQLite schema structure
- `Issue` dataclass name (generic)
- Feature set (no functional changes in this phase)

### Migration

None. No existing users. Local `.keel/` directory will be moved by hand.

## Phase 2: CI & Release Hardening

| Gap | Fix |
|-----|-----|
| CI doesn't enforce coverage | Add `--cov-fail-under=85` to pytest step in `ci.yml` |
| Release workflow skips tests | Add lint+typecheck+test jobs as prerequisites to build/publish |
| Version hardcoded in two places | Single source in `pyproject.toml`; `__init__.py` reads via `importlib.metadata.version("rebar")` |
| No `py.typed` marker | Add `src/rebar/py.typed` + include in build config |

## Phase 3: Docs & Polish

- **CHANGELOG**: Rewrite for rebar identity. Comprehensive 0.1.0 "Added" section covering full feature set.
- **CLI help text**: Audit all commands for clear, consistent docstrings. Ensure `rebar --help` groups commands logically.
- **README**: Rename pass (already comprehensive, no structural changes).
- **Error messages**: Quick audit for clarity. Recent changes (unknown type rejection, double-close error) are good baseline.

## Phase 4: Community Infrastructure

### Files to add

| File | Content |
|------|---------|
| `CODE_OF_CONDUCT.md` | Contributor Covenant v2.1 |
| `SECURITY.md` | Vulnerability reporting process |
| `.github/ISSUE_TEMPLATE/bug_report.md` | Structured bug report template |
| `.github/ISSUE_TEMPLATE/feature_request.md` | Feature request template |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR checklist (tests, changelog, description) |
| `ROADMAP.md` | Brief post-0.1.0 direction |

### README badges

Replace static badges with live ones: Python version, license, PyPI version, CI status.

### Skipped (deferred post-launch)

- Discussion templates
- Sponsorship config
- mkdocs documentation site

## Release Sequence

1. Complete phases 1-4, each committed independently
2. Tag `v0.1.0`
3. Push tag -> release workflow builds + publishes to PyPI
4. Rename GitHub repo to `tachyon-beep/rebar` (manual)
5. Verify `pip install rebar` works
