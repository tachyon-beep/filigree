# Label Taxonomy & Soft-Search System — Design (v3 final)

**Date:** 2026-03-06
**Status:** Approved (after 3-round review panel: architecture, systems thinking, UX, senior user)
**Author:** john + claude

## Problem

Labels in filigree are free-form strings with no discoverability. Agents working on
projects organically invent label conventions (e.g. `cluster:broad-except`) but:

1. No agent can discover what labels exist without guessing the exact string
2. No namespace/prefix search — can't find "all cluster:* issues"
3. Rich structured data (file associations, scan findings, timestamps) isn't
   surfaced through the label query interface
4. Time-dependent properties like issue age require manual re-labeling
5. Multi-agent coordination has no label vocabulary for attribution or review state

## Design

### Labels as a unified soft-search layer

Labels become a single query surface over heterogeneous issue data. An agent
doesn't need to know which table to join or which field to filter — it uses labels
for everything.

Three label types, one query interface:

| Type | Stored? | Writable? | Examples |
|------|---------|-----------|----------|
| Manual | Yes (labels table, `origin='manual'`) | Yes | `cluster:broad-except`, `review:needed`, `tech-debt` |
| Auto-tag | Yes (labels table, `origin='auto'`) | No (system-managed) | `area:mcp`, `severity:high`, `pack:core` |
| Virtual | No (computed at query time) | No | `age:stale`, `has:blockers`, `has:findings` |

All three types are queryable through the same `--label`, `--label-prefix`, and
`--not-label` filters. The caller doesn't need to know which type a label is.

### Schema change: `origin` column on labels table

Add an `origin` column to distinguish manual from auto-tag labels at the storage
level, rather than relying on prefix heuristics:

```sql
ALTER TABLE labels ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual';
```

- `origin='manual'` — user/agent applied
- `origin='auto'` — system-managed, tied to structured data

Column is named `origin` (not `source`) to avoid collision with the `source:`
manual label namespace.

This makes `remove_label` protection durable (`WHERE origin = 'manual'`),
makes `import_jsonl` correct (preserves origin on export/import), and makes
`list_labels` accurate without prefix guessing.

Requires schema version bump (7 -> 8) and migration that defaults all existing
rows to `'manual'`.

**Rollback procedure:** SQLite < 3.35 lacks `DROP COLUMN`. Rollback requires
table rebuild: `CREATE TABLE labels_new` without `origin`, copy data, drop old,
rename. Document this in the migration module.

### Index for label-first queries

Add a covering index for prefix and label-to-issue lookups:

```sql
CREATE INDEX IF NOT EXISTS idx_labels_label ON labels(label, issue_id);
```

The current PK `(issue_id, label)` is optimized for "labels of this issue."
The new index covers "issues with this label" and prefix scans (`LIKE 'cluster:%'`).

### Auto-tag labels

Written to the labels table (with `origin='auto'`) when structured data changes.
The system owns these — manual add/remove is rejected for auto-tag namespaces.

All auto-tag sync is handled by a single `_sync_auto_tags(issue_id, namespace)`
helper method to prevent scattered sync hooks across mixins.

**Default auto-tags (always on):**

| Namespace | Source | Trigger |
|-----------|--------|---------|
| `area:` | Path-to-component mapping | On file association add/remove |
| `severity:` | scan_findings.severity (highest active) | On scan finding ingest |
| `scanner:` | scan_findings.scan_source | On scan finding ingest |
| `pack:` | type_templates.pack | On issue create / type change |

**Opt-in auto-tags (enabled via config.json):**

| Namespace | Source | Why opt-in |
|-----------|--------|------------|
| `lang:` | file_records.language | Noise in single-language projects |
| `rule:` | scan_findings.rule_id (capped at top 5 per issue) | Cardinality explosion with many scanner rules |

Opt-in via `.filigree/config.json`:
```json
{
  "auto_tags": {
    "lang": true,
    "rule": true
  }
}
```

**Dropped from design:** `file:` auto-tag removed entirely (not even opt-in).
`area:` handles coarse grouping; per-file detail is available via `get_issue_files`.
Bare filenames collide across directories, and short relative paths are ambiguous
at project root boundaries.

