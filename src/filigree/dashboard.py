"""Web dashboard for filigree — read-only project visualization.

Single-command local web server: kanban board, dependency graph, issue details.
No build step, all client libraries loaded from CDN.

Usage:
    filigree dashboard                    # Opens browser at localhost:8377
    filigree dashboard --port 9000        # Custom port
    filigree dashboard --no-browser       # Skip auto-open
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Any

from filigree.core import DB_FILENAME, FiligreeDB, find_filigree_root, read_config

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8377

_db: FiligreeDB | None = None
_prefix: str = "filigree"


def _get_db() -> FiligreeDB:
    if _db is None:
        msg = "Database not initialized"
        raise RuntimeError(msg)
    return _db


def create_app() -> Any:
    """Create the FastAPI application with all dashboard endpoints."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="Filigree Dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (STATIC_DIR / "dashboard.html").read_text()
        return HTMLResponse(html)

    @app.get("/api/issues")
    async def api_issues() -> JSONResponse:
        db = _get_db()
        issues = db.list_issues(limit=10000)
        return JSONResponse([i.to_dict() for i in issues])

    @app.get("/api/graph")
    async def api_graph() -> JSONResponse:
        """Graph data: nodes (issues) + edges (dependencies) for Cytoscape.js."""
        db = _get_db()
        issues = db.list_issues(limit=10000)
        deps = db.get_all_dependencies()
        nodes = [
            {
                "id": i.id,
                "title": i.title,
                "status": i.status,
                "status_category": i.status_category,
                "priority": i.priority,
                "type": i.type,
            }
            for i in issues
        ]
        edges = [{"source": d["to"], "target": d["from"]} for d in deps]
        return JSONResponse({"nodes": nodes, "edges": edges})

    @app.get("/api/stats")
    async def api_stats() -> JSONResponse:
        db = _get_db()
        stats = db.get_stats()
        stats["prefix"] = _prefix
        return JSONResponse(stats)

    @app.get("/api/issue/{issue_id}")
    async def api_issue_detail(issue_id: str) -> JSONResponse:
        """Full issue detail with dependency details, events, and comments."""
        db = _get_db()
        try:
            issue = db.get_issue(issue_id)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)

        data = issue.to_dict()

        # Resolve dep details for blocks and blocked_by
        dep_ids = set(issue.blocks + issue.blocked_by)
        dep_details: dict[str, dict[str, Any]] = {}
        for did in dep_ids:
            try:
                dep = db.get_issue(did)
                dep_details[did] = {
                    "title": dep.title,
                    "status": dep.status,
                    "status_category": dep.status_category,
                    "priority": dep.priority,
                }
            except KeyError:
                dep_details[did] = {"title": did, "status": "unknown", "status_category": "open", "priority": 2}
        data["dep_details"] = dep_details

        # Events
        events = db.conn.execute(
            "SELECT event_type, actor, old_value, new_value, created_at "
            "FROM events WHERE issue_id = ? ORDER BY created_at DESC LIMIT 20",
            (issue_id,),
        ).fetchall()
        data["events"] = [dict(e) for e in events]

        # Comments
        data["comments"] = db.get_comments(issue_id)

        return JSONResponse(data)

    @app.get("/api/dependencies")
    async def api_dependencies() -> JSONResponse:
        db = _get_db()
        deps = db.get_all_dependencies()
        return JSONResponse(deps)

    @app.get("/api/type/{type_name}")
    async def api_type_template(type_name: str) -> JSONResponse:
        """Workflow template for a given issue type (WFT-FR-065)."""
        db = _get_db()
        tpl = db.templates.get_type(type_name)
        if tpl is None:
            return JSONResponse({"error": f"Unknown type: {type_name}"}, status_code=404)
        return JSONResponse(
            {
                "type": tpl.type,
                "display_name": tpl.display_name,
                "states": [{"name": s.name, "category": s.category} for s in tpl.states],
                "initial_state": tpl.initial_state,
                "transitions": [
                    {"from": t.from_state, "to": t.to_state, "enforcement": t.enforcement} for t in tpl.transitions
                ],
            }
        )

    return app


def main(port: int = DEFAULT_PORT, *, no_browser: bool = False) -> None:
    """Start the dashboard server."""
    import threading

    import uvicorn

    global _db, _prefix

    filigree_dir = find_filigree_root()
    config = read_config(filigree_dir)
    _prefix = config.get("prefix", "filigree")
    _db = FiligreeDB(filigree_dir / DB_FILENAME, prefix=_prefix)
    _db.initialize()

    app = create_app()

    if not no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    print(f"Filigree Dashboard: http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
