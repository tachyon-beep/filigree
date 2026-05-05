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

This test is **fail-closed**: it walks every .py file under src/filigree/ and
fails if ``classify_value_error`` appears outside the small allowlist of
legitimate state-machine sites. New modules that hardcode ErrorCode.VALIDATION
are covered automatically; a regression that introduces the heuristic at a
new site fails CI with a clear message pointing at this file.

Adding a new legitimate state-machine site? Extend ``ALLOWED_MODULES`` below
AND justify it in the PR: the rule is that the helper is only valid where
a single ``db.*`` call can raise a ValueError for either "bad input" or
"not in the right state" and the caller needs to tell them apart.

See the full rule in src/filigree/types/api.py:classify_value_error's
docstring and the 2B rebaseline doc §Task 2b.4.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_ROOT = _REPO_ROOT / "src" / "filigree"

# Files permitted to reference ``classify_value_error``. All other modules
# under src/filigree/ must hardcode ``ErrorCode.VALIDATION`` for ValueError
# paths. Keep this list small and justified — see module docstring.
ALLOWED_MODULES = frozenset(
    {
        # Definition site.
        "src/filigree/types/api.py",
        # State-machine call sites: each wraps a ``db.*`` method where a
        # single ValueError can mean either "bad input" or
        # "invalid state transition" and callers must disambiguate.
        "src/filigree/db_issues.py",
        "src/filigree/mcp_tools/issues.py",
        "src/filigree/dashboard_routes/issues.py",
        "src/filigree/cli_commands/issues.py",
    }
)


def test_classify_value_error_only_at_state_machine_sites() -> None:
    """Fail-closed boundary: classify_value_error outside the allowlist fails CI."""
    # Sanity: allowlist entries must exist. Catches renames/moves.
    for rel in ALLOWED_MODULES:
        assert (_REPO_ROOT / rel).exists(), f"ALLOWED_MODULES entry missing on disk: {rel}"

    offenders: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if rel in ALLOWED_MODULES:
            continue
        if "classify_value_error" in path.read_text():
            offenders.append(rel)

    assert not offenders, (
        f"classify_value_error used outside state-machine sites: {offenders}. "
        f"It is a substring heuristic that distinguishes INVALID_TRANSITION from "
        f"VALIDATION ValueErrors at db.* state-transition wrappers only. "
        f"Input-validation sites must hardcode ErrorCode.VALIDATION. "
        f"If the new site legitimately wraps a db.* state transition, add it to "
        f"ALLOWED_MODULES in this file and justify in the PR. "
        f"See src/filigree/types/api.py:classify_value_error docstring and "
        f"docs/plans/2026-04-23-2.0-stage-2b-rebaseline.md §Task 2b.4."
    )
