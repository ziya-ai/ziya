"""
The model is never told how full its context is.

Until 2026-10-10, StreamingToolExecutor._handle_usage_event staged a
"Context Size Notice" for the model at 85 / 90 / 95% of the input limit
("consider wrapping up large reads", "stop reading new content and
summarize now").  Together with the handoff prelude's 70% nudge it made
every long conversation end in the model narrating context pressure and
refusing ordinary reads — in exactly the last stretch of the window where
users want it working.  Both are gone; the user's surface is the token bar.

Driven through the real _handle_usage_event with provider-reported usage.
The pressure cache (which the handoff drawer reads) is still recorded, so
the positive control proves the path ran.  Old transcripts may still carry
persisted notices, so the replay stripping stays tested.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.streaming_tool_executor import IterationUsage, StreamingToolExecutor
from app.utils import handoff_prelude

LIMIT = 1_000_000


def _executor():
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)
    inst.model_config = {'token_limit': LIMIT}
    return inst


def _feed(exe, input_tokens, state, conversation_id=None):
    ev = SimpleNamespace(input_tokens=input_tokens, output_tokens=10,
                         cache_read_tokens=0, cache_write_tokens=0)
    exe._handle_usage_event(ev, IterationUsage(), iteration=1,
                            conversation_id=conversation_id, conversation=[],
                            system_content='', throttle_state=state)
    return state.pop('token_budget_warning', None)


@pytest.fixture(autouse=True)
def _quiet():
    with patch.object(StreamingToolExecutor, '_record_calibration', return_value=''), \
         patch.object(StreamingToolExecutor, '_track_estimation_accuracy'), \
         patch('app.agents.models.ModelManager.get_model_settings', return_value={}):
        yield


@pytest.mark.parametrize("tokens", [750_000, 850_000, 900_000, 960_000, 990_000])
def test_no_notice_is_staged_at_any_pressure(tokens):
    cid = f"conv-notice-{tokens}"
    handoff_prelude._pressure_cache.pop(cid, None)
    state = {}
    assert _feed(_executor(), tokens, state, conversation_id=cid) is None
    assert 'token_budget_warning' not in state
    # Positive control: the usage event WAS processed -- the drawer's
    # pressure cache reflects it.
    assert handoff_prelude.get_context_pressure(cid) == pytest.approx(tokens / LIMIT)


def test_main_loop_no_longer_injects_a_token_budget_warning():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "streaming_tool_executor.py").read_text()
    assert "Context Size Notice" not in src
    assert "throttle_state.pop('token_budget_warning'" not in src
    assert "Stop reading new content" not in src
    # The iteration-budget notices are a different thing (iteration count,
    # not context size) and keep their model-only injection.
    assert "yield track_yield({'type': 'text', 'content': warning_message})" not in src
    assert '_notice = f"[System: {model_message.strip()}]"' in src


@pytest.mark.parametrize("line", [
    "⚠️ **Iteration Budget:** You have used 75% of this cycle's iteration budget (150 of 200); 50 remain.",
    "⚠️ **Iteration Limit Warning:** You have only 2 iterations remaining in this cycle.",
    "🚨 **FINAL ITERATION:** This is your last iteration in this cycle.",
])
def test_persisted_iteration_notices_are_stripped_on_replay(line):
    from app.utils.tool_history_rewrite import rewrite_assistant_text
    out = rewrite_assistant_text(f"Before.\n\n{line}\n\nAfter.")
    assert "iteration" not in out.lower()
    assert "Before." in out and "After." in out


def test_persisted_context_notices_are_stripped_on_replay():
    """Transcripts written before the notice was removed still carry it."""
    from app.utils.tool_history_rewrite import rewrite_assistant_text
    old = ("Here is the fix.\n\n⚠️ **Context Size Notice:** This turn has submitted "
           "~750,844 of ~1,000,000 input tokens (75%). Consider wrapping up.\n\nMore text.")
    out = rewrite_assistant_text(old)
    assert "Context Size Notice" not in out and "750,844" not in out
    assert "Here is the fix." in out and "More text." in out
