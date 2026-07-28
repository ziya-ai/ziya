"""Characterization tests for StreamingToolExecutor._has_unclosed_inline_code.

Guards the NO_PREFILL_BLOCK "open_inline" continuation branch added after a
live claude-fable-5 failure: the model emitted the 35-char reply

    The bug is clear: `DirectoryBrowser

on a CLEAN end_turn. The text ends inside an unterminated inline-code span,
but the fence tracker only accounts for ```-delimited blocks, so the gate
fell through to ('end', 'no_prefill_end') and cut the answer off mid-token.

The helper is the inline sibling of the open_fence evidence: it strips paired
``` fenced regions (whose inner backticks are literal) and reports whether an
odd number of single-backtick delimiters remain (an unmatched open span).
"""
import pytest

from app.streaming_tool_executor import StreamingToolExecutor


@pytest.fixture
def has_unclosed():
    # The helper uses only `re` and `self` (no __init__ side effects), so a
    # bare instance suffices — mirrors the tracker_fn fixture convention.
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)
    return inst._has_unclosed_inline_code


class TestUnclosedInlineCode:
    def test_observed_fable5_failure(self, has_unclosed):
        # The exact string that terminated the stream mid-identifier.
        assert has_unclosed("The bug is clear: `DirectoryBrowser") is True

    def test_closed_single_span(self, has_unclosed):
        assert has_unclosed("Use `foo` then continue") is False

    def test_two_closed_spans(self, has_unclosed):
        assert has_unclosed("Open `one` and `two` closed") is False

    def test_no_backticks(self, has_unclosed):
        assert has_unclosed("No code here at all") is False

    def test_empty_text(self, has_unclosed):
        assert has_unclosed("") is False

    def test_fenced_block_only_is_not_inline(self, has_unclosed):
        # Paired fence, no dangling inline span -> not truncation evidence.
        assert has_unclosed("```\npython\ncode\n```") is False

    def test_fenced_block_then_open_inline(self, has_unclosed):
        # Fence closes, then an inline span opens and never closes.
        assert has_unclosed("```\ncode\n``` then `inline") is True

    def test_fenced_block_with_inner_backticks_ignored(self, has_unclosed):
        # Backticks inside a paired fence are literal, not delimiters.
        assert has_unclosed("```\nuse `x` here\n```") is False

    def test_normal_prose_end(self, has_unclosed):
        assert has_unclosed("All done.") is False
