"""
Characterization (golden-master) tests for _decide_no_tool_outcome in
app.streaming_tool_executor.StreamingToolExecutor.

This pins the pure no-tool continuation decider's verdict for every
branch BEFORE it is wired into the live streaming loop (the inline
if/elif ladder it mirrors). The decider is currently dead code; these
tests prove it is a faithful 1:1 of the live ladder so the wiring diff
can be shown behavior-identical.

One branch deliberately encodes a KNOWN BUG (bug ①): a non-prefill model
that produces text and stops on end_turn returns ('end','no_prefill_end')
EVEN WHEN the text announces intent to act ("Let me run the test…"). The
intent-aware branches sit AFTER the non-prefill gate and are structurally
unreachable for non-prefill models. The fix (making branch (a) intent-
aware) flips exactly the row marked KNOWN_BUG; that lands in a separate
diff so the change is a single visible verdict flip.

verdict ∈ {end, continue, spend_textonly_continue, inject_max_tokens,
           nudge, spend_empty_retry}
"""

import pytest

from app.streaming_tool_executor import StreamingToolExecutor


@pytest.fixture
def decide():
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)
    return inst._decide_no_tool_outcome


def _call(decide, **overrides):
    """Call the decider with sensible defaults, overriding per-case."""
    kwargs = dict(
        assistant_text="",
        supports_prefill=True,
        tools_executed=False,
        last_stop_reason='end_turn',
        iteration=1,
        max_iterations=200,
        continuation_happened=False,
        code_block_open=False,
        blocked_tools=0,
        prev_is_tool_result=False,
        textonly_grace_used=0,
        empty_completion_retry_used=0,
    )
    kwargs.update(overrides)
    return decide(**kwargs)


# A 20+ word sentence ending in punctuation, with no intent phrase —
# exercises branch (e)'s "complete_response" path.
_LONG_PLAIN = (
    "This is a complete and final answer that contains well over twenty "
    "words so that it trips the substantial-commentary branch cleanly here."
)
# Same length but announces intent to act — exercises the intent path.
_LONG_INTENT = (
    "Let me check the configuration and verify the result before I write "
    "anything further because there is clearly more work remaining to do here."
)


class TestRunawayGuard:
    def test_blocked_tools_ends(self, decide):
        assert _call(decide, blocked_tools=3)[:2] == ('end', 'runaway_loop')


