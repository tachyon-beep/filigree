"""HTTP route tests for entity_associations (ADR-029, Clarion B.7 / WP9-A).

Mirrors the MCP-layer test surface against the FastAPI routes — same
shapes, same idempotency, same error semantics. Federation §5 audit
tests live in ``tests/test_entity_associations_federation.py``.
"""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import PopulatedDB


class TestListEntityAssociationsHTTP:
    async def test_empty_for_unattached_issue(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]
        resp = await client.get(f"/api/issue/{issue_id}/entity-associations")
        assert resp.status_code == 200
        assert resp.json() == {"associations": []}

    async def test_returns_attached_rows(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]
        # Attach two via the data layer (HTTP attach tested separately).
        dashboard_db.db.add_entity_association(issue_id, "py:func:a", content_hash="h1")
        dashboard_db.db.add_entity_association(issue_id, "py:func:b", content_hash="h2")

        resp = await client.get(f"/api/issue/{issue_id}/entity-associations")
        assert resp.status_code == 200
        body = resp.json()
        ids = {row["clarion_entity_id"] for row in body["associations"]}
        assert ids == {"py:func:a", "py:func:b"}

    async def test_missing_issue_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/issue/proj-nonexistent/entity-associations")
        assert resp.status_code == 404


class TestAddEntityAssociationHTTP:
    async def test_attach_returns_201(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]
        resp = await client.post(
            f"/api/issue/{issue_id}/entity-associations",
            json={
                "entity_id": "py:func:tokenize",
                "content_hash": "hash-a",
                "actor": "alice",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["clarion_entity_id"] == "py:func:tokenize"
        assert body["content_hash_at_attach"] == "hash-a"
        assert body["attached_by"] == "alice"

    async def test_attach_idempotent_refreshes_hash(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]
        await client.post(
            f"/api/issue/{issue_id}/entity-associations",
            json={"entity_id": "py:func:foo", "content_hash": "h1", "actor": "alice"},
        )
        resp = await client.post(
            f"/api/issue/{issue_id}/entity-associations",
            json={"entity_id": "py:func:foo", "content_hash": "h2", "actor": "bob"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["content_hash_at_attach"] == "h2"
        assert body["attached_by"] == "alice"  # preserved

    async def test_attach_missing_issue_returns_404(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/issue/proj-nonexistent/entity-associations",
            json={"entity_id": "py:func:foo", "content_hash": "h"},
        )
        assert resp.status_code == 404

    async def test_attach_missing_entity_id_returns_400(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]
        resp = await client.post(
            f"/api/issue/{issue_id}/entity-associations",
            json={"content_hash": "h"},
        )
        assert resp.status_code == 400

    async def test_attach_missing_content_hash_returns_400(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]
        resp = await client.post(
            f"/api/issue/{issue_id}/entity-associations",
            json={"entity_id": "py:func:foo"},
        )
        assert resp.status_code == 400


class TestRemoveEntityAssociationHTTP:
    async def test_remove_existing_returns_true(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]
        dashboard_db.db.add_entity_association(issue_id, "py:func:foo", content_hash="h")

        resp = await client.delete(
            f"/api/issue/{issue_id}/entity-associations",
            params={"entity_id": "py:func:foo"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"removed": True}

    async def test_remove_missing_returns_false(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]
        resp = await client.delete(
            f"/api/issue/{issue_id}/entity-associations",
            params={"entity_id": "py:func:never-attached"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"removed": False}

    async def test_remove_without_entity_id_returns_400(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]
        resp = await client.delete(f"/api/issue/{issue_id}/entity-associations")
        assert resp.status_code == 400


class TestFullLifecycleViaHTTP:
    async def test_attach_list_reattach_remove(self, client: AsyncClient, dashboard_db: PopulatedDB) -> None:
        issue_id = dashboard_db.ids["a"]

        # Attach
        await client.post(
            f"/api/issue/{issue_id}/entity-associations",
            json={
                "entity_id": "py:func:lifecycle",
                "content_hash": "v1",
                "actor": "alice",
            },
        )
        listed = (await client.get(f"/api/issue/{issue_id}/entity-associations")).json()
        assert len(listed["associations"]) == 1

        # Re-attach (drift refresh) — same row, new hash, preserved actor
        await client.post(
            f"/api/issue/{issue_id}/entity-associations",
            json={
                "entity_id": "py:func:lifecycle",
                "content_hash": "v2",
                "actor": "bob",
            },
        )
        listed = (await client.get(f"/api/issue/{issue_id}/entity-associations")).json()
        assert len(listed["associations"]) == 1
        assert listed["associations"][0]["content_hash_at_attach"] == "v2"
        assert listed["associations"][0]["attached_by"] == "alice"

        # Remove
        removed = (
            await client.delete(
                f"/api/issue/{issue_id}/entity-associations",
                params={"entity_id": "py:func:lifecycle"},
            )
        ).json()
        assert removed == {"removed": True}

        # List is empty
        listed = (await client.get(f"/api/issue/{issue_id}/entity-associations")).json()
        assert listed == {"associations": []}
