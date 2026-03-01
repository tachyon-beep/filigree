"""IssuesMixin — issue CRUD, batch operations, search, and claiming.

Extracted from core.py as part of the module architecture split.
All methods access ``self.conn``, ``self.get_issue()``, etc. via
Python's MRO when composed into ``FiligreeDB``.
"""

from __future__ import annotations

import json
import logging
import re as _re
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from filigree.db_base import DBMixinProtocol, StatusCategory, _now_iso

if TYPE_CHECKING:
    from filigree.core import Issue
    from filigree.templates import TemplateRegistry, TransitionOption

logger = logging.getLogger(__name__)


def _validate_string_list(value: object, name: str) -> None:
    """Raise TypeError if *value* is not a list of strings."""
    if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
        msg = f"{name} must be a list of strings"
        raise TypeError(msg)


class IssuesMixin(DBMixinProtocol):
    """Issue CRUD, batch operations, search, and claiming.

    Inherits ``DBMixinProtocol`` for type-safe access to shared attributes.
    Actual implementations provided by ``FiligreeDB`` at composition time via MRO.
    """

    if TYPE_CHECKING:
        # From EventsMixin
        def _record_event(
            self,
            issue_id: str,
            event_type: str,
            *,
            actor: str = "",
            old_value: str | None = None,
            new_value: str | None = None,
            comment: str = "",
        ) -> None: ...

        # From WorkflowMixin
        @property
        def templates(self) -> TemplateRegistry: ...

        def _validate_status(self, status: str, issue_type: str = "task") -> None: ...
        def _validate_parent_id(self, parent_id: str | None) -> None: ...
        def _validate_label_name(self, label: str) -> str: ...
        def _get_states_for_category(self, category: str) -> list[str]: ...
        def _resolve_status_category(self, issue_type: str, status: str) -> StatusCategory: ...

        @staticmethod
        def _infer_status_category(status: str) -> StatusCategory: ...

        # From MetaMixin
        def add_label(self, issue_id: str, label: str) -> bool: ...
        def add_comment(self, issue_id: str, text: str, *, author: str = "") -> int: ...

        # From PlanningMixin
        def get_ready(self) -> list[Issue]: ...
        def get_valid_transitions(self, issue_id: str) -> list[TransitionOption]: ...

    # -- ID generation -------------------------------------------------------

    def _generate_unique_id(self, table: str, infix: str = "") -> str:
        """Generate a unique ID using O(1) EXISTS checks against the PK index.

        *table* is always a hardcoded literal at the call site (never user input).
        """
        sep = f"-{infix}-" if infix else "-"
        for _ in range(10):
            candidate = f"{self.prefix}{sep}{uuid.uuid4().hex[:10]}"
            if self.conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (candidate,)).fetchone() is None:
                return candidate
        return f"{self.prefix}{sep}{uuid.uuid4().hex[:16]}"

    # -- Issue CRUD ----------------------------------------------------------

    def create_issue(
        self,
        title: str,
        *,
        type: str = "task",
        priority: int = 2,
        parent_id: str | None = None,
        assignee: str = "",
        description: str = "",
        notes: str = "",
        fields: dict[str, Any] | None = None,
        labels: list[str] | None = None,
        deps: list[str] | None = None,
        actor: str = "",
    ) -> Issue:
        if not title or not title.strip():
            msg = "Title cannot be empty"
            raise ValueError(msg)
        if not (0 <= priority <= 4):
            msg = f"Priority must be between 0 and 4, got {priority}"
            raise ValueError(msg)
        if fields:
            for k in fields:
                if not k or not k.strip():
                    msg = "Field key cannot be empty"
                    raise ValueError(msg)
        if labels:
            labels = [self._validate_label_name(label) for label in labels]
        # Reject unknown types — don't silently fall back
        if self.templates.get_type(type) is None:
            valid_types = [t.type for t in self.templates.list_types()]
            msg = f"Unknown type '{type}'. Valid types: {', '.join(valid_types)}"
            raise ValueError(msg)

        self._validate_parent_id(parent_id)

        # Validate deps BEFORE any writes to prevent partial commits
        if deps:
            dep_ph = ",".join("?" * len(deps))
            found = {r["id"] for r in self.conn.execute(f"SELECT id FROM issues WHERE id IN ({dep_ph})", deps).fetchall()}
            missing = [d for d in deps if d not in found]
            if missing:
                msg = f"Invalid dependency IDs (not found): {', '.join(missing)}"
                raise ValueError(msg)

        issue_id = self._generate_unique_id("issues")
        now = _now_iso()
        fields = fields or {}

        # Determine initial state from template
        initial_state = self.templates.get_initial_state(type)

        try:
            self.conn.execute(
                "INSERT INTO issues (id, title, status, priority, type, parent_id, assignee, "
                "created_at, updated_at, description, notes, fields) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    issue_id,
                    title,
                    initial_state,
                    priority,
                    type,
                    parent_id,
                    assignee,
                    now,
                    now,
                    description,
                    notes,
                    json.dumps(fields),
                ),
            )

            self._record_event(issue_id, "created", actor=actor, new_value=title)

            if labels:
                for label in labels:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO labels (issue_id, label) VALUES (?, ?)",
                        (issue_id, label),
                    )

            if deps:
                for dep_id in deps:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, 'blocks', ?)",
                        (issue_id, dep_id, now),
                    )

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return self.get_issue(issue_id)

    def get_issue(self, issue_id: str) -> Issue:
        row = self.conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        if row is None:
            msg = f"Issue not found: {issue_id}"
            raise KeyError(msg)
        return self._build_issue(issue_id)

    def _build_issue(self, issue_id: str) -> Issue:
        """Build a single Issue with all computed fields. Internal — caller must validate existence."""
        issues = self._build_issues_batch([issue_id])
        if not issues:
            msg = f"Issue not found: {issue_id}"
            raise KeyError(msg)
        return issues[0]

    def _build_issues_batch(self, issue_ids: list[str]) -> list[Issue]:
        """Build multiple Issues efficiently with batched queries (eliminates N+1)."""
        from filigree.core import Issue

        if not issue_ids:
            return []

        placeholders = ",".join("?" * len(issue_ids))

        # 1. Fetch all issue rows
        rows_by_id: dict[str, sqlite3.Row] = {}
        for r in self.conn.execute(f"SELECT * FROM issues WHERE id IN ({placeholders})", issue_ids).fetchall():
            rows_by_id[r["id"]] = r

        # 2. Batch fetch labels
        labels_by_id: dict[str, list[str]] = {iid: [] for iid in issue_ids}
        for r in self.conn.execute(f"SELECT issue_id, label FROM labels WHERE issue_id IN ({placeholders})", issue_ids).fetchall():
            labels_by_id[r["issue_id"]].append(r["label"])

        # 3. Batch fetch "blocks" — issues blocked BY these IDs (where depends_on_id = this issue)
        blocks_by_id: dict[str, list[str]] = {iid: [] for iid in issue_ids}
        for r in self.conn.execute(
            f"SELECT depends_on_id, issue_id FROM dependencies WHERE depends_on_id IN ({placeholders})",
            issue_ids,
        ).fetchall():
            blocks_by_id[r["depends_on_id"]].append(r["issue_id"])

        # 4. Batch fetch "blocked_by" — only open (non-done) blockers
        done_states = self._get_states_for_category("done") or ["closed"]
        done_ph = ",".join("?" * len(done_states))
        blocked_by_id: dict[str, list[str]] = {iid: [] for iid in issue_ids}
        for r in self.conn.execute(
            f"SELECT d.issue_id, d.depends_on_id FROM dependencies d "
            f"JOIN issues blocker ON d.depends_on_id = blocker.id "
            f"WHERE d.issue_id IN ({placeholders}) AND blocker.status NOT IN ({done_ph})",
            [*issue_ids, *done_states],
        ).fetchall():
            blocked_by_id[r["issue_id"]].append(r["depends_on_id"])

        # 5. Batch fetch children
        children_by_id: dict[str, list[str]] = {iid: [] for iid in issue_ids}
        for r in self.conn.execute(f"SELECT id, parent_id FROM issues WHERE parent_id IN ({placeholders})", issue_ids).fetchall():
            children_by_id[r["parent_id"]].append(r["id"])

        # 6. Batch compute open blocker counts (reuses done_states/done_ph from step 4)
        open_blockers_by_id: dict[str, int] = dict.fromkeys(issue_ids, 0)
        for r in self.conn.execute(
            f"SELECT d.issue_id, COUNT(*) as cnt FROM dependencies d "
            f"JOIN issues i ON d.depends_on_id = i.id "
            f"WHERE d.issue_id IN ({placeholders}) AND i.status NOT IN ({done_ph}) "
            f"GROUP BY d.issue_id",
            [*issue_ids, *done_states],
        ).fetchall():
            open_blockers_by_id[r["issue_id"]] = r["cnt"]

        # 7. Compute open states for is_ready check
        open_states_set = set(self._get_states_for_category("open")) or {"open"}

        # Build Issue objects preserving input order
        result: list[Issue] = []
        for iid in issue_ids:
            row = rows_by_id.get(iid)
            if row is None:
                continue
            result.append(
                Issue(
                    id=row["id"],
                    title=row["title"],
                    status=row["status"],
                    priority=row["priority"],
                    type=row["type"],
                    parent_id=row["parent_id"],
                    assignee=row["assignee"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    closed_at=row["closed_at"],
                    description=row["description"],
                    notes=row["notes"],
                    fields=json.loads(row["fields"]) if row["fields"] else {},
                    labels=labels_by_id.get(iid, []),
                    blocks=blocks_by_id.get(iid, []),
                    blocked_by=blocked_by_id.get(iid, []),
                    is_ready=(row["status"] in open_states_set and open_blockers_by_id.get(iid, 0) == 0),
                    children=children_by_id.get(iid, []),
                    status_category=self._resolve_status_category(row["type"], row["status"]),
                )
            )
        return result

    def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        assignee: str | None = None,
        description: str | None = None,
        notes: str | None = None,
        parent_id: str | None = None,
        fields: dict[str, Any] | None = None,
        actor: str = "",
        _skip_transition_check: bool = False,
    ) -> Issue:
        current = self.get_issue(issue_id)
        now = _now_iso()

        # --- Validate all inputs BEFORE any writes to prevent partial commits ---
        if priority is not None and priority != current.priority and not (0 <= priority <= 4):
            msg = f"Priority must be between 0 and 4, got {priority}"
            raise ValueError(msg)

        if parent_id is not None and parent_id != "":
            if parent_id == issue_id:
                msg = f"Issue {issue_id} cannot be its own parent"
                raise ValueError(msg)
            self._validate_parent_id(parent_id)
            # Check for circular parent chain
            ancestor = parent_id
            while ancestor is not None:
                row = self.conn.execute("SELECT parent_id FROM issues WHERE id = ?", (ancestor,)).fetchone()
                if row is None:
                    break
                ancestor = row["parent_id"]
                if ancestor == issue_id:
                    msg = f"Setting parent_id to '{parent_id}' would create a circular parent chain"
                    raise ValueError(msg)

        # Cache transition validation result for reuse in write phase (warnings)
        _transition_result = None
        if status is not None and status != current.status:
            self._validate_status(status, current.type)

            if not _skip_transition_check:
                # Atomic transition-with-fields: validate merged fields against target state
                merged_fields = {**current.fields}
                if fields is not None:
                    merged_fields.update(fields)

                tpl = self.templates.get_type(current.type)
                if tpl is not None:
                    _transition_result = self.templates.validate_transition(current.type, current.status, status, merged_fields)
                    if not _transition_result.allowed:
                        if _transition_result.missing_fields:
                            missing_str = ", ".join(_transition_result.missing_fields)
                            msg = (
                                f"Cannot transition '{current.status}' -> '{status}' for type "
                                f"'{current.type}': missing required fields: {missing_str}"
                            )
                        else:
                            msg = (
                                f"Transition '{current.status}' -> '{status}' is not allowed for type "
                                f"'{current.type}'. Use get_valid_transitions() to see allowed transitions."
                            )
                        raise ValueError(msg)

        # --- All validation passed — now record events and apply changes ---
        updates: list[str] = []
        params: list[Any] = []

        try:
            if title is not None and title != current.title:
                self._record_event(issue_id, "title_changed", actor=actor, old_value=current.title, new_value=title)
                updates.append("title = ?")
                params.append(title)

            if status is not None and status != current.status:
                # Record soft-enforcement warnings from cached validation result
                if _transition_result is not None:
                    if _transition_result.warnings:
                        for warning in _transition_result.warnings:
                            self._record_event(
                                issue_id,
                                "transition_warning",
                                actor=actor,
                                old_value=current.status,
                                new_value=status,
                                comment=warning,
                            )
                    if _transition_result.missing_fields and _transition_result.enforcement == "soft":
                        self._record_event(
                            issue_id,
                            "transition_warning",
                            actor=actor,
                            old_value=current.status,
                            new_value=status,
                            comment=f"Missing recommended fields: {', '.join(_transition_result.missing_fields)}",
                        )

                self._record_event(issue_id, "status_changed", actor=actor, old_value=current.status, new_value=status)
                updates.append("status = ?")
                params.append(status)

                # Set closed_at when entering a done-category state
                status_cat = self.templates.get_category(current.type, status)
                is_done = (status_cat or self._infer_status_category(status)) == "done"

                if is_done:
                    updates.append("closed_at = ?")
                    params.append(now)
                else:
                    # Clear closed_at when leaving a done-category state
                    old_cat = self.templates.get_category(current.type, current.status)
                    if (old_cat or self._infer_status_category(current.status)) == "done":
                        updates.append("closed_at = NULL")

            if priority is not None and priority != current.priority:
                self._record_event(
                    issue_id,
                    "priority_changed",
                    actor=actor,
                    old_value=str(current.priority),
                    new_value=str(priority),
                )
                updates.append("priority = ?")
                params.append(priority)

            if assignee is not None and assignee != current.assignee:
                self._record_event(issue_id, "assignee_changed", actor=actor, old_value=current.assignee, new_value=assignee)
                updates.append("assignee = ?")
                params.append(assignee)

            if description is not None and description != current.description:
                self._record_event(
                    issue_id,
                    "description_changed",
                    actor=actor,
                    old_value=current.description,
                    new_value=description,
                )
                updates.append("description = ?")
                params.append(description)

            if notes is not None and notes != current.notes:
                self._record_event(
                    issue_id,
                    "notes_changed",
                    actor=actor,
                    old_value=current.notes,
                    new_value=notes,
                )
                updates.append("notes = ?")
                params.append(notes)

            if parent_id is not None:
                if parent_id == "":
                    # Clear parent
                    if current.parent_id is not None:
                        self._record_event(
                            issue_id,
                            "parent_changed",
                            actor=actor,
                            old_value=current.parent_id or "",
                            new_value="",
                        )
                        updates.append("parent_id = NULL")
                else:
                    if parent_id != current.parent_id:
                        self._record_event(
                            issue_id,
                            "parent_changed",
                            actor=actor,
                            old_value=current.parent_id or "",
                            new_value=parent_id,
                        )
                        updates.append("parent_id = ?")
                        params.append(parent_id)

            if fields is not None:
                # Merge into existing fields
                merged = {**current.fields, **fields}
                updates.append("fields = ?")
                params.append(json.dumps(merged))

            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.append(issue_id)
                sql = f"UPDATE issues SET {', '.join(updates)} WHERE id = ?"
                self.conn.execute(sql, params)
                self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return self.get_issue(issue_id)

    def close_issue(
        self,
        issue_id: str,
        *,
        reason: str = "",
        actor: str = "",
        status: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> Issue:
        if fields is not None and not isinstance(fields, dict):
            msg = "fields must be a dict"
            raise TypeError(msg)

        current = self.get_issue(issue_id)

        # Determine done state via template system
        if self._resolve_status_category(current.type, current.status) == "done":
            msg = f"Issue {issue_id} is already closed (status: '{current.status}', closed_at: {current.closed_at})"
            raise ValueError(msg)

        if status is not None:
            # Validate that the requested status is a done-category state
            target_category = self.templates.get_category(current.type, status)
            if target_category != "done":
                msg = f"Cannot close with status '{status}': it is not a done-category state for type '{current.type}'."
                raise ValueError(msg)
            done_status = status
        else:
            # Default to first done-category state
            _first_done = self.templates.get_first_state_of_category(current.type, "done")
            done_status = _first_done if _first_done is not None else "closed"

        # Enforce hard gates even though close_issue skips transition graph
        # validation. If a defined transition from current→done has hard
        # enforcement, the required fields must be satisfied.
        merged_fields = {**current.fields}
        if fields:
            merged_fields.update(fields)
        if reason:
            merged_fields["close_reason"] = reason
        result = self.templates.validate_transition(current.type, current.status, done_status, merged_fields)
        if not result.allowed and result.enforcement == "hard":
            missing_str = ", ".join(result.missing_fields)
            msg = f"Cannot close issue {issue_id}: hard-enforcement gate requires fields: {missing_str}"
            raise ValueError(msg)

        # Merge close_reason into fields for the update call
        update_fields: dict[str, Any] = {}
        if fields:
            update_fields.update(fields)
        if reason:
            update_fields["close_reason"] = reason

        return self.update_issue(
            issue_id,
            status=done_status,
            fields=update_fields or None,
            actor=actor,
            _skip_transition_check=True,
        )

    def reopen_issue(self, issue_id: str, *, actor: str = "") -> Issue:
        """Reopen a closed issue, returning it to its type's initial state.

        Clears closed_at. Only works on issues in done-category states.
        """
        current = self.get_issue(issue_id)
        if self._resolve_status_category(current.type, current.status) != "done":
            msg = f"Cannot reopen {issue_id}: status '{current.status}' is not in a done-category state"
            raise ValueError(msg)

        initial_state = self.templates.get_initial_state(current.type)
        # _record_event and update_issue share the same implicit transaction;
        # update_issue owns the commit/rollback lifecycle.
        self._record_event(issue_id, "reopened", actor=actor, old_value=current.status, new_value=initial_state)
        return self.update_issue(issue_id, status=initial_state, actor=actor, _skip_transition_check=True)

    def claim_issue(self, issue_id: str, *, assignee: str, actor: str = "") -> Issue:
        """Atomically claim an open-category issue with optimistic locking.

        Sets assignee only — does NOT change status. Agent uses update_issue
        to advance through the workflow after claiming.

        Uses a single atomic UPDATE with WHERE guard to prevent race conditions
        where two agents try to claim the same issue concurrently.
        """
        if not assignee or not assignee.strip():
            msg = "Assignee cannot be empty"
            raise ValueError(msg)
        # Look up the issue type and current assignee so we know which states are "open"
        # and can record old_value for undo
        row = self.conn.execute("SELECT type, assignee FROM issues WHERE id = ?", (issue_id,)).fetchone()
        if row is None:
            msg = f"Issue not found: {issue_id}"
            raise KeyError(msg)
        issue_type = row["type"]
        old_assignee = row["assignee"] or ""

        # Get all open-category states for this type
        open_states: list[str] = []
        tpl = self.templates.get_type(issue_type)
        if tpl is not None:
            open_states = [s.name for s in tpl.states if s.category == "open"]
        if not open_states:
            open_states = ["open"]

        # Atomic UPDATE: only succeeds if issue is unassigned OR already owned by this agent
        status_ph = ",".join("?" * len(open_states))
        try:
            cursor = self.conn.execute(
                f"UPDATE issues SET assignee = ?, updated_at = ? "
                f"WHERE id = ? AND status IN ({status_ph}) "
                f"AND (assignee = '' OR assignee IS NULL OR assignee = ?)",
                [assignee, _now_iso(), issue_id, *open_states, assignee],
            )

            if cursor.rowcount == 0:
                # Figure out why it failed: wrong status or already claimed?
                current = self.conn.execute("SELECT status, assignee FROM issues WHERE id = ?", (issue_id,)).fetchone()
                if current is None:
                    msg = f"Issue not found: {issue_id}"
                    raise KeyError(msg)
                if current["assignee"] and current["assignee"] != assignee:
                    msg = f"Cannot claim {issue_id}: already assigned to '{current['assignee']}'"
                    raise ValueError(msg)
                msg = f"Cannot claim {issue_id}: status is '{current['status']}', expected open-category state"
                raise ValueError(msg)

            self._record_event(issue_id, "claimed", actor=actor, old_value=old_assignee, new_value=assignee)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_issue(issue_id)

    def release_claim(self, issue_id: str, *, actor: str = "") -> Issue:
        """Release a claimed issue by clearing its assignee.

        Does NOT change status. Only succeeds if issue has an assignee.
        """
        current = self.get_issue(issue_id)

        if not current.assignee:
            msg = f"Cannot release {issue_id}: no assignee set"
            raise ValueError(msg)

        try:
            self.conn.execute(
                "UPDATE issues SET assignee = '', updated_at = ? WHERE id = ?",
                [_now_iso(), issue_id],
            )

            self._record_event(issue_id, "released", actor=actor, old_value=current.assignee)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_issue(issue_id)

    def claim_next(
        self,
        assignee: str,
        *,
        type_filter: str | None = None,
        priority_min: int | None = None,
        priority_max: int | None = None,
        actor: str = "",
    ) -> Issue | None:
        """Claim the highest-priority ready issue matching filters.

        Iterates ready issues sorted by priority and attempts claim_issue()
        on each until one succeeds (handles race conditions with retry).
        Returns None if no matching ready issues exist.
        """
        if not assignee or not assignee.strip():
            msg = "Assignee cannot be empty"
            raise ValueError(msg)
        ready = self.get_ready()

        skipped = 0
        for issue in ready:
            if type_filter is not None and issue.type != type_filter:
                continue
            if priority_min is not None and issue.priority < priority_min:
                continue
            if priority_max is not None and issue.priority > priority_max:
                continue
            try:
                return self.claim_issue(issue.id, assignee=assignee, actor=actor or assignee)
            except ValueError as exc:
                skipped += 1
                logger.debug("claim_next: skipping %s: %s", issue.id, exc)
                continue  # Race condition or status mismatch
        if skipped:
            logger.warning("claim_next: all %d candidate(s) failed to claim for '%s'", skipped, assignee)
        return None

    def batch_close(
        self,
        issue_ids: list[str],
        *,
        reason: str = "",
        actor: str = "",
    ) -> tuple[list[Issue], list[dict[str, Any]]]:
        """Close multiple issues with per-item error handling. Returns (closed, errors)."""
        _validate_string_list(issue_ids, "issue_ids")
        results: list[Issue] = []
        errors: list[dict[str, Any]] = []
        for issue_id in issue_ids:
            try:
                results.append(self.close_issue(issue_id, reason=reason, actor=actor))
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}", "code": "not_found"})
            except ValueError as e:
                err: dict[str, Any] = {"id": issue_id, "error": str(e), "code": "invalid_transition"}
                try:
                    transitions = self.get_valid_transitions(issue_id)
                    err["valid_transitions"] = [{"to": t.to, "category": t.category} for t in transitions]
                except KeyError:
                    pass
                errors.append(err)
        return results, errors

    def batch_update(
        self,
        issue_ids: list[str],
        *,
        status: str | None = None,
        priority: int | None = None,
        assignee: str | None = None,
        fields: dict[str, Any] | None = None,
        actor: str = "",
    ) -> tuple[list[Issue], list[dict[str, Any]]]:
        """Update multiple issues with the same changes. Returns (updated, errors)."""
        _validate_string_list(issue_ids, "issue_ids")
        results: list[Issue] = []
        errors: list[dict[str, Any]] = []
        for issue_id in issue_ids:
            try:
                results.append(
                    self.update_issue(
                        issue_id,
                        status=status,
                        priority=priority,
                        assignee=assignee,
                        fields=fields,
                        actor=actor,
                    )
                )
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}", "code": "not_found"})
            except ValueError as e:
                err: dict[str, Any] = {"id": issue_id, "error": str(e), "code": "invalid_transition"}
                try:
                    transitions = self.get_valid_transitions(issue_id)
                    err["valid_transitions"] = [{"to": t.to, "category": t.category} for t in transitions]
                except KeyError:
                    pass
                errors.append(err)
        return results, errors

    def batch_add_label(
        self,
        issue_ids: list[str],
        *,
        label: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Add the same label to multiple issues. Returns (labeled, errors)."""
        _validate_string_list(issue_ids, "issue_ids")
        if not isinstance(label, str):
            msg = "label must be a string"
            raise TypeError(msg)

        results: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        for issue_id in issue_ids:
            try:
                self.get_issue(issue_id)
                added = self.add_label(issue_id, label)
                results.append({"id": issue_id, "status": "added" if added else "already_exists"})
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}", "code": "not_found"})
            except ValueError as e:
                errors.append({"id": issue_id, "error": str(e), "code": "validation_error"})
        return results, errors

    def batch_add_comment(
        self,
        issue_ids: list[str],
        *,
        text: str,
        author: str = "",
    ) -> tuple[list[dict[str, str | int]], list[dict[str, str]]]:
        """Add the same comment to multiple issues. Returns (commented, errors)."""
        _validate_string_list(issue_ids, "issue_ids")
        if not isinstance(text, str):
            msg = "text must be a string"
            raise TypeError(msg)
        if not isinstance(author, str):
            msg = "author must be a string"
            raise TypeError(msg)

        results: list[dict[str, str | int]] = []
        errors: list[dict[str, str]] = []
        for issue_id in issue_ids:
            try:
                self.get_issue(issue_id)
                comment_id = self.add_comment(issue_id, text, author=author)
                results.append({"id": issue_id, "comment_id": comment_id})
            except KeyError:
                errors.append({"id": issue_id, "error": f"Not found: {issue_id}", "code": "not_found"})
            except ValueError as e:
                errors.append({"id": issue_id, "error": str(e), "code": "validation_error"})
        return results, errors

    def list_issues(
        self,
        *,
        status: str | None = None,
        type: str | None = None,
        priority: int | None = None,
        parent_id: str | None = None,
        assignee: str | None = None,
        label: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Issue]:
        if limit < 0:
            limit = 100
        if offset < 0:
            offset = 0
        conditions: list[str] = []
        params: list[Any] = []

        if status is not None:
            # Check if status is a category name (with aliases)
            category_aliases = {"in_progress": "wip", "closed": "done"}
            category_key = category_aliases.get(status, status)
            category_states: list[str] = []
            if category_key in ("open", "wip", "done"):
                category_states = self._get_states_for_category(category_key)

            if category_states:
                placeholders = ",".join("?" * len(category_states))
                conditions.append(f"status IN ({placeholders})")
                params.extend(category_states)
            else:
                # Literal state match (either not a category, or W7 empty guard)
                conditions.append("status = ?")
                params.append(status)
        if type is not None:
            conditions.append("type = ?")
            params.append(type)
        if priority is not None:
            conditions.append("priority = ?")
            params.append(priority)
        if parent_id is not None:
            conditions.append("parent_id = ?")
            params.append(parent_id)
        if assignee is not None:
            conditions.append("assignee = ?")
            params.append(assignee)
        if label is not None:
            conditions.append("id IN (SELECT issue_id FROM labels WHERE label = ?)")
            params.append(label)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        rows = self.conn.execute(
            f"SELECT id FROM issues{where} ORDER BY priority, created_at LIMIT ? OFFSET ?",
            params,
        ).fetchall()

        return self._build_issues_batch([r["id"] for r in rows])

    def search_issues(self, query: str, *, limit: int = 100, offset: int = 0) -> list[Issue]:
        # Try FTS5 first, fall back to LIKE if FTS table doesn't exist
        try:
            # Sanitize: strip non-alphanumeric chars except * (prefix) and " (phrase)
            sanitized = _re.sub(r'[^\w\s*"]', "", query)
            # Quote each token and add * for prefix matching, then join with AND
            # Strip double quotes from tokens to prevent FTS5 syntax injection
            tokens = [t.replace('"', "") for t in sanitized.strip().split()]
            tokens = [t for t in tokens if t]  # drop empty tokens after stripping
            fts_query = " AND ".join(f'"{t}"*' for t in tokens) if tokens else '""'
            rows = self.conn.execute(
                "SELECT i.id FROM issues i "
                "JOIN issues_fts ON issues_fts.rowid = i.rowid "
                "WHERE issues_fts MATCH ? "
                "ORDER BY issues_fts.rank LIMIT ? OFFSET ?",
                (fts_query, limit, offset),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc) and "no such module" not in str(exc):
                raise
            # FTS5 not available — fall back to LIKE
            logging.getLogger(__name__).warning(
                "FTS5 search unavailable (%s); falling back to LIKE. Performance may be degraded. Run 'filigree doctor' to check.",
                exc,
            )
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            rows = self.conn.execute(
                "SELECT id FROM issues WHERE title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
                "ORDER BY priority, created_at LIMIT ? OFFSET ?",
                (pattern, pattern, limit, offset),
            ).fetchall()
        return self._build_issues_batch([r["id"] for r in rows])
