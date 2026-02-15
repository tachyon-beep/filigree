# Dashboard Feature Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the filigree web dashboard from a read-only viewer into a full interactive supervisor tool with 30 features across 3 phases.

**Architecture:** All new API endpoints are added to `src/filigree/dashboard.py` inside the existing `create_app()` function. All frontend changes go into the single `src/filigree/static/dashboard.html` file. Backend methods already exist in `core.py` and `analytics.py` — this plan wires them to HTTP and renders results. Tests use the existing `httpx` async client pattern from `tests/test_dashboard.py`.

**Tech Stack:** FastAPI (backend), vanilla JS + Tailwind CSS (frontend), Cytoscape.js (graphs), httpx (tests). No new dependencies.

**Important patterns:**
- Dashboard tests use `ASGITransport` + `AsyncClient` from httpx
- The `populated_db` fixture gives you 4 issues: Epic E, A (open, P1, child of E, blocked by B), B (open, P2), C (closed, P3), plus a dep A→B and a comment on B
- Backend `_get_db()` returns the module-level `_db` singleton
- All write methods accept `actor: str = ""` for audit trail — dashboard endpoints default to `"dashboard"`
- All issue mutations return `Issue` objects — call `.to_dict()` for JSON serialization

---

## Task 1: Write API Endpoints — PATCH, Transitions, Close, Reopen (R1, R5, R17)

These are the core write endpoints that break the read-only barrier.

**Files:**
- Modify: `src/filigree/dashboard.py:34-144` (inside `create_app()`)
- Test: `tests/test_dashboard.py`

**Step 1: Write failing tests for the transitions endpoint**

Add to `tests/test_dashboard.py`:

```python
class TestTransitionsAPI:
    async def test_get_transitions(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.get(f"/api/issue/{ids['b']}/transitions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(t["to"] == "in_progress" for t in data)
        for t in data:
            assert "to" in t
            assert "category" in t
            assert "ready" in t

    async def test_transitions_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/issue/nonexistent/transitions")
        assert resp.status_code == 404
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/john/filigree && python -m pytest tests/test_dashboard.py::TestTransitionsAPI -v`
Expected: FAIL — 404 because the route doesn't exist yet.

**Step 3: Write failing tests for PATCH update endpoint**

Add to `tests/test_dashboard.py`:

```python
class TestUpdateAPI:
    async def test_patch_priority(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(f"/api/issue/{ids['b']}", json={"priority": 0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["priority"] == 0

    async def test_patch_assignee(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(f"/api/issue/{ids['b']}", json={"assignee": "agent-1"})
        assert resp.status_code == 200
        assert resp.json()["assignee"] == "agent-1"

    async def test_patch_status(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(f"/api/issue/{ids['b']}", json={"status": "in_progress"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    async def test_patch_invalid_status(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.patch(f"/api/issue/{ids['b']}", json={"status": "nonexistent"})
        assert resp.status_code == 409

    async def test_patch_not_found(self, client: AsyncClient) -> None:
        resp = await client.patch("/api/issue/nonexistent", json={"priority": 0})
        assert resp.status_code == 404
```

**Step 4: Write failing tests for close/reopen endpoints**

Add to `tests/test_dashboard.py`:

```python
class TestCloseReopenAPI:
    async def test_close_issue(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(f"/api/issue/{ids['b']}/close", json={"reason": "done"})
        assert resp.status_code == 200
        assert resp.json()["status_category"] == "done"

    async def test_close_already_closed(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(f"/api/issue/{ids['c']}/close", json={})
        assert resp.status_code == 409

    async def test_reopen_issue(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(f"/api/issue/{ids['c']}/reopen", json={})
        assert resp.status_code == 200
        assert resp.json()["status_category"] == "open"

    async def test_reopen_not_closed(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(f"/api/issue/{ids['b']}/reopen", json={})
        assert resp.status_code == 409

    async def test_close_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issue/nonexistent/close", json={})
        assert resp.status_code == 404
```

**Step 5: Run all new tests to verify they fail**

Run: `cd /home/john/filigree && python -m pytest tests/test_dashboard.py::TestTransitionsAPI tests/test_dashboard.py::TestUpdateAPI tests/test_dashboard.py::TestCloseReopenAPI -v`
Expected: All FAIL.

**Step 6: Implement the endpoints in dashboard.py**

Add these endpoints inside `create_app()` in `src/filigree/dashboard.py`, before `return app` (line 144):

```python
    @app.get("/api/issue/{issue_id}/transitions")
    async def api_transitions(issue_id: str) -> JSONResponse:
        db = _get_db()
        try:
            transitions = db.get_valid_transitions(issue_id)
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        return JSONResponse([
            {
                "to": t.to,
                "category": t.category,
                "enforcement": t.enforcement,
                "ready": t.ready,
                "missing_fields": list(t.missing_fields),
                "requires_fields": list(t.requires_fields),
            }
            for t in transitions
        ])

    @app.patch("/api/issue/{issue_id}")
    async def api_update_issue(issue_id: str, request: Request) -> JSONResponse:
        db = _get_db()
        body = await request.json()
        try:
            issue = db.update_issue(
                issue_id,
                title=body.get("title"),
                status=body.get("status"),
                priority=body.get("priority"),
                assignee=body.get("assignee"),
                description=body.get("description"),
                notes=body.get("notes"),
                fields=body.get("fields"),
                actor=body.get("actor", "dashboard"),
            )
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @app.post("/api/issue/{issue_id}/close")
    async def api_close_issue(issue_id: str, request: Request) -> JSONResponse:
        db = _get_db()
        body = await request.json()
        try:
            issue = db.close_issue(
                issue_id,
                reason=body.get("reason", ""),
                actor=body.get("actor", "dashboard"),
            )
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())

    @app.post("/api/issue/{issue_id}/reopen")
    async def api_reopen_issue(issue_id: str, request: Request) -> JSONResponse:
        db = _get_db()
        body = await request.json()
        try:
            issue = db.reopen_issue(
                issue_id,
                actor=body.get("actor", "dashboard"),
            )
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(issue.to_dict())
```

Also add `Request` to the imports: change line 37 from:
```python
    from fastapi.responses import HTMLResponse, JSONResponse
```
to:
```python
    from fastapi import Request
    from fastapi.responses import HTMLResponse, JSONResponse
```

**Step 7: Run tests to verify they pass**

Run: `cd /home/john/filigree && python -m pytest tests/test_dashboard.py -v`
Expected: All PASS.

**Step 8: Commit**

```bash
git add tests/test_dashboard.py src/filigree/dashboard.py
git commit -m "feat(dashboard): add PATCH update, transitions, close, reopen API endpoints (R1, R5, R17)"
```

---

## Task 2: Write API Endpoints — Comments, Search, Metrics, Critical Path (R3, R6, R7, R10)

**Files:**
- Modify: `src/filigree/dashboard.py` (inside `create_app()`)
- Test: `tests/test_dashboard.py`

**Step 1: Write failing tests**

Add to `tests/test_dashboard.py`:

