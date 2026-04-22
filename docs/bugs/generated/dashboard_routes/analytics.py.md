## Summary
logic-error: `/api/graph` rejects valid `types=` filters when the requested type is registered but currently has zero issues.

## Severity
- Severity: major
- Priority: P2

## Evidence
[analytics.py](/home/john/filigree/src/filigree/dashboard_routes/analytics.py:177) validates the graph `types` filter against the types present in the current issue rows, not against the registered workflow types:

```python
type_filter_raw = params.get("types")
gp.type_filter = set(_parse_csv_param(type_filter_raw)) if type_filter_raw else set()
if gp.type_filter:
    known_types = {i.type for i in issues}
    unknown_types = sorted(gp.type_filter - known_types)
    if unknown_types:
        return _error_response(
            f"Unknown types: {', '.join(unknown_types)}",
            ErrorCode.VALIDATION,
            400,
            {"param": "types", "value": type_filter_raw},
        )
```

But the dashboard exposes registered types independently of current issue population. [issues.py](/home/john/filigree/src/filigree/dashboard_routes/issues.py:383) returns every enabled workflow type from `db.templates.list_types()`, and tests assert that `bug` is always one of those valid types in the default packs at [test_api.py](/home/john/filigree/tests/api/test_api.py:1065) and [test_api.py](/home/john/filigree/tests/api/test_api.py:1071).

That means a project with only task issues will still advertise `bug` as a valid type, but `/api/graph?mode=v2&types=bug` returns `400 Unknown types: bug` instead of a valid empty graph.

## Root Cause Hypothesis
The validator conflates “type exists in the workflow schema” with “type appears in the current result set.” That works only when every valid type already has at least one issue, so empty-result filters are misclassified as invalid input.

## Suggested Fix
Validate `types` against registered workflow types, not just the live issue list. The safest version is to pass the allowed type names into `_parse_graph_v2_params` from `db.templates.list_types()` and validate against the union of:

- registered template types
- live issue types already present in the DB

That preserves support for imported/custom live types while allowing valid-but-empty filters to return `200` with `nodes: []` and `edges: []` instead of a false validation error.