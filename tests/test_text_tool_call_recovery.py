"""
A tool call written into the text channel is recovered as a tool call
(Ollama-served models only).

Live failure (Ollama, qwen2.5-coder:7b, only mcp_sequentialthinking
installed): the whole response was

    {"name": "mcp_sequentialthinking", "arguments": {"thought": "Yes, I can
    hear you. How can I assist you today?", "nextThoughtNeeded": "false",
    "thoughtNumber": 1, "totalThoughts": 1}}

delivered as delta.content, finish_reason 'stop'. OpenAIDirectProvider
turned it into TextDelta events, the frontend printed the JSON literally and
no tool ran. DwarfStar / vLLM / OpenAI parse their own templates and never
leak a call this way, so the recovery is gated on the model entry's
``recover_text_tool_calls`` flag, which local_models sets for Ollama.

Three layers: the pure ``TextToolCallSniffer``, the ``_do_stream`` seam
with SDK-shaped chunks, and the discovery seam that sets the flag. Every
recovery test fails against the pre-fix provider (module missing /
TextDelta carrying the JSON / stop_reason 'stop').
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import List

import pytest

from app.providers.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
    UsageEvent,
)
from app.providers.openai_direct import OpenAIDirectProvider
from app.providers.text_tool_calls import TextToolCallSniffer
from app.utils.local_models import DiscoveredModel, apply_discovery

TOOLS = {"mcp_sequentialthinking", "mcp_get_current_time", "file_read"}

LIVE_JSON = (
    '{"name": "mcp_sequentialthinking", "arguments": {"thought": '
    '"Yes, I can hear you. How can I assist you today?", '
    '"nextThoughtNeeded": "false", "thoughtNumber": 1, "totalThoughts": 1}}'
)
LIVE_ARGS = {
    "thought": "Yes, I can hear you. How can I assist you today?",
    "nextThoughtNeeded": "false",
    "thoughtNumber": 1,
    "totalThoughts": 1,
}


def _chunks(text: str, size: int = 7) -> List[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def _run(sniffer: TextToolCallSniffer, text: str, stop: str = "stop", size: int = 7):
    events: List[StreamEvent] = []
    for piece in _chunks(text, size):
        events.extend(sniffer.feed(piece))
    tail, stop_out = sniffer.finish(stop)
    return events, tail, stop_out


def _text_of(events) -> str:
    return "".join(e.content for e in events if isinstance(e, TextDelta))


# ---------------------------------------------------------------------------
# Pure sniffer
# ---------------------------------------------------------------------------

class TestSnifferRecovers:
    @pytest.mark.parametrize("size", [1, 7, 64, 10_000])
    def test_live_response_becomes_one_tool_call(self, size):
        s = TextToolCallSniffer(TOOLS)
        streamed, tail, stop = _run(s, LIVE_JSON, size=size)
        assert streamed == [], "the JSON must never reach the text channel"
        assert stop == "tool_calls"
        assert [type(e) for e in tail] == [ToolUseStart, ToolUseInput, ToolUseEnd]
        start, inp, end = tail
        assert start.name == end.name == "mcp_sequentialthinking"
        assert start.id and start.id == end.id, "orchestrator keys active tools by a non-empty id"
        assert end.input == LIVE_ARGS
        # The orchestrator validates partial_json, not ToolUseEnd.input.
        assert json.loads(inp.partial_json) == LIVE_ARGS
        assert start.index == inp.index == end.index

    @pytest.mark.parametrize("wrapped", [
        "<tool_call>\n{J}\n</tool_call>",
        "```json\n{J}\n```",
        "```\n{J}\n```",
        "\n\n  {J}  \n",
    ])
    def test_wrappers_are_stripped(self, wrapped):
        s = TextToolCallSniffer(TOOLS)
        streamed, tail, stop = _run(s, wrapped.replace("{J}", LIVE_JSON))
        assert streamed == []
        assert stop == "tool_calls"
        assert [e.name for e in tail if isinstance(e, ToolUseEnd)] == ["mcp_sequentialthinking"]

    def test_two_calls_back_to_back_get_distinct_indices_and_ids(self):
        second = '{"name": "mcp_get_current_time", "arguments": {"format": "iso"}}'
        s = TextToolCallSniffer(TOOLS)
        _, tail, stop = _run(s, f"<tool_call>{LIVE_JSON}</tool_call>\n<tool_call>{second}</tool_call>")
        ends = [e for e in tail if isinstance(e, ToolUseEnd)]
        assert [e.name for e in ends] == ["mcp_sequentialthinking", "mcp_get_current_time"]
        assert ends[1].input == {"format": "iso"}
        assert ends[0].index != ends[1].index
        assert ends[0].id != ends[1].id
        assert stop == "tool_calls"

    def test_first_index_offsets_past_real_tool_calls(self):
        s = TextToolCallSniffer(TOOLS)
        for p in _chunks(LIVE_JSON):
            s.feed(p)
        tail, _ = s.finish("stop", first_index=3)
        assert {e.index for e in tail} == {3}

    @pytest.mark.parametrize("key", ["parameters", "input"])
    def test_alternate_argument_keys(self, key):
        s = TextToolCallSniffer(TOOLS)
        _, tail, stop = _run(s, f'{{"name": "file_read", "{key}": {{"path": "x.py"}}}}')
        assert stop == "tool_calls"
        assert [e.input for e in tail if isinstance(e, ToolUseEnd)] == [{"path": "x.py"}]

    def test_double_encoded_arguments_are_decoded(self):
        s = TextToolCallSniffer(TOOLS)
        _, tail, _ = _run(s, '{"name": "file_read", "arguments": "{\\"path\\": \\"a.py\\"}"}')
        assert [e.input for e in tail if isinstance(e, ToolUseEnd)] == [{"path": "a.py"}]

    def test_call_with_no_arguments_object(self):
        s = TextToolCallSniffer(TOOLS)
        _, tail, stop = _run(s, '{"name": "mcp_get_current_time"}')
        assert stop == "tool_calls"
        assert [e.input for e in tail if isinstance(e, ToolUseEnd)] == [{}]


class TestSnifferReleasesText:
    """Positive controls: ordinary responses pass through byte-for-byte."""

    def test_prose_is_released_on_the_first_delta(self):
        s = TextToolCallSniffer(TOOLS)
        assert s.feed("Yes, I ") == [TextDelta(content="Yes, I ")]
        assert s.state == "passthrough"
        assert s.feed("can hear you.") == [TextDelta(content="can hear you.")]

    def test_json_naming_an_unoffered_tool_is_text(self):
        text = '{"name": "Alice", "arguments": {"age": 3}}'
        s = TextToolCallSniffer(TOOLS)
        streamed, tail, stop = _run(s, text)
        assert _text_of(streamed + tail) == text
        assert stop == "stop"
        assert not any(isinstance(e, ToolUseEnd) for e in tail)

    def test_json_with_other_leading_key_is_text_and_released_early(self):
        text = '{"users": [{"name": "mcp_sequentialthinking"}]}'
        s = TextToolCallSniffer(TOOLS)
        streamed, tail, stop = _run(s, text, size=3)
        assert _text_of(streamed + tail) == text
        assert tail == [] and stop == "stop", "decided during streaming, not held to the end"

    def test_truncated_call_is_released_as_text(self):
        """finish_reason 'length' mid-object: nothing to execute, show it."""
        cut = LIVE_JSON[:60]
        s = TextToolCallSniffer(TOOLS)
        streamed, tail, stop = _run(s, cut, stop="length")
        assert _text_of(streamed + tail) == cut
        assert stop == "length"

    def test_call_followed_by_prose_is_text(self):
        text = LIVE_JSON + "\nI have thought about it."
        s = TextToolCallSniffer(TOOLS)
        streamed, tail, stop = _run(s, text)
        assert _text_of(streamed + tail) == text
        assert stop == "stop"

    def test_no_tools_offered_means_passthrough(self):
        s = TextToolCallSniffer([])
        assert s.feed(LIVE_JSON) == [TextDelta(content=LIVE_JSON)]
        assert s.finish("stop") == ([], "stop")

    def test_header_hold_is_bounded(self):
        # A "name" that never closes its quote: stop holding after the cap.
        s = TextToolCallSniffer(TOOLS)
        out: List[StreamEvent] = []
        pending = '{"name": "' + "x" * 400
        for p in _chunks(pending, 50):
            out.extend(s.feed(p))
        assert s.state == "passthrough"
        assert _text_of(out) == pending

    def test_partial_lead_waits_then_prose_releases(self):
        s = TextToolCallSniffer(TOOLS)
        assert s.feed("<tool") == []            # could still become <tool_call>
        out = s.feed("s are useful here.")      # it did not
        assert _text_of(out) == "<tools are useful here."


# ---------------------------------------------------------------------------
# Provider seam: SDK-shaped chunks through _do_stream
# ---------------------------------------------------------------------------

def _delta_chunk(content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls,
                            reasoning_content=None, reasoning=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=None,
    )


def _usage_chunk(prompt=22_471, completion=55):
    return SimpleNamespace(choices=[], usage=SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        prompt_tokens_details=None, completion_tokens_details=None,
    ))


def _provider_with_chunks(chunks, *, recover=True) -> OpenAIDirectProvider:
    p = OpenAIDirectProvider.__new__(OpenAIDirectProvider)
    p.model_id = "qwen2.5-coder:7b"
    p.model_config = {"recover_text_tool_calls": True} if recover else {}

    async def _create(**kwargs):
        async def _iter():
            for c in chunks:
                yield c
        return _iter()

    p.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    return p


REQUEST = {
    "model": "qwen2.5-coder:7b",
    "messages": [],
    "tools": [{"type": "function", "function": {"name": n, "parameters": {}}} for n in TOOLS],
}


def _live_chunks():
    chunks = [_delta_chunk(content=c) for c in _chunks(LIVE_JSON, 9)]
    chunks.append(_delta_chunk(finish_reason="stop"))
    chunks.append(_usage_chunk())
    return chunks


async def _drain(agen):
    out = []
    async for e in agen:
        out.append(e)
    return out


@pytest.mark.asyncio
async def test_do_stream_recovers_the_live_ollama_response():
    events = await _drain(_provider_with_chunks(_live_chunks())._do_stream(REQUEST))

    assert not any(isinstance(e, TextDelta) for e in events), (
        "the JSON reached the text channel: " + _text_of(events)
    )
    ends = [e for e in events if isinstance(e, ToolUseEnd)]
    assert [e.name for e in ends] == ["mcp_sequentialthinking"]
    assert ends[0].input == LIVE_ARGS
    assert any(isinstance(e, ToolUseStart) for e in events)
    assert any(isinstance(e, UsageEvent) for e in events), "usage must still be reported"
    stream_end = [e for e in events if isinstance(e, StreamEnd)]
    assert len(stream_end) == 1 and stream_end[0].stop_reason == "tool_calls"
    # Ordering: tool events before usage before StreamEnd.
    kinds = [type(e) for e in events]
    assert kinds.index(ToolUseEnd) < kinds.index(UsageEvent) < kinds.index(StreamEnd)


@pytest.mark.asyncio
async def test_do_stream_without_the_flag_is_byte_identical_to_before():
    """ds4 / vLLM / OpenAI models: no flag, no sniffing, even with tools."""
    events = await _drain(_provider_with_chunks(_live_chunks(), recover=False)._do_stream(REQUEST))
    assert _text_of(events) == LIVE_JSON
    assert not any(isinstance(e, (ToolUseStart, ToolUseEnd)) for e in events)
    assert events[-1] == StreamEnd(stop_reason="stop")


@pytest.mark.asyncio
async def test_do_stream_plain_prose_is_unchanged():
    text = "Yes, I can hear you. How can I assist you today?"
    chunks = [_delta_chunk(content=c) for c in _chunks(text, 9)]
    chunks.append(_delta_chunk(finish_reason="stop"))
    events = await _drain(_provider_with_chunks(chunks)._do_stream(REQUEST))
    assert _text_of(events) == text
    assert not any(isinstance(e, (ToolUseStart, ToolUseEnd)) for e in events)
    assert events[-1] == StreamEnd(stop_reason="stop")


@pytest.mark.asyncio
async def test_do_stream_without_tools_never_sniffs():
    chunks = [_delta_chunk(content=c) for c in _chunks(LIVE_JSON, 9)]
    chunks.append(_delta_chunk(finish_reason="stop"))
    req = {k: v for k, v in REQUEST.items() if k != "tools"}
    events = await _drain(_provider_with_chunks(chunks)._do_stream(req))
    assert _text_of(events) == LIVE_JSON
    assert not any(isinstance(e, ToolUseEnd) for e in events)


@pytest.mark.asyncio
async def test_do_stream_real_tool_calls_and_text_recovery_do_not_collide():
    """A native tool_calls delta at index 0 plus a text-written call: the
    recovered call must take a fresh index or the orchestrator's index
    lookup at content_block_stop would resolve to the wrong tool."""
    tc = SimpleNamespace(index=0, id="call_native", function=SimpleNamespace(
        name="mcp_get_current_time", arguments='{"format": "iso"}'))
    chunks = [_delta_chunk(tool_calls=[tc])]
    chunks += [_delta_chunk(content=c) for c in _chunks(LIVE_JSON, 9)]
    chunks.append(_delta_chunk(finish_reason="tool_calls"))
    events = await _drain(_provider_with_chunks(chunks)._do_stream(REQUEST))
    ends = {e.name: e for e in events if isinstance(e, ToolUseEnd)}
    assert set(ends) == {"mcp_get_current_time", "mcp_sequentialthinking"}
    assert ends["mcp_get_current_time"].index == 0
    assert ends["mcp_sequentialthinking"].index == 1
    assert ends["mcp_get_current_time"].id != ends["mcp_sequentialthinking"].id


# ---------------------------------------------------------------------------
# Discovery seam: who sets the flag
# ---------------------------------------------------------------------------

def _found(runtime: str) -> DiscoveredModel:
    return DiscoveredModel(runtime=runtime, name="m", context_length=32768,
                           supports_tools=True, supports_vision=None, supports_thinking=None)


def test_ollama_discovery_sets_the_recovery_flag():
    entry = apply_discovery({"model_id": "m"}, _found("ollama"))
    assert entry.get("recover_text_tool_calls") is True


@pytest.mark.parametrize("runtime", ["dwarfstar", "vllm", "lmstudio", "llamacpp", "openai-compatible"])
def test_other_runtimes_leave_the_flag_unset(runtime):
    entry = apply_discovery({"model_id": "m"}, _found(runtime))
    assert "recover_text_tool_calls" not in entry