```python
class TestCommentAPI:
    async def test_add_comment(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            f"/api/issue/{ids['a']}/comments",
            json={"text": "New comment", "author": "dashboard-user"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["text"] == "New comment"
        assert data["author"] == "dashboard-user"

    async def test_add_comment_empty_text(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(f"/api/issue/{ids['a']}/comments", json={"text": ""})
        assert resp.status_code == 400

    async def test_add_comment_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issue/nonexistent/comments", json={"text": "hello"})
        assert resp.status_code == 404


class TestSearchAPI:
    async def test_search(self, client: AsyncClient) -> None:
        resp = await client.get("/api/search?q=Issue")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) >= 2

    async def test_search_empty_query(self, client: AsyncClient) -> None:
        resp = await client.get("/api/search?q=")
        assert resp.status_code == 200

    async def test_search_no_results(self, client: AsyncClient) -> None:
        resp = await client.get("/api/search?q=zzzznonexistent")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 0


class TestMetricsAPI:
    async def test_metrics(self, client: AsyncClient) -> None:
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "period_days" in data
        assert "throughput" in data
        assert "avg_cycle_time_hours" in data
        assert "avg_lead_time_hours" in data
        assert "by_type" in data

    async def test_metrics_custom_days(self, client: AsyncClient) -> None:
        resp = await client.get("/api/metrics?days=7")
        assert resp.status_code == 200
        assert resp.json()["period_days"] == 7


class TestCriticalPathAPI:
    async def test_critical_path(self, client: AsyncClient) -> None:
        resp = await client.get("/api/critical-path")
        assert resp.status_code == 200
        data = resp.json()
        assert "path" in data
        assert "length" in data
        assert isinstance(data["path"], list)
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/john/filigree && python -m pytest tests/test_dashboard.py::TestCommentAPI tests/test_dashboard.py::TestSearchAPI tests/test_dashboard.py::TestMetricsAPI tests/test_dashboard.py::TestCriticalPathAPI -v`
Expected: All FAIL.

**Step 3: Implement the endpoints**

Add inside `create_app()` in `src/filigree/dashboard.py`:

```python
    @app.post("/api/issue/{issue_id}/comments", status_code=201)
    async def api_add_comment(issue_id: str, request: Request) -> JSONResponse:
        db = _get_db()
        body = await request.json()
        text = body.get("text", "")
        author = body.get("author", "dashboard")
        try:
            db.get_issue(issue_id)  # Verify issue exists
        except KeyError:
            return JSONResponse({"error": f"Not found: {issue_id}"}, status_code=404)
        try:
            comment_id = db.add_comment(issue_id, text, author=author)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(
            {"id": comment_id, "author": author, "text": text, "created_at": ""},
            status_code=201,
        )

    @app.get("/api/search")
    async def api_search(q: str = "", limit: int = 50, offset: int = 0) -> JSONResponse:
        db = _get_db()
        if not q.strip():
            return JSONResponse({"results": [], "total": 0})
        issues = db.search_issues(q, limit=limit, offset=offset)
        return JSONResponse({
            "results": [i.to_dict() for i in issues],
            "total": len(issues),
        })

    @app.get("/api/metrics")
    async def api_metrics(days: int = 30) -> JSONResponse:
        from filigree.analytics import get_flow_metrics
        db = _get_db()
        metrics = get_flow_metrics(db, days=days)
        return JSONResponse(metrics)

    @app.get("/api/critical-path")
    async def api_critical_path() -> JSONResponse:
        db = _get_db()
        path = db.get_critical_path()
        return JSONResponse({"path": path, "length": len(path)})
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/john/filigree && python -m pytest tests/test_dashboard.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add tests/test_dashboard.py src/filigree/dashboard.py
git commit -m "feat(dashboard): add comment, search, metrics, critical-path API endpoints (R3, R6, R7, R10)"
```

---

## Task 3: Write API Endpoints — Activity Feed, Plan, Batch, Types (R2, R15, R18, R23)

**Files:**
- Modify: `src/filigree/dashboard.py`
- Test: `tests/test_dashboard.py`

**Step 1: Write failing tests**

Add to `tests/test_dashboard.py`:

```python
class TestActivityAPI:
    async def test_activity_feed(self, client: AsyncClient) -> None:
        resp = await client.get("/api/activity?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # populated_db creates issues and deps, generating events
        assert len(data) >= 1
        evt = data[0]
        assert "event_type" in evt
        assert "issue_id" in evt
        assert "created_at" in evt

    async def test_activity_with_since(self, client: AsyncClient) -> None:
        resp = await client.get("/api/activity?since=2000-01-01T00:00:00&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestPlanAPI:
    async def test_plan_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/plan/nonexistent")
        assert resp.status_code == 404


class TestBatchAPI:
    async def test_batch_update(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/batch/update",
            json={"issue_ids": [ids["a"], ids["b"]], "priority": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "updated" in data
        assert "errors" in data

    async def test_batch_close(self, client: AsyncClient, dashboard_db: FiligreeDB) -> None:
        ids = dashboard_db._test_ids  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/batch/close",
            json={"issue_ids": [ids["b"]], "reason": "batch done"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "closed" in data


class TestTypesListAPI:
    async def test_list_types(self, client: AsyncClient) -> None:
        resp = await client.get("/api/types")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(t["type"] == "task" for t in data)
        for t in data:
            assert "type" in t
            assert "display_name" in t

    async def test_create_issue(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": "New from dashboard", "type": "task"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "New from dashboard"
        assert data["type"] == "task"

    async def test_create_issue_empty_title(self, client: AsyncClient) -> None:
        resp = await client.post("/api/issues", json={"title": ""})
        assert resp.status_code == 400
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/john/filigree && python -m pytest tests/test_dashboard.py::TestActivityAPI tests/test_dashboard.py::TestPlanAPI tests/test_dashboard.py::TestBatchAPI tests/test_dashboard.py::TestTypesListAPI -v`
Expected: All FAIL.

**Step 3: Implement the endpoints**

Add inside `create_app()` in `src/filigree/dashboard.py`:

