## Summary
`rule_id: logic-error` Duplicate `(from_state, to_state)` transitions are accepted, then silently collapsed in the transition cache, causing inconsistent behavior between validation and transition listing.

## Severity
- Severity: major
- Priority: P1

## Evidence
`src/filigree/templates.py:363` validates state references but does not detect duplicate transition pairs.
`src/filigree/templates.py:439` builds `_transition_cache` as a dict keyed by `(from_state, to_state)`, so later duplicates overwrite earlier ones.
`src/filigree/templates.py:595` iterates the raw transition list for `get_valid_transitions`, so duplicates are still surfaced there.
`src/filigree/templates.py:532` `validate_transition` uses the cache, so it sees only one of the duplicates.

```python
# src/filigree/templates.py
self._transition_cache[tpl.type] = {(t.from_state, t.to_state): t for t in tpl.transitions}
```

## Root Cause Hypothesis
The loader validates transition references but not uniqueness. A dict-based cache then implicitly chooses one transition definition, while other code paths still use the full list.

## Suggested Fix
In `parse_type_template()` or `validate_type_template()`, reject duplicate transition keys `(from_state, to_state)` with a clear `ValueError`. Keep cache/list semantics aligned by enforcing uniqueness at load time.

---
## Summary
`rule_id: type-error` Parser accepts transition enforcement value `"none"` even though the declared enforcement type is only `"hard" | "soft"`, leading to missing-field gates being neither enforced nor warned.

## Severity
- Severity: major
- Priority: P1

## Evidence
`src/filigree/templates.py:34` defines `EnforcementLevel = Literal["hard", "soft"]`.
`src/filigree/templates.py:272` accepts `{"hard", "soft", "none"}` at parse time.
`src/filigree/templates.py:556` blocks only when enforcement is `"hard"`.
`src/filigree/templates.py:564` warns only when enforcement is `"soft"`.
For `"none"`, `validate_transition()` returns `allowed=True` with missing fields, so callers do not block and do not warn.

```python
# src/filigree/templates.py
valid_enforcement = {"hard", "soft", "none"}
...
if transition.enforcement == "hard" and all_missing: ...
if transition.enforcement == "soft" and all_missing: ...
return TransitionResult(allowed=True, enforcement=transition.enforcement, missing_fields=all_missing, warnings=tuple(warnings))
```

## Root Cause Hypothesis
A legacy/extra enum value (`"none"`) is still accepted by parser validation but not represented in the typed model or enforcement logic branches.

## Suggested Fix
Remove `"none"` from accepted parser values, or explicitly normalize it to a supported semantic (for example `None` with explicit handling). Also tighten `TransitionDefinition` construction to reject enforcement values outside `"hard"`/`"soft"`.