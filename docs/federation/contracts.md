# Filigree Federation Contracts

This directory documents filigree's published HTTP contracts for federation consumers — the stable, pinnable targets introduced by [ADR-002](../architecture/decisions/ADR-002-api-generations-and-federation-posture.md).

## What a "contract" is here

A **contract** is a named API generation at the HTTP surface. Filigree currently publishes two:

- **`classic`** — `/api/v1/*`. The pre-federation HTTP surface as it existed through the 1.x series. Frozen: no new operations, no shape changes. Continues to be fully supported. Retirement requires a new ADR with 12 months of deprecation notice.
- **`loom`** — `/api/loom/*`. Introduced in 2.0. The federation-era generation, named for the Loom federation (Clarion + Wardline + Shuttle + filigree). Uses the unified `BatchResponse[T]` / `ListResponse[T]` envelopes, the closed `ErrorCode` enum, the `issue_id` vocabulary, and composed operations like `start_work`.

The **living surface** at `/api/*` (no generation prefix) aliases the current recommended generation — as of 2026-04-24 that is `loom`. Living-surface endpoints are explicitly non-stability; production integrations across version boundaries must pin to a named generation.

MCP and CLI reflect the living surface only. They evolve forward with each release; they do not publish pinnable contracts. Callers who need pinned stability use HTTP.

## Fixture layout

```
tests/fixtures/contracts/
├── classic/
│   └── scan-results.json
└── loom/
    └── scan-results.json
```

Each fixture contains:

1. `_meta` — provenance, authority references, stability statement, and the test that verifies the shape in CI.
2. `shape_decl` — a human-readable shape declaration. Present for new (loom) generations where the shape is a design commitment; omitted for frozen generations where the shape is defined by the existing code.
3. `examples` — representative request/response pairs. Each example has a `name`, a `note` describing what it covers, a `request` (method, path, headers, body), and a `response` (status, headers, body).

Additional endpoints join the fixture set as their loom-generation implementations land (Phase C of the 2.0 federation work package).

## Pinning discipline: shape reference, not byte-equality

**Do not diff fixture bytes against a live response and expect equality.** Filigree does not guarantee field ordering, whitespace, or content-type parameter ordering in responses. What filigree does guarantee for a named generation is:

1. **Key set** — the keys present at each level of the response.
2. **Value types** — each key's value has the declared type (`int`, `str`, `list`, `dict`, nested TypedDict).
3. **Semantic invariants** — values encode the stated meaning (e.g. `stats.files_created` counts files newly created in this ingest; `succeeded` contains server-generated ids for newly-created findings; `warnings` is human-readable).
4. **Enum closure** — values declared as `ErrorCode` members are one of the enum's declared values; unknown strings never appear.
5. **Status-code + envelope pairing** — an ErrorCode paired with its documented HTTP status (`VALIDATION` → 400, `NOT_FOUND` → 404, etc.).

A consumer-side pinning test therefore asserts these five properties against parsed JSON, not against raw bytes. The examples in each fixture are *canonical representatives*, not the only shape a response will take — server-generated ids vary per request; counts vary per inputs; ordering within a list may vary.

### Recommended consumer-side pattern

```python
# consumer CI sketch
import json, pytest, requests

FIXTURE = json.load(open("path/to/filigree/tests/fixtures/contracts/classic/scan-results.json"))

def test_scan_results_success_shape(filigree_url):
    request_body = FIXTURE["examples"][0]["request"]["body"]
    resp = requests.post(f"{filigree_url}/api/v1/scan-results", json=request_body)
    assert resp.status_code == FIXTURE["examples"][0]["response"]["status"]
    body = resp.json()
    expected = FIXTURE["examples"][0]["response"]["body"]
    assert set(body.keys()) == set(expected.keys())
    for key, val in expected.items():
        assert type(body[key]) is type(val), f"{key}: {type(body[key])} vs {type(val)}"
```

(Rust / Go / TypeScript analogues follow the same shape.)

## When a contract evolves

**Non-breaking additions** (new optional response fields, new optional request parameters with safe defaults) may land in-place without a new generation. Fixtures are updated to reflect the new shape; the `_meta.updated` field moves.

**Breaking changes** introduce a new named generation — `loom-v2`, `loom-graph`, `loom-entities`, or an entirely new era name if the shift is foundational. The older generation is *not* mutated; it continues to serve the pre-break shape until retired per ADR-002 §8 (new ADR + 12-month deprecation + CLI/docs communication).

## Cross-references

- **ADR-002** (the naming + lifecycle rules): `docs/architecture/decisions/ADR-002-api-generations-and-federation-posture.md`.
- **2.0 work package** (the execution sequence): `docs/plans/2026-04-24-2.0-federation-work-package.md`.
- **ADR-017 audit** (verifies classic-generation semantics are preserved on the 2.0 branch): `docs/plans/2026-04-24-adr017-audit.md`.
- **Clarion ADR-004** (finding exchange format): `/home/john/clarion/docs/clarion/adr/ADR-004-finding-exchange-format.md`.
- **Clarion ADR-017** (severity + dedup): `/home/john/clarion/docs/clarion/adr/ADR-017-severity-and-dedup.md`.
