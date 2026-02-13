"""
Capability tests for all models.
Tests each model's declared capabilities: thinking, vision, streaming, context handling.
Makes real API calls - will incur costs.
"""
import os
import asyncio
import time
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

import app.config.models_config as config
from app.agents.models import ModelManager


def get_all_models():
    models = []
    for endpoint, endpoint_models in config.MODEL_CONFIGS.items():
        for model_name in endpoint_models.keys():
            models.append((endpoint, model_name))
    return models


def init_model(endpoint, model):
    for var in ["ZIYA_MAX_OUTPUT_TOKENS", "ZIYA_MAX_TOKENS", "AWS_REGION"]:
        os.environ.pop(var, None)
    os.environ["ZIYA_ENDPOINT"] = endpoint
    os.environ["ZIYA_MODEL"] = model
    if endpoint == "bedrock":
        os.environ["ZIYA_AWS_PROFILE"] = "ziya"
        os.environ["AWS_PROFILE"] = "ziya"
    ModelManager._reset_state()
    llm = ModelManager.initialize_model(force_reinit=True)
    model_config = ModelManager.get_model_config(endpoint, model)
    return llm, model_config


def invoke_model(llm, messages):
    """Invoke model using the appropriate interface."""
    if hasattr(llm, 'astream') and not hasattr(llm, 'invoke'):
        # Google DirectGoogleModel - async streaming only
        async def _call():
            parts = []
            async for chunk in llm.astream(messages):
                if isinstance(chunk, dict) and chunk.get("type") == "text":
                    parts.append(chunk.get("content", ""))
                elif hasattr(chunk, 'content'):
                    parts.append(str(chunk.content))
            return "".join(parts)
        return asyncio.run(_call())
    else:
        response = llm.invoke(messages)
        return response.content if hasattr(response, 'content') else str(response)


def has_capability(model_config, cap):
    """Check if model has a capability. Model-level config overrides family."""
    # Model-level explicit False takes precedence
    if cap in model_config and model_config[cap] is False:
        return False
    if model_config.get(cap):
        return True
    family = model_config.get("family", "")
    fam_cfg = config.MODEL_FAMILIES.get(family, {})
    if fam_cfg.get(cap):
        return True
    parent = fam_cfg.get("parent", "")
    if parent and config.MODEL_FAMILIES.get(parent, {}).get(cap):
        return True
    return False


# --- Thinking mode tests ---

def get_thinking_models():
    models = []
    for ep, ep_models in config.MODEL_CONFIGS.items():
        for name, cfg in ep_models.items():
            full_cfg = ModelManager.get_model_config(ep, name)
            if has_capability(full_cfg, 'supports_thinking') or full_cfg.get('thinking_level'):
                models.append((ep, name))
    return models


@pytest.mark.real_api
@pytest.mark.parametrize("endpoint,model", get_thinking_models())
def test_thinking_mode(endpoint, model):
    """Test that thinking-capable models can reason through a multi-step problem."""
    llm, model_config = init_model(endpoint, model)

    prompt = "A farmer has 17 sheep. All but 9 run away. How many sheep does the farmer have left? Just give the number."
    content = invoke_model(llm, [HumanMessage(content=prompt)])

    assert content is not None and len(content) > 0
    assert "9" in content, f"Expected '9' in response from {endpoint}/{model}, got: {content[:200]}"
    print(f"✅ {endpoint}/{model} thinking - {content.strip()[:100]}")


# --- Vision tests ---

# 10x10 red PNG, properly encoded
TINY_RED_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAIAAAACUFjqAAAAEklEQVR4nGP4z8CAB+GTG8HSALfKY52fTcuYAAAAAElFTkSuQmCC"


def get_vision_models():
    models = []
    for ep, ep_models in config.MODEL_CONFIGS.items():
        for name, cfg in ep_models.items():
            full_cfg = ModelManager.get_model_config(ep, name)
            if has_capability(full_cfg, 'supports_vision'):
                models.append((ep, name))
    return models


@pytest.mark.real_api
@pytest.mark.parametrize("endpoint,model", get_vision_models())
def test_vision(endpoint, model):
    """Test that vision-capable models can accept an image input."""
    llm, model_config = init_model(endpoint, model)

    # Standard LangChain multimodal format works for both Bedrock and Google
    message = HumanMessage(content=[
        {"type": "text", "text": "What color is this solid-colored square image? Answer with just the color name."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{TINY_RED_PNG}"}},
    ])

    content = invoke_model(llm, [message])
    assert content is not None and len(content) > 0
    # Verify the model processed the image — it should respond with a color name.
    # We don't assert the exact color since tiny PNGs can be interpreted differently.
    color_words = ["red", "green", "blue", "black", "white", "cyan", "lime", "orange",
                   "yellow", "purple", "pink", "gray", "grey", "brown", "magenta"]
    response_lower = content.lower()
    assert any(c in response_lower for c in color_words), \
        f"Expected a color name in response from {endpoint}/{model}, got: {content[:200]}"
    print(f"✅ {endpoint}/{model} vision - {content.strip()[:100]}")


# --- Streaming tests ---

def get_streaming_bedrock_models():
    """Get bedrock models for streaming test."""
    models = []
    for name, cfg in config.MODEL_CONFIGS.get("bedrock", {}).items():
        models.append(("bedrock", name))
    return models


@pytest.mark.real_api
@pytest.mark.parametrize("endpoint,model", get_streaming_bedrock_models())
def test_streaming(endpoint, model):
    """Test that models can stream responses."""
    llm, model_config = init_model(endpoint, model)

    prompt = [HumanMessage(content="Count from 1 to 5, one number per line.")]
    chunks = []

    if hasattr(llm, 'stream'):
        for chunk in llm.stream(prompt):
            if hasattr(chunk, 'content') and chunk.content:
                chunks.append(chunk.content)

    full_response = "".join(chunks)
    assert len(chunks) >= 1, f"Expected at least 1 chunk from {endpoint}/{model}, got 0"
    assert "3" in full_response, f"Expected '3' in streaming response from {endpoint}/{model}"
    print(f"✅ {endpoint}/{model} streaming - {len(chunks)} chunks")


# --- Context window tests ---

def get_large_context_models():
    """Models with token_limit >= 200000."""
    models = []
    for ep, ep_models in config.MODEL_CONFIGS.items():
        for name, cfg in ep_models.items():
            full_cfg = ModelManager.get_model_config(ep, name)
            limit = full_cfg.get("token_limit", 0)
            if limit >= 200000:
                models.append((ep, name))
    return models


@pytest.mark.real_api
@pytest.mark.parametrize("endpoint,model", get_large_context_models())
def test_large_context(endpoint, model):
    """Test that large-context models can handle substantial input."""
    llm, model_config = init_model(endpoint, model)

    # ~10k tokens of padding + a hidden fact
    padding = "The quick brown fox jumps over the lazy dog. " * 500
    hidden = "The secret code is ZIYA42."
    prompt = f"Read the following text carefully and find the secret code.\n\n{padding}\n{hidden}\n{padding}\n\nWhat is the secret code? Answer with just the code."

    content = invoke_model(llm, [HumanMessage(content=prompt)])
    assert content is not None and len(content) > 0
    assert "ZIYA42" in content, f"Expected 'ZIYA42' in response from {endpoint}/{model}, got: {content[:200]}"
    print(f"✅ {endpoint}/{model} large context - found hidden code")
