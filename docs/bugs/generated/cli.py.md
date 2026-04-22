## Summary
`rule_id: api-misuse` Invalid `--actor` values are only validated inside the top-level group callback, so Click bypasses that validation on early-exit paths like `--help`, missing command, and unknown command.

## Severity
- Severity: minor
- Priority: P3

## Evidence
[src/filigree/cli.py](/home/john/filigree/src/filigree/cli.py:18) defines `--actor` as a plain option, and [src/filigree/cli.py](/home/john/filigree/src/filigree/cli.py:20) performs validation only inside `cli()`:

```python
@click.option("--actor", default="cli", help="Actor identity for audit trail (default: cli)")
@click.pass_context
def cli(ctx: click.Context, actor: str) -> None:
    ctx.ensure_object(dict)
    cleaned, err = sanitize_actor(actor)
    if err:
        raise click.BadParameter(err, param_hint="'--actor'")
```

That callback is not reached on several parser short-circuit paths. Reproduced against the current code with `CliRunner`:

```text
['--actor', '\n', '--help']   -> exit 0, help text printed
['--actor', '\n']             -> exit 2, "Missing command."
['--actor', '\n', 'nope']     -> exit 2, "No such command 'nope'."
['--actor', '\n', 'create', 'x'] -> exit 2, "Invalid value for '--actor'..."
```

So the same invalid actor is rejected only when Click gets far enough to invoke the group callback.

## Root Cause Hypothesis
`cli.py` assumes the group callback is the right place to validate a global option. In Click, group callbacks run after command resolution succeeds; eager exits and parse failures happen earlier, so `sanitize_actor()` never runs and the bad input is either silently accepted or masked by a different error.

## Suggested Fix
Move actor validation into the option itself so it runs during parsing on every invocation path. A Click option callback or custom `ParamType` is enough:

```python
def _validate_actor_option(ctx: click.Context, param: click.Parameter, value: str) -> str:
    cleaned, err = sanitize_actor(value)
    if err:
        raise click.BadParameter(err, param=param)
    return cleaned

@click.option("--actor", default="cli", callback=_validate_actor_option, help=...)
def cli(ctx: click.Context, actor: str) -> None:
    ctx.ensure_object(dict)
    ctx.obj["actor"] = actor
```

