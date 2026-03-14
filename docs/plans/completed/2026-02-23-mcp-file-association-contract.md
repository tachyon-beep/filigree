# MCP File Association Tools - Contract

**Date:** 2026-02-23  
**Status:** Approved contract for implementation  
**Related:** `filigree-f4ad4b`, `filigree-f1babe`, `filigree-b40394`, `filigree-5bf143`

## Goal

Define exact MCP contracts for file-centric workflows before implementation:

1. List files
2. Get file detail
3. Get file timeline
4. Get files linked to an issue
5. Create file<->issue association
6. Register file by path (planning-first utility)

## Design Constraints

- Reuse existing `FiligreeDB` methods and dashboard semantics.
- Keep MCP error envelope consistent with existing tools: `{"error": "...", "code": "..."}`.
- Use strict, discoverable argument validation at MCP boundary.
- For path-based registration, enforce project-root safety and canonical project-relative storage.

## Tool Contracts

### 1) `list_files`

List tracked files with filters and pagination.

Maps to:
- `FiligreeDB.list_files_paginated()` in `src/filigree/core.py`

Input:

```json
{
  "type": "object",
  "properties": {
    "limit": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 100},
    "offset": {"type": "integer", "minimum": 0, "default": 0},
    "language": {"type": "string"},
    "path_prefix": {"type": "string"},
    "min_findings": {"type": "integer", "minimum": 0},
    "has_severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
    "scan_source": {"type": "string"},
    "sort": {"type": "string", "enum": ["updated_at", "first_seen", "path", "language"], "default": "updated_at"},
    "direction": {"type": "string", "enum": ["asc", "desc", "ASC", "DESC"]}
  }
}
```

Success response shape:

```json
{
  "results": [
    {
      "id": "filigree-f-...",
      "path": "src/x.py",
      "language": "python",
      "file_type": "",
      "first_seen": "...",
      "updated_at": "...",
      "metadata": {},
      "summary": {
        "total_findings": 0,
        "open_findings": 0,
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0
      },
      "associations_count": 0
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0,
  "has_more": false
}
```

Errors:
- `validation_error` for invalid enum/type/range arguments.

### 2) `get_file`

Get one file with associations, recent findings, and summary.

Maps to:
- `FiligreeDB.get_file_detail()`

Input:

```json
{
  "type": "object",
  "properties": {
    "file_id": {"type": "string"}
  },
  "required": ["file_id"]
}
```

Success response shape:
- Exact object from `get_file_detail()`:
  - `file`
  - `associations`
  - `recent_findings`
  - `summary`

Errors:
- `not_found` when file does not exist.

### 3) `get_file_timeline`

Get merged timeline for a file.

Maps to:
- `FiligreeDB.get_file_timeline()`

Input:

```json
{
  "type": "object",
  "properties": {
    "file_id": {"type": "string"},
    "limit": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 50},
    "offset": {"type": "integer", "minimum": 0, "default": 0},
    "event_type": {"type": "string", "enum": ["finding", "association", "file_metadata_update"]}
  },
  "required": ["file_id"]
}
```

Success response shape:

```json
{
  "results": [{"id": "...", "type": "...", "timestamp": "...", "source_id": "...", "data": {}}],
  "total": 1,
  "limit": 50,
  "offset": 0,
  "has_more": false
}
```

Errors:
- `not_found` when file does not exist.
- `validation_error` for invalid argument types/ranges.

### 4) `get_issue_files`

List files linked to an issue.

Maps to:
- `FiligreeDB.get_issue()` for existence validation
- `FiligreeDB.get_issue_files()`

Input:

```json
{
  "type": "object",
  "properties": {
    "issue_id": {"type": "string"}
  },
  "required": ["issue_id"]
}
```

Success response shape:
- Array from `get_issue_files()` with:
  - `id`, `file_id`, `issue_id`, `assoc_type`, `created_at`, `file_path`, `file_language`

Errors:
- `not_found` when issue does not exist.

### 5) `add_file_association`

Create issue/file association (idempotent).

Maps to:
- `FiligreeDB.get_file()` for file existence
- `FiligreeDB.add_file_association()`

Input:

```json
{
  "type": "object",
  "properties": {
    "file_id": {"type": "string"},
    "issue_id": {"type": "string"},
    "assoc_type": {"type": "string", "enum": ["bug_in", "task_for", "scan_finding", "mentioned_in"]}
  },
  "required": ["file_id", "issue_id", "assoc_type"]
}
```

Success response shape:

```json
{
  "status": "created"
}
```

Notes:
- Underlying insert is `INSERT OR IGNORE`; repeated calls for the same tuple are safe.
- Response remains `"created"` for both first and repeated calls to keep API simple.

Errors:
- `not_found` when file does not exist.
- `validation_error` when issue is missing or `assoc_type` is invalid.

### 6) `register_file` (utility for planning-first workflows)

Register a file record by path without triggering scanner execution.

Maps to:
- `_safe_path()` in `src/filigree/mcp_server.py` for path safety
- `FiligreeDB.register_file()`

Input:

```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string"},
    "language": {"type": "string"},
    "file_type": {"type": "string"},
    "metadata": {"type": "object"}
  },
  "required": ["path"]
}
```

Behavior:
1. Reject absolute/escaping paths.
2. Resolve against project root and canonicalize to project-relative path.
3. Upsert with `register_file(...)`.
4. Return canonicalized record.

Success response shape:
- `FileRecord.to_dict()` result:
  - `id`, `path`, `language`, `file_type`, `first_seen`, `updated_at`, `metadata`

Errors:
- `invalid_path` for absolute or escaping paths.
- `validation_error` for bad argument types.

## Shared Error Contract

All six tools return one of:

- Success payload (JSON object/array).
- Error payload:

```json
{
  "error": "Human-readable message",
  "code": "not_found | validation_error | invalid_path"
}
```

Conventions:
- `not_found`: missing issue/file resource.
- `validation_error`: malformed inputs, unsupported enum values, or downstream validation failures.
- `invalid_path`: path traversal/absolute path rejection for `register_file`.

## Implementation Checklist (for `filigree-f1babe` and `filigree-b40394`)

1. Add six tool definitions in `_tool_list()` with schemas above.
2. Add dispatch handlers with explicit argument validation.
3. Keep return payloads exactly aligned with this contract.
4. Add tests for happy paths and key negative paths:
   - file/issue not found
   - invalid `assoc_type`
   - invalid pagination and enum parameters
   - register path traversal rejection

