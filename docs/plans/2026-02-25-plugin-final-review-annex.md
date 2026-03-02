# Plugin System & Language Packs — Final Review Annex

**Date**: 2026-02-25
**Status**: Review complete — findings delivered, revision pending
**Design**: [2026-02-25-plugin-system-design.md](./2026-02-25-plugin-system-design.md)
**Revision reviewed**: 2.1 (invariant precision pass)

---

## Review Methodology

Four specialist agents reviewed the design document independently and in parallel.
Each examined the design from a different angle with access to the full codebase:

| Reviewer | Focus | Method |
|----------|-------|--------|
| **Architecture Critic** | Complexity budget, layered decomposition, atomicity boundaries, distribution model | Measured codebase size against proposed additions; assessed proportionality |
| **Threat Analyst** | Security claims validation, attack surface enumeration | STRIDE analysis across 9 components; 5 attack trees for highest-risk items |
| **API Architect** | Protocol contracts, schema design, command surface, gate composability | Interface-by-interface assessment against implementation survivability |
| **Systems Thinker** | Feedback loops, cascade dynamics, failure modes, archetype matching | Identified 6 dynamics, matched to Senge/Meadows archetypes, ranked leverage points |

**Previous review**: 8-specialist panel, 3 rounds, full consensus (Revision 1 → 2).
This final review focuses on what the previous panel missed or got wrong.

---

## Unanimous Finding: Proportionality Question

All four reviewers independently flagged the same meta-concern, each from their
own angle:

- **Architecture Critic**: "8,000–12,000 new lines to replace a 222-line module.
  The 8-specialist panel never asked: does a project tracker need a built-in
  refactoring engine?"
- **Threat Analyst**: 21 threats identified across 9 components — the attack
  surface is substantial for a feature whose demand is undemonstrated.
- **API Architect**: 8 interfaces with significant specification gaps — the
  protocol surface is larger than it appears.
- **Systems Thinker**: 6 feedback loops identified, 2 reinforcing failure modes —
  the system's failure-path complexity rivals its happy-path complexity.

**Consensus**: The design is internally consistent and well-engineered after two
revision passes. The question is not "is it well-designed?" but "should it exist
in this form?" The reviewers recommend validating demand before full implementation
(see Recommendation S1 below).

---

## Findings

### Blocking (must resolve before implementation)

#### B1: Journal-Git atomicity — split-brain on crash

**Raised by**: Systems Thinker (Critical), Architecture Critic (noted)

The execution flow (lines 578–579) writes `phase_committed` to the journal BEFORE
the git commit. If the git commit fails (hook failure, disk full, process kill),
the system enters a split state:

- Journal says: phase committed (authoritative for crash recovery)
- Git says: changes staged but uncommitted (dirty tree)

On restart, the journal says "proceed to next phase" but pre-flight requires a
clean tree — **execution is permanently blocked**. The inverse is equally
dangerous: crash after git commit but before journal write causes double-application
on recovery.

**Resolution required**: Two-phase journal entries. Write `phase_commit_initiated`
before git commit, then `phase_committed` (with `commit_sha`) only after git
succeeds. Recovery protocol: if `phase_commit_initiated` exists without
`phase_committed`, check `git log` before proceeding.

#### B2: OperationHandler `match: Node` loses multi-capture context

**Raised by**: API Architect (Critical)

The protocol signature `compute_edit(source, tree, match: Node, args)` passes a
single `Node`. But tree-sitter queries use named captures (`@fn`, `@rt`, etc.) and
handlers need all of them. A `add_type_annotation` handler using
`(function_definition name: (identifier) @fn return_type: ...)` needs `@fn` for
context, not just the function node.

The correct type is `captures: dict[str, Node]`, not `match: Node`. This is a
protocol design error that will break every handler implementation.

**Resolution required**: Change signature to
`compute_edit(source: bytes, tree: Tree, captures: dict[str, Node], args: dict)`.

#### B3: OperationHandler dispatch and registration unspecified

**Raised by**: API Architect (Critical)

The engine must map `operation: "add_type_annotation"` (a string in a delta) to a
Python class implementing the Protocol. The mechanism is completely unspecified:
explicit registry dict? Naming convention? Entry points? Grammar pack authors
cannot write a conforming implementation without knowing this.

Additionally, multi-match dispatch semantics are undefined: is `compute_edit`
called once per match or once per query with all matches? For `rename_symbol`,
per-occurrence is natural. For `add_import`, per-query is more coherent. Without
this spec, every grammar pack author will guess differently.

