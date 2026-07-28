"""
Tests for app.cli._response_looks_incomplete.

This pure, module-level discriminator decides whether the CLI's OUTER
continuation loop (in ask()) should auto-continue a diff-free model response.
It is independent of the inner StreamingToolExecutor's own intent-continuation
logic (intent_stalls, decider, etc.) -- this function exists specifically
because that inner mechanism's decision is invisible to the outer loop, so a
syntactically complete sentence narrating unexecuted intent ("Let me check
X.") was previously accepted as final with zero continuation attempted.

Reproduces the two real trigger messages observed live in session:
  - the user's original trigger message (ends in '.', narrates an action)
  - the assistant's own live failure in the same session (same shape)
"""

import pytest

from unittest.mock import AsyncMock, patch

from app.cli import (
    _response_looks_incomplete,
    _CLI_INTENT_PHRASES,
    _adjudicate_continuation,
    _looks_truncated,
)


class TestTruncationDetection:
    """Pre-existing truncation triggers -- must remain unchanged."""

    def test_trailing_colon_is_incomplete(self):
        looks_incomplete, has_intent = _response_looks_incomplete(
            "Here is the plan:"
        )
        assert looks_incomplete is True
        assert has_intent is False

    def test_trailing_ellipsis_is_incomplete(self):
        looks_incomplete, has_intent = _response_looks_incomplete(
            "Processing the next file..."
        )
        assert looks_incomplete is True
        assert has_intent is False

    def test_long_response_without_terminal_punctuation_is_incomplete(self):
        body = "x" * 101
        looks_incomplete, has_intent = _response_looks_incomplete(body)
        assert looks_incomplete is True
        assert has_intent is False

    def test_short_response_without_terminal_punctuation_is_complete(self):
        # Under the 100-char threshold, missing terminal punctuation alone
        # must NOT trigger -- this guards against over-firing on short
        # fragments that are legitimately complete (e.g. a bare filename).
        looks_incomplete, has_intent = _response_looks_incomplete("app/cli.py")
        assert looks_incomplete is False
        assert has_intent is False

    @pytest.mark.parametrize("terminator", ['.', '!', '?', ')'])
    def test_terminal_punctuation_alone_is_complete(self, terminator):
        body = ("x" * 100) + terminator
        looks_incomplete, has_intent = _response_looks_incomplete(body)
        assert looks_incomplete is False
        assert has_intent is False


class TestUnexecutedIntentDetection:
    """The new trigger: narrates an action without taking it."""

    def test_original_user_trigger_message(self):
        # The exact message the user pasted that started this investigation.
        response = (
            "Committing to fixing this now — no more asking for logs that "
            "don't exist. Let me get the ground truth by reading the actual "
            "ask() method in full, since my last two reads were fragments "
            "and I need the complete control flow before touching anything."
        )
        looks_incomplete, has_intent = _response_looks_incomplete(response)
        assert has_intent is True
        assert looks_incomplete is True

    def test_assistant_own_live_failure_message(self):
        # The assistant's own 249-char response that reproduced the bug live
        # in this session ("...ask() returned, looping to prompt").
        response = (
            "Confirmed live, in real time — I said \"let me get the ground "
            "truth\" and returned 249 chars with no tool call. That's the "
            "exact bug, caught in the act of me committing to investigate "
            "it. Reading the file now, for real."
        )
        looks_incomplete, has_intent = _response_looks_incomplete(response)
        assert has_intent is True
        assert looks_incomplete is True

    @pytest.mark.parametrize("phrase", [
        "let me check", "let me look", "let me examine", "let me read",
        "i'll check", "i'll verify", "i'll run", "before writing",
        "first, let me", "i need to check",
    ])
    def test_each_intent_phrase_triggers(self, phrase):
        response = f"That's a good point. {phrase.capitalize()} the file to confirm."
        looks_incomplete, has_intent = _response_looks_incomplete(response)
        assert has_intent is True
        assert looks_incomplete is True

    def test_intent_phrase_case_insensitive(self):
        response = "LET ME CHECK the exact declaration before proceeding."
        looks_incomplete, has_intent = _response_looks_incomplete(response)
        assert has_intent is True
        assert looks_incomplete is True


class TestQuestionMarkExclusion:
    """Trailing '?' means the model is handing the turn back to the user --
    must NOT be treated as unexecuted intent, matching the server-side
    discriminator's rule."""

    def test_intent_phrase_ending_in_question_does_not_trigger(self):
        response = (
            "Should I check the config file first, or would you rather I "
            "look at the logs?"
        )
        looks_incomplete, has_intent = _response_looks_incomplete(response)
        assert has_intent is False
        assert looks_incomplete is False

    def test_intent_phrase_with_trailing_whitespace_after_question(self):
        response = "Let me check — which file did you mean?  \n"
        looks_incomplete, has_intent = _response_looks_incomplete(response)
        assert has_intent is False
        assert looks_incomplete is False


class TestNoFalsePositives:
    """Complete responses with no intent phrase and terminal punctuation
    must not trigger either discriminator."""

    def test_plain_complete_answer_is_not_incomplete(self):
        response = "The fix has been applied and verified. All tests pass."
        looks_incomplete, has_intent = _response_looks_incomplete(response)
        assert has_intent is False
        assert looks_incomplete is False

    def test_intent_word_as_substring_of_unrelated_word_does_not_trigger(self):
        # "read" appears in "already" -- must not false-positive via
        # substring matching on a phrase that isn't actually present.
        response = "The file has already been fully updated and saved."
        looks_incomplete, has_intent = _response_looks_incomplete(response)
        assert has_intent is False
        assert looks_incomplete is False


