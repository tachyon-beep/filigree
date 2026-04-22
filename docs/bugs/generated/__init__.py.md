## Summary
api-misuse: `__version__` falls back to `"0.0.0-dev"` when distribution metadata is unavailable, so source-checkout runs advertise the wrong Filigree release.

## Severity
- Severity: minor
- Priority: P2

## Evidence
- [src/filigree/__init__.py](/home/john/filigree/src/filigree/__init__.py:8) hard-codes a generic placeholder whenever `importlib.metadata.version("filigree")` cannot find installed metadata:
```python
try:
    __version__ = version("filigree")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
```
- [pyproject.toml](/home/john/filigree/pyproject.toml:5) declares this checkout as version `2.0.0`, so the fallback is wrong for the current source tree.
- [src/filigree/cli.py](/home/john/filigree/src/filigree/cli.py:11) passes `__version__` to `click.version_option`, so `filigree --version` reports the bogus fallback in source-checkout execution paths.
- [src/filigree/dashboard.py](/home/john/filigree/src/filigree/dashboard.py:394) returns `__version__` from `/api/health`, so the dashboard health API also exposes the wrong version.
- [src/filigree/install.py](/home/john/filigree/src/filigree/install.py:110) explicitly says the package `__version__` “handles source-checkout cases”, but it inherits the same incorrect fallback.

## Root Cause Hypothesis
`src/filigree/__init__.py` treats installed distribution metadata as the only authoritative version source. In a source checkout there is usually no `dist-info`, so the package drops the real project version and substitutes `"0.0.0-dev"` instead of consulting a checkout-local source of truth.

## Suggested Fix
Keep `importlib.metadata.version("filigree")` as the fast path, but on `PackageNotFoundError` read the version from a stable source-tree location such as `pyproject.toml` via `tomllib`, or from a dedicated generated module like `filigree/_version.py`. Reserve `"0.0.0-dev"` as the final fallback only when neither installed metadata nor source metadata is available.