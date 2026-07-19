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

from app.cli import _response_looks_incomplete, _CLI_INTENT_PHRASES


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


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