**Resolution required**: Specify dispatch mechanism and document whether handlers
receive one match at a time or all matches.

#### B4: toolchain.toml is an arbitrary code execution vector

**Raised by**: Threat Analyst (Critical — Risk 9/9)

The `command` field in `toolchain.toml` is an in-repo, PR-modifiable path to an
executable. Anyone who can submit a PR can change `command = "mypy"` to
`command = "./scripts/evil.py"`. `shell=False` blocks shell metacharacters but
NOT execution of arbitrary binaries. No integrity check or allowlist on this file
is specified.

**Resolution required**: Either (a) content-address `toolchain.toml` at validation
time and verify at execution, (b) restrict `command` to an allowlist of known
tools, or (c) require absolute paths resolved against a trusted prefix.

#### B5: `{file}` argument injection via crafted filenames

**Raised by**: Threat Analyst (High — Risk 6/9)

A file named `--config=evil.toml` becomes a flag when `{file}` is substituted into
`["ruff", "check", "--config=evil.toml"]`. The path jail doesn't reject
`--`-prefixed filenames.

**Resolution required**: Insert `--` end-of-flags separator before `{file}`
substitution in all validator arg lists, or prefix all paths with `./`.

---

### High Priority (address before v1.0 ships)

#### H1: Extension pack abstraction is premature

**Raised by**: Architecture Critic

Only one extension pack (`code-delta`) is described. Grammar packs have no
consumer other than `code-delta`. The "extension pack" abstraction exists to serve
exactly one concrete use case and one hypothetical (`coverage-tracking`, listed in
a diagram but never specified).

**Recommendation**: Drop the extension pack abstraction. Build `code-delta` as a
named feature module. Extract the abstraction if/when a second extension
materializes.

#### H2: `validate_with` deduplication for project-scoped validators

**Raised by**: API Architect

If two delta elements both reference `python-typecheck` (scope: "project"), does
the validator run once or twice? Silent double-runs waste time; silent skips create
false greens. The deduplication strategy is unspecified at both the schema and
execution levels.

**Recommendation**: Specify that project/workspace-scoped validators are deduped
per phase (run once regardless of how many elements reference them). Document this
in both the delta schema and execution model sections.

#### H3: Confidence level escape valve enables Shifting the Burden

**Raised by**: Systems Thinker

Transition gates check `delta_status == "green"` but not confidence level. A
structurally-validated delta (fast, shallow) passes the same gate as a fully-tested
delta (slow, thorough). Over time, authors learn that structural green is "good
enough" to unblock workflow, and the incentive to run semantic validators weakens.

**Recommendation**: The `active → executing` phase gate should default to requiring
`delta_confidence == "tested"`. Authors can explicitly opt down for draft plans
but must do so consciously. This closes the escape valve at the execution boundary.

#### H4: O(n^2) re-validation creates a growth ceiling

**Raised by**: Systems Thinker, Architecture Critic

Step 2a says "Re-validate ALL remaining phases against current disk." For P phases,
this is P*(P-1)/2 total validation passes. With slow validators (mypy at 120s,
cargo check at 300s), this creates a hard ceiling on practical plan size.

The systems analysis shows this drives a **Limits to Growth** archetype: authors
fragment plans to avoid the wall, which reduces the system's core value proposition
(coordinated multi-phase refactoring).

**Recommendation**: Dependency-aware re-validation. Before executing Phase k,
validate only Phase k itself plus subsequent phases whose `elements[].path` sets
intersect with Phase k's output files. For independent phases (non-overlapping
files), this collapses to O(n).

#### H5: Path jail bypass via Unicode normalization / case sensitivity

**Raised by**: Threat Analyst (Risk 6/9, compounds to Critical)

`_safe_path()` doesn't specify Unicode normalization or case-insensitive handling.
On macOS (case-insensitive HFS+) or Windows, `.Filigree/` or Unicode-equivalent
paths may bypass the `.filigree/` exclusion. If the path jail fails, an attacker
can write directly to the SQLite DB and set `delta_status = "green"` on any delta.

**Recommendation**: NFC-normalize all paths + apply `os.path.normcase()` before
comparison. Re-derive `delta_status` at gate evaluation time rather than trusting
the cached DB value.

#### H6: Tree-sitter query DoS — no resource limits

**Raised by**: Threat Analyst (Risk 6/9)

No query complexity limits, match count caps, or per-query timeouts are specified.
A broad query like `(_) @x` on a large file exhausts CPU/memory, and the execution
lock amplifies this to system-wide DoS.

