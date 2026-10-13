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


class TestRepetitionStopped:
    """A turn whose provider stream was closed by the repetition guard ends,
    whatever branch would otherwise have continued or nudged it."""

    # iteration=0 throughout: at iteration>=1 the short-stable branch (b)
    # ends a <50-char text first and would mask the branch under test.
    _CONTINUING = [
        dict(assistant_text=_LONG_INTENT, iteration=0),           # textonly grace
        dict(assistant_text="x", code_block_open=True, iteration=0),   # incomplete block
        dict(assistant_text="x", continuation_happened=True, iteration=0),
        dict(assistant_text="Here is the plan:", iteration=0),    # suggests continuation
        dict(assistant_text="x", supports_prefill=False, iteration=0,
             code_block_open=True, last_stop_reason='end_turn'),  # non-prefill block
    ]

    @pytest.mark.parametrize("overrides", _CONTINUING)
    def test_ends(self, decide, overrides):
        verdict, reason, _ = _call(decide, repetition_stopped=True, **overrides)
        assert (verdict, reason) == ('end', 'repetition_stopped')

    @pytest.mark.parametrize("overrides", _CONTINUING)
    def test_same_inputs_do_not_end_without_flag(self, decide, overrides):
        """Positive control: each case really is a continuing branch when the
        flag is absent, so the test above is not vacuous."""
        verdict, _, _ = _call(decide, **overrides)
        assert verdict != 'end'


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
        # now returns the provisional judge_intent verdict — the caller
        # resolves it with a cheap judge call (yes → the continuation side
        # effects, no/error → end). The decider itself no longer decides;
        # substring matching alone false-positived on quoted/drafted
        # content and conditional-on-user futures.
        v = _call(decide, assistant_text=_LONG_INTENT,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, textonly_grace_used=0)
        assert v == ('judge_intent', 'no_prefill_intent_prefilter', None)

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
        # reaches the judge (the question-mark discriminator is subtractive).
        # Uses "let me check" — a phrase that is actually in _INTENT_PHRASES.
        v = _call(decide, assistant_text="Let me check the result to confirm it works.",
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=0, textonly_grace_used=0)
        assert v == ('judge_intent', 'no_prefill_intent_prefilter', None)

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
        # at a LATER iteration must still reach the judge. The old code gated
        # on iteration==0 and cut this off; now the prefilter fires while
        # intent_stalls is under the cap, regardless of iteration.
        v = _call(decide, assistant_text=_LONG_INTENT,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=7, intent_stalls=0)
        assert v == ('judge_intent', 'no_prefill_intent_prefilter', None)

    def test_intent_continue_just_under_stall_cap(self, decide):
        v = _call(decide, assistant_text=_LONG_INTENT,
                  supports_prefill=False, last_stop_reason='end_turn',
                  iteration=2, intent_stalls=2)
        assert v == ('judge_intent', 'no_prefill_intent_prefilter', None)

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


