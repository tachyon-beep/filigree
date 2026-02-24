# Plugin System Design Review — Discussion Transcript

**Date:** 2026-02-25
**Format:** 8-specialist facilitated panel, 3 rounds
**Document Reviewed:** `2026-02-25-plugin-system-design.md`
**Outcome:** Full consensus achieved with design revisions

## Panel Composition

| # | Role | Focus |
|---|------|-------|
| 1 | Software Architect | Pack composition, extension points, architectural coherence |
| 2 | Systems Engineer | Execution atomicity, rollback, failure modes, invariants |
| 3 | Python Specialist | Python language pack, tree-sitter Python, mypy/ruff integration |
| 4 | Rust Specialist | Rust language pack, cargo ecosystem, cross-language parity |
| 5 | Quality Engineer | Validation model soundness, testing strategy, false greens |
| 6 | SDLC Specialist | Workflow lifecycle, git integration, CI/CD, audit trail |
| 7 | Security Architect | Threat modeling, injection vectors, authorization, trust boundaries |
| 8 | Parser/Compiler Specialist | Tree-sitter internals, CST vs AST, source generation, query portability |

## Round 1: Initial Assessments

Each specialist independently reviewed the design document and provided domain-specific analysis.

### Key Findings by Specialist

**Software Architect:**
- Lifecycle hooks are completely new execution machinery with zero existing infrastructure (HIGH)
- Language pack validators conflate grammar (pure) with toolchain (impure) (MEDIUM)
- Operations have unclear implementation ownership — are packs data or code? (HIGH)
- Multi-file transforms need architectural commitment (MEDIUM)
- delta_status is a derived property stored as source-of-truth (LOW-MEDIUM)
- Gate condition vocabulary doesn't cover extension pack needs (MEDIUM)
- Recommended: split grammar from toolchain, multi-delta-per-task, OperationHandler protocol

**Systems Engineer:**
- Snapshot/rollback mechanism completely unspecified (HIGH)
- TOCTOU between re-validation and first write (MEDIUM)
- Validator side effects not guarded — pytest creates caches, mypy creates caches (HIGH)
- No concurrent execution mutual exclusion (MEDIUM)
- Partial rollback after validator failure — UX of error reporting (MEDIUM)
- No multi-file delta specification (MEDIUM)
- Sequential transforms on same file need fresh AST invariant (MEDIUM)
- Recommended: concrete snapshot strategy, file hash verification, execution lock, persistent journal

**Python Specialist:**
- Tree-sitter edits do NOT handle indentation reconstruction — critical for Python (HIGH)
- `from __future__ import annotations` (PEP 563) changes query semantics (MEDIUM)
- Mypy strict mode + transform authoring creates iteration loops (HIGH)
- Ruff auto-fix overlaps with AST transforms, invalidating byte offsets (MEDIUM)
- `extract_function` is underspecified for Python (HIGH)
- `__init__.py` re-exports and cross-file awareness (MEDIUM)
- `TYPE_CHECKING` blocks need import placement context (MEDIUM)
- Recommended: defer extract_function, build indentation engine, add import context parameter

**Rust Specialist:**
- Cargo workspace vs file-level validation — `{file}` placeholder fundamentally broken (HIGH)
- Procedural macros invisible to tree-sitter (HIGH)
- Module system creates non-local dependencies (MEDIUM)
- Lifetime annotations and generic bounds create query complexity (MEDIUM)
- `{file}` placeholder model doesn't work for ANY cargo command (HIGH)
- Recommended: package-level validator routing, Rust-specific operations, document macro blindness

**Quality Engineer:**
- Assertion model too coarse — only exists/not_exists/count, can't catch semantic errors (HIGH)
- No timeout/resource/failure-mode specification for validators (HIGH)
- No specification for testing transform operations themselves (HIGH)
- Tree-sitter grammar update regression risk (MEDIUM)
- Rollback path has untested edge cases (MEDIUM)
- "All green" confidence model under-defined (MEDIUM)
- Recommended: expand assertion vocabulary, validator execution contract, transform testing strategy

**SDLC Specialist:**
- Git workflow integration completely absent (HIGH)
- No audit trail for delta execution events (HIGH)
- No partial execution or resumption model (MEDIUM)
- Multi-developer plan coordination unaddressed (MEDIUM)
- Plan versioning and mid-flight changes (MEDIUM)
- CI/CD integration model unclear (MEDIUM)
- Recommended: git as required dependency, delta lifecycle events, phase-level execution

