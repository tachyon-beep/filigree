# Architecture Scanner: Design Review Synthesis

## Panel Composition

Six panelists debated across 4 themed rounds:

| Role | Perspective |
|------|-------------|
| **Systems Thinker** | Feedback loops, leverage points, emergent dynamics |
| **Architecture Critic** | Separation of concerns, coupling, API boundaries |
| **Python Expert** | AST feasibility, dependency costs, implementation reality |
| **UX Specialist** | Agent tool UX, progressive disclosure, discoverability |
| **Senior User (x2)** | First-person agent experience, daily workflow pain points |

## Round Summary

- **Round 1 — Core Value Proposition & MVP Scope**: Identified the core value (graph-based structural awareness), unanimously killed the Quality Gatekeeper, and surfaced a key tension on whether blast radius belongs in MVP.
- **Round 2 — Integration Architecture**: Achieved near-total consensus on standalone-first, anomalies-as-findings, file path as shared key, no bridge tool, and agent-as-integration-layer.
- **Round 3 — Feature Prioritization & Sequencing**: Tiered all 20 proposed features. Two 3-3 splits remained (boundaries and staleness signals).
- **Round 4 — Tiebreaker**: Resolved both splits. Staleness signals moved to unanimous v1.0. Boundaries resolved 4-2 for v1.0.

---

## Consensus Decisions

### Product Model

**Peer product, not a plugin.** Archscan is a standalone tool with its own MCP server, codebase, and release cycle. It integrates with filigree through well-defined contracts but does not depend on it.

- **Standalone operation is mandatory** (unanimous, Round 2)
- Filigree integration is **detected and enriched**, never required
- Both products share **file path** as the only common concept
- No bridge tools, no shared schemas, no RPC between servers

### MVP Tools (v1.0)

Three MCP tools ship in v1.0:

| Tool | Purpose | Panel Vote |
|------|---------|------------|
| `get_symbol_skeleton(path)` | AST-extracted signatures, types, docstrings — no function bodies | 6/6 v1.0 |
| `trace_blast_radius(symbol)` | Direct callers/callees via import graph traversal (1-hop, scoped) | 5/6 v1.0 |
| `map_subsystem_boundaries(dir)` | Module-level import dependency overview | 4/6 v1.0 (Round 4) |

**Cut from all releases:**
- `fetch_surgical_context` / `get_node_source` — unanimous. `Read` with line offsets from skeleton output is sufficient.
- Quality Gatekeeper — unanimous. Refusing to analyze complex code is a perverse incentive; the most complex code needs analysis the most.
- Missing type annotation detection — unanimous. Linters (mypy, ruff) already do this better. Scope creep into a solved problem.

### Infrastructure (v1.0)

| Component | Decision | Rationale |
|-----------|----------|-----------|
| Graph store | `dict[str, set[str]]` adjacency dict | NetworkX is overkill for BFS/DFS. Upgrade to NetworkX in v2 only if graph algorithms justify the dependency. |
| Cache | SHA-256 file hashing | Non-negotiable. Without it, every query re-parses. |
| Invalidation | Lazy per-file revalidation on query | Check hash at query time, re-parse only changed files. |
| Transport | Standalone MCP server (stdio) | The product IS the MCP server. |
| Staleness signals | `last_validated_at` timestamp on all responses | Unanimous v1.0 (Round 4). Trust is existential for adoption. Stale data that looks authoritative is worse than no data. |
| Directionality | Explicit A→B vs B→A in all graph responses | Must be in the data model from day one. Retrofitting is painful. |

### Scanner/Finding Features (v1.0)

| Finding Type | Tier | Rationale |
|--------------|------|-----------|
| Circular dependency detection | v1.0 | Falls out of the graph for free. High signal, zero incremental cost. |
| Orphan module detection | v1.1 | Useful but false-positive-prone (scripts, tests, entry points). |
| Fan-in/fan-out hotspots | v1.1 | Needs threshold tuning against real graph data before shipping. |

### Integration Architecture

```
archscan (standalone)  ──POST anomalies──>  filigree scan API
     |                    (fire-and-forget)        |
     |  file path = shared join key                |
     |                                             |
     v                                             v
  Own MCP server                            Own MCP server
  (3 structural tools)                      (~30 project tools)
     |                                             |
     +------------- agent correlates --------------+
```

**Data flow principles (unanimous):**

1. **Anomalies are findings, topology is not.** Circular dependencies, orphans, extreme fan-in → POST to filigree as scan findings. Raw graph edges ("A imports B") never touch filigree.
2. **Archscan owns the dependency graph.** Filigree owns issues, workflows, and findings. Neither duplicates the other's data.
3. **File discovery is independent.** Archscan crawls the filesystem (respects `.gitignore`). Does NOT depend on filigree's `file_records` table.
4. **Integration is opportunistic.** Detect filigree's presence at startup. POST findings if reachable, skip gracefully if not. Fire-and-forget, never block on filigree.
5. **Cross-reference in tool descriptions.** Zero-coupling navigation hints: archscan tool descriptions mention filigree tools ("Tip: use filigree's `get_issue_files` to find related files") and vice versa.

---

## Release Tiers

### v1.0 — MVP

**MCP Tools:**
1. `get_symbol_skeleton(path)` — signatures, types, docstrings, line numbers
2. `trace_blast_radius(symbol)` — direct callers/callees (1-hop), scoped with declared limitations
3. `map_subsystem_boundaries(dir)` — module import graph overview

