"""Shared gitignore-aware parser for the project-root ``.filigree/`` rule.

Used both by ``ensure_gitignore`` (install side, which decides whether to
append a rule) and by ``run_doctor`` (which reports whether the rule is
already active). Keeping a single implementation prevents the two paths
from drifting on edge cases like comments, negations, and substrings —
the kind of drift that previously let the doctor pass projects whose
``.filigree/`` was not actually ignored (filigree-bc5d2af1ef).
"""

from __future__ import annotations

# Normalised forms that effectively ignore the project-root ``.filigree/``
# directory under gitignore semantics. ``.filigree[/]`` matches at any depth
# (including the root); the ``/``-anchored variants are explicitly root-scoped.
FILIGREE_IGNORE_RULES: frozenset[str] = frozenset({".filigree", ".filigree/", "/.filigree", "/.filigree/"})


def has_active_filigree_ignore(content: str) -> bool:
    """Return True if *content* has an active ignore rule for project-root ``.filigree/``.

    Honours gitignore syntax: blank lines and ``#`` comments are skipped,
    trailing whitespace is stripped. ``!``-prefixed negations are processed
    in declaration order — a later ``!.filigree/`` un-ignores an earlier
    ``.filigree/`` rule, matching ``git``'s actual semantics. Substring
    matches (``src/.filigree/cache/``, ``#.filigree/``) do not count.
    """
    state: bool | None = None
    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        negated = stripped.startswith("!")
        candidate = stripped[1:] if negated else stripped
        if candidate in FILIGREE_IGNORE_RULES:
            state = not negated
    return state is True
