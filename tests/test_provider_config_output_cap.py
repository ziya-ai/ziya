"""The per-request output ceiling never exceeds the resolved model's cap.

2026-10-11: a task block with ``scope.model_tier = "small"`` routed to Haiku
4.5 (max_output_tokens 64000) while the GUI's model settings had written
``ZIYA_MAX_OUTPUT_TOKENS=70001`` for the top-level conversation's model.
``_build_provider_config`` let the process-global env var win over the
overridden model's own limit, and Bedrock refused the request outright::

    ValidationException: max_tokens: 70001 > 64000, which is the maximum
    allowed number of output tokens for anthropic.claude-haiku-4-5-20251001-v1:0

The env var expresses the user's preferred ceiling for *their* model; it is
not a licence to exceed a different model's hard limit.  Clamp to
``model_config["max_output_tokens"]`` whenever the config declares one.
"""
import pytest


def _executor_for(model_config):
    from app.streaming_tool_executor import StreamingToolExecutor
    ex = object.__new__(StreamingToolExecutor)   # skip the boto3/provider setup
    ex.model_config = model_config
    return ex


HAIKU = {"model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
         "max_output_tokens": 64000}


def test_env_ceiling_is_clamped_to_the_resolved_models_cap(monkeypatch):
    monkeypatch.setenv("ZIYA_MAX_OUTPUT_TOKENS", "70001")
    cfg = _executor_for(HAIKU)._build_provider_config(iteration=0)
    assert cfg.max_output_tokens == 64000


def test_env_ceiling_below_the_cap_is_honoured(monkeypatch):
    monkeypatch.setenv("ZIYA_MAX_OUTPUT_TOKENS", "8000")
    cfg = _executor_for(HAIKU)._build_provider_config(iteration=0)
    assert cfg.max_output_tokens == 8000


def test_skill_override_is_clamped_too(monkeypatch):
    monkeypatch.delenv("ZIYA_MAX_OUTPUT_TOKENS", raising=False)
    ex = _executor_for(HAIKU)
    ex.max_tokens_override = 100000
    assert ex._build_provider_config(iteration=0).max_output_tokens == 64000


def test_no_declared_cap_leaves_env_value_alone(monkeypatch):
    monkeypatch.setenv("ZIYA_MAX_OUTPUT_TOKENS", "70001")
    cfg = _executor_for({"model_id": "x"})._build_provider_config(iteration=0)
    assert cfg.max_output_tokens == 70001
