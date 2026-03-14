# Dashboard Settings Gear with Soft Reload — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a gear dropdown menu to the dashboard header with a soft-reload action that re-initializes registry and DB connections, plus the existing theme toggle.

**Architecture:** New `POST /api/reload` root-level endpoint in `dashboard.py` that calls `close_all()` then re-registers active projects. Frontend replaces the standalone theme toggle with a gear icon dropdown using the existing popover pattern.

**Tech Stack:** FastAPI (dashboard.py), vanilla JS + Tailwind CSS (dashboard.html)

---

### Task 1: Backend — `POST /api/reload` endpoint

**Files:**
- Modify: `src/filigree/dashboard.py:620-622` (before `return app`)
- Test: `tests/test_dashboard.py` (new class at end of file)

**Step 1: Write the failing test**

Add at the end of `tests/test_dashboard.py`:

```python
class TestReloadAPI:
    async def test_reload_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.post("/api/reload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "projects" in data

    async def test_reload_clears_connections(self, client: AsyncClient) -> None:
        # Prime the connection cache
        await client.get("/api/issues")
        pm = dash_module._project_manager
        assert pm is not None
        assert len(pm._connections) > 0

        resp = await client.post("/api/reload")
        assert resp.status_code == 200
        # Connections cleared (will reopen lazily on next request)
        assert len(pm._connections) == 0

    async def test_reload_issues_still_work_after(self, client: AsyncClient) -> None:
        await client.post("/api/reload")
        # Lazy reconnect should make subsequent requests work
        resp = await client.get("/api/issues")
        assert resp.status_code == 200
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dashboard.py::TestReloadAPI -v`
Expected: FAIL — 404 on POST /api/reload

**Step 3: Implement the endpoint**

In `src/filigree/dashboard.py`, insert the following block after the `api_register` handler (before `return app` on line 622):

```python
    @app.post("/api/reload")
    async def api_reload() -> JSONResponse:
        if _project_manager is None:
            return JSONResponse({"error": "Project manager not initialized"}, status_code=500)
        _project_manager.close_all()
        projects = _project_manager.get_active_projects()
        for proj in projects:
            _project_manager.register(Path(proj.path))
        return JSONResponse({"ok": True, "projects": len(projects)})
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dashboard.py::TestReloadAPI -v`
Expected: all 3 PASS

**Step 5: Run full test suite**

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: no regressions

**Step 6: Commit**

```bash
git add src/filigree/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): add POST /api/reload endpoint for soft server reload"
```

---

### Task 2: Frontend — Gear dropdown with reload + theme toggle

**Files:**
- Modify: `src/filigree/static/dashboard.html:187-192` (header utility area)
- Modify: `src/filigree/static/dashboard.html:2564-2580` (toggleTheme function — update element ID)

**Step 1: Replace the theme toggle button with gear dropdown in the header**

In `dashboard.html`, replace lines 187-192 (the utility div in the header):

Old:
```html
  <div class="flex items-center gap-3 text-xs text-secondary">
    <button onclick="toggleTheme()" id="themeToggle" class="text-xs px-2 py-1 rounded bg-overlay bg-overlay-hover" title="Toggle theme">&#9788;</button>
    <span id="refreshIndicator" class="opacity-0 text-accent">Refreshing...</span>
    <span id="healthBadge" onclick="showHealthBreakdown()" class="cursor-pointer px-2 py-0.5 rounded text-xs font-bold" title="System Health Score — click for breakdown">--</span>
    <button onclick="showHealthHelp(this)" class="help-icon bg-overlay text-secondary text-primary-hover" title="What is Health Score?" aria-label="Explain health score">?</button>
  </div>
```

New:
```html
  <div class="flex items-center gap-3 text-xs text-secondary">
    <span id="refreshIndicator" class="opacity-0 text-accent">Refreshing...</span>
    <span id="healthBadge" onclick="showHealthBreakdown()" class="cursor-pointer px-2 py-0.5 rounded text-xs font-bold" title="System Health Score — click for breakdown">--</span>
    <button onclick="showHealthHelp(this)" class="help-icon bg-overlay text-secondary text-primary-hover" title="What is Health Score?" aria-label="Explain health score">?</button>
    <div class="relative">
      <button onclick="toggleSettingsMenu(event)" id="settingsGear" class="text-xs px-2 py-1 rounded bg-overlay bg-overlay-hover" title="Settings" aria-label="Settings menu">&#9881;</button>
      <div id="settingsDropdown" class="hidden absolute right-0 top-full mt-1 rounded-lg shadow-xl text-xs z-60" style="background:var(--surface-base);border:1px solid var(--border-strong);min-width:160px">
        <button onclick="reloadServer()" class="w-full text-left px-3 py-2 bg-overlay-hover text-primary rounded-t-lg">&#8635; Reload server</button>
        <button onclick="toggleTheme();closeSettingsMenu()" id="themeToggle" class="w-full text-left px-3 py-2 bg-overlay-hover text-primary rounded-b-lg">&#9788; Toggle theme</button>
      </div>
    </div>
  </div>
```

