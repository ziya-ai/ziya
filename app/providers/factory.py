"""
Provider factory — creates the appropriate LLMProvider based on endpoint config.

Usage from StreamingToolExecutor:
    from app.providers.factory import create_provider
    provider = create_provider(endpoint="anthropic", model_config=config, ...)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from app.providers.base import LLMProvider
from app.utils.logging_utils import logger


def create_provider(
    endpoint: str,
    model_id: str,
    model_config: Optional[Dict[str, Any]] = None,
    *,
    aws_profile: Optional[str] = None,
    region: Optional[str] = None,
    api_key: Optional[str] = None,
) -> LLMProvider:
    """Create an LLMProvider for the given endpoint.

    Parameters
    ----------
    endpoint
        One of: ``bedrock``, ``anthropic``, ``openai``, ``google``,
        ``openrouter`` (future).
    model_id
        The model identifier (e.g. ``anthropic.claude-sonnet-4-20250514-v1:0``
        for Bedrock, ``claude-sonnet-4-20250514`` for Anthropic direct).
    model_config
        Model configuration dict from ModelManager (capabilities, limits, etc.).
    aws_profile
        AWS profile name (Bedrock only).
    region
        AWS region (Bedrock only).
    api_key
        API key (Anthropic direct, OpenAI, OpenRouter).
    """
    model_config = model_config or {}

    if endpoint == "bedrock":
        from app.providers.bedrock import BedrockProvider

        return BedrockProvider(
            model_id=model_id,
            model_config=model_config,
            aws_profile=aws_profile or "ziya",
            region=region or "us-west-2",
        )

    if endpoint == "anthropic":
        from app.providers.anthropic_direct import AnthropicDirectProvider

        return AnthropicDirectProvider(
            model_id=model_id,
            model_config=model_config,
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
        )

    if endpoint == "openai":
        from app.providers.openai_direct import OpenAIDirectProvider

        return OpenAIDirectProvider(
            model_id=model_id,
            model_config=model_config,
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
        )

    if endpoint == "openrouter":
        from app.providers.openai_direct import OpenAIDirectProvider

        return OpenAIDirectProvider(
            model_id=model_id,
            model_config=model_config,
            api_key=api_key or os.getenv("OPENROUTER_API_KEY"),
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        )

    raise ValueError(
        f"No LLMProvider registered for endpoint '{endpoint}'. "
        f"Supported: bedrock, anthropic, openai, openrouter"
    )
