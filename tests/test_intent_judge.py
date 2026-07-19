"""
Tests for app.services.intent_judge — the cheap-model resolver for the
dangling-intent prefilter in streaming_tool_executor branch (a).

Pins:
  1. extract_tail — the judge sees only the final paragraphs, which is
     what keeps mid-response quoted/drafted content (the observed
     "before writing anything" blockquote false positive) out of the
     judge's view in the common case.
  2. _parse_yes_no — strict parsing, ambiguity → False.
  3. judge_dangling_intent — fail-closed polarity: transport error,
     garbage reply, or empty input all resolve to False (end the turn).
     A wrong "end" costs one lost auto-continue; a wrong "continue"
     costs a full-context primary-model round trip.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.intent_judge import (
    _TAIL_MAX_CHARS,
    _parse_yes_no,
    extract_tail,
    judge_dangling_intent,
)


class TestExtractTail:
    def test_single_paragraph_is_whole_text(self):
        assert extract_tail("Let me check the file.") == "Let me check the file."

    def test_returns_last_two_paragraphs(self):
        text = "First.\n\nSecond.\n\nThird.\n\nFourth."
        assert extract_tail(text) == "Third.\n\nFourth."

    def test_mid_text_intent_phrase_excluded_from_tail(self):
        # The observed false positive: an intent phrase inside a
        # blockquoted question drafted for a third party, followed by
        # more prose. The tail the judge sees must NOT contain it.
        text = (
            "Here is the question to send:\n\n"
            "> we need to know by how much before writing anything\n\n"
            "Why this framing: it is answerable in one message.\n\n"
            "The question above is ready for you to relay."
        )
        tail = extract_tail(text)
        assert "before writing" not in tail
        assert "ready for you to relay" in tail

    def test_char_cap_bounds_spend(self):
        text = "x" * (_TAIL_MAX_CHARS * 3)
        assert len(extract_tail(text)) == _TAIL_MAX_CHARS

    def test_empty_and_whitespace(self):
        assert extract_tail("") == ""
        assert extract_tail("   \n\n  ") == ""

    def test_blank_paragraphs_skipped(self):
        # Paragraphs that are only whitespace don't count toward the two.
        text = "Real one.\n\n   \n\nReal two."
        assert extract_tail(text) == "Real one.\n\nReal two."


class TestParseYesNo:
    @pytest.mark.parametrize("reply", ["yes", "Yes", "YES", "y", "true", "  yes."])
    def test_yes_variants(self, reply):
        assert _parse_yes_no(reply) is True

    @pytest.mark.parametrize("reply", ["no", "No", "NO", "n", "false", " no way"])
    def test_no_variants(self, reply):
        assert _parse_yes_no(reply) is False

    @pytest.mark.parametrize("reply", [None, "", "maybe", "the assistant intends to…", "1"])
    def test_ambiguity_defaults_to_no(self, reply):
        assert _parse_yes_no(reply) is False

    def test_yes_must_be_leading_token(self):
        # "no, but yes later" must not read as yes; anchor is ^.
        assert _parse_yes_no("I think yes") is False


_INTENT_TEXT = "The config looks wrong. Let me check the resolver settings."


def _run(coro):
    return asyncio.run(coro)


class TestJudgeDanglingIntent:
    def _judge_with_reply(self, reply, text=_INTENT_TEXT):
        with patch(
            "app.services.model_resolver.call_service_model",
            new=AsyncMock(return_value=reply),
        ) as mock_call:
            result = _run(judge_dangling_intent(text))
        return result, mock_call

    def test_judge_yes_continues(self):
        result, mock_call = self._judge_with_reply("yes")
        assert result is True
        mock_call.assert_awaited_once()

    def test_judge_no_ends(self):
        result, _ = self._judge_with_reply("no")
        assert result is False

    def test_garbage_reply_fails_closed(self):
        result, _ = self._judge_with_reply("cannot determine")
        assert result is False

    def test_transport_error_fails_closed(self):
        with patch(
            "app.services.model_resolver.call_service_model",
            new=AsyncMock(side_effect=RuntimeError("throttled")),
        ):
            assert _run(judge_dangling_intent(_INTENT_TEXT)) is False

    def test_empty_text_skips_the_call_entirely(self):
        with patch(
            "app.services.model_resolver.call_service_model",
            new=AsyncMock(return_value="yes"),
        ) as mock_call:
            assert _run(judge_dangling_intent("   ")) is False
        mock_call.assert_not_awaited()

    def test_judge_receives_only_the_tail(self):
        # The user message handed to the judge must contain the tail,
        # not the mid-text drafted content.
        text = (
            "Draft:\n\n> check this before writing anything\n\n"
            "That covers it.\n\nNothing further is pending."
        )
        with patch(
            "app.services.model_resolver.call_service_model",
            new=AsyncMock(return_value="no"),
        ) as mock_call:
            _run(judge_dangling_intent(text))
        sent = mock_call.await_args.kwargs["user_message"]
        assert "before writing" not in sent
        assert "Nothing further is pending." in sent

    def test_category_and_bounds(self):
        _, mock_call = self._judge_with_reply("yes")
        kwargs = mock_call.await_args.kwargs
        assert kwargs["category"] == "intent_judge"
        assert kwargs["max_tokens"] == 4
        assert kwargs["temperature"] == 0.0
