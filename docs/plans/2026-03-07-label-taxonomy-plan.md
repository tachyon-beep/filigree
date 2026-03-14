# Label Taxonomy & Soft-Search System — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a unified label query surface with virtual labels, namespace search, discovery tools, and auto-tagging — so agents can search issues by `age:stale`, `has:findings`, `--label-prefix=cluster:`, etc.

**Architecture:** Three PRs ship incrementally. PR1 adds virtual label resolution and query improvements to `list_issues` (zero schema changes). PR2 adds `list_labels` and `get_label_taxonomy` discovery tools. PR3 adds the `origin` column, auto-tag sync, and import/export fixes.

---

### Implementation Order & Sequencing Rules

Execute tasks **strictly in order**: 1 → 2 → 3 → 4 → 5 → 6 (PR1), then 7 → 8 → 9 → 10 (PR2), then 11 → 12 → 13 → 15 (PR3). Task 14 is deferred.

**No parallelism is possible** — tasks within each PR modify overlapping files, and PRs are strictly sequential:

- **PR1 → PR2:** Task 7 (`list_labels`) imports `RESERVED_NAMESPACES_AUTO` and `RESERVED_NAMESPACES_VIRTUAL` from `db_workflow.py`, which are created in Task 1.
- **PR2 → PR3:** Task 12 (`_sync_auto_tags`) builds on the `list_labels` infrastructure for virtual count computation.
- **Within PR1:** Task 3 depends on Task 1's namespace constants (`RESERVED_NAMESPACES`). Task 4 depends on Task 3's `list_issues` signature changes. Task 5 depends on Task 3's CLI-level changes.

**Critical conventions the implementation agent must know:**

1. **Sync test (`test_input_type_contracts.py`):** Tools with non-empty `inputSchema.properties` MUST have a `TOOL_ARGS_MAP` entry. Tools with empty properties MUST NOT. Violating this breaks the sync test.
2. **No worktrees.** Work directly on the feature branch.
3. **Never switch branches** without explicit user approval.
4. **Pre-push CI:** Always run `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/filigree/ && uv run pytest --tb=short` before pushing.
5. **Transaction contracts:** Methods that modify DB state but don't commit (like `_sync_auto_tags`) must document that the caller owns commit/rollback. This matches the existing `_record_event` pattern.

---

**Tech Stack:** Python 3.11+, SQLite, MCP SDK, pytest, Click (CLI), FastAPI (dashboard)

**Module layout (post-refactor):**
- Label validation: `WorkflowMixin` in `db_workflow.py` (`_validate_label_name` at line 203)
- Label CRUD: `MetaMixin` in `db_meta.py` (`add_label` at line 61, `remove_label` at line 74)
- Issue queries: `IssuesMixin` in `db_issues.py` (`list_issues` at line 847, `search_issues` at line 907)
- Protocol: `DBMixinProtocol` in `db_base.py` (line 25; `list_issues` signature at lines 101-112)
- Types: `types/inputs.py` (`ListIssuesArgs` at line 43)
- Import/export: `MetaMixin` in `db_meta.py` (`import_jsonl` label INSERT at line 522)
- Note: `db_query.py` does not exist — all query logic is in `db_issues.py`

**Design doc:** `docs/plans/2026-03-06-label-taxonomy-design.md`

---

## PR 1 — Virtual Labels + Query Improvements

Zero schema changes. Highest value, lowest risk.

---

### Task 1: Add namespace reservation to `_validate_label_name`

**Files:**
- Modify: `src/filigree/db_workflow.py:197-215` (`_reserved_label_names` at 199, `_validate_label_name` at 203)
- Test: `tests/core/test_crud.py`

**Step 1: Write failing tests**

Add to `tests/core/test_crud.py`:

```python
class TestNamespaceReservation:
    """Reserved namespaces cannot be used as manual labels."""

    @pytest.mark.parametrize("label", [
        "area:mcp", "severity:high", "scanner:ruff", "pack:core",
        "age:stale", "has:blockers",
        "AREA:MCP",  # case-insensitive
    ])
    def test_reserved_namespace_rejected(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        with pytest.raises(ValueError, match="namespace"):
            db.add_label(issue.id, label)

    @pytest.mark.parametrize("label", [
        "cluster:broad-except", "effort:m", "review:needed",
        "wait:upstream", "tech-debt", "needs-review",
    ])
    def test_manual_namespace_allowed(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        assert db.add_label(issue.id, label) is True

    @pytest.mark.parametrize("label", [
        "has\nnewline", "has\rcarriage", "has\x00null", "has\x1fcontrol",
    ])
    def test_control_characters_rejected(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        with pytest.raises(ValueError, match="control characters"):
            db.add_label(issue.id, label)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_crud.py::TestNamespaceReservation -v`
Expected: FAIL — reserved namespaces currently accepted

**Step 3: Implement namespace reservation**

In `src/filigree/db_workflow.py`, replace `_reserved_label_names` and `_validate_label_name`:

```python
# Line ~197, add constant
RESERVED_NAMESPACES_AUTO: frozenset[str] = frozenset({
    "area", "severity", "scanner", "pack", "lang", "rule",
})
RESERVED_NAMESPACES_VIRTUAL: frozenset[str] = frozenset({"age", "has"})
RESERVED_NAMESPACES: frozenset[str] = RESERVED_NAMESPACES_AUTO | RESERVED_NAMESPACES_VIRTUAL

# Replace _validate_label_name (line 203)
def _validate_label_name(self, label: str) -> str:
    """Normalize and validate a label before writing it."""
    if not isinstance(label, str):
        msg = "Label must be a string"
        raise ValueError(msg)
    normalized = label.strip()
    if not normalized:
        msg = "Label cannot be empty"
        raise ValueError(msg)
    # Reject control characters (would corrupt JSONL export line boundaries)
    if any(ord(c) < 32 or c == "\x7f" for c in normalized):
        msg = "Label contains control characters"
        raise ValueError(msg)
    if normalized.casefold() in self._reserved_label_names():
        msg = f"Label '{normalized}' is reserved as an issue type name; set the issue type explicitly instead."
        raise ValueError(msg)
    # Check namespace reservation
    if ":" in normalized:
        ns = normalized.split(":", 1)[0].casefold()
        if ns in RESERVED_NAMESPACES_AUTO:
            msg = f"{ns}: is a system-managed auto-tag namespace. These labels are computed automatically."
            raise ValueError(msg)
        if ns in RESERVED_NAMESPACES_VIRTUAL:
            msg = f"{ns}: is a virtual namespace computed at query time. You can filter by it with --label but cannot add it manually."
            raise ValueError(msg)
    return normalized
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_crud.py::TestNamespaceReservation -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/db_workflow.py tests/core/test_crud.py
git commit -m "feat(labels): reserve auto-tag and virtual namespaces in _validate_label_name"
```

---

### Task 2: Add `review:` mutual exclusivity to `add_label`

**Files:**
- Modify: `src/filigree/db_meta.py:61-72`
- Test: `tests/core/test_crud.py`

**Step 1: Write failing test**

```python
class TestReviewMutualExclusivity:
    def test_adding_review_label_removes_prior(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        db.add_label(issue.id, "review:needed")
        db.add_label(issue.id, "review:done")
        refreshed = db.get_issue(issue.id)
        assert "review:done" in refreshed.labels
        assert "review:needed" not in refreshed.labels

    def test_review_labels_dont_affect_other_labels(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        db.add_label(issue.id, "tech-debt")
        db.add_label(issue.id, "review:needed")
        db.add_label(issue.id, "review:rework")
        refreshed = db.get_issue(issue.id)
        assert "review:rework" in refreshed.labels
        assert "review:needed" not in refreshed.labels
        assert "tech-debt" in refreshed.labels
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_crud.py::TestReviewMutualExclusivity -v`
Expected: FAIL — `review:needed` still present

**Step 3: Implement mutual exclusivity in `add_label`**

In `src/filigree/db_meta.py`, modify `add_label` (line 61):

```python
def add_label(self, issue_id: str, label: str) -> bool:
    normalized = self._validate_label_name(label)
    try:
        # Mutual exclusivity for review: namespace
        if normalized.startswith("review:"):
            self.conn.execute(
                "DELETE FROM labels WHERE issue_id = ? AND label LIKE 'review:%'",
                (issue_id,),
            )
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO labels (issue_id, label) VALUES (?, ?)",
            (issue_id, normalized),
        )
        self.conn.commit()
    except Exception:
        self.conn.rollback()
        raise
    return cursor.rowcount > 0
```

**Step 4: Run tests**

Run: `uv run pytest tests/core/test_crud.py::TestReviewMutualExclusivity -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/db_meta.py tests/core/test_crud.py
git commit -m "feat(labels): review: namespace with mutual exclusivity"
```

---

### Task 3: Update `list_issues` — array labels, prefix, not-label, virtual dispatch

This is the core task of PR1. It modifies the `list_issues` signature, protocol, types, and query building.

**Files:**
- Modify: `src/filigree/db_base.py:101-112` (protocol + add `AGE_BUCKETS` constant)
- Modify: `src/filigree/types/inputs.py:36-46` (ListIssuesArgs)
- Modify: `src/filigree/db_issues.py:847-905` (list_issues impl)
- Test: `tests/core/test_label_query.py` (new file)

