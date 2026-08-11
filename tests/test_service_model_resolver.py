"""
Tests for app.services.model_resolver — service model resolution.

Covers:
  - Default model selection per endpoint
  - Environment variable overrides
  - Category-specific resolution
  - Per-service endpoint override
"""

import os
from unittest.mock import patch

import pytest

from app.services.model_resolver import resolve_service_model, _ENDPOINT_DEFAULTS


class TestResolveDefaults:

    def test_bedrock_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["endpoint"] == "bedrock"
            assert "haiku" in config["model_id"]  # memory_extraction uses haiku override

    def test_google_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "google"}, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["endpoint"] == "google"
            assert "flash" in config["model_id"].lower()

    def test_openai_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "openai"}, clear=False):
            config = resolve_service_model("default")
            assert config["endpoint"] == "openai"
            assert "mini" in config["model_id"].lower()

    def test_anthropic_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "anthropic"}, clear=False):
            config = resolve_service_model("default")
            assert config["endpoint"] == "anthropic"
            assert "haiku" in config["model_id"].lower()

    def test_unknown_category_falls_back_to_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}, clear=False):
            config = resolve_service_model("nonexistent_category")
            assert config["model_id"] == _ENDPOINT_DEFAULTS["bedrock"]["default"]["model_id"]


class TestEnvOverrides:

    def test_model_override(self):
        env = {
            "ZIYA_ENDPOINT": "bedrock",
            "ZIYA_MEMORY_EXTRACTION_MODEL": "us.amazon.nova-micro-v1:0",
        }
        with patch.dict(os.environ, env, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["model_id"] == "us.amazon.nova-micro-v1:0"

    def test_region_override(self):
        env = {
            "ZIYA_ENDPOINT": "bedrock",
            "ZIYA_MEMORY_EXTRACTION_MODEL": "us.amazon.nova-lite-v1:0",
            "ZIYA_MEMORY_EXTRACTION_REGION": "eu-west-1",
        }
        with patch.dict(os.environ, env, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["region"] == "eu-west-1"

    def test_endpoint_override_per_service(self):
        """A service can use a different endpoint than the primary model."""
        env = {
            "ZIYA_ENDPOINT": "google",
            "ZIYA_MEMORY_EXTRACTION_ENDPOINT": "bedrock",
            "ZIYA_MEMORY_EXTRACTION_MODEL": "us.amazon.nova-lite-v1:0",
        }
        with patch.dict(os.environ, env, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["endpoint"] == "bedrock"
            assert "nova" in config["model_id"]

    def test_env_override_takes_priority_over_defaults(self):
        env = {
            "ZIYA_ENDPOINT": "google",
            "ZIYA_MEMORY_EXTRACTION_MODEL": "my-custom-model",
        }
        with patch.dict(os.environ, env, clear=False):
            config = resolve_service_model("memory_extraction")
            # Should use the env var, not the google default
            assert config["model_id"] == "my-custom-model"


class TestEndpointCoverage:

    def test_all_endpoints_have_defaults(self):
        """Every known endpoint should have at least a 'default' entry."""
        for ep in ("bedrock", "google", "openai", "anthropic"):
            assert ep in _ENDPOINT_DEFAULTS, f"Missing defaults for endpoint: {ep}"
            assert "default" in _ENDPOINT_DEFAULTS[ep], f"Missing 'default' for endpoint: {ep}"
            assert _ENDPOINT_DEFAULTS[ep]["default"]["model_id"], f"Empty model_id for {ep}"

    def test_unknown_endpoint_falls_back_to_bedrock(self):
        """An endpoint with no service-model table falls back to Bedrock
        COMPLETELY — both the model_id AND the reported endpoint.

        This previously asserted the endpoint stayed as the caller's value
        while the model_id came from Bedrock's table.  That pairing is
        unusable: call_service_model dispatches on the endpoint, so a
        non-Bedrock endpoint carrying a Bedrock model_id POSTs e.g.
        'us.amazon.nova-lite-v1:0' to api.meta.ai and fails every call.  It
        stayed latent only because unrecognised endpoints hit the
        _call_bedrock else-branch anyway; adding 'meta' to the
        OpenAI-compatible dispatch tuple made it reachable.
        """
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "some_future_provider"}, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["endpoint"] == "bedrock"
            expected = _ENDPOINT_DEFAULTS["bedrock"].get(
                "memory_extraction", _ENDPOINT_DEFAULTS["bedrock"]["default"]
            )["model_id"]
            assert config["model_id"] == expected

    def test_endpoint_and_model_id_always_come_from_same_table(self):
        """The returned endpoint must own the returned model_id.

        Pins the invariant for every endpoint Ziya can be configured with,
        including ones added later that have no service-model table yet.
        A mismatch means a model ID gets POSTed to the wrong provider.
        """
        from app.config.models_config import MODEL_CONFIGS

        for ep in list(MODEL_CONFIGS.keys()) + ["some_future_provider"]:
            with patch.dict(os.environ, {"ZIYA_ENDPOINT": ep}, clear=False):
                for category in ("default", "memory_extraction", "intent_judge"):
                    cfg = resolve_service_model(category)
                    owner = cfg["endpoint"]
                    assert owner in _ENDPOINT_DEFAULTS, (
                        f"{ep}/{category}: reported endpoint '{owner}' has no "
                        f"service-model table"
                    )
                    table = _ENDPOINT_DEFAULTS[owner]
                    valid = {
                        entry["model_id"] for entry in table.values()
                        if entry.get("model_id")
                    }
                    assert cfg["model_id"] in valid, (
                        f"{ep}/{category}: model_id '{cfg['model_id']}' is not "
                        f"in the '{owner}' table — endpoint/model_id mismatch"
                    )
