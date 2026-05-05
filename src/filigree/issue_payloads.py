"""Public issue projections for agent-facing wire surfaces."""

from __future__ import annotations

from typing import Any

from filigree.models import Issue
from filigree.types.api import PublicIssue


def issue_to_public(issue: Issue) -> PublicIssue:
    """Return the full 2.0 public issue shape with ``issue_id``.

    Keep ``Issue.to_dict()`` internal/classic-shaped; this helper is for MCP,
    CLI JSON, and other agent-facing surfaces that promise ``issue_id``.
    """
    classic = issue.to_dict()
    return PublicIssue(
        issue_id=classic["id"],
        title=classic["title"],
        status=classic["status"],
        status_category=classic["status_category"],
        priority=classic["priority"],
        type=classic["type"],
        parent_id=classic["parent_id"],
        assignee=classic["assignee"],
        created_at=classic["created_at"],
        updated_at=classic["updated_at"],
        closed_at=classic["closed_at"],
        description=classic["description"],
        notes=classic["notes"],
        fields=classic["fields"],
        labels=classic["labels"],
        blocks=classic["blocks"],
        blocked_by=classic["blocked_by"],
        is_ready=classic["is_ready"],
        children=classic["children"],
        data_warnings=classic["data_warnings"],
    )


def public_issue_with(issue: Issue, **extra: Any) -> dict[str, Any]:
    """Return public issue payload plus response-specific extension keys."""
    payload: dict[str, Any] = dict(issue_to_public(issue))
    payload.update(extra)
    return payload
