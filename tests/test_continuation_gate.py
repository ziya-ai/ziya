"""
Characterization + regression tests for the incomplete-code-block
continuation gate in app.message_stop_handler.

Context: the fence tracker (_update_code_block_tracker) can set
in_block=True when the model merely *quotes* triple-backtick fence
markers in narrative prose (e.g. discussing "literal ``` in the body").
When the stream then ends cleanly (end_turn), the old gate fired
_continue_incomplete_code_block anyway, fabricating user-invisible
repeat turns — an over-nudge loop observed repeatedly in practice.

should_continue_incomplete_block() is the stop-reason guard (step 1):
continue ONLY when the tracker says we're in a block AND the model was
genuinely cut off (max_tokens/length). A clean stop with in_block=True
is treated as a tracker false-positive and not continued.

These tests pin that contract as a full truth table so the behavior
cannot silently regress — this region has been historically unstable.
"""

import pytest

from app.message_stop_handler import (
    should_continue_incomplete_block,
    _CUTOFF_STOP_REASONS,
)


# Every stop_reason we have observed across providers. Bedrock reports
# max_tokens correctly; anthropic_direct/google_direct hardcode end_turn;
# openai_direct emits raw length/stop/tool_calls.
_CUTOFF = ['max_tokens', 'length']
_CLEAN = ['end_turn', 'stop', 'stop_sequence', 'tool_calls', None]


class TestContinuationGateTruthTable:
    """Full (in_block × stop_reason) matrix."""

    @pytest.mark.parametrize("stop_reason", _CUTOFF)
    def test_in_block_and_cutoff_continues(self, stop_reason):
        # The only case that should continue: real truncation mid-block.
        assert should_continue_incomplete_block(True, stop_reason) is True

    @pytest.mark.parametrize("stop_reason", _CLEAN)
    def test_in_block_and_clean_stop_does_not_continue(self, stop_reason):
        # The bug case: tracker says in_block but the model ended cleanly.
        # Treated as a quoted-fence false positive — must NOT continue.
        assert should_continue_incomplete_block(True, stop_reason) is False

    @pytest.mark.parametrize("stop_reason", _CUTOFF + _CLEAN)
    def test_not_in_block_never_continues(self, stop_reason):
        # No open block → never continue, regardless of stop_reason.
        assert should_continue_incomplete_block(False, stop_reason) is False


class TestContinuationGateEdgeCases:
    def test_falsy_in_block_values_do_not_continue(self):
        # in_block may arrive as None/0/'' from .get() on a missing key.
        for falsy in (None, 0, '', False):
            assert should_continue_incomplete_block(falsy, 'max_tokens') is False

    def test_unknown_stop_reason_does_not_continue(self):
        # Conservative default: an unrecognized stop_reason is treated as
        # a clean stop (do not fabricate a continuation).
        assert should_continue_incomplete_block(True, 'some_future_reason') is False

    def test_cutoff_set_contents(self):
        # Pin the membership so widening/narrowing it is a deliberate,
        # reviewed change rather than an accident.
        assert _CUTOFF_STOP_REASONS == frozenset({'max_tokens', 'length'})


class TestReturnsStrictBool:
    """Guard against truthy/falsy leakage — callers use it in `and`/`while`."""

    def test_returns_actual_bool_true(self):
        assert should_continue_incomplete_block(True, 'max_tokens') is True

    def test_returns_actual_bool_false(self):
        assert should_continue_incomplete_block(1, 'end_turn') is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
