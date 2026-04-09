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
            assert "nova-lite" in config["model_id"]

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
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "some_future_provider"}, clear=False):
            config = resolve_service_model("memory_extraction")
            # Unknown endpoint → uses bedrock defaults
            assert config["endpoint"] == "some_future_provider"
            # model_id comes from bedrock defaults since there's no entry for the unknown
            assert config["model_id"] == _ENDPOINT_DEFAULTS["bedrock"]["default"]["model_id"]
