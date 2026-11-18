"""
The refusal-recovery ladder runs only for refusals it can help, and strips
echoed thinking only when it retries.

Two fable-5-2 turns on 2026-11-10 ended the same way: ``stop_reason=refusal``
with ``stop_details.category=reasoning_extraction``, rung 1 dropped the file
context, and the retry got a 400 ("Invalid signature in thinking block ...
The system prompt differs from the one this block was created with") because
the thinking blocks echoed through the tool chain were signed against the
old system prompt.

Gate (app/utils/refusal_details.py):
  * category absent -> ladder runs (the case the ladder was built for, whose
    category was null at the time)
  * reasoning_extraction -> ladder runs
  * any other category (cyber, ...) -> no retry; the error shows the
    category and the provider's explanation

The seam tests drive the real ``stream_with_tools`` loop with a scripted
provider (harness from tests/test_stream_loop_recovery_seam.py) and assert
on what the model was SENT on each call and what the user SAW.  A run with
no refusal is checked too: the echoed thinking block is still sent.
"""
import copy
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.base import (
    ProviderConfig, StreamEnd, TextDelta, ThinkingBlock,
    ToolUseEnd, ToolUseInput, ToolUseStart,
)
from app.utils.refusal_details import refusal_is_recoverable, stop_details_fields

EXPL = ("This request was blocked as it seems to violate Anthropic's Terms of "
        "Service restrictions on reverse engineering or duplicating model outputs.")


# -- the gate -----------------------------------------------------------------

@pytest.mark.parametrize("category, recoverable", [
    (None, True), ("", True), ("reasoning_extraction", True),
    ("cyber", False), ("some_future_category", False),
])
def test_gate_rows(category, recoverable):
    assert refusal_is_recoverable(category) is recoverable


def test_stop_details_fields_accepts_dict_object_and_junk():
    assert stop_details_fields(None) == (None, None)
    assert stop_details_fields({"type": "refusal", "category": "cyber",
                                "explanation": " no "}) == ("cyber", "no")
    assert stop_details_fields(SimpleNamespace(
        category="reasoning_extraction", explanation=None)) == ("reasoning_extraction", None)
    assert stop_details_fields({"category": None, "explanation": ""}) == (None, None)
    assert stop_details_fields(MagicMock()) == (None, None)


# -- providers: stop_details reaches StreamEnd ------------------------------

@pytest.fixture
def bedrock_provider():
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as g:
        g.return_value = MagicMock()
        from app.providers.bedrock import BedrockProvider
        return BedrockProvider(model_id="global.anthropic.claude-fable-5-2",
                               model_config={"family": "claude"}, region="us-west-2")


def _bedrock_chunk(data):
    return {"chunk": {"bytes": json.dumps(data).encode("utf-8")}}


@pytest.mark.asyncio
@pytest.mark.parametrize("details, category, explanation", [
    ({"type": "refusal", "category": "reasoning_extraction", "explanation": EXPL},
     "reasoning_extraction", EXPL),
    (None, None, None),
])
async def test_bedrock_streamend_carries_stop_details(bedrock_provider, details,
                                                      category, explanation):
    chunks = [
        _bedrock_chunk({"type": "message_delta", "delta": {
            "stop_reason": "refusal", "stop_sequence": None, "stop_details": details},
            "usage": {"output_tokens": 0}}),
        _bedrock_chunk({"type": "message_stop"}),
    ]
    events = [e async for e in bedrock_provider._parse_stream(
        {"body": iter(chunks)}, ProviderConfig(max_output_tokens=100))]
    ends = [e for e in events if isinstance(e, StreamEnd)]
    assert len(ends) == 1 and ends[0].stop_reason == "refusal"
    assert (ends[0].stop_category, ends[0].stop_explanation) == (category, explanation)


@pytest.fixture
def anthropic_provider():
    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = MagicMock()
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        yield AnthropicDirectProvider(model_id="claude-fable-5-2",
                                      model_config={"family": "claude"}, api_key="sk-x")


class _FakeStream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for ev in self._events:
                yield ev
        return gen()


@pytest.mark.asyncio
async def test_anthropic_streamend_carries_stop_details(anthropic_provider):
    delta = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(
        type="refusal", category="cyber", explanation="Blocked."))
    sdk = [SimpleNamespace(type="message_delta", usage=None, delta=delta),
           SimpleNamespace(type="message_stop")]
    anthropic_provider.client.messages.stream = lambda **kw: _FakeStream(sdk)
    events = [e async for e in anthropic_provider._do_stream({})]
    ends = [e for e in events if isinstance(e, StreamEnd)]
    assert len(ends) == 1
    assert (ends[0].stop_reason, ends[0].stop_category, ends[0].stop_explanation) == (
        "refusal", "cyber", "Blocked.")


# -- seam: the real loop ------------------------------------------------------

TOOL = "fake_tool"
MARKER = "Below is the current codebase of the user:"
SYSTEM = f"BASE PROMPT\n\n{MARKER}\n\nFile: big.py\n" + "x = 1\n" * 1000


class _Provider:
    provider_name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls, self.systems = [], []

    async def stream_response(self, conversation, system_content, tools, config):
        self.calls.append(copy.deepcopy(conversation))
        self.systems.append(system_content)
        i = len(self.calls) - 1
        for ev in (self.script[i] if i < len(self.script) else []):
            yield ev

    def build_assistant_message(self, text, tool_uses, thinking_blocks=None, **_kw):
        content = [{k: v for k, v in b.items() if k != "_index"}
                   for b in (thinking_blocks or [])]
        if text:
            content.append({"type": "text", "text": text})
        for tu in tool_uses:
            content.append({"type": "tool_use", "id": tu["id"],
                            "name": tu["name"], "input": tu["input"]})
        return {"role": "assistant", "content": content}

    def build_tool_result_message(self, tool_results):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["tool_use_id"],
             "content": r["content"]} for r in tool_results]}

    def supports_feature(self, name):
        return name == "thinking_passback"

    def prepare_cache_control(self, messages, *a, **k):
        return messages


