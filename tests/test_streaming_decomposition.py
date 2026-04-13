"""
Tests for Phase 4: stream_with_tools decomposition.

Tests the extracted sub-methods on StreamingToolExecutor that were
previously inline in the 2,800-line stream_with_tools() method.
"""
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor():
    """Create a StreamingToolExecutor with mocked Bedrock client."""
    with patch.dict(os.environ, {
        "ZIYA_ENDPOINT": "bedrock",
        "ZIYA_MODEL": "sonnet3.7",
    }):
        with patch('app.streaming_tool_executor.StreamingToolExecutor.__init__', return_value=None):
            from app.streaming_tool_executor import StreamingToolExecutor
            executor = StreamingToolExecutor.__new__(StreamingToolExecutor)
            executor.model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
            executor.model_config = {
                "family": "claude",
                "max_output_tokens": 8192,
                "supports_assistant_prefill": True,
            }
            executor.bedrock = None
            executor.provider = None
            return executor


# ---------------------------------------------------------------------------
# _build_conversation_from_messages
# ---------------------------------------------------------------------------

class TestBuildConversationFromMessages:
    """Test the extracted conversation builder."""

    def test_basic_dict_messages(self):
        executor = _make_executor()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        conversation, system_content = executor._build_conversation_from_messages(messages)
        assert system_content == "You are helpful."
        assert len(conversation) == 2
        assert conversation[0]["role"] == "user"
        assert conversation[1]["role"] == "assistant"

    def test_langchain_message_objects(self):
        """LangChain message objects have .type and .content attributes."""
        executor = _make_executor()
        
        class FakeMsg:
            def __init__(self, type_, content):
                self.type = type_
                self.content = content
        
        messages = [
            FakeMsg("system", "System prompt"),
            FakeMsg("human", "Question"),
            FakeMsg("ai", "Answer"),
        ]
        conversation, system_content = executor._build_conversation_from_messages(messages)
        assert system_content == "System prompt"
        assert len(conversation) == 2
        assert conversation[0]["role"] == "user"
        assert conversation[1]["role"] == "assistant"

    def test_string_messages(self):
        executor = _make_executor()
        messages = ["Hello there"]
        conversation, system_content = executor._build_conversation_from_messages(messages)
        assert system_content is None
        assert len(conversation) == 1
        assert conversation[0]["role"] == "user"

    def test_multimodal_content_preserved(self):
        executor = _make_executor()
        content_blocks = [
            {"type": "text", "text": "Look at this image"},
            {"type": "image", "source": {"type": "base64", "data": "abc123"}},
        ]
        messages = [{"role": "user", "content": content_blocks}]
        conversation, _ = executor._build_conversation_from_messages(messages)
        assert isinstance(conversation[0]["content"], list)
        assert len(conversation[0]["content"]) == 2


# ---------------------------------------------------------------------------
# _should_continue_or_end_stream
# ---------------------------------------------------------------------------

class TestShouldContinueOrEnd:
    """Test the stream continuation/termination decision logic."""

    def test_tools_executed_always_continues(self):
        executor = _make_executor()
        result = executor._should_continue_or_end_stream(
            assistant_text="Some text",
            tools_executed=True,
            iteration=0,
            code_block_tracker={"in_block": False},
            continuation_happened=False,
            last_stop_reason="end_turn",
            blocked_tools_count=0,
        )
        assert result == "continue"

    def test_blocked_tools_ends(self):
        executor = _make_executor()
        result = executor._should_continue_or_end_stream(
            assistant_text="text",
            tools_executed=False,
            iteration=5,
            code_block_tracker={"in_block": False},
            continuation_happened=False,
            last_stop_reason="end_turn",
            blocked_tools_count=3,
        )
        assert result == "end"

    def test_empty_text_ends(self):
        executor = _make_executor()
        result = executor._should_continue_or_end_stream(
            assistant_text="   ",
            tools_executed=False,
            iteration=0,
            code_block_tracker={"in_block": False},
            continuation_happened=False,
            last_stop_reason="end_turn",
            blocked_tools_count=0,
        )
        assert result == "end"

    def test_incomplete_code_block_continues(self):
        executor = _make_executor()
        result = executor._should_continue_or_end_stream(
            assistant_text="```python\ndef foo():",
            tools_executed=False,
            iteration=0,
            code_block_tracker={"in_block": True, "block_type": "python"},
            continuation_happened=False,
            last_stop_reason="max_tokens",
            blocked_tools_count=0,
        )
        assert result == "continue"

    def test_continuation_just_happened_continues(self):
        executor = _make_executor()
        # The continuation check is after the short-stable-response check,
        # so use a response long enough to not trigger that guard
        result = executor._should_continue_or_end_stream(
            assistant_text="Here is a longer response that needs more content after the continuation happened but isn't done yet",
            tools_executed=False,
            iteration=1,
            code_block_tracker={"in_block": False},
            continuation_happened=True,
            last_stop_reason="end_turn",
            blocked_tools_count=0,
        )
        assert result == "continue"

    def test_short_stable_response_ends(self):
        executor = _make_executor()
        result = executor._should_continue_or_end_stream(
            assistant_text="OK",
            tools_executed=False,
            iteration=1,
            code_block_tracker={"in_block": False},
            continuation_happened=False,
            last_stop_reason="end_turn",
            blocked_tools_count=0,
        )
        assert result == "end"

    def test_long_complete_text_ends(self):
        executor = _make_executor()
        # 25+ words ending with period
        long_text = "Here is a comprehensive analysis of the code. " * 5 + "That completes the review."
        result = executor._should_continue_or_end_stream(
            assistant_text=long_text,
            tools_executed=False,
            iteration=0,
            code_block_tracker={"in_block": False},
            continuation_happened=False,
            last_stop_reason="end_turn",
            blocked_tools_count=0,
        )
        assert result == "end"

    def test_no_prefill_model_ends(self):
        executor = _make_executor()
        executor.model_config = {
            "supports_assistant_prefill": False,
            "max_output_tokens": 8192,
        }
        result = executor._should_continue_or_end_stream(
            assistant_text="Some text here.",
            tools_executed=False,
            iteration=0,
            code_block_tracker={"in_block": False},
            continuation_happened=False,
            last_stop_reason="end_turn",
            blocked_tools_count=0,
        )
        assert result == "end_no_prefill"

    def test_no_prefill_max_tokens_continues(self):
        executor = _make_executor()
        executor.model_config = {
            "supports_assistant_prefill": False,
            "max_output_tokens": 8192,
        }
        result = executor._should_continue_or_end_stream(
            assistant_text="A very long response that got cut off by max tokens mid-sentence and needs to continue with more content",
            tools_executed=False,
            iteration=0,
            code_block_tracker={"in_block": False},
            continuation_happened=False,
            last_stop_reason="max_tokens",
            blocked_tools_count=0,
        )
        assert result == "continue_no_prefill"


