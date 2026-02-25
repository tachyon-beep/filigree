# Plugin System & Language Packs — Design Document

**Date:** 2026-02-25
**Status:** Proposed (Revision 2.1 — invariant precision pass)
**Target:** filigree-next (clean-break architecture)
**Review:** 8-specialist panel, 3 rounds, full consensus
**Transcript:** `2026-02-25-plugin-design-review-transcript.md`

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
2. **Language-aware transforms** — CST-level operations that survive formatting
   changes, comment insertions, and unrelated edits.
3. **Mix-and-match language support** — a single project can enable Python, Rust, C,
   JavaScript, and HTML grammar packs simultaneously.
4. **Replace the scanner system** — toolchain validators subsume
   `.filigree/scanners/*.toml` and SARIF findings with native, structured
   alternatives.
5. **Compose with filigree-next** — use item_links for execution ordering and
   transition gates for readiness enforcement. No new execution machinery in core.

## Architecture: Layered Pack System

Three pack types compose to deliver the full system:

```
┌─────────────────────────────────────────────────────┐
│  Workflow Pack (existing)                            │
│  types, states, transitions, field schemas           │
│  e.g. "engineering", "editorial"                     │
├─────────────────────────────────────────────────────┤
│  Grammar Pack (new)                                  │
│  CST parser, operation handlers, named queries       │
│  e.g. "python", "rust", "c", "javascript", "html"   │
├─────────────────────────────────────────────────────┤
│  Extension Pack (new)                                │
│  schema extensions, references grammar packs         │
│  e.g. "code-delta", "coverage-tracking"              │
└─────────────────────────────────────────────────────┘

Separate from packs (project-level, not distributed):

┌─────────────────────────────────────────────────────┐
│  Toolchain Profile (.filigree/toolchain.toml)        │
│  validator commands, timeouts, scopes, overrides     │
│  project-specific, not part of any pack              │
└─────────────────────────────────────────────────────┘
```

### Composition via config

```json
{
  "packs": {
    "workflow": ["core", "planning", "release"],
    "grammar": ["python", "rust", "c", "javascript", "html"],
    "extensions": ["code-delta"]
  }
}
```

### Language routing

When the code-delta extension needs to parse or transform a file, it resolves the
grammar pack by file extension. Each grammar pack declares its extensions:

```json
{
  "pack": "python",
  "kind": "grammar",
  "file_extensions": [".py", ".pyi"],
  "tree_sitter_language": "python"
}
```

Multiple grammar packs coexist. The extension dispatches to the correct one per
file. If no grammar pack matches a file, the task is flagged as unresolvable (red).

### Distribution

All packs are built-in — they ship with filigree. No external plugin discovery or
third-party packaging. New languages require a filigree release. The registry loader
enforces this: language and extension pack definitions from `.filigree/packs/` or
`.filigree/templates/` are rejected. Only workflow packs support user-provided
definitions.

## Grammar Pack Structure

A grammar pack provides four capabilities for a set of file types: parsing,
querying, transforming, and formatting.

### Source generation model: CST, not AST

Tree-sitter produces **concrete syntax trees (CSTs)**, not abstract syntax trees.
Critically, tree-sitter has **no AST-to-source serialization** — there is no way to
modify a tree node and produce valid source code from the modified tree.

Every operation is implemented as Python code that produces **text edits** (byte
range + replacement string). The architecture has three layers:

1. **Query Layer** (tree-sitter, pure, fast) — match CST nodes via S-expression
   queries, return captures with byte ranges.
2. **Edit Computation Layer** (Python OperationHandler, pure, medium) — given a
   match and arguments, compute the text edits. Language-specific logic lives here
   (indentation, syntax rules, naming conventions).
3. **Edit Application Engine** (shared infrastructure) — apply text edits to source
   bytes, manage byte-offset shifts, detect conflicts, coordinate rollback.

**Hard invariant:** Every transform operates on a freshly-parsed CST from the
current source bytes. After any text edit, the previous CST is invalid due to
shifted byte offsets. Re-parse before every subsequent transform.

**Edit conflict rules:**

- Within a single `compute_edit()` call, a handler may return multiple `TextEdit`
  values. These edits are applied as a **batch**: they must not overlap (no two
  edits may cover overlapping byte ranges). Overlapping edits within a batch are
  illegal and abort the transform.
- Exception: identical replacements to identical byte ranges are deduplicated, not
  rejected.
- Within a batch, edits are applied in **descending `start_byte` order** so that
  earlier edits do not shift the byte offsets of later edits.
- Between transforms (across `compute_edit()` calls), the engine re-parses the
  source after each batch. The next transform sees a fresh CST with correct byte
  offsets. No cross-transform offset management is needed.

### OperationHandler protocol

Operations are Python code, not declarative configs. The grammar pack's JSON
definition declares each operation's **interface** (name, parameters); the
**implementation** is a Python class:

