"""
Tests for global inference profile preference in Bedrock model resolution.

Verifies that:
  - Models with a "global" key in model_id use the global profile by default
  - ZIYA_PREFER_REGIONAL_INFERENCE=1 bypasses global and uses region-specific
  - Models without a "global" key fall through to existing region logic
  - The region is NOT changed when using a global profile (global works everywhere)
  - The region router's _PREFIX_TO_REGIONS includes "global"
"""
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure ZIYA_PREFER_REGIONAL_INFERENCE is unset for each test."""
    monkeypatch.delenv("ZIYA_PREFER_REGIONAL_INFERENCE", raising=False)


def _resolve(model_id, region="us-east-1", model_config=None, model_name=None):
    """Helper to call the resolution function."""
    from app.agents.models import ModelManager
    return ModelManager._get_region_specific_model_id_with_region_update(
        model_id, region, model_config=model_config, model_name=model_name
    )


# -- Default behavior: prefer global when available -------------------------

class TestGlobalPreference:
    def test_global_key_selected_over_us(self):
        model_id = {
            "us": "us.anthropic.claude-sonnet-4-6",
            "eu": "eu.anthropic.claude-sonnet-4-6",
            "global": "global.anthropic.claude-sonnet-4-6",
        }
        resolved_id, resolved_region = _resolve(model_id, region="us-east-1")
        assert resolved_id == "global.anthropic.claude-sonnet-4-6"
        assert resolved_region == "us-east-1"  # Region unchanged

    def test_global_key_selected_over_eu(self):
        model_id = {
            "us": "us.anthropic.claude-opus-4-7",
            "eu": "eu.anthropic.claude-opus-4-7",
            "global": "global.anthropic.claude-opus-4-7",
        }
        resolved_id, resolved_region = _resolve(model_id, region="eu-west-1")
        assert resolved_id == "global.anthropic.claude-opus-4-7"
        assert resolved_region == "eu-west-1"  # Region unchanged

    def test_global_preserves_any_region(self):
        """Global profiles work from any region — don't force a switch."""
        model_id = {
            "us": "us.anthropic.claude-opus-4-8",
            "global": "global.anthropic.claude-opus-4-8",
        }
        for region in ["us-east-1", "us-west-2", "eu-west-1", "ap-northeast-1"]:
            resolved_id, resolved_region = _resolve(model_id, region=region)
            assert resolved_id == "global.anthropic.claude-opus-4-8"
            assert resolved_region == region

    def test_global_key_empty_string_falls_through(self):
        """An empty global value is treated as absent."""
        model_id = {
            "us": "us.anthropic.claude-sonnet-4-6",
            "global": "",
        }
        resolved_id, _ = _resolve(model_id, region="us-east-1")
        assert resolved_id == "us.anthropic.claude-sonnet-4-6"

    def test_global_key_none_falls_through(self):
        """A None global value is treated as absent."""
        model_id = {
            "us": "us.anthropic.claude-sonnet-4-6",
            "global": None,
        }
        resolved_id, _ = _resolve(model_id, region="us-east-1")
        assert resolved_id == "us.anthropic.claude-sonnet-4-6"


# -- Opt-out: ZIYA_PREFER_REGIONAL_INFERENCE=1 ------------------------------

