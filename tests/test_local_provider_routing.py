"""
Tests for the ``local`` endpoint (phase 0 of design/local-models.md).

Local inference servers -- Ollama, LM Studio, llama.cpp ``llama-server`` --
speak OpenAI Chat Completions, so Ziya routes ``local`` through
OpenAIDirectProvider / DirectOpenAIModel exactly as ``zai`` and ``meta`` do,
pointed at ZIYA_LOCAL_MODEL_URL. Phase 0 synthesizes ONE model entry from
ZIYA_LOCAL_MODEL; these tests pin that and every dispatch seam the endpoint
must cross, so a hop that forgets ``local`` fails here rather than as a 500
on the first chat.

Run:
    pytest tests/test_local_provider_routing.py -v
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest

from app.utils import local_models as lm


@pytest.fixture(autouse=True)
def _no_real_servers(monkeypatch):
    """These tests are about the static seams; never scan the dev box."""
    monkeypatch.setattr(lm, "KNOWN_LOCAL_PORTS", ())
    lm.reset_discovery_cache_for_tests()
    yield
    lm.reset_discovery_cache_for_tests()


# ── URL / model helpers (pure, env-driven) ─────────────────────────────

class TestLocalHelpers:
    def test_default_url_is_ollama_with_v1(self, monkeypatch):
        monkeypatch.delenv("ZIYA_LOCAL_MODEL_URL", raising=False)
        assert lm.local_base_url() == "http://localhost:11434/v1"

    @pytest.mark.parametrize("raw", [
        "http://localhost:1234",
        "http://localhost:1234/",
        "http://localhost:1234/v1",
        "http://localhost:1234/v1/",
        "  http://localhost:1234  ",
    ])
    def test_v1_suffix_is_derived_not_duplicated(self, monkeypatch, raw):
        monkeypatch.setenv("ZIYA_LOCAL_MODEL_URL", raw)
        assert lm.local_base_url() == "http://localhost:1234/v1"

    def test_blank_url_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ZIYA_LOCAL_MODEL_URL", "   ")
        assert lm.local_base_url() == "http://localhost:11434/v1"

    def test_model_name_default_and_override(self, monkeypatch):
        monkeypatch.delenv("ZIYA_LOCAL_MODEL", raising=False)
        assert lm.local_model_name() == lm.DEFAULT_LOCAL_MODEL
        monkeypatch.setenv("ZIYA_LOCAL_MODEL", "llama3.2:1b")
        assert lm.local_model_name() == "llama3.2:1b"

    @pytest.mark.parametrize("raw,expected", [
        ("", None),
        ("abc", None),
        ("0", None),
        ("-5", None),
        ("32768", 32768),
    ])
    def test_token_cap_is_optional(self, monkeypatch, raw, expected):
        """ZIYA_LOCAL_TOKEN_LIMIT is a CEILING, unset by default: the model's
        full discovered window is used unless the user caps it."""
        monkeypatch.setenv("ZIYA_LOCAL_TOKEN_LIMIT", raw)
        assert lm.local_token_cap() == expected

    def test_synthesized_entry_shape(self, monkeypatch):
        monkeypatch.delenv("ZIYA_LOCAL_TOKEN_LIMIT", raising=False)
        entry = lm.synthesize_local_model_entry("tiny:1b")
        assert entry["model_id"] == "tiny:1b"
        assert entry["family"] == "local"
        assert entry["native_function_calling"] is True
        assert entry["token_limit"] == lm.UNPROBED_LOCAL_TOKEN_LIMIT
        # Output cap never exceeds the context window.
        assert entry["max_output_tokens"] <= entry["token_limit"]
        # Nothing is sent to the server until discovery has asked it.
        assert "request_extra_body" not in entry


# ── Discovery: the server's answer becomes the config ──────────────────

def _ollama(ctx=131072, caps=("completion", "tools")):
    return lm.DiscoveredModel(
        runtime="ollama", name="qwen2.5-coder:7b", context_length=ctx,
        supports_tools="tools" in caps, supports_vision="vision" in caps,
        supports_thinking="thinking" in caps,
    )


class TestLocalDiscovery:
    def test_full_context_is_used_and_sent_as_num_ctx(self, monkeypatch):
        """The whole point: Ziya's budget and Ollama's allocation are the
        same number, and it is the model's full window, not a guess."""
        monkeypatch.delenv("ZIYA_LOCAL_TOKEN_LIMIT", raising=False)
        entry = lm.apply_discovery(lm.synthesize_local_model_entry("m"), _ollama(ctx=131072))
        assert entry["token_limit"] == 131072
        assert entry["request_extra_body"] == {"options": {"num_ctx": 131072}}
        assert entry["max_output_tokens"] <= 131072

    def test_cap_lowers_both_budget_and_num_ctx(self, monkeypatch):
        monkeypatch.setenv("ZIYA_LOCAL_TOKEN_LIMIT", "65536")
        entry = lm.apply_discovery(lm.synthesize_local_model_entry("m"), _ollama(ctx=131072))
        assert entry["token_limit"] == 65536
        assert entry["request_extra_body"]["options"]["num_ctx"] == 65536

    def test_cap_never_raises_above_server(self, monkeypatch):
        monkeypatch.setenv("ZIYA_LOCAL_TOKEN_LIMIT", "999999")
        entry = lm.apply_discovery(lm.synthesize_local_model_entry("m"), _ollama(ctx=32768))
        assert entry["token_limit"] == 32768

    def test_lmstudio_sets_budget_but_no_num_ctx(self, monkeypatch):
        """LM Studio fixes context at load time; sending options would be
        an unknown field. Budget still follows the server's number."""
        monkeypatch.delenv("ZIYA_LOCAL_TOKEN_LIMIT", raising=False)
        found = lm.DiscoveredModel(runtime="lmstudio", name="m", context_length=40960,
                                   supports_tools=None, supports_vision=None,
                                   supports_thinking=None, loaded=True)
        entry = lm.apply_discovery(lm.synthesize_local_model_entry("m"), found)
        assert entry["token_limit"] == 40960
        assert "request_extra_body" not in entry
        # Unknown capability => the synthesized default is left alone.
        assert entry["native_function_calling"] is True

    def test_capabilities_follow_the_server(self):
        entry = lm.apply_discovery(
            lm.synthesize_local_model_entry("m"),
            _ollama(caps=("completion", "vision", "thinking")),
        )
        assert entry["native_function_calling"] is False
        assert entry["supports_vision"] is True
        assert entry["supports_thinking"] is True

    def test_small_window_warns(self, caplog):
        with caplog.at_level("WARNING", logger="app.utils.local_models"):
            lm.apply_discovery(lm.synthesize_local_model_entry("m"), _ollama(ctx=4096))
        assert any("4096-token context" in r.getMessage() for r in caplog.records)

    def test_ollama_show_parsing(self, monkeypatch):
        """/api/show shape: arch-prefixed context_length + capabilities list."""
        payload = {
            "capabilities": ["completion", "tools"],
            "model_info": {"general.architecture": "qwen2",
                           "qwen2.context_length": 32768},
        }
        monkeypatch.setattr(lm, "_http_json", lambda m, u, b=None, timeout=0: payload)
        found = lm._probe_ollama("http://x", "q")
        assert found.context_length == 32768 and found.supports_tools is True

    def test_probe_failure_is_none_not_exception(self, monkeypatch):
        monkeypatch.setattr(lm, "_http_json", lambda *a, **k: None)
        assert lm.discover_local_model("m", "http://127.0.0.1:1") is None

    def test_discover_and_apply_updates_model_configs(self, monkeypatch):
        """The seam: the entry ModelManager / factory read is the one updated."""
        from app.config.models_config import MODEL_CONFIGS
        lm.reset_discovery_cache_for_tests()
        monkeypatch.delenv("ZIYA_LOCAL_TOKEN_LIMIT", raising=False)
        monkeypatch.setattr(lm, "discover_local_model", lambda n, r=None, rt=None: _ollama(ctx=131072))
        try:
            found = lm.discover_and_apply("probe-test:1b")
            assert found is not None
            entry = MODEL_CONFIGS["local"]["probe-test:1b"]
            assert entry["token_limit"] == 131072
            assert entry["request_extra_body"]["options"]["num_ctx"] == 131072
        finally:
            MODEL_CONFIGS["local"].pop("probe-test:1b", None)
            lm.reset_discovery_cache_for_tests()

    def test_server_down_leaves_placeholder(self, monkeypatch):
        from app.config.models_config import MODEL_CONFIGS
        lm.reset_discovery_cache_for_tests()
        monkeypatch.setattr(lm, "discover_local_model", lambda n, r=None, rt=None: None)
        try:
            assert lm.discover_and_apply("down-test:1b") is None
            entry = MODEL_CONFIGS["local"]["down-test:1b"]
            assert entry["token_limit"] == lm.UNPROBED_LOCAL_TOKEN_LIMIT
            assert "request_extra_body" not in entry
        finally:
            MODEL_CONFIGS["local"].pop("down-test:1b", None)
            lm.reset_discovery_cache_for_tests()

    def test_build_configs_keys_by_model_name(self, monkeypatch):
        monkeypatch.setenv("ZIYA_LOCAL_MODEL", "phi4:14b")
        table = lm.build_local_model_configs()
        assert list(table) == ["phi4:14b"]
        assert table["phi4:14b"]["model_id"] == "phi4:14b"


