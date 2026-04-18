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
