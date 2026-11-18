"""The turn-end decider routes "want me to look into X?" to the research-offer
judge instead of ending the turn.

Before: a non-prefill model that ended a clean, tool-free round on a question
was classified ``phrase_yields`` and the turn ended, whatever the question
was.  An investigation that stopped to ask permission to continue therefore
had no backstop behind the prompt.

Seams: prefilter -> decider verdict; decider verdict -> caller (judge call,
shared stall counter, the note that is injected).  The judge and prefilter
themselves are in tests/test_research_offer.py.
"""
import inspect

import pytest

from app.streaming_tool_executor import StreamingToolExecutor
from app.utils.research_offer import RESEARCH_OFFER_STALL_CAP

OFFER_Q = ("The lease expiry explains two of the three failures.\n\n"
           "Want me to dig into where the third one comes from?")
OFFER_STATEMENT = ("The lease expiry explains two of the three failures.\n\n"
                   "I can also trace the third through the relay if you'd like.")
REAL_QUESTION = "Both layouts work.\n\nWhich of the two do you prefer?"
INTENT = "Let me check the result to confirm it works."


@pytest.fixture
def decide():
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)

    def _call(**overrides):
        kwargs = dict(
            assistant_text="", supports_prefill=False, tools_executed=False,
            last_stop_reason='end_turn', iteration=4, max_iterations=200,
            continuation_happened=False, code_block_open=False, blocked_tools=0,
            prev_is_tool_result=False, textonly_grace_used=0,
            empty_completion_retry_used=0,
        )
        kwargs.update(overrides)
        return inst._decide_no_tool_outcome(**kwargs)
    return _call


@pytest.mark.parametrize("text", [OFFER_Q, OFFER_STATEMENT])
def test_offer_ending_goes_to_the_judge(decide, text):
    assert decide(assistant_text=text) == ('judge_offer', 'no_prefill_offer_prefilter', None)


def test_offer_on_the_first_round_also_goes_to_the_judge(decide):
    # No tool has run yet: "I could look at A, B, C; want me to?"
    assert decide(assistant_text=OFFER_Q, iteration=0)[0] == 'judge_offer'


def test_a_real_question_still_ends_the_turn(decide):
    assert decide(assistant_text=REAL_QUESTION)[:2] == ('end', 'no_prefill_end')


def test_intent_announcement_is_still_the_intent_judges(decide):
    # control for ordering: the pre-existing path is not rerouted
    assert decide(assistant_text=INTENT) == ('judge_intent', 'no_prefill_intent_prefilter', None)


def test_offer_is_bounded_by_its_own_stall_cap(decide):
    under = decide(assistant_text=OFFER_Q, intent_stalls=RESEARCH_OFFER_STALL_CAP - 1)
    at = decide(assistant_text=OFFER_Q, intent_stalls=RESEARCH_OFFER_STALL_CAP)
    assert under[0] == 'judge_offer'
    assert at[:2] == ('end', 'no_prefill_end')


def test_cutoff_and_loop_guards_come_first(decide):
    assert decide(assistant_text=OFFER_Q, last_stop_reason='max_tokens')[0] == 'inject_max_tokens'
    assert decide(assistant_text=OFFER_Q, repetition_stopped=True)[:2] == ('end', 'repetition_stopped')
    assert decide(assistant_text=OFFER_Q, blocked_tools=3)[:2] == ('end', 'runaway_loop')


def test_prefill_models_are_not_rerouted(decide):
    assert decide(assistant_text=OFFER_Q, supports_prefill=True)[0] != 'judge_offer'


def test_caller_resolves_the_verdict_and_injects_the_offer_note():
    """The decider's verdict is provisional; the streaming loop must call the
    judge, continue on yes with the research-offer note, and end on no."""
    src = inspect.getsource(StreamingToolExecutor)
    assert "_verdict == 'judge_offer'" in src
    branch = src.split("_verdict == 'judge_offer'", 1)[1].split("_assistant_turn_pending = (", 1)[0]
    assert "await judge_research_offer(assistant_text)" in branch
    assert "'inject_intent_continue', 'no_prefill_offer_continue'" in branch
    assert "'end', 'no_prefill_end'" in branch
    inject = src.split("if _verdict == 'inject_intent_continue':", 1)[1].split(
        "if _verdict == 'block_continue':", 1)[0]
    assert "RESEARCH_OFFER_CONTINUE_NOTE" in inject
    assert "_reason == 'no_prefill_offer_continue'" in inject
    # the intent path keeps its own note
    assert "You described an action you intend to take" in inject
