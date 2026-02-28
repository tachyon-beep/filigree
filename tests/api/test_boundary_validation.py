"""Dashboard API boundary validation tests for priority and actor."""

from __future__ import annotations

from httpx import AsyncClient


class TestDashboardPriorityValidation:
    """Priority range checks in dashboard routes."""

    async def test_update_issue_priority_too_high(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Target"})
        assert resp.status_code == 201
        issue_id = resp.json()["id"]
        resp = await client.patch(f"/api/issue/{issue_id}", json={"priority": 5})
        assert resp.status_code == 400
        assert "INVALID_PRIORITY" in resp.json()["error"]["code"]

    async def test_update_issue_priority_too_low(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.patch(f"/api/issue/{issue_id}", json={"priority": -1})
        assert resp.status_code == 400

    async def test_create_issue_priority_out_of_range(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Bad", "priority": 99})
        assert resp.status_code == 400
        assert "INVALID_PRIORITY" in resp.json()["error"]["code"]

    async def test_create_issue_priority_boundary_0(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "P0", "priority": 0})
        assert resp.status_code == 201
        assert resp.json()["priority"] == 0

    async def test_create_issue_priority_boundary_4(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "P4", "priority": 4})
        assert resp.status_code == 201
        assert resp.json()["priority"] == 4

    async def test_batch_update_priority_out_of_range(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.post(
            "/api/batch/update",
            json={"issue_ids": [issue_id], "priority": 5},
        )
        assert resp.status_code == 400

    async def test_create_issue_priority_float(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Bad", "priority": 2.5})
        assert resp.status_code == 400

    async def test_create_issue_priority_bool(self, client: AsyncClient) -> None:
        """bool is a subclass of int — should be rejected."""
        resp = await client.post("/api/issues", json={"title": "Bad", "priority": True})
        assert resp.status_code == 400


class TestDashboardActorValidation:
    """Actor validation in dashboard routes."""

    async def test_update_issue_empty_actor(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.patch(f"/api/issue/{issue_id}", json={"actor": "", "title": "New"})
        assert resp.status_code == 400
        assert "VALIDATION_ERROR" in resp.json()["error"]["code"]

    async def test_close_issue_control_char_actor(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.post(f"/api/issue/{issue_id}/close", json={"actor": "\x00evil"})
        assert resp.status_code == 400

    async def test_create_issue_bom_actor(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "Test", "actor": "\uFEFF"})
        assert resp.status_code == 400

    async def test_claim_issue_valid_actor(self, client: AsyncClient) -> None:
        """Valid actor should pass through."""
        resp = await client.post("/api/issues", json={"title": "Target"})
        issue_id = resp.json()["id"]
        resp = await client.post(
            f"/api/issue/{issue_id}/claim",
            json={"assignee": "bot", "actor": "test-agent"},
        )
        assert resp.status_code == 200