**Infrastructure:**
4. Directed adjacency-dict graph store (no NetworkX)
5. SHA-256 file-hash cache invalidation
6. Lazy per-file revalidation on query
7. Staleness/confidence signals on all responses (`last_validated_at`)
8. Dependency directionality in all graph responses (`imports` vs `imported_by`)
9. Standalone MCP server (stdio)

**Findings:**
10. Circular dependency detection → POST as finding (fire-and-forget to filigree if available)

**Polish:**
11. Cross-reference hints in tool descriptions

### v1.1 — Fast Follow

12. Optional filigree detection + fire-and-forget POSTs (robust retry, logging)
13. Scanner TOML registration with filigree (`.filigree/scanners/archscan.toml`)
14. Orphan module detection → POST as finding
15. Fan-in/fan-out hotspot detection → POST as finding
16. Public API view (`get_symbol_skeleton(path, public_only=True)`)
17. Cross-reference hints added to filigree's tool descriptions

### v2.0 — Next Major

18. Transitive blast radius (multi-hop graph traversal, `__init__.py` re-export resolution)
19. Diff-aware view ("what changed since commit X" — requires git integration)
20. Type context enrichment (resolve referenced TypedDicts from other files)
21. NetworkX upgrade (if centrality, community detection, or advanced algorithms needed)

### Permanently Cut

- `fetch_surgical_context` / `get_node_source` — redundant with `Read`
- Quality Gatekeeper — perverse incentive, hostile to primary use case
- Missing type annotation detection — linter territory, not structural analysis

---

## Key Design Arguments (for reference)

### Why blast radius in MVP despite implementation difficulty
> "A blast radius tool that handles 70% of cases in v1 is infinitely more valuable than a perfect one in v2 that never ships." — Systems Thinker
>
> "Grep finds string matches. Blast radius finds semantic dependencies. That's a genuine gap." — Senior User
>
> Ship with declared scope limitations: "Traces direct callers within the analyzed directory. Cross-package resolution via `__init__.py` re-exports may be incomplete."

### Why adjacency dict over NetworkX
> "NetworkX is a 15MB dependency that gives you algorithms you don't need yet. Cycle detection is O(V+E) with Tarjan's on a raw dict. You're not doing PageRank." — Architect
>
> "For what this MVP actually needs — directed graph construction, node/edge traversal — you can get 90% of the value from a simple adjacency-list dict." — Python Expert

### Why staleness signals are v1.0, not v1.1
> "Stale data that looks authoritative is worse than no data. The agent acts on false structural information with high confidence." — Systems Thinker
>
> "Lazy revalidation is invisible to me. A timestamp costs one field and gives me the information I need to decide whether to trust the result." — Senior User
>
> Round 4 saw three panelists switch to v1.0, achieving 6-0 unanimous consensus.

### Why standalone is non-negotiable
> "A tool that requires filigree to function is not a peer product — it's a plugin. The reinforcing loop you want is: archscan delivers value alone → users adopt it → they discover it's better with filigree → they adopt filigree too." — Systems Thinker

---

## Suggested Implementation Sequence

Based on the UX specialist's week-by-week proposal, adapted to consensus:

1. **Foundation**: Graph store (adjacency dict), SHA-256 cache, lazy revalidation, MCP server scaffold
2. **Core tools**: `get_symbol_skeleton` + `map_subsystem_boundaries` (testable product at this point)
3. **Differentiator**: `trace_blast_radius` with directionality + staleness signals
4. **First finding**: Circular dependency detection + fire-and-forget POST to filigree
5. **Polish**: Cross-reference hints in tool descriptions, documentation

---

## Post-Review Note: User vs. Builder Priority Tension

The two 3-3 splits broke along a revealing line — users (systems-thinker + both senior-users) vs. builders (architect, python-expert, ux-specialist):

| Split | Users wanted | Builders wanted | Resolution |
|---|---|---|---|
| Staleness signals | **v1.0** | v1.1 | Users won (builders switched, 6-0) |
| `map_subsystem_boundaries` | v1.1 | **v1.0** | Builders won (4-2) |

The staleness resolution was healthy — builders recognized a trust requirement they'd overlooked. But the boundaries resolution should be scrutinized: **the users should drive feature priority, and architecture should adjust to align.** Both senior-users held v1.1 through Round 4, arguing they arrive at files via the issue tracker or grep, not via subsystem maps. The builders' counter was architectural elegance ("progressive disclosure funnel needs an entry point"), but that's designing for an ideal workflow rather than the actual one.

**Resolution (post-review):** Rather than treating progressive disclosure vs. direct access as an either/or, all tools now support a `detail` parameter. Default (no flag) returns the progressive, token-efficient response. `detail="full"` returns enriched output with cross-cutting context in a single call. This resolves the tension: the funnel exists for exploration, but agents that already have context can skip it. Both senior-users' workflow ("I already know the file") and the builders' architecture ("progressive disclosure protects context budget") are honored simultaneously. Context is treated as the most precious resource — the default protects it, the flag spends it deliberately.

---

## Unresolved Items for Future Discussion

- **Blast radius scope boundary**: Exactly what "direct callers" means for re-exports through `__init__.py` (python-expert flagged this as the hardest edge case)
- **Staleness signal format**: Timestamp vs. boolean vs. enum (`current`/`stale`/`unknown`) — not debated in detail
- **AST version compatibility**: Python 3.8→3.12 AST node changes (`ast.Str` → `ast.Constant`). Pin to 3.11+ minimum.
