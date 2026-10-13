"""
Cutoff census: where did the output budget go on a max_tokens stop?

Live case: two consecutive Bedrock iterations each reported
output_tokens=4096, stop_reason=max_tokens, and zero text.  Nothing in the
logs said what those tokens were.  NO_CONTENT_BLOCKS only fires when no
block opened at all, and THINKING_CENSUS only when thinking was billed or
streamed (thinking_tokens was 0).  The likely explanation, a tool_use block
cut mid-arguments, could not be confirmed.

The census pins three things:
  1. On a max_tokens/length stop it reports text / tool-JSON / thinking
     character counts and names any tool_use block still OPEN at
     message_stop (cut before content_block_stop).
  2. It is silent on every other stop reason.
  3. It is observational: the yielded event sequence is unchanged.  In
     particular a truncated tool block yields ToolUseStart/ToolUseInput but
     NO ToolUseEnd -- the shape the executor's oversized-output seam tests
     (tests/test_stream_loop_recovery_seam.py) script, so this also pins
     that their fixture matches what Bedrock actually produces.
"""

import json

import pytest

from tests.test_bedrock_thinking_census import capture_bedrock_logs, _provider


def _chunk(payload: dict) -> dict:
    return {"chunk": {"bytes": json.dumps(payload).encode("utf-8")}}


ARGS = '{"root": {"block_type": "Group", "body": [' + '"x", ' * 40


def _truncated_tool_turn(stop="max_tokens", text=None):
    events = []
    idx = 0
    if text:
        events += [
            _chunk({"type": "content_block_start", "index": 0,
                    "content_block": {"type": "text"}}),
            _chunk({"type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": text}}),
            _chunk({"type": "content_block_stop", "index": 0}),
        ]
        idx = 1
    events += [
        _chunk({"type": "content_block_start", "index": idx,
                "content_block": {"type": "tool_use", "id": "tu1",
                                  "name": "task_card_stage", "input": {}}}),
        _chunk({"type": "content_block_delta", "index": idx,
                "delta": {"type": "input_json_delta", "partial_json": ARGS}}),
        # No content_block_stop: the stream was cut mid-arguments.
        _chunk({"type": "message_delta", "delta": {"stop_reason": stop},
                "usage": {"output_tokens": 4096}}),
        _chunk({"type": "message_stop"}),
    ]
    return events


async def _drain(events):
    from app.providers.base import ProviderConfig
    out = []
    with capture_bedrock_logs() as records:
        async for ev in _provider()._parse_stream({"body": events}, ProviderConfig()):
            out.append(ev)
        census = [r.getMessage() for r in records
                  if "CUTOFF_CENSUS" in r.getMessage()]
    return out, census


class TestCutoffCensus:

    @pytest.mark.asyncio
    async def test_names_the_open_tool_block(self):
        _, census = await _drain(_truncated_tool_turn())
        assert len(census) == 1
        line = census[0]
        assert "stop_reason='max_tokens'" in line
        assert "out_tok=4096" in line
        assert f"task_card_stage({len(ARGS)} chars)" in line
        assert f"tool_json_chars={len(ARGS)}" in line
        assert "text_chars=0" in line

    @pytest.mark.asyncio
    async def test_counts_text_delivered_before_the_cut(self):
        _, census = await _drain(_truncated_tool_turn(text="Staging the card now."))
        assert "text_chars=21" in census[0]

    @pytest.mark.asyncio
    async def test_length_stop_also_reports(self):
        _, census = await _drain(_truncated_tool_turn(stop="length"))
        assert len(census) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stop", ["end_turn", "tool_use", "stop_sequence"])
    async def test_silent_on_other_stops(self, stop):
        _, census = await _drain(_truncated_tool_turn(stop=stop))
        assert census == []

    @pytest.mark.asyncio
    async def test_completed_tool_is_not_reported_open(self):
        events = _truncated_tool_turn()
        # Close the block before the stop: it is no longer open.
        events.insert(2, _chunk({"type": "content_block_stop", "index": 0}))
        events[1] = _chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "input_json_delta",
                                      "partial_json": '{"a": 1}'}})
        _, census = await _drain(events)
        assert "open_tool_blocks=[]" in census[0]


class TestObservational:

    @pytest.mark.asyncio
    async def test_truncated_tool_yields_no_tool_use_end(self):
        from app.providers.base import ToolUseStart, ToolUseInput, ToolUseEnd, StreamEnd
        out, _ = await _drain(_truncated_tool_turn())
        kinds = [type(e) for e in out]
        assert ToolUseStart in kinds and ToolUseInput in kinds
        assert ToolUseEnd not in kinds
        assert isinstance(out[-1], StreamEnd)
        assert out[-1].stop_reason == "max_tokens"
