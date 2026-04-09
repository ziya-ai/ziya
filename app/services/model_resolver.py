"""
Lightweight service model resolver.

Maps (endpoint, service_category) → a callable that performs a simple
text-in / text-out completion.  Used by subsystems that need a cheap
model call (memory extraction, summarization, classification) without
coupling to a specific provider.

Resolution order for each service category:
  1. Environment variable override (ZIYA_{CATEGORY}_MODEL)
  2. Plugin-registered ServiceModelProvider config
  3. Endpoint-aware default (Nova Lite for Bedrock, Flash Lite for
     Google, GPT-4.1-mini for OpenAI, Haiku for Anthropic)
  4. Fallback: primary model (expensive, last resort)

Adding a new endpoint:
  - Add an entry to _ENDPOINT_DEFAULTS
  - Add a _create_*_client function
  - Register it in _CLIENT_FACTORIES
"""

import os
import json
from typing import Any, Callable, Dict, Optional

from app.utils.logging_utils import logger


# Default lightweight models per endpoint, per service category.
# "default" key is the fallback for any category not explicitly listed.
# Built dynamically from models_config.DEFAULT_SERVICE_MODELS so
# model IDs are centralized in one place, not duplicated here.
def _build_endpoint_defaults() -> Dict[str, Dict[str, Dict[str, Any]]]:
    try:
        from app.config.models_config import DEFAULT_SERVICE_MODELS
    except ImportError:
        DEFAULT_SERVICE_MODELS = {}

    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for endpoint, model_id in DEFAULT_SERVICE_MODELS.items():
        entry: Dict[str, Any] = {"model_id": model_id}
        if endpoint == "bedrock":
            entry["region"] = "us-east-1"
        result[endpoint] = {"default": entry}

    # Apply category-specific overrides (e.g. memory_extraction → Haiku)
    try:
        from app.config.models_config import SERVICE_MODEL_OVERRIDES
    except ImportError:
        SERVICE_MODEL_OVERRIDES = {}

    for category, ep_map in SERVICE_MODEL_OVERRIDES.items():
        for endpoint, model_id in ep_map.items():
            if endpoint not in result:
                result[endpoint] = {}
            entry = {"model_id": model_id}
            if endpoint == "bedrock":
                entry["region"] = "us-east-1"
            result[endpoint][category] = entry

    return result

_ENDPOINT_DEFAULTS = _build_endpoint_defaults()


def resolve_service_model(
    category: str = "default",
) -> Dict[str, Any]:
    """Resolve the model config for a service category.

    Returns a dict with at minimum:
      - endpoint: str (bedrock, google, openai, anthropic)
      - model_id: str
      - region: str (for bedrock)

    The caller uses this to create the appropriate client.
    """
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")

    # 1. Check explicit env var override: ZIYA_{CATEGORY}_MODEL
    env_model = os.environ.get(f"ZIYA_{category.upper()}_MODEL")
    env_region = os.environ.get(
        f"ZIYA_{category.upper()}_REGION",
        os.environ.get("AWS_REGION", "us-east-1"),
    )
    # Allow overriding the endpoint per-service too
    env_endpoint = os.environ.get(f"ZIYA_{category.upper()}_ENDPOINT", endpoint)

    if env_model:
        return {
            "endpoint": env_endpoint,
            "model_id": env_model,
            "region": env_region,
        }

    # 2. Check plugin-registered ServiceModelProvider
    try:
        from app.plugins import get_all_service_model_providers
        for provider in get_all_service_model_providers():
            config = provider.get_service_model_config()
            if category in config:
                cat_config = config[category]
                return {
                    "endpoint": cat_config.get("endpoint", endpoint),
                    "model_id": cat_config.get("model", cat_config.get("model_id", "")),
                    "region": cat_config.get("region", env_region),
                }
    except Exception:
        pass  # No plugin system or no providers

    # 3. Endpoint-aware default
    ep_defaults = _ENDPOINT_DEFAULTS.get(endpoint, _ENDPOINT_DEFAULTS["bedrock"])
    cat_defaults = ep_defaults.get(category, ep_defaults.get("default", {}))

    return {
        "endpoint": endpoint,
        "model_id": cat_defaults.get("model_id", ""),
        "region": cat_defaults.get("region", env_region),
    }


async def call_service_model(
    category: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 2048,
    temperature: float = 0.2,
) -> str:
    """Call a lightweight service model and return the text response.

    This is the main entry point for subsystems that need a cheap
    model call. Handles endpoint routing transparently.
    """
    config = resolve_service_model(category)
    ep = config["endpoint"]

    if ep == "bedrock":
        return await _call_bedrock(config, system_prompt, user_message, max_tokens, temperature)
    elif ep == "google":
        return await _call_google(config, system_prompt, user_message, max_tokens, temperature)
    elif ep in ("openai", "anthropic"):
        return await _call_openai_compatible(config, system_prompt, user_message, max_tokens, temperature)
    else:
        logger.warning(f"ServiceModelResolver: unknown endpoint '{ep}', falling back to bedrock")
        return await _call_bedrock(config, system_prompt, user_message, max_tokens, temperature)


async def _call_bedrock(config, system_prompt, user_message, max_tokens, temperature) -> str:
    """Call via Bedrock Converse API."""
    import boto3
    from botocore.config import Config as BotoConfig

    profile = os.environ.get("ZIYA_AWS_PROFILE") or os.environ.get("AWS_PROFILE", "default")
    session = boto3.Session(profile_name=profile)
    client = session.client(
        "bedrock-runtime",
        region_name=config.get("region", "us-east-1"),
        config=BotoConfig(read_timeout=30, retries={"max_attempts": 2, "mode": "adaptive"}),
    )
    response = client.converse(
        modelId=config["model_id"],
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_message}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return _extract_converse_text(response)


async def _call_google(config, system_prompt, user_message, max_tokens, temperature) -> str:
    """Call via Google Generative AI SDK."""
    try:
        import google.generativeai as genai
        model = genai.GenerativeModel(
            config["model_id"],
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            user_message,
            generation_config=genai.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
            ),
        )
        return response.text or ""
    except ImportError:
        logger.warning("google-generativeai not installed, falling back to bedrock")
        return await _call_bedrock(config, system_prompt, user_message, max_tokens, temperature)


async def _call_openai_compatible(config, system_prompt, user_message, max_tokens, temperature) -> str:
    """Call via OpenAI-compatible API (works for OpenAI and Anthropic direct)."""
    try:
        from openai import OpenAI
        client = OpenAI()  # Uses OPENAI_API_KEY / ANTHROPIC_API_KEY from env
        response = client.chat.completions.create(
            model=config["model_id"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
    except ImportError:
        logger.warning("openai SDK not installed, falling back to bedrock")
        return await _call_bedrock(config, system_prompt, user_message, max_tokens, temperature)


def _extract_converse_text(response: dict) -> str:
    """Extract text from a Bedrock Converse API response."""
    text = ""
    for block in response.get("output", {}).get("message", {}).get("content", []):
        if "text" in block:
            text += block["text"]
    return text
