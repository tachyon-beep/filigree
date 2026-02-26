"""Fixtures for HTTP dashboard API tests (FastAPI)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import filigree.dashboard as dash_module
from filigree.core import DB_FILENAME, FiligreeDB, write_config
from filigree.dashboard import ProjectStore, create_app


@pytest.fixture
def dashboard_db(populated_db: FiligreeDB) -> FiligreeDB:
    """Use the populated_db fixture for dashboard tests.

    Enables check_same_thread=False so sync handlers run in FastAPI's threadpool.
    """
    populated_db._check_same_thread = False
    if populated_db._conn is not None:
        populated_db._conn.commit()
        populated_db._conn.close()
        populated_db._conn = None
    return populated_db


@pytest.fixture
async def client(dashboard_db: FiligreeDB, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """Create a test client backed by a single-project DB (ethereal mode)."""
    dash_module._db = dashboard_db
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._db = None


def _create_project(base: Path, name: str, prefix: str, issue_count: int) -> Path:
    """Helper: create a .filigree/ project dir with *issue_count* issues."""
    filigree_dir = base / name / ".filigree"
    filigree_dir.mkdir(parents=True)
    write_config(filigree_dir, {"prefix": prefix, "version": 1})
    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=prefix, check_same_thread=False)
    db.initialize()
    for i in range(issue_count):
        db.create_issue(f"{prefix} issue {i + 1}")
    db.close()
    return filigree_dir


@pytest.fixture
def project_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectStore:
    """Create a ProjectStore with two temp projects (alpha=1 issue, bravo=2 issues)."""
    config_dir = tmp_path / ".config" / "filigree"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr("filigree.server.SERVER_CONFIG_DIR", config_dir)
    monkeypatch.setattr("filigree.server.SERVER_CONFIG_FILE", config_dir / "server.json")
    monkeypatch.setattr("filigree.dashboard.ProjectStore.__init__", ProjectStore.__init__)

    alpha_dir = _create_project(tmp_path, "proj-alpha", "alpha", 1)
    bravo_dir = _create_project(tmp_path, "proj-bravo", "bravo", 2)

    server_json = {
        "port": 8377,
        "projects": {
            str(alpha_dir): {"prefix": "alpha"},
            str(bravo_dir): {"prefix": "bravo"},
        },
    }
    (config_dir / "server.json").write_text(json.dumps(server_json))

    store = ProjectStore()
    store.load()
    yield store
    store.close_all()


@pytest.fixture
async def multi_client(project_store: ProjectStore) -> AsyncIterator[AsyncClient]:
    """Test client backed by a multi-project ProjectStore (server mode)."""
    dash_module._project_store = project_store
    app = create_app(server_mode=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    dash_module._project_store = None