# ── Config seams ───────────────────────────────────────────────────────

class TestLocalEndpointConfig:
    def test_local_endpoint_exists(self):
        from app.config.models_config import (
            MODEL_CONFIGS, ENDPOINT_DEFAULTS, MODEL_FAMILIES,
        )
        assert "local" in MODEL_CONFIGS
        assert "local" in ENDPOINT_DEFAULTS
        assert "local" in MODEL_FAMILIES

    def test_default_model_is_defined_in_configs(self):
        from app.config.models_config import MODEL_CONFIGS, DEFAULT_MODELS
        assert DEFAULT_MODELS["local"] in MODEL_CONFIGS["local"]

    def test_service_model_is_defined_in_configs(self):
        from app.config.models_config import MODEL_CONFIGS, DEFAULT_SERVICE_MODELS
        assert DEFAULT_SERVICE_MODELS["local"] in MODEL_CONFIGS["local"]

    def test_entry_references_existing_family(self):
        from app.config.models_config import MODEL_CONFIGS, MODEL_FAMILIES
        for cfg in MODEL_CONFIGS["local"].values():
            assert cfg["family"] in MODEL_FAMILIES

    def test_capabilities_resolve(self):
        from app.config.models_config import (
            MODEL_CONFIGS, get_model_capabilities, validate_model_parameters,
        )
        name = next(iter(MODEL_CONFIGS["local"]))
        caps = get_model_capabilities("local", name)
        assert caps["native_function_calling"] is True
        ok, err, filt = validate_model_parameters(
            "local", name, {"temperature": 0.5, "max_tokens": 1000}
        )
        assert ok, err
        assert filt == {"temperature": 0.5, "max_tokens": 1000}

    def test_env_model_name_becomes_the_entry(self, monkeypatch):
        """ZIYA_LOCAL_MODEL is read at import; re-importing must reflect it.

        This is the phase-0 contract: no ~/.ziya/models.json entry is needed
        to run a model the server has pulled.
        """
        import app.config.models_config as mc
        monkeypatch.setenv("ZIYA_LOCAL_MODEL", "llama3.2:1b")
        try:
            fresh = importlib.reload(mc)
            assert "llama3.2:1b" in fresh.MODEL_CONFIGS["local"]
            assert fresh.DEFAULT_MODELS["local"] == "llama3.2:1b"
        finally:
            monkeypatch.delenv("ZIYA_LOCAL_MODEL", raising=False)
            importlib.reload(mc)


