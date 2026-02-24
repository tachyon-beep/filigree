# Plugin System & Language Packs — Design Document

**Date:** 2026-02-25
**Status:** Proposed
**Target:** filigree-next (clean-break architecture)

## Problem Statement

Filigree manages workflows — types, states, transitions, dependencies — but treats
code as opaque. The v1.x scanner system (`.filigree/scanners/*.toml`, SARIF-lite
ingestion, `scan_findings` table) provides crude static analysis integration, but
it's bolted on: scanners are external shell commands, findings are opaque blobs, and
there's no structural understanding of the code being tracked.

Meanwhile, filigree's plan hierarchy (milestone → phase → step → task) already
models multi-step work at the atomic level. If tasks could carry **structured code
transforms** with **pre/post validation**, filigree becomes a deterministic
refactoring engine: plan the entire transformation upfront, review each atomic
change, execute with confidence.

## Design Goals

1. **Pre-calculated refactoring** — author an entire multi-file refactor as a plan,
   validate every delta against disk state, review at the atomic level, then execute
   atomically.
2. **Language-aware transforms** — AST-level operations that survive formatting
   changes, comment insertions, and unrelated edits.
3. **Mix-and-match language support** — a single project can enable Python, Rust, C,
   JavaScript, and HTML language packs simultaneously.
4. **Replace the scanner system** — language pack validators subsume
   `.filigree/scanners/*.toml` and SARIF findings with native, structured
   alternatives.
5. **Compose with filigree-next** — use item_links for execution ordering and
   transition gates for readiness enforcement. Minimal new machinery in core.

## Architecture: Layered Pack System

Three pack types compose to deliver the full system:

```
┌─────────────────────────────────────────────────────┐
│  Workflow Pack (existing)                            │
│  types, states, transitions, field schemas           │
│  e.g. "engineering", "editorial"                     │
├─────────────────────────────────────────────────────┤
│  Language Pack (new)                                 │
│  parser, transform operations, validators            │
│  e.g. "python", "rust", "c", "javascript", "html"   │
├─────────────────────────────────────────────────────┤
│  Extension Pack (new)                                │
│  schema extensions + lifecycle hooks                 │
│  references language packs for AST work              │
│  e.g. "code-delta", "coverage-tracking"              │
└─────────────────────────────────────────────────────┘
```

### Composition via config

```json
{
  "packs": {
    "workflow": ["core", "planning", "release"],
    "language": ["python", "rust", "c", "javascript", "html"],
    "extensions": ["code-delta"]
  }
}
```

### Language routing

When the code-delta extension needs to parse or transform a file, it resolves the
language pack by file extension. Each language pack declares its extensions:

```python
{
  "pack": "python",
  "kind": "language",
  "file_extensions": [".py", ".pyi"],
  "tree_sitter_language": "python",
  ...
}
```

Multiple language packs coexist. The extension dispatches to the correct one per
file. If no language pack matches a file, the task is flagged as unresolvable (red).

### Distribution

All packs are built-in — they ship with filigree. No external plugin discovery or
third-party packaging. New languages require a filigree release.

## Language Pack Structure

A language pack provides five capabilities for a set of file types:

### Definition format

```python
{
  "pack": "python",
  "kind": "language",
  "version": "1.0",
  "file_extensions": [".py", ".pyi"],
  "tree_sitter_language": "python",

  "operations": {
    "add_type_annotation":   {"params": ["node_query", "type_expr"]},
    "rename_symbol":         {"params": ["node_query", "new_name"]},
    "add_parameter":         {"params": ["function_query", "name", "type_expr", "default"]},
    "remove_parameter":      {"params": ["function_query", "name"]},
    "add_decorator":         {"params": ["function_query", "decorator_expr"]},
    "extract_function":      {"params": ["selection_query", "new_name"]},
    "add_import":            {"params": ["module", "names"]},
    "remove_import":         {"params": ["module", "names"]}
  },

  "validators": {
    "typecheck":  {"command": "mypy", "args": ["--no-error-summary", "{file}"]},
    "lint":       {"command": "ruff", "args": ["check", "{file}"]},
    "test":       {"command": "pytest", "args": ["{file}", "--tb=short", "-q"]}
  },

  "queries": {
    "all_functions":     "(function_definition name: (identifier) @name)",
    "all_classes":       "(class_definition name: (identifier) @name)",
    "all_imports":       "(import_statement) @import",
    "untyped_params":    "(function_definition parameters: (parameters (identifier) @param))"
  }
}
```

### Design principles

1. **Operations are per-language.** The operation name (`add_type_annotation`) is
   universal; the implementation is language-specific. Python produces `x: int`,
   Rust produces `x: i32`, C uses a different syntax entirely.

2. **Validators replace scanners.** Instead of the current TOML scanner registry,
   language packs declare their own validators. This subsumes
   `.filigree/scanners/*.toml` entirely.

3. **Named queries are reusable.** Both transforms and assertions can reference
   them. "Find all untyped parameters" is a query; "add type annotations to all
   untyped parameters" is a transform that uses that query.