#### Auto-tag sync rules

- Sync is **per-issue, per-run** — NOT per-finding. A scan run touching 500
  findings recomputes tags once per affected issue after all findings are processed.
- Sync runs inside the **same transaction** as the triggering operation
  (file association add, scan ingest, etc.) — no consistency window.
- Sync is idempotent: DELETE stale auto-tags for the namespace, INSERT current ones.
- All sync goes through `_sync_auto_tags(issue_id, namespace)` — one helper,
  called explicitly from each trigger site. No implicit hooks or scattered logic.

#### Area mapping

Configured via `area_map` key in `.filigree/config.json` (not a separate file):

```json
{
  "area_map": {
    "src/filigree/mcp_tools/*": "mcp",
    "src/filigree/cli_commands/*": "cli",
    "src/filigree/dashboard*": "dashboard",
    "src/filigree/db_*": "db",
    "src/filigree/static/*": "dashboard",
    "tests/*": "tests"
  }
}
```

Matching uses `fnmatch.fnmatch` on relative paths. First match wins. If no
pattern matches, no `area:` tag is written. Malformed patterns are logged and
skipped.

### Virtual labels

Computed at query time. Never stored. The query layer resolves them via an
**explicit allowlist dispatch** (not prefix substring matching) to prevent
injection and handle unknown values cleanly.

Both positive (`--label`) and negative (`--not-label`) queries are supported.
The dispatch returns `EXISTS (subquery)` for positive and `NOT EXISTS (subquery)`
for negative — same resolver, negation flag.

Unknown virtual label values (e.g. `age:garbage`) return an empty result set and
log a warning — they do NOT fall through to a literal label table lookup.

#### `age:` — issue age buckets

Computed from `created_at` relative to current UTC time. Boundaries use exclusive
lower bound to prevent overlap:

| Label | Condition (days since created) |
|-------|-------------------------------|
| `age:fresh` | < 7 |
| `age:recent` | >= 7 AND < 30 |
| `age:aging` | >= 30 AND < 90 |
| `age:stale` | >= 90 AND < 180 |
| `age:ancient` | >= 180 |

SQL uses `julianday('now') - julianday(created_at)` — all timestamps stored as
UTC ISO format, confirmed by existing `_now_iso()` convention.

#### `has:` — existence predicates

| Label | Condition |
|-------|-----------|
| `has:blockers` | Has unresolved dependencies |
| `has:children` | Has child issues (parent_id back-refs) |
| `has:findings` | Has **active** scan_findings (`status NOT IN ('fixed', 'false_positive')`) |
| `has:files` | Has file_associations |
| `has:comments` | Has comments |

### Manual labels

Free-form strings. Agents and humans can create any label that doesn't collide
with a reserved namespace.

**Reserved namespaces (rejected on manual add/remove):**

Auto-tag: `area:`, `severity:`, `scanner:`, `pack:` (always),
`lang:`, `rule:` (when enabled)

Virtual: `age:`, `has:`

**Namespace reservation is enforced from PR1 onward** — even before auto-tags
are implemented in PR3. This prevents manual labels in reserved namespaces from
accumulating and then being silently overwritten when auto-tags land.

#### `review:` namespace — review workflow state

Replaces the bare labels `needs-review`/`reviewed`/`rework`. Using a namespace
enables prefix search (`--label-prefix=review:`) and mutual exclusivity.

| Label | Meaning |
|-------|---------|
| `review:needed` | Agent finished work, human hasn't looked yet |
| `review:done` | Human has signed off |
| `review:rework` | Reviewed and sent back |

**Mutual exclusivity:** When adding a `review:` label, the system auto-removes
any existing `review:*` label on the same issue. This is ~10 lines of code in
`add_label` and prevents state accumulation (e.g. an issue having both
`review:needed` and `review:done` simultaneously).

#### Recommended bare labels (no namespace)