class TestRegionalOptOut:
    def test_prefer_regional_bypasses_global(self, monkeypatch):
        monkeypatch.setenv("ZIYA_PREFER_REGIONAL_INFERENCE", "1")
        model_id = {
            "us": "us.anthropic.claude-sonnet-4-6",
            "eu": "eu.anthropic.claude-sonnet-4-6",
            "global": "global.anthropic.claude-sonnet-4-6",
        }
        resolved_id, _ = _resolve(model_id, region="us-east-1")
        assert resolved_id == "us.anthropic.claude-sonnet-4-6"

    def test_prefer_regional_eu_region(self, monkeypatch):
        monkeypatch.setenv("ZIYA_PREFER_REGIONAL_INFERENCE", "1")
        model_id = {
            "us": "us.anthropic.claude-opus-4-7",
            "eu": "eu.anthropic.claude-opus-4-7",
            "global": "global.anthropic.claude-opus-4-7",
        }
        resolved_id, _ = _resolve(model_id, region="eu-west-1")
        assert resolved_id == "eu.anthropic.claude-opus-4-7"

    def test_prefer_regional_only_on_value_1(self, monkeypatch):
        """Anything other than '1' does not trigger the opt-out."""
        monkeypatch.setenv("ZIYA_PREFER_REGIONAL_INFERENCE", "true")
        model_id = {
            "us": "us.anthropic.claude-opus-4-8",
            "global": "global.anthropic.claude-opus-4-8",
        }
        resolved_id, _ = _resolve(model_id, region="us-east-1")
        assert resolved_id == "global.anthropic.claude-opus-4-8"

    def test_prefer_regional_whitespace_handling(self, monkeypatch):
        monkeypatch.setenv("ZIYA_PREFER_REGIONAL_INFERENCE", " 1 ")
        model_id = {
            "us": "us.anthropic.claude-sonnet-4-6",
            "global": "global.anthropic.claude-sonnet-4-6",
        }
        resolved_id, _ = _resolve(model_id, region="us-east-1")
        assert resolved_id == "us.anthropic.claude-sonnet-4-6"


# -- Fallback: no global key ------------------------------------------------

class TestNoGlobalFallback:
    def test_no_global_key_uses_region_prefix(self):
        """Models without a global key use the existing region logic."""
        model_id = {
            "us": "us.anthropic.claude-3-sonnet-20240229-v1:0",
            "eu": "anthropic.claude-3-sonnet-20240229-v1:0",
        }
        resolved_id, _ = _resolve(model_id, region="us-east-1")
        assert resolved_id == "us.anthropic.claude-3-sonnet-20240229-v1:0"

    def test_no_global_key_eu_region(self):
        model_id = {
            "us": "us.anthropic.claude-3-sonnet-20240229-v1:0",
            "eu": "anthropic.claude-3-sonnet-20240229-v1:0",
        }
        resolved_id, _ = _resolve(model_id, region="eu-west-1")
        assert resolved_id == "anthropic.claude-3-sonnet-20240229-v1:0"

    def test_string_model_id_passthrough(self):
        """String model_id (not a dict) returns as-is."""
        resolved_id, region = _resolve(
            "global.anthropic.claude-opus-4-5-20251101-v1:0", region="us-east-1"
        )
        assert resolved_id == "global.anthropic.claude-opus-4-5-20251101-v1:0"
        assert region == "us-east-1"


# -- Config verification: all Anthropic 4.x models have global keys ----------

class TestConfigIntegrity:
    def test_modern_anthropic_models_have_global_key(self):
        """Every Anthropic model from sonnet4.0+ should have a global profile."""
        from app.config.models_config import MODEL_CONFIGS
        bedrock = MODEL_CONFIGS.get("bedrock", {})
        models_requiring_global = [
            "sonnet4.0", "sonnet4.5", "sonnet4.6",
            "opus4", "opus4.1", "opus4.6", "opus4.7", "opus4.8",
            "haiku-4.5",
        ]
        for name in models_requiring_global:
            cfg = bedrock.get(name)
            assert cfg is not None, f"Model {name} not found in config"
            mid = cfg.get("model_id")
            assert isinstance(mid, dict), f"{name}: model_id should be a dict, got {type(mid)}"
            assert "global" in mid, f"{name}: missing 'global' key in model_id"
            assert mid["global"], f"{name}: 'global' key is empty"
            assert mid["global"].startswith("global."), \
                f"{name}: global model_id should start with 'global.', got {mid['global']}"

    def test_region_router_prefix_includes_global(self):
        """The region router must map 'global' to at least one region."""
        from app.providers.bedrock_region_router import _PREFIX_TO_REGIONS
        assert "global" in _PREFIX_TO_REGIONS
        assert len(_PREFIX_TO_REGIONS["global"]) >= 1
