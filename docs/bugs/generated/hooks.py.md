## Summary
`[rule_id: injection]` Session context output interpolates untrusted issue titles directly, enabling prompt/content injection into hook output.

## Severity
- Severity: major
- Priority: P1

## Evidence
- `src/filigree/hooks.py:65`  
  `lines.append(f'P{issue.priority} {issue.id} [{issue.type}] "{issue.title}"')`
- `src/filigree/hooks.py:74`  
  `lines.append(f'P{issue.priority} {issue.id} [{issue.type}] "{issue.title}"')`
- `src/filigree/hooks.py:86`  
  `lines.append(f'{prefix}P{item["priority"]} {item["id"]} [{item["type"]}] "{item["title"]}"')`
- Titles are user-controlled and only checked for non-empty in `src/filigree/core.py:761`.
- The codebase already treats title sanitization as necessary in summaries (`src/filigree/summary.py:24`, `src/filigree/summary.py:30`, `src/filigree/summary.py:31`).

## Root Cause Hypothesis
`hooks.py` builds prompt-facing text from DB fields assuming titles are safe, but issue titles are free-form user input.

## Suggested Fix
Add a local sanitizer in `hooks.py` (strip control chars/newlines, collapse whitespace, length-cap) and apply it to every interpolated title in `_build_context()`.

---
## Summary
`[rule_id: race-condition]` Dashboard “already running” detection trusts PID liveness + port-open only, so stale PID reuse can produce false positives and block restart.

## Severity
- Severity: major
- Priority: P2

## Evidence
- Pre-lock fast path in `src/filigree/hooks.py:257` to `src/filigree/hooks.py:260`:
  checks `is_pid_alive(pid)` + `_is_port_listening(port)` and returns “running”.
- Post-lock recheck repeats same logic in `src/filigree/hooks.py:275` to `src/filigree/hooks.py:278`.
- Stale cleanup only removes dead PIDs (`src/filigree/hooks.py:263`), not live-but-wrong PID ownership.
- A stronger ownership check exists but is unused here: `src/filigree/ephemeral.py:148` (`verify_pid_ownership`).

## Root Cause Hypothesis
Lifecycle logic validates process existence, not process identity; PID reuse and unrelated listeners on stored port are not distinguished from real dashboard state.

## Suggested Fix
Use `verify_pid_ownership(pid_file, expected_cmd="filigree")` in both running checks (and optionally `_build_context` URL display check). If ownership fails, clear stale pid/port files and continue startup.

---
## Summary
`[rule_id: logic-error]` Skill freshness only checks `.claude/skills`, so Codex skill installs under `.agents/skills` are never auto-refreshed.

## Severity
- Severity: minor
- Priority: P3

## Evidence
- Hardcoded target in `src/filigree/hooks.py:137`:  
  `.claude/skills/filigree-workflow/SKILL.md`
- Update path only calls `install_skills(project_root)` (`src/filigree/hooks.py:148`), which updates `.claude`.
- Codex has a separate install path/function: `src/filigree/install.py:627` to `src/filigree/install.py:648` (`install_codex_skills` to `.agents/skills`).
- Function contract says it checks `CLAUDE.md/AGENTS.md ... and skills` (`src/filigree/hooks.py:112`).

## Root Cause Hypothesis
Freshness logic predates/omits Codex skill path support and only implemented Claude skill refresh.

## Suggested Fix
In `_check_instructions_freshness()`, check both skill targets:
- `.claude/skills/filigree-workflow/SKILL.md` -> `install_skills(project_root)`
- `.agents/skills/filigree-workflow/SKILL.md` -> `install_codex_skills(project_root)`  
and emit separate update messages.