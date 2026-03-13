"""Mixin for observation (agent scratchpad) operations.

Observations are lightweight, disposable candidates — not issues.
They live in their own table and are promoted to issues or dismissed.

Includes:
- 14-day TTL with piggyback sweep on reads (in savepoint)
- Dismissal audit trail via dismissed_observations table
- Promotion to issue (best-effort cleanup — issue creation commits
  independently, so observation deletion is non-atomic but safe:
  orphaned observations are swept on TTL expiry)
- Age stats for session context prompting
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import cast

from filigree.db_base import DBMixinProtocol, _now_iso
from filigree.db_files import _normalize_scan_path
from filigree.types.core import BatchDismissResult, ISOTimestamp, ObservationDict, ObservationStatsDict, PromoteObservationResult

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 14
STALE_THRESHOLD_HOURS = 48


def _alive_clause(sweep: bool, now_iso: str) -> tuple[str, tuple[str, ...]]:
    """Return (SQL WHERE fragment, params) to filter out expired observations.

    When sweep=True (expired rows already deleted), returns empty filter.
    When sweep=False, adds ``expires_at > ?`` to exclude expired rows.
    """
    if sweep:
        return "", ()
    return " AND expires_at > ?", (now_iso,)


DISMISSED_AUDIT_TRAIL_CAP = 10_000


def _expires_iso(ttl_days: int = DEFAULT_TTL_DAYS) -> str:
    """Compute expiry timestamp using same isoformat() as _now_iso for consistent text comparison."""
    return (datetime.now(UTC) + timedelta(days=ttl_days)).isoformat()


class ObservationsMixin(DBMixinProtocol):
    """Observation CRUD — agent scratchpad for things noticed in passing.

    Declares ``DBMixinProtocol`` as a base for type-safe access to shared
    attributes. The Protocol provides method stubs for static analysis;
    actual implementations are provided by ``FiligreeDB`` at composition
    time via MRO.
    """

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
            # Prune dismissed_observations audit trail to prevent unbounded growth.
            # Keep the most recent DISMISSED_AUDIT_TRAIL_CAP entries. Only enforced
            # during sweep (not dismiss/promote) — acceptable for v1 scope. Without
            # an index on dismissed_at, this is O(N log N) for large tables.
            self.conn.execute(
                "DELETE FROM dismissed_observations WHERE id NOT IN "
                "(SELECT id FROM dismissed_observations ORDER BY dismissed_at DESC LIMIT ?)",
                (DISMISSED_AUDIT_TRAIL_CAP,),
            )
            self.conn.execute("RELEASE SAVEPOINT sweep_obs")
            if cursor.rowcount > 0:
                logger.debug("Swept %d expired observations", cursor.rowcount)
            return cursor.rowcount
        except sqlite3.OperationalError:
            # Suppress transient errors (DB locked, busy) — sweep is best-effort.
            logger.warning("Observation sweep failed (transient), rolled back", exc_info=True)
            try:
                self.conn.execute("ROLLBACK TO SAVEPOINT sweep_obs")
            finally:
                try:
                    self.conn.execute("RELEASE SAVEPOINT sweep_obs")
                except sqlite3.Error:
                    logger.warning("Failed to release savepoint after sweep rollback", exc_info=True)
            return 0  # Sweep is best-effort — don't block reads

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
    ) -> ObservationDict:
        """Create an observation (agent scratchpad note).

        If an observation with the same (summary, file_path, line) already
        exists and is still alive, returns the existing observation unchanged.
        If the duplicate has expired, replaces it by deleting the old and
        inserting the new in one transaction (so no gap exists between delete
        and insert where a concurrent caller could claim the dedup slot).
        """
        if not summary or not summary.strip():
            raise ValueError("Observation summary cannot be empty")
        if not (0 <= priority <= 4):
            raise ValueError(f"priority must be between 0 and 4, got {priority}")
        if line is not None and line < 0:
            raise ValueError(f"line must be >= 0, got {line}")

        file_id: str | None = None
        if file_path:
            file_path = _normalize_scan_path(file_path)
            fr = self.register_file(file_path)
            file_id = fr.id

        now = _now_iso()
        summary_stripped = summary.strip()
        line_cmp = line if line is not None else -1

        # Check for existing duplicate (dedup key: summary + file_path + line).
        # If the duplicate is expired, delete it so re-creation succeeds —
        # otherwise the SELECT match would cause us to skip the new INSERT.
        existing = self.conn.execute(
            "SELECT * FROM observations WHERE summary = ? AND file_path = ? AND coalesce(line, -1) = ?",
            (summary_stripped, file_path, line_cmp),
        ).fetchone()
        if existing:
            if existing["expires_at"] <= now:
                # Expired duplicate — delete and fall through to insert.
                # Both operations share a single transaction to avoid a TOCTOU
                # window where a concurrent caller could insert the same dedup key
                # between the delete-commit and the insert-commit.
                self.conn.execute(
                    "INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) "
                    "VALUES (?, ?, 'system', 'expired (replaced)', ?)",
                    (existing["id"], existing["summary"], now),
                )
                self.conn.execute("DELETE FROM observations WHERE id = ?", (existing["id"],))
                # No commit here — fall through to INSERT below so both
                # the deletion and insertion are committed atomically.
            else:
                # Live duplicate — return existing row
                return cast(ObservationDict, dict(existing))

        obs_id = self._generate_unique_id("observations", "obs")
        expires = _expires_iso()
        try:
            self.conn.execute(
                "INSERT INTO observations (id, summary, detail, file_id, file_path, line, "
                "source_issue_id, priority, actor, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (obs_id, summary_stripped, detail, file_id, file_path, line, source_issue_id, priority, actor, now, expires),
            )
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            raise
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
            "expires_at": ISOTimestamp(expires),
        }

    def list_observations(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        file_path: str = "",
        file_id: str = "",
    ) -> list[ObservationDict]:
        """List pending observations with optional filtering.

        Sweeps expired observations first (best-effort, in savepoint).
        ``file_path`` filtering uses substring matching (LIKE), not exact match.
        ``file_id`` filtering uses exact FK match (more precise than path LIKE).
        """
        self._sweep_expired_observations()
        if file_id:
            # Direct FK query — more precise than path LIKE.
            rows = self.conn.execute(
                "SELECT * FROM observations WHERE file_id = ? ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?",
                (file_id, limit, offset),
            ).fetchall()
        elif file_path:
            file_path = _normalize_scan_path(file_path) or file_path
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
        return [cast(ObservationDict, dict(row)) for row in rows]

    def observation_count(self) -> int:
        """Return total observation count WITHOUT sweeping expired rows.

        This is intentionally a raw count. It may include expired observations
        that have not yet been cleaned up. Use ``list_observations()`` for a
        sweep-then-read pattern, or call ``_sweep_expired_observations()``
        explicitly if an accurate count is needed.
        """
        row = self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()
        return int(row[0])

    def observation_stats(self, *, sweep: bool = True) -> ObservationStatsDict:
        """Return observation count + age stats for session context prompting.

        Args:
            sweep: If True (default), sweep expired observations first.
                   Pass False when calling from read-only context paths
                   (summary generation, MCP prompt) to avoid write side effects.
                   When False, expired rows are excluded via WHERE filter
                   to keep counts consistent with what list_observations returns.
        """
        if sweep:
            self._sweep_expired_observations()

        now = datetime.now(UTC)
        now_iso = now.isoformat()
        alive_frag, alive_params = _alive_clause(sweep, now_iso)

        # alive_where is the standalone form (WHERE ...) for queries without a
        # pre-existing WHERE clause. alive_frag (from _alive_clause) is the
        # AND-suffix form for queries that already have a WHERE. Both filter
        # the same condition (exclude expired rows); keep them in sync.
        alive_where = " WHERE expires_at > ?" if not sweep else ""
        count = self.conn.execute(f"SELECT COUNT(*) FROM observations{alive_where}", alive_params).fetchone()[0]
        if count == 0:
            return {"count": 0, "stale_count": 0, "oldest_hours": 0, "expiring_soon_count": 0}

        stale_cutoff = (now - timedelta(hours=STALE_THRESHOLD_HOURS)).isoformat()
        expiring_cutoff = (now + timedelta(hours=24)).isoformat()

        stale = self.conn.execute(
            f"SELECT COUNT(*) FROM observations WHERE created_at <= ?{alive_frag}",
            (stale_cutoff, *alive_params),
        ).fetchone()[0]

        expiring = self.conn.execute(
            f"SELECT COUNT(*) FROM observations WHERE expires_at <= ?{alive_frag}",
            (expiring_cutoff, *alive_params),
        ).fetchone()[0]

        oldest_row = self.conn.execute(f"SELECT MIN(created_at) FROM observations{alive_where}", alive_params).fetchone()
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
        try:
            self.conn.execute(
                "INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) VALUES (?, ?, ?, ?, ?)",
                (obs_id, row["summary"], actor, reason, now),
            )
            self.conn.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            raise

    def batch_dismiss_observations(
        self,
        obs_ids: list[str],
        *,
        actor: str = "",
        reason: str = "",
    ) -> BatchDismissResult:
        if not obs_ids:
            return {"dismissed": 0, "not_found": []}
        # Deduplicate in Python to avoid relying on SQL IN dedup behavior
        unique_ids = list(dict.fromkeys(obs_ids))
        now = _now_iso()
        placeholders = ",".join("?" for _ in unique_ids)
        try:
            # Find which IDs actually exist before deleting
            found_rows = self.conn.execute(
                f"SELECT id FROM observations WHERE id IN ({placeholders})",
                unique_ids,
            ).fetchall()
            found_ids = {row["id"] for row in found_rows}
            not_found = [oid for oid in unique_ids if oid not in found_ids]

            self.conn.execute(
                f"INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) "
                f"SELECT id, summary, ?, ?, ? FROM observations WHERE id IN ({placeholders})",
                [actor, reason, now, *unique_ids],
            )
            cursor = self.conn.execute(
                f"DELETE FROM observations WHERE id IN ({placeholders})",
                unique_ids,
            )
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            raise
        return {"dismissed": cursor.rowcount, "not_found": not_found}

    def promote_observation(
        self,
        obs_id: str,
        *,
        issue_type: str = "task",
        priority: int | None = None,
        title: str | None = None,
        extra_description: str = "",
        actor: str = "",
    ) -> PromoteObservationResult:
        # 1. Read observation (don't delete yet)
        row = self.conn.execute("SELECT * FROM observations WHERE id = ?", (obs_id,)).fetchone()
        if row is None:
            raise ValueError(f"Observation not found: {obs_id}")
        obs = dict(row)

        # Reject expired observations — consistent with TTL enforcement elsewhere.
        if obs["expires_at"] <= _now_iso():
            raise ValueError(f"Observation {obs_id} has expired and cannot be promoted")

        # 2. Build issue fields
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

        # 3. Create issue first — if this fails, observation is untouched
        issue = self.create_issue(
            issue_title,
            type=issue_type,
            priority=priority if priority is not None else obs["priority"],
            description=description,
            actor=actor or obs["actor"],
        )

        # 4. Issue created successfully — now delete observation and write audit trail.
        #    Best-effort: if this fails, the issue still exists and the observation
        #    will be swept on TTL expiry. Log and continue rather than raising,
        #    because the caller must know the issue was created.
        warnings: list[str] = []
        now = _now_iso()
        try:
            self.conn.execute(
                "INSERT INTO dismissed_observations (obs_id, summary, actor, reason, dismissed_at) VALUES (?, ?, ?, 'promoted', ?)",
                (obs_id, obs["summary"], actor or obs["actor"], now),
            )
            cursor = self.conn.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
            self.conn.commit()
            if cursor.rowcount == 0:
                # Observation was swept by a concurrent TTL cleanup between our
                # SELECT and this DELETE.  The issue is already created, so this
                # is harmless — but surface a warning so callers know.
                msg = f"Observation {obs_id} was already swept before cleanup (issue {issue.id} created successfully)"
                logger.info(msg)
                warnings.append(msg)
        except sqlite3.Error:
            self.conn.rollback()
            msg = f"Failed to clean up observation {obs_id} after promotion (issue {issue.id} created)"
            logger.warning(msg, exc_info=True)
            warnings.append(msg)

        # 5. Enrichments (non-critical — failure should not undo the promotion)
        try:
            self.add_label(issue.id, "from-observation")
        except (sqlite3.Error, ValueError):
            msg = f"Failed to add from-observation label to {issue.id}"
            logger.warning(msg, exc_info=True)
            warnings.append(msg)

        try:
            if obs["file_id"]:
                self.add_file_association(obs["file_id"], issue.id, "mentioned_in")
        except (sqlite3.Error, ValueError):
            msg = f"Failed to add file association for promoted observation {obs_id}"
            logger.warning(msg, exc_info=True)
            warnings.append(msg)

        result: PromoteObservationResult = {"issue": issue}
        if warnings:
            result["warnings"] = warnings
        return result
