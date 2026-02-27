"""FilesMixin — file records, scan findings, associations, and timeline.

Extracted from core.py as part of the module architecture split.
All methods access ``self.conn``, ``self.get_issue()``, etc. via
Python's MRO when composed into ``FiligreeDB``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from filigree.db_base import DBMixinProtocol, _now_iso

if TYPE_CHECKING:
    from filigree.core import FileRecord, Issue, ScanFinding
    from filigree.types.core import PaginatedResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (originally in core.py, only used by file-domain methods)
# ---------------------------------------------------------------------------

VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
VALID_FINDING_STATUSES = frozenset({"open", "acknowledged", "fixed", "false_positive", "unseen_in_latest"})
VALID_ASSOC_TYPES = frozenset({"bug_in", "task_for", "scan_finding", "mentioned_in"})


def _normalize_scan_path(path: str) -> str:
    """Normalize scanner-provided paths for stable file identity."""
    normalized = os.path.normpath(path.replace("\\", "/"))
    return "" if normalized == "." else normalized


class FilesMixin(DBMixinProtocol):
    """File records, scan findings, associations, and timeline.

    Inherits ``DBMixinProtocol`` for type-safe access to shared attributes.
    Actual implementations provided by ``FiligreeDB`` at composition time via MRO.
    """

    # SQL fragment for filtering open (non-terminal) findings.
    _OPEN_FINDINGS_FILTER = "status NOT IN ('fixed', 'false_positive')"
    _OPEN_FINDINGS_FILTER_SF = "sf.status NOT IN ('fixed', 'false_positive')"

    # Severity ordering for SQL sort: lower number = more severe.
    _SEVERITY_ORDER_SQL = (
        "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 WHEN 'info' THEN 4 ELSE 5 END"
    )

    _VALID_FINDING_SORTS = frozenset({"updated_at", "severity"})

    if TYPE_CHECKING:
        # From IssuesMixin
        def _generate_unique_id(self, table: str, infix: str = "") -> str: ...
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

    # -- Build helpers -------------------------------------------------------

    def _build_file_record(self, row: Any) -> FileRecord:
        """Build a FileRecord from a database row."""
        from filigree.core import FileRecord

        meta_raw = row["metadata"]
        meta = json.loads(meta_raw) if meta_raw else {}
        return FileRecord(
            id=row["id"],
            path=row["path"],
            language=row["language"] or "",
            file_type=row["file_type"] or "",
            first_seen=row["first_seen"],
            updated_at=row["updated_at"],
            metadata=meta,
        )

    def _build_scan_finding(self, row: Any) -> ScanFinding:
        """Build a ScanFinding from a database row."""
        from filigree.core import ScanFinding

        meta_raw = row["metadata"]
        try:
            parsed_meta = json.loads(meta_raw) if meta_raw else {}
        except (json.JSONDecodeError, TypeError):
            parsed_meta = {}
        meta = parsed_meta if isinstance(parsed_meta, dict) else {}
        return ScanFinding(
            id=row["id"],
            file_id=row["file_id"],
            severity=row["severity"],
            status=row["status"],
            scan_source=row["scan_source"] or "",
            rule_id=row["rule_id"] or "",
            message=row["message"] or "",
            suggestion=row["suggestion"] or "",
            scan_run_id=row["scan_run_id"] or "",
            line_start=row["line_start"],
            line_end=row["line_end"],
            issue_id=row["issue_id"],
            seen_count=row["seen_count"] or 1,
            first_seen=row["first_seen"],
            updated_at=row["updated_at"],
            last_seen_at=row["last_seen_at"],
            metadata=meta,
        )

    # -- File registration ---------------------------------------------------

    def register_file(
        self,
        path: str,
        *,
        language: str = "",
        file_type: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> FileRecord:
        """Register a file or update it if already registered (upsert by path).

        Path is normalized via ``_normalize_scan_path`` to ensure consistent
        identity regardless of caller (MCP tool, scan ingestion, etc.).

        Returns the FileRecord (created or updated).
        """
        path = _normalize_scan_path(path)
        if not path:
            raise ValueError("File path cannot be empty after normalization")
        now = _now_iso()
        existing = self.conn.execute("SELECT * FROM file_records WHERE path = ?", (path,)).fetchone()

        if existing is not None:
            updates: list[str] = []
            params: list[Any] = []
            # Detect field changes and emit events
            changes: list[tuple[str, str, str]] = []  # (field, old, new)
            if language and language != (existing["language"] or ""):
                updates.append("language = ?")
                params.append(language)
                changes.append(("language", existing["language"] or "", language))
            if file_type and file_type != (existing["file_type"] or ""):
                updates.append("file_type = ?")
                params.append(file_type)
                changes.append(("file_type", existing["file_type"] or "", file_type))
            if metadata:
                old_meta_raw = existing["metadata"] or "{}"
                try:
                    old_meta_parsed = json.loads(old_meta_raw)
                except (json.JSONDecodeError, TypeError):
                    old_meta_parsed = {}
                if old_meta_parsed != metadata:
                    new_meta = json.dumps(metadata)
                    updates.append("metadata = ?")
                    params.append(new_meta)
                    changes.append(("metadata", old_meta_raw, new_meta))
            updates.append("updated_at = ?")
            params.append(now)
            params.append(existing["id"])
            self.conn.execute(
                f"UPDATE file_records SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            for field, old_val, new_val in changes:
                self.conn.execute(
                    "INSERT INTO file_events "
                    "(file_id, event_type, field, old_value, new_value, created_at) "
                    "VALUES (?, 'file_metadata_update', ?, ?, ?, ?)",
                    (existing["id"], field, old_val, new_val, now),
                )
            self.conn.commit()
            return self.get_file(existing["id"])

        file_id = self._generate_unique_id("file_records", "f")
        self.conn.execute(
            "INSERT INTO file_records (id, path, language, file_type, first_seen, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_id, path, language, file_type, now, now, json.dumps(metadata or {})),
        )
        self.conn.commit()
        return self.get_file(file_id)

    def get_file(self, file_id: str) -> FileRecord:
        """Get a file record by ID. Raises KeyError if not found."""
        row = self.conn.execute("SELECT * FROM file_records WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise KeyError(file_id)
        return self._build_file_record(row)

    def get_file_by_path(self, path: str) -> FileRecord | None:
        """Get a file record by path. Returns None if not found."""
        row = self.conn.execute("SELECT * FROM file_records WHERE path = ?", (path,)).fetchone()
        if row is None:
            return None
        return self._build_file_record(row)

    def list_files(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        language: str | None = None,
        path_prefix: str | None = None,
        sort: str = "updated_at",
    ) -> list[FileRecord]:
        """List file records with optional filtering and sorting."""
        clauses: list[str] = []
        params: list[Any] = []

        if language is not None:
            clauses.append("language = ?")
            params.append(language)
        if path_prefix is not None:
            escaped = path_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("path LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        valid_sorts = {"updated_at", "first_seen", "path", "language"}
        sort_col = sort if sort in valid_sorts else "updated_at"
        order = "ASC" if sort_col == "path" else "DESC"

        rows = self.conn.execute(
            f"SELECT * FROM file_records{where} ORDER BY {sort_col} {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [self._build_file_record(r) for r in rows]

    def list_files_paginated(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        language: str | None = None,
        path_prefix: str | None = None,
        min_findings: int | None = None,
        has_severity: str | None = None,
        scan_source: str | None = None,
        sort: str = "updated_at",
        direction: str | None = None,
    ) -> PaginatedResult:
        """List file records with pagination metadata.

        Returns ``{results, total, limit, offset, has_more}``.

        When *min_findings* is provided, only files with at least that many
        open findings are returned (uses a correlated subquery).

        When *has_severity* is provided (e.g. ``"critical"``), only files
        with at least one open finding of that severity are returned.
        """
        # Use "fr" alias throughout so the same WHERE works in both the COUNT
        # and enriched queries without string replacement.
        clauses: list[str] = []
        params: list[Any] = []

        if language is not None:
            clauses.append("fr.language = ?")
            params.append(language)
        if path_prefix is not None:
            escaped = path_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("fr.path LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        if min_findings is not None and min_findings > 0:
            clauses.append(f"(SELECT COUNT(*) FROM scan_findings sf WHERE sf.file_id = fr.id AND {self._OPEN_FINDINGS_FILTER_SF}) >= ?")
            params.append(min_findings)
        if has_severity and has_severity in VALID_SEVERITIES:
            clauses.append(
                "(SELECT COUNT(*) FROM scan_findings sf"
                " WHERE sf.file_id = fr.id"
                f" AND {self._OPEN_FINDINGS_FILTER_SF}"
                " AND sf.severity = ?) > 0"
            )
            params.append(has_severity)
        if scan_source:
            clauses.append("EXISTS (SELECT 1 FROM scan_findings sf WHERE sf.file_id = fr.id AND sf.scan_source = ?)")
            params.append(scan_source)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        total: int = self.conn.execute(
            f"SELECT COUNT(*) FROM file_records fr{where}",
            params,
        ).fetchone()[0]

        valid_sorts = {"updated_at", "first_seen", "path", "language"}
        sort_col = sort if sort in valid_sorts else "updated_at"
        default_order = "ASC" if sort_col == "path" else "DESC"
        order = direction.upper() if direction and direction.upper() in ("ASC", "DESC") else default_order

        _open = self._OPEN_FINDINGS_FILTER_SF
        _sev_cols = " ".join(
            f"(SELECT COUNT(*) FROM scan_findings sf WHERE sf.file_id = fr.id AND {_open} AND sf.severity='{s}') AS cnt_{s},"
            for s in ("critical", "high", "medium", "low", "info")
        )
        enriched_sql = (
            f"SELECT fr.*, "
            f"(SELECT COUNT(*) FROM scan_findings sf"
            f" WHERE sf.file_id = fr.id AND {_open}"
            f") AS open_findings, "
            f"(SELECT COUNT(*) FROM scan_findings sf"
            f" WHERE sf.file_id = fr.id"
            f") AS total_findings, "
            f"{_sev_cols} "
            f"(SELECT COUNT(*) FROM file_associations fa"
            f" WHERE fa.file_id = fr.id"
            f") AS associations_count"
            f" FROM file_records fr{where}"
            f" ORDER BY {sort_col} {order}"
            f" LIMIT ? OFFSET ?"
        )
        rows = self.conn.execute(enriched_sql, [*params, limit, offset]).fetchall()

        results = []
        for r in rows:
            d: dict[str, Any] = dict(self._build_file_record(r).to_dict())
            d["summary"] = {
                "total_findings": r["total_findings"],
                "open_findings": r["open_findings"],
                "critical": r["cnt_critical"],
                "high": r["cnt_high"],
                "medium": r["cnt_medium"],
                "low": r["cnt_low"],
                "info": r["cnt_info"],
            }
            d["associations_count"] = r["associations_count"]
            results.append(d)
        return {
            "results": results,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }

    # -- Scan ingestion ------------------------------------------------------

    def process_scan_results(
        self,
        *,
        scan_source: str,
        findings: list[dict[str, Any]],
        scan_run_id: str = "",
        mark_unseen: bool = False,
        create_issues: bool = False,
    ) -> dict[str, Any]:
        """Ingest scan results: create/update file records and findings.

        Each finding dict must have at minimum: path, rule_id, severity, message.
        Optional: language, line_start, line_end, metadata.

        When *mark_unseen* is ``True``, findings in the same (file, scan_source)
        that are NOT in this batch are set to ``unseen_in_latest`` status.
        Only findings with a non-terminal status are affected (``fixed`` and
        ``false_positive`` are left alone).

        When *create_issues* is ``True``, each finding without an ``issue_id``
        is promoted to a new candidate ``bug`` issue and linked to its file via
        ``file_associations(assoc_type='bug_in')``.

        Returns summary stats including ``new_finding_ids``.
        """
        # Validate all findings upfront before any writes, so a bad entry
        # at index N cannot leave writes from 0..N-1 pending.
        warnings: list[str] = []
        for i, f in enumerate(findings):
            if not isinstance(f, dict):
                raise ValueError(f"findings[{i}] must be a dict, got {type(f).__name__}")
            if "path" not in f:
                raise ValueError(f"findings[{i}] is missing required key 'path'")
            if not isinstance(f["path"], str):
                raise ValueError(f"findings[{i}] path must be a string, got {type(f['path']).__name__}")
            f["path"] = _normalize_scan_path(f["path"])
            if "rule_id" not in f:
                raise ValueError(f"findings[{i}] is missing required key 'rule_id'")
            if "message" not in f:
                raise ValueError(f"findings[{i}] is missing required key 'message'")
            rule_id = f["rule_id"]
            if not isinstance(rule_id, str):
                raise ValueError(f"findings[{i}] rule_id must be a string, got {type(rule_id).__name__}")
            if not rule_id.strip():
                raise ValueError(f"findings[{i}] rule_id must be a non-empty string")
            message = f["message"]
            if not isinstance(message, str):
                raise ValueError(f"findings[{i}] message must be a string, got {type(message).__name__}")
            if not message.strip():
                raise ValueError(f"findings[{i}] message must be a non-empty string")
            severity = f.get("severity", "info")
            if not isinstance(severity, str):
                msg = f"findings[{i}] severity must be a string, got {type(severity).__name__}"
                raise ValueError(msg)
            for ln_field in ("line_start", "line_end"):
                ln_val = f.get(ln_field)
                if ln_val is not None and not isinstance(ln_val, int):
                    raise ValueError(f"findings[{i}] {ln_field} must be an integer or null, got {type(ln_val).__name__}")
            suggestion = f.get("suggestion")
            if suggestion is not None and not isinstance(suggestion, str):
                raise ValueError(f"findings[{i}] suggestion must be a string, got {type(suggestion).__name__}")
            # Normalize: strip whitespace and lowercase
            normalized = severity.strip().lower()
            if normalized in VALID_SEVERITIES:
                f["severity"] = normalized
            else:
                path = f["path"]
                rule_id = f.get("rule_id", "")
                warn_msg = f"Unknown severity {severity!r} for finding at {path} (rule_id={rule_id!r}), mapped to 'info'"
                warnings.append(warn_msg)
                logger.warning(
                    "Severity fallback: %r → 'info' for %s (rule_id=%s, scan_source=%s)",
                    severity,
                    path,
                    rule_id,
                    scan_source,
                )
                f["severity"] = "info"

        now = _now_iso()
        stats: dict[str, Any] = {
            "files_created": 0,
            "files_updated": 0,
            "findings_created": 0,
            "findings_updated": 0,
            "new_finding_ids": [],
            "issues_created": 0,
            "issue_ids": [],
            "warnings": warnings,
        }

        def _priority_for_severity(severity: str) -> int:
            return {
                "critical": 0,
                "high": 1,
                "medium": 2,
                "low": 3,
                "info": 3,
            }.get(severity, 2)

        def _create_issue_for_finding(
            *,
            finding_id: str,
            file_id: str,
            path: str,
            rule_id: str,
            severity: str,
            message: str,
            suggestion: str,
            line_start: int | None,
            line_end: int | None,
        ) -> str:
            summary = message.strip().splitlines()[0].strip() if message and message.strip() else "Scanner finding"
            location = f"{path}:{line_start}" if line_start is not None else path
            title = f"[{scan_source}] {location} {summary}"
            if len(title) > 200:
                title = f"{title[:197]}..."

            description_lines = [
                "Automated finding promoted for triage.",
                "",
                f"- Scanner: `{scan_source}`",
                f"- Rule ID: `{rule_id}`",
                f"- Severity: `{severity}`",
                f"- File: `{path}`",
            ]
            if line_start is not None:
                if line_end is not None and line_end != line_start:
                    description_lines.append(f"- Lines: `{line_start}`-`{line_end}`")
                else:
                    description_lines.append(f"- Line: `{line_start}`")
            description_lines.extend(
                [
                    "",
                    "Message:",
                    message or "(empty)",
                ]
            )
            if suggestion:
                description_lines.extend(["", "Suggested fix:", suggestion])
            description = "\n".join(description_lines)

            issue = self.create_issue(
                title,
                type="bug",
                priority=_priority_for_severity(severity),
                description=description,
                fields={
                    "source": "scan",
                    "scan_source": scan_source,
                    "scan_finding_id": finding_id,
                    "scan_rule_id": rule_id,
                    "scan_severity": severity,
                    "file_id": file_id,
                    "file_path": path,
                    "line_start": line_start,
                    "line_end": line_end,
                },
                labels=["candidate", "scan_finding"],
                actor=f"scanner:{scan_source}",
            )
            self.conn.execute(
                "UPDATE scan_findings SET issue_id = ?, updated_at = ? WHERE id = ?",
                (issue.id, now, finding_id),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO file_associations (file_id, issue_id, assoc_type, created_at) VALUES (?, ?, 'bug_in', ?)",
                (file_id, issue.id, now),
            )
            stats["issues_created"] += 1
            stats["issue_ids"].append(issue.id)
            return issue.id

        # Track which finding IDs were seen, keyed by file_id, for mark_unseen
        seen_finding_ids: dict[str, list[str]] = {}

        try:
            for f in findings:
                severity = f.get("severity", "info")
                path = f["path"]
                language = f.get("language", "")

                # Upsert file record
                existing_file = self.conn.execute("SELECT id FROM file_records WHERE path = ?", (path,)).fetchone()
                if existing_file is not None:
                    file_id = existing_file["id"]
                    update_parts = ["updated_at = ?"]
                    update_params: list[Any] = [now]
                    if language:
                        update_parts.append("language = ?")
                        update_params.append(language)
                    update_params.append(file_id)
                    self.conn.execute(
                        f"UPDATE file_records SET {', '.join(update_parts)} WHERE id = ?",
                        update_params,
                    )
                    stats["files_updated"] += 1
                else:
                    file_id = self._generate_unique_id("file_records", "f")
                    self.conn.execute(
                        "INSERT INTO file_records (id, path, language, first_seen, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (file_id, path, language, now, now),
                    )
                    stats["files_created"] += 1

                # Upsert finding (dedup on file_id + scan_source + rule_id + line_start)
                rule_id = f.get("rule_id", "")
                line_start = f.get("line_start")
                dedup_line = line_start if line_start is not None else -1

                # Suggestion size cap (10,000 chars)
                suggestion = f.get("suggestion", "")
                if len(suggestion) > 10_000:
                    logger.warning(
                        "Suggestion truncated for %s (rule_id=%s): %d chars → 10000",
                        path,
                        rule_id,
                        len(suggestion),
                    )
                    suggestion = suggestion[:10_000] + "\n[truncated]"

                existing_finding = self.conn.execute(
                    "SELECT id, seen_count, scan_run_id, issue_id FROM scan_findings "
                    "WHERE file_id = ? AND scan_source = ? AND rule_id = ? AND coalesce(line_start, -1) = ?",
                    (file_id, scan_source, rule_id, dedup_line),
                ).fetchone()

                if existing_finding is not None:
                    # scan_run_id attribution: keep original if non-empty, allow
                    # late attribution for previously-unattributed findings
                    existing_run_id = existing_finding["scan_run_id"] or ""
                    run_id_update = existing_run_id
                    if scan_run_id and not existing_run_id:
                        run_id_update = scan_run_id

                    self.conn.execute(
                        "UPDATE scan_findings SET message = ?, severity = ?, line_end = ?, "
                        "suggestion = ?, scan_run_id = ?, metadata = ?, "
                        "seen_count = seen_count + 1, updated_at = ?, last_seen_at = ?, "
                        "status = CASE WHEN status IN ('fixed', 'unseen_in_latest') THEN 'open' ELSE status END "
                        "WHERE id = ?",
                        (
                            f.get("message", ""),
                            severity,
                            f.get("line_end"),
                            suggestion,
                            run_id_update,
                            json.dumps(f.get("metadata") or {}),
                            now,
                            now,
                            existing_finding["id"],
                        ),
                    )
                    stats["findings_updated"] += 1
                    seen_finding_ids.setdefault(file_id, []).append(existing_finding["id"])
                    existing_issue_id = existing_finding["issue_id"] or ""
                    if create_issues and not existing_issue_id:
                        _create_issue_for_finding(
                            finding_id=existing_finding["id"],
                            file_id=file_id,
                            path=path,
                            rule_id=rule_id,
                            severity=severity,
                            message=f.get("message", ""),
                            suggestion=suggestion,
                            line_start=line_start,
                            line_end=f.get("line_end"),
                        )
                    elif create_issues and existing_issue_id:
                        self.conn.execute(
                            "INSERT OR IGNORE INTO file_associations"
                            " (file_id, issue_id, assoc_type, created_at)"
                            " VALUES (?, ?, 'bug_in', ?)",
                            (file_id, existing_issue_id, now),
                        )
                else:
                    finding_id = self._generate_unique_id("scan_findings", "sf")
                    self.conn.execute(
                        "INSERT INTO scan_findings "
                        "(id, file_id, scan_source, rule_id, severity, status, message, "
                        "suggestion, scan_run_id, "
                        "line_start, line_end, first_seen, updated_at, last_seen_at, metadata) "
                        "VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            finding_id,
                            file_id,
                            scan_source,
                            rule_id,
                            severity,
                            f.get("message", ""),
                            suggestion,
                            scan_run_id,
                            line_start,
                            f.get("line_end"),
                            now,
                            now,
                            now,
                            json.dumps(f.get("metadata") or {}),
                        ),
                    )
                    stats["findings_created"] += 1
                    stats["new_finding_ids"].append(finding_id)
                    seen_finding_ids.setdefault(file_id, []).append(finding_id)
                    if create_issues:
                        _create_issue_for_finding(
                            finding_id=finding_id,
                            file_id=file_id,
                            path=path,
                            rule_id=rule_id,
                            severity=severity,
                            message=f.get("message", ""),
                            suggestion=suggestion,
                            line_start=line_start,
                            line_end=f.get("line_end"),
                        )

            # Mark unseen findings as unseen_in_latest (atomic per file+source)
            if mark_unseen:
                terminal = ("fixed", "false_positive")
                for fid, fids in seen_finding_ids.items():
                    placeholders = ",".join("?" * len(fids))
                    self.conn.execute(
                        f"UPDATE scan_findings SET status = 'unseen_in_latest', updated_at = ? "
                        f"WHERE file_id = ? AND scan_source = ? "
                        f"AND status NOT IN (?, ?) "
                        f"AND id NOT IN ({placeholders})",
                        [now, fid, scan_source, *terminal, *fids],
                    )

            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        return stats

    def get_scan_runs(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Query scan run history from scan_findings grouped by scan_run_id.

        Returns a list of scan run summaries, ordered by most recent activity.
        Findings with empty scan_run_id are excluded.
        """
        rows = self.conn.execute(
            "SELECT scan_run_id, scan_source, "
            "MIN(first_seen) AS started_at, "
            "MAX(updated_at) AS completed_at, "
            "COUNT(*) AS total_findings, "
            "COUNT(DISTINCT file_id) AS files_scanned "
            "FROM scan_findings "
            "WHERE scan_run_id != '' "
            "GROUP BY scan_run_id, scan_source "
            "ORDER BY MAX(updated_at) DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "scan_run_id": row["scan_run_id"],
                "scan_source": row["scan_source"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "total_findings": row["total_findings"],
                "files_scanned": row["files_scanned"],
            }
            for row in rows
        ]

    def update_finding(
        self,
        file_id: str,
        finding_id: str,
        *,
        status: str | None = None,
        issue_id: str | None = None,
    ) -> ScanFinding:
        """Update finding status and/or linked issue for a specific file finding."""
        row = self.conn.execute(
            "SELECT id FROM scan_findings WHERE id = ? AND file_id = ?",
            (finding_id, file_id),
        ).fetchone()
        if row is None:
            msg = f"Finding not found: {finding_id}"
            raise KeyError(msg)

        updates: list[str] = []
        params: list[Any] = []

        if status is not None:
            if status not in VALID_FINDING_STATUSES:
                valid = ", ".join(sorted(VALID_FINDING_STATUSES))
                msg = f'Invalid finding status "{status}". Must be one of: {valid}'
                raise ValueError(msg)
            updates.append("status = ?")
            params.append(status)

        normalized_issue_id: str | None = None
        if issue_id is not None:
            if not isinstance(issue_id, str):
                msg = "issue_id must be a string when provided"
                raise ValueError(msg)
            normalized_issue_id = issue_id.strip()
            if not normalized_issue_id:
                msg = "issue_id cannot be empty when provided"
                raise ValueError(msg)
            issue = self.conn.execute("SELECT id FROM issues WHERE id = ?", (normalized_issue_id,)).fetchone()
            if issue is None:
                msg = f'Issue not found: "{normalized_issue_id}". Verify the issue exists before linking.'
                raise ValueError(msg)
            updates.append("issue_id = ?")
            params.append(normalized_issue_id)

        if not updates:
            msg = "At least one of status or issue_id must be provided"
            raise ValueError(msg)

        now = _now_iso()
        updates.append("updated_at = ?")
        params.append(now)
        params.extend([finding_id, file_id])

        self.conn.execute(
            f"UPDATE scan_findings SET {', '.join(updates)} WHERE id = ? AND file_id = ?",
            params,
        )

        if normalized_issue_id:
            self.conn.execute(
                "INSERT OR IGNORE INTO file_associations (file_id, issue_id, assoc_type, created_at) VALUES (?, ?, 'bug_in', ?)",
                (file_id, normalized_issue_id, now),
            )

        self.conn.commit()
        updated = self.conn.execute("SELECT * FROM scan_findings WHERE id = ?", (finding_id,)).fetchone()
        if updated is None:
            msg = f"Finding not found after update: {finding_id}"
            raise KeyError(msg)
        return self._build_scan_finding(updated)

    def clean_stale_findings(
        self,
        *,
        days: int = 30,
        scan_source: str | None = None,
        actor: str = "",
    ) -> dict[str, Any]:
        """Move ``unseen_in_latest`` findings older than *days* to ``fixed``.

        Only affects findings whose ``last_seen_at`` (or ``updated_at`` as
        fallback) is older than the cutoff.  Returns stats about what changed.
        """
        from datetime import timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

        clauses = [
            "status = 'unseen_in_latest'",
            "coalesce(last_seen_at, updated_at) < ?",
        ]
        params: list[Any] = [cutoff]

        if scan_source is not None:
            clauses.append("scan_source = ?")
            params.append(scan_source)

        now = _now_iso()
        where = " AND ".join(clauses)
        cursor = self.conn.execute(
            f"UPDATE scan_findings SET status = 'fixed', updated_at = ? WHERE {where}",
            [now, *params],
        )
        self.conn.commit()
        return {"findings_fixed": cursor.rowcount}

    @staticmethod
    def _severity_bucket_sql(open_filter: str) -> str:
        """Build ``SUM(CASE WHEN severity=... AND <open_filter> ...)`` columns for all severities."""
        parts = " ".join(
            f"SUM(CASE WHEN severity='{s}' AND {open_filter} THEN 1 ELSE 0 END) AS {s}," for s in ("critical", "high", "medium", "low")
        )
        return f"{parts} SUM(CASE WHEN severity='info' AND {open_filter} THEN 1 ELSE 0 END) AS info"

    def _findings_where(
        self,
        file_id: str,
        *,
        severity: str | None = None,
        status: str | None = None,
        sort: str = "updated_at",
    ) -> tuple[str, list[Any], str]:
        """Build WHERE clause, params, and ORDER clause for findings queries.

        Returns ``(where, params, order_clause)`` — shared by
        ``get_findings`` and ``get_findings_paginated``.
        """
        if sort not in self._VALID_FINDING_SORTS:
            valid = ", ".join(sorted(self._VALID_FINDING_SORTS))
            raise ValueError(f'Invalid sort field "{sort}". Must be one of: {valid}')

        clauses = ["file_id = ?"]
        params: list[Any] = [file_id]

        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        where = " AND ".join(clauses)
        order_clause = f"{self._SEVERITY_ORDER_SQL} ASC, updated_at DESC" if sort == "severity" else "updated_at DESC"
        return where, params, order_clause

    def get_findings(
        self,
        file_id: str,
        *,
        severity: str | None = None,
        status: str | None = None,
        sort: str = "updated_at",
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScanFinding]:
        """Get scan findings for a file with optional filters."""
        where, params, order_clause = self._findings_where(file_id, severity=severity, status=status, sort=sort)
        rows = self.conn.execute(
            f"SELECT * FROM scan_findings WHERE {where} ORDER BY {order_clause} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [self._build_scan_finding(r) for r in rows]

    def get_findings_paginated(
        self,
        file_id: str,
        *,
        severity: str | None = None,
        status: str | None = None,
        sort: str = "updated_at",
        limit: int = 100,
        offset: int = 0,
    ) -> PaginatedResult:
        """Get scan findings with pagination metadata.

        Returns ``{results, total, limit, offset, has_more}``.
        """
        where, params, _order = self._findings_where(file_id, severity=severity, status=status, sort=sort)

        total: int = self.conn.execute(
            f"SELECT COUNT(*) FROM scan_findings WHERE {where}",
            params,
        ).fetchone()[0]

        findings = self.get_findings(file_id, severity=severity, status=status, sort=sort, limit=limit, offset=offset)
        results: list[dict[str, Any]] = [dict(f.to_dict()) for f in findings]
        return {
            "results": results,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }

    def get_file_findings_summary(self, file_id: str) -> dict[str, Any]:
        """Get a severity-bucketed summary of findings for a file."""
        _open = self._OPEN_FINDINGS_FILTER
        _sev = self._severity_bucket_sql(_open)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS total_findings, "
            f"SUM(CASE WHEN {_open} THEN 1 ELSE 0 END) AS open_findings, "
            f"{_sev} "
            f"FROM scan_findings WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        return {
            "total_findings": row["total_findings"],
            "open_findings": row["open_findings"] or 0,
            "critical": row["critical"] or 0,
            "high": row["high"] or 0,
            "medium": row["medium"] or 0,
            "low": row["low"] or 0,
            "info": row["info"] or 0,
        }

    def get_global_findings_stats(self) -> dict[str, Any]:
        """Get project-wide severity-bucketed findings stats."""
        _open = self._OPEN_FINDINGS_FILTER
        _sev = self._severity_bucket_sql(_open)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS total_findings, "
            f"SUM(CASE WHEN {_open} THEN 1 ELSE 0 END) AS open_findings, "
            f"COUNT(DISTINCT CASE WHEN {_open} THEN file_id END) AS files_with_findings, "
            f"{_sev} "
            f"FROM scan_findings",
        ).fetchone()
        return {
            "total_findings": row["total_findings"],
            "open_findings": row["open_findings"] or 0,
            "files_with_findings": row["files_with_findings"],
            "critical": row["critical"] or 0,
            "high": row["high"] or 0,
            "medium": row["medium"] or 0,
            "low": row["low"] or 0,
            "info": row["info"] or 0,
        }

    def get_file_detail(self, file_id: str) -> dict[str, Any]:
        """Get a structured file detail response with separated data layers."""
        f = self.get_file(file_id)
        associations = self.get_file_associations(file_id)
        recent = self.get_findings(file_id, limit=10)
        summary = self.get_file_findings_summary(file_id)
        return {
            "file": f.to_dict(),
            "associations": associations,
            "recent_findings": [r.to_dict() for r in recent],
            "summary": summary,
        }

    # -- File associations ---------------------------------------------------

    def add_file_association(
        self,
        file_id: str,
        issue_id: str,
        assoc_type: str,
    ) -> None:
        """Link a file to an issue. Idempotent (duplicates ignored)."""
        if assoc_type not in VALID_ASSOC_TYPES:
            msg = f'Invalid assoc_type "{assoc_type}". Must be one of: {", ".join(sorted(VALID_ASSOC_TYPES))}'
            raise ValueError(msg)
        # Validate issue exists before creating the association
        row = self.conn.execute("SELECT 1 FROM issues WHERE id = ?", (issue_id,)).fetchone()
        if row is None:
            msg = f'Issue not found: "{issue_id}". Verify the issue exists before creating an association.'
            raise ValueError(msg)
        now = _now_iso()
        self.conn.execute(
            "INSERT OR IGNORE INTO file_associations (file_id, issue_id, assoc_type, created_at) VALUES (?, ?, ?, ?)",
            (file_id, issue_id, assoc_type, now),
        )
        self.conn.commit()

    def get_file_associations(self, file_id: str) -> list[dict[str, Any]]:
        """Get all issue associations for a file."""
        rows = self.conn.execute(
            "SELECT fa.*, i.title as issue_title, i.status as issue_status "
            "FROM file_associations fa "
            "LEFT JOIN issues i ON fa.issue_id = i.id "
            "WHERE fa.file_id = ? ORDER BY fa.created_at DESC",
            (file_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "file_id": r["file_id"],
                "issue_id": r["issue_id"],
                "assoc_type": r["assoc_type"],
                "created_at": r["created_at"],
                "issue_title": r["issue_title"],
                "issue_status": r["issue_status"],
            }
            for r in rows
        ]

    def get_issue_files(self, issue_id: str) -> list[dict[str, Any]]:
        """Get all files associated with an issue (issue -> files direction)."""
        rows = self.conn.execute(
            "SELECT fa.*, fr.path as file_path, fr.language as file_language "
            "FROM file_associations fa "
            "JOIN file_records fr ON fa.file_id = fr.id "
            "WHERE fa.issue_id = ? ORDER BY fa.created_at DESC",
            (issue_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "file_id": r["file_id"],
                "issue_id": r["issue_id"],
                "assoc_type": r["assoc_type"],
                "created_at": r["created_at"],
                "file_path": r["file_path"],
                "file_language": r["file_language"],
            }
            for r in rows
        ]

    def get_issue_findings(self, issue_id: str) -> list[ScanFinding]:
        """Get all scan findings related to an issue."""
        rows = self.conn.execute(
            "SELECT sf.* FROM scan_findings sf WHERE sf.issue_id = ? "
            "UNION "
            "SELECT sf.* FROM scan_findings sf "
            "JOIN file_associations fa ON sf.file_id = fa.file_id "
            "WHERE fa.issue_id = ? AND fa.assoc_type = 'scan_finding'",
            (issue_id, issue_id),
        ).fetchall()
        return [self._build_scan_finding(r) for r in rows]

    def get_file_hotspots(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Get files ranked by weighted finding severity score."""
        rows = self.conn.execute(
            """
            SELECT
                fr.id, fr.path, fr.language,
                SUM(CASE WHEN sf.severity = 'critical' THEN 1 ELSE 0 END) as cnt_critical,
                SUM(CASE WHEN sf.severity = 'high' THEN 1 ELSE 0 END) as cnt_high,
                SUM(CASE WHEN sf.severity = 'medium' THEN 1 ELSE 0 END) as cnt_medium,
                SUM(CASE WHEN sf.severity = 'low' THEN 1 ELSE 0 END) as cnt_low,
                SUM(CASE WHEN sf.severity = 'info' THEN 1 ELSE 0 END) as cnt_info,
                SUM(
                    CASE sf.severity
                        WHEN 'critical' THEN 10
                        WHEN 'high' THEN 5
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 1
                        ELSE 0
                    END
                ) as score
            FROM file_records fr
            JOIN scan_findings sf ON sf.file_id = fr.id
            WHERE sf.status = 'open'
            GROUP BY fr.id
            HAVING score > 0
            ORDER BY score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [
            {
                "file": {"id": r["id"], "path": r["path"], "language": r["language"]},
                "score": r["score"],
                "findings_breakdown": {
                    "critical": r["cnt_critical"],
                    "high": r["cnt_high"],
                    "medium": r["cnt_medium"],
                    "low": r["cnt_low"],
                    "info": r["cnt_info"],
                },
            }
            for r in rows
        ]

    # -- File Timeline -------------------------------------------------------

    def get_file_timeline(
        self,
        file_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
    ) -> PaginatedResult:
        """Build a merged timeline of events for a file.

        Assembles entries from scan findings and file associations, sorted
        newest-first.  Each entry carries a deterministic ``id`` derived from
        ``sha256(type + timestamp + source_id)[:12]`` so clients can
        cache/deduplicate without server coordination.
        """
        self.get_file(file_id)  # validate existence

        entries: list[dict[str, Any]] = []

        # 1. Finding events (created + status changes inferred from updated_at)
        findings = self.conn.execute(
            "SELECT id, scan_source, rule_id, severity, status, message, "
            "first_seen, updated_at FROM scan_findings WHERE file_id = ? "
            "ORDER BY first_seen DESC",
            (file_id,),
        ).fetchall()
        for f in findings:
            entries.append(
                {
                    "type": "finding_created",
                    "timestamp": f["first_seen"],
                    "source_id": f["id"],
                    "data": {
                        "scan_source": f["scan_source"],
                        "rule_id": f["rule_id"],
                        "severity": f["severity"],
                        "message": f["message"],
                    },
                }
            )
            if f["updated_at"] != f["first_seen"]:
                entries.append(
                    {
                        "type": "finding_updated",
                        "timestamp": f["updated_at"],
                        "source_id": f["id"],
                        "data": {
                            "scan_source": f["scan_source"],
                            "rule_id": f["rule_id"],
                            "severity": f["severity"],
                            "status": f["status"],
                        },
                    }
                )

        # 2. Association events
        assocs = self.conn.execute(
            "SELECT fa.id, fa.issue_id, fa.assoc_type, fa.created_at, "
            "i.title as issue_title "
            "FROM file_associations fa "
            "LEFT JOIN issues i ON fa.issue_id = i.id "
            "WHERE fa.file_id = ? ORDER BY fa.created_at DESC",
            (file_id,),
        ).fetchall()
        for a in assocs:
            entries.append(
                {
                    "type": "association_created",
                    "timestamp": a["created_at"],
                    "source_id": str(a["id"]),
                    "data": {
                        "issue_id": a["issue_id"],
                        "issue_title": a["issue_title"],
                        "assoc_type": a["assoc_type"],
                    },
                }
            )

        # 3. File metadata events
        meta_events = self.conn.execute(
            "SELECT id, field, old_value, new_value, created_at FROM file_events WHERE file_id = ? ORDER BY created_at DESC",
            (file_id,),
        ).fetchall()
        for m in meta_events:
            entries.append(
                {
                    "type": "file_metadata_update",
                    "timestamp": m["created_at"],
                    "source_id": str(m["id"]),
                    "data": {
                        "field": m["field"],
                        "old_value": m["old_value"],
                        "new_value": m["new_value"],
                    },
                }
            )

        # Filter by event type before sorting/paginating
        if event_type == "finding":
            entries = [e for e in entries if e["type"].startswith("finding_")]
        elif event_type == "association":
            entries = [e for e in entries if e["type"].startswith("association_")]
        elif event_type == "file_metadata_update":
            entries = [e for e in entries if e["type"] == "file_metadata_update"]
        elif event_type is not None:
            entries = []  # Unknown filter type -> empty results

        # Add deterministic IDs and sort newest-first
        for entry in entries:
            raw = f"{entry['type']}:{entry['timestamp']}:{entry['source_id']}"
            entry["id"] = hashlib.sha256(raw.encode()).hexdigest()[:12]

        entries.sort(key=lambda e: e["timestamp"], reverse=True)

        total = len(entries)
        page = entries[offset : offset + limit]
        return {
            "results": page,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        }
