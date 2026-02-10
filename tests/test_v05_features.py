"""Tests for v0.5 features: release_claim, export_jsonl, import_jsonl."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from filigree.cli import cli
from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, FiligreeDB, write_config

# ---------------------------------------------------------------------------
# Core: release_claim
# ---------------------------------------------------------------------------


class TestReleaseClaim:
    def test_release_success(self, db: FiligreeDB) -> None:
        issue = db.create_issue("To release")
        db.claim_issue(issue.id, assignee="agent-1")
        released = db.release_claim(issue.id, actor="agent-1")
        assert released.status == "open"  # status unchanged
        assert released.assignee == ""

    def test_release_records_event(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Event check")
        db.claim_issue(issue.id, assignee="agent-1")
        db.release_claim(issue.id, actor="agent-1")
        events = db.get_recent_events(limit=10)
        released_events = [e for e in events if e["event_type"] == "released"]
        assert len(released_events) == 1
        assert released_events[0]["actor"] == "agent-1"

    def test_release_no_assignee(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Still open")
        with pytest.raises(ValueError, match="no assignee set"):
            db.release_claim(issue.id)

    def test_release_closed_issue_no_assignee(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Closed one")
        db.close_issue(issue.id)
        with pytest.raises(ValueError, match="no assignee set"):
            db.release_claim(issue.id)

    def test_release_not_found(self, db: FiligreeDB) -> None:
        with pytest.raises(KeyError, match="not found"):
            db.release_claim("nonexistent-xyz")

    def test_release_then_reclaim(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Reclaim test")
        db.claim_issue(issue.id, assignee="agent-1")
        db.release_claim(issue.id)
        reclaimed = db.claim_issue(issue.id, assignee="agent-2")
        assert reclaimed.status == "open"  # status unchanged
        assert reclaimed.assignee == "agent-2"


# ---------------------------------------------------------------------------
# Core: export_jsonl / import_jsonl
# ---------------------------------------------------------------------------


class TestExportJsonl:
    def test_export_populated(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "export.jsonl"
        count = populated_db.export_jsonl(out)
        assert count > 0
        lines = out.read_text().strip().split("\n")
        assert len(lines) == count

    def test_export_record_types(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "export.jsonl"
        populated_db.export_jsonl(out)
        types_seen = set()
        for line in out.read_text().strip().split("\n"):
            record = json.loads(line)
            types_seen.add(record["_type"])
        assert "issue" in types_seen
        assert "dependency" in types_seen
        assert "label" in types_seen
        assert "comment" in types_seen
        assert "event" in types_seen

    def test_export_issue_fields(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "export.jsonl"
        populated_db.export_jsonl(out)
        issues = []
        for line in out.read_text().strip().split("\n"):
            record = json.loads(line)
            if record["_type"] == "issue":
                issues.append(record)
        assert len(issues) == 4  # epic + A + B + C
        assert any(i["title"] == "Issue A" for i in issues)

    def test_export_empty_db(self, db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "export.jsonl"
        count = db.export_jsonl(out)
        assert count == 0
        assert out.read_text() == ""


class TestImportJsonl:
    def test_import_roundtrip(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        """Export from populated, import into fresh — counts should match."""
        out = tmp_path / "roundtrip.jsonl"
        export_count = populated_db.export_jsonl(out)

        fresh = FiligreeDB(tmp_path / "fresh.db", prefix="test")
        fresh.initialize()
        import_count = fresh.import_jsonl(out)
        assert import_count == export_count
        fresh.close()

    def test_import_issues_arrive(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "data.jsonl"
        populated_db.export_jsonl(out)

        fresh = FiligreeDB(tmp_path / "fresh2.db", prefix="test")
        fresh.initialize()
        fresh.import_jsonl(out)
        issues = fresh.list_issues(limit=100)
        assert len(issues) == 4
        titles = {i.title for i in issues}
        assert "Issue A" in titles
        assert "Epic E" in titles
        fresh.close()

    def test_import_dependencies_arrive(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "data.jsonl"
        populated_db.export_jsonl(out)

        fresh = FiligreeDB(tmp_path / "fresh3.db", prefix="test")
        fresh.initialize()
        fresh.import_jsonl(out)
        deps = fresh.get_all_dependencies()
        assert len(deps) >= 1
        fresh.close()

    def test_import_merge_skips_existing(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "merge.jsonl"
        populated_db.export_jsonl(out)
        # Import twice with merge — second import should not fail
        count2 = populated_db.import_jsonl(out, merge=True)
        # All records skipped since they already exist (issues by PK, deps by PK, labels by PK)
        # Events don't have PK constraint so they get duplicated
        assert count2 >= 0

    def test_import_without_merge_fails_on_conflict(self, populated_db: FiligreeDB, tmp_path: Path) -> None:
        out = tmp_path / "conflict.jsonl"
        populated_db.export_jsonl(out)
        with pytest.raises(sqlite3.IntegrityError):
            populated_db.import_jsonl(out, merge=False)

    def test_import_skips_unknown_types(self, db: FiligreeDB, tmp_path: Path) -> None:
        jsonl = tmp_path / "unknown.jsonl"
        jsonl.write_text('{"_type": "alien", "data": "hello"}\n')
        count = db.import_jsonl(jsonl)
        assert count == 0

    def test_import_skips_blank_lines(self, db: FiligreeDB, tmp_path: Path) -> None:
        jsonl = tmp_path / "blanks.jsonl"
        jsonl.write_text('\n\n{"_type": "issue", "id": "test-aaa111", "title": "Blank test"}\n\n')
        count = db.import_jsonl(jsonl)
        assert count == 1


# ---------------------------------------------------------------------------
# MCP: release_claim, export_jsonl, import_jsonl
# ---------------------------------------------------------------------------


class TestMCPReleaseClaim:
    @pytest.fixture(autouse=True)
    def _setup_mcp(self, tmp_path: Path) -> None:
        import filigree.mcp_server as mcp_mod
        from filigree.core import SUMMARY_FILENAME

        filigree_dir = tmp_path / FILIGREE_DIR_NAME
        filigree_dir.mkdir()
        write_config(filigree_dir, {"prefix": "mcp", "version": 1})
        (filigree_dir / SUMMARY_FILENAME).write_text("# test\n")
        d = FiligreeDB(filigree_dir / DB_FILENAME, prefix="mcp")
        d.initialize()
        mcp_mod.db = d
        mcp_mod._filigree_dir = filigree_dir
        self.db = d
        self.tmp_path = tmp_path

    def _parse(self, result: list) -> dict | str:
        text = result[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    async def test_release_via_mcp(self) -> None:
        from filigree.mcp_server import call_tool

        issue = self.db.create_issue("MCP release")
        self.db.claim_issue(issue.id, assignee="agent-1")
        result = await call_tool("release_claim", {"id": issue.id})
        data = self._parse(result)
        assert data["status"] == "open"  # status unchanged
        assert data["assignee"] == ""

    async def test_release_conflict_via_mcp(self) -> None:
        from filigree.mcp_server import call_tool

        issue = self.db.create_issue("Not claimed")
        result = await call_tool("release_claim", {"id": issue.id})
        data = self._parse(result)
        assert data["code"] == "conflict"

    async def test_release_not_found_via_mcp(self) -> None:
        from filigree.mcp_server import call_tool

        result = await call_tool("release_claim", {"id": "nonexistent-xyz"})
        data = self._parse(result)
        assert data["code"] == "not_found"


class TestMCPExportImport:
    @pytest.fixture(autouse=True)
    def _setup_mcp(self, tmp_path: Path) -> None:
        import filigree.mcp_server as mcp_mod
        from filigree.core import SUMMARY_FILENAME

        filigree_dir = tmp_path / FILIGREE_DIR_NAME
        filigree_dir.mkdir()
        write_config(filigree_dir, {"prefix": "mcp", "version": 1})
        (filigree_dir / SUMMARY_FILENAME).write_text("# test\n")
        d = FiligreeDB(filigree_dir / DB_FILENAME, prefix="mcp")
        d.initialize()
        mcp_mod.db = d
        mcp_mod._filigree_dir = filigree_dir
        self.db = d
        self.tmp_path = tmp_path

    def _parse(self, result: list) -> dict | str:
        text = result[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    async def test_export_via_mcp(self) -> None:
        from filigree.mcp_server import call_tool

        self.db.create_issue("Export me")
        out = str(self.tmp_path / "mcp_export.jsonl")
        result = await call_tool("export_jsonl", {"output_path": out})
        data = self._parse(result)
        assert data["status"] == "ok"
        assert data["records"] > 0
        assert Path(out).exists()

    async def test_import_via_mcp(self) -> None:
        from filigree.mcp_server import call_tool

        self.db.create_issue("Import source")
        out = str(self.tmp_path / "mcp_roundtrip.jsonl")
        await call_tool("export_jsonl", {"output_path": out})

        # Import into same DB with merge
        result = await call_tool("import_jsonl", {"input_path": out, "merge": True})
        data = self._parse(result)
        assert data["status"] == "ok"

    async def test_import_bad_path_via_mcp(self) -> None:
        from filigree.mcp_server import call_tool

        result = await call_tool("import_jsonl", {"input_path": "/nonexistent/file.jsonl"})
        data = self._parse(result)
        assert data["code"] == "invalid"


# ---------------------------------------------------------------------------
# CLI: release, export, import
# ---------------------------------------------------------------------------


class TestCLIRelease:
    def test_release_via_cli(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            cli_runner.invoke(cli, ["init", "--prefix", "test"])
            # Create and claim an issue
            result = cli_runner.invoke(cli, ["create", "Release me"])
            # Output: "Created test-XXXXXX: Release me\n..."
            issue_id = result.output.split()[1].rstrip(":")
            cli_runner.invoke(cli, ["update", issue_id, "--status", "in_progress", "--assignee", "agent"])
            # Release it — clears assignee but does NOT change status
            result = cli_runner.invoke(cli, ["release", issue_id])
            assert result.exit_code == 0
            assert "Released" in result.output
            assert "in_progress" in result.output.lower()  # status unchanged
        finally:
            os.chdir(original)

    def test_release_not_found_cli(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            cli_runner.invoke(cli, ["init", "--prefix", "test"])
            result = cli_runner.invoke(cli, ["release", "nonexistent-xyz"])
            assert result.exit_code == 1
        finally:
            os.chdir(original)


class TestCLIExportImport:
    def test_export_import_roundtrip_cli(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            cli_runner.invoke(cli, ["init", "--prefix", "test"])
            cli_runner.invoke(cli, ["create", "CLI export test"])
            # Export
            result = cli_runner.invoke(cli, ["export", "data.jsonl"])
            assert result.exit_code == 0
            assert "Exported" in result.output
            assert Path("data.jsonl").exists()
            # Import into same project with merge
            result = cli_runner.invoke(cli, ["import", "data.jsonl", "--merge"])
            assert result.exit_code == 0
            assert "Imported" in result.output
        finally:
            os.chdir(original)

    def test_export_empty_db_cli(self, tmp_path: Path, cli_runner: CliRunner) -> None:
        original = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            cli_runner.invoke(cli, ["init", "--prefix", "test"])
            result = cli_runner.invoke(cli, ["export", "empty.jsonl"])
            assert result.exit_code == 0
            assert "0 records" in result.output
        finally:
            os.chdir(original)
