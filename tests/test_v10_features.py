"""v1.0 feature tests — dissolved into domain modules.

Core:  tests/core/test_crud.py              (TestArchival, TestCompaction)
       tests/core/test_schema.py            (TestMigrationV3 — merged, TestPerformanceIndexes — already existed)
       tests/core/test_workflow_behavior.py  (TestInvalidStatusRejected — already existed)
MCP:   tests/mcp/test_tools.py              (TestMCPV10)
CLI:   tests/cli/test_admin_commands.py      (TestCLIArchive — already existed via JSON retrofit)
"""