class TestNonPrefillGate:
    """Branch (a) — decided by stop_reason==max_tokens ONLY."""

    def test_max_tokens_injects_continuation(self, decide):
        v = _call(decide, assistant_text="partial",
                  supports_prefill=False, last_stop_reason='max_tokens',
                  iteration=0)
        assert v[:2] == ('inject_max_tokens', 'max_tokens_continue')

    def test_end_turn_ends_even_with_plain_text(self, decide):
        v = _call(decide, assistant_text="done",
                  supports_prefill=False, last_stop_reason='end_turn')
        assert v[:2] == ('end', 'no_prefill_end')

    def test_FIXED_end_turn_with_intent_continues(self, decide):
        # Bug ① FIXED: non-prefill + intent text + clean end_turn at iter 0
        # now grants a continuation so the announced action actually runs,
        # instead of being cut off by no_prefill_end.
        v = _call(decide, assistant_text=_LONG_INTENT,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, textonly_grace_used=0)
        assert v[:2] == ('inject_intent_continue', 'no_prefill_intent_continue')

    def test_intent_but_yields_to_user_does_not_continue(self, decide):
        # Bug #2 fix: intent phrasing that ENDS in a question mark is the
        # model handing the turn back, not announcing an action it's about
        # to take. Must NOT re-prompt.
        text = _LONG_INTENT.rstrip('.') + ', or should I take a different approach?'
        v = _call(decide, assistant_text=text,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, textonly_grace_used=0)
        assert v[:2] == ('end', 'no_prefill_end')

    def test_intent_without_question_still_continues(self, decide):
        # The bug ① fix is preserved: intent NOT ending in a question still
        # gets the continuation (the discriminator is purely subtractive).
        # Uses "let me check" — a phrase that is actually in _INTENT_PHRASES.
        v = _call(decide, assistant_text="Let me check the result to confirm it works.",
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, textonly_grace_used=0)
        assert v[:2] == ('inject_intent_continue', 'no_prefill_intent_continue')

    def test_intent_with_trailing_whitespace_after_question(self, decide):
        # rstrip() handles trailing newline/space after the question mark.
        v = _call(decide, assistant_text="Let me check — which file did you mean?  \n",
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, textonly_grace_used=0)
        assert v[:2] == ('end', 'no_prefill_end')

    def test_intent_continue_bounded_by_stall_cap(self, decide):
        # The bound is now intent_stalls (consecutive no-progress intents),
        # NOT the old one-shot textonly_grace. At the cap, stop re-prompting.
        v = _call(decide, assistant_text=_LONG_INTENT,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, intent_stalls=3)
        assert v[:2] == ('end', 'no_prefill_end')

    def test_intent_continue_allowed_after_prior_work(self, decide):
        # REGRESSION (the cutoff-mid-turn bug): a model that announced intent
        # early, did a long productive tool cycle, then announces more intent
        # at a LATER iteration must still be continued. The old code gated on
        # iteration==0 and cut this off; now it continues while intent_stalls
        # is under the cap, regardless of iteration.
        v = _call(decide, assistant_text=_LONG_INTENT,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=7, intent_stalls=0)
        assert v[:2] == ('inject_intent_continue', 'no_prefill_intent_continue')

    def test_intent_continue_just_under_stall_cap(self, decide):
        v = _call(decide, assistant_text=_LONG_INTENT,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=2, intent_stalls=2)
        assert v[:2] == ('inject_intent_continue', 'no_prefill_intent_continue')

    def test_intent_continue_requires_clean_stop(self, decide):
        # max_tokens is handled by the cutoff path above, not the intent
        # path — confirm the intent branch is gated on a CLEAN stop only.
        # (max_tokens at iter 0 → max_tokens_continue, tested above.)
        v = _call(decide, assistant_text=_LONG_INTENT,
                  supports_prefill=False, last_stop_reason='max_tokens',
                  iteration=0)
        assert v[:2] == ('inject_max_tokens', 'max_tokens_continue')

    def test_plain_text_non_prefill_still_ends(self, decide):
        # No intent phrase → no continuation grant, still ends cleanly.
        v = _call(decide, assistant_text=_LONG_PLAIN,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0)
        assert v[:2] == ('end', 'no_prefill_end')


class TestNonPrefillOpenBlockContinue:
    """Branch (a): non-prefill model cut off mid-code-block on a clean stop.

    An unclosed fence is objective truncation evidence, so the decider
    grants a bounded 'block_continue' to finish the block — reachable only
    because this check sits inside the non-prefill gate, above the
    (unreachable-for-non-prefill) code_block_open branch (c).
    """

    def test_open_block_clean_stop_continues(self, decide):
        v = _call(decide, assistant_text=_LONG_PLAIN,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, code_block_open=True, block_continue_stalls=0)
        assert v[:2] == ('block_continue', 'no_prefill_block_continue')

    def test_open_block_continues_even_at_later_iterations(self, decide):
        # Unlike intent-continue (iter 0 only), a truncated block can be
        # continued at any iteration — completion may span several rounds.
        v = _call(decide, assistant_text=_LONG_PLAIN,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=4, code_block_open=True, block_continue_stalls=0)
        assert v[:2] == ('block_continue', 'no_prefill_block_continue')

    def test_max_tokens_takes_precedence_over_open_block(self, decide):
        # max_tokens path is checked first; an open block at a real cutoff
        # still routes through max_tokens_continue, not block_continue.
        v = _call(decide, assistant_text=_LONG_PLAIN,
                  supports_prefill=False, last_stop_reason='max_tokens',
                  iteration=0, code_block_open=True)
        assert v[:2] == ('inject_max_tokens', 'max_tokens_continue')

    def test_open_block_bounded_by_stall_cap(self, decide):
        # At the cap, stop continuing (wedged-open fence with no progress).
        v = _call(decide, assistant_text=_LONG_PLAIN,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, code_block_open=True, block_continue_stalls=5)
        assert v[:2] == ('end', 'no_prefill_end')

    def test_open_block_just_under_cap_continues(self, decide):
        v = _call(decide, assistant_text=_LONG_PLAIN,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, code_block_open=True, block_continue_stalls=4)
        assert v[:2] == ('block_continue', 'no_prefill_block_continue')

    def test_closed_block_does_not_trigger_block_continue(self, decide):
        # No open block → falls through to the normal no_prefill_end.
        v = _call(decide, assistant_text=_LONG_PLAIN,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, code_block_open=False)
        assert v[:2] == ('end', 'no_prefill_end')

    def test_prefill_model_unaffected_by_open_block_branch(self, decide):
        # The whole branch is gated on not-supports_prefill; a prefill model
        # with an open block uses branch (c) ('continue'), not block_continue.
        v = _call(decide, assistant_text=_LONG_PLAIN,
                  supports_prefill=True, last_stop_reason='end_turn',
                  iteration=2, code_block_open=True)
        assert v[:2] == ('continue', 'incomplete_block')