```python
    @app.get("/api/activity")
    async def api_activity(limit: int = 50, since: str = "") -> JSONResponse:
        db = _get_db()
        if since:
            events = db.get_events_since(since, limit=limit)
        else:
            events = db.get_recent_events(limit=limit)
        return JSONResponse(events)

    @app.get("/api/plan/{milestone_id}")
    async def api_plan(milestone_id: str) -> JSONResponse:
        db = _get_db()
        try:
            plan = db.get_plan(milestone_id)
        except KeyError:
            return JSONResponse({"error": f"Not found: {milestone_id}"}, status_code=404)
        return JSONResponse(plan)

    @app.post("/api/batch/update")
    async def api_batch_update(request: Request) -> JSONResponse:
        db = _get_db()
        body = await request.json()
        issue_ids = body.get("issue_ids", [])
        updated, errors = db.batch_update(
            issue_ids,
            status=body.get("status"),
            priority=body.get("priority"),
            assignee=body.get("assignee"),
            fields=body.get("fields"),
            actor=body.get("actor", "dashboard"),
        )
        return JSONResponse({
            "updated": [i.to_dict() for i in updated],
            "errors": errors,
        })

    @app.post("/api/batch/close")
    async def api_batch_close(request: Request) -> JSONResponse:
        db = _get_db()
        body = await request.json()
        issue_ids = body.get("issue_ids", [])
        try:
            closed = db.batch_close(
                issue_ids,
                reason=body.get("reason", ""),
                actor=body.get("actor", "dashboard"),
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse({"closed": [i.to_dict() for i in closed]})

    @app.get("/api/types")
    async def api_types() -> JSONResponse:
        db = _get_db()
        types = db.templates.list_types()
        return JSONResponse([
            {"type": t.type, "display_name": t.display_name, "pack": t.pack, "initial_state": t.initial_state}
            for t in types
        ])

    @app.post("/api/issues", status_code=201)
    async def api_create_issue(request: Request) -> JSONResponse:
        db = _get_db()
        body = await request.json()
        try:
            issue = db.create_issue(
                body.get("title", ""),
                type=body.get("type", "task"),
                priority=body.get("priority", 2),
                parent_id=body.get("parent_id"),
                assignee=body.get("assignee", ""),
                description=body.get("description", ""),
                notes=body.get("notes", ""),
                labels=body.get("labels"),
                deps=body.get("deps"),
                actor=body.get("actor", "dashboard"),
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(issue.to_dict(), status_code=201)
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/john/filigree && python -m pytest tests/test_dashboard.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add tests/test_dashboard.py src/filigree/dashboard.py
git commit -m "feat(dashboard): add activity, plan, batch, types, create-issue API endpoints (R2, R15, R18, R23)"
```

---

## Task 4: Frontend — Detail Panel Actions (R1, R5, R6, R17)

The core interactive features: status transitions, reprioritize, reassign, add comment, close/reopen — all in the detail panel.

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add action functions to the `<script>` section**

Add before the `// Init` section (before line 778 `parseHash()`):

```javascript
// ---------------------------------------------------------------------------
// Actions (write operations)
// ---------------------------------------------------------------------------
async function updateIssue(issueId, body) {
  try {
    var resp = await fetch('/api/issue/' + issueId, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      var err = await resp.json();
      alert('Error: ' + (err.error || 'Update failed'));
      return null;
    }
    await fetchData();
    return await resp.json();
  } catch (e) { alert('Network error'); return null; }
}

async function closeIssue(issueId) {
  var reason = prompt('Close reason (optional):') || '';
  var resp = await fetch('/api/issue/' + issueId + '/close', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({reason: reason}),
  });
  if (!resp.ok) { var err = await resp.json(); alert('Error: ' + (err.error || 'Close failed')); return; }
  await fetchData();
  if (selectedIssue === issueId) openDetail(issueId);
}

async function reopenIssue(issueId) {
  var resp = await fetch('/api/issue/' + issueId + '/reopen', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({}),
  });
  if (!resp.ok) { var err = await resp.json(); alert('Error: ' + (err.error || 'Reopen failed')); return; }
  await fetchData();
  if (selectedIssue === issueId) openDetail(issueId);
}

async function addComment(issueId) {
  var input = document.getElementById('commentInput');
  var text = input ? input.value.trim() : '';
  if (!text) return;
  var resp = await fetch('/api/issue/' + issueId + '/comments', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: text}),
  });
  if (resp.ok) { input.value = ''; openDetail(issueId); }
}

async function loadTransitions(issueId) {
  try {
    var resp = await fetch('/api/issue/' + issueId + '/transitions');
    if (resp.ok) return await resp.json();
  } catch (e) {}
  return [];
}
```

**Step 2: Modify `openDetail()` to include action controls**

In the `openDetail()` function, after the line that builds `content.innerHTML` (around line 672-713), replace the final `'<div class="mt-4 text-xs text-slate-600 select-all">filigree show ' + d.id + '</div>';` section with an expanded version that includes:

1. A transitions button group (fetched async)
2. Priority selector
3. Assignee input
4. Close/Reopen button
5. Comment input

Replace the closing portion of `content.innerHTML` (the line with `'<div class="mt-4 text-xs text-slate-600 select-all">filigree show '`) with:

```javascript
    // Actions section
    '<div class="mt-4 border-t border-slate-700 pt-3">' +
      '<div class="text-xs font-medium text-slate-400 mb-2">Actions</div>' +
      '<div id="transitionBtns" class="flex flex-wrap gap-1 mb-2"></div>' +
      '<div class="flex gap-2 mb-2">' +
        '<select id="prioSelect" onchange="updateIssue(\'' + d.id + '\', {priority: parseInt(this.value)})" class="bg-slate-700 text-xs rounded px-2 py-1 border border-slate-600">' +
          [0,1,2,3,4].map(function(p) {
            return '<option value="' + p + '"' + (d.priority === p ? ' selected' : '') + '>P' + p + '</option>';
          }).join('') +
        '</select>' +
        '<input id="assigneeInput" type="text" placeholder="Assignee" value="' + escHtml(d.assignee || '') + '"' +
          ' onkeydown="if(event.key===\'Enter\')updateIssue(\'' + d.id + '\',{assignee:this.value})"' +
          ' class="bg-slate-700 text-xs rounded px-2 py-1 border border-slate-600 flex-1">' +
      '</div>' +
      (statusCat !== 'done'
        ? '<button onclick="closeIssue(\'' + d.id + '\')" class="text-xs bg-red-900/50 text-red-400 px-3 py-1 rounded border border-red-800 hover:bg-red-900 mb-2">Close</button>'
        : '<button onclick="reopenIssue(\'' + d.id + '\')" class="text-xs bg-green-900/50 text-green-400 px-3 py-1 rounded border border-green-800 hover:bg-green-900 mb-2">Reopen</button>') +
    '</div>' +
    // Comment input
    '<div class="mt-3 border-t border-slate-700 pt-3">' +
      '<div class="text-xs font-medium text-slate-400 mb-1">Add Comment</div>' +
      '<div class="flex gap-1">' +
        '<input id="commentInput" type="text" placeholder="Comment..." onkeydown="if(event.key===\'Enter\')addComment(\'' + d.id + '\')"' +
          ' class="bg-slate-700 text-xs rounded px-2 py-1 border border-slate-600 flex-1 focus:outline-none focus:border-blue-500">' +
        '<button onclick="addComment(\'' + d.id + '\')" class="text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700">Send</button>' +
      '</div>' +
    '</div>' +
    '<div class="mt-3 text-xs text-slate-600 select-all">filigree show ' + d.id + '</div>';
```

Then after `content.innerHTML = ...;`, add async transition loading:

```javascript
  // Load transitions async and render buttons
  loadTransitions(issueId).then(function(transitions) {
    var container = document.getElementById('transitionBtns');
    if (!container || !transitions.length) return;
    container.innerHTML = transitions.map(function(t) {
      var cls = t.ready
        ? 'bg-blue-600 text-white hover:bg-blue-700'
        : 'bg-slate-700 text-slate-400 cursor-not-allowed';
      var title = t.missing_fields.length ? 'Missing: ' + t.missing_fields.join(', ') : '';
      return '<button ' + (t.ready ? 'onclick="updateIssue(\'' + issueId + '\',{status:\'' + t.to + '\'})"' : 'disabled') +
        ' class="text-xs px-2 py-1 rounded ' + cls + '" title="' + escHtml(title) + '">' +
        t.to + '</button>';
    }).join('');
  });
```

