from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_detail_view_escapes_dependency_status_text() -> None:
    text = _read("src/filigree/static/js/views/detail.js")
    assert "${escHtml(det.status || \"\")}" in text
    assert "${det.status}" not in text


def test_workflow_plan_escapes_step_status_text() -> None:
    text = _read("src/filigree/static/js/views/workflow.js")
    assert "escHtml(s.status || \"\")" in text
    assert "s.status +" not in text


def test_kanban_card_escapes_type_and_status_text() -> None:
    text = _read("src/filigree/static/js/views/kanban.js")
    assert "escHtml(issue.type.replace(/_/g, \" \"))" in text
    assert "escHtml(issue.status || \"\")" in text
    assert "${issue.type.replace(/_/g, \" \")}" not in text
    assert "${issue.status}" not in text


def test_move_status_modal_escapes_transition_button_text() -> None:
    text = _read("src/filigree/static/js/app.js")
    assert "${escHtml(t.to)}</button>" in text
    assert "${t.to}</button>" not in text
