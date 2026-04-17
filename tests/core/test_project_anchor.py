"""Tests for .filigree.conf project anchor + prefix-mismatch guard.

Bug filigree-7840eae0bd: an agent in a directory with no .filigree/ silently
walks up into a parent's .filigree/ and writes tickets into the wrong DB.

The 2.0 fix introduces:
  * .filigree.conf — JSON marker file at the project root, the authoritative
    discovery anchor. Walk-up looks for this file (not the .filigree/ dir).
    Nested .filigree.conf overrides parent.
  * Read-only discovery — legacy installs (have .filigree/, no .filigree.conf)
    are still discoverable via :func:`find_filigree_anchor`, but discovery
    itself never writes. The conf is created only by explicit init/install
    paths so inspection commands work on read-only mounts.
  * WrongProjectError — every ID-taking method rejects IDs whose prefix
    doesn't match the DB's prefix.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from filigree.core import (
    CONF_FILENAME,
    DB_FILENAME,
    FILIGREE_DIR_NAME,
    FiligreeDB,
    ProjectNotInitialisedError,
    WrongProjectError,
    find_filigree_anchor,
    find_filigree_conf,
    find_filigree_root,
    read_conf,
    write_conf,
)

# ---------------------------------------------------------------------------
# Discovery: find_filigree_conf
# ---------------------------------------------------------------------------


class TestFindFiligreeConf:
    def test_finds_in_current_dir(self, tmp_path: Path) -> None:
        conf = tmp_path / CONF_FILENAME
        write_conf(conf, {"version": 1, "project_name": "p", "prefix": "p", "db": ".filigree/filigree.db"})
        assert find_filigree_conf(tmp_path) == conf

    def test_walks_up_to_parent(self, tmp_path: Path) -> None:
        conf = tmp_path / CONF_FILENAME
        write_conf(conf, {"version": 1, "project_name": "p", "prefix": "p", "db": ".filigree/filigree.db"})
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        assert find_filigree_conf(sub) == conf

    def test_child_conf_overrides_parent(self, tmp_path: Path) -> None:
        """Nested .filigree.conf — the child claim wins for that subtree."""
        parent_conf = tmp_path / CONF_FILENAME
        write_conf(parent_conf, {"version": 1, "project_name": "outer", "prefix": "outer", "db": ".filigree/filigree.db"})

        child_dir = tmp_path / "sub-project"
        child_dir.mkdir()
        child_conf = child_dir / CONF_FILENAME
        write_conf(child_conf, {"version": 1, "project_name": "inner", "prefix": "inner", "db": ".filigree/filigree.db"})

        # From inside child: child wins
        deep = child_dir / "src"
        deep.mkdir()
        assert find_filigree_conf(deep) == child_conf

        # From a sibling that's not under the child: parent wins
        sibling = tmp_path / "other"
        sibling.mkdir()
        assert find_filigree_conf(sibling) == parent_conf

    def test_raises_when_no_conf_anywhere(self, tmp_path: Path) -> None:
        """No .filigree.conf in tree → ProjectNotInitialisedError with init/doctor hint."""
        with pytest.raises(ProjectNotInitialisedError) as excinfo:
            find_filigree_conf(tmp_path)
        msg = str(excinfo.value)
        assert "filigree init" in msg
        assert "filigree doctor" in msg

    def test_strict_raises_for_legacy_only_install(self, tmp_path: Path) -> None:
        """``find_filigree_conf`` is strict: a bare legacy ``.filigree/`` does
        not satisfy it. Callers that need to tolerate legacy installs must use
        :func:`find_filigree_anchor` instead.
        """
        (tmp_path / FILIGREE_DIR_NAME).mkdir()
        with pytest.raises(ProjectNotInitialisedError):
            find_filigree_conf(tmp_path)
        # And — critically — discovery did NOT write the conf.
        assert not (tmp_path / CONF_FILENAME).exists()


# ---------------------------------------------------------------------------
# Conf file I/O
# ---------------------------------------------------------------------------


class TestConfIO:
    def test_write_and_read_roundtrip(self, tmp_path: Path) -> None:
        conf = tmp_path / CONF_FILENAME
        data = {"version": 1, "project_name": "demo", "prefix": "demo", "db": ".filigree/filigree.db"}
        write_conf(conf, data)
        assert read_conf(conf) == data

    def test_read_rejects_non_dict(self, tmp_path: Path) -> None:
        conf = tmp_path / CONF_FILENAME
        conf.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ValueError, match="object"):
            read_conf(conf)

    def test_read_rejects_missing_required_keys(self, tmp_path: Path) -> None:
        conf = tmp_path / CONF_FILENAME
        conf.write_text(json.dumps({"version": 1}))  # no prefix, no db
        with pytest.raises(ValueError, match=r"prefix|db"):
            read_conf(conf)


# ---------------------------------------------------------------------------
# find_filigree_anchor — discovery that tolerates legacy installs without writing
# ---------------------------------------------------------------------------


class TestFindFiligreeAnchor:
    """``find_filigree_anchor`` is the discovery primitive for read-only
    contexts (inspection commands, MCP startup, ``filigree doctor``).

    It returns ``(project_root, conf_path_or_None)`` so callers can decide
    how to open the project — via :meth:`FiligreeDB.from_conf` for v2.0
    installs, or via :meth:`FiligreeDB.from_filigree_dir` for legacy ones —
    without ever requiring write access during discovery.
    """

    def test_returns_conf_path_for_v2_install(self, tmp_path: Path) -> None:
        conf = tmp_path / CONF_FILENAME
        write_conf(conf, {"version": 1, "project_name": "p", "prefix": "p", "db": ".filigree/filigree.db"})
        project_root, conf_path = find_filigree_anchor(tmp_path)
        assert project_root == tmp_path
        assert conf_path == conf

    def test_returns_none_conf_for_legacy_install(self, tmp_path: Path) -> None:
        """Legacy install: project_root is identified, conf_path is None."""
        legacy_dir = tmp_path / FILIGREE_DIR_NAME
        legacy_dir.mkdir()
        project_root, conf_path = find_filigree_anchor(tmp_path)
        assert project_root == tmp_path
        assert conf_path is None

    def test_does_not_write_during_legacy_discovery(self, tmp_path: Path) -> None:
        """Regression: discovery must be read-only.

        Previously :func:`find_filigree_conf` backfilled ``.filigree.conf`` on
        legacy installs during the walk, which broke read-only mounts and
        inspection-only commands. Discovery now never writes.
        """
        legacy_dir = tmp_path / FILIGREE_DIR_NAME
        legacy_dir.mkdir()
        (legacy_dir / "config.json").write_text(json.dumps({"prefix": "legacy", "name": "Legacy", "version": 1}))
        FiligreeDB(legacy_dir / DB_FILENAME, prefix="legacy").initialize()

        find_filigree_anchor(tmp_path)
        find_filigree_root(tmp_path)
        assert not (tmp_path / CONF_FILENAME).exists()

    @pytest.mark.skipif(
        sys.platform == "win32" or os.geteuid() == 0,
        reason="POSIX-only; root bypasses dir mode bits",
    )
    def test_legacy_install_discoverable_on_readonly_mount(self, tmp_path: Path) -> None:
        """The motivating regression: discovery must not fail with PermissionError
        when the project root is read-only and a pre-2.0 ``.filigree/`` exists.
        """
        legacy_dir = tmp_path / FILIGREE_DIR_NAME
        legacy_dir.mkdir()
        FiligreeDB(legacy_dir / DB_FILENAME, prefix="legacy").initialize()

        # Drop write permission on the project root (simulates a read-only checkout).
        original_mode = tmp_path.stat().st_mode
        os.chmod(tmp_path, 0o555)  # noqa: S103 — read-only mode is the test scenario
        try:
            project_root, conf_path = find_filigree_anchor(tmp_path)
        finally:
            os.chmod(tmp_path, original_mode)

        assert project_root == tmp_path
        assert conf_path is None  # legacy — no write attempted
        assert not (tmp_path / CONF_FILENAME).exists()

    def test_child_anchor_wins_over_legacy_ancestor(self, tmp_path: Path) -> None:
        """A child ``.filigree.conf`` takes precedence even when an ancestor
        has a legacy ``.filigree/`` dir — closer-first walk-up.
        """
        (tmp_path / FILIGREE_DIR_NAME).mkdir()
        child = tmp_path / "sub"
        child.mkdir()
        child_conf = child / CONF_FILENAME
        write_conf(child_conf, {"version": 1, "project_name": "c", "prefix": "c", "db": ".filigree/filigree.db"})

        project_root, conf_path = find_filigree_anchor(child)
        assert project_root == child
        assert conf_path == child_conf

    def test_raises_when_neither_anchor_anywhere(self, tmp_path: Path) -> None:
        with pytest.raises(ProjectNotInitialisedError):
            find_filigree_anchor(tmp_path)


# ---------------------------------------------------------------------------
# FiligreeDB.from_project / from_conf — discovery integration
# ---------------------------------------------------------------------------


class TestFindFiligreeRoot:
    """``find_filigree_root`` is a back-compat helper. Its historical contract
    is to return the project's ``.filigree/`` directory — every caller in the
    repo concatenates ``SUMMARY_FILENAME`` / ``ephemeral.pid`` / ``DB_FILENAME``
    onto it, or does ``.parent`` to derive the project root.

    Returning ``db_path.parent`` (the previous v2.0 behaviour) silently
    misroutes those callers when the conf's ``db`` field points elsewhere.
    """

    def test_returns_dotfiligree_dir_for_default_layout(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / FILIGREE_DIR_NAME
        filigree_dir.mkdir()
        write_conf(
            tmp_path / CONF_FILENAME,
            {"version": 1, "project_name": "p", "prefix": "p", "db": f"{FILIGREE_DIR_NAME}/{DB_FILENAME}"},
        )
        assert find_filigree_root(tmp_path) == filigree_dir

    def test_returns_dotfiligree_dir_when_db_points_elsewhere(self, tmp_path: Path) -> None:
        """Custom ``db`` location must not alter ``find_filigree_root``'s contract.

        Regression: returning ``db_path.parent`` made ``mcp_server._run`` reopen
        ``storage/filigree.db`` instead of the configured ``storage/track.db``,
        and made ``install`` derive the wrong project root via ``.parent``.
        """
        (tmp_path / "storage").mkdir()
        write_conf(
            tmp_path / CONF_FILENAME,
            {"version": 1, "project_name": "p", "prefix": "p", "db": "storage/track.db"},
        )
        assert find_filigree_root(tmp_path) == tmp_path / FILIGREE_DIR_NAME


class TestFromConf:
    def test_from_conf_opens_db_at_relative_path(self, tmp_path: Path) -> None:
        filigree_dir = tmp_path / FILIGREE_DIR_NAME
        filigree_dir.mkdir()
        conf = tmp_path / CONF_FILENAME
        write_conf(
            conf,
            {"version": 1, "project_name": "p", "prefix": "p", "db": f"{FILIGREE_DIR_NAME}/{DB_FILENAME}"},
        )

        db = FiligreeDB.from_conf(conf)
        try:
            assert db.prefix == "p"
            assert db.db_path == filigree_dir / DB_FILENAME
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Prefix-mismatch guard
# ---------------------------------------------------------------------------


@pytest.fixture
def db_p(tmp_path: Path) -> FiligreeDB:
    """Fresh FiligreeDB with prefix='alpha'."""
    d = FiligreeDB(tmp_path / "filigree.db", prefix="alpha")
    d.initialize()
    return d


class TestWrongProjectErrorOnWrites:
    """The prefix-mismatch guard is enforced on **write** operations only.

    Reads (get_issue, get_comments, get_issue_files, get_issue_events,
    get_valid_transitions, validate_issue) intentionally do *not* enforce —
    they return KeyError / empty results as before. Migration and other
    cross-prefix read scenarios depend on this.

    Writes always enforce: an agent that climbed into the wrong DB and tries
    to mutate a foreign-prefix ticket gets WrongProjectError.
    """

    def test_update_issue_with_wrong_prefix_raises(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError) as excinfo:
            db_p.update_issue("beefdata-abc123", title="x")
        msg = str(excinfo.value)
        assert "alpha" in msg
        assert "beefdata" in msg
        db_p.close()

    def test_close_issue_with_wrong_prefix_raises(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError):
            db_p.close_issue("beefdata-abc123")
        db_p.close()

    def test_reopen_issue_with_wrong_prefix_raises(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError):
            db_p.reopen_issue("beefdata-abc123")
        db_p.close()

    def test_claim_issue_with_wrong_prefix_raises(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError):
            db_p.claim_issue("beefdata-abc123", assignee="me")
        db_p.close()

    def test_release_claim_with_wrong_prefix_raises(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError):
            db_p.release_claim("beefdata-abc123")
        db_p.close()

    def test_add_comment_with_wrong_prefix_raises(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError):
            db_p.add_comment("beefdata-abc123", "hi")
        db_p.close()

    def test_add_label_with_wrong_prefix_raises(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError):
            db_p.add_label("beefdata-abc123", "foo")
        db_p.close()

    def test_remove_label_with_wrong_prefix_raises(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError):
            db_p.remove_label("beefdata-abc123", "foo")
        db_p.close()

    def test_add_dependency_with_wrong_prefix_raises_for_either_side(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError):
            db_p.add_dependency("beefdata-abc", "alpha-xyz")
        with pytest.raises(WrongProjectError):
            db_p.add_dependency("alpha-xyz", "beefdata-abc")
        db_p.close()

    def test_remove_dependency_with_wrong_prefix_raises(self, db_p: FiligreeDB) -> None:
        with pytest.raises(WrongProjectError):
            db_p.remove_dependency("beefdata-abc", "alpha-xyz")
        db_p.close()

    def test_create_issue_unaffected(self, db_p: FiligreeDB) -> None:
        """create_issue assigns the prefix from the DB — no caller-supplied ID to check."""
        issue = db_p.create_issue("Test")
        assert issue.id.startswith("alpha-")
        db_p.close()

    def test_correct_prefix_passes(self, db_p: FiligreeDB) -> None:
        """Sanity: the prefix check doesn't reject legitimate IDs."""
        issue = db_p.create_issue("Test")
        fetched = db_p.get_issue(issue.id)
        assert fetched.id == issue.id
        db_p.close()

    def test_hyphenated_prefix_does_not_trip_guard(self, tmp_path: Path) -> None:
        """A project whose prefix contains a hyphen (e.g. ``my-app``) must still
        be able to mutate its own issues.

        Regression: ``filigree init`` defaults the prefix to ``cwd.name``; a
        repo checked out as ``my-app/`` generates IDs like ``my-app-abc1234567``.
        Splitting the ID on the first ``-`` returned ``my`` and falsely tripped
        WrongProjectError, leaving such projects effectively read-only.
        """
        db = FiligreeDB(tmp_path / "filigree.db", prefix="my-app")
        db.initialize()
        try:
            issue = db.create_issue("Test")
            assert issue.id.startswith("my-app-")
            # Each of these would have raised WrongProjectError pre-fix.
            db.update_issue(issue.id, title="renamed")
            db.add_label(issue.id, "needs-review")
            db.add_comment(issue.id, "still working")
            db.close_issue(issue.id)
        finally:
            db.close()


class TestReadsDoNotEnforcePrefix:
    """Read methods deliberately allow cross-prefix lookups (return KeyError)."""

    def test_get_issue_foreign_prefix_returns_keyerror(self, db_p: FiligreeDB) -> None:
        with pytest.raises(KeyError):
            db_p.get_issue("beefdata-abc123")
        db_p.close()

    def test_get_comments_foreign_prefix_returns_empty(self, db_p: FiligreeDB) -> None:
        # No row exists; should just return [] without error.
        assert db_p.get_comments("beefdata-abc123") == []
        db_p.close()
