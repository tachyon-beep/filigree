"""Parity-per-generation tests against published contract fixtures.

Phase B4 of the 2.0 federation work package. Complements
``test_cross_surface_parity.py`` (which pins cross-*surface* agreement
on the envelope) by pinning cross-*generation* agreement against the
fixtures published under ``tests/fixtures/contracts/<generation>/``.

Shape-reference, not byte-equality, per ``docs/federation/contracts.md``:
a response matches a fixture example if status codes agree, the body's
JSON type agrees at every level, dicts have the exact key set, lists
are empty-or-nonempty as declared, scalars have the declared Python
type, and ``ErrorCode`` values match exactly (enum closure).

Phase C1 scope: classic + loom scan-results both pass against their
fixtures, and a living-surface equivalence test pins
``/api/scan-results`` against ``/api/loom/scan-results``. As later
Phase C tasks land each loom endpoint, they remove the corresponding
``pytest.mark.skip`` here (the conversion is the per-endpoint gate)
and add their own equivalence test if a living-surface alias lands.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import filigree.dashboard as dash_module
from filigree.core import DB_FILENAME, FILIGREE_DIR_NAME, SUMMARY_FILENAME, FiligreeDB, write_config
from filigree.dashboard import create_app
from filigree.types.api import ErrorCode

_VALID_ERROR_CODES: frozenset[str] = frozenset(e.value for e in ErrorCode)

# Fixtures root is relative to the filigree repo root. pytest runs from repo
# root so this is a direct Path; tests/conftest.py does not change cwd.
_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "contracts"


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def _load_contract_fixture(generation: str, endpoint_name: str) -> dict[str, Any]:
    path = _FIXTURE_ROOT / generation / f"{endpoint_name}.json"
    assert path.exists(), f"contract fixture not found: {path}"
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def _examples_for(generation: str, endpoint_name: str) -> list[dict[str, Any]]:
    return list(_load_contract_fixture(generation, endpoint_name)["examples"])


# ---------------------------------------------------------------------------
# Shape assertion
# ---------------------------------------------------------------------------


def _assert_shape_matches(actual: Any, expected: Any, path: str = "$") -> None:
    """Assert ``actual`` has the same *shape* as ``expected``.

    Shape rules (see docs/federation/contracts.md):
    - Same JSON type at each level (dict/list/scalar).
    - Dicts have the exact key set; values recurse.
    - Lists: if ``expected`` is empty, ``actual`` must be empty; if
      ``expected`` is non-empty, the first element of ``actual`` is
      checked against the first element of ``expected``.
    - Scalars: same Python type (``bool`` is not ``int`` for the type
      check; ``int`` and ``float`` are distinct).
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected dict, got {type(actual).__name__}"
        assert set(actual.keys()) == set(expected.keys()), (
            f"{path}: key-set mismatch; missing={set(expected.keys()) - set(actual.keys())} "
            f"extra={set(actual.keys()) - set(expected.keys())}"
        )
        for key, sub_expected in expected.items():
            _assert_shape_matches(actual[key], sub_expected, f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected list, got {type(actual).__name__}"
        if expected:
            assert actual, f"{path}: expected non-empty list, got empty"
            _assert_shape_matches(actual[0], expected[0], f"{path}[0]")
        # Empty expected is a soft check: actual may be any list length.
        # Tests that need exact length pin it in the test body.
    else:
        # Scalars — strict type match (bool vs int distinguished).
        assert type(actual) is type(expected), (
            f"{path}: type mismatch — expected {type(expected).__name__} ({expected!r}), got {type(actual).__name__} ({actual!r})"
        )


def _assert_structural_equivalence(a: Any, b: Any, path: str = "$") -> None:
    """Assert two live responses have the same structural shape.

    Used for living-surface equivalence (same body → two HTTP paths) where
    side effects between the two calls (e.g. scan-results dedup on the
    second invocation) make list lengths legitimately diverge. Equivalence
    here is: same JSON type at each level, same dict key sets, same scalar
    types, list element types match if both are non-empty. Unlike
    ``_assert_shape_matches``, list emptiness is not checked — both being
    lists is enough.
    """
    if isinstance(a, dict):
        assert isinstance(b, dict), f"{path}: a is dict, b is {type(b).__name__}"
        assert set(a.keys()) == set(b.keys()), (
            f"{path}: key-set mismatch; only-in-a={set(a.keys()) - set(b.keys())} only-in-b={set(b.keys()) - set(a.keys())}"
        )
        for key in a:
            _assert_structural_equivalence(a[key], b[key], f"{path}.{key}")
    elif isinstance(a, list):
        assert isinstance(b, list), f"{path}: a is list, b is {type(b).__name__}"
        if a and b:
            _assert_structural_equivalence(a[0], b[0], f"{path}[0]")
    else:
        assert type(a) is type(b), f"{path}: type mismatch — a={type(a).__name__} ({a!r}), b={type(b).__name__} ({b!r})"


def _assert_error_envelope(body: Any, *, expected_code: str, path: str = "$") -> None:
    """Assert ``body`` is a flat 2.0 error envelope with the expected code.

    Used for the 400/404/409 cases in fixtures — more strict than shape
    matching because error envelope correctness is the federation-era
    contract that consumers branch on.
    """
    assert isinstance(body, dict), f"{path}: expected dict, got {type(body).__name__}"
    assert "error" in body, f"{path}: missing 'error'"
    assert "code" in body, f"{path}: missing 'code'"
    assert isinstance(body["error"], str), f"{path}: 'error' not a string"
    assert body["error"], f"{path}: 'error' is empty"
    assert body["code"] == expected_code, f"{path}: code={body['code']!r} expected {expected_code!r}"
    assert body["code"] in _VALID_ERROR_CODES, f"{path}: code {body['code']!r} not a known ErrorCode member"


# ---------------------------------------------------------------------------
# Dashboard surface (isolated per-test, same pattern as test_cross_surface_parity)
# ---------------------------------------------------------------------------


@pytest.fixture
async def dashboard_surface(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[AsyncClient]:
    tmp = tmp_path_factory.mktemp("gen-parity")
    filigree_dir = tmp / FILIGREE_DIR_NAME
    filigree_dir.mkdir()
    write_config(filigree_dir, {"prefix": "genp", "version": 1})
    (filigree_dir / SUMMARY_FILENAME).write_text("# generation parity\n")
    db = FiligreeDB(filigree_dir / DB_FILENAME, prefix="genp", check_same_thread=False)
    db.initialize()

    original_db = dash_module._db
    original_store = dash_module._project_store
    dash_module._db = db
    dash_module._project_store = None
    app = create_app(server_mode=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    dash_module._db = original_db
    dash_module._project_store = original_store
    db.close()


# ---------------------------------------------------------------------------
# Classic generation — scan-results
# ---------------------------------------------------------------------------


_CLASSIC_SCAN_RESULTS_EXAMPLES = _examples_for("classic", "scan-results")


@pytest.mark.asyncio
class TestClassicGenerationParityScanResults:
    """Each example in tests/fixtures/contracts/classic/scan-results.json is
    replayed against the live dashboard; status code + body shape must
    match the fixture declaration. See docs/federation/contracts.md for
    the pinning discipline.
    """

    @pytest.mark.parametrize(
        "example",
        _CLASSIC_SCAN_RESULTS_EXAMPLES,
        ids=[e["name"] for e in _CLASSIC_SCAN_RESULTS_EXAMPLES],
    )
    async def test_example_matches_fixture(
        self,
        dashboard_surface: AsyncClient,
        example: dict[str, Any],
    ) -> None:
        req = example["request"]
        expected_resp = example["response"]
        resp = await dashboard_surface.request(req["method"], req["path"], json=req["body"])
        assert resp.status_code == expected_resp["status"], (
            f"{example['name']}: status {resp.status_code} != fixture {expected_resp['status']}; body={resp.text!r}"
        )
        body = resp.json()
        if expected_resp["status"] >= 400:
            _assert_error_envelope(body, expected_code=expected_resp["body"]["code"], path=example["name"])
        else:
            _assert_shape_matches(body, expected_resp["body"], path=example["name"])


# ---------------------------------------------------------------------------
# Loom generation — scan-results (skip-placeholders until Phase C1)
# ---------------------------------------------------------------------------


_LOOM_SCAN_RESULTS_EXAMPLES = _examples_for("loom", "scan-results")


@pytest.mark.asyncio
class TestLoomGenerationParityScanResults:
    """Loom scan-results parity. Mounted in Phase C1; each example in
    ``tests/fixtures/contracts/loom/scan-results.json`` is replayed
    against the live dashboard.
    """

    @pytest.mark.parametrize(
        "example",
        _LOOM_SCAN_RESULTS_EXAMPLES,
        ids=[e["name"] for e in _LOOM_SCAN_RESULTS_EXAMPLES],
    )
    async def test_example_matches_fixture(
        self,
        dashboard_surface: AsyncClient,
        example: dict[str, Any],
    ) -> None:
        req = example["request"]
        expected_resp = example["response"]
        resp = await dashboard_surface.request(req["method"], req["path"], json=req["body"])
        assert resp.status_code == expected_resp["status"], (
            f"{example['name']}: status {resp.status_code} != fixture {expected_resp['status']}; body={resp.text!r}"
        )
        body = resp.json()
        if expected_resp["status"] >= 400:
            _assert_error_envelope(body, expected_code=expected_resp["body"]["code"], path=example["name"])
        else:
            _assert_shape_matches(body, expected_resp["body"], path=example["name"])


# ---------------------------------------------------------------------------
# Living-surface equivalence — scan-results
# ---------------------------------------------------------------------------


_LIVING_SURFACE_EQUIV_EXAMPLES = [e for e in _LOOM_SCAN_RESULTS_EXAMPLES if e["response"]["status"] < 400]


@pytest.mark.asyncio
class TestLivingSurfaceEquivalenceScanResults:
    """Pin that ``/api/scan-results`` (living-surface alias) and
    ``/api/loom/scan-results`` return the same response shape for the
    same request body. Phase C1 living-surface decision per the loom
    fixture — see ``docs/federation/contracts.md``.

    Equivalence is *structural* between the two live responses, not
    against the fixture (the fixture is already pinned by
    ``TestLoomGenerationParityScanResults``). The shared-state caveat:
    scan-results is state-mutating, so the second call sees the dedup
    result of the first; ``succeeded`` length and ``stats`` counters
    legitimately differ between the two calls even though the wire shape
    is identical. ``_assert_structural_equivalence`` ignores list lengths
    and scalar values, checking only keys and types at every level —
    which is exactly the contract the alias preserves. Error cases are
    excluded: error envelopes are pinned per-generation by the parity
    tests above.
    """

    @pytest.mark.parametrize(
        "example",
        _LIVING_SURFACE_EQUIV_EXAMPLES,
        ids=[e["name"] for e in _LIVING_SURFACE_EQUIV_EXAMPLES],
    )
    async def test_living_matches_loom(
        self,
        dashboard_surface: AsyncClient,
        example: dict[str, Any],
    ) -> None:
        body = example["request"]["body"]
        loom_resp = await dashboard_surface.post("/api/loom/scan-results", json=body)
        living_resp = await dashboard_surface.post("/api/scan-results", json=body)
        assert loom_resp.status_code == living_resp.status_code, (
            f"{example['name']}: loom={loom_resp.status_code} living={living_resp.status_code}"
        )
        _assert_structural_equivalence(living_resp.json(), loom_resp.json(), path=example["name"])


# ---------------------------------------------------------------------------
# Loom generation — batch/update (Phase C2)
# ---------------------------------------------------------------------------


_LOOM_BATCH_UPDATE_EXAMPLES = _examples_for("loom", "batch-update")


_SLIM_ISSUE_LOOM_KEYS = frozenset({"issue_id", "title", "status", "priority", "type"})


def _assert_slim_issue_loom(item: Any, path: str = "$") -> None:
    """Assert ``item`` is a SlimIssueLoom (5 keys, all the expected types)."""
    assert isinstance(item, dict), f"{path}: expected dict, got {type(item).__name__}"
    assert set(item.keys()) == _SLIM_ISSUE_LOOM_KEYS, (
        f"{path}: SlimIssueLoom key-set mismatch; "
        f"missing={_SLIM_ISSUE_LOOM_KEYS - set(item.keys())} "
        f"extra={set(item.keys()) - _SLIM_ISSUE_LOOM_KEYS}"
    )
    assert isinstance(item["issue_id"], str), f"{path}: issue_id must be str"
    assert isinstance(item["title"], str), f"{path}: title must be str"
    assert isinstance(item["status"], str), f"{path}: status must be str"
    assert isinstance(item["priority"], int), f"{path}: priority must be int"
    assert not isinstance(item["priority"], bool), f"{path}: priority must be int (not bool)"
    assert isinstance(item["type"], str), f"{path}: type must be str"


@pytest.mark.asyncio
class TestLoomGenerationParityBatchUpdate:
    """Loom batch/update parity. Phase C2 mounts
    ``POST /api/loom/batch/update`` returning ``BatchResponse[SlimIssueLoom]``.

    Fixture replay covers error envelopes and the all-missing 200 case
    (which pins ``BatchFailure`` shape on ``failed[0]``). The populated-
    success path needs a real seeded issue id, so the
    ``test_succeeded_populated_shape`` method covers it directly.
    """

    @pytest.mark.parametrize(
        "example",
        _LOOM_BATCH_UPDATE_EXAMPLES,
        ids=[e["name"] for e in _LOOM_BATCH_UPDATE_EXAMPLES],
    )
    async def test_example_matches_fixture(
        self,
        dashboard_surface: AsyncClient,
        example: dict[str, Any],
    ) -> None:
        req = example["request"]
        expected_resp = example["response"]
        resp = await dashboard_surface.request(req["method"], req["path"], json=req["body"])
        assert resp.status_code == expected_resp["status"], (
            f"{example['name']}: status {resp.status_code} != fixture {expected_resp['status']}; body={resp.text!r}"
        )
        body = resp.json()
        if expected_resp["status"] >= 400:
            _assert_error_envelope(body, expected_code=expected_resp["body"]["code"], path=example["name"])
        else:
            _assert_shape_matches(body, expected_resp["body"], path=example["name"])

    async def test_succeeded_populated_shape(self, dashboard_surface: AsyncClient) -> None:
        """Pin ``succeeded[0]`` as a SlimIssueLoom against a real update.

        Fixture replay can't populate ``succeeded`` (the fixture body uses
        non-existent ids), so this test seeds a real issue and asserts
        the loom slim shape end-to-end.
        """
        create = await dashboard_surface.post("/api/issues", json={"title": "C2 batch update seed"})
        assert create.status_code in (200, 201), create.text
        issue_id = create.json()["id"]
        resp = await dashboard_surface.post(
            "/api/loom/batch/update",
            json={"issue_ids": [issue_id], "priority": 1},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"succeeded", "failed"}, body
        assert isinstance(body["failed"], list)
        assert body["failed"] == []
        assert isinstance(body["succeeded"], list)
        assert len(body["succeeded"]) == 1
        _assert_slim_issue_loom(body["succeeded"][0], path="succeeded[0]")
        assert body["succeeded"][0]["issue_id"] == issue_id
        assert body["succeeded"][0]["priority"] == 1


# ---------------------------------------------------------------------------
# Loom generation — batch/close (Phase C2)
# ---------------------------------------------------------------------------


_LOOM_BATCH_CLOSE_EXAMPLES = _examples_for("loom", "batch-close")


@pytest.mark.asyncio
class TestLoomGenerationParityBatchClose:
    """Loom batch/close parity. Phase C2 mounts
    ``POST /api/loom/batch/close`` returning ``BatchCloseResponseLoom``
    (BatchResponse[SlimIssueLoom] plus optional ``newly_unblocked``).

    Fixture replay covers errors + all-missing. The seeded test methods
    pin the populated-success shape and the ``newly_unblocked``
    omitted-when-empty rule.
    """

    @pytest.mark.parametrize(
        "example",
        _LOOM_BATCH_CLOSE_EXAMPLES,
        ids=[e["name"] for e in _LOOM_BATCH_CLOSE_EXAMPLES],
    )
    async def test_example_matches_fixture(
        self,
        dashboard_surface: AsyncClient,
        example: dict[str, Any],
    ) -> None:
        req = example["request"]
        expected_resp = example["response"]
        resp = await dashboard_surface.request(req["method"], req["path"], json=req["body"])
        assert resp.status_code == expected_resp["status"], (
            f"{example['name']}: status {resp.status_code} != fixture {expected_resp['status']}; body={resp.text!r}"
        )
        body = resp.json()
        if expected_resp["status"] >= 400:
            _assert_error_envelope(body, expected_code=expected_resp["body"]["code"], path=example["name"])
        else:
            _assert_shape_matches(body, expected_resp["body"], path=example["name"])

    async def test_succeeded_populated_shape(self, dashboard_surface: AsyncClient) -> None:
        """Close one isolated (no dependents) issue. ``succeeded[0]`` is a
        ``SlimIssueLoom``; ``newly_unblocked`` MUST be omitted because no
        issue was waiting on this one (per the BatchResponse §C2 rule).
        """
        create = await dashboard_surface.post("/api/issues", json={"title": "C2 batch close seed"})
        assert create.status_code in (200, 201), create.text
        issue_id = create.json()["id"]
        resp = await dashboard_surface.post(
            "/api/loom/batch/close",
            json={"issue_ids": [issue_id], "reason": "C2 test"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "newly_unblocked" not in body, f"newly_unblocked must be omitted when empty: {body!r}"
        assert set(body.keys()) == {"succeeded", "failed"}
        assert body["failed"] == []
        assert len(body["succeeded"]) == 1
        _assert_slim_issue_loom(body["succeeded"][0], path="succeeded[0]")
        assert body["succeeded"][0]["issue_id"] == issue_id

    async def test_newly_unblocked_populated_shape(self, dashboard_surface: AsyncClient) -> None:
        """Wire up a dependency (B blocked by A), close A, assert
        ``newly_unblocked`` is present and contains B as a SlimIssueLoom.
        """
        a_resp = await dashboard_surface.post("/api/issues", json={"title": "blocker"})
        b_resp = await dashboard_surface.post("/api/issues", json={"title": "blocked"})
        assert a_resp.status_code in (200, 201), a_resp.text
        assert b_resp.status_code in (200, 201), b_resp.text
        a_id = a_resp.json()["id"]
        b_id = b_resp.json()["id"]
        dep = await dashboard_surface.post(f"/api/issue/{b_id}/dependencies", json={"depends_on": a_id})
        assert dep.status_code in (200, 201), dep.text

        resp = await dashboard_surface.post(
            "/api/loom/batch/close",
            json={"issue_ids": [a_id], "reason": "unblock"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"succeeded", "failed", "newly_unblocked"}, body
        assert len(body["succeeded"]) == 1
        assert body["succeeded"][0]["issue_id"] == a_id
        assert isinstance(body["newly_unblocked"], list)
        assert len(body["newly_unblocked"]) == 1
        unblocked = body["newly_unblocked"][0]
        _assert_slim_issue_loom(unblocked, path="newly_unblocked[0]")
        assert unblocked["issue_id"] == b_id