```python
class OperationHandler(Protocol):
    def validate_args(self, args: dict) -> list[str]:
        """Return validation errors. Empty list = valid."""
        ...

    def compute_edit(
        self, source: bytes, tree: Tree, match: Node, args: dict
    ) -> list[TextEdit]:
        """Compute text edits. Must not have side effects."""
        ...
```

Where `TextEdit` is:

```python
@dataclass(frozen=True)
class TextEdit:
    start_byte: int
    end_byte: int
    replacement: str
```

**Implementation cost:** ~40 operation implementations (5 languages × ~8
operations), each 50–300 lines of Python. The Python grammar pack alone is estimated
at 800–1200 lines of operation code due to indentation-as-correctness.

### Formatter-as-normalizer

Languages with deterministic formatters can generate syntactically correct but
unformatted code, then run the formatter as a post-transform step. This
significantly simplifies operation handlers.

This is a first-class field on grammar packs:

```json
{
  "pack": "rust",
  "kind": "grammar",
  "has_deterministic_formatter": true,
  "formatter": "rustfmt"
}
```

| Language   | Formatter        | Effect on operation handlers |
|------------|------------------|------------------------------|
| Rust       | `rustfmt`        | Generate valid syntax, skip formatting |
| C          | `clang-format`   | Same — format post-transform |
| JavaScript | `prettier`       | Same — format post-transform |
| Python     | None (semantic whitespace) | Must handle indentation in edit computation |
| HTML       | None (structural) | Must handle indentation in edit computation |

### Grammar pack definition format

```json
{
  "pack": "python",
  "kind": "grammar",
  "version": "1.0",
  "file_extensions": [".py", ".pyi"],
  "tree_sitter_language": "python",
  "has_deterministic_formatter": false,

  "operations": {
    "add_type_annotation":   {"params": ["node_query", "type_expr"]},
    "rename_symbol":         {"params": ["node_query", "new_name"]},
    "add_parameter":         {"params": ["function_query", "name", "type_expr", "default"]},
    "remove_parameter":      {"params": ["function_query", "name"]},
    "add_decorator":         {"params": ["function_query", "decorator_expr"]},
    "add_import":            {"params": ["module", "names"]},
    "remove_import":         {"params": ["module", "names"]}
  },

  "queries": {
    "all_functions":     "(function_definition name: (identifier) @name)",
    "all_classes":       "(class_definition name: (identifier) @name)",
    "all_imports":       "(import_statement) @import",
    "untyped_params":    "(function_definition parameters: (parameters (identifier) @param))"
  }
}
```

Note: `extract_function` is deferred to v1.1 — it requires semantic analysis (scope
resolution, variable capture) beyond tree-sitter's capability.

### Design principles

1. **Operations are per-language.** The operation name (`add_type_annotation`) is
   universal; the implementation is language-specific. Python produces `x: int`,
   Rust produces `x: i32`, C uses a different syntax entirely.

2. **Named queries are reusable.** Both transforms and assertions can reference
   them. "Find all untyped parameters" is a query; "add type annotations to all
   untyped parameters" is a transform that uses that query.

3. **Tree-sitter is the foundation.** Battle-tested, 150+ languages, Python bindings
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

4. **Grammar packs are pure.** No shell commands, no environment dependencies, no
   I/O beyond reading source files. Toolchain integration is separate.

### Initial grammar packs

| Pack         | Extensions             | Formatter          | Notes                                   |
|--------------|------------------------|--------------------|-----------------------------------------|
| `python`     | `.py`, `.pyi`          | None (semantic ws) | Most complete — primary language          |
| `rust`       | `.rs`                  | `rustfmt`          | Cargo-workspace aware                    |
| `c`          | `.c`, `.h`             | `clang-format`     | Minimal operation set initially          |
| `javascript` | `.js`, `.mjs`, `.cjs`  | `prettier`         | Could expand to `.jsx`                   |
| `html`       | `.html`, `.htm`        | None               | Pure HTML only — no embedded JS/CSS/templates |

## Toolchain Profile

Toolchain configuration is **project-level**, not distributed with grammar packs.
This eliminates command injection by design: delta authors reference validators by
name, never by command template.

Grammar packs provide **default validator recommendations** as documentation only.
The actual validator configuration lives in the project's toolchain profile.

### Validator contract

Each validator definition specifies an execution contract:

```toml
# .filigree/toolchain.toml

[validators.python-typecheck]
command = "mypy"
args = ["--no-error-summary", "src/"]
scope = "project"
timeout_seconds = 120
success_exit_codes = [0]
required = true
fail_if_missing = true
output_parser = "text"
env_allowlist = ["VIRTUAL_ENV", "PATH", "MYPYPATH"]
cwd = "project_root"

[validators.python-lint]
command = "ruff"
args = ["check", "{file}"]
scope = "file"
timeout_seconds = 30
success_exit_codes = [0]
required = true
fail_if_missing = true
output_parser = "text"

[validators.rust-check]
command = "cargo"
args = ["check", "--message-format=json"]
scope = "workspace"
timeout_seconds = 300
success_exit_codes = [0]
required = true
fail_if_missing = true
output_parser = "cargo-json"
cwd = "workspace_root"
```

