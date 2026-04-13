"""
Tests for app.services.model_resolver — lightweight service model resolution.

Covers:
  - Default resolution per endpoint (bedrock, google, openai, anthropic, local)
  - Env var override per category
  - Per-category endpoint override
  - Plugin-based config override
  - Local model routing
  - Fallback behavior for unknown endpoints
"""

import os
from unittest.mock import patch, MagicMock

import pytest

from app.services.model_resolver import resolve_service_model
from app.config.models_config import DEFAULT_SERVICE_MODELS


# ── Default Resolution ────────────────────────────────────────────

class TestDefaultResolution:

    def test_bedrock_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["endpoint"] == "bedrock"
            assert "haiku" in config["model_id"].lower()  # memory_extraction uses haiku override

    def test_google_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "google"}, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["endpoint"] == "google"
            assert "flash" in config["model_id"].lower() or "gemini" in config["model_id"].lower()

    def test_openai_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "openai"}, clear=False):
            config = resolve_service_model("default")
            assert config["endpoint"] == "openai"
            assert "gpt" in config["model_id"].lower() or "mini" in config["model_id"].lower()

    def test_anthropic_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "anthropic"}, clear=False):
            config = resolve_service_model("default")
            assert config["endpoint"] == "anthropic"
            assert "haiku" in config["model_id"].lower() or "claude" in config["model_id"].lower()

    def test_local_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "local"}, clear=False):
            config = resolve_service_model("default")
            assert config["endpoint"] == "local"
            assert config["model_id"]  # Has a default model
            # Should have base_url or the caller derives it from env

    def test_unknown_category_uses_default(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}, clear=False):
            config = resolve_service_model("some_future_category")
            # Falls back to "default" entry for the endpoint
            from app.services.model_resolver import _ENDPOINT_DEFAULTS
            assert config["model_id"] == _ENDPOINT_DEFAULTS["bedrock"]["default"]["model_id"]


# ── Env Var Overrides ─────────────────────────────────────────────

class TestEnvVarOverrides:

    def test_model_override(self):
        env = {
            "ZIYA_ENDPOINT": "bedrock",
            "ZIYA_MEMORY_EXTRACTION_MODEL": "us.amazon.nova-micro-v1:0",
        }
        with patch.dict(os.environ, env, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["model_id"] == "us.amazon.nova-micro-v1:0"

    def test_endpoint_override_per_category(self):
        """A Google user can route extraction through a local model."""
        env = {
            "ZIYA_ENDPOINT": "google",
            "ZIYA_MEMORY_EXTRACTION_ENDPOINT": "local",
            "ZIYA_MEMORY_EXTRACTION_MODEL": "llama3.2:3b",
        }
        with patch.dict(os.environ, env, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["endpoint"] == "local"
            assert config["model_id"] == "llama3.2:3b"

    def test_region_override(self):
        env = {
            "ZIYA_ENDPOINT": "bedrock",
            "ZIYA_MEMORY_EXTRACTION_MODEL": "us.amazon.nova-lite-v1:0",
            "ZIYA_MEMORY_EXTRACTION_REGION": "us-west-2",
        }
        with patch.dict(os.environ, env, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["region"] == "us-west-2"

    def test_env_override_takes_priority_over_defaults(self):
        """Env var should win over endpoint defaults."""
        env = {
            "ZIYA_ENDPOINT": "bedrock",
            "ZIYA_MEMORY_EXTRACTION_MODEL": "custom-model-id",
        }
        with patch.dict(os.environ, env, clear=False):
            config = resolve_service_model("memory_extraction")
            assert config["model_id"] == "custom-model-id"


# ── Endpoint Defaults Structure ───────────────────────────────────

class TestEndpointDefaults:

    def test_all_major_endpoints_have_defaults(self):
        for ep in ("bedrock", "google", "openai", "anthropic"):
            assert ep in DEFAULT_SERVICE_MODELS, f"Missing service model for endpoint: {ep}"
            assert DEFAULT_SERVICE_MODELS[ep], f"Empty model_id for {ep}"

    def test_bedrock_has_memory_extraction_category(self):
        """memory_extraction resolves to the bedrock default service model."""
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}, clear=False):
            config = resolve_service_model("memory_extraction")
            # memory_extraction has a category override (haiku), not the base default (nova)
            from app.config.models_config import SERVICE_MODEL_OVERRIDES
            expected = SERVICE_MODEL_OVERRIDES.get("memory_extraction", {}).get("bedrock", DEFAULT_SERVICE_MODELS["bedrock"])
            assert config["model_id"] == expected


# ── Unknown Endpoint Fallback ─────────────────────────────────────

class TestFallback:

    def test_unknown_endpoint_falls_back_to_bedrock(self):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "unknown_provider"}, clear=False):
            config = resolve_service_model("default")
            # Unknown endpoint → empty defaults, but should not crash
            assert config["model_id"]