| Label | Purpose |
|-------|---------|
| `tech-debt` | Technical debt / cleanup |
| `regression` | Was working before, now broken |
| `security` | Security concern |
| `perf` | Performance concern |
| `cherry-pick` | Needs backport to another branch |
| `hotfix` | Needs to go out immediately |
| `flaky-test` | Intermittent test failure |
| `wontfix` | Intentionally declined |

Removed from v2: `needs-review`, `reviewed`, `rework` (moved to `review:` namespace),
`refactor` (overlaps with `tech-debt` in practice).

#### Recommended namespaced labels

| Namespace | Purpose | Example values |
|-----------|---------|----------------|
| `cluster:` | Root cause pattern | `cluster:broad-except`, `cluster:race-condition`, `cluster:null-check`, `cluster:type-coercion`, `cluster:resource-leak` |
| `effort:` | T-shirt sizing | `effort:xs`, `effort:s`, `effort:m`, `effort:l`, `effort:xl` |
| `source:` | Discovery method | `source:scanner`, `source:review`, `source:agent` |
| `agent:` | Agent instance (manual) | `agent:claude-1`, `agent:claude-2` |
| `release:` | Release targeting | `release:v1.3.0`, `release:v1.4.0` |
| `changelog:` | Changelog category | `changelog:added`, `changelog:changed`, `changelog:fixed`, `changelog:removed`, `changelog:deprecated` |
| `wait:` | External blocker | `wait:design`, `wait:upstream`, `wait:vendor`, `wait:decision` |
| `breaking:` | Breaking change | `breaking:api`, `breaking:schema`, `breaking:config` |
| `review:` | Review workflow state | `review:needed`, `review:done`, `review:rework` |

**Naming decisions:**
- `changelog:` not `cl:` — legibility on read beats brevity on write (unanimous)
- `wait:` not `blocked-by:` — shorter, no hyphen, reads naturally
- `agent:` stays manual — auto-attribution conflates origin with ownership;
  `created-by:` auto-tag is planned for a future version

### Taxonomy as versioned project config

The suggested label vocabulary is stored in `.filigree/config.json` under a
`label_taxonomy` key, not baked into code as a static document. Projects can
customize their vocabulary. The built-in defaults serve as a starting point.

When an agent calls `get_label_taxonomy`, it gets the project-specific vocabulary
merged with built-in defaults. Adding or deprecating namespaces is a config change,
not a code change.

Custom auto-tag namespaces (project-specific computed labels) are out of scope —
only the suggested manual vocabulary is customizable.

### Discovery tools (MCP + CLI)

#### `list_labels`

Returns all distinct labels in the project, grouped by namespace, with counts.
Includes virtual label namespaces with their computed counts (zero-count virtual
labels included for discoverability).

**Default `--top=10` per namespace** to prevent flooding agent context windows.
Use `--top=0` for unlimited. **Sorted alphabetically within each namespace**
(not by count) to prevent vocabulary herding — agents converging on high-count
labels and ignoring novel ones.

```
list_labels                          # Default: top 10 per namespace, alphabetical
list_labels --namespace=cluster      # Only labels in cluster: namespace
list_labels --top=0                  # Unlimited (show all)
```

Response shape is **stable regardless of filters** — filtered calls return the
same envelope with a subset of data:

```json
{
  "namespaces": {
    "cluster": {
      "type": "manual",
      "writable": true,
      "labels": [
        {"label": "cluster:broad-except", "count": 3},
        {"label": "cluster:null-check", "count": 1},
        {"label": "cluster:race-condition", "count": 1}
      ]
    },
    "age": {
      "type": "virtual",
      "writable": false,
      "labels": [
        {"label": "age:aging", "count": 30},
        {"label": "age:ancient", "count": 2},
        {"label": "age:fresh", "count": 12},
        {"label": "age:recent", "count": 45},
        {"label": "age:stale", "count": 8}
      ]
    },
    "area": {
      "type": "auto",
      "writable": false,
      "labels": [
        {"label": "area:dashboard", "count": 3},
        {"label": "area:mcp", "count": 5}
      ]
    }
  },
  "total_in_result": 47
}
```

#### `get_label_taxonomy`

