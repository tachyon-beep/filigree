## Summary
`[logic-error]` `read_config()` backfills `prefix="filigree"` for missing/partial configs, but `from_filigree_dir()` has to override that for legacy installs, so different code paths derive different project identities for the same database.

## Severity
- Severity: major
- Priority: P1

## Evidence
`src/filigree/core.py:282-300` hardcodes `prefix="filigree"` in the default config and injects it whenever `config.json` is missing or omits `prefix`.

`src/filigree/core.py:458-470` then explicitly works around that behavior: `from_filigree_dir()` ignores the backfilled prefix and falls back to `filigree_dir.parent.name` because the hardcoded default is wrong for legacy installs.

`src/filigree/server.py:155-176` still trusts `read_config()` directly when registering projects and uses that derived prefix for collision detection and persisted routing metadata.

```python
# src/filigree/core.py:282-297
defaults = ProjectConfig(prefix="filigree", version=1, enabled_packs=["core", "planning", "release"])
...
if "prefix" not in result:
    result["prefix"] = defaults["prefix"]

# src/filigree/core.py:466-470
configured_prefix = _raw_config_prefix(filigree_dir / CONFIG_FILENAME)
prefix = configured_prefix if configured_prefix is not None else (filigree_dir.parent.name or "filigree")
```

That means the same legacy project can open as `myproj` through `from_filigree_dir()`, but register as `filigree` through `read_config()` consumers. In multi-project server mode, two legacy projects with missing prefixes will falsely collide under the same key.

## Root Cause Hypothesis
`core.py` has two incompatible definitions of the “effective prefix” for legacy projects. One path uses directory-name fallback, while `read_config()` fabricates a constant default. Callers get different identities depending on which helper they happen to call.

## Suggested Fix
Centralize effective-prefix resolution in `core.py` with a helper that uses raw config when present and `filigree_dir.parent.name` otherwise. Then make callers that need a real project identity use that helper instead of trusting `read_config()`’s backfilled `"filigree"` value.

---
## Summary
`[type-error]` `read_conf()` validates only key presence, not key types, so malformed `.filigree.conf` files can escape as raw `TypeError` from `from_conf()` and bypass the project’s friendly error handling.

## Severity
- Severity: major
- Priority: P2

## Evidence
`src/filigree/core.py:258-272` accepts any JSON object as long as it contains `prefix` and `db`; it does not require either field to be a non-empty string.

`src/filigree/core.py:496-505` immediately uses those values as typed strings:

```python
data = read_conf(conf_path)
db_path = (conf_path.parent / data["db"]).resolve()
prefix: str = data["prefix"]
```

If `db` is an integer or list, the path join raises `TypeError`. If `prefix` is non-string, later ID-prefix checks will also fail with raw type errors.

That exception shape leaks past higher-level handlers:
- `src/filigree/cli_common.py:45-50` catches `ValueError`, `OSError`, and `sqlite3.Error`, but not `TypeError`.
- `src/filigree/install_support/doctor.py:305-309` likewise treats unreadable confs as `JSONDecodeError`/`ValueError`/`OSError`, not `TypeError`.

So a malformed but JSON-valid `.filigree.conf` can crash commands with a traceback instead of producing the intended “corrupt config” message.

## Root Cause Hypothesis
Schema validation stops too early. `read_conf()` checks structural presence but not semantic types, while downstream code assumes the config is already type-safe.

## Suggested Fix
Strengthen `read_conf()` to validate `prefix` and `db` as non-empty strings before returning. Raise `ValueError` there, so `from_conf()`, CLI entry points, and doctor all keep their clean error-reporting behavior.

---
## Summary
`[type-error]` `FiligreeDB.__init__()` only rejects bare-string `enabled_packs` and blindly does `list(enabled_packs)` for everything else, which can silently mis-select packs or raise uncaught `TypeError` from malformed JSON config.

## Severity
- Severity: major
- Priority: P2

## Evidence
`src/filigree/core.py:445-449` contains the normalization logic:

```python
if enabled_packs is not None and isinstance(enabled_packs, str):
    raise TypeError(msg)
self._enabled_packs_override = list(enabled_packs) if enabled_packs is not None else None
```

`src/filigree/core.py:465-477` and `src/filigree/core.py:496-505` pass raw `enabled_packs` values from `config.json` / `.filigree.conf` straight into that constructor.

This creates two concrete failure modes:
- A dict value such as `{"core": false, "incident": true}` becomes `["core", "incident"]`, silently enabling packs based on dict keys instead of rejecting invalid config.
- A non-iterable value such as `42` raises `TypeError` before callers can downgrade it to a readable config error.

The repo already treats malformed `enabled_packs` as a real bug class in template loading: `tests/templates/test_registry.py:489-529` adds defensive handling specifically to avoid crashes and mis-selection from non-list values. The constructor path reintroduces the same problem one layer earlier.

## Root Cause Hypothesis
The constructor tries to be permissive by coercing “iterables” to a list, but configuration data needs schema validation, not generic iteration. Only the string case was guarded, leaving dicts and other invalid shapes untreated.

## Suggested Fix
Validate `enabled_packs` in `FiligreeDB.__init__()` as `None` or `list[str]` only. Reject any other type with a clear `TypeError`/`ValueError`, or reuse the same normalization rules already implemented in `TemplateRegistry.load()` so malformed configs are handled consistently across both entry paths.