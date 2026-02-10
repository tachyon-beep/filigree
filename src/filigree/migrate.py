"""Migrate from .beads SQLite database to filigree.

One-time migration. Maps beads schema -> filigree schema, preserving IDs,
dependencies, events, labels, and comments. Domain-specific beads columns
(design, acceptance_criteria, estimated_minutes, etc.) go into the JSON
fields bag.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

from filigree.core import FiligreeDB

# Beads columns that map to the filigree `fields` JSON bag.
# Everything not in the core schema gets stuffed into fields.
FIELDS_COLUMNS = [
    "design",
    "acceptance_criteria",
    "estimated_minutes",
    "close_reason",
    "external_ref",
    "mol_type",
    "work_type",
    "quality_score",
    "source_system",
    "event_kind",
    "actor",
    "target",
    "payload",
    "source_repo",
    "await_type",
    "await_id",
    "role_type",
    "rig",
    "spec_id",
    "wisp_type",
    "sender",
]


def migrate_from_beads(beads_db_path: str | Path, tracker: FiligreeDB) -> int:
    """Migrate all non-deleted issues from beads to filigree. Returns count."""
    beads_conn = sqlite3.connect(str(beads_db_path))
    beads_conn.row_factory = sqlite3.Row

    # -- Migrate issues
    rows = beads_conn.execute("SELECT * FROM issues WHERE deleted_at IS NULL").fetchall()
    migrated_ids = {row["id"] for row in rows}

    count = 0
    for row in rows:
        # Build fields bag from beads-specific columns
        fields: dict[str, object] = {}
        for col in FIELDS_COLUMNS:
            try:
                val = row[col]
            except IndexError:
                val = None
            if val is not None and val != "" and val != 0:
                fields[col] = val

        # Also preserve beads metadata if present
        if row["metadata"] and row["metadata"] != "null":
            try:
                meta = json.loads(row["metadata"])
                if meta:
                    fields["_beads_metadata"] = meta
            except (json.JSONDecodeError, TypeError):
                pass

        # Map beads status -> filigree status
        status = row["status"]
        if status not in ("open", "in_progress", "closed"):
            status = "open"  # Default unknown statuses to open

        # Map priority (beads uses 0-4 same as us)
        priority = row["priority"] if row["priority"] is not None else 2
        priority = max(0, min(4, priority))

        # Map type
        issue_type = row["issue_type"] or "task"

        # Map parent relationships: prefer parent_id, fall back to parent_epic
        parent_id: str | None = None
        with contextlib.suppress(IndexError, KeyError):
            parent_id = row["parent_id"] or None
        if parent_id is None:
            with contextlib.suppress(IndexError, KeyError):
                parent_id = row["parent_epic"] or None
        # Only keep parent_id if the referenced issue was also migrated
        if parent_id and parent_id not in migrated_ids:
            parent_id = None

        issue_data = {
            "id": row["id"],
            "title": row["title"] or "(untitled)",
            "status": status,
            "priority": priority,
            "type": issue_type,
            "parent_id": parent_id,
            "assignee": row["assignee"] or "",
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
            "closed_at": row["closed_at"],
            "description": row["description"] or "",
            "notes": row["notes"] or "",
            "fields": fields,
        }

        tracker.bulk_insert_issue(issue_data, validate=False)
        count += 1

    # -- Migrate dependencies (only where both sides were migrated)
    deps = beads_conn.execute("SELECT * FROM dependencies").fetchall()
    for dep in deps:
        if dep["issue_id"] in migrated_ids and dep["depends_on_id"] in migrated_ids:
            tracker.bulk_insert_dependency(
                dep["issue_id"],
                dep["depends_on_id"],
                dep["type"] or "blocks",
            )

    # -- Migrate events (only for migrated issues)
    try:
        events = beads_conn.execute(
            "SELECT issue_id, event_type, actor, old_value, new_value, comment, created_at FROM events"
        ).fetchall()
        for evt in events:
            if evt["issue_id"] in migrated_ids:
                tracker.bulk_insert_event(
                    {
                        "issue_id": evt["issue_id"],
                        "event_type": evt["event_type"] or "unknown",
                        "actor": evt["actor"] or "beads",
                        "old_value": evt["old_value"],
                        "new_value": evt["new_value"],
                        "comment": evt["comment"] or "",
                        "created_at": evt["created_at"] or "",
                    }
                )
    except sqlite3.OperationalError:
        pass  # Events table might not exist in all beads versions

    # -- Migrate labels (only for migrated issues)
    try:
        labels = beads_conn.execute("SELECT issue_id, label FROM labels").fetchall()
        for lbl in labels:
            if lbl["issue_id"] in migrated_ids:
                tracker.conn.execute(
                    "INSERT OR IGNORE INTO labels (issue_id, label) VALUES (?, ?)",
                    (lbl["issue_id"], lbl["label"]),
                )
    except sqlite3.OperationalError:
        pass

    # -- Migrate comments (only for migrated issues)
    try:
        comments = beads_conn.execute("SELECT issue_id, author, text, created_at FROM comments").fetchall()
        for cmt in comments:
            if cmt["issue_id"] in migrated_ids:
                tracker.conn.execute(
                    "INSERT INTO comments (issue_id, author, text, created_at) VALUES (?, ?, ?, ?)",
                    (cmt["issue_id"], cmt["author"] or "", cmt["text"], cmt["created_at"] or ""),
                )
    except sqlite3.OperationalError:
        pass

    tracker.bulk_commit()
    beads_conn.close()
    return count
