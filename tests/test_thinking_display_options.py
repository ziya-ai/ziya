"""Reasoning display selector: one vocabulary, honest per-backend subsets.

Pinned end to end:
  - app/utils/thinking_options.py classifies every model into a control
    group from its merged config and produces the option list the modal
    renders, using the REAL model registry so a config change that moves a
    model between groups shows up here;
  - each provider translates ThinkingConfig.display into its own wire knob
    (Anthropic display, OpenAI reasoning.summary, Nova prompt injection);
  - the streaming executor drops ThinkingDelta events at "quiet" so the raw
    chain-of-thought providers, which have no server knob, honour it too;
  - /api/model-capabilities publishes the option list, and the settings
    request model accepts thinking_display.
"""
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils import thinking_options as to
from app.agents.models import ModelManager


def _cfg(endpoint, alias):
    return ModelManager.get_model_config(endpoint, alias)


# ---------------------------------------------------------------------------
# Vocabulary + translations
# ---------------------------------------------------------------------------

def test_normalize_display_defaults_to_summary():
    assert to.normalize_display(None) == "summary"
    assert to.normalize_display("QUIET ") == "quiet"
    assert to.normalize_display("verbose") == "summary"


def test_anthropic_display_has_only_two_values():
    assert to.anthropic_display("quiet") == "omitted"
    assert to.anthropic_display("summary") == "summarized"
    # No full-text tier on this API: "full" must not leak through as-is.
    assert to.anthropic_display("full") == "summarized"


def test_openai_summary_mapping_preserves_prior_default():
    assert to.openai_reasoning_summary("quiet") is None
    assert to.openai_reasoning_summary("summary") == "auto"   # what Ziya sent before
    assert to.openai_reasoning_summary("full") == "detailed"


def test_gemini_and_suppression():
    assert to.gemini_include_thoughts("quiet") is False
    assert to.gemini_include_thoughts("summary") is True
    assert to.suppress_thinking_deltas("quiet") is True
    assert to.suppress_thinking_deltas("full") is False


# ---------------------------------------------------------------------------
# Classification against the real registry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint,alias,group,options", [
    ("bedrock", "sonnet4.6", to.GROUP_CLAUDE_ADAPTIVE, ["off", "quiet", "summary"]),
    ("bedrock", "fable5", to.GROUP_CLAUDE_ADAPTIVE, ["off", "quiet", "summary"]),
    ("bedrock", "sonnet4.5", to.GROUP_CLAUDE_BUDGET, ["off", "on"]),
    ("bedrock", "gpt-5.6-sol", to.GROUP_OPENAI_RESPONSES, ["quiet", "summary", "full"]),
    ("google", "gemini-3.1-pro", to.GROUP_GEMINI, ["quiet", "summary"]),
    ("google", "gemini-2.5-flash-lite", to.GROUP_GEMINI, ["quiet", "summary"]),
    ("bedrock", "deepseek-v3.2", to.GROUP_RAW_REASONING, ["off", "quiet", "full"]),
    ("zai", "glm-5.2", to.GROUP_RAW_REASONING, ["off", "quiet", "full"]),
    ("openai", "gpt-5.5-pro", to.GROUP_RAW_REASONING, ["off", "quiet", "full"]),
    ("bedrock", "nova-pro", to.GROUP_PROMPTED, ["off", "on"]),
])
def test_real_models_land_in_expected_group(endpoint, alias, group, options):
    desc = to.describe_thinking_options(_cfg(endpoint, alias), endpoint)
    assert desc["group"] == group
    assert desc["options"] == options
    assert set(desc["labels"]) == set(options)
    assert desc["help"]


def test_model_without_thinking_has_no_control():
    desc = to.describe_thinking_options(_cfg("bedrock", "haiku"), "bedrock")
    assert desc["group"] is None and desc["options"] == []


def test_group_specific_labels():
    r = to.describe_thinking_options(_cfg("bedrock", "gpt-5.6-sol"), "bedrock")
    assert r["labels"]["full"] == "On · detailed"
    raw = to.describe_thinking_options(_cfg("zai", "glm-5.2"), "zai")
    assert raw["labels"]["quiet"] == "On · hidden"
    prompted = to.describe_thinking_options(_cfg("bedrock", "nova-pro"), "bedrock")
    assert prompted["title"] == "Step-by-step prompting"


def test_model_override_narrows_options_and_ignores_junk():
    cfg = dict(_cfg("zai", "glm-5.2"))
    cfg[to.OVERRIDE_KEY] = ["off", "on", "bogus"]
    desc = to.describe_thinking_options(cfg, "zai")
    assert desc["options"] == ["off", "on"]
    assert desc["group"] == to.GROUP_RAW_REASONING