**Step 3: Test manually**

Run: `cd /home/john/filigree && .venv/bin/filigree dashboard --no-browser`
Open `http://localhost:8377`, click an issue, verify:
- Transition buttons appear
- Priority dropdown changes priority
- Close/Reopen buttons work
- Comment input posts comments

**Step 4: Run existing tests to verify nothing broke**

Run: `cd /home/john/filigree && python -m pytest tests/test_dashboard.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add interactive detail panel — status transitions, priority, assignee, comments, close/reopen (R1, R5, R6, R17)"
```

---

## Task 5: Frontend — Metrics View (R3)

Add a third view tab alongside Graph/Kanban.

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add Metrics button to nav bar**

In the header nav buttons section (around line 42-44), add a third button:

```html
<button id="btnMetrics" onclick="switchView('metrics')" class="px-3 py-1 rounded text-xs font-medium">Metrics</button>
```

**Step 2: Add metrics view container**

After the kanban view div (after line 99 `</div>`), add:

```html
  <!-- Metrics view -->
  <div id="metricsView" class="flex-1 hidden overflow-y-auto p-6">
    <div class="max-w-3xl mx-auto">
      <div class="flex items-center gap-3 mb-6">
        <span class="text-base font-semibold text-slate-200">Flow Metrics</span>
        <select id="metricsDays" onchange="loadMetrics()" class="bg-slate-700 text-xs rounded px-2 py-1 border border-slate-600">
          <option value="7">7 days</option>
          <option value="30" selected>30 days</option>
          <option value="90">90 days</option>
        </select>
      </div>
      <div id="metricsContent" class="text-slate-400 text-xs">Loading...</div>
    </div>
  </div>
```

**Step 3: Add metrics rendering logic**

Add to the `<script>` section before `// Init`:

```javascript
// ---------------------------------------------------------------------------
// Metrics view
// ---------------------------------------------------------------------------
async function loadMetrics() {
  var days = document.getElementById('metricsDays').value;
  var container = document.getElementById('metricsContent');
  container.innerHTML = '<div class="text-slate-500">Loading...</div>';
  try {
    var resp = await fetch('/api/metrics?days=' + days);
    var m = await resp.json();
    var byTypeHtml = Object.keys(m.by_type || {}).map(function(t) {
      var d = m.by_type[t];
      return '<tr><td class="py-1 pr-4 text-slate-300">' + escHtml(t) + '</td>' +
        '<td class="py-1 pr-4">' + (d.avg_cycle_time_hours !== null ? d.avg_cycle_time_hours + 'h' : '—') + '</td>' +
        '<td class="py-1">' + d.count + '</td></tr>';
    }).join('');

    container.innerHTML =
      '<div class="grid grid-cols-3 gap-4 mb-6">' +
        '<div class="bg-slate-800 rounded p-4 border border-slate-700">' +
          '<div class="text-slate-500 text-xs mb-1">Throughput</div>' +
          '<div class="text-2xl font-bold text-blue-400">' + m.throughput + '</div>' +
          '<div class="text-xs text-slate-500">issues closed</div></div>' +
        '<div class="bg-slate-800 rounded p-4 border border-slate-700">' +
          '<div class="text-slate-500 text-xs mb-1">Avg Cycle Time</div>' +
          '<div class="text-2xl font-bold text-emerald-400">' + (m.avg_cycle_time_hours !== null ? m.avg_cycle_time_hours + 'h' : '—') + '</div>' +
          '<div class="text-xs text-slate-500">first WIP to done</div></div>' +
        '<div class="bg-slate-800 rounded p-4 border border-slate-700">' +
          '<div class="text-slate-500 text-xs mb-1">Avg Lead Time</div>' +
          '<div class="text-2xl font-bold text-amber-400">' + (m.avg_lead_time_hours !== null ? m.avg_lead_time_hours + 'h' : '—') + '</div>' +
          '<div class="text-xs text-slate-500">creation to done</div></div>' +
      '</div>' +
      (byTypeHtml
        ? '<div class="bg-slate-800 rounded p-4 border border-slate-700">' +
          '<div class="text-xs font-medium text-slate-400 mb-2">By Type</div>' +
          '<table class="text-xs w-full"><thead><tr class="text-slate-500">' +
            '<th class="text-left py-1 pr-4">Type</th><th class="text-left py-1 pr-4">Avg Cycle</th><th class="text-left py-1">Count</th>' +
          '</tr></thead><tbody>' + byTypeHtml + '</tbody></table></div>'
        : '<div class="text-slate-500">No completed issues in this period.</div>');
  } catch (e) {
    container.innerHTML = '<div class="text-red-400">Failed to load metrics.</div>';
  }
}
```

**Step 4: Update `switchView()` to handle metrics**

Modify the `switchView()` function to add metrics toggling. Add this line inside the function after the kanbanView toggle:

```javascript
  document.getElementById('metricsView').classList.toggle('hidden', view !== 'metrics');
```

And add the metrics button styling:

```javascript
  document.getElementById('btnMetrics').className = view === 'metrics'
    ? 'px-3 py-1 rounded text-xs font-medium bg-blue-600 text-white'
    : 'px-3 py-1 rounded text-xs font-medium bg-slate-700 text-slate-300 hover:bg-slate-600';
```

Add to `switchView()` after the existing view renders:

```javascript
  if (view === 'metrics') loadMetrics();
```

**Step 5: Test manually**

Run the dashboard and click the Metrics tab. Verify the metrics panel renders with throughput, cycle time, lead time, and per-type breakdown.

**Step 6: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add Metrics view with throughput, cycle time, lead time (R3)"
```

---

## Task 6: Frontend — Activity Feed View (R2)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add Activity button to nav bar**

Add after the Metrics button in the header:

```html
<button id="btnActivity" onclick="switchView('activity')" class="px-3 py-1 rounded text-xs font-medium">Activity</button>
```

**Step 2: Add activity view container**

After the metrics view div:

```html
  <!-- Activity view -->
  <div id="activityView" class="flex-1 hidden overflow-y-auto p-6">
    <div class="max-w-3xl mx-auto">
      <div class="flex items-center gap-3 mb-4">
        <span class="text-base font-semibold text-slate-200">Recent Activity</span>
        <button onclick="loadActivity()" class="text-xs bg-slate-700 px-2 py-1 rounded hover:bg-slate-600">Refresh</button>
      </div>
      <div id="activityContent" class="text-slate-400 text-xs">Loading...</div>
    </div>
  </div>