4. **Tree-sitter is the foundation.** Battle-tested, 150+ languages, Python bindings
   via `tree-sitter`. Queries use S-expressions:

   ```scheme
   ;; find all methods that take self and have no return type
   (function_definition
     name: (identifier) @fn_name
     parameters: (parameters
       (identifier) @first_param)
     !return_type)
     (#eq? @first_param "self")
   ```

### Initial language packs

| Pack         | Extensions             | Validators                    | Notes                          |
|--------------|------------------------|-------------------------------|--------------------------------|
| `python`     | `.py`, `.pyi`          | mypy, ruff, pytest            | Most complete — primary language |
| `rust`       | `.rs`                  | cargo check, cargo test, clippy | Cargo-workspace aware         |
| `c`          | `.c`, `.h`             | gcc/clang syntax check        | Minimal validators initially   |
| `javascript` | `.js`, `.mjs`, `.cjs`  | eslint, node --check          | Could expand to `.jsx`         |
| `html`       | `.html`, `.htm`        | html-validate or similar      | Structural/attribute transforms |

## Extension Pack: Code Delta

The `code-delta` extension pack wires language packs into the workflow lifecycle. It
adds schema to task-level items and defines what happens at transitions.

### Definition format

```python
{
  "pack": "code-delta",
  "kind": "extension",
  "version": "1.0",
  "requires_language_pack": true,

  "schema_extensions": {
    "applicable_types": ["task", "step"],
    "fields": [
      {
        "name": "delta",
        "type": "object",
        "schema": {
          "file": "text",
          "transforms": [{
            "operation": "text",
            "query": "text",
            "args": "object"
          }],
          "assertions": [{
            "query": "text",
            "expect": "text"   # "exists" | "not_exists" | "count:N"
          }],
          "validate_with": ["text"]
        }
      },
      {
        "name": "delta_status",
        "type": "enum",
        "options": ["pending", "green", "red", "stale"],
        "default": "pending"
      }
    ]
  },

  "lifecycle_hooks": {
    "on_plan_validate": {
      "action": "validate_all_deltas",
      "description": "Check every task's delta against current disk state"
    },
    "on_plan_execute": {
      "action": "execute_all_deltas",
      "description": "Apply all deltas in topological order, rollback on failure"
    }
  }
}
```

### Task delta example

```json
{
  "file": "src/filigree/core.py",
  "transforms": [
    {
      "operation": "add_return_type",
      "query": "(function_definition name: (identifier) @fn (#eq? @fn \"create_issue\"))",
      "args": {"type": "Issue"}
    }
  ],
  "assertions": [
    {
      "query": "(function_definition name: (identifier) @fn (#eq? @fn \"create_issue\") return_type: (type) @rt)",
      "expect": "exists"
    }
  ],
  "validate_with": ["typecheck"]
}
```

### Delta status lifecycle

```
pending ──(validate)──→ green ──(disk changes outside filigree)──→ stale
   │                      │                                           │
   └──(validate fails)──→ red ←──(validate fails)────────────────────┘
                          │
                          └──(fix + revalidate)──→ green
```

## Execution Model: Atomic All-or-Nothing

### Three-phase execution

```
1. VALIDATE PHASE (read-only, no disk changes)
   ├── Topological sort: order tasks by item_links dependency graph
   ├── For each task with a delta:
   │   ├── Resolve language pack from file extension
   │   ├── Parse current file → AST via tree-sitter
   │   ├── Locate target node via query
   │   ├── Verify node exists (pre-condition)
   │   └── Simulate transform in memory, verify assertions pass
   └── All green? → proceed. Any red? → abort with report.

2. EXECUTE PHASE (atomic)
   ├── Snapshot all target files (backup for rollback)
   ├── For each task in topological order:
   │   ├── Apply transforms to disk
   │   ├── Run AST assertions on modified file
   │   └── If assertions fail → ROLLBACK all files, abort
   ├── All transforms landed → run language pack validators
   │   ├── mypy, ruff, pytest, cargo check, etc.
   │   └── If validators fail → ROLLBACK all files, abort
   └── All green → mark all tasks as completed

3. ROLLBACK (if anything fails in execute phase)
   ├── Restore all files from snapshots
   ├── Mark failing task as "red"
   └── Report exactly what failed and why
```

### Staleness detection

If someone edits a file outside of filigree between validation and execution, the
pre-condition query might no longer match. The delta goes `green → stale`, and the
user must re-validate before executing. The execute phase always re-validates first
to catch this.

## Integration with filigree-next

The code-delta extension adds minimal new machinery because filigree-next's
item_links and transition gates handle the hard parts.

### Execution ordering via item_links

The topological sort for delta execution is a query over the existing item_links
`blocks` relationships. No separate dependency graph needed.

### Transition gates enforce delta readiness

