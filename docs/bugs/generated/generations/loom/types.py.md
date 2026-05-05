## Summary
ScanIngestResponseLoom inherits a generic batch shape, so its concrete endpoint contract exposes an unresolved `succeeded` type and an impossible `newly_unblocked` field.

## Severity
- Severity: major
- Priority: P2
- Rule ID: type-error

## Evidence
`src/filigree/generations/loom/types.py:355` defines:

```python
class ScanIngestResponseLoom(BatchResponse[str]):
```

but `BatchResponse` is generic and includes issue-close-only state:

`src/filigree/types/api.py:384-398`

```python
class BatchResponse(TypedDict, Generic[_T]):
    succeeded: list[_T]
    failed: list[BatchFailure]
    newly_unblocked: NotRequired[list[SlimIssue]]
```

The actual Loom scan-results fixture declares only `succeeded`, `failed`, `stats`, and `warnings`, with `succeeded` as `list[str]`:

`tests/fixtures/contracts/loom/scan-results.json:16-31`

The adapter also returns only those four fields:

`src/filigree/generations/loom/adapters.py:343-354`

A local type introspection check confirms the concrete type does not resolve as intended:

```text
ScanIngestResponseLoom {'succeeded': list[~_T], 'failed': list[filigree.types.api.BatchFailure], 'newly_unblocked': list[filigree.types.api.SlimIssue], 'stats': <class 'filigree.generations.loom.types.ScanStats'>, 'warnings': list[str]}
```

## Root Cause Hypothesis
The type tries to specialize `BatchResponse[str]`, but runtime `TypedDict` introspection does not preserve the concrete `str` substitution here, and it inherits the generic optional `newly_unblocked` key even though scan ingest can never unblock issues.

## Suggested Fix
Make `ScanIngestResponseLoom` a concrete `TypedDict` in `src/filigree/generations/loom/types.py`:

```python
class ScanIngestResponseLoom(TypedDict):
    succeeded: list[str]
    failed: list[BatchFailure]
    stats: ScanStats
    warnings: list[str]
```

Add a small `get_type_hints(ScanIngestResponseLoom)` contract test so this does not drift again.

---

## Summary
BatchCloseResponseLoom is typed as slim-only even though `response_detail=full` returns full IssueLoom items in `succeeded`.

## Severity
- Severity: major
- Priority: P2
- Rule ID: type-error

## Evidence
`src/filigree/generations/loom/types.py:61-75` declares the batch-close response as:

```python
class BatchCloseResponseLoom(TypedDict):
    succeeded: list[SlimIssueLoom]
    failed: list[BatchFailure]
    newly_unblocked: NotRequired[list[SlimIssueLoom]]
```

The actual handler switches `succeeded` between slim and full projections:

`src/filigree/dashboard_routes/issues.py:927-944`

```python
project = issue_to_loom if detail == "full" else slim_issue_to_loom
response = {
    "succeeded": [project(i) for i in closed],
    "failed": errors,
}
```

The federation contract explicitly says `response_detail=full` returns `IssueLoom` items in `succeeded[]`, while `newly_unblocked[]` remains slim:

`docs/federation/contracts.md:90-94`

The seeded parity test pins that full response shape:

`tests/util/test_generation_parity.py:514-531`

## Root Cause Hypothesis
Phase C5 added `response_detail=slim|full` to batch-close, but the target type stayed at the older C2 slim-only contract.

## Suggested Fix
Update `BatchCloseResponseLoom` in `src/filigree/generations/loom/types.py` so `succeeded` covers both legal modes, while keeping `newly_unblocked` slim-only. For example:

```python
succeeded: list[SlimIssueLoom | IssueLoom]
failed: list[BatchFailure]
newly_unblocked: NotRequired[list[SlimIssueLoom]]
```

Alternatively split it into slim and full response `TypedDict`s if the codebase wants stricter mode-specific typing.

