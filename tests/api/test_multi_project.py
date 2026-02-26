"""Dashboard API tests — multi-project (server mode), ProjectStore, and routing."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import filigree.dashboard as dash_module
from filigree.core import FiligreeDB
from filigree.dashboard import ProjectStore, create_app
from tests.api.conftest import _create_project


class TestProjectStore:
    """Unit tests for ProjectStore."""

    def test_load_discovers_projects(self, project_store: ProjectStore) -> None:
        projects = project_store.list_projects()
        assert len(projects) == 2
        keys = {p["key"] for p in projects}
        assert keys == {"alpha", "bravo"}

    def test_get_db_returns_correct_db(self, project_store: ProjectStore) -> None:
        db = project_store.get_db("alpha")
        assert db.prefix == "alpha"
        db2 = project_store.get_db("bravo")
        assert db2.prefix == "bravo"

    def test_get_db_unknown_key_raises(self, project_store: ProjectStore) -> None:
        with pytest.raises(KeyError):
            project_store.get_db("nonexistent")

    def test_get_db_closes_connection_on_init_failure(self, project_store: ProjectStore) -> None:
        """Bug filigree-6128be: DB connection must be closed if initialize() fails."""
        from unittest.mock import patch

        with (
            patch.object(FiligreeDB, "initialize", side_effect=RuntimeError("migration exploded")),
            pytest.raises(RuntimeError, match="migration exploded"),
        ):
            project_store.get_db("alpha")

        # After failure, the key should NOT be cached — next call retries
        assert "alpha" not in project_store._dbs

    def test_reload_adds_new_project(self, project_store: ProjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        charlie_dir = _create_project(tmp_path, "proj-charlie", "charlie", 3)
        config_dir = tmp_path / ".config" / "filigree"

        # Read existing, add charlie
        existing = json.loads((config_dir / "server.json").read_text())
        existing["projects"][str(charlie_dir)] = {"prefix": "charlie"}
        (config_dir / "server.json").write_text(json.dumps(existing))

        diff = project_store.reload()
        assert "charlie" in diff["added"]
        assert len(diff["removed"]) == 0
        assert len(project_store.list_projects()) == 3

    def test_reload_removes_project(self, project_store: ProjectStore, tmp_path: Path) -> None:
        import json

        config_dir = tmp_path / ".config" / "filigree"
        existing = json.loads((config_dir / "server.json").read_text())
        # Remove bravo
        to_remove = [k for k, v in existing["projects"].items() if v["prefix"] == "bravo"]
        for k in to_remove:
            del existing["projects"][k]
        (config_dir / "server.json").write_text(json.dumps(existing))

        diff = project_store.reload()
        assert "bravo" in diff["removed"]
        assert len(project_store.list_projects()) == 1

    def test_reload_closes_removed_project_db_handles(self, project_store: ProjectStore, tmp_path: Path) -> None:
        import json

        bravo_db = project_store.get_db("bravo")
        assert "bravo" in project_store._dbs

        config_dir = tmp_path / ".config" / "filigree"
        existing = json.loads((config_dir / "server.json").read_text())
        to_remove = [k for k, v in existing["projects"].items() if v["prefix"] == "bravo"]
        for k in to_remove:
            del existing["projects"][k]
        (config_dir / "server.json").write_text(json.dumps(existing))

        diff = project_store.reload()

        assert "bravo" in diff["removed"]
        assert "bravo" not in project_store._dbs
        assert bravo_db._conn is None

    def test_reload_logs_db_close_error_at_warning(self, project_store: ProjectStore, tmp_path: Path) -> None:
        """Bug filigree-191611: reload must log DB close errors at warning, not debug."""
        import json
        from unittest.mock import patch

        # Force a DB handle to exist
        bravo_db = project_store.get_db("bravo")
        assert "bravo" in project_store._dbs

        # Make close() raise
        original_close = bravo_db.close
        bravo_db.close = lambda: (_ for _ in ()).throw(RuntimeError("close failed"))  # type: ignore[assignment]

        # Remove bravo from config so reload tries to close it
        config_dir = tmp_path / ".config" / "filigree"
        existing = json.loads((config_dir / "server.json").read_text())
        to_remove = [k for k, v in existing["projects"].items() if v["prefix"] == "bravo"]
        for k in to_remove:
            del existing["projects"][k]
        (config_dir / "server.json").write_text(json.dumps(existing))

        with patch("filigree.dashboard.logger") as mock_logger:
            project_store.reload()

        mock_logger.warning.assert_called_once()
        assert "bravo" in str(mock_logger.warning.call_args)
        bravo_db.close = original_close  # type: ignore[assignment]

    def test_reload_evicts_db_handle_when_project_path_changes(self, project_store: ProjectStore, tmp_path: Path) -> None:
        import json

        bravo_db = project_store.get_db("bravo")
        old_db_path = bravo_db.db_path
        assert "bravo" in project_store._dbs

        replacement_dir = _create_project(tmp_path, "proj-bravo-replacement", "bravo", 4)
        config_dir = tmp_path / ".config" / "filigree"
        existing = json.loads((config_dir / "server.json").read_text())

        old_bravo_paths = [k for k, v in existing["projects"].items() if v["prefix"] == "bravo"]
        assert len(old_bravo_paths) == 1
        del existing["projects"][old_bravo_paths[0]]
        existing["projects"][str(replacement_dir)] = {"prefix": "bravo"}
        (config_dir / "server.json").write_text(json.dumps(existing))

        diff = project_store.reload()

        assert diff["added"] == []
        assert diff["removed"] == []
        assert "bravo" not in project_store._dbs
        assert bravo_db._conn is None

        reopened = project_store.get_db("bravo")
        assert reopened.db_path != old_db_path
        assert reopened.db_path.parent == replacement_dir

    def test_reload_corrupt_file_retains_state(self, project_store: ProjectStore, tmp_path: Path) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        before_keys = {p["key"] for p in project_store.list_projects()}

        (config_dir / "server.json").write_text("{bad json")
        diff = project_store.reload()

        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["error"]
        after_keys = {p["key"] for p in project_store.list_projects()}
        assert after_keys == before_keys

    def test_get_db_logs_and_reraises_open_failure(
        self,
        project_store: ProjectStore,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def _boom(_self: FiligreeDB) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(FiligreeDB, "initialize", _boom)
        with caplog.at_level("ERROR"), pytest.raises(RuntimeError, match="boom"):
            project_store.get_db("alpha")
        assert "Failed to open project DB" in caplog.text

    def test_load_skips_missing_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        # Register a path that doesn't exist
        server_json = {
            "port": 8377,
            "projects": {"/nonexistent/.filigree": {"prefix": "ghost"}},
        }
        (config_dir / "server.json").write_text(json.dumps(server_json))

        store = ProjectStore()
        store.load()
        assert len(store.list_projects()) == 0

    def test_empty_store_default_key(self) -> None:
        store = ProjectStore()
        assert store.default_key == ""

    def test_prefix_collision_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        config_dir = tmp_path / ".config" / "filigree"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
        monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")

        dir_a = _create_project(tmp_path, "dup-a", "samename", 1)
        dir_b = _create_project(tmp_path, "dup-b", "samename", 1)

        server_json = {
            "port": 8377,
            "projects": {
                str(dir_a): {"prefix": "samename"},
                str(dir_b): {"prefix": "samename"},
            },
        }
        (config_dir / "server.json").write_text(json.dumps(server_json))

        store = ProjectStore()
        with pytest.raises(ValueError, match="Prefix collision"):
            store.load()


class TestMultiProjectRouting:
    """Integration tests for multi-project URL routing."""

    async def test_default_project_issues(self, multi_client: AsyncClient) -> None:
        """GET /api/issues returns the default (first) project's issues."""
        resp = await multi_client.get("/api/issues")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1  # alpha has 1 issue

    async def test_scoped_project_issues(self, multi_client: AsyncClient) -> None:
        """GET /api/p/bravo/issues returns bravo's 2 issues."""
        resp = await multi_client.get("/api/p/bravo/issues")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_unknown_project_404(self, multi_client: AsyncClient) -> None:
        """GET /api/p/nonexistent/issues returns structured 404, not raw stack trace."""
        resp = await multi_client.get("/api/p/nonexistent/issues")
        assert resp.status_code == 404
        data = resp.json()
        assert "detail" in data
        assert "nonexistent" in data["detail"]

    async def test_empty_project_key_returns_404(self, multi_client: AsyncClient) -> None:
        """GET /api/p//issues with empty key does not match any route."""
        resp = await multi_client.get("/api/p//issues")
        assert resp.status_code == 404

    async def test_mcp_unknown_project_returns_404(self, multi_client: AsyncClient) -> None:
        """MCP should reject unknown project keys and never reuse a stale DB."""
        resp = await multi_client.get("/mcp/?project=nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == "project_not_found"

    async def test_stats_per_project(self, multi_client: AsyncClient) -> None:
        """Stats endpoint returns different prefixes per project."""
        alpha_resp = await multi_client.get("/api/p/alpha/stats")
        bravo_resp = await multi_client.get("/api/p/bravo/stats")
        assert alpha_resp.status_code == 200
        assert bravo_resp.status_code == 200
        assert alpha_resp.json()["prefix"] == "alpha"
        assert bravo_resp.json()["prefix"] == "bravo"

    async def test_empty_store_returns_503(self, tmp_path: Path) -> None:
        """A ProjectStore with 0 projects returns 503."""
        empty_store = ProjectStore()
        dash_module._project_store = empty_store
        try:
            app = create_app(server_mode=True)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/issues")
                assert resp.status_code == 503
        finally:
            dash_module._project_store = None

    async def test_empty_store_503_propagates_to_multiple_endpoints(self, tmp_path: Path) -> None:
        """503 from _get_db() propagates to all project-scoped endpoints when no projects registered."""
        empty_store = ProjectStore()
        dash_module._project_store = empty_store
        try:
            app = create_app(server_mode=True)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Verify multiple endpoints return 503 with correct detail message
                for endpoint in ("/api/issues", "/api/stats", "/api/graph"):
                    resp = await client.get(endpoint)
                    assert resp.status_code == 503, f"{endpoint} returned {resp.status_code}, expected 503"
                    body = resp.json()
                    assert "detail" in body, f"{endpoint} missing 'detail' key"
                    assert "No projects registered" in body["detail"]
        finally:
            dash_module._project_store = None


class TestMultiProjectManagement:
    """Tests for server-mode management endpoints."""

    async def test_list_projects(self, multi_client: AsyncClient) -> None:
        resp = await multi_client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        keys = {p["key"] for p in data}
        assert keys == {"alpha", "bravo"}

    async def test_reload_endpoint(self, multi_client: AsyncClient) -> None:
        resp = await multi_client.post("/api/reload")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "added" in data
        assert "removed" in data

    async def test_reload_endpoint_surfaces_errors(self, multi_client: AsyncClient, tmp_path: Path) -> None:
        config_dir = tmp_path / ".config" / "filigree"
        (config_dir / "server.json").write_text("{bad json")

        resp = await multi_client.post("/api/reload")
        assert resp.status_code == 409
        err = resp.json()["error"]
        assert err["code"] == "RELOAD_FAILED"
        assert "reload" in err["message"].lower()

    async def test_health_in_server_mode(self, multi_client: AsyncClient) -> None:
        resp = await multi_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["mode"] == "server"
        assert data["projects"] == 2


class TestEtherealProjectsEndpoint:
    """Backward-compat: /api/projects in ethereal mode."""

    async def test_projects_returns_single_with_empty_key(self, client: AsyncClient) -> None:
        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["key"] == ""
        assert data[0]["name"] == "test"  # from populated_db prefix