```python
# Task-level: can't mark ready until delta validates
"transitions": [
  {
    "from": "in_progress",
    "to": "ready_to_land",
    "enforcement": "hard",
    "gates": [
      {"type": "field_set", "field": "delta"},
      {"type": "field_eq", "field": "delta_status", "value": "green"}
    ]
  }
]

# Phase-level: can't execute until all child tasks are green
"transitions": [
  {
    "from": "active",
    "to": "executing",
    "enforcement": "hard",
    "gates": [
      {"type": "all_linked", "link_type": "contains",
       "condition": {"field": "delta_status", "value": "green"}}
    ]
  },
  {
    "from": "executing",
    "to": "completed",
    "enforcement": "hard",
    "gates": [
      {"type": "all_linked", "link_type": "contains",
       "condition": {"status_category": "done"}}
    ]
  }
]
```

### Workflow

1. Author tasks with deltas (status: `in_progress`)
2. Validate each → delta_status goes `green` (transition to `ready_to_land`)
3. All tasks green → phase can transition to `executing` (gate passes)
4. Execute atomically → tasks close → phase transitions to `completed`

## End-to-End Data Flow

```
AUTHORING (agent or human creates the plan)
│
├─ filigree create-plan with code-delta extension enabled
│  ├─ Phase: "Add type annotations to core.py"
│  │  ├─ Task 1: annotate create_issue return type
│  │  │  └─ delta: {file, transforms, assertions, validate_with}
│  │  ├─ Task 2: annotate update_issue (depends on task 1)
│  │  └─ Task 3: annotate close_issue (depends on task 1)
│  └─ Phase: "Add type annotations to db_files.py"
│     └─ ...
│
VALIDATION (pre-flight, no disk changes)
│
├─ filigree validate-deltas <plan-id>
│  ├─ For each task in topological order:
│  │  ├─ Resolve language pack from file extension
│  │  ├─ Parse → query → simulate → assert
│  │  └─ Mark delta_status: green or red
│  └─ Report: "14/14 green" or "12 green, 2 red: [details]"
│
REVIEW (human inspects each atomic change)
│
├─ Dashboard or CLI shows each task's delta:
│  ├─ File, query match highlighted, before/after preview
│  ├─ Red/green status visible per task
│  └─ Human can edit, reorder, or reject individual tasks
│
EXECUTION (atomic all-or-nothing)
│
├─ filigree execute-deltas <plan-id>
│  ├─ Re-validate all (catch stale deltas)
│  ├─ Snapshot target files
│  ├─ Apply transforms in topological order
│  ├─ Run per-task assertions
│  ├─ Run language pack validators
│  ├─ ALL GREEN → close all tasks, advance phases
│  └─ ANY RED → rollback all files, report failure
│
DONE
```

## New Commands & MCP Tools

### CLI commands

- `filigree validate-deltas <plan-id>` — dry-run validation of all deltas in a plan
- `filigree execute-deltas <plan-id>` — atomic execution with rollback
- `filigree delta-status <plan-id>` — summary of green/red/stale/pending counts

### MCP tools

- `validate_deltas` — agent-callable validation
- `execute_deltas` — agent-callable execution
- `get_delta_status` — check plan readiness

## What This Replaces

| v1.x Component                          | Replaced By                         |
|-----------------------------------------|-------------------------------------|
| `.filigree/scanners/*.toml`             | Language pack validator definitions  |
| `scanners.py` TOML registry             | Language pack registry               |
| `scan_findings` table (SARIF blobs)     | AST assertion results (structured)   |
| `process_scan_results` SARIF ingestion  | Native assertion evaluation          |
| `file_records.language` field           | Language pack resolution by extension |

The `file_records` and `file_associations` tables survive — they remain useful for
tracking which files associate with which items. They get populated by the language
pack system instead of external scanners.

## Key Design Decisions

| Decision          | Choice                                       | Rationale                                    |
|-------------------|----------------------------------------------|----------------------------------------------|
| Target version    | filigree-next only                           | Clean slate, no legacy compromises           |
| Architecture      | Layered packs (workflow + language + extension) | Composes with existing pack model          |
| Parser foundation | tree-sitter                                  | 150+ languages, battle-tested, Python bindings |
| Transform format  | Tree-sitter queries + declarative operations | Position-independent, composable, reviewable |
| Execution model   | Atomic all-or-nothing                        | Deterministic — it either all lands or nothing does |
| Distribution      | Built-in only                                | Simpler, no package discovery overhead       |
| Validation        | AST assertions + language pack validators    | Structural checks + language-native tooling  |
| Initial languages | Python, Rust, C, JavaScript, HTML            | Covers primary use cases                     |

## Open Questions

- **Transform authoring UX** — how do agents/humans author the tree-sitter queries
  and operation args? A builder tool or template library would reduce friction.
- **Multi-file transforms** — a single logical change sometimes spans multiple files
  (e.g., rename a function and update all call sites). Should a task support
  multiple file deltas, or should these be modeled as linked tasks?
- **Conflict resolution** — when two tasks target the same AST node, the second
  transform is written assuming the first already applied. What tooling helps
  authors get this right?
- **Dashboard visualization** — how should the before/after diff and AST query
  match be rendered in the dashboard?
