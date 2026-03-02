# Plugin System & Language Packs — Design Document

**Date:** 2026-02-25
**Status:** Proposed (Revision 3 — final review fixes)
**Target:** filigree-next (clean-break architecture)
**Review:** 8-specialist panel (Rev 2) + 4-specialist final review (Rev 3)
**Transcript:** `2026-02-25-plugin-design-review-transcript.md`
**Final review:** `2026-02-25-plugin-final-review-annex.md`

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
3. **Mix-and-match language support** — a single project can enable multiple grammar
   packs simultaneously (e.g., Python + JavaScript + CSS + HTML).
4. **Replace the scanner system** — toolchain validators subsume
   `.filigree/scanners/*.toml` and SARIF findings with native, structured
   alternatives.
5. **Compose with filigree-next** — use item_links for execution ordering and
   transition gates for readiness enforcement. No new execution machinery in core.

## Architecture: Layered Pack System

Two pack types plus a built-in feature module compose to deliver the full system:

```
┌─────────────────────────────────────────────────────┐
│  Workflow Pack (existing)                            │
│  types, states, transitions, field schemas           │
│  e.g. "engineering", "editorial"                     │
├─────────────────────────────────────────────────────┤
│  Grammar Pack (new)                                  │
│  CST parser, operation handlers, named queries       │
│  e.g. "python", "javascript", "css", "html"          │
├─────────────────────────────────────────────────────┤
│  Code-Delta Module (new, built-in)                   │
│  schema extensions, delta lifecycle, execution engine │
│  composes grammar packs into workflow                 │
└─────────────────────────────────────────────────────┘

Separate from packs (project-level, not distributed):

┌─────────────────────────────────────────────────────┐
│  Toolchain Profile (.filigree/toolchain.toml)        │
│  validator commands, timeouts, scopes, overrides     │
│  project-specific, not part of any pack              │
└─────────────────────────────────────────────────────┘
```

The code-delta module is a built-in feature, not a pack. Unlike workflow and
grammar packs, there is no "extension pack" abstraction — code-delta is the only
consumer of grammar packs, and building a generic extension system for one concrete
use case would be premature. If a second grammar-pack consumer materializes in the
future, the abstraction can be extracted then.

### Composition via config

```json
{
  "packs": {
    "workflow": ["core", "planning", "release"],
    "grammar": ["python", "javascript", "css", "html"]
  },
  "features": {
    "code_delta": true
  }
}
```

### Language routing

When the code-delta module needs to parse or transform a file, it resolves the
grammar pack by file extension. Each grammar pack declares its extensions:

```json
{
  "pack": "python",
  "kind": "grammar",
  "file_extensions": [".py", ".pyi"],
  "tree_sitter_language": "python"
}
```

Multiple grammar packs coexist. The code-delta module dispatches to the correct one
per file. If no grammar pack matches a file, the task is flagged as unresolvable
(red). If multiple grammar packs claim the same extension (e.g., future overlap),
the engine rejects the configuration at load time — ambiguous extensions require
the delta element to specify an explicit `language` field.

### Distribution

All packs are built-in — they ship with filigree. No external plugin discovery or
third-party packaging in v1.0. New languages require a filigree release. The
registry loader enforces this: grammar pack definitions from `.filigree/packs/` or
`.filigree/templates/` are rejected. Only workflow packs support user-provided
definitions.

The initial language set covers filigree's own codebase: Python (core logic) and
JavaScript/CSS/HTML (dashboard). This "dogfood first" scope validates the design
against real usage before expanding to additional languages.

**Future language extensibility (design constraint):** The grammar pack interface
is intentionally language-agnostic. Adding a new language requires:

1. A tree-sitter grammar binding (150+ already exist)
2. A grammar pack JSON definition (file extensions, queries, operation declarations)
3. Python `OperationHandler` implementations for each declared operation
4. A formatter declaration (if the language has one)