Returns the full vocabulary with descriptions, writability, and suggested values.
The `values` (closed enumeration) vs `examples` (open-ended) distinction is
documented in the tool description so agents know which to validate against.

Tool description includes: *"Use `get_label_taxonomy` to see reserved namespaces
and suggested vocabulary before adding labels."*

```json
{
  "auto": {
    "area": {"description": "Component area from file paths", "writable": false, "example": "area:mcp"},
    "severity": {"description": "Highest active finding severity", "writable": false, "values": ["critical","high","medium","low","info"]},
    "scanner": {"description": "Scan source that produced findings", "writable": false, "example": "scanner:ruff"},
    "pack": {"description": "Workflow pack the issue type belongs to", "writable": false, "values": ["core","planning","release","requirements"]}
  },
  "virtual": {
    "age": {"description": "Issue age bucket", "writable": false, "values": ["fresh","recent","aging","stale","ancient"]},
    "has": {"description": "Existence predicates", "writable": false, "values": ["blockers","children","findings","files","comments"]}
  },
  "manual_suggested": {
    "cluster": {"description": "Root cause pattern for bugs", "writable": true, "examples": ["broad-except","race-condition","null-check","type-coercion","resource-leak"]},
    "effort": {"description": "T-shirt sizing", "writable": true, "values": ["xs","s","m","l","xl"]},
    "source": {"description": "How the issue was discovered", "writable": true, "examples": ["scanner","review","agent"]},
    "agent": {"description": "Agent instance attribution (manual)", "writable": true, "examples": ["claude-1","claude-2"]},
    "release": {"description": "Release version targeting", "writable": true, "examples": ["v1.3.0","v1.4.0"]},
    "changelog": {"description": "Changelog category", "writable": true, "values": ["added","changed","fixed","removed","deprecated"]},
    "wait": {"description": "External blocker type", "writable": true, "examples": ["design","upstream","vendor","decision"]},
    "breaking": {"description": "Breaking change marker", "writable": true, "examples": ["api","schema","config"]},
    "review": {"description": "Review workflow state (mutually exclusive — adding one removes prior)", "writable": true, "values": ["needed","done","rework"]}
  },
  "bare_labels": {
    "description": "Common labels without namespace prefix",
    "writable": true,
    "suggested": ["tech-debt","regression","security","perf","cherry-pick","hotfix","flaky-test","wontfix"]
  }
}
```

### Query interface changes

#### `list_issues` additions

The `label` parameter becomes an **array** (backward compatible — single string
still accepted, wrapped to array internally):

```
list_issues --label=age:stale                          # Single label
list_issues --label=age:stale --label=has:findings     # AND logic
list_issues --label-prefix=cluster:                    # Namespace search
list_issues --not-label=review:rework                  # Negation (exact)
list_issues --not-label=wait:                          # Negation (prefix)
list_issues --not-label=age:fresh                      # Negation (virtual)
```

`--label-prefix` **must include the trailing colon** — `cluster` does not match
`clusterfoo`. The implementation validates this.

`--label` + `--label-prefix` + `--not-label` combined uses AND logic. Documented
explicitly in the tool description.

`--not-label` supports exact match, prefix (trailing colon), AND virtual labels.
Virtual negation uses `NOT EXISTS(same subquery)` — same resolver, negation flag.

**`db_base.py` protocol update required:** The `list_issues` signature change
(`label: str | None` -> `label: list[str] | None`, plus `label_prefix`, `not_label`)
must be reflected in `DBMixinProtocol`.

#### `search_issues` expansion

FTS search includes label text using **Option B** (query-time concatenation) to
avoid schema migration. Labels are concatenated into a synthetic field at search
time. If profiling shows this is too slow, Option A (FTS trigger) can be added
with a proper migration in a future version.

### Validation and protection

#### `_validate_label_name` changes

- Rejects manual writes to auto-tag namespaces (even before auto-tags exist)
- Rejects manual writes to virtual namespaces
- Error messages distinguish the rejection type:
  - `"area: is a system-managed auto-tag namespace. These labels are computed automatically from file associations."`
  - `"age: is a virtual namespace computed at query time. You can filter by it with --label but cannot add it manually."`

