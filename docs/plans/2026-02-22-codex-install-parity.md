# Codex Install Parity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give Codex feature parity with Claude Code in filigree's install/doctor system — add Codex skill installation, a session-context hint in instructions, and a doctor check for Codex skills.

**Architecture:** Extend `install.py` with `install_codex_skills()` that copies the shared skill pack to `.agents/skills/`. Add a doctor check for the new path. Wire into CLI with `--codex-skills` flag. Add a session-context hint to the shared instructions template.

**Tech Stack:** Python, pytest, shutil, pathlib

---

### Task 1: Add `install_codex_skills()` function

**Files:**
- Modify: `src/filigree/install.py:566-598` (after existing `install_skills`)
- Test: `tests/test_install.py`

**Step 1: Write the failing tests**

Add a new test class after `TestInstallSkills` (line ~1050):

```python
class TestInstallCodexSkills:
    def test_installs_skill_pack(self, tmp_path: Path) -> None:
        ok, _msg = install_codex_skills(tmp_path)
        assert ok
        skill_md = tmp_path / ".agents" / "skills" / SKILL_NAME / "SKILL.md"
        assert skill_md.exists()
        content = skill_md.read_text()
        assert "filigree-workflow" in content

    def test_overwrites_on_reinstall(self, tmp_path: Path) -> None:
        """Re-install should overwrite existing skill (picks up upgrades)."""
        install_codex_skills(tmp_path)
        skill_md = tmp_path / ".agents" / "skills" / SKILL_NAME / "SKILL.md"
        skill_md.write_text("stale content")
        install_codex_skills(tmp_path)
        assert "filigree-workflow" in skill_md.read_text()

    def test_preserves_other_skills(self, tmp_path: Path) -> None:
        """Installing filigree skill should not touch other skills."""
        other_skill = tmp_path / ".agents" / "skills" / "other-skill"
        other_skill.mkdir(parents=True)
        (other_skill / "SKILL.md").write_text("other")
        install_codex_skills(tmp_path)
        assert (other_skill / "SKILL.md").read_text() == "other"

    def test_includes_references(self, tmp_path: Path) -> None:
        install_codex_skills(tmp_path)
        refs = tmp_path / ".agents" / "skills" / SKILL_NAME / "references"
        assert refs.is_dir()
        assert (refs / "workflow-patterns.md").exists()
        assert (refs / "team-coordination.md").exists()

    def test_includes_examples(self, tmp_path: Path) -> None:
        install_codex_skills(tmp_path)
        examples = tmp_path / ".agents" / "skills" / SKILL_NAME / "examples"
        assert examples.is_dir()
        assert (examples / "sprint-plan.json").exists()
```

