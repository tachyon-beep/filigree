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
- **Conflict resolution** — when two tasks target the same AST node, the second
  transform is written assuming the first already applied. What tooling helps
  authors get this right?
- **Dashboard visualization** — how should the before/after diff and AST query
  match be rendered in the dashboard?

---

## Design Review Addendum (2026-02-25)

**Reviewed by:** 8-specialist panel (architect, systems engineer, Python specialist,
Rust specialist, quality engineer, SDLC specialist, security architect,
parser/compiler specialist). 3 rounds, full consensus achieved.

**Full transcript:** `2026-02-25-plugin-design-review-transcript.md`

### Resolved: Multi-file Transforms

Multi-delta-per-task. The delta schema changes to an array:

```json
{
  "deltas": [
    {"file": "src/core.py", "transforms": [...], "assertions": [...]},
    {"file": "src/__init__.py", "transforms": [...], "assertions": [...]},
    {"file": "tests/test_core.py", "transforms": [...], "assertions": [...]}
  ]
}
```

Single-file tasks use a one-element array. The execution engine processes `deltas[]`
uniformly. Within a task, all file deltas are applied atomically (snapshot all files
before any writes, restore all on any failure). Each `deltas[].file` is
independently path-validated; any invalid path rejects the entire task.

### Critical Design Revisions Required

The following changes were identified as must-haves before implementation begins.
These represent consensus findings from the 8-specialist review panel.

#### 1. Source Generation Model (CST, not AST)

Tree-sitter produces **concrete syntax trees (CSTs)**, not abstract syntax trees.
Critically, tree-sitter has **no AST-to-source serialization** — there is no way to
modify a tree node and produce valid source code from the modified tree.

Every operation must be implemented as a Python code module that:
1. Uses tree-sitter queries to locate target CST nodes (Query Layer)
2. Computes text edits (byte range + replacement string) using language-specific
   Python logic, including indentation handling (Edit Computation Layer)
3. Applies text edits to the source file via a shared edit engine (Edit Application
   Layer)

Operations are NOT declarative configs. The JSON definition in the language pack
declares the operation's **interface** (name, parameters); the **implementation** is
a Python class conforming to the `OperationHandler` protocol:

```python
class OperationHandler(Protocol):
    def validate_args(self, args: dict) -> list[str]:
        """Return validation errors. Empty = valid."""
        ...

    def compute_edit(self, source: bytes, tree: Tree, match: Node, args: dict) -> list[TextEdit]:
        """Compute text edits. Must not have side effects."""
        ...
```

**Implementation cost:** ~40 operation implementations (5 languages × ~8
operations), each 50-300 lines of Python. The Python language pack alone is
estimated at 800-1200 lines of operation code due to indentation-as-correctness.

**Formatter-as-normalizer:** Languages with deterministic formatters (Rust via
`rustfmt`, C via `clang-format`, JS via `prettier`) can generate syntactically
correct but unformatted code and run the formatter as a post-transform step. This
significantly simplifies operation handlers for those languages. Python cannot use
this approach because whitespace is semantic — the Python language pack requires an
explicit indentation engine.

**Hard invariant:** Every transform operates on a freshly-parsed CST from the
on-disk file. After any text edit, the previous CST is invalid due to shifted byte
offsets. Re-parse before every subsequent transform.

#### 2. Grammar/Toolchain Split

Language packs must be split into two concerns:

**Grammar pack** (built-in, pure, deterministic):
- Tree-sitter language reference and file extension mapping
- Named queries (reusable S-expression patterns)
- Operation handler implementations (Python OperationHandler classes)

**Toolchain config** (project-level, overridable):
- Validator commands per language (mypy, ruff, cargo check, etc.)
- Validator arguments, timeouts, and scoping (file vs package vs workspace)
- Required vs advisory distinction
- Output format parsing (e.g., `--message-format=json` for cargo)

This split resolves multiple issues:
- `{file}` placeholders don't work for cargo (operates on packages/workspaces)
- Projects may use pyright instead of mypy, or have custom validator configurations
- Eliminates command injection by design — delta authors reference validators by
  name, never by command template

Language packs provide **default validator recommendations** as documentation, but
the actual validator configuration is project-level.

#### 3. Execution Model: Phase-Level with Safety Invariants

The execution atomicity boundary is the **phase** (atomic within, sequential
across phases), not the plan:

- `execute-deltas <plan-id>` defaults to full-plan execution (all phases
  sequentially). `--phase=<id>` available for selective execution.
- Before executing each phase: re-validate ALL remaining phases' deltas against
  current disk state (catches cross-phase staleness). This includes file hash
  verification.
- All file snapshots for a phase taken before any writes within that phase.
- If a phase fails and rolls back, all subsequent phases are automatically marked
  `stale`. The user must re-validate before further execution.
- No auto-resume after rollback — explicit user action required to continue.

#### 4. No Lifecycle Hooks

Remove the `lifecycle_hooks` section from the extension pack definition. The
code-delta extension does not need a generic hook framework.

Instead:
- `validate-deltas` and `execute-deltas` are direct CLI/MCP commands
- These commands set the `delta_status` field on tasks as a side effect
- The existing transition gate mechanism reads `delta_status` to allow/block
  transitions (e.g., `{"type": "field_eq", "field": "delta_status", "value":
  "green"}`)
