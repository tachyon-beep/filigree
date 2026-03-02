"""Dashboard API tests — release endpoints."""

from __future__ import annotations

from httpx import AsyncClient

from filigree.core import FiligreeDB


class TestGetReleasesEndpoint:
    """GET /api/releases — list releases with progress rollups."""

    async def test_returns_200_with_releases_key(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        resp = await release_client.get("/api/releases")
        assert resp.status_code == 200
        assert "releases" in resp.json()

    async def test_excludes_done_releases_by_default(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        db.create_issue("Active Release", type="release")
        r2 = db.create_issue("Done Release", type="release")
        db.close_issue(r2.id)

        resp = await release_client.get("/api/releases")
        data = resp.json()
        assert len(data["releases"]) == 2  # Active Release + auto-seeded Future (Done excluded)

    async def test_include_released_shows_all(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        db.create_issue("Active Release", type="release")
        r2 = db.create_issue("Done Release", type="release")
        db.close_issue(r2.id)

        resp = await release_client.get("/api/releases?include_released=true")
        data = resp.json()
        assert len(data["releases"]) == 3  # Active + Done + auto-seeded Future

    async def test_include_released_false_is_default(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        db.create_issue("Active Release", type="release")
        r2 = db.create_issue("Done Release", type="release")
        db.close_issue(r2.id)

        resp_default = await release_client.get("/api/releases")
        resp_explicit = await release_client.get("/api/releases?include_released=false")
        assert len(resp_default.json()["releases"]) == len(resp_explicit.json()["releases"])

    async def test_invalid_include_released_returns_400(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        resp = await release_client.get("/api/releases?include_released=maybe")
        assert resp.status_code == 400

    async def test_response_shape(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        release = db.create_issue("R1", type="release")
        db.create_issue("T1", type="task", parent_id=release.id)

        resp = await release_client.get("/api/releases")
        data = resp.json()
        assert len(data["releases"]) == 2  # R1 + auto-seeded Future
        entry = next(r for r in data["releases"] if r["id"] == release.id)
        for key in ("id", "title", "status", "progress", "child_summary", "blocks", "blocked_by"):
            assert key in entry, f"Missing key: {key}"

    async def test_progress_shape(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        release = db.create_issue("R1", type="release")
        db.create_issue("T1", type="task", parent_id=release.id)

        resp = await release_client.get("/api/releases")
        progress = resp.json()["releases"][0]["progress"]
        for key in ("total", "completed", "in_progress", "open", "pct"):
            assert key in progress, f"Missing progress key: {key}"
            assert isinstance(progress[key], int)

    async def test_blocks_are_id_title_objects(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        r1 = db.create_issue("Blocker", type="release")
        r2 = db.create_issue("Blocked", type="release")
        # r2 depends on r1 -> r1 blocks r2
        db.add_dependency(r2.id, r1.id)

        resp = await release_client.get("/api/releases")
        releases = resp.json()["releases"]
        entry_r1 = next(r for r in releases if r["id"] == r1.id)
        assert len(entry_r1["blocks"]) == 1
        assert "id" in entry_r1["blocks"][0]
        assert "title" in entry_r1["blocks"][0]

    async def test_empty_releases(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        """With no manually created releases, only the auto-seeded Future release exists."""
        resp = await release_client.get("/api/releases")
        releases = resp.json()["releases"]
        assert len(releases) == 1
        assert releases[0]["title"] == "Future"

    async def test_sort_order_by_semver(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        r2 = db.create_issue("v2.0.0", type="release", fields={"version": "v2.0.0"})
        r1 = db.create_issue("v1.0.0", type="release", fields={"version": "v1.0.0"})
        r15 = db.create_issue("v1.5.0", type="release", fields={"version": "v1.5.0"})

        resp = await release_client.get("/api/releases")
        releases = resp.json()["releases"]
        ids = [r["id"] for r in releases]
        # Semver ascending, auto-seeded Future release always sorts last
        assert len(releases) == 4
        assert ids[:3] == [r1.id, r15.id, r2.id]
        assert releases[-1]["title"] == "Future"

    async def test_sort_order_future_always_last(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        # Auto-seeded Future release already exists; create another + a versioned release
        db.create_issue("Future", type="release")
        r1 = db.create_issue("v1.0.0", type="release", fields={"version": "v1.0.0"})

        resp = await release_client.get("/api/releases")
        releases = resp.json()["releases"]
        # v1.0.0 sorts first; both Future releases (auto-seeded + manual) sort after it
        assert len(releases) == 3
        assert releases[0]["id"] == r1.id
        assert all(r["title"] == "Future" for r in releases[1:])

    async def test_sort_order_version_from_title_fallback(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        # No version field — should parse from title
        r2 = db.create_issue("v2.0.0 — Big Release", type="release")
        r1 = db.create_issue("v1.0.0 — First", type="release")

        resp = await release_client.get("/api/releases")
        releases = resp.json()["releases"]
        ids = [r["id"] for r in releases]
        assert ids.index(r1.id) < ids.index(r2.id)


class TestGetReleaseTreeEndpoint:
    """GET /api/release/{release_id}/tree — release hierarchy with progress."""

    async def test_returns_200_with_tree(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        release = db.create_issue("R1", type="release")

        resp = await release_client.get(f"/api/release/{release.id}/tree")
        assert resp.status_code == 200
        data = resp.json()
        assert "release" in data
        assert "children" in data

    async def test_nonexistent_id_returns_404(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        resp = await release_client.get("/api/release/nonexistent-abc123/tree")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "RELEASE_NOT_FOUND"

    async def test_non_release_type_returns_404(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        epic = db.create_issue("E1", type="epic")

        resp = await release_client.get(f"/api/release/{epic.id}/tree")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_A_RELEASE"

    async def test_tree_structure_shape(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        release = db.create_issue("R1", type="release")
        epic = db.create_issue("E1", type="epic", parent_id=release.id)
        db.create_issue("T1", type="task", parent_id=epic.id)

        resp = await release_client.get(f"/api/release/{release.id}/tree")
        data = resp.json()
        assert len(data["children"]) == 1
        child = data["children"][0]
        assert "issue" in child
        assert "progress" in child
        assert "children" in child

    async def test_leaf_has_null_progress(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        release = db.create_issue("R1", type="release")
        db.create_issue("T1", type="task", parent_id=release.id)

        resp = await release_client.get(f"/api/release/{release.id}/tree")
        data = resp.json()
        child = data["children"][0]
        assert child["progress"] is None

    async def test_non_leaf_has_progress_dict(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        release = db.create_issue("R1", type="release")
        epic = db.create_issue("E1", type="epic", parent_id=release.id)
        db.create_issue("T1", type="task", parent_id=epic.id)

        resp = await release_client.get(f"/api/release/{release.id}/tree")
        data = resp.json()
        epic_child = data["children"][0]
        assert epic_child["progress"] is not None
        for key in ("total", "completed", "pct"):
            assert key in epic_child["progress"]

    async def test_empty_release_returns_empty_children(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        release = db.create_issue("R1", type="release")

        resp = await release_client.get(f"/api/release/{release.id}/tree")
        data = resp.json()
        assert data["children"] == []

    async def test_release_with_only_direct_tasks(self, release_client: AsyncClient, release_dashboard_db: FiligreeDB) -> None:
        db = release_dashboard_db
        release = db.create_issue("R1", type="release")
        db.create_issue("T1", type="task", parent_id=release.id)
        db.create_issue("T2", type="task", parent_id=release.id)
        db.create_issue("T3", type="task", parent_id=release.id)

        resp = await release_client.get(f"/api/release/{release.id}/tree")
        data = resp.json()
        assert len(data["children"]) == 3
        for child in data["children"]:
            assert child["progress"] is None