```

**Step 3: Add activity rendering logic**

Add to `<script>`:

```javascript
// ---------------------------------------------------------------------------
// Activity feed
// ---------------------------------------------------------------------------
async function loadActivity() {
  var container = document.getElementById('activityContent');
  container.innerHTML = '<div class="text-slate-500">Loading...</div>';
  try {
    var resp = await fetch('/api/activity?limit=50');
    var events = await resp.json();
    if (!events.length) { container.innerHTML = '<div class="text-slate-500">No recent activity.</div>'; return; }
    container.innerHTML = events.map(function(e) {
      var time = e.created_at ? e.created_at.slice(5, 16) : '';
      var title = e.issue_title ? escHtml(e.issue_title.slice(0, 50)) : e.issue_id;
      var detail = '';
      if (e.event_type === 'status_changed') detail = e.old_value + ' \u2192 ' + e.new_value;
      else if (e.new_value) detail = e.new_value;
      return '<div class="flex items-start gap-3 py-2 border-b border-slate-800 cursor-pointer hover:bg-slate-800/50" onclick="openDetail(\'' + e.issue_id + '\')">' +
        '<span class="text-slate-600 shrink-0 w-24">' + time + '</span>' +
        '<span class="text-slate-400 shrink-0 w-32">' + escHtml(e.event_type) + '</span>' +
        '<span class="text-slate-300 truncate">' + title + '</span>' +
        (detail ? '<span class="text-slate-500 shrink-0">' + escHtml(detail) + '</span>' : '') +
        (e.actor ? '<span class="text-slate-600 shrink-0">' + escHtml(e.actor) + '</span>' : '') +
      '</div>';
    }).join('');
  } catch (e) {
    container.innerHTML = '<div class="text-red-400">Failed to load activity.</div>';
  }
}
```

**Step 4: Update `switchView()` for activity**

Add toggles for activity view and button styling, same pattern as metrics. Add `if (view === 'activity') loadActivity();` to trigger load.

**Step 5: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add Activity feed view showing recent events across all issues (R2)"
```

---

## Task 7: Frontend — WIP Aging Indicators (R4)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add aging CSS classes**

Add to the `<style>` section:

```css
  @keyframes stale-pulse { 0%,100% { border-left-color: #EF4444; } 50% { border-left-color: #7F1D1D; } }
  .aging-border { border-left: 4px solid #F59E0B; }
  .stale-border { border-left: 4px solid #EF4444; animation: stale-pulse 2s infinite; }
```

**Step 2: Add aging computation to `renderCard()`**

In the `renderCard()` function, after computing `readyClass`, add aging logic:

```javascript
  var agingClass = '';
  if (cat === 'wip' && issue.updated_at) {
    var ageMs = Date.now() - new Date(issue.updated_at).getTime();
    var ageHours = ageMs / 3600000;
    if (ageHours > 24) agingClass = 'stale-border';
    else if (ageHours > 4) agingClass = 'aging-border';
  }
```

Then add `agingClass` to the card's class list alongside `readyClass`. Update the card div to use `readyClass + ' ' + agingClass`.

**Step 3: Add age badge to card**

After the assignee span in `renderCard()`, add:

```javascript
  (cat === 'wip' && issue.updated_at
    ? (function() {
        var mins = Math.floor((Date.now() - new Date(issue.updated_at).getTime()) / 60000);
        if (mins < 60) return '<span class="text-slate-500">' + mins + 'm</span>';
        var hrs = Math.floor(mins / 60);
        if (hrs < 24) return '<span class="' + (hrs > 4 ? 'text-amber-400' : 'text-slate-500') + '">' + hrs + 'h</span>';
        return '<span class="text-red-400">' + Math.floor(hrs / 24) + 'd</span>';
      })()
    : '')
```

**Step 4: Test manually**

Run dashboard, verify WIP issues show age badges and color-coded borders.

**Step 5: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add WIP aging indicators with color-coded borders and time badges (R4)"
```

---

## Task 8: Frontend — Server-Side Search (R7)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Replace client-side search with server-side**

Replace the `filterSearch` input's `oninput="applyFilters()"` with `oninput="debouncedSearch()"`.

Add debounced search function:

```javascript
// ---------------------------------------------------------------------------
// Server-side search
// ---------------------------------------------------------------------------
var _searchTimeout = null;
function debouncedSearch() {
  clearTimeout(_searchTimeout);
  _searchTimeout = setTimeout(doSearch, 200);
}

var searchResults = null;
async function doSearch() {
  var q = document.getElementById('filterSearch').value.trim();
  if (!q) { searchResults = null; render(); return; }
  try {
    var resp = await fetch('/api/search?q=' + encodeURIComponent(q) + '&limit=100');
    var data = await resp.json();
    searchResults = new Set(data.results.map(function(i) { return i.id; }));
  } catch (e) { searchResults = null; }
  render();
}
```

In `getFilteredIssues()`, replace the existing client-side search filter block (lines 196-199) with:

```javascript
  if (searchResults !== null) {
    items = items.filter(function(i) { return searchResults.has(i.id); });
  }
```

**Step 2: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): replace client-side search with server-side FTS5 search (R7)"
```

---

## Task 9: Frontend — Auto-Refresh with Change Highlighting (R8, R20)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add polling and change detection**

Add auto-refresh polling and change tracking:

```javascript
// ---------------------------------------------------------------------------
// Auto-refresh with change highlighting
// ---------------------------------------------------------------------------
var previousIssueState = {};
var changedIds = new Set();
var REFRESH_INTERVAL = 15000; // 15 seconds

function trackChanges(newIssues) {
  changedIds.clear();
  newIssues.forEach(function(i) {
    var prev = previousIssueState[i.id];
    if (prev && (prev.status !== i.status || prev.priority !== i.priority || prev.assignee !== i.assignee || prev.updated_at !== i.updated_at)) {
      changedIds.add(i.id);
    }
  });
  previousIssueState = {};
  newIssues.forEach(function(i) { previousIssueState[i.id] = {status: i.status, priority: i.priority, assignee: i.assignee, updated_at: i.updated_at}; });
}
```

Call `trackChanges(allIssues)` inside `fetchData()` after `allIssues` is set.

In `renderCard()`, add a changed indicator class:

```javascript
  var changedClass = changedIds.has(issue.id) ? 'ring-1 ring-blue-500' : '';
```

Add to the card div classes.

Add a polling timer:

```javascript
setInterval(function() { if (!document.hidden) fetchData(); }, REFRESH_INTERVAL);
```

Add a "last updated" indicator — update the `refreshIndicator` span to show timestamp after each fetch.

**Step 2: Add changed-indicator CSS**

```css
  .changed-flash { animation: flash 1s ease-out; }
  @keyframes flash { 0% { box-shadow: 0 0 8px rgba(59,130,246,0.5); } 100% { box-shadow: none; } }
```