def test_meta_muse_is_pinned_to_on_off():
    # Meta bills for reasoning it never streams (see models_config comment),
    # so the entry pins the selector to off/on rather than promising "full".
    desc = to.describe_thinking_options(_cfg("meta", "muse-spark-1.2"), "meta")
    assert desc["options"] == ["off", "on"]


@pytest.mark.parametrize("mode,display,options,expected", [
    (False, "summary", ["off", "quiet", "summary"], "off"),
    (True, "summary", ["off", "quiet", "summary"], "summary"),
    (True, "full", ["off", "quiet", "summary"], "summary"),      # unoffered tier -> default
    (False, "summary", ["quiet", "summary", "full"], "summary"), # no off offered
    (True, "quiet", ["off", "on"], "on"),
    (False, None, ["off", "on"], "off"),
])
def test_selector_value_mirrors_frontend(mode, display, options, expected):
    assert to.selector_value(mode, display, options) == expected


# ---------------------------------------------------------------------------
# Provider seams
# ---------------------------------------------------------------------------

def test_thinking_config_carries_display():
    from app.providers.base import ThinkingConfig
    t = ThinkingConfig()
    assert t.display == "summary"


def test_bedrock_adaptive_maps_display():
    from app.providers.base import ThinkingConfig
    from app.providers.bedrock import BedrockProvider
    p = BedrockProvider.__new__(BedrockProvider)
    p.model_config = _cfg("bedrock", "sonnet4.6")
    body = {}
    p._apply_thinking(body, ThinkingConfig(enabled=True, mode="adaptive", effort="medium", display="quiet"))
    assert body["thinking"] == {"type": "adaptive", "display": "omitted"}
    body = {}
    p._apply_thinking(body, ThinkingConfig(enabled=True, mode="adaptive", effort="medium", display="summary"))
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_anthropic_direct_maps_display():
    from app.providers.base import ProviderConfig, ThinkingConfig
    from app.providers.anthropic_direct import AnthropicDirectProvider
    p = AnthropicDirectProvider.__new__(AnthropicDirectProvider)
    p.model_id = "claude-x"
    p.model_config = _cfg("bedrock", "sonnet4.6")
    cfg = ProviderConfig(thinking=ThinkingConfig(enabled=True, mode="adaptive", display="quiet"))
    with patch.object(AnthropicDirectProvider, "prepare_cache_control", lambda self, m, i: m, create=True):
        kwargs = p._build_request([{"role": "user", "content": "hi"}], None, [], cfg)
    assert kwargs["thinking"] == {"type": "adaptive", "display": "omitted"}


def test_openai_responses_maps_summary():
    from app.providers.base import ProviderConfig, ThinkingConfig
    from app.providers.openai_responses_mantle import OpenAIResponsesMantleProvider as P
    p = P.__new__(P)
    p.model_id = "openai.gpt-5.6-sol"
    p.model_config = _cfg("bedrock", "gpt-5.6-sol")
    msgs = [{"role": "user", "content": "hi"}]
    quiet = p._build_request(msgs, None, [], ProviderConfig(thinking=ThinkingConfig(enabled=True, display="quiet")))
    assert "summary" not in quiet["reasoning"]
    full = p._build_request(msgs, None, [], ProviderConfig(thinking=ThinkingConfig(enabled=True, display="full")))
    assert full["reasoning"]["summary"] == "detailed"
    summ = p._build_request(msgs, None, [], ProviderConfig(thinking=ThinkingConfig(enabled=True, display="summary")))
    assert summ["reasoning"]["summary"] == "auto"


def test_nova_prompt_injection_only_when_enabled():
    from app.providers.base import ProviderConfig, ThinkingConfig
    from app.providers.nova_bedrock import NovaBedrockProvider as P
    p = P.__new__(P)
    p.model_id = "amazon.nova-pro-v1:0"
    p.model_config = _cfg("bedrock", "nova-pro")
    msgs = [{"role": "user", "content": "hi"}]
    on = p._build_converse_params(msgs, "SYS", [], ProviderConfig(thinking=ThinkingConfig(enabled=True, mode="enabled")))
    assert on["system"][0]["text"].startswith("SYS")
    assert to.STEP_BY_STEP_INSTRUCTION.strip() in on["system"][0]["text"]
    off = p._build_converse_params(msgs, "SYS", [], ProviderConfig(thinking=None))
    assert off["system"] == [{"text": "SYS"}]
    # No system prompt at all: the instruction becomes the system prompt.
    bare = p._build_converse_params(msgs, None, [], ProviderConfig(thinking=ThinkingConfig(enabled=True, mode="enabled")))
    assert bare["system"][0]["text"].strip() == to.STEP_BY_STEP_INSTRUCTION.strip()