# ---------------------------------------------------------------------------
# _classify_and_handle_error
# ---------------------------------------------------------------------------

class TestClassifyAndHandleError:
    """Test error classification and throttle backoff logic."""

    def _base_throttle_state(self):
        return {
            'retry_count': 0,
            'max_retries': 5,
            'base_delay': 2,
            'last_cache_efficiency': 0.0,
            'cache_working': None,
            'output_tokens_reduction_factor': 1.0,
            'max_output_tokens_override': None,
        }

    def _base_delay_state(self):
        return {
            'current': 0.1, 'min': 0.1, 'max': 3.0,
            'decay_factor': 0.6, 'growth_factor': 2.5,
            'last_was_throttled': False,
        }

    def _base_provider_config(self):
        mock = MagicMock()
        mock.max_output_tokens = 8192
        return mock

    def test_auth_error_detected(self):
        executor = _make_executor()
        error = Exception("ExpiredToken: session expired")
        
        with patch('app.plugins.get_active_auth_provider') as mock_auth:
            provider = MagicMock()
            provider.is_auth_error.return_value = True
            provider.get_credential_help_message.return_value = "Please refresh credentials"
            mock_auth.return_value = provider
            
            result = executor._classify_and_handle_error(
                error, str(error), 0, [],
                self._base_throttle_state(), self._base_delay_state(),
                [], self._base_provider_config()
            )
        
        assert result['type'] == 'auth'
        assert result['should_retry'] is False

    def test_throttling_error_detected(self):
        executor = _make_executor()
        error = Exception("ThrottlingException: Too many requests")
        
        with patch('app.plugins.get_active_auth_provider') as mock_auth:
            mock_auth.return_value = MagicMock(is_auth_error=MagicMock(return_value=False))
            
            result = executor._classify_and_handle_error(
                error, str(error), 0, [],
                self._base_throttle_state(), self._base_delay_state(),
                [], self._base_provider_config()
            )
        
        assert result['type'] == 'throttling'
        assert result['error_chunk']['can_retry'] is True

    def test_read_timeout_retries_internally(self):
        executor = _make_executor()
        error = Exception("Read timed out on stream")
        
        with patch('app.plugins.get_active_auth_provider') as mock_auth:
            mock_auth.return_value = MagicMock(is_auth_error=MagicMock(return_value=False))
            
            result = executor._classify_and_handle_error(
                error, str(error), 0, [],
                self._base_throttle_state(), self._base_delay_state(),
                [], self._base_provider_config()
            )
        
        assert result['type'] == 'read_timeout'
        assert result['should_retry'] is True
        assert result['delay'] > 0

    def test_generic_error_no_retry(self):
        executor = _make_executor()
        error = Exception("Some random error")
        
        with patch('app.plugins.get_active_auth_provider') as mock_auth:
            mock_auth.return_value = MagicMock(is_auth_error=MagicMock(return_value=False))
            
            result = executor._classify_and_handle_error(
                error, str(error), 0, [],
                self._base_throttle_state(), self._base_delay_state(),
                [], self._base_provider_config()
            )
        
        assert result['type'] == 'generic'
        assert result['should_retry'] is False

    def test_token_reduction_on_throttle(self):
        executor = _make_executor()
        error = Exception("ThrottlingException: Too many tokens")
        throttle = self._base_throttle_state()
        
        with patch('app.plugins.get_active_auth_provider') as mock_auth:
            mock_auth.return_value = MagicMock(is_auth_error=MagicMock(return_value=False))
            
            result = executor._classify_and_handle_error(
                error, str(error), 0, [],
                throttle, self._base_delay_state(),
                [], self._base_provider_config()
            )
        
        # Should reduce max tokens
        assert result['reduced_max_tokens'] is not None
        assert result['reduced_max_tokens'] < 8192

    def test_broken_cache_aggressive_reduction(self):
        executor = _make_executor()
        error = Exception("ThrottlingException: Too many tokens")
        throttle = self._base_throttle_state()
        throttle['cache_working'] = False
        
        with patch('app.plugins.get_active_auth_provider') as mock_auth:
            mock_auth.return_value = MagicMock(is_auth_error=MagicMock(return_value=False))
            
            result = executor._classify_and_handle_error(
                error, str(error), 1, [],
                throttle, self._base_delay_state(),
                [], self._base_provider_config()
            )
        
        # Broken cache should reduce to 50%
        assert result['reduced_max_tokens'] <= 8192 * 0.5 + 1
