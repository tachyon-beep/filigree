# Architecture Scanner: A Deterministic, Graph-Based Codebase Representation for AI Agents

## 1. System Objective

To provide a deterministic, statically analyzed, graph-based representation of a Python codebase via the Model Context Protocol (MCP). This allows AI agents to perform high-level architectural reasoning, dependency tracking, and targeted code retrieval without reading unparsed text files or hallucinating structural connections.

## 2. Core Architecture Components

The system is divided into four primary layers, isolating the raw file system from the LLM context window.

### A. The Transport & Protocol Layer (MCP Interface)

* **Responsibility:** Handles the JSON-RPC communication between the AI Client and the Server via standard input/output (stdio) or Server-Sent Events (SSE).
* **Function:** Registers the available tools, their JSON schemas, and their descriptive prompts. It acts as the router, taking an agent's request for `get_symbol_skeleton` and passing the parameters down to the Query Engine.

### B. The Quality Gatekeeper (The "Fail Fast" Engine)

* **Responsibility:** Protects the agent from burning tokens on "spaghetti code" or heavily obfuscated metaprogramming.
* **Function:** Before the parser maps a directory, this engine runs a lightweight sweep (using metrics like McCabe complexity or `eval`/`exec` counts). If the codebase crosses the failure thresholds, it short-circuits the request and returns a deterministic refusal payload, instructing the agent that the system requires human refactoring before AI analysis.

### C. The Static Analysis Engine (The Crawler)

* **Responsibility:** Parses raw Python files into Abstract Syntax Trees (AST) without executing them.
* **Function:** Implements custom `ast.NodeVisitor` classes. It sweeps through directories, stripping out function bodies and runtime logic, extracting only structural metadata: Class names, method signatures, type hints, docstrings, and explicit import paths.

### D. The Architectural Graph Store (In-Memory State)

* **Responsibility:** Maintains the cross-file relationship map. Single-file AST parsing isn't enough; the system needs to know how subsystems connect.
* **Function:** Uses a lightweight directed graph library (like `NetworkX`). As the Crawler parses files, it populates this graph:
* **Nodes:** Modules, Classes, and Functions.
* **Edges:** `imports_from`, `inherits_from`, `calls_method`.
* This graph is what powers the "Blast Radius" and "Subsystem Boundary" queries.

---

## 3. The API / Tool Registry

The tools exposed to the orchestrating agents follow a strict pattern of progressive disclosure:

1. **`map_subsystem_boundaries(target_dir)`:** Returns the 10,000-foot view of module exports and inter-module dependencies.
2. **`get_symbol_skeleton(target_path)`:** Returns the AST-parsed signatures and type hints for a specific class or module, omitting internal logic.
3. **`trace_blast_radius(target_symbol)`:** Traverses the Architectural Graph Store to return all upstream callers and downstream dependencies of a specific node.
4. **`fetch_surgical_context(target_node)`:** The only tool that returns raw code. It extracts the exact line numbers of a specific AST node for deep-dive logical analysis.

---

## 4. Data Flow: The Agentic Loop

To see how this architecture enables token-efficient operations, here is the data flow when an orchestrator agent delegates an analysis task:

1. **Context Request:** The orchestrator agent decides it needs to understand how a specific subsystem (e.g., `kasmina`) interacts with a broader evaluator module (e.g., `tamiyo`).
2. **Boundary Mapping:** The orchestrator calls `map_subsystem_boundaries`. The MCP Server queries the Graph Store and returns the structural edges showing that `kasmina` imports three specific interfaces from `tamiyo`.
3. **Delegation:** The orchestrator spins up a sub-agent, providing it *only* those specific boundary details, instructing it to analyze the mutation logic.
4. **Skeleton Retrieval:** The sub-agent calls `get_symbol_skeleton` on the exact interface. The Static Analysis Engine parses the file on the fly, strips the bodies, and returns the strict Python signatures.
5. **Targeted Reading:** The sub-agent identifies the exact method handling the logic and calls `fetch_surgical_context`. The server slices out those 40 lines of raw code and returns them.
6. **Resolution:** The sub-agent completes its analysis using minimal tokens and reports back to the orchestrator.

---

## 5. Caching & State Management

Because codebases change during an agentic workflow (especially if the AI is writing or refactoring code), the MCP server cannot rely on a stale graph.

* **File Hashing:** The Graph Store tracks the SHA-256 hash of every file it crawls.
* **Lazy Revalidation:** When an agent queries a specific module, the server checks the current file hash against the stored hash. If it differs, the Static Analysis Engine re-runs the AST parser *only* for that file and updates the specific nodes and edges in the Graph Store before returning the result.