**Recommendation**: Add max match count (e.g., 10,000), per-query timeout (e.g.,
5 seconds), max captures per query.

#### H7: Git hooks during `--auto-commit` execute arbitrary code

**Raised by**: Threat Analyst (Risk 6/9)

`--auto-commit` runs `git commit`, which triggers pre-commit hooks. A malicious
`core.hooksPath` in repo config executes arbitrary code during the commit phase,
inside the execution lock.

**Recommendation**: Set `core.hooksPath=/dev/null` (or equivalent) for all git
operations during delta execution.

#### H8: `TextEdit.replacement` encoding unspecified

**Raised by**: API Architect

Source is `bytes`, replacement is `str`. The encoding for converting `replacement`
to bytes is unspecified. A multi-byte UTF-8 character in `replacement` means
byte-offset math after insertion differs from `len(replacement)`. The engine needs
a documented encoding contract (UTF-8, consistent with capture text semantics).

#### H9: Grammar pack extension conflicts undefined

**Raised by**: Systems Thinker, Architecture Critic, API Architect

Multiple grammar packs claiming the same file extension (e.g., `.h` for C and
future C++) has no defined resolution. The current v1.0 pack list avoids this, but
it's a time bomb for v1.1.

**Recommendation**: Define the rule now (recommended: require explicit `language`
field for ambiguous extensions; reject inference). Detect conflicts at pack load
time and warn.

---

### Medium Priority (address before wide adoption)

#### M1: SnapshotBackend has no exception contract

**Raised by**: API Architect

`restore()` failure during rollback is catastrophic and needs different handling
than ordinary execution failure. The interface provides no signal. Additionally,
partial restore (crash mid-restore) leaves the repo in an undefined state. The
snapshot (file-copy) backend leaks temp directories on crash.

**Recommendation**: Define exception types. Add post-rollback hash verification
(compare restored files against pre-execution baseline from journal).

#### M2: Formatter timing unspecified

**Raised by**: Architecture Critic

The document says formatters run "post-transform" but doesn't specify: per-task?
Per-phase? Per-file? If per-phase, a formatter failure rolls back the entire phase
including tasks that formatted correctly.

**Recommendation**: Per-file, after all transforms for that file are applied but
before assertions. Document explicitly.

#### M3: Operation handler versioning absent

**Raised by**: Architecture Critic

If a `rename_symbol` implementation changes behavior between filigree releases,
previously-validated deltas become semantically incorrect even though their digests
still match (digests cover intent, not handler version).

**Recommendation**: Add a handler version field to the digest canonicalization, or
document that digests are only valid within a single filigree release.

#### M4: Stale cascade transparency

**Raised by**: Systems Thinker

When Phase N fails and subsequent phases are marked stale, authors see opaque
failures. The **Fixes that Fail** archetype takes hold: each fix triggers new
downstream staleness, and progress feels negative.

**Recommendation**: When marking phases stale, store the causal event (which
upstream phase failed, which files triggered the cascade). Surface in
`delta-status` output.

#### M5: Gate naming — `link_type: "parent", direction: "inbound"` is inverted

**Raised by**: API Architect

`all_linked_field_eq(link_type="parent", direction="inbound")` means "all items
for which I am the parent" — i.e., children. But it reads as "check my parent."
Every gate author will misread this.

**Recommendation**: Either rename to `link_type: "child"` or add prominent
documentation clarifying that `direction: "inbound"` means "items that reference
me via this link type."

#### M6: No cancellation command for stuck executions

**Raised by**: API Architect

`execute-deltas` holds an execution lock for the duration. If the process is killed
mid-execution, no `filigree cancel-execution` command exists to inspect or clean up
the lock. A lock-breaking/inspection command is missing.

#### M7: Delta schema has no version field

**Raised by**: API Architect

Individual delta payloads have no version tag. If the delta schema evolves between
filigree releases, old stored deltas have no version for parsing decisions.

---

### Low Priority (acknowledged, not blocking)

#### L1: No usage scenario documenting who authors deltas

**Raised by**: Architecture Critic

The document never describes WHO authors a delta, step by step. The "Transform
authoring UX" open question (line 931) is not an open question — it's a missing
product definition.

#### L2: No performance model

**Raised by**: Architecture Critic

No estimate of expected execution time for typical plans. Re-parsing after every
transform (line 125–127) combined with toolchain validators has unknown aggregate
cost.

#### L3: No comparison to alternatives

**Raised by**: Architecture Critic

