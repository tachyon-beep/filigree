"""v0.5 feature tests — dissolved into domain modules.

Core:  tests/core/test_crud.py        (TestExportJsonl, TestImportJsonl)
       tests/core/test_workflow_behavior.py  (TestReleaseClaim — merged)
MCP:   tests/mcp/test_tools.py        (TestMCPReleaseClaim, TestMCPExportImport)
CLI:   tests/cli/test_admin_commands.py      (TestExportImportCli — merged)
       tests/cli/test_issue_commands.py      (TestReleaseCli — already existed)
"""