No changes to the execution engine, delta schema, validation pipeline, or toolchain
profile system are needed. The architecture supports any language tree-sitter can
parse. The v1.0 "built-in only" constraint is a distribution policy, not an
architectural limitation — it can be relaxed in a future version if demand warrants
community-contributed grammar packs.

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
        self, source: bytes, tree: Tree, captures: dict[str, Node], args: dict
    ) -> list[TextEdit]:
        """Compute text edits from matched captures. Must not have side effects."""
        ...
```

The `captures` parameter provides all named captures from the tree-sitter query
(e.g., `{"fn": <Node>, "rt": <Node>}`). Handlers use these to locate the exact
nodes they need to modify. A query `(function_definition name: (identifier) @fn
return_type: (type) @rt)` produces `captures = {"fn": <name node>, "rt": <type
node>}`.

Where `TextEdit` is:

```python
@dataclass(frozen=True)
class TextEdit:
    start_byte: int
    end_byte: int
    replacement: str  # UTF-8 encoded when applied to source bytes
```

**Encoding contract:** `replacement` is a Python `str`. The edit application engine
encodes it as UTF-8 before splicing into the source `bytes`. Byte-offset arithmetic
after insertion uses `len(replacement.encode('utf-8'))`, not `len(replacement)`.

**Validation:** `start_byte` must be ≤ `end_byte`. A zero-length range
(`start_byte == end_byte`) is a valid insertion. Inverted ranges are rejected.

**Implementation cost:** ~16 operation implementations (2 language groups × ~8
operations), each 50–300 lines of Python. The Python grammar pack alone is estimated
at 800–1200 lines of operation code due to indentation-as-correctness. The
JavaScript pack shares some structural similarity with Python (tree-sitter queries
are similar for function/class definitions) but has distinct formatting needs.

### Handler dispatch

Each grammar pack provides a **handler registry** — a `dict[str, OperationHandler]`
mapping operation names to handler instances. The registry is returned by a
module-level factory function in the grammar pack's Python module:

```python
# filigree/grammar_packs/python/handlers.py
def create_handlers() -> dict[str, OperationHandler]:
    return {
        "rename_symbol": RenameSymbolHandler(),
        "add_type_annotation": AddTypeAnnotationHandler(),
        "add_import": AddImportHandler(),
        # ...
    }
