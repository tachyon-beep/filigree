"""Tests for ``filigree doctor`` schema-mismatch (v+1) handling.

When a project's DB schema is newer than the installed filigree (a
"forward" mismatch), ``filigree doctor`` must exit with code 3 — distinct
from the generic exit 1 used for other failed checks — and surface the
shared guidance text from
:mod:`filigree.install_support.version_marker`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, FiligreeDB, write_config
from filigree.db_schema import CURRENT_SCHEMA_VERSION
from filigree.install_support.doctor import run_doctor


@pytest.fixture
def v_plus_one_project(tmp_path: Path) -> Path:
    """Create a filigree project, then forcibly bump its DB schema to v+1.

    Returns the project root (parent of ``.filigree/``). Opening this DB
    via :class:`FiligreeDB.from_filigree_dir` would raise
    ``SchemaVersionMismatchError`` by design — the bump uses raw sqlite3
    on a closed connection to simulate the "newer filigree wrote this DB"
    condition for ``filigree doctor`` to detect.
    """
    filigree_dir = tmp_path / FILIGREE_DIR_NAME
    filigree_dir.mkdir()
    write_config(filigree_dir, {"prefix": "proj", "version": 1})

    db_path = filigree_dir / DB_FILENAME
    d = FiligreeDB(db_path, prefix="proj")
    d.initialize()
    d.close()

    # Forcibly bump user_version past the installed schema version.
    bumped = CURRENT_SCHEMA_VERSION + 1
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA user_version = {bumped}")
        conn.commit()
    finally:
        conn.close()

    return tmp_path


def test_doctor_cli_exits_3_on_forward_mismatch(
    v_plus_one_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``filigree doctor`` must exit 3 (not 1) and emit guidance text."""
    from filigree.cli_commands.admin import doctor

    monkeypatch.chdir(v_plus_one_project)
    runner = CliRunner()
    result = runner.invoke(doctor, [])

    assert result.exit_code == 3, f"expected exit 3 for forward schema mismatch, got {result.exit_code}\noutput:\n{result.output}"
    assert "Downgrade is not supported" in result.output


def test_run_doctor_sets_schema_mismatch_forward_code(
    v_plus_one_project: Path,
) -> None:
    """Structural contract: the v+1 CheckResult carries the typed code.

    Guards against a future refactor renaming the code string and
    silently regressing exit-code wiring (the CLI test only checks
    exit 3, which is downstream of this field).
    """
    results = run_doctor(project_root=v_plus_one_project)
    schema_results = [r for r in results if r.name == "Schema version"]
    assert schema_results, f"no Schema version check found in results: {results}"
    schema_result = schema_results[0]
    assert schema_result.passed is False
    assert schema_result.code == "schema_mismatch_forward"


# ---------------------------------------------------------------------------
# F2: Dashboard startup + per-project schema-mismatch handling
# ---------------------------------------------------------------------------