**Step 1: Write failing tests**

Create `tests/core/test_label_query.py`:

```python
"""Tests for label query improvements: array labels, prefix, not-label, virtual labels."""

from __future__ import annotations

import pytest
from filigree.core import FiligreeDB


class TestArrayLabels:
    """Multiple --label filters use AND logic."""

    def test_single_label_filter(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["bug", "urgent"])
        b = db.create_issue("B", labels=["bug"])
        results = db.list_issues(label=["bug"])
        ids = [i.id for i in results]
        assert a.id in ids
        assert b.id in ids

    def test_multiple_labels_and_logic(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["bug", "urgent"])
        db.create_issue("B", labels=["bug"])
        results = db.list_issues(label=["bug", "urgent"])
        ids = [i.id for i in results]
        assert ids == [a.id]

    def test_backward_compat_string_label(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["bug"])
        results = db.list_issues(label="bug")
        assert len(results) == 1
        assert results[0].id == a.id


class TestLabelPrefix:
    """--label-prefix matches namespace."""

    def test_prefix_matches_namespace(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["cluster:broad-except"])
        b = db.create_issue("B", labels=["cluster:race-condition"])
        db.create_issue("C", labels=["effort:m"])
        results = db.list_issues(label_prefix="cluster:")
        ids = [i.id for i in results]
        assert a.id in ids
        assert b.id in ids
        assert len(ids) == 2

    def test_prefix_requires_trailing_colon(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="trailing colon"):
            db.list_issues(label_prefix="cluster")

    def test_prefix_combined_with_label_is_and(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["cluster:broad-except", "urgent"])
        db.create_issue("B", labels=["cluster:broad-except"])
        results = db.list_issues(label=["urgent"], label_prefix="cluster:")
        ids = [i.id for i in results]
        assert ids == [a.id]


class TestNotLabel:
    """--not-label negation filter."""

    def test_not_label_exact(self, db: FiligreeDB) -> None:
        db.create_issue("A", labels=["wontfix"])
        b = db.create_issue("B", labels=["bug"])
        results = db.list_issues(not_label="wontfix")
        ids = [i.id for i in results]
        assert b.id in ids
        assert len([i for i in results if "wontfix" in i.labels]) == 0

    def test_not_label_prefix(self, db: FiligreeDB) -> None:
        db.create_issue("A", labels=["wait:upstream"])
        b = db.create_issue("B", labels=["bug"])
        results = db.list_issues(not_label="wait:")
        ids = [i.id for i in results]
        assert b.id in ids


class TestVirtualLabels:
    """Virtual labels resolve to SQL at query time."""

    def test_age_fresh(self, db: FiligreeDB) -> None:
        """Newly created issues are age:fresh."""
        db.create_issue("A")
        results = db.list_issues(label=["age:fresh"])
        assert len(results) == 1

    def test_age_stale_no_recent_issues(self, db: FiligreeDB) -> None:
        """Fresh issues should not match age:stale."""
        db.create_issue("A")
        results = db.list_issues(label=["age:stale"])
        assert len(results) == 0

    def test_has_comments(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        db.create_issue("B")
        db.add_comment(a.id, "hello")
        results = db.list_issues(label=["has:comments"])
        assert len(results) == 1
        assert results[0].id == a.id

    def test_has_children(self, db: FiligreeDB) -> None:
        parent = db.create_issue("Parent", type="epic")
        db.create_issue("Child", parent_id=parent.id)
        db.create_issue("Orphan")
        results = db.list_issues(label=["has:children"])
        assert len(results) == 1
        assert results[0].id == parent.id

    def test_has_files(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        db.create_issue("B")
        file_rec = db.register_file("src/core.py")
        db.add_file_association(file_rec.id, a.id, "bug_in")  # register_file returns FileRecord dataclass
        results = db.list_issues(label=["has:files"])
        assert len(results) == 1
        assert results[0].id == a.id

    def test_unknown_virtual_returns_empty(self, db: FiligreeDB) -> None:
        db.create_issue("A")
        results = db.list_issues(label=["age:garbage"])
        assert len(results) == 0

    def test_not_label_virtual(self, db: FiligreeDB) -> None:
        """Negation works on virtual labels."""
        db.create_issue("A")
        results = db.list_issues(not_label="age:fresh")
        assert len(results) == 0  # all issues are fresh


class TestVirtualAndStoredCombined:
    def test_virtual_and_stored_label_and(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["bug"])
        db.create_issue("B")
        results = db.list_issues(label=["age:fresh", "bug"])
        assert len(results) == 1
        assert results[0].id == a.id
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_label_query.py -v`
Expected: FAIL — `label_prefix`, `not_label`, array `label` not yet supported

**Step 3: Update protocol in `db_base.py`**

Modify `src/filigree/db_base.py:101-112` — update `list_issues` signature:

```python
def list_issues(
    self,
    *,
    status: str | None = None,
    type: str | None = None,
    priority: int | None = None,
    parent_id: str | None = None,
    assignee: str | None = None,
    label: str | list[str] | None = None,
    label_prefix: str | None = None,
    not_label: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Issue]: ...
```

> **Note:** The protocol at `db_base.py:25` (`DBMixinProtocol`) declares `add_label` (line 116) but does NOT declare `remove_label`. If this PR or PR3 modifies `db_base.py`, also add a `remove_label` declaration to the protocol for cross-mixin type safety.

**Step 4: Update `ListIssuesArgs` in `types/inputs.py`**

Modify `src/filigree/types/inputs.py:36-46`:

```python
class ListIssuesArgs(TypedDict):
    status: NotRequired[str]
    status_category: NotRequired[str]
    type: NotRequired[str]
    priority: NotRequired[int]
    parent_id: NotRequired[str]
    assignee: NotRequired[str]
    label: NotRequired[str | list[str]]
    label_prefix: NotRequired[str]
    not_label: NotRequired[str]
    limit: NotRequired[int]
    offset: NotRequired[int]
    no_limit: NotRequired[bool]
```

**Step 5: Implement virtual label resolver and updated `list_issues`**

First, add `_AGE_BUCKETS` to `src/filigree/db_base.py` (shared constant accessible to all mixins without cross-mixin imports):

```python
# Add near top of db_base.py, after existing constants
# Virtual label dispatch — explicit allowlist, no prefix matching
AGE_BUCKETS: dict[str, tuple[int, int]] = {
    "fresh":   (0, 7),
    "recent":  (7, 30),
    "aging":   (30, 90),
    "stale":   (90, 180),
    "ancient": (180, 999999),
}
```

Then modify `src/filigree/db_issues.py`. Add the virtual label resolver before `list_issues`, then update the method:

```python
# Add near top of file, after imports
import logging

from filigree.db_base import AGE_BUCKETS

logger = logging.getLogger(__name__)


def _escape_like(value: str) -> str:
    """Escape LIKE wildcard characters (%, _) to prevent pattern broadening."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _resolve_virtual_label(
    label: str,
    *,
    negate: bool = False,
    done_states: list[str] | None = None,
) -> tuple[str, list[Any]] | None:
    """Resolve a virtual label to a SQL condition + params.

    Returns (sql_fragment, params) or None if not a virtual label.
    The done_states parameter is required for has:blockers resolution.
    """
    if label.startswith("age:"):
        value = label.split(":", 1)[1]
        bucket = AGE_BUCKETS.get(value)
        if bucket is None:
            logger.warning("Unknown virtual label value: %s", label)
            return ("1=0" if not negate else "1=1", [])
        low, high = bucket
        # Use datetime() for index-scannable range queries on created_at
        if negate:
            return (
                "(i.created_at > datetime('now', ?) OR i.created_at <= datetime('now', ?))",
                [f"-{low} days", f"-{high} days"],
            )
        return (
            "i.created_at <= datetime('now', ?) AND i.created_at > datetime('now', ?)",
            [f"-{low} days", f"-{high} days"],
        )

    if label.startswith("has:"):
        value = label.split(":", 1)[1]
        exists_op = "NOT EXISTS" if negate else "EXISTS"
        # Resolve done states with fallback for empty-workflow edge case
        effective_done = done_states or ["closed"]
        done_ph = ",".join("?" * len(effective_done))
        subqueries: dict[str, tuple[str, list[Any]]] = {
            "blockers": (
                f"{exists_op} (SELECT 1 FROM dependencies d "
                "JOIN issues blocker ON d.depends_on_id = blocker.id "
                "WHERE d.issue_id = i.id AND blocker.status NOT IN "
                f"({done_ph}))",
                list(effective_done),
            ),
            "children": (
                f"{exists_op} (SELECT 1 FROM issues child WHERE child.parent_id = i.id)",
                [],
            ),
            "findings": (
                f"{exists_op} (SELECT 1 FROM scan_findings sf "
                "WHERE sf.issue_id = i.id AND sf.status NOT IN ('fixed', 'false_positive'))",
                [],
            ),
            "files": (
                f"{exists_op} (SELECT 1 FROM file_associations fa WHERE fa.issue_id = i.id)",
                [],
            ),
            "comments": (
                f"{exists_op} (SELECT 1 FROM comments c WHERE c.issue_id = i.id)",
                [],
            ),
        }
        entry = subqueries.get(value)
        if entry is None:
            logger.warning("Unknown virtual label value: %s", label)
            return ("1=0" if not negate else "1=1", [])
        return entry

    return None  # Not a virtual label
```