class TestDroppedStream:
    """stop_reason=None is a transport drop, not a model decision.

    last_stop_reason is reset per iteration and assigned only from a
    StreamEnd; every provider's StreamEnd carries a concrete reason.  So
    None means no terminal event arrived.  Live case: iteration 48 of a
    48-round Bedrock run returned one metrics chunk then EOF, the nudge
    budget was already spent, and the turn ended silently.
    """

    def test_dropped_after_tools_retries_even_with_grace_spent(self, decide):
        # The exact live shape: grace exhausted, no stop_reason.  Pre-fix
        # this returned ('end', 'no_activity').
        v = _call(decide, prev_is_tool_result=True, last_stop_reason=None,
                  textonly_grace_used=1, empty_completion_retry_used=0)
        assert v[:2] == ('spend_empty_retry', 'dropped_stream_retry')

    def test_dropped_after_tools_retries_not_nudges(self, decide):
        # Even with grace available, a drop is re-issued, not nudged: the
        # model said nothing, so there is nothing to nudge against.
        # Pre-fix this returned 'nudge'.
        v = _call(decide, prev_is_tool_result=True, last_stop_reason=None,
                  textonly_grace_used=0)
        assert v[:2] == ('spend_empty_retry', 'dropped_stream_retry')

    def test_dropped_after_user_turn_is_labelled_as_drop(self, decide):
        v = _call(decide, prev_is_tool_result=False, last_stop_reason=None)
        assert v[:2] == ('spend_empty_retry', 'dropped_stream_retry')

    def test_drop_retry_budget_exhausted_falls_through(self, decide):
        # Budget spent → the regular ladder applies (nudge if grace allows,
        # else end).  Bounded, so a provider that drops every request cannot
        # loop forever.
        v = _call(decide, prev_is_tool_result=True, last_stop_reason=None,
                  textonly_grace_used=1, empty_completion_retry_used=2)
        assert v[:2] == ('end', 'no_activity')
        v = _call(decide, prev_is_tool_result=True, last_stop_reason=None,
                  textonly_grace_used=0, empty_completion_retry_used=2)
        assert v[:2] == ('nudge', 'empty_after_tools_retry')

    def test_real_end_turn_is_not_treated_as_drop(self, decide):
        # Positive control for the None check: a genuine clean stop with
        # grace spent still ends — the drop path must not widen into it.
        v = _call(decide, prev_is_tool_result=True, last_stop_reason='end_turn',
                  textonly_grace_used=1, empty_completion_retry_used=0)
        assert v[:2] == ('end', 'no_activity')

    def test_refusal_still_wins_over_drop_check(self, decide):
        v = _call(decide, last_stop_reason='refusal', empty_completion_retry_used=0)
        assert v[:2] == ('end', 'refusal')

    @pytest.mark.parametrize("prev_is_tool_result", [False, True])
    def test_drop_after_visible_text_is_never_reissued(self, decide, prev_is_tool_result):
        # The drop branch sits on the no-text path only.  Text that already
        # streamed is on the user's screen; a re-issue would stream a second
        # full response after it.  Whatever the text-present ladder decides,
        # it must not be the drop retry.
        v = _call(decide, assistant_text="Here is half an answ",
                  last_stop_reason=None, prev_is_tool_result=prev_is_tool_result,
                  empty_completion_retry_used=0)
        assert v[1] != 'dropped_stream_retry'
        assert v[0] != 'spend_empty_retry'


class TestEmptyCompletionRetry:
    """Branch (h) — empty after a normal user message."""

    def test_retry_allowed(self, decide):
        v = _call(decide, prev_is_tool_result=False, empty_completion_retry_used=0)
        assert v[:2] == ('spend_empty_retry', 'empty_completion_retry')

    def test_retry_exhausted_ends(self, decide):
        v = _call(decide, prev_is_tool_result=False, empty_completion_retry_used=2)
        assert v[:2] == ('end', 'no_activity')


class TestMaxIterations:
    def test_no_text_last_iteration_ends(self, decide):
        """The cap fires on the loop's last valid index (max_iterations-1)."""
        v = _call(decide, assistant_text="", iteration=199, max_iterations=200,
                  prev_is_tool_result=False, empty_completion_retry_used=2)
        assert v[:2] == ('end', 'max_iterations')

    def test_cap_scales_with_configured_limit(self, decide):
        """The cap tracks max_iterations rather than a hardcoded 100."""
        v = _call(decide, assistant_text="", iteration=999, max_iterations=1000,
                  prev_is_tool_result=False, empty_completion_retry_used=2)
        assert v[:2] == ('end', 'max_iterations')

    def test_mid_budget_empty_is_not_attributed_to_max_iterations(self, decide):
        """Regression: a hardcoded 100 ended turns mid-budget as 'max_iterations'.

        With retries already spent the turn still ends, but the reason must
        reflect the real cause (no_activity), not an iteration cap that has
        900 iterations of budget left.
        """
        v = _call(decide, assistant_text="", iteration=100, max_iterations=1000,
                  prev_is_tool_result=False, empty_completion_retry_used=2)
        assert v[:2] == ('end', 'no_activity')

    def test_mid_budget_empty_after_tools_still_recovers(self, decide):
        """The severe case: recovery must remain available past iteration 100."""
        v = _call(decide, assistant_text="", iteration=100, max_iterations=1000,
                  prev_is_tool_result=True, empty_completion_retry_used=0)
        assert v[0] != 'end'