**Step 2: Add the settings menu JS functions**

In `dashboard.html`, insert before the theme toggle section (before `// Theme toggle (R30)` at line 2561):

```javascript
// ---------------------------------------------------------------------------
// Settings gear menu
// ---------------------------------------------------------------------------
function toggleSettingsMenu(e) {
  e.stopPropagation();
  var dd = document.getElementById('settingsDropdown');
  if (dd.classList.contains('hidden')) {
    dd.classList.remove('hidden');
    setTimeout(function() { document.addEventListener('click', _settingsOutsideClick); }, 0);
  } else {
    closeSettingsMenu();
  }
}
function _settingsOutsideClick(e) {
  var dd = document.getElementById('settingsDropdown');
  if (dd && !dd.contains(e.target) && e.target.id !== 'settingsGear') closeSettingsMenu();
}
function closeSettingsMenu() {
  document.getElementById('settingsDropdown').classList.add('hidden');
  document.removeEventListener('click', _settingsOutsideClick);
}
function reloadServer() {
  closeSettingsMenu();
  var ind = document.getElementById('refreshIndicator');
  ind.textContent = 'Reloading...';
  ind.style.opacity = '1';
  fetch(BASE + '/reload', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      ind.textContent = data.ok ? 'Reloaded (' + data.projects + ' projects)' : 'Reload failed';
      ind.style.color = data.ok ? '' : '#EF4444';
      setTimeout(function() { ind.style.opacity = '0'; ind.textContent = 'Refreshing...'; ind.style.color = ''; }, 2000);
      if (data.ok) loadAllData();
    })
    .catch(function() {
      ind.textContent = 'Reload failed';
      ind.style.color = '#EF4444';
      setTimeout(function() { ind.style.opacity = '0'; ind.textContent = 'Refreshing...'; ind.style.color = ''; }, 2000);
    });
}
```

**Step 3: Update `toggleTheme()` to use the new icon text**

The `toggleTheme()` function at line 2569 sets `themeToggle.textContent` to just the icon character. Update it to include the label since the button is now inside a dropdown menu:

Old:
```javascript
  document.getElementById('themeToggle').textContent = next === 'light' ? '\u263E' : '\u2606';
```

New:
```javascript
  document.getElementById('themeToggle').innerHTML = (next === 'light' ? '&#9790;' : '&#9788;') + ' Toggle theme';
```

Also update the init block (around line 2598) similarly:

Old:
```javascript
    if (btn) btn.textContent = '\u263E';
```

New:
```javascript
    if (btn) btn.innerHTML = '&#9790; Toggle theme';
```

**Step 4: Manual verification**

Open `http://localhost:8377` in browser. Verify:
1. Gear icon (⚙) visible in header top-right
2. Click gear → dropdown appears with two items
3. Click outside → dropdown closes
4. "Reload server" → indicator shows "Reloaded (N projects)", data refreshes
5. "Toggle theme" → theme switches, dropdown closes, button label updates icon

**Step 5: Commit**

```bash
git add src/filigree/static/dashboard.html
git commit -m "feat(dashboard): add settings gear dropdown with reload server action"
```

---

### Task 3: Lint, type-check, full test suite

**Step 1: Run linter**

Run: `uv run ruff check src/filigree/dashboard.py tests/test_dashboard.py`
Expected: clean

**Step 2: Run formatter check**

Run: `uv run ruff format --check src/filigree/dashboard.py tests/test_dashboard.py`
Expected: clean

**Step 3: Run type checker**

Run: `uv run mypy src/filigree/dashboard.py`
Expected: clean

**Step 4: Run full test suite**

Run: `uv run pytest --tb=short`
Expected: all pass, no regressions