class TestShortStable:
    """Branch (b) — short text at iteration>=1."""

    def test_short_plain_ends(self, decide):
        assert _call(decide, assistant_text="ok", iteration=1)[:2] == ('end', 'short_stable')

    def test_short_intent_grants_grace(self, decide):
        v = _call(decide, assistant_text="let me check", iteration=1,
                  textonly_grace_used=0)
        assert v[:2] == ('spend_textonly_continue', 'short_intent_grace')

    def test_short_intent_grace_exhausted_ends(self, decide):
        v = _call(decide, assistant_text="let me check", iteration=1,
                  textonly_grace_used=3)
        assert v[:2] == ('end', 'short_stable')


class TestIncompleteBlock:
    def test_code_block_open_continues(self, decide):
        v = _call(decide, assistant_text=_LONG_PLAIN, code_block_open=True)
        assert v[:2] == ('continue', 'incomplete_block')


class TestContinuationHappened:
    def test_continuation_continues(self, decide):
        v = _call(decide, assistant_text=_LONG_PLAIN, continuation_happened=True)
        assert v[:2] == ('continue', 'continuation_complete')


class TestSubstantialCommentary:
    """Branch (e) — 20+ words ending in punctuation."""

    def test_long_plain_ends(self, decide):
        v = _call(decide, assistant_text=_LONG_PLAIN, iteration=0)
        assert v[:2] == ('end', 'complete_response')

    def test_long_intent_iter0_grants_grace(self, decide):
        v = _call(decide, assistant_text=_LONG_INTENT, iteration=0,
                  textonly_grace_used=0)
        assert v[:2] == ('spend_textonly_continue', 'textonly_grace')

    def test_long_intent_iter1_ends(self, decide):
        # grace only granted at iteration 0
        v = _call(decide, assistant_text=_LONG_INTENT, iteration=1)
        assert v[:2] == ('end', 'complete_response')


class TestEmptyAfterTools:
    """Branch (g) — Option A stop_reason gate."""

    def test_end_turn_caps_at_one(self, decide):
        # clean stop → cap 1: first nudge allowed, second ends
        assert _call(decide, prev_is_tool_result=True, last_stop_reason='end_turn',
                     textonly_grace_used=0)[:2] == ('nudge', 'empty_after_tools_retry')
        assert _call(decide, prev_is_tool_result=True, last_stop_reason='end_turn',
                     textonly_grace_used=1)[:2] == ('end', 'no_activity')

    def test_max_tokens_keeps_three(self, decide):
        # genuine cutoff → cap 3
        assert _call(decide, prev_is_tool_result=True, last_stop_reason='max_tokens',
                     textonly_grace_used=2)[:2] == ('nudge', 'empty_after_tools_retry')


class TestEmptyCompletionRetry:
    """Branch (h) — empty after a normal user message."""

    def test_retry_allowed(self, decide):
        v = _call(decide, prev_is_tool_result=False, empty_completion_retry_used=0)
        assert v[:2] == ('spend_empty_retry', 'empty_completion_retry')

    def test_retry_exhausted_ends(self, decide):
        v = _call(decide, prev_is_tool_result=False, empty_completion_retry_used=2)
        assert v[:2] == ('end', 'no_activity')


class TestMaxIterations:
    def test_no_text_high_iteration_ends(self, decide):
        v = _call(decide, assistant_text="", iteration=100,
                  prev_is_tool_result=False, empty_completion_retry_used=2)
        assert v[:2] == ('end', 'max_iterations')


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