class TestPhraseListIntegrity:
    def test_phrase_list_is_nonempty_and_lowercase(self):
        assert len(_CLI_INTENT_PHRASES) > 0
        assert all(p == p.lower() for p in _CLI_INTENT_PHRASES)


# The substring prefilter above cannot tell a genuine dangling announcement
# from an intent phrase in quoted content or one that was actually carried
# out.  _adjudicate_continuation wraps it with cheap-model adjudication.
# Every expected value below was measured against the real implementation.

GENUINE_DANGLING = (
    "I have applied the change to the handler.\n\n"
    "Now let me run the test suite to confirm nothing regressed."
)

QUOTED_INTENT = (
    "You asked about this line from the log:\n\n"
    '    "Now let me run the test suite to confirm."\n\n'
    "That phrase comes from the trace output, not from me. The counter is at five."
)

SATISFIED_INTENT = (
    "Let me verify the running copy actually changed.\n\n"
    "I ran the hash comparison and both files match at md5 966d2668. "
    "The installed copy is current."
)

TRUNCATED_AND_INTENT = (
    "Here is a long paragraph of explanation that easily exceeds one hundred "
    "characters so the truncation trigger fires independently of anything "
    "else at all.\n\nNow let me check the config"
)

PURELY_TRUNCATED = (
    "Here is a long paragraph of explanation that easily exceeds one hundred "
    "characters and simply stops without any terminal punctuation at all"
)


def _judge(**kwargs):
    """Patch the judge at its source module.

    _adjudicate_continuation imports judge_dangling_intent INSIDE the
    function body, so the name is resolved at call time and patching the
    source module is what takes effect.
    """
    return patch(
        "app.services.intent_judge.judge_dangling_intent",
        new=AsyncMock(**kwargs),
    )


class TestJudgeGate:
    """The judge overrules the prefilter's intent verdict, not its
    truncation verdict."""

    async def test_genuine_dangling_intent_still_continues(self):
        # The whole point of the feature: a real unexecuted announcement
        # must survive the judge, or the gate has just disabled it.
        assert _response_looks_incomplete(GENUINE_DANGLING) == (True, True)
        with _judge(return_value=True):
            assert await _adjudicate_continuation(GENUINE_DANGLING) == (True, True)

    async def test_quoted_intent_phrase_is_suppressed(self):
        # Prefilter matches the quoted phrase; judge overrules.
        assert _response_looks_incomplete(QUOTED_INTENT) == (True, True)
        with _judge(return_value=False):
            assert await _adjudicate_continuation(QUOTED_INTENT) == (False, False)

    async def test_satisfied_intent_is_suppressed(self):
        # The live false positive: announces work in the opening line and
        # then actually does it.
        assert _response_looks_incomplete(SATISFIED_INTENT) == (True, True)
        with _judge(return_value=False):
            assert await _adjudicate_continuation(SATISFIED_INTENT) == (False, False)

    async def test_truncation_survives_a_negative_judge_verdict(self):
        # A response that is BOTH truncated and intent-matching must still
        # continue on truncation alone after the judge drops the intent
        # trigger.  Regression guard for collapsing both triggers into one
        # boolean at the call site.
        assert _response_looks_incomplete(TRUNCATED_AND_INTENT) == (True, True)
        assert _looks_truncated(TRUNCATED_AND_INTENT) is True
        with _judge(return_value=False):
            assert await _adjudicate_continuation(TRUNCATED_AND_INTENT) == (True, False)


class TestJudgeGateCost:
    async def test_pure_truncation_makes_no_model_call(self):
        # The judge is billable.  It must run ONLY when intent is the
        # trigger, never on the common truncation path.
        assert _response_looks_incomplete(PURELY_TRUNCATED) == (True, False)
        mock = AsyncMock(return_value=True)
        with patch("app.services.intent_judge.judge_dangling_intent", new=mock):
            assert await _adjudicate_continuation(PURELY_TRUNCATED) == (True, False)
        assert mock.await_count == 0

    async def test_complete_response_makes_no_model_call(self):
        mock = AsyncMock(return_value=True)
        with patch("app.services.intent_judge.judge_dangling_intent", new=mock):
            assert await _adjudicate_continuation(
                "The change is applied and all 26 tests pass."
            ) == (False, False)
        assert mock.await_count == 0


class TestJudgeGateFailsClosed:
    async def test_judge_exception_does_not_abort_the_turn(self):
        # judge_dangling_intent guards its own transport errors, but an
        # ImportError on the inline import -- or anything else escaping it --
        # would propagate out of ask() and kill the turn.  Losing one
        # auto-continue is strictly better.
        with _judge(side_effect=RuntimeError("transport exploded")):
            assert await _adjudicate_continuation(GENUINE_DANGLING) == (False, False)

    async def test_cancellation_still_propagates(self):
        # CancelledError is a BaseException in 3.8+, so the fail-closed
        # `except Exception` must NOT swallow genuine cancellation.
        with _judge(side_effect=__import__("asyncio").CancelledError()):
            with pytest.raises(__import__("asyncio").CancelledError):
                await _adjudicate_continuation(GENUINE_DANGLING)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
