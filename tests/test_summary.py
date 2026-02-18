"""Tests for the summary generator (context.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from filigree.core import FiligreeDB
from filigree.summary import generate_summary, write_summary


class TestGenerateSummary:
    def test_empty_db(self, db: FiligreeDB) -> None:
        summary = generate_summary(db)
        assert "Project Pulse" in summary
        assert "Open: 0" in summary
        assert "(none)" in summary

    def test_with_issues(self, populated_db: FiligreeDB) -> None:
        summary = generate_summary(populated_db)
        assert "Project Pulse" in summary
        # Should show vitals
        assert "Open:" in summary
        assert "Ready:" in summary
        assert "Blocked:" in summary

    def test_in_progress_section(self, db: FiligreeDB) -> None:
        issue = db.create_issue("WIP task")
        db.update_issue(issue.id, status="in_progress")
        summary = generate_summary(db)
        assert "In Progress" in summary
        assert "WIP task" in summary

    def test_blocked_section(self, db: FiligreeDB) -> None:
        a = db.create_issue("Blocked task")
        b = db.create_issue("Blocker")
        db.add_dependency(a.id, b.id)
        summary = generate_summary(db)
        assert "Blocked" in summary
        assert "Blocked task" in summary

    def test_plan_section(self, db: FiligreeDB) -> None:
        ms = db.create_issue("Milestone 1", type="milestone")
        p = db.create_issue("Phase 1", type="phase", parent_id=ms.id)
        s1 = db.create_issue("Step 1", type="step", parent_id=p.id)
        db.create_issue("Step 2", type="step", parent_id=p.id)
        db.close_issue(s1.id)
        summary = generate_summary(db)
        assert "Active Plans" in summary
        assert "Milestone 1" in summary
        assert "Phase 1" in summary

    def test_epic_progress_section(self, db: FiligreeDB) -> None:
        epic = db.create_issue("Epic A", type="epic")
        c1 = db.create_issue("Child 1", parent_id=epic.id)
        db.create_issue("Child 2", parent_id=epic.id)
        db.close_issue(c1.id)
        summary = generate_summary(db)
        assert "Epic Progress" in summary
        assert "Epic A" in summary

    def test_recent_activity_section(self, db: FiligreeDB) -> None:
        db.create_issue("Event source")
        summary = generate_summary(db)
        assert "Recent Activity" in summary
        assert "CREATED" in summary

    def test_ready_section_truncation(self, db: FiligreeDB) -> None:
        """More than 12 ready issues shows truncation message."""
        for i in range(18):
            db.create_issue(f"Ready {i}")
        summary = generate_summary(db)
        assert "...and 6 more" in summary

    def test_stale_section(self, db: FiligreeDB) -> None:
        """In-progress issues >3 days old appear in stale section."""
        issue = db.create_issue("Stale task")
        db.update_issue(issue.id, status="in_progress")
        # Backdate the updated_at to 5 days ago
        old_ts = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        db.conn.execute("UPDATE issues SET updated_at = ? WHERE id = ?", (old_ts, issue.id))
        db.conn.commit()
        summary = generate_summary(db)
        assert "Stale" in summary
        assert "Stale task" in summary
        assert "5d stale" in summary

    def test_no_stale_when_recent(self, db: FiligreeDB) -> None:
        """Recently updated in-progress issues don't appear in stale section."""
        issue = db.create_issue("Fresh task")
        db.update_issue(issue.id, status="in_progress")
        summary = generate_summary(db)
        assert "Stale" not in summary