Also add `install_codex_skills` to the import block at the top of the test file (line ~33).

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install.py::TestInstallCodexSkills -v`
Expected: ImportError — `install_codex_skills` not found

**Step 3: Implement `install_codex_skills`**

In `src/filigree/install.py`, after `install_skills()` (around line 598), add:

```python
def install_codex_skills(project_root: Path) -> tuple[bool, str]:
    """Copy filigree skill pack into ``.agents/skills/`` for Codex.

    Codex discovers skills at ``.agents/skills/<name>/SKILL.md``.
    Uses the same skill content as Claude Code.

    Idempotent — overwrites existing skill files to keep them up-to-date
    with the installed filigree version.
    """
    source_dir = _get_skills_source_dir()
    skill_source = source_dir / SKILL_NAME
    if not skill_source.is_dir():
        return False, f"Skill source not found at {skill_source}"

    target_dir = project_root / ".agents" / "skills" / SKILL_NAME
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(skill_source, target_dir)

    return True, f"Installed skill pack to {target_dir}"
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install.py::TestInstallCodexSkills -v`
Expected: All 5 PASS

**Step 5: Commit**

```bash
git add src/filigree/install.py tests/test_install.py
git commit -m "feat(install): add install_codex_skills for .agents/skills/"
```

---

### Task 2: Add Codex skills doctor check

**Files:**
- Modify: `src/filigree/install.py` (in `run_doctor`, after check 9)
- Test: `tests/test_install.py`

**Step 1: Write the failing tests**

Add after `TestDoctorSkillsCheck`:

```python
class TestDoctorCodexSkillsCheck:
    def test_passes_when_skill_installed(self, filigree_project: Path) -> None:
        install_codex_skills(filigree_project)
        results = run_doctor(filigree_project)
        check = next((r for r in results if r.name == "Codex skills"), None)
        assert check is not None
        assert check.passed

    def test_fails_when_skill_missing(self, filigree_project: Path) -> None:
        results = run_doctor(filigree_project)
        check = next((r for r in results if r.name == "Codex skills"), None)
        assert check is not None
        assert not check.passed
        assert "not found" in check.message
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install.py::TestDoctorCodexSkillsCheck -v`
Expected: FAIL — no "Codex skills" check in results

**Step 3: Add doctor check**

In `run_doctor()` in `install.py`, after check 9 (Claude Code skills, around line 981), add:

```python
    # 9b. Check Codex skills
    codex_skill_md = (filigree_dir.parent) / ".agents" / "skills" / SKILL_NAME / SKILL_MARKER
    if codex_skill_md.exists():
        results.append(CheckResult("Codex skills", True, f"{SKILL_NAME} skill installed"))
    else:
        results.append(
            CheckResult(
                "Codex skills",
                False,
                f"{SKILL_NAME} skill not found in .agents/skills/",
                fix_hint="Run: filigree install --codex-skills",
            )
        )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install.py::TestDoctorCodexSkillsCheck -v`
Expected: All 2 PASS

**Step 5: Commit**

```bash
git add src/filigree/install.py tests/test_install.py
git commit -m "feat(doctor): add Codex skills health check"
```

---

### Task 3: Wire `--codex-skills` into CLI

**Files:**
- Modify: `src/filigree/cli.py:788-900` (install command)
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

In `tests/test_cli.py`, find the install tests and add:

```python
    def test_install_codex_skills_flag(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, project = cli_in_project
        result = runner.invoke(cli, ["install", "--codex-skills"])
        assert result.exit_code == 0
        assert "Codex skills" in result.output
        skill_md = project / ".agents" / "skills" / "filigree-workflow" / "SKILL.md"
        assert skill_md.exists()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::TestInstallCommand::test_install_codex_skills_flag -v` (adjust class name as needed)
Expected: FAIL — no such option `--codex-skills`

**Step 3: Add CLI flag and wiring**

In `src/filigree/cli.py`, modify the `install` command:

1. Add option after `--skills`:
```python
@click.option("--codex-skills", "codex_skills_only", is_flag=True, help="Install Codex skills only")
```

2. Add parameter to function signature:
```python
def install(
    claude_code: bool,
    codex: bool,
    claude_md: bool,
    agents_md: bool,
    gitignore: bool,
    hooks_only: bool,
    skills_only: bool,
    codex_skills_only: bool,
    mode: str | None,
) -> None:
```

3. Update `install_all` check:
```python
install_all = not any([claude_code, codex, claude_md, agents_md, gitignore, hooks_only, skills_only, codex_skills_only])
```

4. Add the import for `install_codex_skills` to the lazy import block:
```python
from filigree.install import (
    ensure_gitignore,
    inject_instructions,
    install_claude_code_hooks,
    install_claude_code_mcp,
    install_codex_mcp,
    install_codex_skills,
    install_skills,
)
```

5. Add install logic after the Claude Code skills block:
```python
    if install_all or codex or codex_skills_only:
        ok, msg = install_codex_skills(project_root)
        results.append(("Codex skills", ok, msg))
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -k "codex_skills" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/cli.py tests/test_cli.py
git commit -m "feat(cli): add --codex-skills flag to install command"
```

---

### Task 4: Add session-context hint to instructions template

**Files:**
- Modify: `src/filigree/data/instructions.md`
- Test: `tests/test_install.py`

**Step 1: Write the failing test**

```python
class TestInstructionsSessionHint:
    def test_instructions_contain_session_context_hint(self) -> None:
        from filigree.install import _instructions_text
        text = _instructions_text()
        assert "filigree session-context" in text
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_install.py::TestInstructionsSessionHint -v`
Expected: FAIL — `filigree session-context` not in instructions text

**Step 3: Add hint to instructions template**

In `src/filigree/data/instructions.md`, add after the "### Workflow" section (before "### Priority Scale"):

```markdown
### Session Start
When beginning a new session, run `filigree session-context` to load project
snapshot (ready work, in-progress items, critical path). This provides the
context needed to pick up where the previous session left off.
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_install.py::TestInstructionsSessionHint -v`
Expected: PASS

**Step 5: Rebuild installed instructions (CLAUDE.md)**

Since the instructions hash changes, `filigree install --claude-md` and `--agents-md` will pick up the new content. No code change needed — the versioned marker handles this.

**Step 6: Commit**

```bash
git add src/filigree/data/instructions.md tests/test_install.py
git commit -m "feat(instructions): add session-context hint for Codex agents"
```

---

### Task 5: Run full test suite and lint

**Step 1: Run linter**

Run: `uv run ruff check src/filigree/install.py src/filigree/cli.py tests/test_install.py tests/test_cli.py`
Expected: No errors

**Step 2: Run formatter check**

Run: `uv run ruff format --check src/filigree/install.py src/filigree/cli.py tests/test_install.py tests/test_cli.py`
Expected: No formatting issues

**Step 3: Run mypy**

Run: `uv run mypy src/filigree/install.py src/filigree/cli.py`
Expected: No type errors

**Step 4: Run full test suite**

Run: `uv run pytest --tb=short`
Expected: All tests pass

**Step 5: Verify doctor output**

Run: `filigree doctor --verbose`
Expected: "Codex skills" check appears in output

**Step 6: Verify install output**

Run: `filigree install --codex-skills`
Expected: "Codex skills: Installed skill pack to .agents/skills/filigree-workflow"
