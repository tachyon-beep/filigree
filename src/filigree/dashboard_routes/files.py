"""File tracking and scan findings route handlers."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from starlette.requests import Request

from filigree.core import (
    VALID_ASSOC_TYPES,
    VALID_FINDING_STATUSES,
    VALID_SEVERITIES,
    FiligreeDB,
)
from filigree.dashboard_routes.common import (
    _error_response,
    _parse_json_body,
    _parse_pagination,
    _safe_int,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_router() -> Any:
    """Build the APIRouter for file tracking and scan findings endpoints.

    NOTE: All handlers are intentionally async despite doing synchronous
    SQLite I/O. This serializes DB access on the event loop thread,
    avoiding concurrent multi-thread access to the shared DB connection.

    Route order matters: ``/files/_schema`` must be registered before
    ``/files/{file_id}`` so FastAPI matches the literal path first.
    """
    from fastapi import APIRouter, Depends
    from fastapi.responses import JSONResponse

    from filigree.dashboard import _get_db

    router = APIRouter()

    @router.get("/files")
    async def api_list_files(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """List tracked file records with optional filtering and pagination."""
        params = request.query_params
        pagination = _parse_pagination(params)
        if isinstance(pagination, JSONResponse):
            return pagination
        limit, offset = pagination
        min_findings = _safe_int(params.get("min_findings", "0"), "min_findings", min_value=0)
        if isinstance(min_findings, JSONResponse):
            return min_findings
        result = db.list_files_paginated(
            limit=limit,
            offset=offset,
            language=params.get("language"),
            path_prefix=params.get("path_prefix"),
            min_findings=min_findings if min_findings > 0 else None,
            has_severity=params.get("has_severity"),
            scan_source=params.get("scan_source"),
            sort=params.get("sort", "updated_at"),
            direction=params.get("direction"),
        )
        return JSONResponse(result, headers={"Cache-Control": "no-cache"})

    @router.get("/files/hotspots")
    async def api_file_hotspots(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Files ranked by weighted finding severity score."""
        params = request.query_params
        limit = _safe_int(params.get("limit", "10"), "limit", min_value=1)
        if isinstance(limit, JSONResponse):
            return limit
        result = db.get_file_hotspots(limit=limit)
        return JSONResponse(result)

    @router.get("/files/stats")
    async def api_file_stats(db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Global findings severity stats across all files."""
        return JSONResponse(db.get_global_findings_stats())

    @router.get("/files/_schema")
    async def api_files_schema() -> JSONResponse:
        """API discovery: valid enum values and endpoint catalog for file/scan features."""
        schema = {
            "valid_severities": sorted(VALID_SEVERITIES),
            "valid_finding_statuses": sorted(VALID_FINDING_STATUSES),
            "valid_association_types": sorted(VALID_ASSOC_TYPES),
            "valid_file_sort_fields": ["first_seen", "language", "path", "updated_at"],
            "valid_finding_sort_fields": ["severity", "updated_at"],
            "endpoints": [
                {
                    "method": "POST",
                    "path": "/api/v1/scan-results",
                    "description": "Ingest scan results",
                    "status": "live",
                    "request_body": {
                        "scan_source": "string (required)",
                        "findings": "array (required)",
                        "scan_run_id": "string (optional)",
                        "mark_unseen": "boolean (optional)",
                    },
                },
                {"method": "GET", "path": "/api/files", "description": "List tracked files", "status": "live"},
                {
                    "method": "GET",
                    "path": "/api/files/{file_id}",
                    "description": "Get file details",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/{file_id}/findings",
                    "description": "Findings for a specific file",
                    "status": "live",
                },
                {
                    "method": "PATCH",
                    "path": "/api/files/{file_id}/findings/{finding_id}",
                    "description": "Update finding status/linkage",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/{file_id}/timeline",
                    "description": "Merged event timeline for a file",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/hotspots",
                    "description": "Files ranked by weighted finding severity",
                    "status": "live",
                },
                {
                    "method": "POST",
                    "path": "/api/files/{file_id}/associations",
                    "description": "Link a file to an issue",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/stats",
                    "description": "Global findings severity stats",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/scan-runs",
                    "description": "Scan run history (grouped by scan_run_id)",
                    "status": "live",
                },
                {
                    "method": "GET",
                    "path": "/api/files/_schema",
                    "description": "API discovery (this endpoint)",
                    "status": "live",
                },
            ],
        }
        return JSONResponse(schema, headers={"Cache-Control": "max-age=3600"})

    @router.get("/files/{file_id}")
    async def api_get_file(file_id: str, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Get file record with associations, recent findings, and summary."""
        try:
            data = db.get_file_detail(file_id)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        return JSONResponse(data, headers={"Cache-Control": "no-cache"})

    @router.get("/files/{file_id}/findings")
    async def api_get_file_findings(file_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Get scan findings for a file with pagination."""
        try:
            db.get_file(file_id)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        params = request.query_params
        pagination = _parse_pagination(params)
        if isinstance(pagination, JSONResponse):
            return pagination
        limit, offset = pagination
        try:
            result = db.get_findings_paginated(
                file_id,
                severity=params.get("severity"),
                status=params.get("status"),
                sort=params.get("sort", "updated_at"),
                limit=limit,
                offset=offset,
            )
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse(result, headers={"Cache-Control": "max-age=30"})

    @router.patch("/files/{file_id}/findings/{finding_id}")
    async def api_update_file_finding(
        file_id: str,
        finding_id: str,
        request: Request,
        db: FiligreeDB = Depends(_get_db),
    ) -> JSONResponse:
        """Update finding status and/or linked issue."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        status = body.get("status")
        issue_id = body.get("issue_id")
        if status is None and issue_id is None:
            return _error_response("At least one of status or issue_id is required", "VALIDATION_ERROR", 400)
        if status is not None and not isinstance(status, str):
            return _error_response("status must be a string", "VALIDATION_ERROR", 400)
        if issue_id is not None and not isinstance(issue_id, str):
            return _error_response("issue_id must be a string", "VALIDATION_ERROR", 400)
        try:
            finding = db.update_finding(
                file_id,
                finding_id,
                status=status,
                issue_id=issue_id,
            )
        except KeyError:
            return _error_response(f"Finding not found: {finding_id}", "FINDING_NOT_FOUND", 404)
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse(finding.to_dict())

    @router.get("/files/{file_id}/timeline")
    async def api_get_file_timeline(file_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Get merged timeline of events for a file."""
        params = request.query_params
        pagination = _parse_pagination(params, default_limit=50)
        if isinstance(pagination, JSONResponse):
            return pagination
        limit, offset = pagination
        event_type = params.get("event_type")
        try:
            result = db.get_file_timeline(file_id, limit=limit, offset=offset, event_type=event_type)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        return JSONResponse(result)

    @router.post("/files/{file_id}/associations")
    async def api_add_file_association(file_id: str, request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Link a file to an issue."""
        try:
            db.get_file(file_id)
        except KeyError:
            return _error_response(f"File not found: {file_id}", "FILE_NOT_FOUND", 404)
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        issue_id = body.get("issue_id", "")
        assoc_type = body.get("assoc_type", "")
        if not issue_id or not assoc_type:
            return _error_response("issue_id and assoc_type are required", "VALIDATION_ERROR", 400)
        try:
            db.add_file_association(file_id, issue_id, assoc_type)
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse({"status": "created"}, status_code=201)

    @router.post("/v1/scan-results")
    async def api_scan_results(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Ingest scan results."""
        body = await _parse_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        scan_source = body.get("scan_source", "")
        if not isinstance(scan_source, str) or not scan_source:
            return _error_response("scan_source is required and must be a string", "VALIDATION_ERROR", 400)
        findings = body.get("findings", [])
        if "create_issues" in body:
            return _error_response(
                "create_issues is not supported on scan ingest; create tickets via UI or MCP",
                "VALIDATION_ERROR",
                400,
            )
        mark_unseen = body.get("mark_unseen", False)
        if not isinstance(mark_unseen, bool):
            return _error_response("mark_unseen must be a boolean", "VALIDATION_ERROR", 400)
        status_code = 202 if not findings else 200
        try:
            result = db.process_scan_results(
                scan_source=scan_source,
                findings=findings,
                scan_run_id=body.get("scan_run_id", ""),
                mark_unseen=mark_unseen,
            )
        except ValueError as e:
            return _error_response(str(e), "VALIDATION_ERROR", 400)
        return JSONResponse(result, status_code=status_code)

    @router.get("/scan-runs")
    async def api_scan_runs(request: Request, db: FiligreeDB = Depends(_get_db)) -> JSONResponse:
        """Get scan run history from scan_findings grouped by scan_run_id."""
        params = request.query_params
        limit = _safe_int(params.get("limit", "10"), "limit", min_value=1)
        if isinstance(limit, JSONResponse):
            return limit
        try:
            runs = db.get_scan_runs(limit=limit)
        except sqlite3.Error:
            logger.exception("Failed to query scan runs")
            return _error_response("Failed to query scan runs", "INTERNAL_ERROR", 500)
        return JSONResponse({"scan_runs": runs}, headers={"Cache-Control": "no-cache"})

    return router