The document doesn't compare this to: (a) agents directly editing files with `git
diff` review, (b) a patch-based system, (c) a simpler CST query system without
transforms. Any of these would deliver partial value at 10–20% complexity.

#### L4: TOCTOU hash cycling — files that change and change back

**Raised by**: Systems Thinker

If a file goes through H1 → H2 → H1 between validation and execution, the
pre-hash check reports no change. The TOCTOU resistance claim is subtly overstated
— it resists current-state changes, not intermediate-state changes.

**Recommendation**: Add a `validated_at` timestamp. If execution begins more than
T minutes after validation (configurable, default 60 min), require re-validation.

#### L5: `field_set` gate semantics undefined for edge cases

**Raised by**: API Architect

Is an empty list "set"? Is `false` "set"? Is an empty delta object `{}` "set"?

---

## Strategic Recommendation: Staged Validation

**Raised by**: Architecture Critic (primary), all reviewers (concurring)

Before implementing the full design, validate demand:

1. **S1 — Ship Python-only v1.0 with 2 operations** (`rename_symbol`,
   `add_type_annotation`). Measure whether anyone authors deltas. If adoption is
   zero after a release cycle, the design was solving a non-problem.

2. **S2 — Drop the extension pack abstraction.** Build `code-delta` as a direct
   feature module. Extract the abstraction only if a second extension materializes.

3. **S3 — Defer languages 3–5.** Ship Python, then Rust if Python proves useful.
   Don't commit to 5 languages before validating the concept with 1.

This reduces the v1.0 implementation from ~8,000–12,000 lines to ~2,000–3,000
lines while preserving the full design's option value. If the feature has traction,
the remaining languages and capabilities can be added incrementally.

---

## What the Design Gets Right

The reviewers unanimously credited the following:

- **Grammar/toolchain split** eliminates command injection from deltas by design
- **Content-addressing** provides strong tamper detection for transform intent
- **`shell=False` + `env_allowlist`** blocks the most common injection patterns
- **Built-in-only distribution** eliminates the third-party supply chain risk
- **Defense-in-depth path validation** (authoring + execution time)
- **Two-tiered validation** (structural then semantic) provides fast feedback
- **Phase-level atomicity** is the right boundary (commit, review unit, rollback)
- **Explicit commands over hooks** — no untestable mini-runtime
- **Edit conflict rules** (descending start_byte, identical-range dedup) are precise

The design's internal consistency is high — a credit to the 8-specialist panel
that produced Revisions 2 and 2.1.

---

## Summary Scorecard

| Aspect | Score | Notes |
|--------|-------|-------|
| Internal consistency | 4/5 | Well-specified after 3 revision passes |
| Security design | 4/5 | Strong foundations; 5 gaps identified (all fixable) |
| Interface contracts | 3/5 | OperationHandler has protocol errors; several gaps |
| System dynamics | 3/5 | Happy path sound; failure paths need precision |
| Problem-solution fit | 2/5 | Solving an unvalidated problem at high complexity |
| Proportionality | 2/5 | ~10K new lines to replace ~200 lines; demand unknown |

**Overall verdict**: The design is well-engineered but needs demand validation
before full implementation. The 5 blocking findings (B1–B5) must be resolved
regardless. The strategic recommendation (S1–S3) de-risks the investment.

---

## Appendix: Threat Catalog Summary

| ID | Threat | STRIDE | Risk | Status |
|----|--------|--------|------|--------|
| THREAT-11 | toolchain.toml arbitrary execution | Elevation | 9/9 | **B4** above |
| THREAT-04+23 | Path jail Unicode bypass → gate bypass | Tampering+Elevation | 6/9 compound | **H5** above |
| THREAT-12 | `{file}` argument injection | Tampering | 6/9 | **B5** above |
| THREAT-08 | Tree-sitter query DoS | DoS | 6/9 | **H6** above |
| THREAT-19 | Git hooks during auto-commit | Elevation | 6/9 | **H7** above |

21 threats total; 15 mitigations recommended. See full threat analyst report for
complete catalog with attack trees.

---

## Appendix: System Archetypes Identified

| Dynamic | Archetype | Severity | Design Impact |
|---------|-----------|----------|---------------|
| Journal/Git split-brain | Accidental Adversaries | Critical | **B1** above |
| Structural green bypasses gates | Shifting the Burden | High | **H3** above |
| O(n^2) revalidation ceiling | Limits to Growth | High | **H4** above |
| Fix one phase, break the next | Fixes that Fail | Medium | **M4** above |
| Lock contention under agents | Resource Contention | Medium | **M6** above |
| Hash cycling defeats TOCTOU | Drifting Goals | Low | **L4** above |