Then update the `list_issues` method itself (line 850):

```python
def list_issues(
    self,
    *,
    status: str | None = None,
    type: str | None = None,
    priority: int | None = None,
    parent_id: str | None = None,
    assignee: str | None = None,
    label: str | list[str] | None = None,
    label_prefix: str | None = None,
    not_label: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Issue]:
    if limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")
    if offset < 0:
        raise ValueError(f"offset must be non-negative, got {offset}")
    if label_prefix is not None and not label_prefix.endswith(":"):
        msg = f"label_prefix must include a trailing colon (got {label_prefix!r})"
        raise ValueError(msg)

    # Normalize label to list
    if isinstance(label, str):
        label = [label]

    conditions: list[str] = []
    params: list[Any] = []

    # Get done states once for virtual has:blockers
    done_states = self._get_states_for_category("done")

    if status is not None:
        category_aliases = {"in_progress": "wip", "closed": "done"}
        category_key = category_aliases.get(status, status)
        category_states: list[str] = []
        if category_key in ("open", "wip", "done"):
            category_states = self._get_states_for_category(category_key)
        if category_states:
            placeholders = ",".join("?" * len(category_states))
            conditions.append(f"i.status IN ({placeholders})")
            params.extend(category_states)
        else:
            conditions.append("i.status = ?")
            params.append(status)
    if type is not None:
        conditions.append("i.type = ?")
        params.append(type)
    if priority is not None:
        conditions.append("i.priority = ?")
        params.append(priority)
    if parent_id is not None:
        conditions.append("i.parent_id = ?")
        params.append(parent_id)
    if assignee is not None:
        conditions.append("i.assignee = ?")
        params.append(assignee)

    # Label filters (array, AND logic)
    if label:
        for lbl in label:
            virtual = _resolve_virtual_label(lbl, negate=False, done_states=done_states)
            if virtual is not None:
                sql_frag, vparams = virtual
                conditions.append(f"({sql_frag})")
                params.extend(vparams)
            else:
                conditions.append("i.id IN (SELECT issue_id FROM labels WHERE label = ?)")
                params.append(lbl)

    # Label prefix filter (escape LIKE wildcards to prevent pattern broadening)
    if label_prefix is not None:
        escaped = _escape_like(label_prefix)
        conditions.append("i.id IN (SELECT issue_id FROM labels WHERE label LIKE ? ESCAPE '\\')")
        params.append(escaped + "%")

    # Not-label filter
    if not_label is not None:
        if not_label.endswith(":"):
            # Prefix negation — check if it's a virtual namespace
            ns = not_label.rstrip(":")
            if ns in ("age", "has"):
                msg = f"Cannot negate virtual namespace prefix {not_label!r} — use a specific value like {ns}:stale"
                raise ValueError(msg)
            escaped = _escape_like(not_label)
            conditions.append("i.id NOT IN (SELECT issue_id FROM labels WHERE label LIKE ? ESCAPE '\\')")
            params.append(escaped + "%")
        else:
            virtual = _resolve_virtual_label(not_label, negate=True, done_states=done_states)
            if virtual is not None:
                sql_frag, vparams = virtual
                conditions.append(f"({sql_frag})")
                params.extend(vparams)
            else:
                conditions.append("i.id NOT IN (SELECT issue_id FROM labels WHERE label = ?)")
                params.append(not_label)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])
    rows = self.conn.execute(
        f"SELECT i.id FROM issues i{where} ORDER BY i.priority, i.created_at LIMIT ? OFFSET ?",
        params,
    ).fetchall()

    return self._build_issues_batch([r["id"] for r in rows])
```

**Step 6: Run tests**

Run: `uv run pytest tests/core/test_label_query.py -v`
Expected: PASS

**Step 7: Commit**

```bash
git add src/filigree/db_base.py src/filigree/types/inputs.py src/filigree/db_issues.py tests/core/test_label_query.py
git commit -m "feat(labels): virtual labels, array labels, prefix search, and not-label in list_issues"
```

---

### Task 4: Update MCP tool schema for `list_issues`

**Files:**
- Modify: `src/filigree/mcp_tools/issues.py:71-107` (schema)
- Modify: `src/filigree/mcp_tools/issues.py:~385` (handler)
- Test: `tests/mcp/test_tools.py`

**Step 1: Update tool schema**

In `src/filigree/mcp_tools/issues.py`, update the `list_issues` tool inputSchema:

```python
"label": {
    "oneOf": [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
    ],
    "description": "Filter by label(s). Multiple labels use AND logic. Supports virtual labels (age:fresh, has:findings).",
},
"label_prefix": {
    "type": "string",
    "description": "Filter by label namespace prefix (must include trailing colon, e.g. 'cluster:')",
},
"not_label": {
    "type": "string",
    "description": "Exclude issues with this label. Supports exact match, prefix (trailing colon), and virtual labels.",
},
```

**Step 2: Update handler**

In the `list_issues` handler (around line 385), pass new params:

```python
issues = tracker.list_issues(
    status=args.get("status"),
    type=args.get("type"),
    priority=args.get("priority"),
    parent_id=args.get("parent_id"),
    assignee=args.get("assignee"),
    label=args.get("label"),
    label_prefix=args.get("label_prefix"),
    not_label=args.get("not_label"),
    limit=limit,
    offset=offset,
)
```

**Step 3: Write MCP integration test**

Add to `tests/mcp/test_tools.py` (using `mcp_db` fixture + `call_tool()` — the established MCP test pattern):

```python
from tests.mcp._helpers import _parse

class TestListIssuesLabelQuery:
    async def test_label_prefix_via_mcp(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("A", labels=["cluster:broad-except"])
        mcp_db.create_issue("B", labels=["effort:m"])
        result = await call_tool("list_issues", {"label_prefix": "cluster:"})
        data = _parse(result)
        assert len(data["issues"]) == 1

    async def test_virtual_label_via_mcp(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("A")
        result = await call_tool("list_issues", {"label": "age:fresh"})
        data = _parse(result)
        assert len(data["issues"]) >= 1

    async def test_array_labels_via_mcp(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("A", labels=["bug", "urgent"])
        mcp_db.create_issue("B", labels=["bug"])
        result = await call_tool("list_issues", {"label": ["bug", "urgent"]})
        data = _parse(result)
        assert len(data["issues"]) == 1

    async def test_not_label_via_mcp(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("A", labels=["wontfix"])
        mcp_db.create_issue("B", labels=["bug"])
        result = await call_tool("list_issues", {"not_label": "wontfix"})
        data = _parse(result)
        assert all("wontfix" not in i.get("labels", []) for i in data["issues"])

    async def test_label_prefix_without_colon_errors(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("list_issues", {"label_prefix": "cluster"})
        assert result[0].text and "trailing colon" in result[0].text
```

**Step 4: Run tests**

Run: `uv run pytest tests/mcp/test_tools.py::TestListIssuesLabelQuery -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/filigree/mcp_tools/issues.py tests/mcp/test_tools.py
git commit -m "feat(mcp): update list_issues tool schema for label_prefix, not_label, array labels"
```

---

### Task 5: Update CLI for new label query params

**Files:**
- Modify: `src/filigree/cli_commands/issues.py:135-177`
- Test: `tests/cli/test_issue_commands.py`

**Step 1: Update CLI options**

In `src/filigree/cli_commands/issues.py`, update the `list_issues` command:

```python
@click.command("list")
@click.option("--status", default=None, help="Filter by status")
@click.option("--type", "issue_type", default=None, help="Filter by type")
@click.option("--priority", "-p", default=None, type=click.IntRange(0, 4), help="Filter by priority")
@click.option("--parent", default=None, help="Filter by parent ID")
@click.option("--assignee", default=None, help="Filter by assignee")
@click.option("--label", "-l", multiple=True, help="Filter by label (repeatable, AND logic)")
@click.option("--label-prefix", default=None, help="Filter by label namespace prefix (include trailing colon)")
@click.option("--not-label", default=None, help="Exclude issues with this label")
@click.option("--limit", default=100, type=int, help="Max results (default 100)")
@click.option("--offset", default=0, type=int, help="Skip first N results")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_issues(
    status, issue_type, priority, parent, assignee,
    label, label_prefix, not_label, limit, offset, as_json,
):
    with get_db() as db:
        label_filter = list(label) if label else None
        issues = db.list_issues(
            status=status,
            type=issue_type,
            priority=priority,
            parent_id=parent,
            assignee=assignee,
            label=label_filter,
            label_prefix=label_prefix,
            not_label=not_label,
            limit=limit,
            offset=offset,
        )
        # ... rest unchanged
```

**Step 2: Write CLI test**

Add to `tests/cli/test_issue_commands.py` (using `cli_in_project` fixture — the established CLI test pattern):