**Step 3: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add 15s auto-refresh with change highlighting (R8, R20)"
```

---

## Task 10: Frontend — Keyboard Navigation (R9)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add `tabindex` and focus styles to cards**

In `renderCard()`, add `tabindex="0"` to the card div. Add focus styles in CSS:

```css
  .card:focus { outline: 2px solid #3B82F6; outline-offset: -2px; }
```

**Step 2: Add keyboard handler**

Extend the existing `keydown` listener (around line 747):

```javascript
document.addEventListener('keydown', function(e) {
  var active = document.activeElement;
  if (active && (active.tagName === 'INPUT' || active.tagName === 'SELECT' || active.tagName === 'TEXTAREA')) return;

  if (e.key === '/' && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    document.getElementById('filterSearch').focus();
    return;
  }
  if (e.key === 'Escape') {
    if (selectedIssue) closeDetail();
    else { document.getElementById('filterSearch').value = ''; searchResults = null; applyFilters(); }
    return;
  }

  // j/k navigation
  if (e.key === 'j' || e.key === 'k') {
    var cards = Array.from(document.querySelectorAll('.card[tabindex]'));
    if (!cards.length) return;
    var idx = cards.indexOf(active);
    if (e.key === 'j') idx = Math.min(idx + 1, cards.length - 1);
    else idx = Math.max(idx - 1, 0);
    cards[idx].focus();
    return;
  }

  // Enter to open detail
  if (e.key === 'Enter' && active && active.classList.contains('card')) {
    var id = active.getAttribute('data-id');
    if (id) openDetail(id);
    return;
  }

  // Shortcuts when detail panel is open
  if (selectedIssue) {
    if (e.key === 'c') { var ci = document.getElementById('commentInput'); if (ci) { e.preventDefault(); ci.focus(); } }
    if (e.key === 'x') { e.preventDefault(); closeIssue(selectedIssue); }
  }
});
```

**Step 3: Add `data-id` attributes to cards**

In `renderCard()`, add `data-id="' + issue.id + '"` to the card div.

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add keyboard navigation — j/k, Enter, c, x, Escape (R9)"
```

---

## Task 11: Frontend — Critical Path Overlay on Graph (R10)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add critical path toggle**

In the graph toolbar (around line 80-85), add:

```html
<button id="btnCritPath" onclick="toggleCriticalPath()" class="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600">Critical Path</button>
```

**Step 2: Add critical path logic**

```javascript
var criticalPathIds = new Set();
var criticalPathActive = false;

async function toggleCriticalPath() {
  criticalPathActive = !criticalPathActive;
  var btn = document.getElementById('btnCritPath');
  if (criticalPathActive) {
    btn.className = 'px-2 py-0.5 rounded bg-red-600 text-white';
    var resp = await fetch('/api/critical-path');
    var data = await resp.json();
    criticalPathIds = new Set(data.path.map(function(p) { return p.id; }));
  } else {
    btn.className = 'px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600';
    criticalPathIds.clear();
  }
  renderGraph();
}
```

In `renderGraph()`, after creating the Cytoscape instance, add:

```javascript
  if (criticalPathActive && criticalPathIds.size) {
    cy.nodes().forEach(function(n) {
      if (!criticalPathIds.has(n.id())) n.style('opacity', 0.2);
    });
    cy.edges().forEach(function(e) {
      if (criticalPathIds.has(e.source().id()) && criticalPathIds.has(e.target().id())) {
        e.style({'width': 3, 'line-color': '#EF4444', 'target-arrow-color': '#EF4444'});
      } else {
        e.style('opacity', 0.1);
      }
    });
  }
```

**Step 3: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add critical path overlay on dependency graph (R10)"
```

---

## Task 12: Frontend — Cascade Impact & Bottleneck Score (R11, R12)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Compute impact scores client-side**

Add after `fetchData()` populates `allDeps`:

```javascript
// Compute transitive downstream count per issue
var impactScores = {};
function computeImpactScores() {
  // Build forward graph: blocker -> list of issues it blocks
  var forward = {};
  allDeps.forEach(function(d) {
    if (!forward[d.to]) forward[d.to] = [];
    forward[d.to].push(d.from);
  });
  impactScores = {};
  allIssues.forEach(function(i) {
    // BFS from this issue following forward edges
    var visited = new Set();
    var queue = [i.id];
    while (queue.length) {
      var cur = queue.shift();
      (forward[cur] || []).forEach(function(next) {
        if (!visited.has(next)) { visited.add(next); queue.push(next); }
      });
    }
    impactScores[i.id] = visited.size;
  });
}
```

Call `computeImpactScores()` in `fetchData()` after deps are loaded.

**Step 2: Show impact badge on cards**

In `renderCard()`, after the blocked-by indicator:

```javascript
  (impactScores[issue.id] > 0 ? '<span class="text-amber-400" title="Blocks ' + impactScores[issue.id] + ' downstream">\u26A1' + impactScores[issue.id] + '</span>' : '')
```

**Step 3: Add cascade preview on graph hover**

In `renderGraph()`, after creating the Cytoscape instance, add:

```javascript
  cy.on('mouseover', 'node', function(evt) {
    if (criticalPathActive) return;
    var nodeId = evt.target.id();
    var downstream = new Set();
    var queue = [nodeId];
    while (queue.length) {
      var cur = queue.shift();
      cy.edges().forEach(function(e) {
        if (e.source().id() === cur && !downstream.has(e.target().id())) {
          downstream.add(e.target().id());
          queue.push(e.target().id());
        }
      });
    }
    if (downstream.size) {
      cy.nodes().forEach(function(n) {
        if (n.id() !== nodeId && !downstream.has(n.id())) n.style('opacity', 0.15);
      });
    }
  });
  cy.on('mouseout', 'node', function() {
    if (criticalPathActive) return;
    cy.nodes().forEach(function(n) { n.style('opacity', n.data('opacity')); });
  });
```

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add bottleneck impact scores and cascade preview on graph hover (R11, R12)"
```

---

## Task 13: Frontend — Agent Workload Balance (R13) & Blocked Spotlight (R14)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add workload widget to metrics view**

In `loadMetrics()`, after the by-type table, add an agent workload section:

```javascript
    // Agent workload
    var agentLoad = {};
    allIssues.forEach(function(i) {
      if (i.assignee && (i.status_category || 'open') === 'wip') {
        agentLoad[i.assignee] = (agentLoad[i.assignee] || 0) + 1;
      }
    });
    var agents = Object.keys(agentLoad).sort(function(a, b) { return agentLoad[b] - agentLoad[a]; });
    if (agents.length) {
      var maxLoad = Math.max.apply(null, agents.map(function(a) { return agentLoad[a]; }));
      var agentHtml = agents.map(function(a) {
        var pct = (agentLoad[a] / maxLoad * 100);
        return '<div class="flex items-center gap-2 mb-1">' +
          '<span class="text-xs text-slate-300 w-24 truncate">' + escHtml(a) + '</span>' +
          '<div class="flex-1 h-4 bg-slate-900 rounded overflow-hidden">' +
            '<div class="h-full bg-blue-600 rounded" style="width:' + pct + '%"></div>' +
          '</div>' +
          '<span class="text-xs text-slate-400 w-6 text-right">' + agentLoad[a] + '</span></div>';
      }).join('');
      // Append to metrics content
      container.innerHTML += '<div class="bg-slate-800 rounded p-4 border border-slate-700 mt-4">' +
        '<div class="text-xs font-medium text-slate-400 mb-2">Agent Workload (Active WIP)</div>' +
        agentHtml + '</div>';
    }
```

**Step 2: Add "Blocked" quick-filter button to header**

Next to the "Ready" button in the header filter bar, add:

```html
<button id="btnBlocked" onclick="toggleBlocked()" class="px-2 py-1 rounded text-xs font-medium bg-slate-700 text-slate-400 border border-slate-600">
  &#128279; Blocked (<span id="blockedCount">0</span>)
</button>
```

Add the toggle logic:

```javascript
var blockedFilter = false;
function toggleBlocked() {
  blockedFilter = !blockedFilter;
  if (blockedFilter) readyFilter = false;
  var btn = document.getElementById('btnBlocked');
  btn.className = blockedFilter
    ? 'px-2 py-1 rounded text-xs font-medium bg-red-900/50 text-red-400 border border-red-700'
    : 'px-2 py-1 rounded text-xs font-medium bg-slate-700 text-slate-400 border border-slate-600';
  render();
}
```

In `getFilteredIssues()`, add after the ready-filter sort:

```javascript
  if (blockedFilter) {
    items = items.filter(function(i) {
      return (i.blocked_by || []).some(function(bid) {
        var b = issueMap[bid];
        return b && (b.status_category || 'open') !== 'done';
      });
    });
  }
```

Update `blockedCount` in `updateStats()`:

```javascript
  document.getElementById('blockedCount').textContent = s.blocked_count;
```

**Step 3: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add agent workload balance chart and blocked issue spotlight filter (R13, R14)"
```

---

## Task 14: Frontend — Plan/Milestone Tree View (R15)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add plan view trigger**

When clicking a milestone or epic in the kanban, add a "View Plan" button in the detail panel. In the detail panel rendering, after the type icon, add:

```javascript
    ((d.type === 'milestone' || d.type === 'epic')
      ? '<button onclick="loadPlanView(\'' + d.id + '\')" class="text-xs bg-slate-700 px-2 py-1 rounded hover:bg-slate-600 ml-2">View Plan</button>'
      : '')
```

**Step 2: Add plan rendering**

```javascript
async function loadPlanView(milestoneId) {
  var panel = document.getElementById('detailContent');
  panel.innerHTML = '<div class="text-slate-500 text-xs">Loading plan...</div>';
  try {
    var resp = await fetch('/api/plan/' + milestoneId);
    if (!resp.ok) { panel.innerHTML = '<div class="text-red-400 text-xs">No plan found for this issue.</div>'; return; }
    var plan = await resp.json();
    var m = plan.milestone || {};
    var phases = plan.phases || [];
    var totalSteps = plan.total_steps || 0;
    var completedSteps = plan.completed_steps || 0;
    var pct = totalSteps ? Math.round(completedSteps / totalSteps * 100) : 0;

    var html = '<div class="flex items-center justify-between mb-3">' +
      '<span class="text-xs text-slate-500">' + escHtml(m.id || milestoneId) + '</span>' +
      '<button onclick="openDetail(\'' + milestoneId + '\')" class="text-xs text-blue-400 hover:underline">Back to detail</button></div>' +
      '<div class="text-lg font-semibold text-slate-100 mb-2">' + escHtml(m.title || 'Plan') + '</div>' +
      '<div class="w-full h-3 rounded-full bg-slate-900 mb-1 overflow-hidden">' +
        '<div class="h-full bg-emerald-500 rounded-full" style="width:' + pct + '%"></div></div>' +
      '<div class="text-xs text-slate-500 mb-4">' + completedSteps + '/' + totalSteps + ' steps (' + pct + '%)</div>';

    phases.forEach(function(p) {
      var phase = p.phase || {};
      var steps = p.steps || [];
      var pDone = steps.filter(function(s) { return (s.status_category || 'open') === 'done'; }).length;
      var pPct = steps.length ? Math.round(pDone / steps.length * 100) : 0;
      html += '<div class="mb-3 bg-slate-800 rounded border border-slate-700 p-3">' +
        '<div class="flex items-center justify-between mb-1">' +
          '<span class="text-xs font-medium text-slate-300">' + escHtml(phase.title || 'Phase') + '</span>' +
          '<span class="text-xs text-slate-500">' + pDone + '/' + steps.length + '</span></div>' +
        '<div class="w-full h-1.5 rounded-full bg-slate-900 mb-2 overflow-hidden">' +
          '<div class="h-full bg-blue-500 rounded-full" style="width:' + pPct + '%"></div></div>' +
        steps.map(function(s) {
          var catColor = CATEGORY_COLORS[s.status_category || 'open'] || '#64748B';
          return '<div class="flex items-center gap-2 py-1 ml-4 cursor-pointer hover:text-blue-400" onclick="openDetail(\'' + s.id + '\')">' +
            '<span class="w-2 h-2 rounded-full shrink-0" style="background:' + catColor + '"></span>' +
            '<span class="text-xs text-slate-300">' + escHtml(s.title) + '</span>' +
            '<span class="text-xs text-slate-600">' + s.status + '</span></div>';
        }).join('') +
      '</div>';
    });

    panel.innerHTML = html;
  } catch (e) {
    panel.innerHTML = '<div class="text-red-400 text-xs">Failed to load plan.</div>';
  }
}
```

**Step 3: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add plan/milestone tree view with progress bars (R15)"
```

---

## Task 15: Frontend — Detail Panel Navigation Stack (R16)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add navigation stack**

```javascript
var detailHistory = [];

// Wrap existing openDetail to push to history
var _originalOpenDetail = openDetail;
// Replace openDetail:
// At the start of openDetail(), push current selectedIssue to history before changing
```

Modify `openDetail()`: at the very top, before `selectedIssue = issueId`, add:

```javascript
  if (selectedIssue && selectedIssue !== issueId) detailHistory.push(selectedIssue);
```

Add a back button to the detail panel header (next to the close button):

```javascript
    (detailHistory.length
      ? '<button onclick="detailBack()" class="text-slate-500 hover:text-slate-300 text-xs mr-2">&larr; Back</button>'
      : '')
```

Add the back function:

```javascript
function detailBack() {
  if (detailHistory.length) {
    var prev = detailHistory.pop();
    selectedIssue = null; // prevent pushing to history again
    openDetail(prev);
  }
}
```

Clear history when detail panel is closed:

In `closeDetail()`, add: `detailHistory = [];`

**Step 2: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add detail panel navigation stack with back button (R16)"
```

---

## Task 16: Frontend — Batch Operations (R18)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add multi-select state and UI**

```javascript
var selectedCards = new Set();
var multiSelectMode = false;

function toggleMultiSelect() {
  multiSelectMode = !multiSelectMode;
  if (!multiSelectMode) selectedCards.clear();
  render();
}

function toggleCardSelect(e, issueId) {
  if (!multiSelectMode) return;
  e.stopPropagation();
  if (selectedCards.has(issueId)) selectedCards.delete(issueId);
  else selectedCards.add(issueId);
  render();
}
```

**Step 2: Add select toggle button to header**

Add after the search input:

```html
<button onclick="toggleMultiSelect()" id="btnMultiSelect" class="px-2 py-1 rounded text-xs font-medium bg-slate-700 text-slate-400 border border-slate-600">Select</button>
```

**Step 3: Add checkbox rendering to cards**

In `renderCard()`, prepend a checkbox when multi-select is active:

```javascript
  var checkbox = multiSelectMode
    ? '<input type="checkbox" ' + (selectedCards.has(issue.id) ? 'checked' : '') +
      ' onclick="toggleCardSelect(event,\'' + issue.id + '\')" class="accent-blue-500 mr-1">'
    : '';
```

**Step 4: Add floating action bar**

After the footer, add:

```html
<div id="batchBar" class="fixed bottom-12 left-1/2 -translate-x-1/2 bg-slate-800 border border-slate-600 rounded-lg px-4 py-2 flex items-center gap-3 shadow-xl hidden z-20">
  <span id="batchCount" class="text-xs text-slate-300">0 selected</span>
  <button onclick="batchSetPriority()" class="text-xs bg-slate-700 px-2 py-1 rounded hover:bg-slate-600">Set Priority</button>
  <button onclick="batchCloseSelected()" class="text-xs bg-red-900/50 text-red-400 px-2 py-1 rounded border border-red-800 hover:bg-red-900">Close All</button>
  <button onclick="toggleMultiSelect()" class="text-xs text-slate-500 hover:text-slate-300">Cancel</button>
</div>
```

Show/hide based on selection:

```javascript
function updateBatchBar() {
  var bar = document.getElementById('batchBar');
  if (selectedCards.size > 0) {
    bar.classList.remove('hidden');
    document.getElementById('batchCount').textContent = selectedCards.size + ' selected';
  } else {
    bar.classList.add('hidden');
  }
}
```

Call `updateBatchBar()` at the end of `render()`.

**Step 5: Add batch action functions**

```javascript
async function batchSetPriority() {
  var p = prompt('Set priority (0-4):');
  if (p === null || p === '') return;
  var prio = parseInt(p);
  if (isNaN(prio) || prio < 0 || prio > 4) { alert('Invalid priority'); return; }
  await fetch('/api/batch/update', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({issue_ids: Array.from(selectedCards), priority: prio}),
  });
  selectedCards.clear();
  multiSelectMode = false;
  await fetchData();
}