def test_dashboard_main_exits_3_on_forward_mismatch(
    v_plus_one_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``filigree dashboard`` (ethereal mode) must exit 3 with stderr
    guidance — no Python stack trace — when the project DB is v+1.

    Calls :func:`filigree.dashboard.main` directly; ``find_filigree_root``
    resolves to the fixture via ``monkeypatch.chdir``. uvicorn never gets
    a chance to start because :class:`SchemaVersionMismatchError` is
    caught at the ``from_filigree_dir`` call before app construction.
    """
    from filigree import dashboard

    monkeypatch.chdir(v_plus_one_project)
    with pytest.raises(SystemExit) as excinfo:
        dashboard.main(no_browser=True)

    assert excinfo.value.code == 3, f"expected exit 3 for forward schema mismatch, got {excinfo.value.code}"
    captured = capsys.readouterr()
    assert "Downgrade is not supported" in captured.err
    # Confirm we did not fall through to uvicorn / app construction
    assert "Filigree" not in captured.out or "Dashboard" not in captured.out


def test_mcp_server_warm_degraded_on_v_plus_one(
    v_plus_one_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP server stays warm on a v+1 DB:

    * ``_attempt_startup`` does not raise.
    * ``list_tools`` returns the full tool registry (no DB needed).
    * Every ``call_tool`` invocation returns a structured
      ``SCHEMA_MISMATCH`` envelope carrying the shared guidance text,
      regardless of the tool's normal arity / requirements.

    Tests three representative tools — a read (``get_issue``), a list
    (``list_issues``), and a write (``create_issue``) — to confirm the
    guard sits at the dispatcher entry, before any per-tool dispatch.
    """
    import asyncio

    import filigree.mcp_server as mcp_mod

    filigree_dir = v_plus_one_project / FILIGREE_DIR_NAME

    # Reset/restore module globals via monkeypatch so this test cannot
    # leak state into siblings even if it raises mid-flight.
    monkeypatch.setattr(mcp_mod, "db", None)
    monkeypatch.setattr(mcp_mod, "_filigree_dir", None)
    monkeypatch.setattr(mcp_mod, "_schema_mismatch", None)

    # The startup helper must catch SchemaVersionMismatchError and set
    # the flag rather than letting it escape.
    mcp_mod._attempt_startup(filigree_dir)
    assert mcp_mod.db is None, "db should remain unset on schema mismatch"
    assert mcp_mod._schema_mismatch is not None, "schema-mismatch flag must be set"
    assert mcp_mod._schema_mismatch.database == CURRENT_SCHEMA_VERSION + 1
    assert mcp_mod._schema_mismatch.installed == CURRENT_SCHEMA_VERSION

    # list_tools must still work — it touches no DB state.
    tools = asyncio.run(mcp_mod.list_tools())
    assert len(tools) > 0, "list_tools should expose the full registry"
    tool_names = {t.name for t in tools}
    assert {"get_issue", "list_issues", "create_issue"}.issubset(tool_names)

    # Every call_tool must short-circuit to SCHEMA_MISMATCH — exercise
    # a read, a list, and a write to prove the guard is dispatcher-wide,
    # not handler-local.
    import json as _json

    for tool_name, args in (
        ("get_issue", {"issue_id": "anything"}),
        ("list_issues", {}),
        ("create_issue", {"title": "x", "type": "task"}),
    ):
        result = asyncio.run(mcp_mod.call_tool(tool_name, args))
        assert len(result) == 1, f"{tool_name}: expected single TextContent reply"
        payload = _json.loads(result[0].text)
        assert payload["code"] == "SCHEMA_MISMATCH", f"{tool_name}: expected SCHEMA_MISMATCH envelope, got {payload}"
        assert "Downgrade is not supported" in payload["error"], f"{tool_name}: missing guidance text in error: {payload['error']}"

    # An unknown tool should also short-circuit to SCHEMA_MISMATCH —
    # the guard runs before the unknown-tool fast-path, so degraded mode
    # is the more informative signal for the client.
    result = asyncio.run(mcp_mod.call_tool("nonexistent_tool", {}))
    payload = _json.loads(result[0].text)
    assert payload["code"] == "SCHEMA_MISMATCH"


def test_mcp_server_logs_degraded_warning_on_v_plus_one(
    v_plus_one_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operators tailing the MCP server log must see a WARNING when the
    server starts in degraded mode — not learn about it only when a
    client invokes a tool.

    The warning is emitted by ``_log_startup_status`` (called from
    ``_run`` right after ``setup_logging``), so we drive it directly to
    avoid spinning up the async ``stdio_server`` loop. This still
    exercises the degraded-mode branch end-to-end on the helper.
    """
    import logging as _logging

    import filigree.mcp_server as mcp_mod

    filigree_dir = v_plus_one_project / FILIGREE_DIR_NAME

    monkeypatch.setattr(mcp_mod, "db", None)
    monkeypatch.setattr(mcp_mod, "_filigree_dir", None)
    monkeypatch.setattr(mcp_mod, "_schema_mismatch", None)

    mcp_mod._attempt_startup(filigree_dir)
    assert mcp_mod._schema_mismatch is not None

    logger = _logging.getLogger("filigree.mcp_server.test")
    with caplog.at_level(_logging.WARNING, logger=logger.name):
        mcp_mod._log_startup_status(logger)

    degraded_records = [r for r in caplog.records if r.message == "mcp_server_degraded"]
    assert degraded_records, f"expected mcp_server_degraded WARNING, got: {[r.message for r in caplog.records]}"
    rec = degraded_records[0]
    assert rec.levelno == _logging.WARNING
    # Structured fields must carry both schema versions so the operator
    # knows which side is ahead without grepping further.
    assert getattr(rec, "args_data", None) == {
        "installed": CURRENT_SCHEMA_VERSION,
        "database": CURRENT_SCHEMA_VERSION + 1,
    }


def test_mcp_server_log_startup_status_silent_on_clean_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Negative case: when the DB opens cleanly, ``_log_startup_status``
    must NOT emit the degraded warning. Guards against a future refactor
    flipping the guard's polarity and spamming the log on every start.
    """
    import logging as _logging

    import filigree.mcp_server as mcp_mod

    filigree_dir = tmp_path / FILIGREE_DIR_NAME
    filigree_dir.mkdir()
    write_config(filigree_dir, {"prefix": "ok", "version": 1})
    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix="ok")
    db.initialize()
    db.close()

    monkeypatch.setattr(mcp_mod, "db", None)
    monkeypatch.setattr(mcp_mod, "_filigree_dir", None)
    monkeypatch.setattr(mcp_mod, "_schema_mismatch", None)

    mcp_mod._attempt_startup(filigree_dir)
    assert mcp_mod._schema_mismatch is None

    logger = _logging.getLogger("filigree.mcp_server.test_clean")
    with caplog.at_level(_logging.WARNING, logger=logger.name):
        mcp_mod._log_startup_status(logger)

    assert not [r for r in caplog.records if r.message == "mcp_server_degraded"]


def test_dashboard_server_mode_returns_409_for_v_plus_one_project(
    v_plus_one_project: Path,
) -> None:
    """Server-mode lazy open: a v+1 project returns a structured 409
    SCHEMA_MISMATCH on the project-scoped route, NOT a 500 stack trace.

    Other projects in the same server are unaffected — verified by the
    explicit-key route returning the schema-mismatch envelope while the
    server itself stays up.
    """
    from fastapi.testclient import TestClient

    import filigree.dashboard as dash
    from filigree.dashboard import ProjectStore, create_app

    # Build a ProjectStore manually pointing at the v+1 fixture, bypassing
    # server.json — the fixture's ``.filigree/`` is the project record.
    store = ProjectStore()
    filigree_path = v_plus_one_project / FILIGREE_DIR_NAME
    store._projects = {"badproj": {"name": "badproj", "path": str(filigree_path)}}

    original_db = dash._db
    original_store = dash._project_store
    dash._db = None
    dash._project_store = store
    try:
        app = create_app(server_mode=True)
        client = TestClient(app, raise_server_exceptions=False)
        # Hit a project-scoped endpoint via /api/p/{key}/… — this triggers
        # ProjectStore.get_db which raises SchemaVersionMismatchError, which
        # the registered exception handler converts to 409 SCHEMA_MISMATCH.
        resp = client.get("/api/p/badproj/issues")

        assert resp.status_code == 409, f"expected 409, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["code"] == "SCHEMA_MISMATCH"
        assert "Downgrade is not supported" in body["error"]

        # Server is still alive — health check works
        health = client.get("/api/health")
        assert health.status_code == 200
    finally:
        dash._db = original_db
        dash._project_store = original_store
        store.close_all()