```python
class TestListLabelQuery:
    def test_list_label_prefix(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A", "-l", "cluster:broad-except"])
        runner.invoke(cli, ["create", "Issue B", "-l", "effort:m"])
        result = runner.invoke(cli, ["list", "--label-prefix", "cluster:"])
        assert result.exit_code == 0
        assert "Issue A" in result.output

    def test_list_not_label(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A", "-l", "wontfix"])
        runner.invoke(cli, ["create", "Issue B", "-l", "bug"])
        result = runner.invoke(cli, ["list", "--not-label", "wontfix"])
        assert result.exit_code == 0
        assert "Issue A" not in result.output

    def test_list_multiple_labels_and(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A", "-l", "bug", "-l", "urgent"])
        runner.invoke(cli, ["create", "Issue B", "-l", "bug"])
        result = runner.invoke(cli, ["list", "-l", "bug", "-l", "urgent"])
        assert result.exit_code == 0
        assert "Issue A" in result.output

    def test_list_virtual_label_via_cli(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Fresh issue"])
        result = runner.invoke(cli, ["list", "-l", "age:fresh"])
        assert result.exit_code == 0
        assert "Fresh issue" in result.output
```

**Step 3: Run tests**

Run: `uv run pytest tests/cli/test_issue_commands.py::test_list_label_prefix -v`
Expected: PASS

**Step 4: Commit**

```bash
git add src/filigree/cli_commands/issues.py tests/cli/test_issue_commands.py
git commit -m "feat(cli): add --label-prefix, --not-label, repeatable --label to list command"
```

---

### Task 6: Run full test suite and prepare PR1

**Step 1: Run full CI pipeline**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short
```

Fix any issues that arise.

**Step 2: Commit any fixes and push**

```bash
git push origin feat/dashboard-ux-and-observations
```

---

## PR 2 — Discovery Tools

Read-only tools, no schema changes. Depends on PR1 for namespace constants.

---

### Task 7: Add `list_labels` DB method

**Files:**
- Modify: `src/filigree/db_meta.py` (add method)
- Modify: `src/filigree/db_base.py` (protocol)
- Test: `tests/core/test_label_discovery.py` (new file)

**Step 1: Write failing tests**

Create `tests/core/test_label_discovery.py`:

```python
"""Tests for list_labels and get_label_taxonomy."""

from __future__ import annotations

import pytest
from filigree.core import FiligreeDB


class TestListLabels:
    def test_empty_project_includes_virtual_namespaces(self, db: FiligreeDB) -> None:
        result = db.list_labels()
        assert "age" in result["namespaces"]
        assert "has" in result["namespaces"]
        assert result["namespaces"]["age"]["type"] == "virtual"
        assert result["namespaces"]["age"]["writable"] is False

    def test_returns_manual_labels_grouped(self, db: FiligreeDB) -> None:
        db.create_issue("A", labels=["cluster:broad-except", "cluster:null-check"])
        db.create_issue("B", labels=["cluster:broad-except", "effort:m"])
        result = db.list_labels()
        cluster = result["namespaces"]["cluster"]
        assert cluster["type"] == "manual"
        assert cluster["writable"] is True
        labels = {l["label"]: l["count"] for l in cluster["labels"]}
        assert labels["cluster:broad-except"] == 2
        assert labels["cluster:null-check"] == 1

    def test_sorted_alphabetically(self, db: FiligreeDB) -> None:
        db.create_issue("A", labels=["cluster:zebra", "cluster:alpha", "cluster:mid"])
        result = db.list_labels()
        cluster_labels = [l["label"] for l in result["namespaces"]["cluster"]["labels"]]
        assert cluster_labels == ["cluster:alpha", "cluster:mid", "cluster:zebra"]

    def test_top_n_limits_per_namespace(self, db: FiligreeDB) -> None:
        for i in range(15):
            db.create_issue(f"Issue {i}", labels=[f"cluster:type-{i:02d}"])
        result = db.list_labels(top=5)
        assert len(result["namespaces"]["cluster"]["labels"]) == 5

    def test_namespace_filter(self, db: FiligreeDB) -> None:
        db.create_issue("A", labels=["cluster:x", "effort:m"])
        result = db.list_labels(namespace="cluster")
        assert "cluster" in result["namespaces"]
        assert "effort" not in result["namespaces"]

    def test_bare_labels_grouped_under_empty_namespace(self, db: FiligreeDB) -> None:
        db.create_issue("A", labels=["tech-debt", "security"])
        result = db.list_labels()
        bare = result["namespaces"].get("_bare", result["namespaces"].get("", None))
        assert bare is not None
        labels = {l["label"] for l in bare["labels"]}
        assert "tech-debt" in labels
```

**Step 2: Implement `list_labels` in `db_meta.py`**

Add method to `MetaMixin` class:

```python
def list_labels(
    self,
    *,
    namespace: str | None = None,
    top: int = 10,
) -> dict[str, Any]:
    """Return all distinct labels grouped by namespace with counts.

    Includes virtual namespaces with computed counts.
    Sorted alphabetically within each namespace.
    """
    from filigree.db_workflow import RESERVED_NAMESPACES_AUTO, RESERVED_NAMESPACES_VIRTUAL

    # Query stored labels with counts
    rows = self.conn.execute(
        "SELECT label, COUNT(*) as cnt FROM labels GROUP BY label ORDER BY label"
    ).fetchall()

    # Group by namespace
    namespaces: dict[str, dict[str, Any]] = {}
    for row in rows:
        lbl = row["label"]
        cnt = row["cnt"]
        if ":" in lbl:
            ns = lbl.split(":", 1)[0]
        else:
            ns = "_bare"

        if namespace is not None and ns != namespace:
            continue

        if ns not in namespaces:
            ns_lower = ns.casefold() if ns != "_bare" else "_bare"
            if ns_lower in RESERVED_NAMESPACES_AUTO:
                label_type, writable = "auto", False
            elif ns_lower in RESERVED_NAMESPACES_VIRTUAL:
                label_type, writable = "virtual", False
            else:
                label_type, writable = "manual", True
            namespaces[ns] = {"type": label_type, "writable": writable, "labels": []}

        namespaces[ns]["labels"].append({"label": lbl, "count": cnt})

    # Apply top-N limit per namespace
    if top > 0:
        for ns_data in namespaces.values():
            ns_data["labels"] = ns_data["labels"][:top]

    # Add virtual namespaces with computed counts
    if namespace is None or namespace == "age":
        age_labels = self._compute_virtual_age_counts()
        namespaces.setdefault("age", {"type": "virtual", "writable": False, "labels": age_labels})

    if namespace is None or namespace == "has":
        has_labels = self._compute_virtual_has_counts()
        namespaces.setdefault("has", {"type": "virtual", "writable": False, "labels": has_labels})

    total = sum(len(ns["labels"]) for ns in namespaces.values())
    return {"namespaces": namespaces, "total_in_result": total}
```

Also add the virtual count helpers:

```python
def _compute_virtual_age_counts(self) -> list[dict[str, Any]]:
    from filigree.db_base import AGE_BUCKETS
    results = []
    # Use datetime() for index-scannable queries (matches _resolve_virtual_label)
    for name, (low, high) in sorted(AGE_BUCKETS.items()):
        cnt = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM issues "
            "WHERE created_at <= datetime('now', ?) "
            "AND created_at > datetime('now', ?)",
            (f"-{low} days", f"-{high} days"),
        ).fetchone()["cnt"]
        results.append({"label": f"age:{name}", "count": cnt})
    return results

def _compute_virtual_has_counts(self) -> list[dict[str, Any]]:
    _, done_states_raw, _, _ = self._resolve_open_done_states()
    # Fallback for empty-workflow edge case (no templates loaded)
    done_states = done_states_raw or ["closed"]
    done_ph = ",".join("?" * len(done_states))
    counts = []
    # has:blockers — counts only open issues with unresolved blockers
    cnt = self.conn.execute(
        f"SELECT COUNT(DISTINCT i.id) as cnt FROM issues i "
        f"JOIN dependencies d ON d.issue_id = i.id "
        f"JOIN issues b ON d.depends_on_id = b.id "
        f"WHERE b.status NOT IN ({done_ph})",
        done_states,
    ).fetchone()["cnt"]
    counts.append({"label": "has:blockers", "count": cnt})
    # has:children
    cnt = self.conn.execute(
        "SELECT COUNT(DISTINCT parent_id) as cnt FROM issues WHERE parent_id IS NOT NULL"
    ).fetchone()["cnt"]
    counts.append({"label": "has:children", "count": cnt})
    # has:findings
    cnt = self.conn.execute(
        "SELECT COUNT(DISTINCT issue_id) as cnt FROM scan_findings "
        "WHERE issue_id IS NOT NULL AND status NOT IN ('fixed', 'false_positive')"
    ).fetchone()["cnt"]
    counts.append({"label": "has:findings", "count": cnt})
    # has:files
    cnt = self.conn.execute(
        "SELECT COUNT(DISTINCT issue_id) as cnt FROM file_associations"
    ).fetchone()["cnt"]
    counts.append({"label": "has:files", "count": cnt})
    # has:comments
    cnt = self.conn.execute(
        "SELECT COUNT(DISTINCT issue_id) as cnt FROM comments"
    ).fetchone()["cnt"]
    counts.append({"label": "has:comments", "count": cnt})
    return counts
