"""
Tests for the GPT-5.5 family registration in the openai endpoint.

Verifies:
  - All four variants (gpt-5.5, gpt-5.5-pro, gpt-5.5-mini, gpt-5.5-nano) are registered
  - Vision support is enabled on all variants (GPT-5.5 is omnimodal)
  - Token limits and family wiring match other openai-gpt models
  - Older models (5.4, 5.3, 4.1) remain available (API-side is not yet deprecated)
  - OpenAIDirectProvider accepts the new model_ids without configuration errors
"""

import pytest
import sys
from unittest.mock import MagicMock, patch

from app.config.models_config import MODEL_CONFIGS, get_model_capabilities


GPT_55_VARIANTS = ["gpt-5.5", "gpt-5.5-pro", "gpt-5.5-mini", "gpt-5.5-nano"]


class TestGPT55Registration:
    def test_all_variants_registered(self):
        for name in GPT_55_VARIANTS:
            assert name in MODEL_CONFIGS["openai"], f"{name} missing from openai endpoint"

    def test_all_variants_are_openai_gpt_family(self):
        for name in GPT_55_VARIANTS:
            assert MODEL_CONFIGS["openai"][name]["family"] == "openai-gpt"

    def test_all_variants_support_vision(self):
        """GPT-5.5 is natively omnimodal (text/image/audio/video) across the family."""
        for name in GPT_55_VARIANTS:
            cfg = MODEL_CONFIGS["openai"][name]
            assert cfg.get("supports_vision") is True, (
                f"{name} should have supports_vision=True (GPT-5.5 is omnimodal)"
            )

    def test_pro_variant_supports_thinking(self):
        assert MODEL_CONFIGS["openai"]["gpt-5.5-pro"].get("supports_thinking") is True

    def test_token_limits_are_1m(self):
        """GPT-5.5 API context window is 1M tokens across the family."""
        for name in GPT_55_VARIANTS:
            cfg = MODEL_CONFIGS["openai"][name]
            assert cfg["token_limit"] == 1_000_000, (
                f"{name} token_limit {cfg['token_limit']} != 1M"
            )
            assert cfg["max_output_tokens"] == 128_000

    def test_native_function_calling(self):
        for name in GPT_55_VARIANTS:
            assert MODEL_CONFIGS["openai"][name].get("native_function_calling") is True

    def test_model_id_matches_name(self):
        for name in GPT_55_VARIANTS:
            assert MODEL_CONFIGS["openai"][name]["model_id"] == name


class TestOlderModelsNotDeprecated:
    """
    OpenAI's announced retirements (Feb 2026) apply to ChatGPT only; API access
    to 5.4, 5.3, 4.1 continues. These must stay registered and must NOT carry
    a 'deprecated' marker until the API sunset is announced.
    """

    @pytest.mark.parametrize("name", [
        "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
        "gpt-4.1",
    ])
    def test_still_registered_and_not_deprecated(self, name):
        assert name in MODEL_CONFIGS["openai"], f"{name} unexpectedly removed"
        assert "deprecated" not in MODEL_CONFIGS["openai"][name], (
            f"{name} marked deprecated — verify OpenAI has announced API sunset"
        )


class TestCapabilitiesApi:
    """Capabilities reported via the public helper match the config."""

    @pytest.mark.parametrize("name", GPT_55_VARIANTS)
    def test_capabilities_report_vision(self, name):
        caps = get_model_capabilities(endpoint="openai", model_name=name)
        assert caps.get("supports_vision") is True, (
            f"get_model_capabilities should report vision for {name}"
        )


class TestProviderAcceptsNewModels:
    """OpenAIDirectProvider instantiates cleanly for each new model."""

    @pytest.fixture
    def mock_openai_module(self):
        mock_mod = MagicMock()
        mock_client = MagicMock()
        mock_mod.AsyncOpenAI.return_value = mock_client
        with patch.dict(sys.modules, {"openai": mock_mod}):
            yield mock_mod

    @pytest.mark.parametrize("name", GPT_55_VARIANTS)
    def test_provider_instantiates(self, mock_openai_module, name):
        from app.providers.openai_direct import OpenAIDirectProvider

        cfg = MODEL_CONFIGS["openai"][name]
        p = OpenAIDirectProvider(
            model_id=cfg["model_id"],
            model_config=cfg,
            api_key="sk-test-key",
        )
        assert p is not None

    def test_vision_request_shape(self, mock_openai_module):
        """
        Build a request containing an image-bearing user message and verify the
        provider preserves the multimodal content structure (OpenAI image_url
        content parts) for a vision-capable GPT-5.5 model.
        """
        from app.providers.openai_direct import OpenAIDirectProvider
        from app.providers.base import ProviderConfig

        cfg = MODEL_CONFIGS["openai"]["gpt-5.5"]
        p = OpenAIDirectProvider(
            model_id=cfg["model_id"],
            model_config=cfg,
            api_key="sk-test-key",
        )

        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "what is in this image?"},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
            ],
        }]
        req = p._build_request(messages, None, [], ProviderConfig(max_output_tokens=256))

        assert req["model"] == "gpt-5.5"
        # The multimodal content array must survive into the request unchanged.
        user_msg = req["messages"][-1]
        assert user_msg["role"] == "user"
        assert isinstance(user_msg["content"], list)
        kinds = [part.get("type") for part in user_msg["content"]]
        assert "image_url" in kinds
        assert "text" in kinds
