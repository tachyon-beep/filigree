"""Mixin for observation (agent scratchpad) operations.

Observations are lightweight, disposable candidates — not issues.
They live in their own table and are promoted to issues or dismissed.

Includes:
- 14-day TTL with piggyback sweep on reads (in savepoint)
- Dismissal audit trail via dismissed_observations table
- Atomic promotion via DELETE...RETURNING
- Age stats for session context prompting
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from filigree.db_base import DBMixinProtocol, _now_iso

if TYPE_CHECKING:
    from filigree.core import FileRecord, Issue

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 14
STALE_THRESHOLD_HOURS = 48
DISMISSED_AUDIT_TRAIL_CAP = 10_000


def _expires_iso(ttl_days: int = DEFAULT_TTL_DAYS) -> str:
    """Compute expiry timestamp using same isoformat() as _now_iso for consistent text comparison."""
    return (datetime.now(UTC) + timedelta(days=ttl_days)).isoformat()


class ObservationsMixin(DBMixinProtocol):
    """Observation CRUD — agent scratchpad for things noticed in passing.

    Inherits ``DBMixinProtocol`` for type-safe access to shared attributes.
    Actual implementations provided by ``FiligreeDB`` at composition time via MRO.
    """

    if TYPE_CHECKING:
        # From FilesMixin
        def register_file(
            self,
            path: str,
            *,
            language: str = "",
            file_type: str = "",
            metadata: dict[str, Any] | None = None,
        ) -> FileRecord: ...
        def add_file_association(self, file_id: str, issue_id: str, assoc_type: str) -> None: ...

        # From IssuesMixin — stub must match real signature exactly
        # (test_stub_signature_matches enforces parameter count)
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
        ) -> Issue: ...

        # From MetaMixin
        def add_label(self, issue_id: str, label: str) -> bool: ...

        # From core
        def _generate_unique_id(self, table: str, infix: str = "") -> str: ...

    def _sweep_expired_observations(self) -> int:
        """Delete expired observations in a savepoint (piggyback cleanup).

        All expired observations are logged to dismissed_observations and deleted.
        Uses a savepoint so it doesn't commit or interfere with in-flight transactions.
        """
        now = _now_iso()
        self.conn.execute("SAVEPOINT sweep_obs")
        try:
            self.conn.execute(
                "INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) "
                "SELECT id, summary, 'system', 'expired (TTL)', ? FROM observations WHERE expires_at <= ?",
                (now, now),
            )
            cursor = self.conn.execute("DELETE FROM observations WHERE expires_at <= ?", (now,))
            # v1 known limitation: audit trail cap is only enforced during sweep
            # (triggered by list_observations / observation_stats(sweep=True)).
            # Dismiss/promote paths do not prune. Acceptable for experiment scope.
            # Prune dismissed_observations audit trail to prevent unbounded growth.
            # Keep the most recent DISMISSED_AUDIT_TRAIL_CAP entries.
            # Note: without an index on dismissed_at, this is O(N log N) for
            # large tables. Acceptable for v1 experiment scale.
            self.conn.execute(
                "DELETE FROM dismissed_observations WHERE id NOT IN "
                "(SELECT id FROM dismissed_observations ORDER BY dismissed_at DESC LIMIT ?)",
                (DISMISSED_AUDIT_TRAIL_CAP,),
            )
            self.conn.execute("RELEASE SAVEPOINT sweep_obs")
            if cursor.rowcount > 0:
                logger.info("Swept %d expired observations", cursor.rowcount)
            return cursor.rowcount
        except Exception:
            logger.warning("Observation sweep failed, rolled back", exc_info=True)
            self.conn.execute("ROLLBACK TO SAVEPOINT sweep_obs")
            raise

    def create_observation(
        self,
        summary: str,
        *,
        detail: str = "",
        file_path: str = "",
        line: int | None = None,
        source_issue_id: str = "",
        priority: int = 3,
        actor: str = "",
    ) -> dict[str, Any]:
        if not summary or not summary.strip():
            raise ValueError("Observation summary cannot be empty")
        if not (0 <= priority <= 4):
            raise ValueError(f"priority must be between 0 and 4, got {priority}")
        if line is not None and line < 0:
            raise ValueError(f"line must be >= 0, got {line}")

        file_id: str | None = None
        if file_path:
            fr = self.register_file(file_path)
            file_id = fr.id

        obs_id = self._generate_unique_id("observations", "obs")
        now = _now_iso()
        expires = _expires_iso()
        # INSERT OR IGNORE: dedup index silently drops exact duplicates.
        # On conflict, rowcount == 0 and we return the existing row instead
        # of the rejected candidate — avoids returning a stale obs_id that
        # doesn't exist in the DB.
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO observations (id, summary, detail, file_id, file_path, line, "
            "source_issue_id, priority, actor, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (obs_id, summary.strip(), detail, file_id, file_path, line, source_issue_id, priority, actor, now, expires),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            # Duplicate — return the existing observation
            existing = self.conn.execute(
                "SELECT * FROM observations WHERE summary = ? AND file_path = ? AND coalesce(line, -1) = ?",
                (summary.strip(), file_path, line if line is not None else -1),
            ).fetchone()
            if existing:
                return dict(existing)
        return {
            "id": obs_id,
            "summary": summary.strip(),
            "detail": detail,
            "file_id": file_id,
            "file_path": file_path,
            "line": line,
            "source_issue_id": source_issue_id,
            "priority": priority,
            "actor": actor,
            "created_at": now,
            "expires_at": expires,
        }

    def list_observations(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        file_path: str = "",
        file_id: str = "",
    ) -> list[dict[str, Any]]:
        self._sweep_expired_observations()
        if file_id:
            # Direct FK query — more precise than path LIKE.
            rows = self.conn.execute(
                "SELECT * FROM observations WHERE file_id = ? ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?",
                (file_id, limit, offset),
            ).fetchall()
        elif file_path:
            # Escape LIKE wildcards in user-provided path to prevent % and _
            # from being interpreted as SQL wildcards.
            escaped = file_path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = self.conn.execute(
                "SELECT * FROM observations WHERE file_path LIKE ? ESCAPE '\\' ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?",
                (f"%{escaped}%", limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM observations ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def observation_count(self) -> int:
        """Return total observation count WITHOUT sweeping expired rows.

        This is intentionally a raw count. It may include expired observations
        that have not yet been cleaned up. Use ``list_observations()`` for a
        sweep-then-read pattern, or call ``_sweep_expired_observations()``
        explicitly if an accurate count is needed.
        """
        row = self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()
        return int(row[0])

    def observation_stats(self, *, sweep: bool = True) -> dict[str, Any]:
        """Return observation count + age stats for session context prompting.

        Args:
            sweep: If True (default), sweep expired observations first.
                   Pass False when calling from read-only context paths
                   (summary generation, MCP prompt) to avoid write side effects.
        """
        if sweep:
            self._sweep_expired_observations()
        count = self.observation_count()
        if count == 0:
            return {"count": 0, "stale_count": 0, "oldest_hours": 0, "expiring_soon_count": 0}

        now = datetime.now(UTC)
        stale_cutoff = (now - timedelta(hours=STALE_THRESHOLD_HOURS)).isoformat()
        expiring_cutoff = (now + timedelta(hours=24)).isoformat()

        stale = self.conn.execute("SELECT COUNT(*) FROM observations WHERE created_at <= ?", (stale_cutoff,)).fetchone()[0]
        expiring = self.conn.execute("SELECT COUNT(*) FROM observations WHERE expires_at <= ?", (expiring_cutoff,)).fetchone()[0]
        oldest_row = self.conn.execute("SELECT MIN(created_at) FROM observations").fetchone()
        oldest_hours = 0.0
        if oldest_row and oldest_row[0]:
            oldest_dt = datetime.fromisoformat(oldest_row[0])
            oldest_hours = (now - oldest_dt).total_seconds() / 3600

        return {
            "count": count,
            "stale_count": stale,
            "oldest_hours": round(oldest_hours, 1),
            "expiring_soon_count": expiring,
        }

    def dismiss_observation(
        self,
        obs_id: str,
        *,
        actor: str = "",
        reason: str = "",
    ) -> None:
        row = self.conn.execute("SELECT id, summary FROM observations WHERE id = ?", (obs_id,)).fetchone()
        if row is None:
            raise ValueError(f"Observation not found: {obs_id}")
        now = _now_iso()
        self.conn.execute(
            "INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) VALUES (?, ?, ?, ?, ?)",
            (obs_id, row["summary"], actor, reason, now),
        )
        self.conn.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
        self.conn.commit()

    def batch_dismiss_observations(
        self,
        obs_ids: list[str],
        *,
        actor: str = "",
        reason: str = "",
    ) -> int:
        if not obs_ids:
            return 0
        now = _now_iso()
        placeholders = ",".join("?" for _ in obs_ids)
        # Log all to audit trail before deletion
        self.conn.execute(
            f"INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) "
            f"SELECT id, summary, ?, ?, ? FROM observations WHERE id IN ({placeholders})",
            [actor, reason, now, *obs_ids],
        )
        cursor = self.conn.execute(
            f"DELETE FROM observations WHERE id IN ({placeholders})",
            obs_ids,
        )
        self.conn.commit()
        return cursor.rowcount

    def promote_observation(
        self,
        obs_id: str,
        *,
        issue_type: str = "bug",
        priority: int | None = None,
        title: str | None = None,
        extra_description: str = "",
        actor: str = "",
    ) -> dict[str, Any]:
        # Wrap the entire promote in a savepoint so the observation DELETE
        # and issue creation are atomic. If issue creation fails, the
        # observation is restored via rollback — no data loss.
        self.conn.execute("SAVEPOINT promote_obs")
        try:
            row = self.conn.execute("DELETE FROM observations WHERE id = ? RETURNING *", (obs_id,)).fetchone()
            if row is None:
                self.conn.execute("RELEASE SAVEPOINT promote_obs")
                raise ValueError(f"Observation not found: {obs_id}")
            obs = dict(row)

            issue_title = title or obs["summary"]
            desc_parts = []
            if extra_description:
                desc_parts.append(extra_description)
            if obs["detail"]:
                desc_parts.append(obs["detail"])
            if obs["file_path"]:
                loc = f"`{obs['file_path']}`"
                if obs["line"] is not None:
                    loc += f":{obs['line']}"
                desc_parts.append(f"Observed in: {loc}")
            if obs.get("source_issue_id"):
                desc_parts.append(f"Observed while working on: {obs['source_issue_id']}")
            description = "\n\n".join(desc_parts)

            # NOTE: create_issue calls conn.commit() internally, which releases
            # all savepoints. We must accept that the savepoint boundary is the
            # DELETE only. Since create_issue() commits, we structure it so the
            # DELETE is inside the savepoint and we log to dismissed_observations
            # as a safety net before attempting create_issue.
            #
            # Safety net: log the observation to dismissed_observations BEFORE
            # attempting issue creation, so if create_issue fails the data is
            # preserved in the audit trail.
            now = _now_iso()
            self.conn.execute(
                "INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) VALUES (?, ?, ?, 'promoted', ?)",
                (obs_id, obs["summary"], actor or obs["actor"], now),
            )

            self.conn.execute("RELEASE SAVEPOINT promote_obs")

            issue = self.create_issue(
                issue_title,
                type=issue_type,
                priority=priority if priority is not None else obs["priority"],
                description=description,
                actor=actor or obs["actor"],
            )

            # Label for measuring pipeline output
            self.add_label(issue.id, "from-observation")

            if obs["file_id"]:
                self.add_file_association(obs["file_id"], issue.id, "mentioned_in")

            self.conn.commit()
            return {"issue": issue}

        except Exception:
            # The savepoint was released at "RELEASE SAVEPOINT promote_obs"
            # above, before calling create_issue. If create_issue (or add_label /
            # add_file_association) raises, the savepoint no longer exists and
            # this ROLLBACK will fail silently. This is expected — the observation
            # DELETE has already been committed as part of the savepoint release,
            # and the safety-net audit trail entry preserves the data.
            try:
                self.conn.execute("ROLLBACK TO SAVEPOINT promote_obs")
                self.conn.execute("RELEASE SAVEPOINT promote_obs")
            except Exception:  # noqa: S110
                pass  # Savepoint already released — see comment above
            raise