**Security Architect:**
- Command injection via crafted file paths in validators (HIGH)
- Path traversal in delta file field (HIGH)
- No authorization model for delta authorship (HIGH)
- Transform args as arbitrary code execution vector (MEDIUM)
- Rollback snapshot tampering (MEDIUM)
- Tree-sitter query denial-of-service (MEDIUM)
- TOCTOU via staleness detection bypass (MEDIUM)
- Recommended: _safe_path() enforcement, strict arg grammars, human review gate, content-addressed deltas

**Parser/Compiler Specialist:**
- CST vs AST — document conflates them, tree-sitter produces CSTs (HIGH)
- Tree-sitter has NO AST-to-source serialization — the core technical gap (HIGH)
- Query portability across languages — node types differ significantly (MEDIUM)
- Error recovery interaction with transforms (MEDIUM)
- Multiple transforms on same file need edit ordering strategy (MEDIUM)
- HTML/template language embedded parser limitations (MEDIUM)
- Recommended: explicit source generation model, drop extract_function, per-language operation matrix

### Round 1 Statistics
- Total concerns raised: 58
- HIGH risk: 24
- MEDIUM risk: 28
- LOW risk: 6
- Total recommendations: 72

## Round 2: Cross-Specialist Review

Six themes were identified for cross-domain discussion:
- A: Source Generation Model
- B: Validator Architecture
- C: Execution Atomicity Scope
- D: Lifecycle Hooks
- E: Security Controls
- F: Multi-File Transforms

### Theme A Consensus: Operations as Python Code

**Unanimous agreement.** All specialists who addressed this theme agreed:
- Operations are Python code modules, not declarative JSON configs
- JSON definitions declare the interface; Python classes implement behavior
- Parser specialist proposed three-layer decomposition:
  1. Query Layer (tree-sitter, pure, fast)
  2. Edit Computation Layer (Python OperationHandler, pure, medium)
  3. Edit Application Engine (generic, shared infrastructure)
- ~40 operation implementations needed (5 languages × ~8 operations)
- Rust specialist discovered that `rustfmt` as post-transform normalizer simplifies Rust operations significantly — languages with deterministic formatters have simpler handlers

### Theme B Consensus: Grammar/Toolchain Split

**Unanimous agreement.** Three-tier architecture:
1. Grammar pack (built-in): tree-sitter language, queries, operation handlers
2. Default toolchain (built-in, overridable): validator defaults per language
3. Project toolchain config (per-project): validator commands, timeouts, scoping

Key findings:
- Rust specialist showed `{file}` placeholders are fundamentally incompatible with cargo
- Security architect showed extracting validators eliminates command injection by design
- SDLC specialist proposed two-tier governance: pack defaults + project overrides with audit visibility

### Theme C: Atomicity Scope (Resolved in Round 3)

**Initial tension:** Plan-level (systems, QA) vs phase-level (SDLC, architect, rust, python).

Systems engineer argument: partial execution creates untested states; cross-phase dependencies cause staleness.

Phase-level argument: maps to natural boundaries (crate, import graph); practical for large refactors; natural commit boundary.

**Resolved in Round 3** — see below.

### Theme D Consensus: No Generic Hook Framework

**Unanimous agreement:** Remove lifecycle hooks from the design. Replace with:
- `validate-deltas` and `execute-deltas` as direct CLI/MCP commands
- Existing field-based transition gates (`field_eq` on `delta_status`) for workflow enforcement
- No new execution machinery needed

### Theme E Consensus: Minimum Viable Security

**Unanimous agreement on v1.0 security baseline:**
- Path containment via `_safe_path()` + `.filigree/` exclusion for all delta file fields
- `shell=False` for all subprocess invocations
- Content-addressed deltas (hash delta content at authoring, hash files at validation, verify both at execution)
- Human-only execution gate (`execute-deltas` requires human actor)
- Strict input grammars for transform args (allowlists, not blocklists)
- Built-in-only enforced at loader level (language/extension packs cannot be loaded from `.filigree/packs/`)

**Deferred to v1.1:** Full RBAC authorization model, import allowlists, signed deltas, semantic lint pass.

### Theme F: Multi-File Deltas (Resolved in Round 3)

**Initial tension:** Multi-delta-per-task (architect, QA, parser, rust, python) vs single-delta (systems).

**Resolved in Round 3** — see below.

### Additional Consensus Items from Round 2

- **Defer `extract_function`** to v1.1 (parser, python — unanimous)
- **Git as rollback mechanism** — SDLC proposed replacing custom snapshots with git (phase execution = git commit, rollback = git restore). Supported but not fully debated.
- **Two-tiered validation**: structural (AST assertions, fast) vs semantic (toolchain validators, slow). QA and architect converged on this independently.
- **Phased implementation**: Phase 1 declarative extensions, Phase 2 code execution engine (architect, no dissent).
- **Inverted test pyramid** — QA identified that the system relies on the slowest layer (validators) for confidence rather than the fastest (operation unit tests).