```

**Step 3: Run tests**

Run: `uv run pytest tests/core/test_label_discovery.py -v`

**Step 4: Commit**

```bash
git add src/filigree/db_meta.py src/filigree/db_base.py tests/core/test_label_discovery.py
git commit -m "feat(labels): add list_labels with namespace grouping, virtual counts, alphabetical sort"
```

---

### Task 8: Add `get_label_taxonomy` DB method

**Files:**
- Modify: `src/filigree/db_meta.py`
- Test: `tests/core/test_label_discovery.py`

**Step 1: Write failing test**

Add to `tests/core/test_label_discovery.py`:

```python
class TestGetLabelTaxonomy:
    def test_returns_all_sections(self, db: FiligreeDB) -> None:
        result = db.get_label_taxonomy()
        assert "auto" in result
        assert "virtual" in result
        assert "manual_suggested" in result
        assert "bare_labels" in result

    def test_auto_namespaces_not_writable(self, db: FiligreeDB) -> None:
        result = db.get_label_taxonomy()
        for ns_data in result["auto"].values():
            assert ns_data["writable"] is False

    def test_manual_suggested_writable(self, db: FiligreeDB) -> None:
        result = db.get_label_taxonomy()
        for ns_data in result["manual_suggested"].values():
            assert ns_data["writable"] is True

    def test_review_namespace_lists_values(self, db: FiligreeDB) -> None:
        result = db.get_label_taxonomy()
        assert "review" in result["manual_suggested"]
        assert "needed" in result["manual_suggested"]["review"]["values"]
```

**Step 2: Implement**

Add to `MetaMixin`:

```python
def get_label_taxonomy(self) -> dict[str, Any]:
    """Return the full label vocabulary with descriptions and writability."""
    # Built-in defaults; override with config.json label_taxonomy if present
    return {
        "auto": {
            "area": {"description": "Component area from file paths", "writable": False, "example": "area:mcp"},
            "severity": {"description": "Highest active finding severity", "writable": False, "values": ["critical", "high", "medium", "low", "info"]},
            "scanner": {"description": "Scan source that produced findings", "writable": False, "example": "scanner:ruff"},
            "pack": {"description": "Workflow pack the issue type belongs to", "writable": False, "values": ["core", "planning", "release", "requirements"]},
        },
        "virtual": {
            "age": {"description": "Issue age bucket", "writable": False, "values": ["fresh", "recent", "aging", "stale", "ancient"]},
            "has": {"description": "Existence predicates", "writable": False, "values": ["blockers", "children", "findings", "files", "comments"]},
        },
        "manual_suggested": {
            "cluster": {"description": "Root cause pattern for bugs", "writable": True, "examples": ["broad-except", "race-condition", "null-check", "type-coercion", "resource-leak"]},
            "effort": {"description": "T-shirt sizing", "writable": True, "values": ["xs", "s", "m", "l", "xl"]},
            "source": {"description": "How the issue was discovered", "writable": True, "examples": ["scanner", "review", "agent"]},
            "agent": {"description": "Agent instance attribution (manual)", "writable": True, "examples": ["claude-1", "claude-2"]},
            "release": {"description": "Release version targeting", "writable": True, "examples": ["v1.3.0", "v1.4.0"]},
            "changelog": {"description": "Changelog category", "writable": True, "values": ["added", "changed", "fixed", "removed", "deprecated"]},
            "wait": {"description": "External blocker type", "writable": True, "examples": ["design", "upstream", "vendor", "decision"]},
            "breaking": {"description": "Breaking change marker", "writable": True, "examples": ["api", "schema", "config"]},
            "review": {"description": "Review workflow state (mutually exclusive)", "writable": True, "mutually_exclusive": True, "values": ["needed", "done", "rework"]},
        },
        "bare_labels": {
            "description": "Common labels without namespace prefix",
            "writable": True,
            "suggested": ["tech-debt", "regression", "security", "perf", "cherry-pick", "hotfix", "flaky-test", "wontfix"],
        },
    }
