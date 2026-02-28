"""Tests for the shared validation module."""

from __future__ import annotations

from filigree.validation import sanitize_actor


class TestSanitizeActor:
    """sanitize_actor() pure function tests."""

    def test_valid_simple(self) -> None:
        cleaned, err = sanitize_actor("alice")
        assert cleaned == "alice"
        assert err is None

    def test_strips_whitespace(self) -> None:
        cleaned, err = sanitize_actor("  spaced  ")
        assert cleaned == "spaced"
        assert err is None

    def test_at_max_length(self) -> None:
        cleaned, err = sanitize_actor("a" * 128)
        assert cleaned == "a" * 128
        assert err is None

    def test_over_max_length(self) -> None:
        cleaned, err = sanitize_actor("a" * 129)
        assert cleaned == ""
        assert err is not None
        assert "128" in err

    def test_empty_string(self) -> None:
        cleaned, err = sanitize_actor("")
        assert cleaned == ""
        assert err is not None
        assert "empty" in err

    def test_whitespace_only(self) -> None:
        cleaned, err = sanitize_actor("   ")
        assert cleaned == ""
        assert err is not None
        assert "empty" in err

    def test_not_a_string(self) -> None:
        cleaned, err = sanitize_actor(123)
        assert cleaned == ""
        assert err is not None
        assert "string" in err

    def test_none_value(self) -> None:
        cleaned, err = sanitize_actor(None)
        assert cleaned == ""
        assert err is not None
        assert "string" in err

    def test_control_char_null(self) -> None:
        cleaned, err = sanitize_actor("\x00bad")
        assert cleaned == ""
        assert err is not None
        assert "control" in err.lower()

    def test_control_char_newline(self) -> None:
        cleaned, err = sanitize_actor("\nbad")
        assert cleaned == ""
        assert err is not None
        assert "control" in err.lower()

    def test_bom(self) -> None:
        cleaned, err = sanitize_actor("\ufeff")
        assert cleaned == ""
        assert err is not None

    def test_zero_width_space(self) -> None:
        cleaned, err = sanitize_actor("\u200b")
        assert cleaned == ""
        assert err is not None

    def test_rtl_override(self) -> None:
        cleaned, err = sanitize_actor("\u202e")
        assert cleaned == ""
        assert err is not None

    def test_unicode_name_allowed(self) -> None:
        """Non-ASCII normal letters are fine."""
        cleaned, err = sanitize_actor("café-bot")
        assert cleaned == "café-bot"
        assert err is None
