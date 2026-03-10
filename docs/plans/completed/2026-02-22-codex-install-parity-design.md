# Codex Install Parity Design

**Date:** 2026-02-22
**Status:** Approved

## Problem

Filigree's install and doctor systems have full Claude Code support (MCP, hooks,
skills, CLAUDE.md instructions) but incomplete Codex support. Codex currently
gets MCP config and AGENTS.md instructions only — no skills and no session
context bootstrapping.

## Audit Results

| Feature | Claude Code | Codex | Gap |
|---------|------------|-------|-----|
| MCP server | `.mcp.json` | `.codex/config.toml` | None |
| Instructions | `CLAUDE.md` | `AGENTS.md` | None |
| SessionStart hooks | `.claude/settings.json` | N/A (no platform support) | Mitigated via instructions hint |
| Skills | `.claude/skills/` | `.agents/skills/` | **New: install + doctor check** |

## Design

### 1. Codex Skill Installation

New function `install_codex_skills(project_root)` in `install.py`:
- Copies `src/filigree/skills/filigree-workflow/` to `.agents/skills/filigree-workflow/`
- Same content as Claude Code skills (reused, not forked)
- Idempotent — overwrites existing to pick up version upgrades

### 2. AGENTS.md Session Context Hint

Add a small note to `src/filigree/data/instructions.md` telling the agent to
run `filigree session-context` at the start of a session. This is harmless for
Claude Code (which already has the SessionStart hook) and helpful for Codex
(which has no hook mechanism).

### 3. Doctor Check Expansion

Add check: "Codex skills" — looks for `.agents/skills/filigree-workflow/SKILL.md`.
Reports failure with fix hint `Run: filigree install --codex-skills`.

### 4. CLI Wiring

- New `--codex-skills` flag on `filigree install`
- `install_all` and `--codex` both trigger Codex skill installation
- Doctor reports the new check

## Out of Scope

- Codex session hooks (platform has no equivalent)
- `notify` workaround (fires on wrong event)
- Forked skill content for Codex
