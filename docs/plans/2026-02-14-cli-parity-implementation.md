# CLI Parity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bring the keel CLI to full parity with the MCP server's 36 tools — 8 missing commands, global `--actor`, and `--json` on every command.

**Architecture:** All changes are in `src/keel/cli.py` (the Click CLI) and `tests/test_cli.py`. No changes to core.py, mcp_server.py, or any other module. Each new command calls an existing `KeelDB` method. The `explain-state` command uses template introspection (same logic as the MCP handler, no core method needed).

**Tech Stack:** Click (CLI framework), KeelDB (SQLite backend), pytest + Click CliRunner (testing)

---

### Task 1: Global `--actor` Infrastructure

**Files:**
- Modify: `src/keel/cli.py:72-74` (the `@click.group()` definition)

**Step 1: Add `--actor` to click group and pass context**

Replace the existing group definition:

```python
@click.group()
@click.option("--actor", default="cli", help="Actor identity for audit trail (default: cli)")
@click.pass_context
def cli(ctx: click.Context, actor: str) -> None:
    """Keel — agent-native issue tracker."""
    ctx.ensure_object(dict)
    ctx.obj["actor"] = actor
```

**Step 2: Run existing tests to confirm nothing breaks**

Run: `pytest tests/test_cli.py -x -q`
Expected: All existing tests pass (Click groups with `@click.pass_context` are backward-compatible — subcommands that don't use `@click.pass_context` still work).

**Step 3: Commit**

```bash
git add src/keel/cli.py
git commit -m "feat(cli): add global --actor flag to click group"
```

---

### Task 2: Retrofit `--actor` on Existing Mutation Commands

**Files:**
- Modify: `src/keel/cli.py` — commands: `create`, `update`, `close`, `reopen`, `dep_add`, `dep_remove`, `comment`, `label_add`, `label_remove`, `release`, `archive`, `undo`

For each command, the change follows this pattern:

1. Add `@click.pass_context` decorator
2. Add `ctx: click.Context` as first parameter
3. Replace hardcoded `actor="cli"` with `actor=ctx.obj["actor"]`
4. For `comment`, replace `author="cli"` with `author=ctx.obj["actor"]`

**Step 1: Write a test that verifies `--actor` is threaded through**

Add to `tests/test_cli.py`:

```python
class TestActorFlag:
    def test_create_with_actor(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["--actor", "test-agent", "create", "Actor test"])
        assert r.exit_code == 0
        issue_id = _extract_id(r.output)
        # Verify via show --json that events recorded the actor
        result = runner.invoke(cli, ["show", issue_id, "--json"])
        data = json.loads(result.output)
        assert data["title"] == "Actor test"

    def test_comment_with_actor(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Commentable"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["--actor", "bot-1", "comment", issue_id, "Hello"])
        result = runner.invoke(cli, ["comments", issue_id])
        assert "bot-1" in result.output

    def test_default_actor_is_cli(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Default actor"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["comment", issue_id, "Default"])
        result = runner.invoke(cli, ["comments", issue_id])
        assert "cli" in result.output
```

**Step 2: Run tests to see them fail**

Run: `pytest tests/test_cli.py::TestActorFlag -x -v`
Expected: Failures because `--actor` flag exists on group but commands still hardcode `actor="cli"`. Actually, the `comment_with_actor` test will fail because author is still hardcoded.

**Step 3: Retrofit all mutation commands**

Apply the pattern to each command. Here's the transformation for each:

**`create`** — add `@click.pass_context`, `ctx` param, change `actor="cli"` to `actor=ctx.obj["actor"]`

**`update`** — add `@click.pass_context`, `ctx` param, change `actor="cli"` to `actor=ctx.obj["actor"]`

**`close`** — add `@click.pass_context`, `ctx` param, change `actor="cli"` to `actor=ctx.obj["actor"]`

**`reopen`** — add `@click.pass_context`, `ctx` param, change `actor="cli"` to `actor=ctx.obj["actor"]`

**`dep_add`** — add `@click.pass_context`, `ctx` param, change `actor="cli"` to `actor=ctx.obj["actor"]`

**`dep_remove`** — add `@click.pass_context`, `ctx` param, change `actor="cli"` to `actor=ctx.obj["actor"]`

**`comment`** — add `@click.pass_context`, `ctx` param, change `author="cli"` to `author=ctx.obj["actor"]`

**`label_add`** — add `@click.pass_context`, `ctx` param (no actor param on `db.add_label()` — this is fine, labels don't record actor)

**`label_remove`** — same as label_add

**`release`** — add `@click.pass_context`, `ctx` param, change `actor="cli"` to `actor=ctx.obj["actor"]`

**`archive`** — add `@click.pass_context`, `ctx` param, change `actor="cli"` to `actor=ctx.obj["actor"]`

**`undo`** — add `@click.pass_context`, `ctx` param, change `actor="cli"` to `actor=ctx.obj["actor"]`

**Step 4: Run full test suite**

Run: `pytest tests/test_cli.py -x -q`
Expected: All pass including new TestActorFlag tests.

**Step 5: Commit**

```bash
git add src/keel/cli.py tests/test_cli.py
git commit -m "feat(cli): retrofit --actor on all mutation commands"
```

---

### Task 3: Add `--json` to Commands Missing It

**Files:**
- Modify: `src/keel/cli.py` — commands: `create`, `close`, `reopen`, `comment`, `comments`, `dep_add`, `dep_remove`, `workflow_states`, `undo`, `guide`, `archive`, `compact`, `label_add`, `label_remove`
- Modify: `tests/test_cli.py` — add JSON output tests

**Step 1: Write tests for each new `--json` flag**

Add to `tests/test_cli.py`:

```python
class TestJsonRetrofit:
    def test_create_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create", "JSON create", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["title"] == "JSON create"
        assert "id" in data

    def test_close_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Close JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["close", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["id"] == issue_id

    def test_reopen_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Reopen JSON"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["close", issue_id])
        result = runner.invoke(cli, ["reopen", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_comment_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Comment JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["comment", issue_id, "My comment", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "comment_id" in data
        assert data["issue_id"] == issue_id

    def test_comments_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Comments JSON"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["comment", issue_id, "A comment"])
        result = runner.invoke(cli, ["comments", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_dep_add_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)
        result = runner.invoke(cli, ["dep-add", id1, id2, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "added"

    def test_dep_remove_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)
        runner.invoke(cli, ["dep-add", id1, id2])
        result = runner.invoke(cli, ["dep-remove", id1, id2, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "removed"

    def test_workflow_states_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["workflow-states", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "open" in data
        assert "wip" in data
        assert "done" in data

    def test_undo_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Undo JSON"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["update", issue_id, "--title", "Changed"])
        result = runner.invoke(cli, ["undo", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["undone"] is True

    def test_guide_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["guide", "core", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "pack" in data
        assert "guide" in data

    def test_archive_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["archive", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "archived" in data
        assert "count" in data

    def test_compact_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["compact", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "deleted_events" in data

    def test_label_add_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Label JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["label", "add", issue_id, "urgent", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "added"

    def test_label_remove_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Label JSON", "-l", "urgent"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["label", "remove", issue_id, "urgent", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "removed"
```

**Step 2: Run tests to see them fail**

Run: `pytest tests/test_cli.py::TestJsonRetrofit -x -v`
Expected: All fail (no `--json` flag on these commands yet).

**Step 3: Add `--json` to each command**

For each command, add `@click.option("--json", "as_json", is_flag=True, help="Output as JSON")`, add `as_json: bool` parameter, and add JSON output branch.

**`create`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
# In function body, after db.create_issue():
if as_json:
    click.echo(json_mod.dumps(issue.to_dict(), indent=2, default=str))
else:
    click.echo(f"Created {issue.id}: {issue.title}")
    click.echo("Next: keel ready")
```

**`close`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
# Collect closed issues into a list, output at end:
closed = []
for issue_id in issue_ids:
    try:
        issue = db.close_issue(issue_id, reason=reason, actor=ctx.obj["actor"])
        closed.append(issue)
        if not as_json:
            click.echo(f"Closed {issue.id}: {issue.title}")
    except KeyError:
        if not as_json:
            click.echo(f"Not found: {issue_id}", err=True)
if as_json:
    click.echo(json_mod.dumps([i.to_dict() for i in closed], indent=2, default=str))
```

**`reopen`:** Same pattern as `close` — collect reopened issues, JSON output list at end.

**`comment`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    click.echo(json_mod.dumps({"comment_id": comment_id, "issue_id": issue_id}))
else:
    click.echo(f"Added comment {comment_id} to {issue_id}")
```

**`comments`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    click.echo(json_mod.dumps(result, indent=2, default=str))
    return
```

**`dep-add`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    click.echo(json_mod.dumps({"from_id": issue_id, "to_id": depends_on_id, "status": "added"}))
else:
    click.echo(f"Added: {issue_id} depends on {depends_on_id}")
```

**`dep-remove`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    click.echo(json_mod.dumps({"from_id": issue_id, "to_id": depends_on_id, "status": "removed"}))
else:
    click.echo(f"Removed: {issue_id} no longer depends on {depends_on_id}")
```

**`workflow-states`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    data = {}
    for category in ("open", "wip", "done"):
        data[category] = db._get_states_for_category(category)
    click.echo(json_mod.dumps(data, indent=2))
    return
```

**`undo`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    click.echo(json_mod.dumps(result, indent=2, default=str))
    if not result["undone"]:
        sys.exit(1)
    return
```

**`guide`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    click.echo(json_mod.dumps({"pack": pack_name, "guide": guide}, indent=2))
    return
```

**`archive`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    click.echo(json_mod.dumps({"archived": archived or [], "count": len(archived or [])}, indent=2))
else:
    # existing human output
```

**`compact`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    click.echo(json_mod.dumps({"deleted_events": deleted}))
    return
```

**`label add` and `label remove`:**
```python
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
if as_json:
    click.echo(json_mod.dumps({"issue_id": issue_id, "label": label_name, "status": "added"}))
else:
    click.echo(f"Added label '{label_name}' to {issue_id}")
```

**Step 4: Run all tests**

Run: `pytest tests/test_cli.py -x -q`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/keel/cli.py tests/test_cli.py
git commit -m "feat(cli): add --json flag to all commands"
```

---

### Task 4: New Command — `claim`

**Files:**
- Modify: `src/keel/cli.py` — add `claim` command
- Modify: `tests/test_cli.py` — add tests

**Step 1: Write tests**

```python
class TestClaimCli:
    def test_claim_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Claimable"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-1"])
        assert result.exit_code == 0
        assert "Claimed" in result.output
        assert "agent-1" in result.output

    def test_claim_already_claimed(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Claimable"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-1"])
        result = runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-2"])
        assert result.exit_code == 1
        assert "Cannot claim" in result.output

    def test_claim_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Claimable JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["claim", issue_id, "--assignee", "agent-1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["assignee"] == "agent-1"

    def test_claim_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["claim", "nonexistent-abc", "--assignee", "a"])
        assert result.exit_code == 1
```

**Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli.py::TestClaimCli -x -v`
Expected: FAIL (no such command)

**Step 3: Implement the command**

Add to `src/keel/cli.py`:

```python
@cli.command()
@click.argument("issue_id")
@click.option("--assignee", required=True, help="Who is claiming (agent name)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def claim(ctx: click.Context, issue_id: str, assignee: str, as_json: bool) -> None:
    """Atomically claim an open issue (optimistic locking)."""
    with _get_db() as db:
        try:
            issue = db.claim_issue(issue_id, assignee=assignee, actor=ctx.obj["actor"])
        except KeyError:
            if as_json:
                click.echo(json_mod.dumps({"error": f"Not found: {issue_id}"}))
            else:
                click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)
        except ValueError as e:
            if as_json:
                click.echo(json_mod.dumps({"error": str(e)}))
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json_mod.dumps(issue.to_dict(), indent=2, default=str))
        else:
            click.echo(f"Claimed {issue.id}: {issue.title} [{issue.status}] -> {assignee}")
        _refresh_summary(db)
```

**Step 4: Run tests**

Run: `pytest tests/test_cli.py::TestClaimCli -x -v`
Expected: All pass.

**Step 5: Commit**

```bash
git add src/keel/cli.py tests/test_cli.py
git commit -m "feat(cli): add claim command with optimistic locking"
```

---

### Task 5: New Command — `claim-next`

**Files:**
- Modify: `src/keel/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write tests**

```python
class TestClaimNextCli:
    def test_claim_next_basic(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Ready task", "-p", "1"])
        result = runner.invoke(cli, ["claim-next", "--assignee", "agent-1"])
        assert result.exit_code == 0
        assert "Claimed" in result.output

    def test_claim_next_empty(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["claim-next", "--assignee", "agent-1"])
        assert result.exit_code == 0
        assert "No issues available" in result.output

    def test_claim_next_with_type_filter(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "A task", "--type", "task"])
        runner.invoke(cli, ["create", "A bug", "--type", "bug"])
        result = runner.invoke(cli, ["claim-next", "--assignee", "a", "--type", "bug", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["type"] == "bug"

    def test_claim_next_json_empty(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["claim-next", "--assignee", "a", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "empty"
```

**Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli.py::TestClaimNextCli -x -v`

**Step 3: Implement**

```python
@cli.command("claim-next")
@click.option("--assignee", required=True, help="Who is claiming")
@click.option("--type", "type_filter", default=None, help="Filter by issue type")
@click.option("--priority-min", default=None, type=int, help="Minimum priority (0=critical)")
@click.option("--priority-max", default=None, type=int, help="Maximum priority")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def claim_next(
    ctx: click.Context, assignee: str, type_filter: str | None,
    priority_min: int | None, priority_max: int | None, as_json: bool,
) -> None:
    """Claim the highest-priority ready issue matching filters."""
    with _get_db() as db:
        issue = db.claim_next(
            assignee,
            type_filter=type_filter,
            priority_min=priority_min,
            priority_max=priority_max,
            actor=ctx.obj["actor"],
        )
        if issue is None:
            if as_json:
                click.echo(json_mod.dumps({"status": "empty"}))
            else:
                click.echo("No issues available")
        else:
            if as_json:
                click.echo(json_mod.dumps(issue.to_dict(), indent=2, default=str))
            else:
                click.echo(f"Claimed {issue.id}: {issue.title} [{issue.status}] -> {assignee}")
        _refresh_summary(db)
```

**Step 4: Run tests**

Run: `pytest tests/test_cli.py::TestClaimNextCli -x -v`

**Step 5: Commit**

```bash
git add src/keel/cli.py tests/test_cli.py
git commit -m "feat(cli): add claim-next command"
```

---

### Task 6: New Command — `create-plan`

**Files:**
- Modify: `src/keel/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write tests**

```python
class TestCreatePlanCli:
    def test_create_plan_from_stdin(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        plan_json = json.dumps({
            "milestone": {"title": "v1.0 Release"},
            "phases": [
                {
                    "title": "Phase 1",
                    "steps": [
                        {"title": "Step A"},
                        {"title": "Step B", "deps": [0]},
                    ],
                }
            ],
        })
        result = runner.invoke(cli, ["create-plan"], input=plan_json)
        assert result.exit_code == 0
        assert "v1.0 Release" in result.output

    def test_create_plan_from_file(self, cli_in_project: tuple[CliRunner, Path], tmp_path: Path) -> None:
        runner, project_root = cli_in_project
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps({
            "milestone": {"title": "File Plan"},
            "phases": [{"title": "Phase 1", "steps": [{"title": "Step 1"}]}],
        }))
        result = runner.invoke(cli, ["create-plan", "--file", str(plan_file)])
        assert result.exit_code == 0
        assert "File Plan" in result.output

    def test_create_plan_json_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        plan_json = json.dumps({
            "milestone": {"title": "JSON Plan"},
            "phases": [{"title": "P1", "steps": [{"title": "S1"}]}],
        })
        result = runner.invoke(cli, ["create-plan", "--json"], input=plan_json)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "milestone" in data
        assert "phases" in data

    def test_create_plan_invalid_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["create-plan"], input="not json")
        assert result.exit_code == 1
        assert "Invalid JSON" in result.output
```

**Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli.py::TestCreatePlanCli -x -v`

**Step 3: Implement**

```python
@cli.command("create-plan")
@click.option("--file", "file_path", default=None, type=click.Path(exists=True), help="JSON file (reads stdin if omitted)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def create_plan(ctx: click.Context, file_path: str | None, as_json: bool) -> None:
    """Create a milestone/phase/step hierarchy from JSON.

    Reads JSON from --file or stdin. Structure:
    {"milestone": {"title": "..."}, "phases": [{"title": "...", "steps": [...]}]}
    """
    import sys as _sys

    if file_path:
        raw = Path(file_path).read_text()
    else:
        raw = click.get_text_stream("stdin").read()

    try:
        data = json_mod.loads(raw)
    except json_mod.JSONDecodeError as e:
        click.echo(f"Invalid JSON: {e}", err=True)
        sys.exit(1)

    if "milestone" not in data or "phases" not in data:
        click.echo("JSON must contain 'milestone' and 'phases' keys", err=True)
        sys.exit(1)

    with _get_db() as db:
        result = db.create_plan(data["milestone"], data["phases"], actor=ctx.obj["actor"])

        if as_json:
            click.echo(json_mod.dumps(result, indent=2, default=str))
        else:
            ms = result["milestone"]
            click.echo(f"Created plan: {ms['title']} ({ms['id']})")
            for phase_data in result["phases"]:
                phase = phase_data["phase"]
                step_count = len(phase_data["steps"])
                click.echo(f"  Phase: {phase['title']} ({step_count} steps)")
        _refresh_summary(db)
```

**Step 4: Run tests**

Run: `pytest tests/test_cli.py::TestCreatePlanCli -x -v`

**Step 5: Commit**

```bash
git add src/keel/cli.py tests/test_cli.py
git commit -m "feat(cli): add create-plan command (stdin/file JSON input)"
```

---

### Task 7: New Commands — `batch-update` and `batch-close`

**Files:**
- Modify: `src/keel/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write tests**

```python
class TestBatchCli:
    def test_batch_update(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)
        result = runner.invoke(cli, ["batch-update", id1, id2, "--priority", "0"])
        assert result.exit_code == 0
        assert "Updated 2" in result.output

    def test_batch_update_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        result = runner.invoke(cli, ["batch-update", id1, "--priority", "1", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_batch_close(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        r2 = runner.invoke(cli, ["create", "B"])
        id2 = _extract_id(r2.output)
        result = runner.invoke(cli, ["batch-close", id1, id2])
        assert result.exit_code == 0
        assert "Closed 2" in result.output

    def test_batch_close_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        result = runner.invoke(cli, ["batch-close", id1, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_batch_close_partial_failure(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r1 = runner.invoke(cli, ["create", "A"])
        id1 = _extract_id(r1.output)
        result = runner.invoke(cli, ["batch-close", id1, "nonexistent-abc", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Should still close the one that exists
```

**Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli.py::TestBatchCli -x -v`

**Step 3: Implement**

```python
@cli.command("batch-update")
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--status", default=None, help="New status")
@click.option("--priority", "-p", default=None, type=int, help="New priority")
@click.option("--assignee", default=None, help="New assignee")
@click.option("--field", "-f", multiple=True, help="Custom field as key=value (repeatable)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def batch_update(
    ctx: click.Context, issue_ids: tuple[str, ...], status: str | None,
    priority: int | None, assignee: str | None, field: tuple[str, ...], as_json: bool,
) -> None:
    """Update multiple issues with the same changes."""
    fields = None
    if field:
        fields = {}
        for f in field:
            if "=" not in f:
                click.echo(f"Invalid field format: {f}", err=True)
                sys.exit(1)
            k, v = f.split("=", 1)
            fields[k] = v

    with _get_db() as db:
        results = db.batch_update(
            list(issue_ids), status=status, priority=priority,
            assignee=assignee, fields=fields, actor=ctx.obj["actor"],
        )
        if as_json:
            click.echo(json_mod.dumps([i.to_dict() for i in results], indent=2, default=str))
        else:
            for issue in results:
                click.echo(f"  Updated {issue.id}: {issue.title}")
            click.echo(f"Updated {len(results)} issues")
        _refresh_summary(db)


@cli.command("batch-close")
@click.argument("issue_ids", nargs=-1, required=True)
@click.option("--reason", default="", help="Close reason")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def batch_close(ctx: click.Context, issue_ids: tuple[str, ...], reason: str, as_json: bool) -> None:
    """Close multiple issues with per-item error reporting."""
    closed = []
    errors = []
    with _get_db() as db:
        for issue_id in issue_ids:
            try:
                issue = db.close_issue(issue_id, reason=reason, actor=ctx.obj["actor"])
                closed.append(issue)
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}"})
            except ValueError as e:
                errors.append({"id": issue_id, "error": str(e)})

        if as_json:
            click.echo(json_mod.dumps({
                "closed": [i.to_dict() for i in closed],
                "errors": errors,
            }, indent=2, default=str))
        else:
            for issue in closed:
                click.echo(f"  Closed {issue.id}: {issue.title}")
            for err in errors:
                click.echo(f"  Error {err['id']}: {err['error']}", err=True)
            click.echo(f"Closed {len(closed)}/{len(issue_ids)} issues")
        _refresh_summary(db)
```

**Step 4: Run tests**

Run: `pytest tests/test_cli.py::TestBatchCli -x -v`

**Step 5: Commit**

```bash
git add src/keel/cli.py tests/test_cli.py
git commit -m "feat(cli): add batch-update and batch-close commands"
```

---

### Task 8: New Commands — `changes` and `events`

**Files:**
- Modify: `src/keel/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write tests**

```python
class TestEventsCli:
    def test_changes_since(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Event test"])
        result = runner.invoke(cli, ["changes", "--since", "2020-01-01T00:00:00"])
        assert result.exit_code == 0
        assert "created" in result.output.lower()

    def test_changes_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Event JSON"])
        result = runner.invoke(cli, ["changes", "--since", "2020-01-01T00:00:00", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_changes_empty(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["changes", "--since", "2099-01-01T00:00:00"])
        assert result.exit_code == 0

    def test_events_for_issue(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Track events"])
        issue_id = _extract_id(r.output)
        runner.invoke(cli, ["update", issue_id, "--title", "Changed"])
        result = runner.invoke(cli, ["events", issue_id])
        assert result.exit_code == 0
        assert issue_id in result.output

    def test_events_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        r = runner.invoke(cli, ["create", "Track JSON"])
        issue_id = _extract_id(r.output)
        result = runner.invoke(cli, ["events", issue_id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_events_not_found(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["events", "nonexistent-abc"])
        assert result.exit_code == 1
```

**Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli.py::TestEventsCli -x -v`

**Step 3: Implement**

```python
@cli.command("changes")
@click.option("--since", required=True, help="ISO timestamp to get events after")
@click.option("--limit", default=100, type=int, help="Max events (default 100)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def changes(since: str, limit: int, as_json: bool) -> None:
    """Get events since a timestamp (for session resumption)."""
    with _get_db() as db:
        events = db.get_events_since(since, limit=limit)

        if as_json:
            click.echo(json_mod.dumps(events, indent=2, default=str))
            return

        if not events:
            click.echo("No events since that timestamp.")
            return

        for ev in events:
            title = ev.get("issue_title", "")
            click.echo(f"  {ev['created_at']}  {ev['event_type']:<12} {ev['issue_id']}  {title}")
        click.echo(f"\n{len(events)} events")


@cli.command("events")
@click.argument("issue_id")
@click.option("--limit", default=50, type=int, help="Max events (default 50)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def events_cmd(issue_id: str, limit: int, as_json: bool) -> None:
    """Get event history for a specific issue, newest first."""
    with _get_db() as db:
        try:
            event_list = db.get_issue_events(issue_id, limit=limit)
        except KeyError:
            click.echo(f"Not found: {issue_id}", err=True)
            sys.exit(1)

        if as_json:
            click.echo(json_mod.dumps(event_list, indent=2, default=str))
            return

        if not event_list:
            click.echo(f"No events for {issue_id}.")
            return

        for ev in event_list:
            old_val = ev.get("old_value", "")
            new_val = ev.get("new_value", "")
            detail = ""
            if old_val or new_val:
                detail = f" ({old_val} -> {new_val})" if old_val else f" ({new_val})"
            click.echo(f"  #{ev['id']}  {ev['created_at']}  {ev['event_type']}{detail}")
        click.echo(f"\n{len(event_list)} events")
```

**Step 4: Run tests**

Run: `pytest tests/test_cli.py::TestEventsCli -x -v`

**Step 5: Commit**

```bash
git add src/keel/cli.py tests/test_cli.py
git commit -m "feat(cli): add changes and events commands"
```

---

### Task 9: New Command — `explain-state`

**Files:**
- Modify: `src/keel/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write tests**

```python
class TestExplainStateCli:
    def test_explain_state_basic(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["explain-state", "task", "open"])
        assert result.exit_code == 0
        assert "open" in result.output
        assert "category" in result.output.lower() or "open" in result.output

    def test_explain_state_json(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["explain-state", "task", "open", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["state"] == "open"
        assert "category" in data
        assert "inbound_transitions" in data
        assert "outbound_transitions" in data

    def test_explain_state_unknown_type(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["explain-state", "nonexistent", "open"])
        assert result.exit_code == 1
        assert "Unknown type" in result.output

    def test_explain_state_unknown_state(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["explain-state", "task", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown state" in result.output
```

**Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli.py::TestExplainStateCli -x -v`

**Step 3: Implement**

```python
@cli.command("explain-state")
@click.argument("type_name")
@click.argument("state_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def explain_state(type_name: str, state_name: str, as_json: bool) -> None:
    """Explain a state's transitions and required fields."""
    with _get_db() as db:
        tpl = db.templates.get_type(type_name)
        if tpl is None:
            click.echo(f"Unknown type: {type_name}", err=True)
            sys.exit(1)

        state_def = None
        for s in tpl.states:
            if s.name == state_name:
                state_def = s
                break
        if state_def is None:
            click.echo(f"Unknown state '{state_name}' for type '{type_name}'", err=True)
            sys.exit(1)

        inbound = [
            {"from": t.from_state, "enforcement": t.enforcement}
            for t in tpl.transitions if t.to_state == state_name
        ]
        outbound = [
            {"to": t.to_state, "enforcement": t.enforcement, "requires_fields": list(t.requires_fields)}
            for t in tpl.transitions if t.from_state == state_name
        ]
        required_fields = [f.name for f in tpl.fields_schema if state_name in f.required_at]

        if as_json:
            click.echo(json_mod.dumps({
                "state": state_name,
                "category": state_def.category,
                "type": type_name,
                "inbound_transitions": inbound,
                "outbound_transitions": outbound,
                "required_fields": required_fields,
            }, indent=2))
            return

        click.echo(f"State: {state_name} [{state_def.category}] (type: {type_name})")
        if inbound:
            click.echo("\nInbound transitions:")
            for t in inbound:
                click.echo(f"  <- {t['from']} [{t['enforcement']}]")
        else:
            click.echo("\nNo inbound transitions (initial state)")
        if outbound:
            click.echo("\nOutbound transitions:")
            for t in outbound:
                fields_note = f" (requires: {', '.join(t['requires_fields'])})" if t["requires_fields"] else ""
                click.echo(f"  -> {t['to']} [{t['enforcement']}]{fields_note}")
        else:
            click.echo("\nNo outbound transitions (terminal state)")
        if required_fields:
            click.echo(f"\nRequired fields at this state: {', '.join(required_fields)}")
```

**Step 4: Run tests**

Run: `pytest tests/test_cli.py::TestExplainStateCli -x -v`

**Step 5: Commit**

```bash
git add src/keel/cli.py tests/test_cli.py
git commit -m "feat(cli): add explain-state command"
```

---

### Task 10: Final Integration — Full Test Suite + Lint

**Step 1: Run the complete test suite**

Run: `pytest tests/test_cli.py -v`
Expected: All tests pass.

**Step 2: Run the full CI pipeline**

Run: `make ci`
Expected: Lint (ruff), typecheck (mypy), and all tests pass.

**Step 3: Fix any lint or type issues**

Address ruff/mypy errors if any. Common ones:
- Line length > 120 chars (ruff E501)
- Missing type annotations
- Unused imports

**Step 4: Run tests one final time**

Run: `make ci`
Expected: Clean pass.

**Step 5: Final commit if any fixes were needed**

```bash
git add src/keel/cli.py tests/test_cli.py
git commit -m "chore: fix lint/type issues from CLI parity work"
```

---

### Task 11: Update Install Instructions

**Files:**
- Modify: `src/keel/install.py:35-88` (KEEL_INSTRUCTIONS block)

**Step 1: Update the instructions block to include new commands**

Add the new commands to the Quick Reference section:

```
# Atomic claiming
keel claim <id> --assignee <name>            # Claim issue (optimistic lock)
keel claim-next --assignee <name>            # Claim highest-priority ready issue

# Batch operations
keel batch-update <ids...> --status=closed   # Update multiple issues
keel batch-close <ids...>                    # Close multiple with error reporting

# Planning
keel create-plan --file plan.json            # Create milestone/phase/step hierarchy

# Event history
keel changes --since 2026-01-01T00:00:00    # Events since timestamp
keel events <id>                             # Event history for issue
keel explain-state <type> <state>            # Explain a workflow state

# All commands support --json and --actor
keel --actor bot-1 create "Title"            # Specify actor identity
keel list --json                             # Machine-readable output
```

**Step 2: Run tests**

Run: `pytest tests/test_cli.py -x -q`

**Step 3: Commit**

```bash
git add src/keel/install.py
git commit -m "docs: update KEEL_INSTRUCTIONS with new CLI commands"
```