```

**Step 3: Run tests, commit**

```bash
uv run pytest tests/core/test_label_discovery.py -v
git add src/filigree/db_meta.py tests/core/test_label_discovery.py
git commit -m "feat(labels): add get_label_taxonomy with full vocabulary"
```

---

### Task 9: Register `list_labels` and `get_label_taxonomy` as MCP tools

**Files:**
- Modify: `src/filigree/mcp_tools/meta.py` (add tool defs + handlers)
- Modify: `src/filigree/types/inputs.py` (add `ListLabelsArgs` to `TOOL_ARGS_MAP` — **NOT** `GetLabelTaxonomyArgs`)
- Test: `tests/mcp/test_tools.py`

**Step 1: Add tool definitions to `register()` in `mcp_tools/meta.py`**

Add to the `tools` list:

```python
Tool(
    name="list_labels",
    description="List all distinct labels grouped by namespace with counts. Sorted alphabetically. Use get_label_taxonomy to see reserved namespaces and suggested vocabulary.",
    inputSchema={
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "description": "Filter to a specific namespace (e.g. 'cluster')"},
            "top": {"type": "integer", "default": 10, "minimum": 0, "description": "Max labels per namespace (default 10, 0 for unlimited)"},
        },
    },
),
Tool(
    name="get_label_taxonomy",
    description="Get the full label vocabulary: reserved namespaces, auto-tags, virtual labels, and suggested manual labels. Use this before adding labels to see what's available and what's reserved.",
    inputSchema={"type": "object", "properties": {}},
),
```

Add handlers (following the existing pattern — import `_get_db` from `filigree.mcp_server`):

```python
async def _handle_list_labels(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db
    tracker = _get_db()
    result = tracker.list_labels(
        namespace=arguments.get("namespace"),
        top=arguments.get("top", 10),
    )
    return _text(result)

async def _handle_get_label_taxonomy(arguments: dict[str, Any]) -> list[TextContent]:
    from filigree.mcp_server import _get_db
    tracker = _get_db()
    result = tracker.get_label_taxonomy()
    return _text(result)
```

Wire into `handlers` dict:

```python
"list_labels": _handle_list_labels,
"get_label_taxonomy": _handle_get_label_taxonomy,
```

**Step 2: Write MCP integration tests**

Add to `tests/mcp/test_tools.py` (using `mcp_db` fixture + `call_tool()` — the established MCP test pattern):

```python
from tests.mcp._helpers import _parse

class TestListLabels:
    async def test_list_labels_returns_namespaces(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("A", labels=["cluster:broad-except"])
        result = await call_tool("list_labels", {})
        data = _parse(result)
        assert "namespaces" in data
        assert "cluster" in data["namespaces"]

    async def test_list_labels_with_namespace_filter(self, mcp_db: FiligreeDB) -> None:
        mcp_db.create_issue("A", labels=["cluster:x", "effort:m"])
        result = await call_tool("list_labels", {"namespace": "cluster"})
        data = _parse(result)
        assert "cluster" in data["namespaces"]
        assert "effort" not in data["namespaces"]

    async def test_list_labels_includes_virtual(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("list_labels", {})
        data = _parse(result)
        assert "age" in data["namespaces"]
        assert "has" in data["namespaces"]

class TestGetLabelTaxonomy:
    async def test_taxonomy_returns_all_sections(self, mcp_db: FiligreeDB) -> None:
        result = await call_tool("get_label_taxonomy", {})
        data = _parse(result)
        assert "auto" in data
        assert "virtual" in data
        assert "manual_suggested" in data
        assert "bare_labels" in data
```

**Step 3: Add `TOOL_ARGS_MAP` entry in `types/inputs.py`**

The existing sync test (`test_input_type_contracts.py`) asserts every tool with args has a `TOOL_ARGS_MAP` entry, and that **tools with empty `properties: {}` must NOT be in the map** (see `test_no_arg_tool_excluded` at line 122). `list_labels` has properties, so it needs a TypedDict. `get_label_taxonomy` has empty properties, so do NOT add it.

```python
# In src/filigree/types/inputs.py

class ListLabelsArgs(TypedDict):
    namespace: NotRequired[str]
    top: NotRequired[int]


# Add ONLY list_labels to TOOL_ARGS_MAP — get_label_taxonomy has no
# inputSchema properties, so the sync test requires it to be ABSENT:
TOOL_ARGS_MAP = {
    ...
    "list_labels": ListLabelsArgs,
    # Do NOT add "get_label_taxonomy" — empty properties → excluded by sync test
}
```

**Step 4: Run tests, commit**

```bash
uv run pytest tests/mcp/ -v
git add src/filigree/mcp_tools/meta.py src/filigree/types/inputs.py tests/mcp/test_tools.py
git commit -m "feat(mcp): add list_labels and get_label_taxonomy tools"
```

---

### Task 10: Register CLI commands for `list_labels` and `get_label_taxonomy`

**Files:**
- Modify: `src/filigree/cli_commands/meta.py`
- Test: `tests/cli/test_workflow_commands.py`

**Step 1: Add CLI commands**

Add to `src/filigree/cli_commands/meta.py`:

```python
@click.command("labels")
@click.option("--namespace", "-n", default=None, help="Filter to a namespace")
@click.option("--top", default=10, type=int, help="Max labels per namespace (0 for unlimited)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_labels_cmd(namespace: str | None, top: int, as_json: bool) -> None:
    """List all labels grouped by namespace with counts."""
    with get_db() as db:
        result = db.list_labels(namespace=namespace, top=top)
        if as_json:
            click.echo(json_mod.dumps(result, indent=2))
            return
        for ns_name, ns_data in sorted(result["namespaces"].items()):
            writable = "rw" if ns_data["writable"] else "ro"
            click.echo(f"\n{ns_name}: ({ns_data['type']}, {writable})")
            for item in ns_data["labels"]:
                click.echo(f"  {item['label']}  ({item['count']})")


@click.command("taxonomy")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def taxonomy_cmd(as_json: bool) -> None:
    """Show the label taxonomy vocabulary."""
    with get_db() as db:
        result = db.get_label_taxonomy()
        if as_json:
            click.echo(json_mod.dumps(result, indent=2))
            return
        for section, data in result.items():
            click.echo(f"\n== {section} ==")
            if isinstance(data, dict) and "suggested" in data:
                click.echo(f"  {', '.join(data['suggested'])}")
            elif isinstance(data, dict):
                for ns, info in data.items():
                    vals = info.get("values") or info.get("examples") or [info.get("example", "")]
                    click.echo(f"  {ns}: {info['description']}  [{', '.join(str(v) for v in vals)}]")
```

Register them:

```python
cli.add_command(list_labels_cmd)
cli.add_command(taxonomy_cmd)
```

**Step 2: Write CLI tests**

Add to `tests/cli/test_workflow_commands.py` (using `cli_in_project` fixture — the established CLI test pattern):

```python
class TestLabelsCommand:
    def test_labels_command(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A", "-l", "cluster:broad-except"])
        result = runner.invoke(cli, ["labels"])
        assert result.exit_code == 0
        assert "cluster" in result.output
        assert "broad-except" in result.output

    def test_labels_json_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["labels", "--json"])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert "namespaces" in data

    def test_labels_namespace_filter(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        runner.invoke(cli, ["create", "Issue A", "-l", "cluster:x", "-l", "effort:m"])
        result = runner.invoke(cli, ["labels", "--namespace", "cluster"])
        assert result.exit_code == 0
        assert "cluster" in result.output
        assert "effort" not in result.output


class TestTaxonomyCommand:
    def test_taxonomy_command(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["taxonomy"])
        assert result.exit_code == 0
        assert "auto" in result.output
        assert "virtual" in result.output

    def test_taxonomy_json_output(self, cli_in_project: tuple[CliRunner, Path]) -> None:
        runner, _ = cli_in_project
        result = runner.invoke(cli, ["taxonomy", "--json"])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert "manual_suggested" in data
```

**Step 3: Run full CI, commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short
git add src/filigree/cli_commands/meta.py tests/cli/
git commit -m "feat(cli): add labels and taxonomy commands"
```

---

## PR 3 — Auto-Tags (Schema Migration)

Requires schema version bump. Highest risk PR.

---

### Task 11: Schema migration — `origin` column + covering index

**Files:**
- Modify: `src/filigree/db_schema.py` (schema + version bump)
- Modify: `src/filigree/migrations.py` (migration logic — all migrations live here, not in core.py)
- Test: `tests/core/test_schema.py` (existing migration test file)

**Step 1: Update schema**

In `src/filigree/db_schema.py`, update the labels table in `SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS labels (
    issue_id TEXT NOT NULL REFERENCES issues(id),
    label    TEXT NOT NULL,
    origin   TEXT NOT NULL DEFAULT 'manual',
    PRIMARY KEY (issue_id, label)
);

CREATE INDEX IF NOT EXISTS idx_labels_label ON labels(label, issue_id);
```

Bump `CURRENT_SCHEMA_VERSION = 8`.

**Step 2: Add migration function**

In `src/filigree/migrations.py` (where all schema upgrades live), add and register in the `MIGRATIONS` dict. **Convention:** all migration functions use the public `migrate_v<N>_to_v<N+1>` naming (no underscore prefix):

```python
def migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Add origin column to labels, covering index, and re-classify reserved-namespace labels.

    Rollback (SQLite < 3.35 lacks DROP COLUMN — use rebuild_table):
        rebuild_table(conn, "labels",
            "CREATE TABLE labels (issue_id TEXT NOT NULL REFERENCES issues(id), "
            "label TEXT NOT NULL, PRIMARY KEY (issue_id, label))",
        )
        DROP INDEX IF EXISTS idx_labels_label;
        PRAGMA user_version = 7;
    """
    conn.execute("ALTER TABLE labels ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_labels_label ON labels(label, issue_id)")

    # Re-classify existing labels in reserved auto-tag namespaces from 'manual' to 'auto'.
    # These labels were valid before PR1 enforcement but should be system-managed going forward.
    from filigree.db_workflow import RESERVED_NAMESPACES_AUTO
    for ns in RESERVED_NAMESPACES_AUTO:
        conn.execute(
            "UPDATE labels SET origin = 'auto' WHERE label LIKE ? AND origin = 'manual'",
            (ns + ":%",),
        )
```

Register in the `MIGRATIONS` dict (line 347, currently ends at key `6: migrate_v6_to_v7`):

```python
MIGRATIONS = {
    ...
    7: migrate_v7_to_v8,
}
```

**Step 3: Write migration test**

Add to `tests/core/test_schema.py` (where existing migration tests live):

```python
def test_migrate_v7_to_v8(tmp_path):
    """V7 DB gains origin column, label index, and reserved-namespace re-classification."""
    import sqlite3

    db_path = tmp_path / "test.db"
    # Build a raw v7 schema directly — do NOT use FiligreeDB() constructor,
    # because it creates the current (v8) schema which already has the origin
    # column, causing the ALTER TABLE ADD COLUMN to fail with "duplicate column".
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE issues ("
        "  id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '',"
        "  status TEXT NOT NULL DEFAULT 'open', type TEXT NOT NULL DEFAULT 'task',"
        "  priority INTEGER NOT NULL DEFAULT 2, parent_id TEXT,"
        "  assignee TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE labels ("
        "  issue_id TEXT NOT NULL REFERENCES issues(id),"
        "  label TEXT NOT NULL,"
        "  PRIMARY KEY (issue_id, label)"
        ")"
    )
    conn.execute("PRAGMA user_version = 7")

    # Insert test data: an issue with manual + reserved-namespace labels
    import datetime
    now = datetime.datetime.now(datetime.UTC).isoformat()
    conn.execute(
        "INSERT INTO issues (id, title, status, type, priority, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test-1", "Test", "open", "task", 2, now, now),
    )
    conn.execute("INSERT INTO labels (issue_id, label) VALUES (?, ?)", ("test-1", "tech-debt"))
    conn.execute("INSERT INTO labels (issue_id, label) VALUES (?, ?)", ("test-1", "review:needed"))
    conn.execute("INSERT INTO labels (issue_id, label) VALUES (?, ?)", ("test-1", "severity:high"))
    conn.commit()
    pre_count = conn.execute("SELECT COUNT(*) as cnt FROM labels").fetchone()["cnt"]

    # Run migration
    from filigree.migrations import migrate_v7_to_v8
    migrate_v7_to_v8(conn)
    conn.execute("PRAGMA user_version = 8")
    conn.commit()

    # Verify: column exists, data preserved
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(labels)").fetchall()]
    assert "origin" in cols

    post_count = conn.execute("SELECT COUNT(*) as cnt FROM labels").fetchone()["cnt"]
    assert post_count == pre_count  # no data lost

    # Manual labels stay 'manual', reserved-namespace labels re-classified to 'auto'
    manual_origins = conn.execute(
        "SELECT label, origin FROM labels WHERE origin = 'manual' ORDER BY label"
    ).fetchall()
    manual_labels = {r["label"] for r in manual_origins}
    assert "tech-debt" in manual_labels
    assert "review:needed" in manual_labels

    auto_origins = conn.execute(
        "SELECT label, origin FROM labels WHERE origin = 'auto'"
    ).fetchall()
    auto_labels = {r["label"] for r in auto_origins}
    assert "severity:high" in auto_labels  # re-classified by migration sweep

    # Verify index exists
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_labels_label'"
    ).fetchall()
    assert len(indexes) == 1

    conn.close()
```

**Step 4: Run tests, commit**

```bash
uv run pytest tests/ --tb=short
git add src/filigree/db_schema.py src/filigree/migrations.py tests/
git commit -m "feat(schema): v8 migration — origin column + idx_labels_label index"
```

---

### Task 12: `_sync_auto_tags` + `_compute_auto_tags` helpers + auto-tag hooks

**Files:**
- Modify: `src/filigree/db_meta.py` (add `_sync_auto_tags`, `_compute_auto_tags`, update `remove_label`)
- Modify: `src/filigree/db_files.py` (restructure `add_file_association` for pre-commit hook, hook into `process_scan_results`)
- Modify: `src/filigree/db_issues.py` (hook into `create_issue` for `pack:`)
- Test: `tests/core/test_auto_tags.py` (new file)

**Step 1: Write failing tests**

Create `tests/core/test_auto_tags.py`:

```python
"""Tests for auto-tag sync: pack:, area:, severity: labels."""

from __future__ import annotations

import pytest
from filigree.core import FiligreeDB


class TestAutoTagSync:
    def test_pack_label_on_create(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="bug")
        assert "pack:core" in issue.labels

    def test_area_label_on_file_association(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        file_rec = db.register_file("src/filigree/mcp_tools/scanner.py")
        db.add_file_association(file_rec.id, issue.id, "bug_in")  # register_file returns FileRecord dataclass
        refreshed = db.get_issue(issue.id)
        # area:mcp should be auto-tagged based on path mapping
        assert "area:mcp" in refreshed.labels

    def test_severity_label_on_scan_ingest(self, db: FiligreeDB) -> None:
        """Scan findings with linked issues get severity auto-tags."""
        issue = db.create_issue("Test")
        file_rec = db.register_file("src/core.py")
        db.add_file_association(file_rec.id, issue.id, "bug_in")
        db.process_scan_results(
            scan_source="test-scanner",
            findings=[{
                "path": "src/core.py",
                "rule_id": "E001",
                "severity": "high",
                "message": "test finding",
            }],
        )
        # After scan ingest, severity should be synced for linked issues
        refreshed = db.get_issue(issue.id)
        assert "severity:high" in refreshed.labels

    def test_auto_tag_not_manually_removable(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="bug")
        assert "pack:core" in issue.labels
        removed = db.remove_label(issue.id, "pack:core")
        assert removed is False  # rejected — auto-tag

    def test_auto_tag_rejection_is_logged(self, db: FiligreeDB, caplog) -> None:
        """Rejecting auto-tag removal should log a debug message."""
        import logging
        issue = db.create_issue("Test", type="bug")
        with caplog.at_level(logging.DEBUG):
            db.remove_label(issue.id, "pack:core")
        assert any("auto" in r.message.lower() for r in caplog.records)

    def test_sync_is_idempotent(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="bug")
        # Calling sync again doesn't duplicate
        db._sync_auto_tags(issue.id, "pack")
        refreshed = db.get_issue(issue.id)
        assert refreshed.labels.count("pack:core") == 1

    def test_compute_auto_tags_pack(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test", type="bug")
        tags = db._compute_auto_tags(issue.id, "pack")
        assert "pack:core" in tags

    def test_compute_auto_tags_scanner(self, db: FiligreeDB) -> None:
        """Scanner auto-tags derived from scan_findings.scan_source."""
        issue = db.create_issue("Test")
        file_rec = db.register_file("src/core.py")
        db.add_file_association(file_rec.id, issue.id, "bug_in")
        db.process_scan_results(
            scan_source="ruff",
            findings=[{
                "path": "src/core.py",
                "rule_id": "E001",
                "severity": "low",
                "message": "test",
            }],
        )
        refreshed = db.get_issue(issue.id)
        assert "scanner:ruff" in refreshed.labels

    def test_compute_auto_tags_unknown_namespace(self, db: FiligreeDB) -> None:
        issue = db.create_issue("Test")
        tags = db._compute_auto_tags(issue.id, "nonexistent")
        assert tags == []

    def test_compute_auto_tags_unknown_namespace_logs(self, db: FiligreeDB, caplog) -> None:
        """Unknown namespace should log a warning for debugging."""
        import logging
        issue = db.create_issue("Test")
        with caplog.at_level(logging.WARNING):
            db._compute_auto_tags(issue.id, "nonexistent")
        assert any("nonexistent" in r.message for r in caplog.records)
```

**Step 2: Implement `_compute_auto_tags`**

Add the module-level constant and the method to `MetaMixin` in `db_meta.py`:

```python
# Module-level constant (outside MetaMixin class)
_DEFAULT_AREA_MAP: dict[str, str] = {
    "src/filigree/mcp_tools/*": "mcp",
    "src/filigree/cli_commands/*": "cli",
    "src/filigree/dashboard*": "dashboard",
    "src/filigree/db_*": "core",
    "tests/*": "tests",
}


# Method on MetaMixin class
def _compute_auto_tags(self, issue_id: str, namespace: str) -> list[str]:
    """Compute the current auto-tag labels for a namespace on an issue.

    Returns a list of fully-qualified label strings (e.g. ["pack:core"]).
    """
    import fnmatch

    if namespace == "pack":
        # Derive from issue type → template → pack
        row = self.conn.execute(
            "SELECT type FROM issues WHERE id = ?", (issue_id,)
        ).fetchone()
        if row and row["type"]:
            tpl = self.templates.get_type(row["type"])
            if tpl and tpl.pack:
                return [f"pack:{tpl.pack}"]
        return []

    if namespace == "area":
        # Derive from file associations → path-to-area mapping
        rows = self.conn.execute(
            "SELECT f.path FROM file_records f "
            "JOIN file_associations fa ON fa.file_id = f.id "
            "WHERE fa.issue_id = ?",
            (issue_id,),
        ).fetchall()
        areas: set[str] = set()
        area_map = _DEFAULT_AREA_MAP
        for row in rows:
            path = row["path"]
            for pattern, area in area_map.items():
                if fnmatch.fnmatch(path, pattern):
                    areas.add(f"area:{area}")
                    break
        return sorted(areas)

    if namespace == "severity":
        # Derive from highest active finding severity
        row = self.conn.execute(
            "SELECT sf.severity FROM scan_findings sf "
            "WHERE sf.issue_id = ? AND sf.status NOT IN ('fixed', 'false_positive') "
            "ORDER BY CASE sf.severity "
            "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 WHEN 'low' THEN 3 "
            "  WHEN 'info' THEN 4 ELSE 5 END "
            "LIMIT 1",
            (issue_id,),
        ).fetchone()
        if row and row["severity"]:
            return [f"severity:{row['severity']}"]
        return []

    if namespace == "scanner":
        # Derive from scan sources that produced findings
        rows = self.conn.execute(
            "SELECT DISTINCT sf.scan_source FROM scan_findings sf "
            "WHERE sf.issue_id = ? AND sf.status NOT IN ('fixed', 'false_positive')",
            (issue_id,),
        ).fetchall()
        return sorted(f"scanner:{r['scan_source']}" for r in rows if r["scan_source"])

    # Unknown namespace — log for debugging, return empty
    logger.warning("_compute_auto_tags called with unknown namespace: %s", namespace)
    return []
```

**Step 3: Implement `_sync_auto_tags`**

Add to `MetaMixin` in `db_meta.py`:

```python
def _sync_auto_tags(self, issue_id: str, namespace: str) -> None:
    """Recompute auto-tag labels for a namespace on an issue.

    Idempotent: deletes ALL labels in the namespace (regardless of origin),
    then inserts the current computed set as origin='auto'.

    Note: caller owns commit/rollback — this method does not commit.
    This matches the transaction contract used by _record_event.
    """
    prefix = namespace + ":"
    # Delete ALL labels for this namespace (any origin) to prevent stale
    # manual-origin labels from pre-v8 imports blocking correct auto-tags
    self.conn.execute(
        "DELETE FROM labels WHERE issue_id = ? AND label LIKE ?",
        (issue_id, prefix + "%"),
    )
    # Compute current tags
    tags = self._compute_auto_tags(issue_id, namespace)
    for tag in tags:
        self.conn.execute(
            "INSERT OR IGNORE INTO labels (issue_id, label, origin) VALUES (?, ?, 'auto')",
            (issue_id, tag),
        )
```

**Step 4: Hook into `create_issue`**

In `src/filigree/db_issues.py`, add `_sync_auto_tags("pack")` inside `create_issue` **before** `self.conn.commit()` (line ~210):

```python
# Inside create_issue, before commit:
self._sync_auto_tags(issue_id, "pack")
self.conn.commit()
```

**Step 5: Restructure `add_file_association` for pre-commit hook**

In `src/filigree/db_files.py`, `add_file_association` (starts at line 1108, commits at line 1129). Restructure to insert the auto-tag sync before commit:

```python
def add_file_association(self, file_id: str, issue_id: str, assoc_type: str) -> None:
    # ... existing validation and INSERT logic ...
    try:
        self.conn.execute(
            "INSERT OR IGNORE INTO file_associations (file_id, issue_id, assoc_type) VALUES (?, ?, ?)",
            (file_id, issue_id, assoc_type),
        )
        # Auto-tag sync: recompute area: tags based on new file association
        self._sync_auto_tags(issue_id, "area")
        self.conn.commit()
    except Exception:
        self.conn.rollback()
        raise
```

**Step 6: Hook into `process_scan_results`**

In `src/filigree/db_files.py`, after the findings loop in `process_scan_results`, sync severity/scanner/area tags for all affected issues. Note: `process_scan_results` already calls `create_issue` internally which commits mid-loop. The sync should run **after** the main findings loop completes, as a post-commit pass.

**Important:** `_create_issue_for_finding` (defined at line 631, raw INSERT at lines 701–703) inserts into `file_associations` using raw SQL, bypassing `self.add_file_association()`. This means the `area:` hook from Step 5 is never reached for scan-created issues. The post-commit pass below must also sync `area:` tags to close this gap:

```python
# At end of process_scan_results, after main commit:
# ── WHY POST-COMMIT ──────────────────────────────────────────────────
# Auto-tag sync runs as a separate post-commit pass (not inline with
# the findings loop) because _create_issue_for_finding() calls
# create_issue() which commits mid-loop — so we cannot wrap the full
# findings + sync in a single transaction. This is safe because
# _sync_auto_tags is idempotent (DELETE + INSERT), so partial failures
# are recoverable by re-running the sync.
# ─────────────────────────────────────────────────────────────────────
# Uses stats["issue_ids"] (already collected during the findings loop at
# db_files.py:706) rather than maintaining a parallel set.
for issue_id in stats["issue_ids"]:
    try:
        self._sync_auto_tags(issue_id, "area")  # covers raw INSERT bypass in _create_issue_for_finding
        self._sync_auto_tags(issue_id, "severity")
        self._sync_auto_tags(issue_id, "scanner")
    except Exception:
        self.conn.rollback()
        warning = f"Failed to sync auto-tags for issue {issue_id}"
        logger.warning(warning)
        stats["warnings"].append(warning)
        continue
self.conn.commit()  # single commit for all sync operations
```

Auto-tag sync warnings are appended to the existing `warnings: list[str]` field on `ScanIngestResult` (at `types/files.py:114`). No new TypedDict field is needed.

> **Design note:** `process_scan_results` has a pre-existing transaction fragmentation issue — it calls `create_issue` which commits mid-loop. Rather than restructuring that entire flow (high blast radius), auto-tag sync for area/severity/scanner runs as a separate post-commit pass. This is safe because auto-tag sync is idempotent. The `area:` sync in this pass covers the gap where `_create_issue_for_finding` uses raw SQL INSERTs into `file_associations` instead of calling `self.add_file_association()`. The post-commit syncs are batched into a single commit to reduce SQLite WAL checkpoints under load.

**Step 7: Update `remove_label` to check `origin`**

```python
def remove_label(self, issue_id: str, label: str) -> bool:
    # Check if this is an auto-tag
    row = self.conn.execute(
        "SELECT origin FROM labels WHERE issue_id = ? AND label = ?",
        (issue_id, label),
    ).fetchone()
    if row and row["origin"] == "auto":
        logger.debug("Rejecting removal of auto-tag %r on issue %s", label, issue_id)
        return False  # Cannot manually remove auto-tags
    try:
        cursor = self.conn.execute(
            "DELETE FROM labels WHERE issue_id = ? AND label = ? AND origin = 'manual'",
            (issue_id, label),
        )
        self.conn.commit()
    except Exception:
        self.conn.rollback()
        raise
    return cursor.rowcount > 0
```

**Step 8: Run tests, commit**

```bash
uv run pytest tests/core/test_auto_tags.py -v
git add src/filigree/db_meta.py src/filigree/db_files.py src/filigree/db_issues.py tests/core/test_auto_tags.py
git commit -m "feat(labels): auto-tag sync with _sync_auto_tags/_compute_auto_tags, origin-based protection"
```

---

### Task 13: Update `import_jsonl` / `export_jsonl` for `origin` column

**Files:**
- Modify: `src/filigree/db_meta.py` — export is via `_EXPORT_TABLES` at line 340 (`SELECT *` already includes `origin` after migration); import label INSERT at line 522 needs `origin` param
- Test: `tests/core/test_import_export.py` (new file)

**Step 1: Write failing test**

Create `tests/core/test_import_export.py` (new file):

```python
"""Tests for import/export with origin column preservation."""

from __future__ import annotations

import pytest
from filigree.core import FiligreeDB
from tests._db_factory import make_db


def test_export_import_preserves_origin(db: FiligreeDB, tmp_path) -> None:
    issue = db.create_issue("Test", type="bug")
    # After PR3, this will have pack:core with origin='auto'
    db.add_label(issue.id, "tech-debt")  # origin='manual'
    export_path = tmp_path / "export.jsonl"
    db.export_jsonl(export_path)
    # Reimport into fresh DB
    db2 = make_db(tmp_path / "db2")
    db2.import_jsonl(export_path, merge=True)
    labels = db2.conn.execute(
        "SELECT label, origin FROM labels WHERE issue_id = ?", (issue.id,)
    ).fetchall()
    label_map = {r["label"]: r["origin"] for r in labels}
    assert label_map.get("tech-debt") == "manual"
    assert label_map.get("pack:core") == "auto"


def test_import_pre_v8_jsonl_defaults_to_manual(db: FiligreeDB, tmp_path) -> None:
    """Pre-v8 exports have no origin field; default to manual."""
    export_path = tmp_path / "old.jsonl"
    export_path.write_text(
        '{"_type":"issue","id":"test-1","title":"Old","status":"open","type":"task","priority":2}\n'
        '{"_type":"label","issue_id":"test-1","label":"severity:high"}\n'
    )
    db.import_jsonl(export_path)
    row = db.conn.execute("SELECT origin FROM labels WHERE label = 'severity:high'").fetchone()
    assert row["origin"] == "manual"  # Not auto — we don't know the source


def test_import_rejects_invalid_origin(db: FiligreeDB, tmp_path) -> None:
    """Crafted JSONL with arbitrary origin value is clamped to 'manual'."""
    export_path = tmp_path / "crafted.jsonl"
    export_path.write_text(
        '{"_type":"issue","id":"test-1","title":"Crafted","status":"open","type":"task","priority":2}\n'
        '{"_type":"label","issue_id":"test-1","label":"tech-debt","origin":"evil"}\n'
    )
    db.import_jsonl(export_path)
    row = db.conn.execute("SELECT origin FROM labels WHERE label = 'tech-debt'").fetchone()
    assert row["origin"] == "manual"  # Invalid origin clamped to manual
```

**Step 2: Verify export — no code change needed**

Export uses `_EXPORT_TABLES` (line 340) which does `SELECT * FROM labels`. Once the v8 migration adds the `origin` column, `SELECT *` automatically includes it. **No export code change required.**

**Step 3: Update import to preserve `origin` (with validation)**

```python
# Labels import (db_meta.py ~line 520)
_VALID_ORIGINS = {"manual", "auto"}

for record in labels:
    raw_origin = record.get("origin", "manual")  # Default for pre-v8 exports
    origin = raw_origin if raw_origin in _VALID_ORIGINS else "manual"  # Clamp invalid values
    cursor = self.conn.execute(
        f"INSERT {conflict} INTO labels (issue_id, label, origin) VALUES (?, ?, ?)",
        (record["issue_id"], record["label"], origin),
    )
    count += cursor.rowcount
```

**Step 4: Run tests, commit**

```bash
uv run pytest tests/core/test_import_export.py -v
git add src/filigree/db_meta.py tests/
git commit -m "feat(labels): import/export preserves origin column, pre-v8 defaults to manual"
```

---

### Task 14: ~~FTS Option B~~ — DEFERRED TO PR4

> **Deferred:** This task was underspecified and carries disproportionate risk inside PR3.
> The current `search_issues` uses an FTS5 external content index on `(title, description)`.
> Adding label text to FTS requires either:
>
> - **(a) FTS index rebuild** — a second schema migration (new FTS columns), not scoped into PR3
> - **(b) LIKE fallback** — bypasses FTS5 ranking entirely, degrading search quality
>
> PR3 already carries the highest risk (schema migration + auto-tag hooks). Adding an
> underspecified FTS change compounds that risk. This task should be fully designed and
> implemented in a dedicated PR4 after PR3 is validated.
>
> When implemented, the recommended approach is Option (b) with a LIKE fallback that
> searches `GROUP_CONCAT(label, ' ')` via LEFT JOIN, applied only when the query
> doesn't match any FTS results. This avoids a schema migration while preserving
> FTS ranking for title/description matches.

---

### Task 15: Final CI check for PR3

**Step 1: Run full pipeline**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/filigree/
uv run pytest --tb=short
```

**Step 2: Commit, push**

```bash
git push origin feat/dashboard-ux-and-observations
```

---

## Summary

| PR | Tasks | Key Changes | Risk |
|----|-------|-------------|------|
| PR1 | 1-6 | Virtual labels, array labels, prefix, not-label, namespace reservation, review: mutex | Low |
| PR2 | 7-10 | list_labels, get_label_taxonomy (MCP + CLI) | Low |
| PR3 | 11-13, 15 | origin column, auto-tag sync (`_compute_auto_tags` + `_sync_auto_tags`), import/export | Medium |
| PR4 | 14 | FTS label text in search (deferred — requires FTS design decision) | Medium |

### Known Edge Cases & Caveats

- **LIKE wildcard escaping:** `label_prefix` and `not_label` prefix patterns escape `%` and `_` via `_escape_like()` with `ESCAPE '\'` to prevent unintended pattern broadening.
- **Empty done-states:** `has:blockers` falls back to `["closed"]` if no workflow templates are loaded, preventing SQLite `IN ()` syntax errors.
- **Pre-v8 JSONL import:** Reserved-namespace labels (e.g. `severity:high`) from pre-PR1 exports import as `origin='manual'`. The v7→v8 migration includes a sweep that re-classifies labels in reserved namespaces to `origin='auto'`. Post-migration JSONL imports of pre-v8 data will still default to `'manual'`, but `_sync_auto_tags` deletes ALL labels in the namespace regardless of origin before re-inserting, so running any auto-tag sync after import will correct stale labels.
- **Transaction safety in `process_scan_results`:** Auto-tag sync runs as a batched post-commit pass (not inline) because `create_issue` commits mid-loop. Sync is idempotent, so partial failures are recoverable. Failures are appended to the existing `warnings` field in `ScanIngestResult`.
- **`datetime()` vs `julianday()`:** Age bucket queries use `datetime('now', '-N days')` for index-scannable range queries on `created_at`, not `julianday()` subtraction which forces full table scans.
- **`list_labels` query count:** `list_labels` fires 10 separate COUNT queries (5 age buckets + 5 has predicates) per call. Acceptable at current scale (hundreds to low thousands of issues). If this becomes a bottleneck at 10k+ issues, consider caching or combining queries.
