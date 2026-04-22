## Summary
`api-misuse`: `filigree server start --port` accepts invalid port numbers, so `--port 0` is silently ignored and out-of-range values are forwarded into a broken daemon launch.

## Severity
- Severity: minor
- Priority: P2

## Evidence
[cli_commands/server.py](/home/john/filigree/src/filigree/cli_commands/server.py:44) only validates `--port` as a bare integer:

```python
@click.option("--port", default=None, type=int, help="Override port")
def server_start(port: int | None) -> None:
    ...
    result = start_daemon(port=port)
```

That means values like `0`, `-1`, and `65536` are accepted by the CLI and passed straight through at [cli_commands/server.py](/home/john/filigree/src/filigree/cli_commands/server.py:49).

Downstream, [server.py](/home/john/filigree/src/filigree/server.py:247) treats `0` as "no override" because it uses truthiness instead of an explicit `None` check:

```python
config = read_server_config()
daemon_port = port or config.port
if config.port != daemon_port:
    config.port = daemon_port
    write_server_config(config)
```

So `filigree server start --port 0` does not fail fast; it silently falls back to the stored/default port.

For other invalid integers, the same code persists the bad value and then tries to launch the daemon with it at [server.py](/home/john/filigree/src/filigree/server.py:259):

```python
proc = subprocess.Popen(
    [*filigree_cmd, "dashboard", "--no-browser", "--server-mode", "--port", str(daemon_port)],
    ...
)
```

That turns a CLI validation miss into a runtime startup failure.

## Root Cause Hypothesis
The CLI boundary in the target file validates only "is this an integer?" instead of "is this a valid TCP port?". Because `server_start()` forwards the raw value unchanged, backend logic has to interpret invalid ports after the fact, and currently does so inconsistently (`0` is ignored; other invalid values are propagated into process startup).

## Suggested Fix
Change the target file to reject invalid ports at parse time, e.g. use `click.IntRange(1, 65535)` for `--port` in [cli_commands/server.py](/home/john/filigree/src/filigree/cli_commands/server.py:44). Add CLI tests for `0`, negative ports, and values above `65535`.

As defense in depth, `start_daemon()` should also switch from `port or config.port` to `port if port is not None else config.port` and validate the range there too, but the primary fix belongs at this CLI entry point.