# ---------------------------------------------------------------------------
# Executor: env -> ThinkingConfig, and the quiet drop
# ---------------------------------------------------------------------------

def _executor_with(model_config):
    from app.streaming_tool_executor import StreamingToolExecutor
    ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    ex.model_config = model_config
    return ex


def _env(**kv):
    clear = ["ZIYA_THINKING_MODE", "ZIYA_THINKING_DISPLAY", "ZIYA_THINKING_EFFORT",
             "ZIYA_THINKING_BUDGET", "ZIYA_MAX_OUTPUT_TOKENS"]
    env = {k: v for k, v in os.environ.items() if k not in clear}
    env.update(kv)
    return patch.dict(os.environ, env, clear=True)


def test_adaptive_off_only_when_explicit():
    ex = _executor_with(_cfg("bedrock", "sonnet4.6"))
    with _env():
        cfg = ex._build_provider_config(0)
    assert cfg.thinking is not None and cfg.thinking.mode == "adaptive"
    assert cfg.thinking.display == "summary"
    with _env(ZIYA_THINKING_MODE="1", ZIYA_THINKING_DISPLAY="quiet"):
        cfg = ex._build_provider_config(0)
    assert cfg.thinking.display == "quiet"
    with _env(ZIYA_THINKING_MODE="0"):
        cfg = ex._build_provider_config(0)
    assert cfg.thinking is None, "explicit Off on an adaptive model must omit the thinking block"


def test_gemini_gets_display_without_thinking_mode():
    ex = _executor_with(_cfg("google", "gemini-3.1-pro"))
    with _env(ZIYA_THINKING_DISPLAY="quiet"):
        cfg = ex._build_provider_config(0)
    assert cfg.thinking is not None and cfg.thinking.display == "quiet"


def test_raw_reasoning_display_threaded():
    ex = _executor_with(_cfg("zai", "glm-5.2"))
    with _env(ZIYA_THINKING_MODE="1", ZIYA_THINKING_DISPLAY="full"):
        cfg = ex._build_provider_config(0)
    assert cfg.thinking.enabled and cfg.thinking.display == "full"
    with _env():
        assert ex._build_provider_config(0).thinking is None


def test_executor_source_drops_thinking_deltas_when_quiet():
    # The drop sits inside a 6k-line generator; assert the seam textually
    # rather than driving a full stream: the ThinkingDelta branch must
    # consult suppress_thinking_deltas before emitting a display chunk.
    import inspect
    from app import streaming_tool_executor as ste
    src = inspect.getsource(ste)
    idx = src.index("elif isinstance(stream_event, ThinkingDelta):")
    window = src[idx: idx + 600]
    assert "suppress_thinking_deltas" in window


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def test_settings_request_accepts_display_and_rejects_junk():
    from app.routes.model_routes import ModelSettingsRequest
    r = ModelSettingsRequest(thinking_mode=True, thinking_display="quiet")
    assert r.thinking_display == "quiet"
    with pytest.raises(Exception):
        ModelSettingsRequest(thinking_display="loud")


def test_env_registry_declares_display():
    from app.config.env_registry import ziya_env
    with patch.dict(os.environ, {"ZIYA_THINKING_DISPLAY": "full"}):
        assert ziya_env("ZIYA_THINKING_DISPLAY") == "full"
    from app.config.models_config import ENV_VAR_MAPPING
    assert ENV_VAR_MAPPING["ZIYA_THINKING_DISPLAY"] == "thinking_display"


def test_capabilities_publish_thinking_options():
    from app.routes.model_routes import get_model_capabilities
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet4.6",
                                 "ZIYA_THINKING_MODE": "1", "ZIYA_THINKING_DISPLAY": "quiet"}):
        caps = get_model_capabilities(model="sonnet4.6", endpoint="bedrock")
    assert "error" not in caps, caps
    assert caps["thinking_group"] == to.GROUP_CLAUDE_ADAPTIVE
    assert caps["thinking_options"] == ["off", "quiet", "summary"]
    assert caps["thinking_display"] == "quiet"
    assert caps["thinking_option_labels"]["quiet"] == "On · quiet"
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "haiku"}):
        caps = get_model_capabilities(model="haiku", endpoint="bedrock")
    assert caps["thinking_options"] == []