```

The engine resolves handlers by: grammar pack name → handler registry → operation
name. Unknown operation names produce an immediate validation error (not a runtime
KeyError).

### Multi-match dispatch

When a query matches multiple nodes (e.g., "all untyped parameters"), the engine
calls `compute_edit` **once per match**. Each call receives the captures for that
specific match. The engine collects all returned `TextEdit` values into a single
batch and applies them together (descending `start_byte` order, overlap rejection).

This means `add_import` handlers that need whole-file context (e.g., checking if
an import already exists) should use the `source` and `tree` parameters for
context, not expect to see all matches at once.

### Formatter-as-normalizer

Languages with deterministic formatters can generate syntactically correct but
unformatted code, then run the formatter as a post-transform step. This
significantly simplifies operation handlers.

This is a first-class field on grammar packs:

```json
{
  "pack": "javascript",
  "kind": "grammar",
  "has_deterministic_formatter": true,
  "formatter": "prettier"
}
```

| Language   | Formatter        | Effect on operation handlers |
|------------|------------------|------------------------------|
| Python     | None (semantic whitespace) | Must handle indentation in edit computation |
| JavaScript | `prettier`       | Generate valid syntax, skip formatting |
| CSS        | `prettier`       | Same — format post-transform |
| HTML       | None (structural) | Must handle indentation in edit computation |

**Formatter timing:** Formatters run **per-file**, after all transforms for that
file are applied but before assertions are evaluated. This ensures assertions check
the formatted output. A formatter failure on one file does not roll back transforms
on other files within the same task — it marks only the failing file's element as
red.

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
   JavaScript uses JSDoc `/** @type {number} */` or TypeScript-style annotations.

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

5. **Query resource limits.** All tree-sitter query executions are bounded:
   - **Max match count:** 10,000 matches per query (prevents `(_) @x` on large files)
   - **Per-query timeout:** 5 seconds
   - **Max captures per query:** 100 named captures
   Exceeding any limit aborts the query and marks the delta element as red.

### Initial grammar packs

| Pack         | Extensions             | Formatter          | Notes                                   |
|--------------|------------------------|--------------------|-----------------------------------------|
| `python`     | `.py`, `.pyi`          | None (semantic ws) | Most complete — primary language          |
| `javascript` | `.js`, `.mjs`, `.cjs`  | `prettier`         | Could expand to `.jsx`, `.ts` in v1.1    |
| `css`        | `.css`                 | `prettier`         | Selector/property-level operations       |
| `html`       | `.html`, `.htm`        | None               | Pure HTML only — no embedded JS/CSS/templates |

## Toolchain Profile

Toolchain configuration is **project-level**, not distributed with grammar packs.
This eliminates command injection by design: delta authors reference validators by
name, never by command template.

Grammar packs provide **default validator recommendations** as documentation only.
The actual validator configuration lives in the project's toolchain profile.

### Toolchain integrity

`toolchain.toml` is project-level and in-repo. Since the `command` field specifies
an executable, it is a potential code execution vector. Mitigations:

1. **Content-addressing at validation time:** `validate-deltas` computes the
   SHA-256 of `toolchain.toml` and stores it alongside the delta's `pre_hash`
   values. At execution time, the hash is re-verified. Any change between
   validation and execution aborts.
2. **Command allowlist (recommended):** Projects may define an explicit allowlist
   of permitted validator commands in `toolchain.toml` itself:
   ```toml
   [toolchain]
   allowed_commands = ["mypy", "ruff", "prettier", "eslint"]
   ```
   When present, the engine rejects any validator whose `command` is not in the
   list. When absent, any command is permitted (backwards-compatible, but logged
   as a warning).
3. **No relative paths:** `command` must be a bare executable name (resolved via
   PATH) or an absolute path. Relative paths (`./scripts/check.py`) are rejected
   to prevent in-repo executable planting.

### Validator contract

Each validator definition specifies an execution contract:

```toml
# .filigree/toolchain.toml

[toolchain]
allowed_commands = ["mypy", "ruff", "prettier", "eslint"]

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
args = ["check", "--", "{file}"]
scope = "file"
timeout_seconds = 30
success_exit_codes = [0]
required = true
fail_if_missing = true
output_parser = "text"