class TestRefusal:
    """A provider refusal must end the turn immediately, never retry.

    stop_reason 'refusal' arrives with an empty content array, so it is
    indistinguishable from a transient empty completion at the call site.
    It is not transient: the verdict is deterministic for a given payload,
    so re-issuing the identical request is rejected identically while
    re-writing the whole prompt-cache prefix each attempt.

    Reaching this branch depends on last_stop_reason carrying the REAL
    value. Bedrock sends it on message_delta, not message_stop; a parser
    that reads only message_stop falls back to a hardcoded 'end_turn' and
    silently makes every assertion here unreachable in production.
    """

    def test_refusal_ends_turn(self, decide):
        assert _call(decide, last_stop_reason='refusal')[:2] == ('end', 'refusal')

    def test_refusal_does_not_spend_empty_retry(self, decide):
        """The bug this guards: 3 identical rejected calls per submission."""
        v = _call(decide, last_stop_reason='refusal',
                  empty_completion_retry_used=0)
        assert v[0] != 'spend_empty_retry'

    def test_refusal_after_tool_results_does_not_nudge(self, decide):
        """Must precede the empty-after-tools nudge, which would also retry."""
        v = _call(decide, last_stop_reason='refusal',
                  prev_is_tool_result=True, textonly_grace_used=0)
        assert v[:2] == ('end', 'refusal')

    def test_refusal_verdict_is_stable_across_retry_budget(self, decide):
        for used in (0, 1, 2):
            v = _call(decide, last_stop_reason='refusal',
                      empty_completion_retry_used=used)
            assert v[:2] == ('end', 'refusal'), f"budget={used}"

    @pytest.mark.parametrize('reason', ['end_turn', 'max_tokens', 'length',
                                        'stop_sequence', None])
    def test_non_refusal_empty_still_retries(self, decide, reason):
        """Guard against over-broad matching: only 'refusal' short-circuits."""
        v = _call(decide, assistant_text="", last_stop_reason=reason,
                  prev_is_tool_result=False, empty_completion_retry_used=0)
        assert v[0] == 'spend_empty_retry', f"{reason!r} regressed"


class TestOversizedOutput:
    """Output cap hit with nothing delivered must not be a bare retry.

    Live case: max_tokens=4096, two consecutive iterations each returned
    output_tokens=4096 with text_len=0 and no completed tool call.  The
    no-text branch treated both as transient empties, re-issued the
    identical request (deterministically cut at the same cap), and ended
    the turn silently.  The discriminator is output_tokens at the cap:
    a max_tokens stop that reports ~0 output is still a plain empty.
    """

    _CAP = dict(last_stop_reason='max_tokens', output_tokens=4096,
                max_output_tokens=4096)

    def test_after_user_message_corrects_not_retries(self, decide):
        v = _call(decide, prev_is_tool_result=False, **self._CAP)
        assert v[:2] == ('correct_oversized_output', 'oversized_output')

    def test_after_tool_results_corrects_not_nudges(self, decide):
        # The nudge text ("you did not synthesize an answer") is wrong here.
        v = _call(decide, prev_is_tool_result=True, textonly_grace_used=0,
                  **self._CAP)
        assert v[:2] == ('correct_oversized_output', 'oversized_output')

    def test_length_stop_is_a_cutoff_too(self, decide):
        v = _call(decide, last_stop_reason='length', output_tokens=8000,
                  max_output_tokens=8192)
        assert v[0] == 'correct_oversized_output'

    def test_unknown_cap_with_output_still_corrects(self, decide):
        v = _call(decide, last_stop_reason='max_tokens', output_tokens=4096,
                  max_output_tokens=0)
        assert v[0] == 'correct_oversized_output'

    def test_second_occurrence_ends_visibly(self, decide):
        v = _call(decide, oversized_output_used=1, **self._CAP)
        assert v[:2] == ('end', 'oversized_output')

    def test_second_occurrence_ends_even_with_retry_budget_left(self, decide):
        # Must not fall through to the identical re-issue it replaces.
        v = _call(decide, oversized_output_used=1,
                  empty_completion_retry_used=0, **self._CAP)
        assert v[0] == 'end'

    def test_small_output_under_cap_keeps_retry_ladder(self, decide):
        # Positive control for the 90% gate: a max_tokens stop far below the
        # cap is not budget exhaustion.
        v = _call(decide, last_stop_reason='max_tokens', output_tokens=100,
                  max_output_tokens=4096, prev_is_tool_result=False)
        assert v[:2] == ('spend_empty_retry', 'empty_completion_retry')

    def test_zero_output_keeps_retry_ladder(self, decide):
        v = _call(decide, last_stop_reason='max_tokens', output_tokens=0,
                  max_output_tokens=4096, prev_is_tool_result=False)
        assert v[:2] == ('spend_empty_retry', 'empty_completion_retry')

    def test_clean_stop_at_cap_is_not_oversized(self, decide):
        v = _call(decide, last_stop_reason='end_turn', output_tokens=4096,
                  max_output_tokens=4096, prev_is_tool_result=False)
        assert v[0] == 'spend_empty_retry'

    def test_refusal_still_wins(self, decide):
        v = _call(decide, last_stop_reason='refusal', output_tokens=4096,
                  max_output_tokens=4096)
        assert v[:2] == ('end', 'refusal')

    def test_text_present_path_unaffected(self, decide):
        # With text, max_tokens on a non-prefill model keeps its own branch.
        v = _call(decide, assistant_text="partial answer", supports_prefill=False,
                  **self._CAP)
        assert v[:2] == ('inject_max_tokens', 'max_tokens_continue')


