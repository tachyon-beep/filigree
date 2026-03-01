"""PlanningMixin — dependencies, plans, and DAG queries.

Extracted from core.py as part of the module architecture split.
All methods access ``self.conn``, ``self.get_issue()``, etc. via
Python's MRO when composed into ``FiligreeDB``.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import TYPE_CHECKING, Any, TypedDict

from filigree.db_base import DBMixinProtocol, _now_iso
from filigree.types.planning import CriticalPathNode, DependencyRecord, PlanPhase, PlanTree

if TYPE_CHECKING:
    from filigree.core import Issue
    from filigree.templates import TemplateRegistry

logger = logging.getLogger(__name__)


class ProgressDict(TypedDict):
    total: int
    completed: int
    in_progress: int
    open: int
    pct: int


class ChildSummary(TypedDict):
    epics: int
    milestones: int
    tasks: int
    bugs: int
    other: int
    total: int


class IssueRef(TypedDict):
    id: str
    title: str
    type: str


class TreeNode(TypedDict):
    issue: dict[str, Any]
    progress: ProgressDict | None
    children: list[TreeNode]


_MAX_TREE_DEPTH = 10


class PlanningMixin(DBMixinProtocol):
    """Dependencies, plans, and DAG queries (ready/blocked/critical path).

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
        def _get_states_for_category(self, category: str) -> list[str]: ...

        @property
        def templates(self) -> TemplateRegistry: ...

        # From FiligreeDB (not yet extracted to a mixin)
        def _generate_unique_id(self, table: str, infix: str = "") -> str: ...
        def _build_issues_batch(self, issue_ids: list[str]) -> list[Issue]: ...
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
        ) -> list[Issue]: ...

    # -- Dependencies --------------------------------------------------------

    def add_dependency(self, issue_id: str, depends_on_id: str, *, dep_type: str = "blocks", actor: str = "") -> bool:
        # Validate both issues exist
        self.get_issue(issue_id)  # raises KeyError if not found
        self.get_issue(depends_on_id)  # raises KeyError if not found

        if issue_id == depends_on_id:
            msg = f"Cannot add self-dependency: {issue_id}"
            raise ValueError(msg)

        # Check for cycles: would depends_on_id transitively reach issue_id?
        if self._would_create_cycle(issue_id, depends_on_id):
            msg = f"Dependency {issue_id} -> {depends_on_id} would create a cycle"
            raise ValueError(msg)

        now = _now_iso()
        try:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, ?, ?)",
                (issue_id, depends_on_id, dep_type, now),
            )
            if cursor.rowcount == 0:
                return False  # Already exists — no-op, no event
            self._record_event(issue_id, "dependency_added", actor=actor, new_value=f"{dep_type}:{depends_on_id}")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return True

    def _would_create_cycle(self, issue_id: str, depends_on_id: str) -> bool:
        """Check if adding issue_id -> depends_on_id would create a cycle.

        Uses BFS from depends_on_id following existing dependency edges.
        If issue_id is reachable, adding the new edge would close a cycle.
        """
        visited: set[str] = set()
        queue = deque([depends_on_id])
        while queue:
            current = queue.popleft()
            if current == issue_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            # Follow existing dependencies: current depends_on X means current -> X
            for r in self.conn.execute("SELECT depends_on_id FROM dependencies WHERE issue_id = ?", (current,)).fetchall():
                queue.append(r["depends_on_id"])
        return False

    def remove_dependency(self, issue_id: str, depends_on_id: str, *, actor: str = "") -> bool:
        cursor = self.conn.execute(
            "DELETE FROM dependencies WHERE issue_id = ? AND depends_on_id = ?",
            (issue_id, depends_on_id),
        )
        if cursor.rowcount == 0:
            return False  # Nothing to remove
        self._record_event(issue_id, "dependency_removed", actor=actor, old_value=depends_on_id)
        self.conn.commit()
        return True

    def get_all_dependencies(self) -> list[DependencyRecord]:
        rows = self.conn.execute("SELECT issue_id, depends_on_id, type FROM dependencies").fetchall()
        return [{"from": r["issue_id"], "to": r["depends_on_id"], "type": r["type"]} for r in rows]

    # -- Ready / Blocked -----------------------------------------------------

    def _resolve_open_done_states(self) -> tuple[list[str], list[str], str, str]:
        """Return (open_states, done_states, open_placeholders, done_placeholders).

        ``done_states`` falls back to ``["closed"]`` when no templates define done states.
        """
        open_states = self._get_states_for_category("open")
        done_states = self._get_states_for_category("done") or ["closed"]
        open_ph = ",".join("?" * len(open_states))
        done_ph = ",".join("?" * len(done_states))
        return open_states, done_states, open_ph, done_ph

    def get_ready(self) -> list[Issue]:
        """Issues in open-category states with no open blockers."""
        open_states, done_states, open_ph, done_ph = self._resolve_open_done_states()

        if not open_states:
            return []

        rows = self.conn.execute(
            f"SELECT i.id FROM issues i "
            f"WHERE i.status IN ({open_ph}) "
            f"AND NOT EXISTS ("
            f"  SELECT 1 FROM dependencies d "
            f"  JOIN issues blocker ON d.depends_on_id = blocker.id "
            f"  WHERE d.issue_id = i.id AND blocker.status NOT IN ({done_ph})"
            f") ORDER BY i.priority, i.created_at",
            [*open_states, *done_states],
        ).fetchall()

        return self._build_issues_batch([r["id"] for r in rows])

    def get_blocked(self) -> list[Issue]:
        """Issues in open-category states that have at least one non-done blocker."""
        open_states, done_states, open_ph, done_ph = self._resolve_open_done_states()

        if not open_states:
            return []

        rows = self.conn.execute(
            f"SELECT DISTINCT i.id FROM issues i "
            f"JOIN dependencies d ON d.issue_id = i.id "
            f"JOIN issues blocker ON d.depends_on_id = blocker.id "
            f"WHERE i.status IN ({open_ph}) AND blocker.status NOT IN ({done_ph}) "
            f"ORDER BY i.priority, i.created_at",
            [*open_states, *done_states],
        ).fetchall()

        return self._build_issues_batch([r["id"] for r in rows])

    # -- Critical path -------------------------------------------------------

    def get_critical_path(self) -> list[CriticalPathNode]:
        """Compute the longest dependency chain among non-done issues.

        Uses topological-order dynamic programming on the open-issue dependency DAG.
        Returns the chain as a list of {id, title, priority, type} dicts, ordered
        from the root blocker to the final blocked issue.
        """
        done_states = self._get_states_for_category("done")

        done_ph = ",".join("?" * len(done_states)) if done_states else "'__none__'"
        open_rows = self.conn.execute(
            f"SELECT id, title, priority, type FROM issues WHERE status NOT IN ({done_ph})",
            done_states if done_states else [],
        ).fetchall()
        open_ids = {r["id"] for r in open_rows}
        info = {
            r["id"]: CriticalPathNode(id=r["id"], title=r["title"], priority=r["priority"], type=r["type"])
            for r in open_rows
        }

        # edges: blocker -> list of issues it blocks (forward edges)
        forward: dict[str, list[str]] = {nid: [] for nid in open_ids}
        in_degree: dict[str, int] = dict.fromkeys(open_ids, 0)
        dep_rows = self.conn.execute("SELECT issue_id, depends_on_id FROM dependencies").fetchall()
        for dep in dep_rows:
            from_id, to_id = dep["issue_id"], dep["depends_on_id"]
            if from_id in open_ids and to_id in open_ids:
                forward[to_id].append(from_id)  # to_id blocks from_id
                in_degree[from_id] = in_degree.get(from_id, 0) + 1

        if not open_ids:
            return []

        # Topological sort (Kahn's algorithm) + longest path DP
        queue = deque(nid for nid in open_ids if in_degree[nid] == 0)
        dist: dict[str, int] = dict.fromkeys(open_ids, 0)
        pred: dict[str, str | None] = dict.fromkeys(open_ids, None)

        while queue:
            node = queue.popleft()
            for neighbor in forward[node]:
                if dist[node] + 1 > dist[neighbor]:
                    dist[neighbor] = dist[node] + 1
                    pred[neighbor] = node
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if not dist:
            return []

        # Find the node with the longest path
        end_node = max(dist, key=lambda n: dist[n])
        if dist[end_node] == 0:
            return []  # No chains at all

        # Reconstruct path
        path: list[str] = []
        current: str | None = end_node
        while current is not None:
            path.append(current)
            current = pred[current]
        path.reverse()

        return [info[nid] for nid in path]

    # -- Plan tree -----------------------------------------------------------

    def get_plan(self, milestone_id: str) -> PlanTree:
        """Get milestone->phase->step tree with progress stats."""
        milestone = self.get_issue(milestone_id)

        phases = self.list_issues(parent_id=milestone_id)
        phases.sort(key=lambda p: p.fields.get("sequence", 999))

        phase_list: list[PlanPhase] = []
        total_steps = 0
        completed_steps = 0

        for phase in phases:
            steps = self.list_issues(parent_id=phase.id)
            steps.sort(key=lambda s: s.fields.get("sequence", 999))

            completed = sum(1 for s in steps if s.status_category == "done")
            ready = sum(1 for s in steps if s.is_ready)

            phase_list.append(
                PlanPhase(
                    phase=phase.to_dict(),
                    steps=[s.to_dict() for s in steps],
                    total=len(steps),
                    completed=completed,
                    ready=ready,
                )
            )
            total_steps += len(steps)
            completed_steps += completed

        return PlanTree(
            milestone=milestone.to_dict(),
            phases=phase_list,
            total_steps=total_steps,
            completed_steps=completed_steps,
        )

    def create_plan(
        self,
        milestone: dict[str, Any],
        phases: list[dict[str, Any]],
        *,
        actor: str = "",
    ) -> PlanTree:
        """Create a full milestone -> phase -> step hierarchy in one transaction.

        Args:
            milestone: {title, priority?, description?, fields?}
            phases: [{title, priority?, description?, steps: [{title, priority?, description?, deps?: [step_index]}]}]
            actor: Who created the plan

        Step deps use integer indices (0-based within the phase's steps list)
        or cross-phase references as "phase_idx.step_idx" strings.

        Returns the full plan tree (same format as get_plan).
        """
        # Validate inputs — specific error messages for each level
        if not milestone.get("title", "").strip():
            msg = "Milestone 'title' is required and cannot be empty"
            raise ValueError(msg)
        for phase_idx, phase_data in enumerate(phases):
            if not phase_data.get("title", "").strip():
                msg = f"Phase {phase_idx + 1} 'title' is required and cannot be empty"
                raise ValueError(msg)
            for step_idx, step_data in enumerate(phase_data.get("steps", [])):
                if not step_data.get("title", "").strip():
                    msg = f"Phase {phase_idx + 1}, Step {step_idx + 1} 'title' is required and cannot be empty"
                    raise ValueError(msg)

        now = _now_iso()
        milestone_initial = self.templates.get_initial_state("milestone")
        phase_initial = self.templates.get_initial_state("phase")
        step_initial = self.templates.get_initial_state("step")

        try:
            # Create milestone
            ms_id = self._generate_unique_id("issues")
            ms_fields = milestone.get("fields") or {}
            self.conn.execute(
                "INSERT INTO issues (id, title, status, priority, type, parent_id, assignee, "
                "created_at, updated_at, description, notes, fields) "
                "VALUES (?, ?, ?, ?, 'milestone', NULL, '', ?, ?, ?, '', ?)",
                (
                    ms_id,
                    milestone["title"],
                    milestone_initial,
                    milestone.get("priority", 2),
                    now,
                    now,
                    milestone.get("description", ""),
                    json.dumps(ms_fields),
                ),
            )
            self._record_event(ms_id, "created", actor=actor, new_value=milestone["title"])

            # Track all created step IDs for cross-phase dependency resolution
            # step_ids[phase_idx][step_idx] = issue_id
            step_ids: list[list[str]] = []

            for phase_idx, phase_data in enumerate(phases):
                # Create phase
                phase_id = self._generate_unique_id("issues")
                phase_fields = phase_data.get("fields") or {}
                phase_fields["sequence"] = phase_idx + 1
                self.conn.execute(
                    "INSERT INTO issues (id, title, status, priority, type, parent_id, assignee, "
                    "created_at, updated_at, description, notes, fields) "
                    "VALUES (?, ?, ?, ?, 'phase', ?, '', ?, ?, ?, '', ?)",
                    (
                        phase_id,
                        phase_data["title"],
                        phase_initial,
                        phase_data.get("priority", 2),
                        ms_id,
                        now,
                        now,
                        phase_data.get("description", ""),
                        json.dumps(phase_fields),
                    ),
                )
                self._record_event(phase_id, "created", actor=actor, new_value=phase_data["title"])

                # Create steps
                phase_step_ids: list[str] = []
                steps = phase_data.get("steps") or []
                for step_idx, step_data in enumerate(steps):
                    step_id = self._generate_unique_id("issues")
                    step_fields = step_data.get("fields") or {}
                    step_fields["sequence"] = step_idx + 1
                    self.conn.execute(
                        "INSERT INTO issues (id, title, status, priority, type, parent_id, assignee, "
                        "created_at, updated_at, description, notes, fields) "
                        "VALUES (?, ?, ?, ?, 'step', ?, '', ?, ?, ?, '', ?)",
                        (
                            step_id,
                            step_data["title"],
                            step_initial,
                            step_data.get("priority", 2),
                            phase_id,
                            now,
                            now,
                            step_data.get("description", ""),
                            json.dumps(step_fields),
                        ),
                    )
                    self._record_event(step_id, "created", actor=actor, new_value=step_data["title"])
                    phase_step_ids.append(step_id)
                step_ids.append(phase_step_ids)

            # Wire up dependencies after all steps exist
            for phase_idx, phase_data in enumerate(phases):
                steps = phase_data.get("steps") or []
                for step_idx, step_data in enumerate(steps):
                    for dep_ref in step_data.get("deps", []):
                        dep_ref_str = str(dep_ref)
                        if "." in dep_ref_str:
                            # Cross-phase: "phase_idx.step_idx"
                            p_idx_str, s_idx_str = dep_ref_str.split(".", 1)
                            p_idx_int, s_idx_int = int(p_idx_str), int(s_idx_str)
                            if p_idx_int < 0 or s_idx_int < 0:
                                msg = f"Negative dep index not allowed: {dep_ref_str}"
                                raise ValueError(msg)
                            dep_issue_id = step_ids[p_idx_int][s_idx_int]
                        else:
                            # Same phase: step index
                            same_idx = int(dep_ref_str)
                            if same_idx < 0:
                                msg = f"Negative dep index not allowed: {dep_ref_str}"
                                raise ValueError(msg)
                            dep_issue_id = step_ids[phase_idx][same_idx]

                        issue_id = step_ids[phase_idx][step_idx]
                        if issue_id == dep_issue_id:
                            msg = f"Cannot add self-dependency: {issue_id}"
                            raise ValueError(msg)
                        if self._would_create_cycle(issue_id, dep_issue_id):
                            msg = f"Dependency {issue_id} -> {dep_issue_id} would create a cycle"
                            raise ValueError(msg)

                        self.conn.execute(
                            "INSERT OR IGNORE INTO dependencies (issue_id, depends_on_id, type, created_at) VALUES (?, ?, 'blocks', ?)",
                            (issue_id, dep_issue_id, now),
                        )
                        self._record_event(issue_id, "dependency_added", actor=actor, new_value=f"blocks:{dep_issue_id}")

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return self.get_plan(ms_id)

    # -- Release tree --------------------------------------------------------

    def get_releases_summary(self, *, include_released: bool = False) -> list[dict[str, Any]]:
        releases = self.list_issues(type="release")
        if not include_released:
            # Note: rolled_back is category "wip", so it IS included (intentional —
            # a rolled-back release needs attention, it is not "finished")
            releases = [r for r in releases if r.status_category != "done"]

        result: list[dict[str, Any]] = []
        for release in releases:
            # Build the full tree once; extract progress from it
            subtree = self._build_tree(release.id)
            progress = self._progress_from_subtree(subtree)

            child_summary = self._summarize_children_by_type(subtree)

            blocks_resolved = self._resolve_issue_refs(release.blocks)
            blocked_by_resolved = self._resolve_issue_refs(release.blocked_by)

            # Build response dict explicitly — do not spread to_dict() and override keys
            data: dict[str, Any] = dict(release.to_dict())
            data["version"] = release.fields.get("version")
            data["target_date"] = release.fields.get("target_date")
            data["blocks"] = blocks_resolved
            data["blocked_by"] = blocked_by_resolved
            data["progress"] = progress
            data["child_summary"] = child_summary
            result.append(data)

        return result

    def get_release_tree(self, release_id: str) -> dict[str, Any]:
        release = self.get_issue(release_id)  # raises KeyError if not found
        if release.type != "release":
            raise ValueError(f"Issue {release_id} is not a release")
        return {
            "release": release.to_dict(),
            "children": self._build_tree(release.id),
        }

    def _build_tree(self, parent_id: str, *, _depth: int = 0) -> list[TreeNode]:
        if _depth > _MAX_TREE_DEPTH:
            logger.warning("_build_tree: depth limit reached at parent_id=%s", parent_id)
            return []

        children = self.list_issues(parent_id=parent_id)
        nodes: list[TreeNode] = []
        for child in children:
            subtree = self._build_tree(child.id, _depth=_depth + 1)
            progress = self._progress_from_subtree(subtree) if subtree else None
            nodes.append(
                {
                    "issue": dict(child.to_dict()),
                    "progress": progress,
                    "children": subtree,
                }
            )
        # Group nodes: epics/milestones first, then loose items (tasks, bugs, etc.)
        nodes.sort(key=lambda n: 0 if n["issue"].get("type") in ("epic", "milestone") else 1)
        return nodes

    def _progress_from_subtree(self, nodes: list[TreeNode]) -> ProgressDict:
        total = completed = in_progress = open_count = 0
        for node in nodes:
            if not node["children"]:  # leaf node
                cat = node["issue"].get("status_category", "open")
                total += 1
                if cat == "done":
                    completed += 1
                elif cat == "wip":
                    in_progress += 1
                else:
                    open_count += 1
            else:  # recurse into non-leaf's children
                sub = self._progress_from_subtree(node["children"])
                total += sub["total"]
                completed += sub["completed"]
                in_progress += sub["in_progress"]
                open_count += sub["open"]
        pct = round(completed / total * 100) if total > 0 else 0
        return {"total": total, "completed": completed, "in_progress": in_progress, "open": open_count, "pct": pct}

    def _summarize_children_by_type(self, nodes: list[TreeNode]) -> ChildSummary:
        counts: ChildSummary = {"epics": 0, "milestones": 0, "tasks": 0, "bugs": 0, "other": 0, "total": len(nodes)}
        type_map = {"epic": "epics", "milestone": "milestones", "task": "tasks", "bug": "bugs"}
        for node in nodes:
            key = type_map.get(node["issue"]["type"], "other")
            counts[key] += 1  # type: ignore[literal-required]
        return counts

    def _resolve_issue_refs(self, ids: list[str]) -> list[IssueRef]:
        refs: list[IssueRef] = []
        for issue_id in ids:
            try:
                issue = self.get_issue(issue_id)
                refs.append({"id": issue.id, "title": issue.title, "type": issue.type})
            except KeyError:
                logger.warning("_resolve_issue_refs: dangling reference %s", issue_id)
                refs.append({"id": issue_id, "title": "(deleted)", "type": "unknown"})
        return refs