[validators.js-lint]
command = "eslint"
args = ["--format=json", "--", "{file}"]
scope = "file"
timeout_seconds = 30
success_exit_codes = [0]
required = true
fail_if_missing = true
output_parser = "json"
```

### Validator contract fields

| Field               | Type            | Description |
|---------------------|-----------------|-------------|
| `command`           | string          | Executable name (resolved via PATH) or absolute path. Relative paths rejected |
| `args`              | list[string]    | Arguments. `{file}` placeholder available for file-scoped validators only. A `--` separator must precede `{file}` to prevent filename-as-flag injection (engine enforces this) |
| `scope`             | enum            | `"file"`, `"package"`, `"project"`, or `"workspace"` |
| `timeout_seconds`   | int             | Hard timeout. Exceeded = failure = rollback |
| `success_exit_codes`| list[int]       | Exit codes treated as success (default: `[0]`) |
| `required`          | bool            | `true` = must pass, `false` = advisory (logged, doesn't block) |
| `fail_if_missing`   | bool            | `true` = error if command not found (prevents silent false green) |
| `output_parser`     | string          | `"text"`, `"json"`, `"sarif"` |
| `env_allowlist`     | list[string]    | Environment variables passed to subprocess (all others stripped) |
| `cwd`               | string          | Working directory: `"project_root"`, `"workspace_root"`, or `"file_parent"` |

### Scope model

The `{file}` placeholder is only valid for `scope = "file"` validators. mypy
(in strict mode) and similar tools operate at project/workspace scope — they
never accept individual file targets. The scope field makes this explicit rather
than papering over it with broken placeholders.

**v1.0 scopes** (no parameterized placeholders beyond `{file}`):

| Scope       | When run                          | `{file}` valid? | Example            |
|-------------|-----------------------------------|-----------------|--------------------|
| `file`      | Per file touched by transforms    | Yes             | `ruff check {file}` |
| `project`   | Once per project                  | No              | `mypy src/`        |
| `workspace` | Once per workspace                | No              | `mypy src/`        |

**`{file}` injection prevention:** The engine enforces that `args` contains a `--`
element before any element containing `{file}`. This prevents filenames starting
with `--` from being interpreted as flags. Additionally, all `{file}` substitutions
are prefixed with `./` (e.g., `./src/core.py`) to prevent bare `-`-prefixed paths.

**Deferred to v1.1:** `package` scope with `{package}` placeholder. This requires
workspace/package discovery logic per language (npm workspaces, Python namespace
packages) and is substantial enough to warrant its own design pass if needed.

### Two-tiered validation

Validation happens in two tiers with different performance characteristics:

1. **Structural validation** (fast, always runs) — CST assertions evaluate whether
   the transform produced syntactically valid output and the expected nodes exist
   with correct content.
2. **Semantic validation** (slow, runs after structural) — toolchain validators
   (mypy, eslint) verify that the code is semantically correct.

This ordering prevents slow validator runs on structurally broken code.

**Validator deduplication:** Project-scoped and workspace-scoped validators are
deduped per phase — they run once regardless of how many delta elements reference
them. File-scoped validators run once per unique file. This prevents silent
double-runs that waste time while ensuring no validator is accidentally skipped.

## Code-Delta Module

The code-delta module is a **built-in feature** (not a pack) that wires grammar
packs into the workflow lifecycle. It adds schema to task-level items and provides
commands for validation and execution. It is enabled via `features.code_delta` in
the project config.

### Schema extensions

When code-delta is enabled, the following fields are added to `task` and `step`
item types:

| Field | Type | Description |
|-------|------|-------------|
| `delta` | object | The delta payload (see Delta Schema below) |
| `delta_status` | enum | `pending`, `green`, `red`, `stale` (derived cache) |
| `delta_confidence` | enum | `structural`, `validated`, `tested` (derived cache) |

### Commands

| Command | CLI | MCP | Description |
|---------|-----|-----|-------------|
| `validate-deltas` | `filigree validate-deltas <plan-id> [--phase=<id>]` | `validate_deltas` | Dry-run validation, sets delta_status and delta_confidence |
| `execute-deltas` | `filigree execute-deltas <plan-id> [--phase=<id>]` | `execute_deltas` | Atomic execution with rollback |
| `delta-status` | `filigree delta-status <plan-id>` | `get_delta_status` | Green/red/stale/pending counts + confidence breakdown |

### Gate types

The code-delta module registers gate types that compose with the existing
transition gate system:

```json
{
  "field_set": {"field": "delta"},
  "field_eq": {"field": "delta_status", "value": "green"},
  "field_eq": {"field": "delta_confidence", "value": "tested"},
  "all_children_field_eq": {"field": "delta_status", "value": "green"}
}
```

Note: `all_children_field_eq` replaces the previous `all_linked_field_eq` with
`link_type: "parent", direction: "inbound"` — the old naming was semantically
inverted (it checked children, not parents, despite the name).

No lifecycle hooks. Validation and execution are explicit commands (CLI + MCP), not
callbacks. The gate evaluator reads `delta_status` and `delta_confidence` —
no coupling between commands and the gate system.

### Delta schema

A task's delta contains one or more **elements**, each targeting a single file.
Multi-file tasks use multiple elements. Single-file tasks use a one-element array.

```json
{
  "delta": {
    "schema_version": 1,
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
| `schema_version` | Integer version of the delta schema format. Current: `1`. Allows future schema evolution without breaking stored deltas |
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

**Gate enforcement:** The `active → executing` phase transition gate defaults to
requiring `delta_confidence == "tested"` (not just `delta_status == "green"`). This
prevents the "structural green is good enough" shortcut from bypassing semantic
validation. Plans can override this with a `minimum_confidence` field (e.g.,
`"validated"` for draft plans), but must do so explicitly.

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
       ├── Re-validate this phase + downstream phases that share files
       ├── (Only phases whose elements[].path sets overlap this phase's outputs)
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
       ├── Journal: phase_commit_initiated
       ├── Optional: git commit --no-verify (--auto-commit flag)
       ├── Journal: phase_committed (with commit_sha if git)
       └── If git commit fails → ROLLBACK, abort

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

| Event                     | Fields |
|---------------------------|--------|
| `execution_started`       | plan_id, actor, timestamp, baseline_sha, toolchain_hash |
| `phase_validated`         | plan_id, phase_id, result, file_hashes |
| `file_written`            | plan_id, task_id, file_path, edit_count |
| `validator_started`       | plan_id, validator_name, scope |
| `validator_passed`        | plan_id, validator_name, duration_ms |
| `validator_failed`        | plan_id, validator_name, exit_code, output_snippet |
| `phase_commit_initiated`  | plan_id, phase_id |
| `phase_committed`         | plan_id, phase_id, commit_sha (null if no auto-commit) |
| `phase_rolled_back`       | plan_id, phase_id, reason, files_restored, upstream_phase_id (if cascade) |
| `execution_completed`     | plan_id, phases_committed, phases_failed |

**Crash recovery protocol:** On startup, the engine scans the journal for
incomplete executions. If `phase_commit_initiated` exists without a corresponding
`phase_committed`, the engine checks `git log` (if git backend) to determine
whether the commit actually landed. If committed: write the missing
`phase_committed` entry and continue. If not committed: treat as rollback. This
two-phase journal design prevents the split-brain state where the journal and git
disagree about what happened.

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
   `git add <affected_files> && git commit --no-verify -m "filigree: <phase_name>"`.
   `--no-verify` is mandatory to prevent git hooks from executing arbitrary code
   during the delta execution window. Otherwise, leave changes staged but
   uncommitted.
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
- **NFC-normalize** all paths and apply `os.path.normcase()` before any comparison
  (prevents Unicode homoglyph bypass and case-sensitivity exploits on macOS/Windows)
- Normalise and **reject any path escaping root** (`..`, absolute paths, symlinks
  resolving outside)
- **Resolve symlinks** and verify the resolved path is still within project root
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

The code-delta module adds no new execution machinery to core. It composes with
filigree-next's existing item_links and transition gates.

### Execution ordering via item_links

The topological sort for delta execution is a query over the existing item_links
`blocks` relationships. No separate dependency graph needed.

### Commands replace hooks

No generic hook framework. The code-delta module provides three explicit commands:

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

# Phase-level: can't execute until all child tasks are green + tested
"transitions": [
  {
    "from": "active",
    "to": "executing",
    "enforcement": "hard",
    "gates": [
      {"type": "all_children_field_eq",
       "field": "delta_status", "value": "green"},
      {"type": "all_children_field_eq",
       "field": "delta_confidence", "value": "tested"}
    ]
  },
  {
    "from": "executing",
    "to": "completed",
    "enforcement": "hard",
    "gates": [
      {"type": "all_children_status", "status_category": "done"}
    ]
  }
]
```

Gate types registered by the code-delta module:

| Gate type | Description |
|-----------|-------------|
| `field_set` | Field exists and is non-null, non-empty |
| `field_eq` | Field equals a specific value |
| `all_children_field_eq` | All child items have field equal to value |
| `all_children_status` | All child items are in a status category |

`field_set` semantics: `null` → not set. Empty string `""` → not set. Empty
object `{}` → not set. Empty list `[]` → not set. `false` → set. `0` → set.

Note: `field_eq` and `all_children_field_eq` gate types must be added to the
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
├─ filigree create-plan with code-delta enabled
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
│  │  ├─ Re-validate this phase + file-overlapping downstream phases
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
| Architecture      | Packs (workflow + grammar) + code-delta module (built-in)  | No premature extension abstraction; one consumer, one module  |
| Parser foundation | tree-sitter (CST)                                         | 150+ languages, battle-tested, Python bindings                |
| Source generation | Text edits via OperationHandler (code, not config)         | CST has no serialization — operations must produce text edits |
| Handler dispatch  | Per-match invocation with `captures: dict[str, Node]`      | Handlers see all named captures; consistent per-match model   |
| Grammar/toolchain | Split: grammar packs (pure) + toolchain profile (project)  | Eliminates command injection; supports project-specific tools |
| Toolchain integrity | Content-addressed toolchain.toml + command allowlist      | Prevents in-repo executable planting via PR                   |
| Delta schema      | Multi-element per task with content addressing + version    | Multi-file changes without task noise; tamper/TOCTOU resistance |
| Execution model   | Phase-level atomic, plan-level sequential                  | Survivable large refactors with safety invariants             |
| Re-validation     | Dependency-aware (shared-file phases only), not all-remaining | O(n) common case instead of O(n²); correctness preserved    |
| Journal           | Two-phase commits (initiated → committed)                  | Prevents journal/git split-brain on crash                     |
| Lifecycle         | Explicit commands + field-based gates (no hooks)           | No untestable mini-runtime; composes with existing gates      |
| Confidence gates  | Phase execution requires `delta_confidence == "tested"`    | Prevents structural-only green from bypassing semantic checks |
| Security          | Path jail (NFC + normcase), content-addressing, shell=False | Defense in depth at every layer                              |
| Rollback          | Git-preferred (--no-verify), snapshot-fallback             | Git when available, degrade gracefully; no hook execution     |
| Distribution      | Built-in only                                              | No package discovery overhead, security boundary              |
| Validation        | Two-tiered: structural (CST) then semantic (toolchain)     | Fast feedback on broken syntax before slow validators         |
| Initial languages | Python, JavaScript, CSS, HTML                              | Dogfood first — filigree's own languages                      |
| Formatters        | First-class field, per-file timing                         | Explicit per-language pipeline; format before assertions      |

## Staged Delivery

### v1.0 — Declarative + Safe Execution

**Non-goal:** v1.0 does not ship a query builder or visual authoring tool for
tree-sitter queries. It ships a small query template library per grammar pack and
expects authors to write queries directly.

**Scope:** filigree's own languages (Python + JavaScript/CSS/HTML).

- Grammar packs: Python, JavaScript, CSS, HTML
- Minimal operation set: `rename_symbol` (single-file), `add_import`,
  `remove_import`, `add_type_annotation` (Python), `add_parameter`,
  `remove_parameter`, `add_decorator` (Python)
- Multi-element delta schema with content addressing and schema version
- Assertion vocabulary: `exists`, `not_exists`, `count_eq`, `count_gte`,
  `capture_equals`, `capture_regex`, `node_text_contains`
- Confidence levels (structural / validated / tested) with gate enforcement
- Validator contract + toolchain profile (TOML) with integrity checking
- Phase-level atomic execution with dependency-aware re-validation and rollback
- Two-phase execution journal + mutual exclusion
- Git-preferred rollback backend (--no-verify)
- Interactive confirmation prompt (CLI) / agent-directed execution (MCP)
- Path jail invariant (NFC-normalized, symlink-resolved)
- Query resource limits (match count, timeout, captures)
- Audit events (delta lifecycle)

### v1.1+

- Additional languages (Rust, TypeScript, etc.) based on demand
- `extract_function` and other high-risk refactors
- Cross-file `rename_symbol` (requires `find_references`)
- More powerful assertions (cross-file consistency, plan-level)
- Richer git integration (PR creation, branch management)
- Optional CI/CD automation
- HTML embedded language support (`<script>`, `<style>`, templates)
- Signed deltas

## Open Questions

- **Transform authoring UX** — how do agents/humans author tree-sitter queries
  and operation args? A builder tool or template library would reduce friction.
  (This is the primary adoption risk — if authoring is too hard, nobody will use it.)
- **Conflict resolution** — when two tasks target the same CST node, the second
  transform assumes the first already applied. What tooling helps authors get this
  right?
- **Dashboard visualization** — how should the before/after diff and CST query
  match be rendered in the dashboard?
- **Cross-file assertion model** — plan-level consistency assertions across files
  (deferred, but needs design before v1.1)
- **Python indentation engine** — specification needed for correctness-grade
  indentation handling in the Python grammar pack
- **HTML embedded language boundary** — the HTML grammar pack is "pure HTML only"
  in v1.0. What's the interaction model when transforms target an HTML file that
  contains `<script>` or `<style>` tags? Should those regions be masked or flagged?

---

## Change Log

### Revision 3 (2026-02-25) — Final Review Fixes

Incorporates findings from 4-specialist final review panel (architecture critic,
threat analyst, API architect, systems thinker). See
`2026-02-25-plugin-final-review-annex.md` for full review report.

**A) Language scope reduction**
- Reduced from 5 languages (Python, Rust, C, JavaScript, HTML) to 4:
  Python, JavaScript, CSS, HTML — filigree's own codebase languages
- Removed Rust, C grammar packs from v1.0; moved to v1.1+ based on demand
- Added CSS grammar pack (tree-sitter-css, prettier formatter)
- Rationale: "dogfood first" — validate the design with filigree's own code

**B) Extension pack abstraction removed**
- Replaced "extension pack" layer with built-in code-delta module
- Only one extension was ever specified; premature abstraction removed
- Code-delta is now a `features.code_delta` toggle, not a pack
- Previous: three-layer model (workflow + grammar + extension)

**C) OperationHandler protocol fixes (blocking)**
- Changed `match: Node` to `captures: dict[str, Node]` — handlers need all
  named captures, not just a single node
- Added UTF-8 encoding contract on `TextEdit.replacement`
- Added `start_byte <= end_byte` validation rule
- Specified per-match dispatch model (one `compute_edit` call per match)
- Added handler registry specification (module-level factory function)
- Previous: single Node parameter, unspecified dispatch

**D) Journal-Git atomicity (blocking)**
- Two-phase journal entries: `phase_commit_initiated` then `phase_committed`
- Git commits use `--no-verify` to prevent hook-based code execution
- Added crash recovery protocol (check git log on incomplete entries)
- Previous: journal wrote "committed" before git commit — split-brain risk

**E) Toolchain security hardening (blocking)**
- Added toolchain.toml content-addressing (hash verified at execution time)
- Added `allowed_commands` allowlist in `[toolchain]` section
- Rejected relative paths in `command` field
- Added `--` end-of-flags separator before `{file}` substitution
- All `{file}` paths prefixed with `./` to prevent flag injection
- Previous: `command` field accepted arbitrary executables

**F) Confidence-level gate enforcement**
- Phase execution gate now requires `delta_confidence == "tested"` by default
- Plans can override with explicit `minimum_confidence` for draft work
- Prevents "structural green is good enough" bypass of semantic validators

**G) Dependency-aware re-validation**
- Changed from "re-validate ALL remaining phases" to "re-validate this phase +
  downstream phases sharing files"
- Reduces O(n²) to O(n) for non-overlapping phase plans
- Previous: quadratic validation cost with plan size

**H) Gate naming fix**
- Replaced `all_linked_field_eq` (link_type: "parent", direction: "inbound")
  with `all_children_field_eq` — previous naming was semantically inverted
- Added `field_set` edge-case semantics (null, empty string/object/list = unset)

**I) Security hardening**
- Path jail: added NFC normalization + `os.path.normcase()` + symlink resolution
- Tree-sitter queries: added resource limits (10K matches, 5s timeout, 100 captures)
- Validator deduplication: project/workspace-scoped validators deduped per phase
- Formatter timing: specified as per-file, post-transform, pre-assertion

**J) Delta schema versioning**
- Added `schema_version: 1` field to delta payload
- Added `delta_confidence` as separate derived field alongside `delta_status`
- Added `toolchain_hash` to `execution_started` journal event

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
