"""Dashboard error responses emit the flat 2.0 ErrorResponse shape."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import filigree.dashboard as dash
from filigree.core import FILIGREE_DIR_NAME, FiligreeDB
from filigree.dashboard import create_app
from filigree.types.api import ErrorCode


@pytest.fixture
def flat_client(filigree_project: Path) -> TestClient:
    """Sync TestClient wired to a fresh filigree project DB."""
    filigree_dir = filigree_project / FILIGREE_DIR_NAME
    db = FiligreeDB.from_filigree_dir(filigree_dir, check_same_thread=False)

    original_db = dash._db
    original_store = dash._project_store
    dash._db = db
    dash._project_store = None
    app = create_app(server_mode=False)
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    dash._db = original_db
    dash._project_store = original_store
    db.close()


class TestFlatErrorShape:
    """All dashboard error responses must use the flat 2.0 ErrorResponse shape."""

    _VALID_CODES: frozenset[str] = frozenset(m.value for m in ErrorCode)

    def test_404_issue_not_found_is_flat(self, flat_client: TestClient) -> None:
        """GET /api/issue/<bogus> must return flat {error: str, code: str}."""
        resp = flat_client.get("/api/issue/nope-does-not-exist")
        assert resp.status_code == 404
        body = resp.json()
        # Flat shape — error must be a string, not a nested dict
        assert isinstance(body.get("error"), str), f"expected error to be str, got: {body!r}"
        assert "code" in body, f"expected 'code' key in body: {body!r}"
        assert body["code"] in self._VALID_CODES, f"unknown code {body['code']!r}"

    def test_400_validation_error_is_flat(self, flat_client: TestClient) -> None:
        """POST /api/issues with bad body must return flat {error: str, code: str}."""
        resp = flat_client.post("/api/issues", content=b"not-json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400
        body = resp.json()
        assert isinstance(body.get("error"), str), f"expected error to be str, got: {body!r}"
        assert body.get("code") in self._VALID_CODES

    def test_no_nested_error_object(self, flat_client: TestClient) -> None:
        """The error key must not be a nested dict (old shape)."""
        resp = flat_client.get("/api/issue/nope-does-not-exist")
        body = resp.json()
        # Old shape was {"error": {"message": ..., "code": ...}}
        assert not isinstance(body.get("error"), dict), f"Got old nested error shape: {body!r}"
