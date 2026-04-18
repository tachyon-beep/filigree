"""Structural tests for the 2.0 envelope TypedDicts."""

from __future__ import annotations

from typing import get_type_hints

from filigree.types.api import BatchFailure, BatchResponse, ListResponse, SlimIssue


def test_batch_response_fields() -> None:
    hints = get_type_hints(BatchResponse)
    assert "succeeded" in hints
    assert "failed" in hints
    assert "newly_unblocked" in hints  # NotRequired, still listed in hints


def test_batch_failure_fields() -> None:
    hints = get_type_hints(BatchFailure)
    assert set(hints.keys()) == {"item_id", "error", "code"}


def test_list_response_fields() -> None:
    hints = get_type_hints(ListResponse)
    assert "items" in hints
    assert "has_more" in hints
    assert "next_offset" in hints  # NotRequired, still listed


def test_batch_response_is_generic() -> None:
    # A parameterized form should be usable
    inst: BatchResponse[SlimIssue] = {"succeeded": [], "failed": []}
    assert inst["succeeded"] == []


def test_error_code_enum_members() -> None:
    from filigree.types.api import ErrorCode

    # Exact 9-member set — locked by spec §1e
    expected = {
        "VALIDATION",
        "NOT_FOUND",
        "CONFLICT",
        "INVALID_TRANSITION",
        "PERMISSION",
        "NOT_INITIALIZED",
        "IO",
        "INVALID_API_URL",
        "STOP_FAILED",
    }
    assert {e.name for e in ErrorCode} == expected


def test_error_code_is_str_subclass() -> None:
    from filigree.types.api import ErrorCode

    assert ErrorCode.VALIDATION == "VALIDATION"
    assert isinstance(ErrorCode.VALIDATION, str)


def test_error_response_flat_shape() -> None:
    from filigree.types.api import ErrorCode, ErrorResponse

    # Without details
    err1: ErrorResponse = {"error": "nope", "code": ErrorCode.VALIDATION}
    assert err1["code"] == ErrorCode.VALIDATION

    # With details (optional field)
    err2: ErrorResponse = {
        "error": "conflict",
        "code": ErrorCode.CONFLICT,
        "details": {"issue_id": "abc", "current_assignee": "alice"},
    }
    assert err2["details"]["issue_id"] == "abc"


def test_error_response_has_no_legacy_fields() -> None:
    from typing import get_type_hints

    from filigree.types.api import ErrorResponse

    hints = get_type_hints(ErrorResponse)
    assert set(hints.keys()) == {"error", "code", "details"}