class TestOversizedOutputHelpers:

    def test_note_names_the_cap_and_the_remedy(self):
        note = StreamingToolExecutor._oversized_output_note(4096)
        assert "4096 tokens" in note
        assert "smaller tool calls" in note

    def test_note_without_cap(self):
        note = StreamingToolExecutor._oversized_output_note(0)
        assert "output token limit" in note and "0 tokens" not in note

    def test_append_to_list_content(self):
        conv = [{'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a'}]}]
        StreamingToolExecutor._append_to_last_user_turn(conv, "NOTE")
        assert len(conv) == 1
        assert conv[0]['content'][-1] == {'type': 'text', 'text': 'NOTE'}

    def test_append_to_string_content(self):
        conv = [{'role': 'user', 'content': 'hello'}]
        StreamingToolExecutor._append_to_last_user_turn(conv, "NOTE")
        assert conv == [{'role': 'user', 'content': 'hello\n\nNOTE'}]

    def test_append_after_assistant_adds_user_turn(self):
        conv = [{'role': 'assistant', 'content': 'x'}]
        StreamingToolExecutor._append_to_last_user_turn(conv, "NOTE")
        assert conv[-1] == {'role': 'user', 'content': 'NOTE'}


class TestMaxTokensSource:
    """_build_provider_config records which of skill / env / model_config
    produced max_output_tokens.  The streaming loop logs it
    (MAX_TOKENS_SOURCE) and names it in the oversized-output error; before
    this, a cap set process-wide by the settings route looked identical to
    the model's own limit in the logs."""

    def _ex(self, **attrs):
        ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
        ex.model_config = {"max_output_tokens": 64000}
        for k, v in attrs.items():
            setattr(ex, k, v)
        return ex

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for k in ("ZIYA_MAX_OUTPUT_TOKENS", "ZIYA_MAX_TOKENS"):
            monkeypatch.delenv(k, raising=False)

    def test_model_config(self):
        ex = self._ex()
        assert int(ex._build_provider_config(0).max_output_tokens) == 64000
        assert ex._max_tokens_source == 'model_config'

    def test_env_wins_over_model_config(self, monkeypatch):
        monkeypatch.setenv("ZIYA_MAX_OUTPUT_TOKENS", "4096")
        ex = self._ex()
        assert int(ex._build_provider_config(0).max_output_tokens) == 4096
        assert ex._max_tokens_source == 'env'

    def test_skill_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("ZIYA_MAX_OUTPUT_TOKENS", "4096")
        ex = self._ex(max_tokens_override=12000)
        assert int(ex._build_provider_config(0).max_output_tokens) == 12000
        assert ex._max_tokens_source == 'skill'

    @pytest.mark.parametrize('source,needle', [
        ('env', 'ZIYA_MAX_OUTPUT_TOKENS'),
        ('skill', 'maxOutputTokens'),
        ('throttle', 'throttling'),
        ('model_config', "model's configured limit"),
    ])
    def test_hint_names_the_source(self, source, needle):
        assert needle in StreamingToolExecutor._max_tokens_source_hint(source)

    def test_unknown_source_still_gives_advice(self):
        assert "smaller pieces" in StreamingToolExecutor._max_tokens_source_hint('?')


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