class TestCategoryAwareSummary:
    """Workflow-aware summary tests (Phase 4 — WFT-FR-060, WFT-FR-061, WFT-NFR-010, WFT-FR-071)."""

    def test_vitals_uses_categories(self, db: FiligreeDB) -> None:
        """Vitals line shows Open/In Progress/Done counts from categories."""
        db.create_issue("A")  # open
        b = db.create_issue("B")
        db.update_issue(b.id, status="in_progress")  # wip
        c = db.create_issue("C")
        db.close_issue(c.id)  # done
        summary = generate_summary(db)
        assert "Open: 1" in summary
        assert "In Progress: 1" in summary
        assert "Done: 1" in summary

    def test_ready_shows_state_in_parens(self, db: FiligreeDB) -> None:
        """Bug in 'confirmed' (open category) shows (confirmed) in ready section."""
        bug = db.create_issue("Login crash", type="bug")
        db.update_issue(bug.id, status="confirmed")
        summary = generate_summary(db)
        assert "(confirmed)" in summary

    def test_ready_omits_open_parens(self, db: FiligreeDB) -> None:
        """Task in 'open' status has no redundant (open) annotation."""
        db.create_issue("Basic task", type="task")
        summary = generate_summary(db)
        # Should show the title but NOT "(open)"
        assert "Basic task" in summary
        assert "(open)" not in summary

    def test_in_progress_uses_wip_category(self, db: FiligreeDB) -> None:
        """Bug in 'fixing' (wip category) appears in In Progress section."""
        bug = db.create_issue("Fixable bug", type="bug")
        db.update_issue(bug.id, status="confirmed")
        db.update_issue(bug.id, status="fixing", fields={"root_cause": "found it"})
        summary = generate_summary(db)
        assert "Fixable bug" in summary
        # Should be in the In Progress section
        lines = summary.split("\n")
        in_progress_idx = next(i for i, line in enumerate(lines) if line.startswith("## In Progress"))
        next_section_idx = next(
            (i for i, line in enumerate(lines) if line.startswith("## ") and i > in_progress_idx),
            len(lines),
        )
        in_progress_section = "\n".join(lines[in_progress_idx:next_section_idx])
        assert "Fixable bug" in in_progress_section

    def test_in_progress_shows_state_in_parens(self, db: FiligreeDB) -> None:
        """Bug in 'fixing' shows (fixing) in In Progress section."""
        bug = db.create_issue("Verifiable bug", type="bug")
        db.update_issue(bug.id, status="confirmed")
        db.update_issue(bug.id, status="fixing", fields={"root_cause": "root"})
        summary = generate_summary(db)
        assert "(fixing)" in summary

    def test_ready_truncation_at_12(self, db: FiligreeDB) -> None:
        """14 ready issues → shows 12 + '...and 2 more'."""
        for i in range(14):
            db.create_issue(f"Ready {i}")
        summary = generate_summary(db)
        assert "...and 2 more" in summary

    def test_epic_limit_10(self, db: FiligreeDB) -> None:
        """12 open epics → only 10 shown in Epic Progress."""
        for i in range(12):
            epic = db.create_issue(f"Epic {i}", type="epic")
            db.create_issue(f"Child of {i}", parent_id=epic.id)
        summary = generate_summary(db)
        # Count epic entries in Epic Progress section
        lines = summary.split("\n")
        epic_section_lines = []
        in_epic = False
        for line in lines:
            if line.startswith("## Epic Progress"):
                in_epic = True
                continue
            if in_epic and line.startswith("## "):
                break
            if in_epic and line.startswith("- "):
                epic_section_lines.append(line)
        assert len(epic_section_lines) == 10

    def test_epic_done_uses_category(self, db: FiligreeDB) -> None:
        """Child with done-category status (e.g., wont_fix) counts as done in epic."""
        epic = db.create_issue("Test Epic", type="epic")
        child = db.create_issue("Bug child", type="bug", parent_id=epic.id)
        db.close_issue(child.id, status="wont_fix")
        summary = generate_summary(db)
        # Epic should show 1/1 completion
        assert "1/1" in summary

    def test_needs_attention_section(self, db: FiligreeDB) -> None:
        """Bug in 'fixing' missing root_cause → appears in Needs Attention."""
        bug = db.create_issue("Missing fields bug", type="bug")
        db.update_issue(bug.id, status="confirmed")
        # Move to fixing WITHOUT setting root_cause (which is required_at fixing)
        db.update_issue(bug.id, status="fixing")
        summary = generate_summary(db)
        assert "## Needs Attention" in summary
        assert "root_cause" in summary

    def test_needs_attention_absent_when_clean(self, db: FiligreeDB) -> None:
        """All required fields populated → no Needs Attention section."""
        bug = db.create_issue("Clean bug", type="bug")
        db.update_issue(bug.id, status="confirmed")
        db.update_issue(bug.id, status="fixing", fields={"root_cause": "identified"})
        summary = generate_summary(db)
        assert "## Needs Attention" not in summary


class TestWriteSummary:
    def test_atomic_write(self, db: FiligreeDB, tmp_path: Path) -> None:
        output = tmp_path / "context.md"
        write_summary(db, output)
        assert output.exists()
        content = output.read_text()
        assert "Project Pulse" in content

    def test_overwrites_existing(self, db: FiligreeDB, tmp_path: Path) -> None:
        output = tmp_path / "context.md"
        output.write_text("old content")
        write_summary(db, output)
        content = output.read_text()
        assert "old content" not in content
        assert "Project Pulse" in content

    def test_no_temp_file_left(self, db: FiligreeDB, tmp_path: Path) -> None:
        output = tmp_path / "context.md"
        write_summary(db, output)
        tmp_file = output.with_suffix(".tmp")
        assert not tmp_file.exists()

    def test_temp_file_is_unique_per_call(self, db: FiligreeDB, tmp_path: Path) -> None:
        """Temp filenames must be unique to avoid races between concurrent writers."""
        import os
        from unittest.mock import patch

        output = tmp_path / "context.md"
        temp_paths: list[str] = []

        original_replace = os.replace

        def capture_replace(src: str, dst: str) -> None:
            temp_paths.append(src)
            return original_replace(src, dst)

        with patch("filigree.summary.os.replace", side_effect=capture_replace):
            write_summary(db, output)
            write_summary(db, output)

        # Two calls must use different temp paths
        assert len(temp_paths) == 2
        assert temp_paths[0] != temp_paths[1]