### Validator contract fields

| Field               | Type            | Description |
|---------------------|-----------------|-------------|
| `command`           | string          | Executable name (resolved via PATH) |
| `args`              | list[string]    | Arguments. `{file}` placeholder available for file-scoped validators only |
| `scope`             | enum            | `"file"`, `"package"`, `"project"`, or `"workspace"` |
| `timeout_seconds`   | int             | Hard timeout. Exceeded = failure = rollback |
| `success_exit_codes`| list[int]       | Exit codes treated as success (default: `[0]`) |
| `required`          | bool            | `true` = must pass, `false` = advisory (logged, doesn't block) |
| `fail_if_missing`   | bool            | `true` = error if command not found (prevents silent false green) |
| `output_parser`     | string          | `"text"`, `"json"`, `"cargo-json"`, `"sarif"` |
| `env_allowlist`     | list[string]    | Environment variables passed to subprocess (all others stripped) |
| `cwd`               | string          | Working directory: `"project_root"`, `"workspace_root"`, or `"file_parent"` |

### Scope model

The `{file}` placeholder is only valid for `scope = "file"` validators. Cargo,
mypy (in strict mode), and similar tools operate at project/workspace scope — they
never accept individual file targets. The scope field makes this explicit rather
than papering over it with broken placeholders.

**v1.0 scopes** (no parameterized placeholders beyond `{file}`):

| Scope       | When run                          | `{file}` valid? | Example            |
|-------------|-----------------------------------|-----------------|--------------------|
| `file`      | Per file touched by transforms    | Yes             | `ruff check {file}` |
| `project`   | Once per project                  | No              | `mypy src/`        |
| `workspace` | Once per workspace                | No              | `cargo check`      |

**Deferred to v1.1:** `package` scope with `{package}` placeholder. This requires
workspace/package discovery logic per language (Cargo workspace membership, npm
workspaces, Python namespace packages) and is substantial enough to warrant its own
design pass.

### Two-tiered validation

Validation happens in two tiers with different performance characteristics:

1. **Structural validation** (fast, always runs) — CST assertions evaluate whether
   the transform produced syntactically valid output and the expected nodes exist
   with correct content.
2. **Semantic validation** (slow, runs after structural) — toolchain validators
   (mypy, cargo check) verify that the code is semantically correct.

This ordering prevents slow validator runs on structurally broken code.

## Extension Pack: Code Delta

The `code-delta` extension pack wires grammar packs into the workflow lifecycle. It
adds schema to task-level items and provides commands for validation and execution.

### Definition format

```json
{
  "pack": "code-delta",
  "kind": "extension",
  "version": "2.0",
  "requires_grammar_pack": true,

  "schema_extensions": {
    "applicable_types": ["task", "step"],
    "fields": [
      {
        "name": "delta",
        "type": "object",
        "schema": "See: Delta Schema below"
      },
      {
        "name": "delta_status",
        "type": "enum",
        "options": ["pending", "green", "red", "stale"],
        "default": "pending",
        "derived": true
      }
    ]
  },

  "commands": {
    "validate-deltas": "Validate all deltas in a plan/phase against current disk state",
    "execute-deltas": "Apply all deltas atomically with rollback",
    "delta-status": "Report green/red/stale/pending counts for a plan/phase"
  },

  "gates": {
    "field_set": {"field": "delta"},
    "field_eq": {"field": "delta_status", "value": "green"},
    "all_linked_field_eq": {"link_type": "parent", "direction": "inbound",
                            "field": "delta_status", "value": "green"}
  }
}
```

No lifecycle hooks. Validation and execution are explicit commands (CLI + MCP), not
callbacks. The existing transition gate mechanism reads `delta_status` to
allow/block state transitions.

### Delta schema

A task's delta contains one or more **elements**, each targeting a single file.
Multi-file tasks use multiple elements. Single-file tasks use a one-element array.

```json
{
  "delta": {
    "elements": [
      {
        "path": "src/filigree/core.py",
        "language": "python",
        "transforms": [
          {
            "operation": "add_type_annotation",
            "query": "(function_definition name: (identifier) @fn (#eq? @fn \"create_issue\"))",
            "args": {"type_expr": "Issue"}
          }
        ],
        "assertions": [
          {
            "query": "(function_definition name: (identifier) @fn (#eq? @fn \"create_issue\") return_type: (type) @rt)",
            "expect": "exists"
          },
          {
            "capture": "@rt",
            "expect": "capture_equals",
            "value": "Issue"
          }
        ],
        "validate_with": ["python-typecheck", "python-lint"],
        "pre_hash": "sha256:a1b2c3...",
        "post_hash": "sha256:d4e5f6..."
      }
    ],
    "digest": "sha256:abc123..."
  }
}
```

**Field descriptions:**

| Field          | Description |
|----------------|-------------|
| `elements[]`   | Array of per-file deltas. Processed uniformly; single-file = one element |
| `.path`        | Relative path from project root. Must pass `_safe_path()` validation |
| `.language`    | Grammar pack name (optional — inferred from extension if omitted) |
| `.transforms`  | Ordered list of operations to apply |
| `.assertions`  | Post-transform structural checks |
| `.validate_with` | Named validators from the toolchain profile |
| `.pre_hash`    | SHA-256 of file content at validation time (TOCTOU resistance) |
| `.post_hash`   | SHA-256 of expected file content after simulated edits (optional) |
| `digest`       | SHA-256 of the canonical delta JSON (content addressing for audit + immutability) |

**`delta_status` is a derived cache**, not a source of truth. Its value is
determined by running validation against the current disk state + file hashes. The
cache is invalidated when:
- Any `pre_hash` no longer matches the file on disk → `stale`
- The `digest` doesn't match the stored delta content → `stale`
- Validation fails → `red`
- Validation succeeds and all hashes match → `green`

Each `elements[].path` is independently validated by `_safe_path()`. Any invalid
path rejects the entire task's delta.

### Assertion vocabulary

Assertions are **structural sanity checks**, not semantic correctness proofs. They
catch "the transform produced valid syntax" and "the target node exists with the
right content." Semantic correctness comes from toolchain validators.

| Assertion         | Description | Example |
|-------------------|-------------|---------|
| `exists`          | A node matching the query exists | `{"query": "...", "expect": "exists"}` |
| `not_exists`      | No node matches the query | `{"query": "...", "expect": "not_exists"}` |
| `count_eq`        | Exactly N nodes match | `{"query": "...", "expect": "count_eq", "value": 3}` |
| `count_gte`       | At least N nodes match | `{"query": "...", "expect": "count_gte", "value": 1}` |
| `capture_equals`  | Named capture's text equals expected | `{"capture": "@rt", "expect": "capture_equals", "value": "Issue"}` |
| `capture_regex`   | Named capture's text matches regex | `{"capture": "@name", "expect": "capture_regex", "value": "^test_.*"}` |
| `node_text_contains` | Bounded substring check on matched node | `{"query": "...", "expect": "node_text_contains", "value": "return"}` |

**Capture text semantics:** Tree-sitter captures are byte ranges into the source
buffer. "Capture text" is defined as the **raw source slice decoded as UTF-8**, with
no trimming, no whitespace normalization, and no line-ending conversion. If a
capture includes leading indentation, the text includes that indentation. If
trimming is needed, use `capture_regex` with an appropriate pattern. This avoids
implicit normalization that silently changes assertion behavior across platforms.

### Confidence levels

Delta validation produces a confidence level reflecting how thoroughly the change
has been verified:

| Level         | Meaning | What passed |
|---------------|---------|-------------|
| `structural`  | Query-level checks only | CST assertions passed |
| `validated`   | Simulated edits verified | Assertions + simulated transforms passed |
| `tested`      | Full validation complete | Assertions + transforms + toolchain validators passed |

The UI surfaces this — a delta can be "green but only structural" which is
meaningfully different from "green and fully tested."

### Delta status lifecycle

```
pending ──(validate)──→ green ──(file hash changes)──→ stale
   │                      │                                │
   └──(validate fails)──→ red ←──(validate fails)─────────┘
                          │
                          └──(fix + revalidate)──→ green
```

Transitions between states are driven by the `validate-deltas` and `execute-deltas`
commands, never by hooks. The `delta_status` field is set as a side effect of these
commands.

## Execution Model: Phase-Level Atomic

Execution atomicity is at the **phase** level — atomic within a phase, sequential
across phases. This is the "large refactors are survivable" answer without
accepting untracked partial chaos.

### Phase execution flow

```
1. PRE-FLIGHT
   ├── Acquire execution lock (process-level file lock)
   ├── Check working tree clean (or --allow-dirty)
   ├── Record branch + commit SHA (baseline)
   └── Journal: execution_started

2. FOR EACH PHASE (sequential):

   2a. VALIDATE (read-only, no disk changes)
       ├── Re-validate ALL remaining phases against current disk
       ├── Verify file hashes match pre_hash on every element
       ├── Verify delta digests match stored content
       ├── For each task in topological order:
       │   ├── Resolve grammar pack from file extension
       │   ├── Parse current file → CST via tree-sitter
       │   ├── Simulate transforms in memory
       │   └── Run assertions against simulated result
       ├── Journal: phase_validated
       └── Any red? → abort phase, mark remaining phases stale

   2b. SNAPSHOT
       ├── Record baseline for all files in this phase
       ├── Backend A (preferred): git stash or branch checkpoint
       └── Backend B (fallback): copy files to temp directory

   2c. EXECUTE (atomic within phase)
       ├── For each task in topological order:
       │   ├── Apply transforms to disk (re-parse CST after each edit)
       │   ├── Run post-transform assertions
       │   ├── Journal: file_written (per file)
       │   └── If assertions fail → ROLLBACK, abort
       ├── Run language-specific formatter if has_deterministic_formatter
       ├── Run toolchain validators (two-tiered: structural then semantic)
       │   ├── Journal: validator_started / validator_passed / validator_failed
       │   └── If any required validator fails → ROLLBACK, abort
       ├── Journal: phase_committed
       └── Optional: git commit (--auto-commit flag)

   2d. ON FAILURE → ROLLBACK
       ├── Restore all files from snapshot
       ├── Mark failing task as "red"
       ├── Mark ALL subsequent phases as "stale" (systems amendment)
       ├── Journal: phase_rolled_back
       └── No auto-resume — user must re-validate and explicitly retry

3. COMPLETION
   ├── All phases committed → close tasks, advance phase states
   ├── Journal: execution_completed
   └── Release execution lock
```

### Default behaviour

`execute-deltas <plan-id>` runs all phases sequentially (full-plan execution).
`--phase=<id>` is available for selective single-phase execution. Phase-level
execution is the natural boundary — it maps to a commit, a review unit, and a
rollback unit.

### Execution journal

Even with git-as-rollback, a journal is needed for crash recovery and audit:

| Event                | Fields |
|----------------------|--------|
| `execution_started`  | plan_id, actor, timestamp, baseline_sha |
| `phase_validated`    | plan_id, phase_id, result, file_hashes |
| `file_written`       | plan_id, task_id, file_path, edit_count |
| `validator_started`  | plan_id, validator_name, scope |
| `validator_passed`   | plan_id, validator_name, duration_ms |
| `validator_failed`   | plan_id, validator_name, exit_code, output_snippet |
| `phase_committed`    | plan_id, phase_id, commit_sha |
| `phase_rolled_back`  | plan_id, phase_id, reason, files_restored |
| `execution_completed`| plan_id, phases_committed, phases_failed |

### Mutual exclusion

A process-level file lock (using `portalocker`, already a dependency) prevents
concurrent delta executions. Only one `execute-deltas` process can run at a time
per project. The lock is held for the duration of execution and released on
completion or crash.

### Rollback backends

Rollback is a selectable backend. Both implement the same `SnapshotBackend`
interface: `create(file_list) → handle`, `restore(handle)`, `discard(handle)`.

| Backend       | When used | Mechanism |
|---------------|-----------|-----------|
| **git** (preferred) | Repo is clean, git available | See exact contract below |
| **snapshot** (fallback) | Git unavailable or `--no-git` | Copy affected files to temp directory; rollback via file restore |

**Git backend exact contract:**

1. **Pre-flight:** Require clean working tree (`git status --porcelain` is empty).
   If dirty and `--allow-dirty` is set, refuse — unrelated uncommitted changes
   make rollback unsafe because `git restore` would discard them. (`--allow-dirty`
   only skips the cleanliness check for *untracked* files, not for modified tracked
   files.)
2. **Snapshot:** Record `HEAD` SHA as `baseline_sha`. No branch creation needed.
3. **On phase commit (success):** If `--auto-commit`, run
   `git add <affected_files> && git commit -m "filigree: <phase_name>"`.
   Otherwise, leave changes staged but uncommitted.
4. **On rollback (failure):** Run `git restore --source <baseline_sha> -- <filelist>`
   to restore exactly the affected files without touching the rest of the tree.
5. **Discard:** No-op (baseline SHA requires no cleanup).

Git is preferred but not mandatory. The execution engine auto-selects: if `git
rev-parse --is-inside-work-tree` succeeds and the tree is clean, use git; otherwise
fall back to snapshot.

### Staleness detection

If a file changes between validation and execution (TOCTOU), the `pre_hash` will no
longer match. The execute phase always re-validates before writing — any hash
mismatch immediately aborts. Additionally, the delta `digest` is verified to ensure
the delta content itself hasn't been tampered with.

## Security

Security is not an afterthought — it's an explicit invariant enforced at every
layer.

### Path jail (core filigree-next invariant)

This applies beyond the plugin system to all file operations in filigree-next:

- All file paths must be **relative to project root**
- Normalise and **reject any path escaping root** (`..`, absolute paths, symlinks
  resolving outside)
- **Exclude** `.filigree/` and optionally `.git/` and other sensitive directories
- Validate per delta element; reject whole task on any invalid element
- Implemented as `_safe_path()` called at authoring time AND execution time (defense
  in depth — even if authoring validation is bypassed, execution re-checks)

### Content-addressed deltas

- `delta.digest` = SHA-256 of the canonical delta JSON payload
- Each `elements[].pre_hash` = SHA-256 of file content at validation time
- Each `elements[].post_hash` = SHA-256 of expected content after simulated edits
- At execution: verify `digest` matches stored delta, verify `pre_hash` matches
  current file. Any mismatch → abort
- Provides: tamper detection, TOCTOU resistance, audit immutability

**Canonicalization rules for `digest`:**

The digest covers the **intent** (transforms, assertions, validators, paths) but
not the **observations** (pre_hash, post_hash — these change on revalidation).

1. Construct a JSON object containing only: `elements[].path`,
   `elements[].language` (always included, even if inferred — otherwise digest
   changes depending on inference), `elements[].transforms`,
   `elements[].assertions`, `elements[].validate_with`
2. Serialize with: sorted keys, no whitespace (`separators=(',', ':')` in Python),
   UTF-8 encoding. No floating-point values permitted in delta payloads (all
   numeric values are integers).
3. `digest` = `sha256:<hex of UTF-8 bytes>`

This prevents "digest mismatch" ghosts caused by key ordering or whitespace
differences across serializers.

### Command safety

- All validator subprocess invocations use `shell=False` with list-of-strings args
- Delta authors reference validators **by name** from the toolchain profile, never
  by command template
- Moving validator commands out of pack-distributed content eliminates a whole class
  of command injection risk by design

### Input validation

- Transform `args` validated against strict per-operation grammars
- Allowlists for identifiers, type expressions, module paths
- Never `eval()` or `exec()` on args
- Strict argument typing enforced by `OperationHandler.validate_args()`

### Execution authorization

Agents nominally have unrestricted access to the execution environment, so
`execute-deltas` does not enforce a human-only gate. The agent executes when
directed by the human. The security boundary is the content-addressing and path
jail — not actor identity.

In interactive CLI mode, `execute-deltas` prompts for confirmation before writing
(`--yes` to skip). In MCP mode, the tool is available without additional gating —
the human's decision to invoke the agent constitutes authorization.

### Dependency graph as attack surface

- Don't blindly trust the topological sort — validate that the dependency graph
  itself doesn't create circular dependencies or force unexpected execution ordering
- Validate graph integrity before execution

## Integration with filigree-next

The code-delta extension adds no new execution machinery to core. It composes with
filigree-next's existing item_links and transition gates.

### Execution ordering via item_links

The topological sort for delta execution is a query over the existing item_links
`blocks` relationships. No separate dependency graph needed.

### Commands replace hooks

No generic hook framework. The code-delta extension provides three explicit commands:

| Command | CLI | MCP | Description |
|---------|-----|-----|-------------|
| `validate-deltas` | `filigree validate-deltas <plan-id>` | `validate_deltas` | Dry-run validation, sets delta_status |
| `execute-deltas` | `filigree execute-deltas <plan-id>` | `execute_deltas` | Atomic execution with rollback |
| `delta-status` | `filigree delta-status <plan-id>` | `get_delta_status` | Green/red/stale/pending counts + confidence |

These commands set `delta_status` as a side effect. The gate evaluator reads
`delta_status` — no coupling between commands and the gate system.

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
# "parent" with direction "inbound" means "all items whose parent is this phase"
"transitions": [
  {
    "from": "active",
    "to": "executing",
    "enforcement": "hard",
    "gates": [
      {"type": "all_linked_field_eq", "link_type": "parent",
       "direction": "inbound",
       "field": "delta_status", "value": "green"}
    ]
  },
  {
    "from": "executing",
    "to": "completed",
    "enforcement": "hard",
    "gates": [
      {"type": "all_linked", "link_type": "parent",
       "direction": "inbound",
       "condition": {"status_category": "done"}}
    ]
  }
]
```

Note: `field_eq` and `all_linked_field_eq` gate types must be added to the
workflow extensibility design's gate vocabulary.

### Workflow

1. Author tasks with deltas (status: `in_progress`)
2. `validate-deltas` → delta_status goes `green` (transition to `ready_to_land`)
3. All tasks green → phase can transition to `executing` (gate passes)
4. `execute-deltas` → tasks close → phase transitions to `completed`

## End-to-End Data Flow

```
AUTHORING (agent or human creates the plan)
│
├─ filigree create-plan with code-delta extension enabled
│  ├─ Phase: "Add type annotations to core.py"
│  │  ├─ Task 1: annotate create_issue return type
│  │  │  └─ delta: {elements: [{path, transforms, assertions, validate_with}]}
│  │  ├─ Task 2: annotate update_issue (depends on task 1)
│  │  └─ Task 3: annotate close_issue (depends on task 1)
│  └─ Phase: "Add type annotations to db_files.py"
│     └─ ...
│  Delta digest computed and stored at authoring time.
│
VALIDATION (pre-flight, no disk changes)
│
├─ filigree validate-deltas <plan-id>
│  ├─ For each phase, for each task in topological order:
│  │  ├─ Verify delta digest matches stored content
│  │  ├─ Resolve grammar pack from file extension
│  │  ├─ Compute file hash, store as pre_hash
│  │  ├─ Parse → query → simulate → assert
│  │  └─ Mark delta_status: green (with confidence level) or red
│  └─ Report: "14/14 green (12 tested, 2 structural)"
│
REVIEW (human inspects each atomic change)
│
├─ Dashboard or CLI shows each task's delta:
│  ├─ File, query match highlighted, before/after preview
│  ├─ Red/green status + confidence level visible per task
│  └─ Human can edit, reorder, or reject individual tasks
│
EXECUTION (phase-level atomic, plan-level sequential)
│
├─ filigree execute-deltas <plan-id>
│  ├─ Acquire execution lock
│  ├─ For each phase:
│  │  ├─ Re-validate all remaining phases (hash + digest verification)
│  │  ├─ Snapshot phase files (git or file-copy backend)
│  │  ├─ Apply transforms in topological order
│  │  ├─ Run per-task assertions
│  │  ├─ Run formatter (if has_deterministic_formatter)
│  │  ├─ Run toolchain validators (structural then semantic)
│  │  ├─ ALL GREEN → commit phase, journal event
│  │  └─ ANY RED → rollback phase, mark subsequent phases stale, abort
│  └─ Release execution lock
│
DONE
```

## What This Replaces

| v1.x Component                          | Replaced By                               |
|-----------------------------------------|-------------------------------------------|
| `.filigree/scanners/*.toml`             | Toolchain profile validators              |
| `scanners.py` TOML registry             | Grammar pack registry                     |
| `scan_findings` table (SARIF blobs)     | CST assertion results (structured)        |
| `process_scan_results` SARIF ingestion  | Native assertion evaluation               |
| `file_records.language` field           | Grammar pack resolution by extension      |

The `file_records` and `file_associations` tables survive — they remain useful for
tracking which files associate with which items. They get populated by the grammar
pack system instead of external scanners.

## Key Design Decisions

| Decision          | Choice                                                    | Rationale                                                     |
|-------------------|-----------------------------------------------------------|---------------------------------------------------------------|
| Target version    | filigree-next only                                        | Clean slate, no legacy compromises                            |
| Architecture      | Layered packs (workflow + grammar + extension)             | Composes with existing pack model                             |
| Parser foundation | tree-sitter (CST)                                         | 150+ languages, battle-tested, Python bindings                |
| Source generation | Text edits via OperationHandler (code, not config)         | CST has no serialization — operations must produce text edits |
| Grammar/toolchain | Split: grammar packs (pure) + toolchain profile (project)  | Eliminates command injection; supports project-specific tools |
| Delta schema      | Multi-element per task with content addressing              | Multi-file changes without task noise; tamper/TOCTOU resistance |
| Execution model   | Phase-level atomic, plan-level sequential                  | Survivable large refactors with safety invariants             |
| Lifecycle         | Explicit commands + field-based gates (no hooks)           | No untestable mini-runtime; composes with existing gates      |
| Security          | Path jail, content-addressing, shell=False, confirmation prompt | Defense in depth at every layer                            |
| Rollback          | Git-preferred, snapshot-fallback                           | Git when available, degrade gracefully                        |
| Distribution      | Built-in only                                              | No package discovery overhead, security boundary              |
| Validation        | Two-tiered: structural (CST) then semantic (toolchain)     | Fast feedback on broken syntax before slow validators         |
| Initial languages | Python, Rust, C, JavaScript, HTML                          | Covers primary use cases                                      |
| Formatters        | First-class field on grammar packs                         | Explicit per-language pipeline, not implicit assumption        |

## Staged Delivery

### v1.0 — Declarative + Safe Execution

**Non-goal:** v1.0 does not ship a query builder or visual authoring tool for
tree-sitter queries. It ships a small query template library per grammar pack and
expects authors to write queries directly.

- Grammar packs (Python, Rust, C, JavaScript, HTML)
- Minimal operation set: `rename_symbol` (single-file), `add_import`,
  `remove_import`, `add_type_annotation` (where feasible), `add_parameter`,
  `remove_parameter`, `add_decorator`
- Multi-element delta schema with content addressing
- Assertion vocabulary: `exists`, `not_exists`, `count_eq`, `count_gte`,
  `capture_equals`, `capture_regex`, `node_text_contains`
- Confidence levels (structural / validated / tested)
- Validator contract + toolchain profile (TOML)
- Phase-level atomic execution with re-validation and rollback
- Execution journal + mutual exclusion
- Git-preferred rollback backend
- Interactive confirmation prompt (CLI) / agent-directed execution (MCP)
- Path jail invariant
- Audit events (delta lifecycle)

### v1.1+

- `extract_function` and other high-risk refactors
- Cross-file `rename_symbol` (requires `find_references`)
- More powerful assertions (cross-file consistency, plan-level)
- Richer git integration (PR creation, branch management)
- Optional CI/CD automation
- HTML embedded language support (`<script>`, `<style>`, templates)
- Full RBAC authorization model
- Signed deltas

## Open Questions

- **Transform authoring UX** — how do agents/humans author tree-sitter queries
  and operation args? A builder tool or template library would reduce friction.
- **Conflict resolution** — when two tasks target the same CST node, the second
  transform assumes the first already applied. What tooling helps authors get this
  right?
- **Dashboard visualization** — how should the before/after diff and CST query
  match be rendered in the dashboard?
- **Cross-file assertion model** — plan-level consistency assertions across files
  (deferred, but needs design before v1.1)
- **Python indentation engine** — specification needed for correctness-grade
  indentation handling in the Python grammar pack
- **Rust workspace discovery** — `Cargo.toml` workspace detection and package
  resolution for workspace-scoped validators

---

## Change Log

### Revision 2 (2026-02-25) — Post-Review Consolidation

Incorporates all findings from the 8-specialist panel review and subsequent
design feedback. Changes listed by topic:

**A) Delta schema rewrite (multi-element, content-addressed, fingerprinted)**
- Replaced single `delta` object with `delta.elements[]` array
- Added per-element `pre_hash` and `post_hash` for TOCTOU resistance
- Added `delta.digest` for content addressing (audit + immutability)
- Made `delta_status` explicitly a derived cache with invalidation rules
- Previous: single `{file, transforms, assertions}` object

**B) Assertion vocabulary expansion (fix false greens)**
- Added `count_eq`, `count_gte`, `capture_equals`, `capture_regex`,
  `node_text_contains`
- Added confidence levels: structural / validated / tested
- Previous: only `exists`, `not_exists`, `count:N`

**C) Validator contract specification**
- Added full validator execution contract: scope, timeout, required, environment,
  failure semantics, output parser
- Documented scope model (file/package/project/workspace) with `{file}` validity
- Added two-tiered validation ordering (structural before semantic)
- Previous: validators embedded in language pack as `{command, args}` pairs

**D) Execution engine hardening (journal + mutual exclusion + backends)**
- Added execution journal (event table for crash recovery and audit)
- Added process-level file lock via portalocker
- Made rollback backend selectable (git preferred, snapshot fallback)
- Previous: unspecified snapshot mechanism, no journal, no locking

**E) Grammar/toolchain split**
- Renamed "language pack" → "grammar pack" throughout
- Extracted toolchain config to project-level `.filigree/toolchain.toml`
- Added `has_deterministic_formatter` as first-class grammar pack field
- Grammar packs are now pure (no shell commands, no environment dependencies)
- Previous: validators embedded inside language pack definitions

**F) Lifecycle hooks removed**
- Deleted `lifecycle_hooks` from extension pack definition
- Replaced with explicit commands (`validate-deltas`, `execute-deltas`,
  `delta-status`) + field-based transition gates
- Previous: `on_plan_validate` / `on_plan_execute` hook declarations

**G) Path jail as core filigree-next invariant**
- Elevated path containment from a delta-specific concern to a system-wide
  invariant applying to all file operations
- Documented defense-in-depth: validate at authoring AND execution
- Previous: not explicitly addressed

**Additional changes:**
- Updated architecture diagram (grammar pack, no hooks)
- Added execution model detail (phase-level flow, staleness, re-validation)
- Added security as a dedicated top-level section
- Rewrote execution model from plan-level to phase-level atomic
- Added staged delivery roadmap (v1.0 / v1.1+)
- Added open questions from panel (indentation engine, workspace discovery,
  cross-file assertions)
- Moved source generation model (CST not AST) into grammar pack section
- Added OperationHandler protocol with TextEdit dataclass

### Revision 2.1 (2026-02-25) — Invariant Precision Pass

Addresses reviewer feedback on remaining ambiguity traps. No design changes;
only invariant specifications and terminology fixes.

1. **Delta digest canonicalization** — added concrete serialization rules (sorted
   keys, no whitespace, UTF-8, no floats; digest covers intent not observations)
2. **Capture text semantics** — defined as raw UTF-8 source slice, no trimming
3. **Edit conflict rules** — overlapping edits in a batch are illegal; descending
   start_byte application order; identical-range dedup
4. **`contains` → `parent`** — replaced all `contains` link type references with
   `parent` (inbound direction) to match filigree-next's link type vocabulary
5. **`{package}` scope deferred** — removed from v1.0 scope table; package
   discovery requires per-language design, deferred to v1.1
6. **Git backend exact contract** — pinned to: require clean tree, record HEAD SHA,
   `git restore --source` for rollback, defined `--allow-dirty` semantics
7. **Human execution gate removed** — agents execute when directed by humans;
   security boundary is content-addressing and path jail, not actor identity
8. **Code example syntax normalized** — JSON data blocks use `json` fence, Python
   code blocks use `python` fence
9. **v1.0 non-goal** — explicitly states no query builder ships in v1.0

### Revision 2 (2026-02-25) — Post-Review Consolidation

Incorporates all findings from the 8-specialist panel review and subsequent
design feedback. Committed at `9ec456e`. See above for full change list.

### Revision 1 (2026-02-25) — Initial Design

Original design document covering pack taxonomy, language pack structure, code-delta
extension, execution model, and filigree-next integration. Committed at `bf740e9`.
