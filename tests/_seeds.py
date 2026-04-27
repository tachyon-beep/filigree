"""Shared seed helpers + dataclasses for mcp/cli/workflows fixtures.

Seed functions take a FiligreeDB and populate known-shape scenarios. They
are used by fixtures in tests/mcp/conftest.py and tests/cli/conftest.py so
both surfaces exercise the same seed data — diverging conftests were
previously maintaining near-identical copies of these setups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from filigree.core import FiligreeDB


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
    file_id: str | None = None
    finding_id: str | None = None
    bug_ids: list[str] = field(default_factory=list)
    obs_ids: list[str] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)
    finding_ids: list[str] = field(default_factory=list)


def seed_open_bug(db: FiligreeDB, *, title: str = "Test bug", priority: int = 2) -> str:
    """Seed one open, unclaimed bug. Returns the issue id.

    Shared between MCP and CLI conftests so both surfaces see the same
    scenario shape.
    """
    return db.create_issue(title, type="bug", priority=priority).id


def seed_bugs(db: FiligreeDB, *, count: int = 3, priority: int = 2) -> list[str]:
    """Seed ``count`` open bugs. Returns the list of ids."""
    return [db.create_issue(f"Bug {i}", type="bug", priority=priority).id for i in range(count)]


def seed_observations(db: FiligreeDB, *, count: int = 3, actor: str = "test") -> list[str]:
    """Seed ``count`` observations. Returns the list of observation ids."""
    return [db.create_observation(f"note {i}", actor=actor)["id"] for i in range(count)]


def seed_file(db: FiligreeDB, *, path: str = "src/foo.py", language: str = "python") -> str:
    """Seed one file record. Returns the file id."""
    return db.register_file(path, language=language).id


def seed_finding(
    db: FiligreeDB,
    *,
    file_id: str,
    severity: str = "high",
    scan_source: str = "test-scanner",
    rule_id: str = "test-rule",
    message: str = "Test finding",
) -> str:
    """Seed one scan finding via process_scan_results. Returns the finding id."""
    result = db.process_scan_results(
        scan_source=scan_source,
        findings=[
            {
                "path": db.get_file(file_id).path,
                "rule_id": rule_id,
                "message": message,
                "severity": severity,
            }
        ],
    )
    return result["new_finding_ids"][0]
