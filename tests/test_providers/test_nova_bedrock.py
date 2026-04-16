"""Tests for NovaBedrockProvider — Converse API format correctness."""

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.providers.base import ProviderConfig


@pytest.fixture
def nova_model_config():
    return {
        "family": "nova",
        "wrapper_class": "NovaBedrock",
        "message_format": "nova",
        "max_output_tokens": 5000,
        "supports_thinking": False,
        "supports_assistant_prefill": False,
    }


@pytest.fixture
def nova_provider(nova_model_config):
    """Create a NovaBedrockProvider with mocked client."""
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as mock_get:
        mock_client = MagicMock()
        mock_get.return_value = mock_client
        from app.providers.nova_bedrock import NovaBedrockProvider
        provider = NovaBedrockProvider(
            model_id="us.amazon.nova-micro-v1:0",
            model_config=nova_model_config,
            aws_profile="test",
            region="us-east-1",
        )
        return provider


class TestRequestBodyFormat:
    """Verify the Converse API request body is built correctly."""

    def test_no_anthropic_version_in_params(self, nova_provider):
        """Nova must not include anthropic_version."""
        config = ProviderConfig(max_output_tokens=4096, temperature=0.7)
        params = nova_provider._build_converse_params(
            messages=[{"role": "user", "content": "hello"}],
            system_content="You are helpful.",
            tools=[], config=config,
        )
        assert "anthropic_version" not in params
        assert "max_tokens" not in params

    def test_inference_config_format(self, nova_provider):
        """maxTokens must be inside inferenceConfig, not at top level."""
        config = ProviderConfig(max_output_tokens=4096, temperature=0.5)
        params = nova_provider._build_converse_params(
            messages=[{"role": "user", "content": "test"}],
            system_content=None, tools=[], config=config,
        )
        assert "inferenceConfig" in params
        assert params["inferenceConfig"]["maxTokens"] == 4096
        assert params["inferenceConfig"]["temperature"] == 0.5

    def test_max_tokens_capped_to_model_limit(self, nova_provider):
        """maxTokens must be capped to model's max_output_tokens.

        When switching from a Claude model (which may set ZIYA_MAX_OUTPUT_TOKENS=36000)
        to a Nova model (limit 5000-10000), the provider must cap the value to avoid
        ValidationException from the Converse API.
        """
        # Simulate STE passing a Claude-scale value (36000) to a Nova model with 5000 limit
        config = ProviderConfig(max_output_tokens=36000, temperature=0.7)
        params = nova_provider._build_converse_params(
            messages=[{"role": "user", "content": "test"}],
            system_content=None, tools=[], config=config,
        )
        # Should be capped to model_config's max_output_tokens (5000)
        assert params["inferenceConfig"]["maxTokens"] == 5000

    def test_messages_content_is_array(self, nova_provider):
        """Message content must be arrays of content blocks, not strings."""
        config = ProviderConfig(max_output_tokens=4096)
        params = nova_provider._build_converse_params(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
            system_content=None, tools=[], config=config,
        )
        for msg in params["messages"]:
            assert isinstance(msg["content"], list), (
                f"Message content should be list, got {type(msg['content'])}"
            )
            assert all(isinstance(b, dict) for b in msg["content"])

    def test_system_prompt_is_array(self, nova_provider):
        """System prompt must be an array of text blocks."""
        config = ProviderConfig(max_output_tokens=4096)
        params = nova_provider._build_converse_params(
            messages=[{"role": "user", "content": "test"}],
            system_content="You are a helpful assistant.",
            tools=[], config=config,
        )
        assert isinstance(params["system"], list)
        assert params["system"][0] == {"text": "You are a helpful assistant."}

    def test_tools_in_converse_format(self, nova_provider):
        """Tools must use toolSpec format, not Anthropic format."""
        config = ProviderConfig(max_output_tokens=4096)
        tools = [
            {
                "name": "get_time",
                "description": "Get current time",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
        params = nova_provider._build_converse_params(
            messages=[{"role": "user", "content": "what time?"}],
            system_content=None, tools=tools, config=config,
        )
        assert "toolConfig" in params
        tool_list = params["toolConfig"]["tools"]
        assert len(tool_list) == 1
        assert "toolSpec" in tool_list[0]
        spec = tool_list[0]["toolSpec"]
        assert spec["name"] == "get_time"
        assert "inputSchema" in spec
        assert "json" in spec["inputSchema"]


class TestContentBlockNormalization:
    """Verify Anthropic-format content blocks convert to Converse format."""

    def test_anthropic_text_block(self, nova_provider):
        blocks = [{"type": "text", "text": "hello"}]
        result = nova_provider._normalize_content_blocks(blocks)
        assert result == [{"text": "hello"}]

    def test_anthropic_tool_use_block(self, nova_provider):
        blocks = [{"type": "tool_use", "id": "t1", "name": "foo", "input": {"x": 1}}]
        result = nova_provider._normalize_content_blocks(blocks)
        assert result == [{"toolUse": {"toolUseId": "t1", "name": "foo", "input": {"x": 1}}}]

    def test_anthropic_tool_result_block(self, nova_provider):
        blocks = [{"type": "tool_result", "tool_use_id": "t1", "content": "done"}]
        result = nova_provider._normalize_content_blocks(blocks)
        assert result == [{"toolResult": {"toolUseId": "t1", "content": [{"text": "done"}]}}]

    def test_already_converse_format(self, nova_provider):
        """Blocks already in Converse format should pass through."""
        blocks = [{"text": "already formatted"}]
        result = nova_provider._normalize_content_blocks(blocks)
        assert result == [{"text": "already formatted"}]

    def test_empty_blocks_get_placeholder(self, nova_provider):
        """Empty content blocks should get a space placeholder."""
        result = nova_provider._normalize_content_blocks([])
        assert result == [{"text": " "}]


class TestAssistantMessageFormat:
    """Verify assistant messages use Converse format."""

    def test_text_only(self, nova_provider):
        msg = nova_provider.build_assistant_message("Hello!", [])
        assert msg == {"role": "assistant", "content": [{"text": "Hello!"}]}

    def test_with_tool_use(self, nova_provider):
        msg = nova_provider.build_assistant_message("Let me check.", [
            {"id": "t1", "name": "mcp_get_time", "input": {}}
        ])
        assert msg["role"] == "assistant"
        assert len(msg["content"]) == 2
        assert msg["content"][0] == {"text": "Let me check."}
        tool_block = msg["content"][1]
        assert "toolUse" in tool_block
        assert tool_block["toolUse"]["name"] == "get_time"  # mcp_ prefix stripped

    def test_tool_result_message(self, nova_provider):
        msg = nova_provider.build_tool_result_message([
            {"tool_use_id": "t1", "content": "14:00 UTC"}
        ])
        assert msg["role"] == "user"
        assert "toolResult" in msg["content"][0]
        tr = msg["content"][0]["toolResult"]
        assert tr["toolUseId"] == "t1"
        assert tr["content"] == [{"text": "14:00 UTC"}]


class TestFactoryRouting:
    """Verify the factory routes models to correct providers.

    Claude → BedrockProvider (Anthropic invoke_model API)
    Everything else → NovaBedrockProvider (Converse API)
    """

    @patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client")
    def test_nova_micro_routes_to_converse_provider(self, mock_client):
        mock_client.return_value = MagicMock()
        from app.providers.factory import create_provider
        provider = create_provider(
            endpoint="bedrock",
            model_id="us.amazon.nova-micro-v1:0",
            model_config={"family": "nova", "wrapper_class": "NovaBedrock"},
            aws_profile="test", region="us-east-1",
        )
        from app.providers.nova_bedrock import NovaBedrockProvider
        assert isinstance(provider, NovaBedrockProvider)

    @patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client")
    def test_deepseek_routes_to_converse_provider(self, mock_client):
        mock_client.return_value = MagicMock()
        from app.providers.factory import create_provider
        provider = create_provider(
            endpoint="bedrock",
            model_id="deepseek.v3-v1:0",
            model_config={"family": "deepseek", "wrapper_class": "OpenAIBedrock"},
            aws_profile="test", region="us-west-2",
        )
        from app.providers.nova_bedrock import NovaBedrockProvider
        assert isinstance(provider, NovaBedrockProvider)

    @patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client")
    def test_qwen_routes_to_converse_provider(self, mock_client):
        mock_client.return_value = MagicMock()
        from app.providers.factory import create_provider
        provider = create_provider(
            endpoint="bedrock",
            model_id="qwen.qwen3-coder-480b-a35b-v1:0",
            model_config={"family": "oss_openai_gpt"},
            aws_profile="test", region="us-west-2",
        )
        from app.providers.nova_bedrock import NovaBedrockProvider
        assert isinstance(provider, NovaBedrockProvider)

    @patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client")
    def test_openai_gpt_routes_to_converse_provider(self, mock_client):
        mock_client.return_value = MagicMock()
        from app.providers.factory import create_provider
        provider = create_provider(
            endpoint="bedrock",
            model_id="openai.gpt-oss-120b-1:0",
            model_config={"family": "oss_openai_gpt", "wrapper_class": "OpenAIBedrock"},
            aws_profile="test", region="us-west-2",
        )
        from app.providers.nova_bedrock import NovaBedrockProvider
        assert isinstance(provider, NovaBedrockProvider)

    @patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client")
    def test_claude_still_routes_to_bedrock_provider(self, mock_client):
        mock_client.return_value = MagicMock()
        from app.providers.factory import create_provider
        provider = create_provider(
            endpoint="bedrock",
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            model_config={"family": "claude"},
            aws_profile="test", region="us-east-1",
        )
        from app.providers.bedrock import BedrockProvider
        assert isinstance(provider, BedrockProvider)


class TestProviderFeatures:
    """Verify feature support flags."""

    def test_no_cache_control(self, nova_provider):
        assert not nova_provider.supports_feature("cache_control")

    def test_no_extended_context(self, nova_provider):
        assert not nova_provider.supports_feature("extended_context")

    def test_no_adaptive_thinking(self, nova_provider):
        assert not nova_provider.supports_feature("adaptive_thinking")

    def test_provider_name(self, nova_provider):
        assert nova_provider.provider_name == "nova_bedrock"