async function batchCloseSelected() {
  if (!confirm('Close ' + selectedCards.size + ' issues?')) return;
  await fetch('/api/batch/close', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({issue_ids: Array.from(selectedCards)}),
  });
  selectedCards.clear();
  multiSelectMode = false;
  await fetchData();
}
```

**Step 6: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add multi-select batch operations — set priority, close all (R18)"
```

---

## Task 17: Frontend — Accessibility (R19)

**Files:**
- Modify: `src/filigree/static/dashboard.html`

**Step 1: Add ARIA roles**

- Add `role="region" aria-label="Kanban Board"` to `#kanbanBoard`
- Add `role="complementary" aria-label="Issue Detail"` to `#detailPanel`
- Add `aria-live="polite"` to stats elements (`#statOpen`, `#statActive`, `#statReady`)
- Add `aria-label` to all buttons and interactive elements
- Add `<a href="#kanbanBoard" class="sr-only focus:not-sr-only">Skip to content</a>` after `<body>`

**Step 2: Fix contrast**

Replace `text-slate-500` on interactive/informational elements with `text-slate-400` (bumps contrast from ~3.4:1 to ~5.6:1, passing AA).

**Step 3: Add focus trap to detail panel**

When detail panel opens, trap Tab key within it. When it closes, return focus.

