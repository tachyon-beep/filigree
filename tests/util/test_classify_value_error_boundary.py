"""Enforcement of the `classify_value_error` boundary rule (2B task 2b.4).

``classify_value_error`` (src/filigree/types/api.py) is a substring heuristic
that distinguishes INVALID_TRANSITION from VALIDATION ValueErrors raised by
``db.*`` methods that wrap a state-machine transition. The heuristic is
correct at state-machine sites — where ValueErrors come from multiple
classes of failure and the caller needs to disambiguate — but wrong at
input-validation sites — where ValueErrors only ever mean "bad input from
the caller" and running them through the heuristic would mis-classify any
future error message containing "status"/"state"/"transition" as
INVALID_TRANSITION.

This test greps the input-validation modules and fails CI if the helper
appears anywhere it shouldn't. Adding a new input-validation module?
Extend INPUT_VALIDATION_MODULES below.

See the full rule in src/filigree/types/api.py:classify_value_error's
docstring and the 2B rebaseline doc §Task 2b.4.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

INPUT_VALIDATION_MODULES = (
    "src/filigree/dashboard_routes/common.py",  # _safe_path, URL parsing
    "src/filigree/mcp_tools/meta.py",
    "src/filigree/mcp_tools/files.py",
    "src/filigree/mcp_tools/scanners.py",
    "src/filigree/mcp_tools/observations.py",
)


def test_classify_value_error_not_used_at_input_validation_sites() -> None:
    for rel in INPUT_VALIDATION_MODULES:
        path = _REPO_ROOT / rel
        assert path.exists(), f"{rel} not found — update INPUT_VALIDATION_MODULES list?"
        content = path.read_text()
        assert "classify_value_error" not in content, (
            f"{rel} is an input-validation site; it must hardcode "
            f"ErrorCode.VALIDATION, not route through classify_value_error. "
            f"See src/filigree/types/api.py:classify_value_error docstring "
            f"and docs/plans/2026-04-23-2.0-stage-2b-rebaseline.md §Task 2b.4."
        )