- This composes with filigree-next's existing gate evaluator without new machinery

Note: the `field_eq` and `all_linked_field_eq` gate conditions must be added to
the workflow extensibility design's gate vocabulary.

#### 5. Assertion Vocabulary Expansion

The current assertion vocabulary (`exists`, `not_exists`, `count:N`) is too coarse
to prevent false greens. Expand to:

- `exists` — a node matching the query exists (unchanged)
- `not_exists` — no node matches the query (unchanged)
- `count:N` — exactly N nodes match (unchanged)
- `text_equals` — the captured node's text matches an expected string
- `text_matches` — regex match on captured node text
- `parent_is` — the matched node has a specific parent node type

Assertions are explicitly **structural sanity checks**, not semantic correctness
proofs. They catch "the transform produced valid syntax" and "the target node
exists with the right content." Semantic correctness comes from toolchain validators
(mypy, cargo check).

#### 6. Security Requirements

**Must-have for v1.0:**

- **Path containment**: Every `delta.file` value passes through `_safe_path()` at
  authoring time AND execution time. Reject paths containing `..`, absolute paths,
  symlinks resolving outside project root, and any path under `.filigree/`.
- **Command safety**: All validator subprocess invocations use `shell=False` with
  list-of-strings args. Delta authors reference validators by name, never by
  command template.
- **Input validation**: Transform `args` validated against strict per-operation
  grammars (allowlists for identifiers, type expressions, module paths). Never
  `eval()` or `exec()` on args.
- **Content-addressed deltas**: Hash delta content at authoring, hash target files
  at validation. Verify both hashes at execution time. Any mismatch aborts.
- **Human execution gate**: `execute-deltas` requires a human actor. Agents can
  author, validate, and review, but cannot execute.
- **Built-in enforcement**: The registry loader rejects language and extension pack
  definitions from `.filigree/packs/` and `.filigree/templates/`. Only workflow
  packs support user-provided definitions.

#### 7. Lifecycle Integration

**Git integration:**
- Pre-execution check for clean working tree (or `--allow-dirty` flag)
- Record branch + commit SHA at validation time, detect drift at execution time
- Each successful phase execution can produce a git commit (optional auto-commit)
- Rollback via `git checkout -- <affected files>` as alternative to custom snapshots

**Audit trail — new event types:**
- `delta_validated`: plan_id, task_id, file, result, validator output
- `delta_executed`: plan_id, task_id, file, transforms applied, commit SHA
- `delta_failed`: plan_id, task_id, file, failure reason, rollback status
- `delta_stale`: plan_id, task_id, file, reason for staleness

#### 8. Validator Execution Contract

Each validator definition in the project toolchain config must include:

```json
{
  "command": "mypy",
  "args": ["--no-error-summary", "src/"],
  "scope": "project",
  "timeout_seconds": 120,
  "success_exit_codes": [0],
  "required": true,
  "fail_if_missing": true,
  "output_parser": "text"
}
```

- `scope`: `"file"`, `"package"`, `"project"`, or `"workspace"`
- `timeout_seconds`: hard timeout, exceeded = failure = rollback
- `required`: false = advisory-only, failure logged but doesn't block
- `fail_if_missing`: error if command not found (prevents silent skip = false green)
- `output_parser`: structured output parsing (e.g., `"cargo-json"` for
  `--message-format=json`)

#### 9. Scope Reductions

- **Defer `extract_function`** to v1.1 — requires semantic analysis (scope
  resolution, variable capture) beyond tree-sitter's capability.
- **Single-file `rename_symbol` only in v1.0** — cross-file rename requires
  `find_references` capability, defer to v1.1.
- **Scope HTML pack to pure HTML** — exclude embedded `<script>` JS, `<style>` CSS,
  and template languages (Jinja2, etc.) which require multi-language injection.

#### 10. Implementation Phasing

**Phase 1 — Declarative Extensions:**
Schema extensions (delta field, delta_status), transition gates (field_eq on
delta_status), multi-delta-per-task schema. Validates that extension packs compose
with filigree-next before building the execution engine.

**Phase 2 — Code Execution Engine:**
Operation handlers (OperationHandler protocol), grammar packs (tree-sitter
integration), toolchain config, validate-deltas/execute-deltas commands, snapshot/
rollback, security controls.

### Revised Key Design Decisions

| Decision          | Original                                      | Revised (Post-Review)                                   |
|-------------------|-----------------------------------------------|---------------------------------------------------------|
| Transform format  | Declarative operations                        | Python OperationHandler protocol (code, not config)     |
| Execution model   | Plan-level all-or-nothing                     | Phase-level atomic, plan-level sequential (with safety invariants) |
| Validator home    | Inside language packs                         | Project-level toolchain config (grammar/toolchain split) |
| Delta schema      | Single file per task                          | Multi-delta-per-task (`deltas[]` array)                 |
| Lifecycle hooks   | Extension pack hook declarations              | Removed — direct CLI/MCP commands + field-based gates   |
| Security model    | Not addressed                                 | Path jail, content-addressing, human execution gate     |
| extract_function  | Included in initial set                       | Deferred to v1.1                                        |
| Implementation    | Single phase                                  | Phase 1 declarative, Phase 2 execution engine           |
