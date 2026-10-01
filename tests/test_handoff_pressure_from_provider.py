"""
The handoff pressure cache (app/utils/handoff_prelude.py) must reflect what
the provider ACTUALLY billed, not only the chars/4 pre-send estimate that
server.py records.  The streaming executor already computes the real figure
for its "Context Size Notice"; this pins that it also feeds the cache, so
the frontend token bar and the drawer see the same number the model sees.

Observed failure: chars/4 estimate 52%, provider 79%, no Hand off button.
"""
from types import SimpleNamespace
from unittest.mock import patch

from app.streaming_tool_executor import IterationUsage, StreamingToolExecutor
from app.utils import handoff_prelude


def _bare_executor(token_limit=1_000_000):
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)
    inst.model_config = {'token_limit': token_limit}
    return inst


def _usage_event(input_tokens, cache_read=0, cache_write=0):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=10,
                           cache_read_tokens=cache_read, cache_write_tokens=cache_write)


@patch('app.streaming_tool_executor.StreamingToolExecutor._record_calibration', return_value='')
@patch('app.streaming_tool_executor.StreamingToolExecutor._track_estimation_accuracy')
@patch('app.agents.models.ModelManager.get_model_settings', return_value={})
def test_provider_usage_overrides_pre_send_estimate(_settings, _acc, _cal):
    cid = 'conv-pressure-1'
    handoff_prelude._pressure_cache.pop(cid, None)
    # server.py's pre-send estimate said 52%.
    handoff_prelude.record_context_pressure(cid, 517_051, 1_000_000)
    assert handoff_prelude.get_context_pressure(cid) < 0.6

    exe = _bare_executor()
    exe._handle_usage_event(_usage_event(700_000, cache_read=97_952), IterationUsage(),
                             iteration=1, conversation_id=cid, conversation=[],
                             system_content='', throttle_state={})

    # Provider said 797,952 of 1,000,000.
    assert abs(handoff_prelude.get_context_pressure(cid) - 0.797952) < 1e-6


@patch('app.streaming_tool_executor.StreamingToolExecutor._record_calibration', return_value='')
@patch('app.streaming_tool_executor.StreamingToolExecutor._track_estimation_accuracy')
@patch('app.agents.models.ModelManager.get_model_settings', return_value={'max_input_tokens': 200_000})
def test_uses_the_users_max_input_setting_as_ceiling(_settings, _acc, _cal):
    cid = 'conv-pressure-2'
    handoff_prelude._pressure_cache.pop(cid, None)
    exe = _bare_executor(token_limit=1_000_000)
    exe._handle_usage_event(_usage_event(150_000), IterationUsage(),
                             iteration=0, conversation_id=cid, conversation=[],
                             system_content='', throttle_state={})
    # 150k against the user's 200k ceiling, not the model's 1M.
    assert abs(handoff_prelude.get_context_pressure(cid) - 0.75) < 1e-6


@patch('app.streaming_tool_executor.StreamingToolExecutor._record_calibration', return_value='')
@patch('app.streaming_tool_executor.StreamingToolExecutor._track_estimation_accuracy')
@patch('app.agents.models.ModelManager.get_model_settings', return_value={})
def test_no_conversation_id_records_nothing(_settings, _acc, _cal):
    before = dict(handoff_prelude._pressure_cache)
    exe = _bare_executor()
    exe._handle_usage_event(_usage_event(900_000), IterationUsage(),
                             iteration=0, conversation_id=None, conversation=[],
                             system_content='', throttle_state={})
    assert handoff_prelude._pressure_cache == before
