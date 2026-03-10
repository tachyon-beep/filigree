# File Traceability Readiness Checklist

**Date:** 2026-02-23  
**Scope:** `filigree-5bf143` integration verification (`filigree-bb1532`)  
**Goal:** Validate file registration -> association -> issue/files visibility -> timeline/scan auditability with happy-path and key failure-path evidence.

## Verification Command

```bash
uv run pytest -q \
  tests/test_mcp.py::TestFileTools \
  tests/test_mcp.py::TestScannerTools \
  tests/test_files.py::TestBidirectionalEndpoints \
  tests/test_dashboard.py::TestGraphFrontendContracts
```

Result: `54 passed`

## Checklist

- [x] MCP file registration workflow verified
  - Evidence: `TestFileTools::test_register_file_and_get_file_round_trip`
  - Evidence: `TestFileTools::test_register_file_is_idempotent_by_path`
- [x] MCP file association workflow verified
  - Evidence: `TestFileTools::test_add_file_association_and_get_issue_files`
  - Evidence: `TestFileTools::test_get_issue_files_not_found` (failure path)
  - Evidence: `TestFileTools::test_add_file_association_invalid_assoc_type` (failure path)
- [x] Scanner trigger and correlation behavior verified
  - Evidence: `TestScannerTools::test_trigger_scan_success`
  - Evidence: `TestScannerTools::test_trigger_scan_uses_canonical_path_in_scanner_command`
  - Evidence: `TestScannerTools::test_trigger_scan_rate_limited` (failure path)
  - Evidence: traversal and missing-file failures in `TestScannerTools`
- [x] Dashboard API issue->files and issue->findings visibility verified
  - Evidence: `TestBidirectionalEndpoints::test_issue_files_endpoint`
  - Evidence: `TestBidirectionalEndpoints::test_issue_findings_endpoint`
  - Evidence: `TestBidirectionalEndpoints::test_issue_files_not_found` (failure path)
- [x] Dashboard UI contract includes issue-associated files rendering
  - Evidence: `TestGraphFrontendContracts::test_issue_detail_fetches_issue_files_contract`
  - Evidence: `TestGraphFrontendContracts::test_issue_detail_renders_associated_files_section`
- [x] Operator documentation published
  - Evidence: `docs/file-traceability.md`
  - Evidence: links from `docs/README.md` and `docs/mcp.md`

## Release Notes (Feature `filigree-5bf143`)

1. MCP now supports first-class file traceability operations for listing files, retrieving file detail/timeline, registering files by path, linking issues to files, and scanning workflows.
2. Issue detail UI now shows associated files and supports drill-down into file detail, completing issue-centric and file-centric navigation paths.
3. Scanner triggering now uses canonical project-relative paths for command correlation, reducing split-record risk from path mismatches.
4. Operator documentation now includes an end-to-end MCP + dashboard playbook with troubleshooting for missing links and missing file records.