# ── Credential registry ────────────────────────────────────────────────

class TestLocalCredentialRegistry:
    def test_registered_and_autoselectable(self):
        import app.utils.provider_detection as pd
        entry = next(p for p in pd.PROVIDER_CREDENTIALS if p.endpoint == "local")
        assert entry.canonical_key == "ZIYA_LOCAL_MODEL_URL"
        # A MacBook with only a local server must be able to land on it.
        assert entry.autoselectable is True

    def test_detected_when_url_set(self, monkeypatch):
        import app.utils.provider_detection as pd
        monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
        for p in pd.PROVIDER_CREDENTIALS:
            for k in p.keys:
                monkeypatch.delenv(k, raising=False)
        assert pd.detect_available_providers()["local"] is False
        monkeypatch.setenv("ZIYA_LOCAL_MODEL_URL", "http://localhost:11434")
        assert pd.detect_available_providers()["local"] is True
        assert pd.maybe_autoselect_endpoint("bedrock", False) == "local"


# ── Factory / ModelManager / service-model seams ───────────────────────

class TestLocalFactoryRouting:
    def _cfg(self):
        from app.config.models_config import MODEL_CONFIGS
        name = next(iter(MODEL_CONFIGS["local"]))
        return name, MODEL_CONFIGS["local"][name]

    def test_is_endpoint_supported(self):
        from app.providers.factory import is_endpoint_supported
        assert is_endpoint_supported("local")

    def test_routes_to_openai_direct_provider(self, monkeypatch):
        from app.providers.factory import create_provider
        monkeypatch.delenv("ZIYA_LOCAL_MODEL_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        name, cfg = self._cfg()
        provider = create_provider(endpoint="local", model_id=name, model_config=cfg)
        assert provider.__class__.__name__ == "OpenAIDirectProvider"
        assert str(provider.client.base_url).rstrip("/") == "http://localhost:11434/v1"
        # No OPENAI_API_KEY in the environment: the placeholder must be used,
        # otherwise the SDK refuses to build the client at all.
        assert provider.client.api_key == lm.LOCAL_PLACEHOLDER_API_KEY

    def test_base_url_override_reaches_client(self, monkeypatch):
        from app.providers.factory import create_provider
        monkeypatch.setenv("ZIYA_LOCAL_MODEL_URL", "http://localhost:1234")
        name, cfg = self._cfg()
        provider = create_provider(endpoint="local", model_id=name, model_config=cfg)
        assert str(provider.client.base_url).rstrip("/") == "http://localhost:1234/v1"

    def test_provider_sends_declared_envelope(self, monkeypatch):
        """SEAM: request_extra_body on the model entry must reach the kwargs
        the SDK is called with -- otherwise discovery sets num_ctx on a dict
        nobody reads and Ollama silently stays at its 4k default."""
        from app.providers.factory import create_provider
        from app.providers.base import ProviderConfig
        monkeypatch.setattr(lm, "discover_local_model", lambda n, r=None, rt=None: None)
        lm.reset_discovery_cache_for_tests()
        name, cfg = self._cfg()
        cfg = {**cfg, "request_extra_body": {"options": {"num_ctx": 131072}}}
        provider = create_provider(endpoint="local", model_id=name, model_config=cfg)
        kwargs = provider._build_request(
            [{"role": "user", "content": "hi"}], None, [], ProviderConfig(),
        )
        assert kwargs["extra_body"]["options"]["num_ctx"] == 131072

    def test_provider_reads_live_entry_not_stale_copy(self, monkeypatch):
        """The streaming path hands the factory a COPY of the entry taken
        before discovery ran; the provider must still see the discovered
        window, so the factory re-reads MODEL_CONFIGS after discovery."""
        from app.providers.factory import create_provider
        monkeypatch.delenv("ZIYA_LOCAL_TOKEN_LIMIT", raising=False)
        monkeypatch.setattr(lm, "discover_local_model", lambda n, r=None, rt=None: _ollama(ctx=65536))
        lm.reset_discovery_cache_for_tests()
        name, cfg = self._cfg()
        try:
            stale = {**cfg}
            stale.pop("request_extra_body", None)
            provider = create_provider(endpoint="local", model_id=name, model_config=stale)
            assert provider.model_config["token_limit"] == 65536
            assert provider.model_config["request_extra_body"]["options"]["num_ctx"] == 65536
        finally:
            lm.reset_discovery_cache_for_tests()
            # Undo the in-place update so other tests see the placeholder.
            lm.discover_and_apply(name)
            from app.config.models_config import MODEL_CONFIGS
            MODEL_CONFIGS["local"][name] = lm.synthesize_local_model_entry(name)
            lm.reset_discovery_cache_for_tests()

    def test_wrapper_sends_envelope_on_every_request(self, monkeypatch):
        """SEAM for the DirectOpenAIModel path (ModelManager)."""
        import asyncio
        from app.agents.models import ModelManager
        name, cfg = self._cfg()
        cfg = {**cfg, "request_extra_body": {"options": {"num_ctx": 65536}}}
        model = ModelManager._initialize_local_model(dict(cfg))
        assert model.extra_body == {"options": {"num_ctx": 65536}}

        seen = {}

        async def _fake_create(**kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop after capture")

        model.client.chat.completions.create = _fake_create

        from langchain_core.messages import HumanMessage

        async def _drive():
            out = []
            async for ev in model.astream([HumanMessage(content="hi")]):
                out.append(ev)
                break
            return out

        asyncio.run(_drive())
        assert seen.get("extra_body") == {"options": {"num_ctx": 65536}}

    def test_model_manager_initializes_without_credentials(self, monkeypatch):
        """--endpoint local with NO env vars set must build a model. The
        credential registry marks it unavailable for auto-select purposes,
        but explicit selection uses the default URL, not an error."""
        from app.agents.models import ModelManager
        monkeypatch.delenv("ZIYA_LOCAL_MODEL_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        name, cfg = self._cfg()
        model = ModelManager._initialize_local_model(dict(cfg))
        assert model.__class__.__name__ == "DirectOpenAIModel"
        assert model.model_name == name
        assert str(model.client.base_url).rstrip("/") == "http://localhost:11434/v1"

    def test_service_model_routes_through_openai_compatible(self):
        import inspect
        import app.services.model_resolver as mr
        src = inspect.getsource(mr.call_service_model)
        assert '"local"' in src or "'local'" in src, (
            "call_service_model should route 'local' to the OpenAI-compatible path"
        )
        src2 = inspect.getsource(mr._call_openai_compatible)
        # Per-endpoint refactor: the URL comes from local_endpoint_base_url(ep);
        # the pre-refactor global helper local_base_url is accepted for history.
        assert "local_endpoint_base_url" in src2 or "local_base_url" in src2, (
            "_call_openai_compatible must point the client at the local server, "
            "not fall through to OpenAI()"
        )

    def test_resolve_service_model_for_local(self):
        from app.services.model_resolver import resolve_service_model
        from app.config.models_config import DEFAULT_SERVICE_MODELS
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "local"}, clear=False):
            os.environ.pop("ZIYA_DEFAULT_MODEL", None)
            cfg = resolve_service_model("default")
        assert cfg["endpoint"] == "local"
        assert cfg["model_id"] == DEFAULT_SERVICE_MODELS["local"]


# ── Accounting / display ───────────────────────────────────────────────

class TestLocalAccounting:
    def test_pricing_has_zero_cost_rule_for_local(self):
        """The built-in catalog prices the real endpoint name. Before this
        the rule named a provider 'ollama' that no endpoint ever reports, so
        local usage would have been 'unknown', never $0."""
        from app.config.pricing import BUILTIN_CATALOG
        providers = {r.get("provider") for r in BUILTIN_CATALOG}
        assert "ollama" not in providers
        rules = [r for r in BUILTIN_CATALOG if r.get("provider") == "local"]
        assert rules, "no zero-cost pricing rule for provider 'local'"
        assert all(v == 0.0 for v in rules[0]["unit_prices"].values())

    def test_pdf_export_has_display_name(self):
        from app.services.pdf_exporter import _PROVIDER_DISPLAY_NAMES
        assert _PROVIDER_DISPLAY_NAMES.get("local") == "Local"
        assert "ollama" not in _PROVIDER_DISPLAY_NAMES
