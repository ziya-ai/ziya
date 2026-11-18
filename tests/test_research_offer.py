"""app/utils/research_offer.py: the turn-end backstop for an investigation
that stops to ask whether it should continue.

The prefilter decides whether a judge call is worth making; the judge
decides whether the turn continues.  A wrong "continue" on an offer of a
side-effecting action would act without approval, so the cases that must
stay "end" are pinned as firmly as the ones that must continue.

The decider/caller wiring in streaming_tool_executor is covered by
tests/test_decide_no_tool_outcome_research_offer.py.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.utils import research_offer as ro


OFFERS = [
    "Root cause is probably the stale lease. Want me to dig into where it is refreshed?",
    "That covers the client side.\n\nWould you like me to trace the server path as well?",
    "Two candidates remain. Should I check the other three call sites?",
    "Shall I keep digging into the timeout?",
    "I can also look at the retry ladder if you'd like.",
    "If you want, I can pull the journal for that window and compare.",
    "Happy to dig further into the allocator.",
    "Let me know if you'd like me to verify that against the logs.",
    "Say the word and I'll trace it through the relay.",
    "Do you want me to reproduce it first?",
]

NOT_OFFERS = [
    "The lease is refreshed in `_renew`; the bug is the missing await on line 212.",
    "Which of the two layouts do you prefer?",
    "Let me check — which file did you mean?",
    "Let me check the configuration, or should I take a different approach?",
    "Is the staging cluster the one in us-west-2?",
    "",
    "   \n\n ",
]


@pytest.mark.parametrize("text", OFFERS)
def test_prefilter_matches_offer_shapes(text):
    assert ro.looks_like_research_offer(text) is True


@pytest.mark.parametrize("text", NOT_OFFERS)
def test_prefilter_ignores_findings_and_real_questions(text):
    assert ro.looks_like_research_offer(text) is False


def test_prefilter_reads_only_the_last_paragraph():
    # An offer the response already moved past is not the ending.
    text = ("Earlier I asked: want me to dig into the cache?\n\n"
            "I did. The cache is not involved; the failure is in the relay.")
    assert ro.looks_like_research_offer(text) is False
    # control: the same offer as the ending does match
    assert ro.looks_like_research_offer("Relay is fine.\n\nWant me to dig into the cache?") is True


def test_prefilter_also_passes_side_effect_offers_to_the_judge():
    """The prefilter is loose on purpose; the judge is what refuses these."""
    assert ro.looks_like_research_offer("Draft is above. Want me to post it to the channel?") is True


def _judge(reply=None, raises=None):
    mock = AsyncMock(return_value=reply) if raises is None else AsyncMock(side_effect=raises)
    with patch("app.services.model_resolver.call_service_model", mock):
        verdict = asyncio.new_event_loop().run_until_complete(
            ro.judge_research_offer("Found one cause.\n\nWant me to check the other call sites?"))
    return verdict, mock


def test_judge_yes_continues_and_sees_the_ending():
    verdict, mock = _judge("yes")
    assert verdict is True
    kwargs = mock.call_args.kwargs
    assert "Want me to check the other call sites?" in kwargs["user_message"]
    assert kwargs["category"] == "intent_judge"
    # the classifier is told what must NOT continue
    system = kwargs["system_prompt"]
    for word in ("posting", "committing", "implementing a fix", "choose between"):
        assert word in system


@pytest.mark.parametrize("reply", ["no", "No.", "maybe", "", None, "the answer is yes"])
def test_judge_anything_but_a_leading_yes_ends_the_turn(reply):
    assert _judge(reply)[0] is False


def test_judge_transport_failure_ends_the_turn():
    assert _judge(raises=RuntimeError("boom"))[0] is False


def test_judge_infra_outage_ends_the_turn_but_cancellation_propagates():
    class Outage(BaseException):
        infra_kind = "credentials"
    assert _judge(raises=Outage())[0] is False
    with pytest.raises(asyncio.CancelledError):
        _judge(raises=asyncio.CancelledError())


def test_judge_is_not_called_for_empty_text():
    mock = AsyncMock(return_value="yes")
    with patch("app.services.model_resolver.call_service_model", mock):
        assert asyncio.new_event_loop().run_until_complete(ro.judge_research_offer("  ")) is False
    mock.assert_not_called()


def test_continue_note_forbids_side_effects_and_repeating_the_offer():
    note = ro.RESEARCH_OFFER_CONTINUE_NOTE
    assert note.startswith("[System:")
    assert "Do not repeat the offer" in note
    assert "Do not take any action that changes something" in note