#### `remove_label` changes

Currently `remove_label` does NOT call `_validate_label_name`. Must be updated to:
- Check `origin` column — reject removal of `origin='auto'` labels
- Or: call namespace validation before DELETE

#### `import_jsonl` changes

The import path at `db_meta.py:557-562` currently does a raw INSERT bypassing
validation. Must be updated to:
- Preserve the `origin` column on export/import
- Validate imported labels against reserved namespaces
- Pre-v8 JSONL imports (no `origin` field): default to `'manual'`

### Rollout strategy

Ship in three independent PRs:

**PR 1 — Virtual labels + query improvements (zero schema changes)**
- Virtual label resolution in `list_issues` (`age:*`, `has:*`) — allowlist dispatch
- `--label-prefix` parameter (trailing colon validation)
- `--not-label` parameter (exact, prefix, AND virtual negation via `NOT EXISTS`)
- `label` parameter as array (backward compat: string wrapped to array)
- **Namespace reservation in `_validate_label_name`** — reserve auto-tag and
  virtual namespaces even before PR3. Prevents accumulation of manual labels
  that would be silently overwritten later.
- `db_base.py` protocol update for new `list_issues` signature
- `review:` mutual exclusivity in `add_label` (auto-remove prior `review:*`)

**PR 2 — Discovery tools (read-only, no schema changes)**
- `list_labels` MCP tool + CLI command (default `--top=10`, alphabetical sort)
- `get_label_taxonomy` MCP tool + CLI command
- Taxonomy config in `.filigree/config.json` (`label_taxonomy` key)
- Tool description hints: *"Use get_label_taxonomy to see reserved namespaces"*

**PR 3 — Auto-tags (schema migration required)**
- `origin` column migration on labels table (v7 -> v8)
- Documented rollback procedure (table rebuild for SQLite < 3.35)
- `idx_labels_label` covering index
- `_sync_auto_tags(issue_id, namespace)` helper — single entry point
- Auto-tag sync hooks (transactional, per-issue-per-run)
- `remove_label` protection via `origin` column
- `import_jsonl` validation and `origin` preservation
- FTS Option B for label search

### Future work (out of scope for this design)

- `created-by:` auto-tag from `actor` field (origin attribution for multi-agent)
- Label colors / display metadata
- Label-based automation / triggers
- Cross-project label sharing
- Hierarchical labels (e.g. `area:mcp:tools` sub-namespaces)
- Custom auto-tag namespaces (project-specific computed labels)
- Dashboard UI for label filtering
- Mutual exclusivity groups in taxonomy config (generalize `review:` pattern)

## Resolved questions

### Round 1

1. **AND or OR for multiple `--label`?** AND. Unanimous.
2. **`file:` path format?** Dropped entirely. `area:` covers the useful case.
3. **`list_labels` + `get_label_taxonomy` separate?** Yes — different call
   frequency, different response sizes, different intent.
4. **`--label-prefix` must include trailing colon?** Yes.
5. **Include zero-count virtual labels?** Yes, for discoverability.
6. **FTS Option A or B?** B (query-time concatenation) — avoids schema migration.
7. **Separate `area_map.json` config file?** No — goes in existing `config.json`.

### Round 2

1. **Drop `file:` entirely?** Yes. Unanimous across all 4 reviewers.
2. **Custom auto-tag namespaces?** No. Manual vocabulary customization only.
3. **`agent:` auto vs manual?** Manual. `created-by:` deferred to future version.
4. **`--not-label` on virtual labels?** Yes. `NOT EXISTS(same subquery)` — same
   resolver, negation flag. Scoped into PR1.
5. **`changelog:` vs `cl:`?** `changelog:`. Unanimous — legibility wins.
6. **Column name collision?** `origin` not `source`. Unanimous.
7. **`refactor` bare label?** Dropped. Overlaps `tech-debt`.
8. **`list_labels` default cap?** `--top=10`, alphabetical sort. 3-1 consensus.
9. **Bare label state machine?** `review:` namespace with mutual exclusivity.
10. **`wontfix` bare label?** Added.