## Round 3: Final Resolution

Two tensions were submitted for formal vote with proposed compromises.

### Resolution 1: Atomicity Scope

**Proposed:** Phase-level execution with systems-engineer's safety invariants.

- Execution unit: phase (atomic within, sequential across)
- Before executing a phase: re-validate ALL remaining phases against current disk
- All snapshots taken before any writes within a phase
- Fresh AST after every text edit (hard invariant)
- No auto-resume after rollback — user must re-validate and explicitly resume
- v1.0 default: full-plan execution; `--phase=<id>` available as opt-in
- Amendment (systems): on phase N rollback, phases N+1...K auto-marked `stale`
- Amendment (security): re-validation includes file hash verification

**Vote: 8/8 AGREE** (with accepted amendments)

### Resolution 2: Multi-file Deltas

**Proposed:** Multi-delta-per-task as the schema, single-delta as convenience.

- Schema: `{"deltas": [{"file": "...", "transforms": [...], "assertions": [...]}]}`
- Single-file task: one-element array (no special case)
- Execution engine processes `deltas[]` uniformly
- Within a task, all file deltas applied atomically
- Amendment (security): each `deltas[].file` independently path-validated; any invalid path rejects entire task

**Vote: 8/8 AGREE** (with accepted amendment)

## Final Consensus Summary

### Architectural Decisions

| Decision | Consensus Position | Vote |
|----------|-------------------|------|
| Operations model | Python code modules with OperationHandler protocol, three-layer decomposition | Unanimous |
| Validator architecture | Grammar packs separate from project-level toolchain config | Unanimous |
| Execution atomicity | Phase-level with safety invariants, plan-level as default | 8/8 |
| Lifecycle hooks | Remove; use direct CLI/MCP commands + field-based gates | Unanimous |
| Security model | Path jail, content-addressed deltas, human execution gate | Unanimous |
| Multi-file deltas | Multi-delta-per-task schema with uniform array processing | 8/8 |
| Implementation phasing | Phase 1 declarative, Phase 2 code execution engine | No dissent |
| extract_function | Defer to v1.1 | Unanimous |
| Built-in-only enforcement | Enforced at loader level for language/extension packs | Unanimous |
| Formatter-as-normalizer | Languages with deterministic formatters (Rust, C, JS) get simpler operation handlers | Emerging consensus |

### Must-Have Design Changes Before Implementation

1. **Source generation model** — add explicit section on CST→text-edit pipeline, OperationHandler protocol
2. **Grammar/toolchain split** — restructure language packs into grammar packs + project toolchain config
3. **Snapshot/rollback specification** — concrete storage strategy with crash recovery
4. **Assertion vocabulary expansion** — add text_equals, text_matches, parent_is beyond exists/not_exists/count
5. **Validator execution contract** — timeouts, exit codes, required/advisory, scope model
6. **Security section** — path traversal prevention, input validation, human execution gate
7. **Git integration** — branch awareness, auto-commit per phase, rollback via git restore
8. **Audit trail** — delta lifecycle event types (validated, executed, failed, stale)
9. **Phase-level execution** — with re-validation invariants and stale-marking on rollback
10. **Multi-delta schema** — `{"deltas": [...]}` with per-element path validation

### Risk Assessment Summary

| Area | Pre-Review Risk | Post-Review Risk | Change |
|------|----------------|-----------------|--------|
| Source generation (CST→text edits) | Not identified | **CRITICAL** | Was completely hidden by the design's declarative framing |
| Validator architecture | LOW (assumed working) | **HIGH** | `{file}` placeholder broken for Rust; environment coupling |
| Execution atomicity | MEDIUM | **MEDIUM** | Resolved via phase-level with invariants |
| Security | Not addressed | **HIGH** | Multiple injection vectors, no authorization model |
| Lifecycle integration | LOW | **HIGH** | Git, audit trail, CI/CD all missing |
| Implementation cost | Underestimated | **HIGH** | ~40 operation handlers, each non-trivial Python code |
| Testing strategy | Not addressed | **HIGH** | Inverted test pyramid, no transform testing specification |

### Open Items for Implementation Planning

1. Transform authoring UX — how do agents/humans write tree-sitter queries and operation args?
2. Dashboard visualization — before/after diff rendering, AST query match highlighting
3. Conflict resolution tooling — when two tasks target the same AST node
4. Cross-file assertion model — plan-level consistency assertions across files
5. `find_references` query capability — for cross-file rename authoring support
6. Grammar version compatibility testing strategy
7. Rust `Cargo.toml` workspace discovery and package resolution
8. Python indentation engine specification