class _FakeTool:
    def __init__(self, name):
        self.name = name
        self.metadata = None


def _make_executor(provider):
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet3.7"}):
        with patch("app.streaming_tool_executor.StreamingToolExecutor.__init__",
                   return_value=None):
            from app.streaming_tool_executor import StreamingToolExecutor
            ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    ex.model_id = "test-model"
    ex.model_config = {"family": "claude", "max_output_tokens": 8192,
                       "supports_assistant_prefill": True, "supports_thinking": True,
                       "supports_adaptive_thinking": True}
    ex.bedrock = None
    ex.provider = provider
    ex._block_opening_buffer = ""
    ex._normalize_fence_spacing = lambda text, tracker, **kw: text
    ex._update_code_block_tracker = lambda text, tracker: None
    optimizer = MagicMock()
    optimizer.add_content.side_effect = lambda t: [t] if t else []
    optimizer.flush_remaining.return_value = ""
    ex._content_optimizer = optimizer
    ex._build_conversation_from_messages = MagicMock(return_value=(
        [{"role": "user", "content": [{"type": "text", "text": "Go"}]}], SYSTEM))
    ex._format_tools_for_api = MagicMock(return_value=[])
    ex._build_provider_config = MagicMock(return_value=MagicMock(max_output_tokens=8192))
    ex._handle_usage_event = MagicMock()
    ex._build_tool_reminder_message = MagicMock(
        return_value={"role": "user", "content": "reminder"})
    ex._load_and_prepare_tools = AsyncMock(
        return_value=([_FakeTool(TOOL)], [], set(), set(), set()))
    return ex


async def _fake_execute_single_tool(ctx):
    yield {"type": "tool_start", "tool_name": ctx.actual_tool_name, "tool_id": ctx.tool_id}
    yield {"type": "_tool_result", "tool_id": ctx.tool_id,
           "tool_name": ctx.tool_name, "result": "ok"}


async def _run(script):
    provider = _Provider(script)
    ex = _make_executor(provider)
    mgr = MagicMock()
    mgr.is_initialized = True
    events = []
    with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
         patch("app.tool_execution.execute_single_tool", _fake_execute_single_tool), \
         patch("app.streaming_tool_executor.StreamingToolExecutor._FEEDBACK_GRACE_SECONDS",
               0, create=True):
        async for ev in ex.stream_with_tools(
                messages=[{"role": "user", "content": "Go"}], tools=[]):
            events.append(ev)
            if len(events) > 400:
                break
    return provider, events


def _think_then_tool():
    return [
        ThinkingBlock(content="the plan", signature="SIG", index=0),
        ToolUseStart(id="t1", name=TOOL, index=1),
        ToolUseInput(partial_json='{"value": "x"}', index=1),
        ToolUseEnd(id="t1", name=TOOL, input={"value": "x"}, index=1),
        StreamEnd(stop_reason="tool_use"),
    ]


def _refusal(category, explanation=None):
    return [StreamEnd(stop_reason="refusal", stop_category=category,
                      stop_explanation=explanation)]


def _final():
    return [TextDelta(content="All done."), StreamEnd(stop_reason="end_turn")]


def _has_thinking(conv):
    return any(isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking")
               for m in conv if m.get("role") == "assistant"
               for b in (m.get("content") if isinstance(m.get("content"), list) else []))


def _text(events):
    return "".join(e.get("content", "") for e in events if e.get("type") == "text")


def _of(events, kind):
    return [e for e in events if e.get("type") == kind]


@pytest.mark.asyncio
async def test_no_refusal_keeps_echoed_thinking():
    provider, events = await _run([_think_then_tool(), _final()])
    assert len(provider.calls) == 2
    assert _has_thinking(provider.calls[1]), "passback must reach the next call"
    assert not _of(events, "refusal_recovery")
    assert "All done." in _text(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["reasoning_extraction", None])
async def test_retryable_refusal_retries_without_file_context_or_thinking(category):
    provider, events = await _run([
        _think_then_tool(), _refusal(category, EXPL if category else None), _final()])
    assert len(provider.calls) == 3, "rung 1 did not retry"
    # Control: the refused call did carry the echoed block and the files.
    assert _has_thinking(provider.calls[1])
    assert "File: big.py" in provider.systems[1]
    # The retry: smaller system prompt AND no block signed against the old one.
    assert "File: big.py" not in provider.systems[2]
    assert MARKER in provider.systems[2]
    assert not _has_thinking(provider.calls[2])
    # The tool_use the block preceded is still there.
    assert any(b.get("type") == "tool_use" and b.get("id") == "t1"
               for m in provider.calls[2] if m.get("role") == "assistant"
               for b in m["content"])
    assert [e.get("rung") for e in _of(events, "refusal_recovery")] == [1]
    assert "All done." in _text(events)


@pytest.mark.asyncio
async def test_other_category_is_not_retried_and_says_why():
    provider, events = await _run([
        _think_then_tool(), _refusal("cyber", "Blocked for cyber reasons."), _final()])
    assert len(provider.calls) == 2, "a cyber refusal must not be resubmitted"
    assert not _of(events, "refusal_recovery")
    errors = " ".join(str(e.get("content")) for e in _of(events, "error"))
    assert "cyber" in errors and "Blocked for cyber reasons." in errors
    assert "All done." not in _text(events)
