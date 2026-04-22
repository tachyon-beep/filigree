## Summary
logic-error: `_semver_sort_key()` never actually falls back to `title` when `version` is a non-empty invalid string, so imported releases can be sorted into the non-semver bucket even when their title clearly encodes the version or `"Future"`.

## Severity
- Severity: minor
- Priority: P2

## Evidence
[releases.py](/home/john/filigree/src/filigree/dashboard_routes/releases.py:43) says semver parsing checks `version` first and then falls back to `title`, but the implementation at [releases.py](/home/john/filigree/src/filigree/dashboard_routes/releases.py:64) does this instead:

```py
if version:
    m = _SEMVER_STRICT_RE.match(version)
    if m:
        return ...
text = version or title
m = _SEMVER_LOOSE_RE.search(text)
```

Because `text = version or title` uses raw truthiness, any non-empty garbage value in `version` suppresses title parsing entirely. The `"Future"` fallback has the same problem at [releases.py](/home/john/filigree/src/filigree/dashboard_routes/releases.py:60), where title-based detection only runs when `not version`.

This is reachable with real persisted data, not just a synthetic dict. The import path serializes arbitrary `fields` content without validating that `fields["version"]` matches the release schema: [db_meta.py](/home/john/filigree/src/filigree/db_meta.py:390). Then `/api/releases` sorts every release through `_semver_sort_key()` at [releases.py](/home/john/filigree/src/filigree/dashboard_routes/releases.py:107).

Concrete examples that this code misclassifies:
- `{"version": "planned", "title": "v2.0.0 - Big Release"}` sorts as non-semver instead of using the title version.
- `{"version": " ", "title": "Future"}` sorts as non-semver instead of the special Future bucket.
- `{"version": "junk", "title": "Future"}` does the same.

## Root Cause Hypothesis
The helper is trying to prefer `version` over `title`, but it uses “non-empty string” as the decision point instead of “successfully parsed a valid version.” Once bad imported data puts any non-empty junk into `version`, the intended title fallback becomes unreachable.

## Suggested Fix
Normalize `version` with `strip()` and treat blank strings as absent. Then parse in separate stages instead of collapsing them into `version or title`:

1. Check normalized `version` for exact `"Future"`.
2. Try semver parsing on normalized `version`.
3. If that fails, run the title-based `"Future"` fallback.
4. If that also fails, try loose semver parsing on `title`.

Add regressions for:
- `{"version": "planned", "title": "v2.0.0 - Big Release"}`
- `{"version": " ", "title": "Future"}`
- `{"version": "junk", "title": "Future"}`