```javascript
function trapFocus(panel) {
  var focusable = panel.querySelectorAll('button, input, select, [tabindex]');
  if (!focusable.length) return;
  focusable[0].focus();
}
```

Call `trapFocus(panel)` at the end of `openDetail()`.

**Step 4: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add ARIA roles, focus management, contrast fixes (R19)"
```

---

## Task 18: Remaining Phase 3 Features (R21-R30)

These are lower-priority features. Each is small enough to be a single commit.

**R21 — System Health Score:** Compute composite score from blocked ratio, WIP age, throughput trend. Show in header as color-coded badge. Pure frontend computation using existing data.

**R22 — Workflow State Machine Viz:** When type filter is active in kanban, add "Workflow" toggle that renders the state machine as a small Cytoscape graph. States as nodes, transitions as edges. Uses existing `/api/type/{type}` data.

**R23 — Issue Creation Form:** Already have `POST /api/issues` from Task 3. Add a "+" button in the header that opens a modal with title input, type dropdown (from `/api/types`), priority selector, description textarea.

**R24 — Claim/Release:** Add endpoints `POST /api/issue/{id}/claim`, `POST /api/issue/{id}/release`, `POST /api/claim-next`. Add claim/release buttons to detail panel.

**R25 — Dependency Management:** Add endpoints `POST /api/issue/{id}/dependencies`, `DELETE /api/issue/{id}/dependencies/{dep_id}`. Add "Add blocker" button with searchable picker, "x" remove buttons on dep rows.

**R26 — Saved Filter Presets:** Store filter state in localStorage. Add a "Save" button and preset quick-switch buttons above the kanban.

**R27 — Throughput Sparkline:** In the footer, render a tiny SVG sparkline of issues closed per day over 14 days. Compute from allIssues `closed_at` timestamps.

**R28 — Responsive Layout:** Add Tailwind breakpoint classes. Stack kanban columns on `max-w-lg`. Full-screen detail panel on mobile.

**R29 — Stale Issue Alerts:** Persistent badge in header: "N stale". Computed client-side from WIP issues with `updated_at` > threshold.

**R30 — Dark/Light Theme Toggle:** Add toggle button. Use CSS variables for color scheme. Store preference in localStorage.

Each feature follows the same pattern: write test (if API), implement, commit. Implement in priority order.

---

## Task 19: Final Integration Test & Documentation

**Files:**
- Modify: `docs/cli.md` — update dashboard section with new API capabilities
- Modify: `src/filigree/dashboard.py` — update module docstring
- Run: `python -m pytest tests/ -v` — full test suite

**Step 1: Run full test suite**

Run: `cd /home/john/filigree && python -m pytest tests/ -v`
Expected: All tests PASS.

**Step 2: Update dashboard docstring**

Change the module docstring in `dashboard.py` from "read-only project visualization" to reflect the new interactive capabilities.

**Step 3: Update docs/cli.md dashboard section**

Add notes about the new API endpoints and interactive features.

**Step 4: Manual smoke test**

Run: `cd /home/john/filigree && .venv/bin/filigree dashboard --no-browser`

Verify in browser:
- Kanban with aging indicators
- Graph with critical path overlay
- Metrics view with throughput/cycle time
- Activity feed with recent events
- Detail panel with status transitions, priority, assignee, comments, close/reopen
- Keyboard navigation (j/k, Enter, Escape, /, c, x)
- Search using FTS5
- Auto-refresh every 15s
- Multi-select batch operations

**Step 5: Commit**

```bash
git add docs/cli.md src/filigree/dashboard.py
git commit -m "docs: update dashboard documentation for interactive features"
```
