"""Shared dataclasses for fixture handles used across mcp/cli/workflows tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SeededMCPClient:
    """Wrapper yielded by mcp_client_* fixtures.

    Holds the live client plus any seeded IDs the test cares about.
    Fields are opt-in — a fixture populates the ones relevant to its
    scenario.
    """

    client: Any  # MCP client handle (see existing mcp_db)
    bug_id: str | None = None
    issue_id: str | None = None  # generic single-issue handle
    a_id: str | None = None
    b_id: str | None = None
    issue_a_id: str | None = None  # alias for a_id in same_priority_queue
    issue_b_id: str | None = None
    obs_ids: list[str] = field(default_factory=list)
    open_issue_ids: list[str] = field(default_factory=list)

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        return self.client.call_tool(name, args)

    def list_tools(self) -> Any:
        return self.client.list_tools()


@dataclass
class SeededProject:
    """Wrapper yielded by initialized_project_* fixtures.

    `path` is the project root (parent of `.filigree/`). Seed IDs are
    populated per scenario.
    """

    path: Path
    bug_id: str | None = None
    obs_id: str | None = None
    bug_ids: list[str] = field(default_factory=list)
    obs_ids: list[str] = field(default_factory=list)
