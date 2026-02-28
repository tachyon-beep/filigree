# IMPORT CONSTRAINT: types/ modules must only import from typing, stdlib, and each other.
# NEVER import from core.py, db_base.py, or any mixin — this prevents circular imports.
"""TypedDict contracts for MCP tool handler input arguments.

Each TypedDict mirrors the JSON Schema ``inputSchema`` on the corresponding
``mcp.types.Tool`` definition.  The ``TOOL_ARGS_MAP`` registry maps tool names
to their TypedDict class so the sync test can verify structural agreement.

Safety note on cast():
    The MCP SDK validates argument presence/types against JSON Schema before
    handler invocation.  Core validates authoritatively.  The TypedDicts here
    are a *static-analysis* tool — ``cast()`` provides type narrowing only,
    not runtime validation.  Direct handler calls that bypass MCP SDK
    validation are unsafe — callers must pre-validate arguments.
"""

# NOTE: Do NOT add ``from __future__ import annotations`` to this module.
# It breaks TypedDict.__required_keys__ / __optional_keys__ introspection
# on Python <3.14, which the sync test in test_input_type_contracts.py
# depends on for verifying required/optional agreement with JSON Schema.

from typing import Any

# Registry: tool_name -> TypedDict class.
# Populated as TypedDicts are defined below.
# No-argument tools (empty inputSchema properties) are intentionally excluded.
TOOL_ARGS_MAP: dict[str, type] = {}
