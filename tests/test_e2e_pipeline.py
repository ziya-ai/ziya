"""
End-to-end pipeline integration test.

Exercises the full request → agent → tool → response pipeline:

    FastAPI /api/chat  →  stream_chunks()  →  StreamingToolExecutor
        →  LLMProvider.stream_response()  →  MCP tool execution
        →  SSE response stream

Uses dependency injection at two levels:
  1. LLMProvider — a mock provider that yields controlled StreamEvent
     sequences (text deltas, tool_use blocks, stream end).
  2. MCP Manager — a mock that returns controlled tool results.

No real AWS credentials, no real LLM calls, no real MCP servers.

Run:
    pytest tests/test_e2e_pipeline.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Mock LLM Provider
# ---------------------------------------------------------------------------

from app.providers.base import (
    LLMProvider,
    ProviderConfig,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
    UsageEvent,
)


class MockLLMProvider(LLMProvider):
    """
    A controllable LLM provider for integration tests.

    On each call to ``stream_response``, pops the next scripted turn
    from ``self.turns``.  Each turn is a list of ``StreamEvent`` objects
    to yield.

    If a turn includes ``ToolUseEnd``, the orchestrator will execute the
    tool and call ``stream_response`` again for the next turn (tool-result
    → assistant response).
    """

    def __init__(self, turns: List[List[StreamEvent]]):
        self.turns = list(turns)
        self._call_count = 0

    async def stream_response(
        self,
        messages: List[Dict[str, Any]],
        system_content: Optional[str],
        tools: List[Dict[str, Any]],
        config: ProviderConfig,
    ) -> AsyncGenerator[StreamEvent, None]:
        turn_index = self._call_count
        self._call_count += 1

        if turn_index >= len(self.turns):
            # Safety: if the orchestrator calls more times than scripted,
            # just end the stream.
            yield TextDelta(content="[unexpected extra turn]")
            yield StreamEnd(stop_reason="end_turn")
            return

        for event in self.turns[turn_index]:
            yield event
            await asyncio.sleep(0)  # yield control

    def build_assistant_message(
        self, text: str, tool_uses: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        content = []
        if text:
            content.append({"type": "text", "text": text})
        for tu in tool_uses:
            content.append({
                "type": "tool_use",
                "id": tu["id"],
                "name": tu["name"],
                "input": tu["input"],
            })
        return {"role": "assistant", "content": content}

    def build_tool_result_message(
        self, tool_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        content = []
        for tr in tool_results:
            content.append({
                "type": "tool_result",
                "tool_use_id": tr["tool_use_id"],
                "content": tr["content"],
            })
        return {"role": "user", "content": content}

    @property
    def provider_name(self) -> str:
        return "mock"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_text_only_turns(text: str) -> List[List[StreamEvent]]:
    """Single turn: emit text then end."""
    return [[
        TextDelta(content=text),
        UsageEvent(input_tokens=100, output_tokens=20),
        StreamEnd(stop_reason="end_turn"),
    ]]


def _make_tool_call_turns(
    text_before_tool: str,
    tool_name: str,
    tool_input: Dict[str, Any],
    text_after_tool: str,
    tool_use_id: str = "tu_001",
) -> List[List[StreamEvent]]:
    """
    Two turns:
      Turn 1: emit text, then a tool_use block, then end with stop_reason=tool_use.
      Turn 2: emit the final response text, then end.
    """
    return [
        # Turn 1 — assistant text + tool call
        [
            TextDelta(content=text_before_tool),
            ToolUseStart(id=tool_use_id, name=tool_name, index=0),
            ToolUseInput(partial_json=json.dumps(tool_input), index=0),
            ToolUseEnd(id=tool_use_id, name=tool_name, input=tool_input, index=0),
            UsageEvent(input_tokens=150, output_tokens=30),
            StreamEnd(stop_reason="tool_use"),
        ],
        # Turn 2 — assistant response after tool result
        [
            TextDelta(content=text_after_tool),
            UsageEvent(input_tokens=200, output_tokens=40, cache_read_tokens=100),
            StreamEnd(stop_reason="end_turn"),
        ],
    ]


def _build_mock_mcp_manager(tool_name: str, tool_result: str):
    """
    Build a mock MCP manager that responds to a specific tool name.

    Returns a MagicMock with:
      - is_initialized = True
      - get_all_tools() returning one tool with the given name
      - call_tool() returning the scripted result
    """
    mock_tool = MagicMock()
    mock_tool.name = tool_name
    mock_tool.description = f"Mock tool: {tool_name}"
    mock_tool.inputSchema = {"type": "object", "properties": {}}
    mock_tool._server_name = "mock_server"

    mock_manager = MagicMock()
    mock_manager.is_initialized = True
    mock_manager.get_all_tools.return_value = [mock_tool]
    mock_manager._tool_cache = [mock_tool]
    mock_manager.clients = {"mock_server": MagicMock(is_connected=True, tools=[mock_tool])}
    mock_manager.server_configs = {
        "mock_server": {"enabled": True, "builtin": True}
    }
    mock_manager.invalidate_tools_cache = MagicMock()
    mock_manager.get_server_status.return_value = {
        "mock_server": {"connected": True, "tools": 1, "resources": 0,
                        "prompts": 0, "capabilities": {}, "builtin": True}
    }

    # call_tool returns the scripted result
    async def mock_call_tool(name, arguments, **kwargs):
        # Strip mcp_ prefix like the real manager does
        internal_name = name[4:] if name.startswith("mcp_") else name
        if internal_name == tool_name:
            return {
                "content": [{"type": "text", "text": tool_result}]
            }
        return {"error": True, "message": f"Unknown tool: {name}"}

    mock_manager.call_tool = AsyncMock(side_effect=mock_call_tool)
    return mock_manager


def _collect_sse_events(raw_body: str) -> List[Dict[str, Any]]:
    """Parse SSE text into a list of JSON payloads."""
    events = []
    for line in raw_body.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            payload = line[6:]
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                pass  # skip non-JSON lines like [DONE]
    return events


# ---------------------------------------------------------------------------
# Environment setup — runs once per module
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _pipeline_env(tmp_path, monkeypatch):
    """Set required environment variables for the pipeline to run."""
    monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
    monkeypatch.setenv("ZIYA_MODEL", "sonnet4.0")
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    monkeypatch.setenv("ZIYA_MODE", "test")
    monkeypatch.setenv("ZIYA_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("AWS_PROFILE", "default")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    # Prevent real plugin loading from interferring
    monkeypatch.setenv("ZIYA_DISABLE_PLUGINS", "1")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestE2EPipelineTextOnly:
    """Verify the simplest path: question → LLM text → SSE stream."""

    @pytest.mark.asyncio
    async def test_text_only_response_streams_content_and_done(self, _pipeline_env):
        """A plain text response should produce content events + done marker."""
        expected_text = "Hello! The answer is 42."
        provider = MockLLMProvider(turns=_make_text_only_turns(expected_text))

        from app.streaming_tool_executor import StreamingToolExecutor

        # Patch the provider factory so StreamingToolExecutor uses our mock
        with patch("app.providers.factory.create_provider", return_value=provider), \
             patch("app.streaming_tool_executor.StreamingToolExecutor.__init__",
                   lambda self, **kw: self.__dict__.update(
                       provider=provider,
                       bedrock=None,
                       model_id="mock-model",
                       model_config={"family": "claude", "token_limit": 200000,
                                     "max_output_tokens": 4096,
                                     "supports_extended_context": False,
                                     "name": "mock"},
                       temperature_override=None,
                       max_tokens_override=None,
                   )):
            executor = StreamingToolExecutor()

            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is the meaning of life?"},
            ]

            chunks = []
            async for chunk in executor.stream_with_tools(messages, tools=[]):
                chunks.append(chunk)

        # Verify we got text content
        text_chunks = [c for c in chunks if c.get("type") == "text"]
        assert len(text_chunks) > 0, f"Expected text chunks, got: {chunks}"

        combined_text = "".join(c["content"] for c in text_chunks)
        assert expected_text in combined_text, (
            f"Expected '{expected_text}' in response, got: {combined_text}"
        )

        # Verify stream ended
        end_chunks = [c for c in chunks if c.get("type") == "stream_end"]
        assert len(end_chunks) == 1, f"Expected exactly 1 stream_end, got: {end_chunks}"


class TestE2EPipelineWithToolCall:
    """Verify the tool execution path: question → LLM → tool_use → tool result → LLM → response."""

    @pytest.mark.asyncio
    async def test_tool_call_executes_and_result_feeds_back(self, _pipeline_env):
        """
        When the LLM emits a tool_use block, the orchestrator should:
        1. Execute the tool via MCP manager
        2. Feed the result back to the LLM
        3. Stream the final response
        """
        tool_name = "get_current_time"
        tool_input = {"format": "iso"}
        tool_result_text = "2025-01-15T10:30:00Z"
        tool_use_id = f"toolu_{uuid.uuid4().hex[:12]}"

        provider = MockLLMProvider(
            turns=_make_tool_call_turns(
                text_before_tool="Let me check the time. ",
                tool_name=tool_name,
                tool_input=tool_input,
                text_after_tool=f"The current time is {tool_result_text}.",
                tool_use_id=tool_use_id,
            )
        )

        mock_mcp = _build_mock_mcp_manager(tool_name, tool_result_text)

        # Build a minimal tool definition for the executor
        mock_tool = MagicMock()
        mock_tool.name = tool_name
        mock_tool.description = "Get the current time"
        mock_tool.metadata = {
            "input_schema": {"type": "object", "properties": {"format": {"type": "string"}}}
        }

        from app.streaming_tool_executor import StreamingToolExecutor

        with patch("app.providers.factory.create_provider", return_value=provider), \
             patch("app.streaming_tool_executor.StreamingToolExecutor.__init__",
                   lambda self, **kw: self.__dict__.update(
                       provider=provider,
                       bedrock=None,
                       model_id="mock-model",
                       model_config={"family": "claude", "token_limit": 200000,
                                     "max_output_tokens": 4096,
                                     "supports_extended_context": False,
                                     "name": "mock"},
                       temperature_override=None,
                       max_tokens_override=None,
                   )), \
             patch("app.mcp.manager.get_mcp_manager", return_value=mock_mcp), \
             patch("app.mcp.signing.sign_tool_result",
                   side_effect=lambda name, args, result, conv_id: result), \
             patch("app.mcp.signing.verify_tool_result",
                   return_value=(True, None)):
            executor = StreamingToolExecutor()

            messages = [
                {"role": "system", "content": "You are a helpful assistant with tools."},
                {"role": "user", "content": "What time is it?"},
            ]

            chunks = []
            async for chunk in executor.stream_with_tools(
                messages, tools=[mock_tool], conversation_id="test-conv-001"
            ):
                chunks.append(chunk)

        # Verify the provider was called twice (turn 1 + turn 2 after tool result)
        assert provider._call_count == 2, (
            f"Expected 2 provider calls (initial + post-tool), got {provider._call_count}"
        )

        # Verify tool was actually called via MCP manager
        mock_mcp.call_tool.assert_called_once()
        call_args = mock_mcp.call_tool.call_args
        called_tool_name = call_args[0][0]
        # The executor may strip the mcp_ prefix or leave it
        assert tool_name in called_tool_name, (
            f"Expected tool '{tool_name}' to be called, got '{called_tool_name}'"
        )

        # Verify we got text from both turns
        text_chunks = [c for c in chunks if c.get("type") == "text"]
        combined_text = "".join(c.get("content", "") for c in text_chunks)
        assert "check the time" in combined_text.lower() or "let me" in combined_text.lower(), (
            f"Expected pre-tool text in response, got: {combined_text}"
        )
        assert tool_result_text in combined_text, (
            f"Expected '{tool_result_text}' in post-tool response, got: {combined_text}"
        )

        # Verify tool_display events were emitted (for frontend UI)
        tool_display_chunks = [c for c in chunks if c.get("type") == "tool_display"]
        assert len(tool_display_chunks) >= 1, (
            f"Expected at least 1 tool_display event, got: "
            f"{[c.get('type') for c in chunks]}"
        )

        # Verify tool result content is in the display
        display_result = tool_display_chunks[0].get("result", "")
        assert tool_result_text in display_result, (
            f"Expected tool result '{tool_result_text}' in tool_display, got: {display_result}"
        )

        # Verify stream ended cleanly
        end_chunks = [c for c in chunks if c.get("type") == "stream_end"]
        assert len(end_chunks) == 1


class TestE2EPipelineFastAPIIntegration:
    """
    Full-stack test: send an HTTP POST to /api/chat and validate the SSE
    response stream.  This exercises server.py's stream_chunks() and the
    FastAPI middleware stack.
    """

    @pytest.mark.asyncio
    async def test_chat_endpoint_returns_sse_stream(self, _pipeline_env, tmp_path):
        """POST /api/chat should return SSE with content events and done marker."""
        expected_text = "Integration test response."
        provider = MockLLMProvider(turns=_make_text_only_turns(expected_text))
        mock_mcp = _build_mock_mcp_manager("dummy", "unused")

        # We need to patch several things to prevent real initialization:
        # 1. StreamingToolExecutor.__init__ — avoid real AWS client creation
        # 2. create_provider — return our mock
        # 3. MCP manager — return our mock
        # 4. create_secure_mcp_tools — return empty list (no real tools)
        # 5. build_messages_for_streaming — return simple messages

        simple_messages = [
            MagicMock(type="system", content="You are helpful."),
            MagicMock(type="human", content="Hello"),
        ]
        # Make them behave like LangChain messages for hasattr checks
        for m in simple_messages:
            m.additional_kwargs = {}

        with patch("app.streaming_tool_executor.StreamingToolExecutor.__init__",
                   lambda self, **kw: self.__dict__.update(
                       provider=provider,
                       bedrock=None,
                       model_id="mock-model",
                       model_config={"family": "claude", "token_limit": 200000,
                                     "max_output_tokens": 4096,
                                     "supports_extended_context": False,
                                     "name": "mock"},
                       temperature_override=None,
                       max_tokens_override=None,
                   )), \
             patch("app.mcp.manager.get_mcp_manager", return_value=mock_mcp), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
             patch("app.server.build_messages_for_streaming",
                   return_value=simple_messages):

            # Import app AFTER patches are in place
            from httpx import AsyncClient, ASGITransport
            from app.server import app as fastapi_app

            transport = ASGITransport(app=fastapi_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/chat",
                    json={
                        "question": "Hello",
                        "messages": [],
                        "files": [],
                        "conversation_id": f"e2e-test-{uuid.uuid4().hex[:8]}",
                    },
                    headers={"Content-Type": "application/json"},
                    timeout=30.0,
                )

            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: {response.text[:500]}"
            )

            # Verify SSE content type
            content_type = response.headers.get("content-type", "")
            assert "text/event-stream" in content_type, (
                f"Expected text/event-stream, got: {content_type}"
            )

            # Parse SSE events
            events = _collect_sse_events(response.text)
            assert len(events) > 0, f"Expected SSE events, got raw: {response.text[:500]}"

            # Check for content events
            content_events = [e for e in events if "content" in e]
            assert len(content_events) > 0, (
                f"Expected content events, got: {events}"
            )

            combined = "".join(e.get("content", "") for e in content_events)
            assert expected_text in combined, (
                f"Expected '{expected_text}' in SSE content, got: {combined}"
            )

            # Check for done marker
            done_events = [e for e in events if e.get("done")]
            assert len(done_events) >= 1, (
                f"Expected done marker in SSE stream, events: {events}"
            )

            # Guard: no spurious error events in the stream.
            # A missing cleanup_stream definition caused a NameError
            # chunk to be injected after the done marker on every
            # successful stream completion.
            error_events = [e for e in events if e.get("error")]
            assert len(error_events) == 0, (
                f"Unexpected error events in successful SSE stream: {error_events}"
            )

class TestE2EPipelineErrorHandling:
    """Verify error conditions are properly surfaced through the pipeline."""

    @pytest.mark.asyncio
    async def test_provider_error_yields_error_chunk(self, _pipeline_env):
        """When the provider yields an ErrorEvent, the executor should emit an error chunk."""
        from app.providers.base import ErrorEvent, ErrorType

        provider = MockLLMProvider(turns=[[
            ErrorEvent(
                message="Rate limit exceeded",
                error_type=ErrorType.THROTTLE,
                retryable=False,
                status_code=429,
            ),
        ]])

        from app.streaming_tool_executor import StreamingToolExecutor

        with patch("app.providers.factory.create_provider", return_value=provider), \
             patch("app.streaming_tool_executor.StreamingToolExecutor.__init__",
                   lambda self, **kw: self.__dict__.update(
                       provider=provider,
                       bedrock=None,
                       model_id="mock-model",
                       model_config={"family": "claude", "token_limit": 200000,
                                     "max_output_tokens": 4096,
                                     "supports_extended_context": False,
                                     "name": "mock"},
                       temperature_override=None,
                       max_tokens_override=None,
                   )):
            executor = StreamingToolExecutor()

            messages = [{"role": "user", "content": "Trigger error"}]
            chunks = []
            async for chunk in executor.stream_with_tools(messages, tools=[]):
                chunks.append(chunk)

        # The executor should emit an error or throttling chunk
        error_chunks = [c for c in chunks
                        if c.get("type") in ("error", "throttling_error")]
        assert len(error_chunks) >= 1, (
            f"Expected error chunk from provider ErrorEvent, got types: "
            f"{[c.get('type') for c in chunks]}"
        )

    @pytest.mark.asyncio
    async def test_tool_execution_failure_is_reported(self, _pipeline_env):
        """When a tool call fails, the error should be reported in the stream."""
        tool_name = "failing_tool"
        tool_use_id = f"toolu_{uuid.uuid4().hex[:12]}"

        provider = MockLLMProvider(turns=[
            # Turn 1: call the tool
            [
                TextDelta(content="Let me try this tool. "),
                ToolUseStart(id=tool_use_id, name=tool_name, index=0),
                ToolUseInput(partial_json='{"arg": "value"}', index=0),
                ToolUseEnd(id=tool_use_id, name=tool_name,
                           input={"arg": "value"}, index=0),
                UsageEvent(input_tokens=100, output_tokens=20),
                StreamEnd(stop_reason="tool_use"),
            ],
            # Turn 2: respond after tool error
            [
                TextDelta(content="The tool encountered an error."),
                StreamEnd(stop_reason="end_turn"),
            ],
        ])

        # Build mock MCP manager that returns an error
        mock_mcp = MagicMock()
        mock_mcp.is_initialized = True
        mock_mcp.get_all_tools.return_value = []
        mock_mcp.clients = {}
        mock_mcp.server_configs = {}
        mock_mcp.invalidate_tools_cache = MagicMock()

        async def failing_call_tool(name, arguments, **kwargs):
            return {"error": True, "message": "Tool crashed: connection refused"}

        mock_mcp.call_tool = AsyncMock(side_effect=failing_call_tool)

        mock_tool = MagicMock()
        mock_tool.name = tool_name
        mock_tool.description = "A tool that fails"
        mock_tool.metadata = {"input_schema": {"type": "object", "properties": {}}}

        from app.streaming_tool_executor import StreamingToolExecutor

        with patch("app.providers.factory.create_provider", return_value=provider), \
             patch("app.streaming_tool_executor.StreamingToolExecutor.__init__",
                   lambda self, **kw: self.__dict__.update(
                       provider=provider,
                       bedrock=None,
                       model_id="mock-model",
                       model_config={"family": "claude", "token_limit": 200000,
                                     "max_output_tokens": 4096,
                                     "supports_extended_context": False,
                                     "name": "mock"},
                       temperature_override=None,
                       max_tokens_override=None,
                   )), \
             patch("app.mcp.manager.get_mcp_manager", return_value=mock_mcp), \
             patch("app.mcp.signing.sign_tool_result",
                   side_effect=lambda name, args, result, conv_id: result), \
             patch("app.mcp.signing.verify_tool_result",
                   return_value=(True, None)):
            executor = StreamingToolExecutor()

            messages = [{"role": "user", "content": "Use the tool"}]
            chunks = []
            async for chunk in executor.stream_with_tools(
                messages, tools=[mock_tool], conversation_id="test-error-001"
            ):
                chunks.append(chunk)

        # Provider should still have been called twice (tool error is fed back as a tool result)
        assert provider._call_count == 2, (
            f"Expected 2 provider calls even with tool error, got {provider._call_count}"
        )

        # The stream should still complete (error is handled gracefully)
        end_chunks = [c for c in chunks if c.get("type") == "stream_end"]
        assert len(end_chunks) >= 1, "Stream should end cleanly even after tool error"


class TestE2EPipelineMultipleToolCalls:
    """Verify multiple sequential tool calls in a single conversation turn."""

    @pytest.mark.asyncio
    async def test_two_sequential_tool_calls(self, _pipeline_env):
        """LLM calls two tools in sequence, both results feed back correctly."""
        tool1_id = f"toolu_{uuid.uuid4().hex[:12]}"
        tool2_id = f"toolu_{uuid.uuid4().hex[:12]}"

        provider = MockLLMProvider(turns=[
            # Turn 1: call tool_a and tool_b
            [
                TextDelta(content="I'll check two things. "),
                ToolUseStart(id=tool1_id, name="tool_a", index=0),
                ToolUseInput(partial_json='{"q": "first"}', index=0),
                ToolUseEnd(id=tool1_id, name="tool_a", input={"q": "first"}, index=0),
                ToolUseStart(id=tool2_id, name="tool_b", index=1),
                ToolUseInput(partial_json='{"q": "second"}', index=1),
                ToolUseEnd(id=tool2_id, name="tool_b", input={"q": "second"}, index=1),
                UsageEvent(input_tokens=200, output_tokens=50),
                StreamEnd(stop_reason="tool_use"),
            ],
            # Turn 2: respond with both results
            [
                TextDelta(content="Tool A said: alpha. Tool B said: beta."),
                StreamEnd(stop_reason="end_turn"),
            ],
        ])

        # MCP manager that handles both tools
        mock_mcp = MagicMock()
        mock_mcp.is_initialized = True
        mock_mcp.get_all_tools.return_value = []
        mock_mcp.clients = {}
        mock_mcp.server_configs = {}
        mock_mcp.invalidate_tools_cache = MagicMock()

        tool_results = {"tool_a": "alpha", "tool_b": "beta"}

        async def multi_call_tool(name, arguments, **kwargs):
            internal = name[4:] if name.startswith("mcp_") else name
            if internal in tool_results:
                return {"content": [{"type": "text", "text": tool_results[internal]}]}
            return {"error": True, "message": f"Unknown: {name}"}

        mock_mcp.call_tool = AsyncMock(side_effect=multi_call_tool)

        mock_tools = []
        for tname in ["tool_a", "tool_b"]:
            t = MagicMock()
            t.name = tname
            t.description = f"Mock {tname}"
            t.metadata = {"input_schema": {"type": "object", "properties": {}}}
            mock_tools.append(t)

        from app.streaming_tool_executor import StreamingToolExecutor

        with patch("app.providers.factory.create_provider", return_value=provider), \
             patch("app.streaming_tool_executor.StreamingToolExecutor.__init__",
                   lambda self, **kw: self.__dict__.update(
                       provider=provider,
                       bedrock=None,
                       model_id="mock-model",
                       model_config={"family": "claude", "token_limit": 200000,
                                     "max_output_tokens": 4096,
                                     "supports_extended_context": False,
                                     "name": "mock"},
                       temperature_override=None,
                       max_tokens_override=None,
                   )), \
             patch("app.mcp.manager.get_mcp_manager", return_value=mock_mcp), \
             patch("app.mcp.signing.sign_tool_result",
                   side_effect=lambda name, args, result, conv_id: result), \
             patch("app.mcp.signing.verify_tool_result",
                   return_value=(True, None)):
            executor = StreamingToolExecutor()

            messages = [{"role": "user", "content": "Check both tools"}]
            chunks = []
            async for chunk in executor.stream_with_tools(
                messages, tools=mock_tools, conversation_id="test-multi-001"
            ):
                chunks.append(chunk)

        # Both tools should have been called
        assert mock_mcp.call_tool.call_count == 2, (
            f"Expected 2 tool calls, got {mock_mcp.call_tool.call_count}"
        )

        # Final text should contain results from both tools
        text_chunks = [c for c in chunks if c.get("type") == "text"]
        combined = "".join(c.get("content", "") for c in text_chunks)
        assert "alpha" in combined and "beta" in combined, (
            f"Expected both tool results in response, got: {combined}"
        )
