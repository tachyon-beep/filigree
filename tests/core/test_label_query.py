"""Tests for label query improvements: array labels, prefix, not-label, virtual labels."""

from __future__ import annotations

import pytest

from filigree.core import FiligreeDB


class TestArrayLabels:
    """Multiple --label filters use AND logic."""

    def test_single_label_filter(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["defect", "urgent"])
        b = db.create_issue("B", labels=["defect"])
        results = db.list_issues(label=["defect"])
        ids = [i.id for i in results]
        assert a.id in ids
        assert b.id in ids

    def test_multiple_labels_and_logic(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["defect", "urgent"])
        db.create_issue("B", labels=["defect"])
        results = db.list_issues(label=["defect", "urgent"])
        ids = [i.id for i in results]
        assert ids == [a.id]

    def test_backward_compat_string_label(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["defect"])
        results = db.list_issues(label="defect")
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

    def test_prefix_escapes_like_wildcards(self, db: FiligreeDB) -> None:
        """LIKE wildcards in label values must not cause over-matching."""
        db.create_issue("A", labels=["ns%evil:val"])
        db.create_issue("B", labels=["ns_other:val"])
        db.create_issue("C", labels=["ns:normal"])
        # "ns:" should only match "ns:normal", not "ns%evil:" or "ns_other:"
        results = db.list_issues(label_prefix="ns:")
        ids = [i.id for i in results]
        assert len(ids) == 1

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
        db.create_issue("A", labels=["wont-fix"])
        b = db.create_issue("B", labels=["defect"])
        results = db.list_issues(not_label="wont-fix")
        ids = [i.id for i in results]
        assert b.id in ids
        assert len([i for i in results if "wont-fix" in i.labels]) == 0

    def test_not_label_prefix(self, db: FiligreeDB) -> None:
        db.create_issue("A", labels=["wait:upstream"])
        b = db.create_issue("B", labels=["defect"])
        results = db.list_issues(not_label="wait:")
        ids = [i.id for i in results]
        assert b.id in ids


class TestVirtualLabels:
    """Virtual labels resolve to SQL at query time."""

    def test_age_fresh(self, db: FiligreeDB) -> None:
        """Newly created issues are age:fresh."""
        a = db.create_issue("A")
        results = db.list_issues(label=["age:fresh"], type="task")
        ids = [i.id for i in results]
        assert a.id in ids

    def test_age_stale_no_recent_issues(self, db: FiligreeDB) -> None:
        """Fresh issues should not match age:stale."""
        db.create_issue("A")
        results = db.list_issues(label=["age:stale"], type="task")
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
        db.add_file_association(file_rec.id, a.id, "bug_in")
        results = db.list_issues(label=["has:files"])
        assert len(results) == 1
        assert results[0].id == a.id

    def test_has_findings_matches_linked_issue(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        db.create_issue("B")
        file_rec = db.register_file("src/core.py")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "src/core.py", "rule_id": "R1", "severity": "medium", "message": "bug"}],
        )
        # Link a finding to the issue
        findings = db.get_findings_paginated(file_rec.id)
        fid = findings["results"][0]["id"]
        db.update_finding(fid, issue_id=a.id)
        results = db.list_issues(label=["has:findings"])
        assert len(results) == 1
        assert results[0].id == a.id

    def test_has_findings_excludes_fixed(self, db: FiligreeDB) -> None:
        a = db.create_issue("A")
        file_rec = db.register_file("src/core.py")
        db.process_scan_results(
            scan_source="test",
            findings=[{"path": "src/core.py", "rule_id": "R1", "severity": "medium", "message": "bug"}],
        )
        findings = db.get_findings_paginated(file_rec.id)
        fid = findings["results"][0]["id"]
        db.update_finding(fid, issue_id=a.id, status="fixed")
        results = db.list_issues(label=["has:findings"])
        assert len(results) == 0

    def test_unknown_virtual_raises_valueerror(self, db: FiligreeDB) -> None:
        db.create_issue("A")
        with pytest.raises(ValueError, match="Unknown age bucket"):
            db.list_issues(label=["age:garbage"])

    def test_not_label_virtual(self, db: FiligreeDB) -> None:
        """Negation works on virtual labels."""
        db.create_issue("A")
        results = db.list_issues(not_label="age:fresh", type="task")
        assert len(results) == 0  # all task issues are fresh


class TestHasBlockers:
    """has:blockers involves a complex three-table JOIN."""

    def test_has_blockers_matches_blocked_issue(self, db: FiligreeDB) -> None:
        blocker = db.create_issue("Blocker task")
        blocked = db.create_issue("Blocked task")
        db.add_dependency(blocked.id, blocker.id)
        results = db.list_issues(label=["has:blockers"])
        ids = [i.id for i in results]
        assert blocked.id in ids
        assert blocker.id not in ids

    def test_has_blockers_excludes_resolved_blockers(self, db: FiligreeDB) -> None:
        """Closed blockers don't count — issue should not appear."""
        blocker = db.create_issue("Blocker task")
        blocked = db.create_issue("Blocked task")
        db.add_dependency(blocked.id, blocker.id)
        db.close_issue(blocker.id)
        results = db.list_issues(label=["has:blockers"])
        ids = [i.id for i in results]
        assert blocked.id not in ids

    def test_has_blockers_no_deps(self, db: FiligreeDB) -> None:
        db.create_issue("No deps")
        results = db.list_issues(label=["has:blockers"])
        assert len(results) == 0


class TestNotLabelVirtualPrefix:
    """Negating virtual namespace prefixes is rejected."""

    def test_not_label_age_prefix_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Cannot negate virtual namespace prefix"):
            db.list_issues(not_label="age:")

    def test_not_label_has_prefix_raises(self, db: FiligreeDB) -> None:
        with pytest.raises(ValueError, match="Cannot negate virtual namespace prefix"):
            db.list_issues(not_label="has:")

    def test_not_label_specific_virtual_is_allowed(self, db: FiligreeDB) -> None:
        """Specific virtual values like age:stale are fine to negate."""
        db.create_issue("A")
        # Should not raise — specific values are ok, only bare prefixes are blocked
        results = db.list_issues(not_label="age:fresh", type="task")
        assert isinstance(results, list)


class TestVirtualAndStoredCombined:
    def test_virtual_and_stored_label_and(self, db: FiligreeDB) -> None:
        a = db.create_issue("A", labels=["defect"])
        db.create_issue("B")
        results = db.list_issues(label=["age:fresh", "defect"])
        assert len(results) == 1
        assert results[0].id == a.id
