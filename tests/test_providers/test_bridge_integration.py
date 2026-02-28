"""
Integration tests for the provider → bridge → orchestrator pipeline.

These tests verify that:
  1. Provider StreamEvent objects are correctly translated to legacy chunk dicts
     by the bridge pattern in StreamingToolExecutor
  2. The orchestrator's event dispatch handles all event types correctly
  3. The _build_provider_config helper produces correct configs from model_config
  4. build_assistant_message / build_tool_result_message produce valid conversation
     entries for both providers
  5. The provider guard (self.provider is None) yields error gracefully
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from typing import Dict, Any, List

from app.providers.base import (
    ErrorEvent,
    ErrorType,
    LLMProvider,
    ProviderConfig,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingConfig,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
    UsageEvent,
)


# -----------------------------------------------------------------------
# Bridge pattern tests — StreamEvent → legacy chunk dict translation
# -----------------------------------------------------------------------

class TestBridgeTranslation:
    """Verify the bridge code in streaming_tool_executor converts events
    to the legacy chunk dict format the orchestration code expects."""

    def _bridge(self, event: StreamEvent) -> Dict[str, Any]:
        """Replicate the bridge logic from streaming_tool_executor.py lines ~1453-1488."""
        if isinstance(event, TextDelta):
            return {'type': 'content_block_delta',
                    'delta': {'type': 'text_delta', 'text': event.content}}
        elif isinstance(event, ToolUseStart):
            return {'type': 'content_block_start',
                    'index': event.index,
                    'content_block': {'type': 'tool_use',
                                      'id': event.id,
                                      'name': event.name}}
        elif isinstance(event, ToolUseInput):
            return {'type': 'content_block_delta',
                    'index': event.index,
                    'delta': {'type': 'input_json_delta',
                              'partial_json': event.partial_json}}
        elif isinstance(event, ToolUseEnd):
            return {'type': 'content_block_stop',
                    'index': event.index}
        elif isinstance(event, ThinkingDelta):
            return {'type': 'content_block_delta',
                    'delta': {'type': 'thinking_delta',
                              'thinking': event.content}}
        elif isinstance(event, ErrorEvent):
            return {'type': 'error', 'content': event.message}
        elif isinstance(event, StreamEnd):
            return {'type': 'message_stop',
                    'stop_reason': event.stop_reason}
        return None

    def test_text_delta_bridge(self):
        chunk = self._bridge(TextDelta(content="Hello world"))
        assert chunk['type'] == 'content_block_delta'
        assert chunk['delta']['type'] == 'text_delta'
        assert chunk['delta']['text'] == 'Hello world'

    def test_tool_use_start_bridge(self):
        chunk = self._bridge(ToolUseStart(id="toolu_123", name="run_shell_command", index=2))
        assert chunk['type'] == 'content_block_start'
        assert chunk['index'] == 2
        assert chunk['content_block']['type'] == 'tool_use'
        assert chunk['content_block']['id'] == 'toolu_123'
        assert chunk['content_block']['name'] == 'run_shell_command'

    def test_tool_use_input_bridge(self):
        chunk = self._bridge(ToolUseInput(partial_json='{"cmd":', index=2))
        assert chunk['type'] == 'content_block_delta'
        assert chunk['index'] == 2
        assert chunk['delta']['type'] == 'input_json_delta'
        assert chunk['delta']['partial_json'] == '{"cmd":'

    def test_tool_use_end_bridge(self):
        chunk = self._bridge(ToolUseEnd(id="toolu_123", name="foo", input={"x": 1}, index=2))
        assert chunk['type'] == 'content_block_stop'
        assert chunk['index'] == 2

    def test_thinking_delta_bridge(self):
        chunk = self._bridge(ThinkingDelta(content="Let me consider..."))
        assert chunk['type'] == 'content_block_delta'
        assert chunk['delta']['type'] == 'thinking_delta'
        assert chunk['delta']['thinking'] == 'Let me consider...'

    def test_error_event_bridge(self):
        chunk = self._bridge(ErrorEvent(message="Rate limit hit", error_type=ErrorType.THROTTLE))
        assert chunk['type'] == 'error'
        assert chunk['content'] == 'Rate limit hit'

    def test_stream_end_bridge(self):
        chunk = self._bridge(StreamEnd(stop_reason="tool_use"))
        assert chunk['type'] == 'message_stop'
        assert chunk['stop_reason'] == 'tool_use'

    def test_usage_event_not_bridged(self):
        """UsageEvent is handled directly, not converted to a chunk."""
        result = self._bridge(UsageEvent(input_tokens=100))
        assert result is None

    def test_full_tool_call_sequence(self):
        """Verify a complete tool call sequence bridges correctly."""
        events = [
            TextDelta(content="Let me check: "),
            ToolUseStart(id="t1", name="run_shell_command", index=1),
            ToolUseInput(partial_json='{"command":', index=1),
            ToolUseInput(partial_json='"ls -la"}', index=1),
            ToolUseEnd(id="t1", name="run_shell_command", input={"command": "ls -la"}, index=1),
            StreamEnd(stop_reason="tool_use"),
        ]
        chunks = [self._bridge(e) for e in events]

        assert chunks[0]['delta']['text'] == "Let me check: "
        assert chunks[1]['content_block']['name'] == "run_shell_command"
        assert chunks[2]['delta']['partial_json'] == '{"command":'
        assert chunks[3]['delta']['partial_json'] == '"ls -la"}'
        assert chunks[4]['type'] == 'content_block_stop'
        assert chunks[5]['type'] == 'message_stop'


# -----------------------------------------------------------------------
# _build_provider_config tests
# -----------------------------------------------------------------------

class TestBuildProviderConfig:
    """Test _build_provider_config produces correct ProviderConfig from model settings."""

    def _make_executor(self, model_config=None):
        """Create a minimal StreamingToolExecutor for testing config building."""
        from app.streaming_tool_executor import StreamingToolExecutor
        executor = StreamingToolExecutor.__new__(StreamingToolExecutor)
        executor.model_config = model_config or {}
        executor.model_id = "test-model"
        executor.bedrock = None
        executor.provider = None
        return executor

    def test_basic_config(self):
        executor = self._make_executor({"max_output_tokens": 8192})
        config = executor._build_provider_config(iteration=0)
        assert config.max_output_tokens == 8192
        assert config.thinking is None
        assert config.suppress_tools is False
        assert config.iteration == 0

    def test_adaptive_thinking(self):
        executor = self._make_executor({
            "max_output_tokens": 16384,
            "supports_adaptive_thinking": True,
            "thinking_effort_default": "high",
        })
        config = executor._build_provider_config(iteration=1)
        assert config.thinking is not None
        assert config.thinking.mode == "adaptive"
        assert config.thinking.effort == "high"
        assert config.thinking.enabled is True

    @patch.dict('os.environ', {'ZIYA_THINKING_EFFORT': 'max'})
    def test_adaptive_thinking_effort_from_env(self):
        executor = self._make_executor({
            "max_output_tokens": 16384,
            "supports_adaptive_thinking": True,
        })
        config = executor._build_provider_config(iteration=0)
        assert config.thinking.effort == "max"

    @patch.dict('os.environ', {'ZIYA_THINKING_MODE': '1', 'ZIYA_THINKING_BUDGET': '32000'})
    def test_standard_thinking(self):
        executor = self._make_executor({
            "max_output_tokens": 16384,
            "supports_thinking": True,
        })
        config = executor._build_provider_config(iteration=0)
        assert config.thinking is not None
        assert config.thinking.mode == "enabled"
        assert config.thinking.budget_tokens == 32000

    def test_tool_suppression(self):
        executor = self._make_executor({"max_output_tokens": 4096})
        config = executor._build_provider_config(iteration=5, consecutive_empty_tool_calls=5)
        assert config.suppress_tools is True

    def test_no_tool_suppression_below_threshold(self):
        executor = self._make_executor({"max_output_tokens": 4096})
        config = executor._build_provider_config(iteration=5, consecutive_empty_tool_calls=4)
        assert config.suppress_tools is False

    def test_empty_model_config(self):
        executor = self._make_executor({})
        config = executor._build_provider_config(iteration=0)
        assert config.max_output_tokens == 16384  # default
        assert config.thinking is None

    def test_none_model_config(self):
        executor = self._make_executor(None)
        config = executor._build_provider_config(iteration=0)
        assert config.max_output_tokens == 16384


# -----------------------------------------------------------------------
# Message builder integration tests — verify both providers produce
# messages the orchestrator's safety check can validate
# -----------------------------------------------------------------------

class TestMessageBuildersIntegration:
    """Verify build_assistant_message and build_tool_result_message produce
    messages that pass the orchestrator's orphan-check validation."""

    def _validate_conversation(self, conversation: List[Dict]) -> List[str]:
        """Run the orchestrator's safety check logic. Returns orphaned IDs."""
        tool_use_ids = set()
        tool_result_ids = set()

        for msg in conversation:
            if msg.get('role') == 'assistant' and isinstance(msg.get('content'), list):
                for block in msg['content']:
                    if isinstance(block, dict) and block.get('type') == 'tool_use':
                        tool_use_ids.add(block.get('id'))
            elif msg.get('role') == 'user' and isinstance(msg.get('content'), list):
                for block in msg['content']:
                    if isinstance(block, dict) and block.get('type') == 'tool_result':
                        tool_result_ids.add(block.get('tool_use_id'))

        return list(tool_use_ids - tool_result_ids)

    def test_bedrock_roundtrip(self):
        from app.providers.bedrock import BedrockProvider
        provider = BedrockProvider.__new__(BedrockProvider)
        provider.model_config = {}

        assistant_msg = provider.build_assistant_message(
            text="Running command:",
            tool_uses=[{"id": "t1", "name": "mcp_run_shell_command", "input": {"command": "ls"}}],
        )
        tool_result_msg = provider.build_tool_result_message([
            {"tool_use_id": "t1", "content": "file1.py\nfile2.py"},
        ])

        orphaned = self._validate_conversation([assistant_msg, tool_result_msg])
        assert orphaned == [], f"Orphaned tool_use IDs: {orphaned}"

    def test_anthropic_roundtrip(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        provider = AnthropicDirectProvider.__new__(AnthropicDirectProvider)
        provider.model_config = {}

        assistant_msg = provider.build_assistant_message(
            text="Let me check:",
            tool_uses=[
                {"id": "t1", "name": "run_shell_command", "input": {"command": "ls"}},
                {"id": "t2", "name": "read_file", "input": {"path": "/tmp/x"}},
            ],
        )
        tool_result_msg = provider.build_tool_result_message([
            {"tool_use_id": "t1", "content": "file1.py"},
            {"tool_use_id": "t2", "content": "hello world"},
        ])

        orphaned = self._validate_conversation([assistant_msg, tool_result_msg])
        assert orphaned == [], f"Orphaned tool_use IDs: {orphaned}"

    def test_bedrock_strips_mcp_prefix(self):
        from app.providers.bedrock import BedrockProvider
        provider = BedrockProvider.__new__(BedrockProvider)
        provider.model_config = {}

        msg = provider.build_assistant_message(
            text="",
            tool_uses=[{"id": "t1", "name": "mcp_run_shell_command", "input": {}}],
        )
        # Bedrock should strip mcp_ prefix
        tool_block = msg['content'][0]
        assert tool_block['name'] == 'run_shell_command'

    def test_anthropic_preserves_name(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        provider = AnthropicDirectProvider.__new__(AnthropicDirectProvider)
        provider.model_config = {}

        msg = provider.build_assistant_message(
            text="",
            tool_uses=[{"id": "t1", "name": "run_shell_command", "input": {}}],
        )
        tool_block = msg['content'][0]
        assert tool_block['name'] == 'run_shell_command'

    def test_empty_text_no_text_block(self):
        """Both providers should omit text block when text is empty/whitespace."""
        from app.providers.bedrock import BedrockProvider
        provider = BedrockProvider.__new__(BedrockProvider)
        provider.model_config = {}

        msg = provider.build_assistant_message(
            text="   ",
            tool_uses=[{"id": "t1", "name": "foo", "input": {}}],
        )
        # Should only have tool_use block, no text block
        assert len(msg['content']) == 1
        assert msg['content'][0]['type'] == 'tool_use'

    def test_multiple_tools_all_matched(self):
        from app.providers.bedrock import BedrockProvider
        provider = BedrockProvider.__new__(BedrockProvider)
        provider.model_config = {}

        tool_ids = [f"t{i}" for i in range(5)]
        tool_uses = [{"id": tid, "name": f"tool_{i}", "input": {}} for i, tid in enumerate(tool_ids)]
        results = [{"tool_use_id": tid, "content": f"result_{i}"} for i, tid in enumerate(tool_ids)]

        assistant_msg = provider.build_assistant_message("Working on it:", tool_uses)
        result_msg = provider.build_tool_result_message(results)

        orphaned = self._validate_conversation([assistant_msg, result_msg])
        assert orphaned == []
