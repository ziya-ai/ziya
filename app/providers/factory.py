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


# Single source of truth for which endpoints can be streamed. Every endpoint
# create_provider() can build belongs here; nothing else should enumerate
# this set. The web /api/chat handler and any other "can we stream this?"
# caller MUST consult is_endpoint_supported rather than re-deriving their own
# predicate list — that duplication is exactly what silently dropped new
# endpoints (fable5/mythos5 → 200 null pre-0.7.3.0; zai/openrouter → 500).
_SUPPORTED_ENDPOINTS = frozenset({
    "bedrock", "anthropic", "openai", "openrouter", "google", "zai",
})


def is_endpoint_supported(
    endpoint: str,
    model_config: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True if create_provider() can build a streaming provider for
    this endpoint.

    This is the authoritative routability check. It is intentionally a pure
    membership test against the same set create_provider dispatches on, so
    the two cannot drift: adding an endpoint to create_provider without
    adding it here (or vice-versa) is caught by
    tests/test_chat_endpoint_routing.py, which asserts the set matches the
    branches in create_provider.

    model_config is accepted (and currently unused) so a future endpoint
    whose routability depends on model capabilities — not just the endpoint
    name — can refine this without changing the call sites.
    """
    return endpoint in _SUPPORTED_ENDPOINTS


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
        # Some models override the default bedrock-runtime endpoint.
        endpoint_override = model_config.get("endpoint_override", "")
        if endpoint_override == "bedrock-mantle":
            from app.providers.bedrock_mantle import BedrockMantleProvider
            _region = region or os.environ.get(
                "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
            )
            return BedrockMantleProvider(
                model_id=model_id,
                model_config=model_config,
                region=_region,
            )

        # Claude uses the Anthropic invoke_model API (prompt caching,
        # extended context headers, region failover).
        # OpenAI-format models use invoke_model with Chat Completions body.
        # Nova and other models use the unified Converse API.
        family = model_config.get("family", "")
        is_claude = (family == "claude")

        # Models with wrapper_class "OpenAIBedrock" speak the OpenAI Chat
        # Completions wire format via invoke_model, not the Converse API.
        # Routing them through converse_stream mangles newlines.
        wrapper_class = model_config.get("wrapper_class", "")
        if wrapper_class == "OpenAIBedrock":
            from app.providers.openai_bedrock import OpenAIBedrockProvider
            return OpenAIBedrockProvider(
                model_id=model_id, model_config=model_config,
                aws_profile=aws_profile or "ziya", region=region or "us-west-2",
            )

        if not is_claude:
            from app.providers.nova_bedrock import NovaBedrockProvider
            return NovaBedrockProvider(
                model_id=model_id, model_config=model_config,
                aws_profile=aws_profile or "ziya", region=region or "us-west-2",
            )

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

    if endpoint == "google":
        from app.providers.google_direct import GoogleDirectProvider
        return GoogleDirectProvider(
            model_id=model_id,
            model_config=model_config,
            api_key=api_key or os.getenv("GOOGLE_API_KEY"),
            thinking_level=model_config.get("thinking_level"),
        )

    if endpoint == "openrouter":
        from app.providers.openai_direct import OpenAIDirectProvider
        return OpenAIDirectProvider(
            model_id=model_id,
            model_config=model_config,
            api_key=api_key or os.getenv("OPENROUTER_API_KEY"),
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        )

    if endpoint == "zai":
        # z.ai (Zhipu / GLM) exposes an OpenAI-compatible chat completions API.
        # Pay-as-you-go keys use api/paas/v4; Coding Plan subscriptions use
        # api/coding/paas/v4. Default to pay-as-you-go; override via ZAI_BASE_URL.
        from app.providers.openai_direct import OpenAIDirectProvider
        return OpenAIDirectProvider(
            model_id=model_id,
            model_config=model_config,
            api_key=api_key or os.getenv("ZAI_API_KEY") or os.getenv("ZHIPUAI_API_KEY"),
            base_url=os.getenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4"),
        )

    raise ValueError(
        f"No LLMProvider registered for endpoint '{endpoint}'. "
        f"Supported: bedrock, anthropic, openai, openrouter, google, zai"
    )
