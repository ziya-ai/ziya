"""
Tests for app.providers.factory — the provider factory function.

Tests verify:
  1. Correct provider type returned for each endpoint
  2. Unsupported endpoints raise ValueError
  3. Credential and config passthrough
"""

import pytest
from unittest.mock import patch, MagicMock


class TestCreateProvider:
    """Tests for create_provider()."""

    @patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client")
    def test_bedrock_endpoint(self, mock_get_client):
        mock_get_client.return_value = MagicMock()
        from app.providers.factory import create_provider
        from app.providers.bedrock import BedrockProvider

        provider = create_provider(
            endpoint="bedrock",
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            model_config={"family": "claude"},
            aws_profile="test-profile",
            region="us-east-1",
        )
        assert isinstance(provider, BedrockProvider)
        assert provider.provider_name == "bedrock"
        assert provider.model_id == "anthropic.claude-sonnet-4-20250514-v1:0"
        assert provider._region == "us-east-1"

    def test_anthropic_endpoint(self):
        mock_client = MagicMock()
        with patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=lambda **kw: mock_client)}):
            from app.providers.factory import create_provider
            from app.providers.anthropic_direct import AnthropicDirectProvider

            provider = create_provider(
                endpoint="anthropic",
                model_id="claude-sonnet-4-20250514",
                model_config={"family": "claude"},
                api_key="sk-test-key",
            )
            assert isinstance(provider, AnthropicDirectProvider)
            assert provider.provider_name == "anthropic"
            assert provider.model_id == "claude-sonnet-4-20250514"

    def test_unsupported_endpoint(self):
        from app.providers.factory import create_provider
        with pytest.raises(ValueError, match="No LLMProvider"):
            create_provider(endpoint="unsupported_provider", model_id="gpt-4")

    @patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client")
    def test_empty_model_config_defaults(self, mock_get_client):
        mock_get_client.return_value = MagicMock()
        from app.providers.factory import create_provider

        provider = create_provider(endpoint="bedrock", model_id="test-model")
        assert provider.model_config == {}

    def test_anthropic_api_key_from_env(self):
        mock_client = MagicMock()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-env-key"}), \
             patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=lambda **kw: mock_client)}):
            from app.providers.factory import create_provider
            from app.providers.anthropic_direct import AnthropicDirectProvider

            provider = create_provider(
                endpoint="anthropic",
                model_id="claude-sonnet-4-20250514",
            )
            assert isinstance(provider, AnthropicDirectProvider)

    @patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client")
    def test_bedrock_default_profile_and_region(self, mock_get_client):
        mock_get_client.return_value = MagicMock()
        from app.providers.factory import create_provider

        provider = create_provider(endpoint="bedrock", model_id="test-model")
        assert provider._region == "us-west-2"

    def test_model_config_passthrough(self):
        """model_config dict is passed through to provider unchanged."""
        mock_client = MagicMock()
        config = {"family": "claude", "supports_thinking": True, "token_limit": 200000}
        with patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=lambda **kw: mock_client)}):
            from app.providers.factory import create_provider

            provider = create_provider(
                endpoint="anthropic",
                model_id="test-model",
                model_config=config,
                api_key="sk-test",
            )
            assert provider.model_config == config
