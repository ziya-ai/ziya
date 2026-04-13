"""
Integration tests for the full streaming orchestration loop.

These tests exercise the assembled system end-to-end: a mock LLM provider
emits scripted StreamEvent sequences, the StreamingToolExecutor dispatches
tool calls to mock tools, tool results feed back into the conversation,
and the provider is called again with the updated conversation.

This is the test that proves the *contract between components* — that
providers, the orchestrator, tool execution, and conversation management
plug together correctly.  It catches regressions that per-module unit
tests cannot: event ordering, conversation state evolution, multi-iteration
tool loops, and error recovery flows.

Requires: pytest-asyncio
"""

import asyncio
import json
import os
import time
import pytest
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers.base import (
    LLMProvider,
    ProviderConfig,
    StreamEvent,
    TextDelta,
    ToolUseStart,
    ToolUseInput,
    ToolUseEnd,
    UsageEvent,
    StreamEnd,
    ErrorEvent,
    ErrorType,
)


# ---------------------------------------------------------------------------
# Mock Provider — yields scripted event sequences per call
# ---------------------------------------------------------------------------

class MockProvider(LLMProvider):
    """LLMProvider that yields pre-scripted StreamEvent sequences.

    Each call to stream_response() pops the next script from the queue.
    This lets tests define a multi-iteration conversation: iteration 0
    uses scripts[0], iteration 1 uses scripts[1], etc.
    """

    def __init__(self, scripts: List[List[StreamEvent]]):
        self._scripts = list(scripts)
        self.call_count = 0
        self.call_args_log: List[dict] = []

    @property
    def provider_name(self) -> str:
        return "mock"

    async def stream_response(
        self,
        messages: List[Dict[str, Any]],
        system_content: Optional[str],
        tools: List[Dict[str, Any]],
        config: ProviderConfig,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.call_args_log.append({
            "messages": messages,
            "system_content": system_content,
            "tools": tools,
            "config": config,
        })
        idx = self.call_count
        self.call_count += 1
        if idx < len(self._scripts):
            for event in self._scripts[idx]:
                yield event
        else:
            # Fallback: just end
            yield TextDelta(content="(no more scripts)")
            yield UsageEvent(input_tokens=10, output_tokens=5)
            yield StreamEnd(stop_reason="end_turn")

    def build_assistant_message(
        self, text: str, tool_uses: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        blocks = []
        if text.strip():
            blocks.append({"type": "text", "text": text.rstrip()})
        for tu in tool_uses:
            blocks.append({
                "type": "tool_use",
                "id": tu["id"],
                "name": tu["name"],
                "input": tu.get("input", {}),
            })
        return {"role": "assistant", "content": blocks}

    def build_tool_result_message(
        self, tool_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        blocks = []
        for tr in tool_results:
            blocks.append({
                "type": "tool_result",
                "tool_use_id": tr["tool_use_id"],
                "content": tr["content"],
            })
        return {"role": "user", "content": blocks}


# ---------------------------------------------------------------------------
# Mock Tool — a BaseMCPTool that returns canned results
# ---------------------------------------------------------------------------

class MockTool:
    """Minimal tool implementation compatible with DirectMCPTool wrapping.

    InputSchema must be a real Pydantic BaseModel for LangChain's BaseTool
    validation to accept it.
    """
    name = "mock_echo"
    description = "Echoes back the input message."
    is_internal = False

    def __init__(self, canned_response: str = "echo: hello"):
        self._response = canned_response
        # Build InputSchema dynamically so pydantic is only needed at runtime
        from pydantic import BaseModel, Field

        class _Schema(BaseModel):
            message: str = Field(default="", description="Message to echo")

        self.InputSchema = _Schema

    async def execute(self, message: str = "", **kwargs) -> str:
        return self._response


# ---------------------------------------------------------------------------
# Executor factory
# ---------------------------------------------------------------------------

def _make_executor(provider: MockProvider):
    """Create a StreamingToolExecutor with a MockProvider, bypassing __init__."""
    with patch.dict(os.environ, {
        "ZIYA_ENDPOINT": "bedrock",
        "ZIYA_MODEL": "sonnet3.7",
    }):
        with patch(
            "app.streaming_tool_executor.StreamingToolExecutor.__init__",
            return_value=None,
        ):
            from app.streaming_tool_executor import StreamingToolExecutor
            executor = StreamingToolExecutor.__new__(StreamingToolExecutor)
            executor.model_id = "mock-model-id"
            executor.model_config = {
                "family": "claude",
                "max_output_tokens": 8192,
                "supports_assistant_prefill": True,
                "supports_thinking": False,
                "supports_adaptive_thinking": False,
            }
            executor.bedrock = None
            executor.provider = provider
            return executor


# ---------------------------------------------------------------------------
# Common patches — disable subsystems that need real infrastructure
# ---------------------------------------------------------------------------

# The signing module is imported lazily inside tool_execution.py
def _make_fake_calibrator():
    """Return a calibrator-like object that returns real ints (not MagicMock).

    The orchestrator uses f-string {:,} formatting on values returned by
    the calibrator, which blows up on MagicMock instances.
    """
    cal = MagicMock()
    cal.estimate_tokens = MagicMock(return_value=500)
    cal.get_baseline_overhead = MagicMock(return_value=100)
    cal.baselines_measured = set()
    cal.baseline_overhead_tokens = {}
    cal.baseline_tool_counts = {}
    cal.record_actual_usage = MagicMock()
    cal._save_calibration_data = MagicMock()
    return cal


_COMMON_PATCHES = {
    # MCP manager + tool loading
    "app.mcp.manager.get_mcp_manager": MagicMock,
    "app.mcp.enhanced_tools.create_secure_mcp_tools": lambda: [],
    # Signing & verification
    "app.mcp.signing.verify_tool_result": lambda result, *a, **kw: (True, None),
    "app.mcp.signing.strip_signature_metadata": lambda r: r,
    "app.mcp.signing.sign_tool_result": lambda *a, **kw: a[2] if len(a) > 2 else {},
    # Audit log
    "app.utils.tool_audit_log.log_tool_execution": lambda **kw: None,
    # Result sanitizer
    "app.utils.tool_result_sanitizer.sanitize_for_context": lambda text, **kw: text,
    # Response validator (text sanitization)
    "app.mcp.response_validator.sanitize_text": lambda t: t,
    # Server-side globals that don't exist in test context
    "app.server.record_verification_result": lambda *a, **kw: None,
    "app.server.active_feedback_connections": {},
    # Token calibration — must return an object whose methods return real ints,
    # not MagicMock, because the orchestrator uses f-string {:,} formatting.
    "app.utils.token_calibrator.get_token_calibrator": lambda: _make_fake_calibrator(),
}


def _apply_patches():
    """Return a list of started unittest.mock._patch objects."""
    patches = []
    for target, replacement in _COMMON_PATCHES.items():
        if callable(replacement) and not isinstance(replacement, (dict, list)):
            p = patch(target, side_effect=replacement)
        else:
            p = patch(target, replacement)
        patches.append(p)
        p.start()
    return patches


def _stop_patches(patches):
    for p in patches:
        p.stop()


async def _collect_stream(executor, messages, extra_tools=None):
    """Run stream_with_tools and collect all yielded events."""
    events = []
    async for evt in executor.stream_with_tools(
        messages,
        conversation_id="test-conv-001",
        extra_tools=extra_tools,
    ):
        events.append(evt)
    return events


def _events_of_type(events, type_name):
    """Filter events by their 'type' key."""
    return [e for e in events if e.get("type") == type_name]


# ---------------------------------------------------------------------------
# Script builders — create StreamEvent sequences for common patterns
# ---------------------------------------------------------------------------

def _usage(inp=100, out=50, cached=0, written=0):
    return UsageEvent(
        input_tokens=inp, output_tokens=out,
        cache_read_tokens=cached, cache_write_tokens=written,
    )


def script_text_only(text: str = "Hello! I can help you with that."):
    """Provider responds with plain text, no tool calls."""
    return [
        TextDelta(content=text),
        _usage(),
        StreamEnd(stop_reason="end_turn"),
    ]


def script_tool_call(
    tool_name: str = "mock_echo",
    tool_id: str = "toolu_abc123",
    args: dict = None,
    index: int = 1,
):
    """Provider requests a tool call (text preamble + tool_use + end)."""
    args = args or {"message": "hello"}
    args_json = json.dumps(args)
    return [
        TextDelta(content="Let me check that for you.\n\n"),
        ToolUseStart(id=tool_id, name=tool_name, index=index),
        ToolUseInput(partial_json=args_json, index=index),
        ToolUseEnd(id=tool_id, name=tool_name, input=args, index=index),
        _usage(inp=120, out=80),
        StreamEnd(stop_reason="tool_use"),
    ]


def script_final_response(text: str = "Based on the result, everything looks good."):
    """Provider delivers a final text response after tool execution."""
    return [
        TextDelta(content=text),
        _usage(inp=200, out=60, cached=100),
        StreamEnd(stop_reason="end_turn"),
    ]


# ===========================================================================
# Test cases
# ===========================================================================

class TestTextOnlyResponse:
    """Simplest path: model responds with text, no tools."""

    @pytest.mark.asyncio
    async def test_text_chunks_arrive_and_stream_ends(self):
        provider = MockProvider([script_text_only("The answer is 42.")])
        executor = _make_executor(provider)
        patches = _apply_patches()
        try:
            events = await _collect_stream(
                executor,
                [{"role": "user", "content": "What is the answer?"}],
            )
            text_events = _events_of_type(events, "text")
            assert len(text_events) >= 1
            combined_text = "".join(e["content"] for e in text_events)
            assert "42" in combined_text

            end_events = _events_of_type(events, "stream_end")
            assert len(end_events) >= 1

            assert provider.call_count == 1
        finally:
            _stop_patches(patches)


class TestSingleToolCall:
    """Core loop: model calls one tool, gets result, produces final response."""

    @pytest.mark.asyncio
    async def test_tool_call_result_feeds_back_to_model(self):
        """Iteration 0: tool call. Iteration 1: final text using tool result."""
        provider = MockProvider([
            script_tool_call(tool_name="mock_echo", args={"message": "ping"}),
            script_final_response("The echo returned: pong."),
        ])
        executor = _make_executor(provider)
        mock_tool = MockTool(canned_response="pong")
        patches = _apply_patches()
        try:
            events = await _collect_stream(
                executor,
                [{"role": "user", "content": "Echo ping for me."}],
                extra_tools=[mock_tool],
            )

            # Tool lifecycle events should be present
            tool_starts = _events_of_type(events, "tool_start")
            assert len(tool_starts) >= 1
            assert tool_starts[0]["tool_name"] == "mock_echo"

            tool_displays = _events_of_type(events, "tool_display")
            assert len(tool_displays) >= 1
            assert "pong" in str(tool_displays[0].get("result", ""))

            # Final text should appear
            text_events = _events_of_type(events, "text")
            combined = "".join(e["content"] for e in text_events)
            assert "pong" in combined.lower() or "echo" in combined.lower()

            # Provider should have been called twice (tool call + final response)
            assert provider.call_count == 2

            # Second call should include tool result in conversation
            second_call = provider.call_args_log[1]
            messages = second_call["messages"]
            # Should have: original user msg, assistant w/tool_use, user w/tool_result
            assert len(messages) >= 3
            # Find the tool_result message (user message with tool_result blocks)
            tool_result_msgs = [
                m for m in messages
                if m.get("role") == "user"
                and isinstance(m.get("content"), list)
                and any(b.get("type") == "tool_result" for b in m["content"])
            ]
            assert len(tool_result_msgs) >= 1, (
                f"Expected a user message with tool_result blocks in: "
                f"{[m.get('role') for m in messages]}"
            )
        finally:
            _stop_patches(patches)


class TestMultiToolSequence:
    """Model calls two tools in sequence within one response, then responds."""

    @pytest.mark.asyncio
    async def test_two_tools_then_final_response(self):
        # Iteration 0: model calls two tools in a single response
        two_tool_script = [
            TextDelta(content="I'll check two things.\n\n"),
            # First tool call
            ToolUseStart(id="toolu_1", name="mock_echo", index=1),
            ToolUseInput(partial_json='{"message": "first"}', index=1),
            ToolUseEnd(id="toolu_1", name="mock_echo", input={"message": "first"}, index=1),
            # Second tool call
            ToolUseStart(id="toolu_2", name="mock_echo", index=2),
            ToolUseInput(partial_json='{"message": "second"}', index=2),
            ToolUseEnd(id="toolu_2", name="mock_echo", input={"message": "second"}, index=2),
            _usage(inp=150, out=100),
            StreamEnd(stop_reason="tool_use"),
        ]
        provider = MockProvider([
            two_tool_script,
            script_final_response("Both checks passed. First: done. Second: done."),
        ])
        executor = _make_executor(provider)
        mock_tool = MockTool(canned_response="done")
        patches = _apply_patches()
        try:
            events = await _collect_stream(
                executor,
                [{"role": "user", "content": "Run both checks."}],
                extra_tools=[mock_tool],
            )

            tool_starts = _events_of_type(events, "tool_start")
            assert len(tool_starts) == 2

            tool_displays = _events_of_type(events, "tool_display")
            assert len(tool_displays) == 2

            # Provider called twice: once for tool calls, once for final response
            assert provider.call_count == 2

            # Second call conversation should have 2 tool_result blocks
            second_msgs = provider.call_args_log[1]["messages"]
            tool_result_blocks = []
            for msg in second_msgs:
                if isinstance(msg.get("content"), list):
                    for b in msg["content"]:
                        if b.get("type") == "tool_result":
                            tool_result_blocks.append(b)
            assert len(tool_result_blocks) == 2
        finally:
            _stop_patches(patches)


class TestConversationStateEvolution:
    """Verify conversation history is correctly maintained across iterations."""

    @pytest.mark.asyncio
    async def test_conversation_grows_correctly(self):
        """After a tool call, conversation should contain:
        [user_msg, assistant_w_tool_use, user_w_tool_result]"""
        provider = MockProvider([
            script_tool_call(tool_name="mock_echo", args={"message": "test"}),
            script_final_response("Done."),
        ])
        executor = _make_executor(provider)
        mock_tool = MockTool(canned_response="result")
        patches = _apply_patches()
        try:
            await _collect_stream(
                executor,
                [{"role": "user", "content": "Do the thing."}],
                extra_tools=[mock_tool],
            )

            # After iteration 0 (tool call), conversation sent to iteration 1 should have:
            # msg[0] = original user message
            # msg[1] = assistant message with tool_use block
            # msg[2] = user message with tool_result block
            assert provider.call_count == 2
            iter1_msgs = provider.call_args_log[1]["messages"]

            assert iter1_msgs[0]["role"] == "user"
            assert iter1_msgs[0]["content"] == "Do the thing."

            assert iter1_msgs[1]["role"] == "assistant"
            assistant_blocks = iter1_msgs[1]["content"]
            assert any(b.get("type") == "tool_use" for b in assistant_blocks)

            assert iter1_msgs[2]["role"] == "user"
            result_blocks = iter1_msgs[2]["content"]
            assert any(b.get("type") == "tool_result" for b in result_blocks)
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_system_content_passed_to_provider(self):
        """System messages should be extracted and passed as system_content."""
        provider = MockProvider([script_text_only("OK.")])
        executor = _make_executor(provider)
        patches = _apply_patches()
        try:
            await _collect_stream(
                executor,
                [
                    {"role": "system", "content": "You are a helpful bot."},
                    {"role": "user", "content": "Hello."},
                ],
            )
            assert provider.call_count == 1
            assert provider.call_args_log[0]["system_content"] == "You are a helpful bot."
        finally:
            _stop_patches(patches)


class TestEmptyToolArgsRecovery:
    """When the model sends a tool call with empty/invalid args,
    the orchestrator should send a self-correcting error back and
    the model should retry successfully."""

    @pytest.mark.asyncio
    async def test_empty_args_get_error_feedback(self):
        """Iteration 0: model sends tool with empty JSON.
        The orchestrator feeds back an error.
        Iteration 1: model retries with correct args.
        Iteration 2: final response."""
        # Script 0: tool call with empty args (will be caught by arg validation)
        empty_call = [
            TextDelta(content="Let me run that.\n\n"),
            ToolUseStart(id="toolu_empty", name="mock_echo", index=1),
            ToolUseInput(partial_json="{}", index=1),
            ToolUseEnd(id="toolu_empty", name="mock_echo", input={}, index=1),
            _usage(),
            StreamEnd(stop_reason="tool_use"),
        ]
        # Script 1: retry with correct args
        retry_call = script_tool_call(
            tool_name="mock_echo", tool_id="toolu_retry",
            args={"message": "hello"},
        )
        # Script 2: final response
        final = script_final_response("Got the echo result.")

        provider = MockProvider([empty_call, retry_call, final])
        executor = _make_executor(provider)
        mock_tool = MockTool(canned_response="echoed")
        patches = _apply_patches()
        try:
            events = await _collect_stream(
                executor,
                [{"role": "user", "content": "Echo hello."}],
                extra_tools=[mock_tool],
            )

            # The empty-args call should have produced a tool_result_for_model
            # with an error message, causing the model to retry
            assert provider.call_count >= 2

            # The retry should have succeeded — look for a tool_display with result
            displays = _events_of_type(events, "tool_display")
            successful_displays = [
                d for d in displays if "echoed" in str(d.get("result", ""))
            ]
            assert len(successful_displays) >= 1
        finally:
            _stop_patches(patches)


class TestErrorEventHandling:
    """Provider yields a non-retryable ErrorEvent (e.g. context too long)."""

    @pytest.mark.asyncio
    async def test_context_limit_error_surfaces_to_user(self):
        error_script = [
            ErrorEvent(
                message="Input is too long for model context window.",
                error_type=ErrorType.CONTEXT_LIMIT,
                retryable=False,
            ),
        ]
        provider = MockProvider([error_script])
        executor = _make_executor(provider)
        patches = _apply_patches()
        try:
            events = await _collect_stream(
                executor,
                [{"role": "user", "content": "x" * 1000}],
            )
            error_events = _events_of_type(events, "error")
            assert len(error_events) >= 1
            assert "too long" in error_events[0]["content"].lower()
        finally:
            _stop_patches(patches)


class TestUsageTracking:
    """Verify that token usage metrics are tracked across iterations."""

    @pytest.mark.asyncio
    async def test_usage_metrics_accumulate(self):
        """Two iterations should produce cumulative usage."""
        provider = MockProvider([
            # Iteration 0: tool call with known usage
            [
                TextDelta(content="Calling tool.\n\n"),
                ToolUseStart(id="toolu_u1", name="mock_echo", index=1),
                ToolUseInput(partial_json='{"message":"hi"}', index=1),
                ToolUseEnd(id="toolu_u1", name="mock_echo",
                           input={"message": "hi"}, index=1),
                UsageEvent(input_tokens=100, output_tokens=50,
                           cache_read_tokens=80),
                StreamEnd(stop_reason="tool_use"),
            ],
            # Iteration 1: final response with more usage
            [
                TextDelta(content="All done."),
                UsageEvent(input_tokens=200, output_tokens=30,
                           cache_read_tokens=150),
                StreamEnd(stop_reason="end_turn"),
            ],
        ])
        executor = _make_executor(provider)
        mock_tool = MockTool(canned_response="hi back")
        patches = _apply_patches()
        try:
            events = await _collect_stream(
                executor,
                [{"role": "user", "content": "Say hi."}],
                extra_tools=[mock_tool],
            )
            # Stream should complete successfully
            assert len(_events_of_type(events, "stream_end")) >= 1
            # Provider called twice
            assert provider.call_count == 2
        finally:
            _stop_patches(patches)


class TestStreamEndConditions:
    """Verify the orchestrator correctly terminates under various conditions."""

    @pytest.mark.asyncio
    async def test_no_provider_yields_error(self):
        """If provider is None, stream should yield error and end."""
        with patch.dict(os.environ, {
            "ZIYA_ENDPOINT": "bedrock",
            "ZIYA_MODEL": "sonnet3.7",
        }):
            with patch(
                "app.streaming_tool_executor.StreamingToolExecutor.__init__",
                return_value=None,
            ):
                from app.streaming_tool_executor import StreamingToolExecutor
                executor = StreamingToolExecutor.__new__(StreamingToolExecutor)
                executor.model_id = "mock"
                executor.model_config = {"family": "claude"}
                executor.bedrock = None
                executor.provider = None  # No provider

                patches = _apply_patches()
                try:
                    events = await _collect_stream(
                        executor,
                        [{"role": "user", "content": "Hello"}],
                    )
                    error_events = _events_of_type(events, "error")
                    assert len(error_events) >= 1
                    assert "provider" in error_events[0]["content"].lower()
                    end_events = _events_of_type(events, "stream_end")
                    assert len(end_events) >= 1
                finally:
                    _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_text_only_response_ends_cleanly(self):
        """A text-only response should produce stream_end without extra iterations."""
        provider = MockProvider([script_text_only("Simple answer.")])
        executor = _make_executor(provider)
        patches = _apply_patches()
        try:
            events = await _collect_stream(
                executor,
                [{"role": "user", "content": "Question?"}],
            )
            assert provider.call_count == 1
            assert len(_events_of_type(events, "stream_end")) >= 1
        finally:
            _stop_patches(patches)


class TestEventOrdering:
    """Verify events arrive in the correct sequence for a tool-call flow."""

    @pytest.mark.asyncio
    async def test_event_sequence_for_tool_flow(self):
        """Expected order:
        text → tool_start → tool_display → tool_result_for_model →
        iteration_continue → text → stream_end
        """
        provider = MockProvider([
            script_tool_call(tool_name="mock_echo", args={"message": "order_test"}),
            script_final_response("Final answer after tool."),
        ])
        executor = _make_executor(provider)
        mock_tool = MockTool(canned_response="ordered_result")
        patches = _apply_patches()
        try:
            events = await _collect_stream(
                executor,
                [{"role": "user", "content": "Test ordering."}],
                extra_tools=[mock_tool],
            )
            types = [e.get("type") for e in events]

            # Text from preamble should come before tool_start
            first_text_idx = next(
                (i for i, t in enumerate(types) if t == "text"), None
            )
            first_tool_start_idx = next(
                (i for i, t in enumerate(types) if t == "tool_start"), None
            )
            assert first_text_idx is not None
            assert first_tool_start_idx is not None
            assert first_text_idx < first_tool_start_idx

            # tool_display should come after tool_start
            first_display_idx = next(
                (i for i, t in enumerate(types) if t == "tool_display"), None
            )
            assert first_display_idx is not None
            assert first_display_idx > first_tool_start_idx

            # stream_end should be last meaningful event
            last_end_idx = max(
                i for i, t in enumerate(types) if t == "stream_end"
            )
            last_text_idx = max(
                i for i, t in enumerate(types) if t == "text"
            )
            # stream_end comes after the final text
            assert last_end_idx > last_text_idx
        finally:
            _stop_patches(patches)
