"""
Regression coverage for the Claude Opus 5.5 additions (2026-09-23).

Verified live against Bedrock the day this landed:
  - anthropic.claude-opus-5-5 ("Claude Opus 5.5") is a foundation model with
    us./eu./global. inference profiles (us-east-1, us-west-2, eu-west-1) and
    TEXT+IMAGE input.

These tests pin three things the addition had to get right, and one seam the
*previous* Opus addition (opus5) got wrong:

  1. The entries exist on both endpoints with the correct model_id.
  2. The `opus` alias resolves to the newest Opus, not a stale older one.
  3. The `large` tier resolves to Opus 5.5 on both endpoints.
  4. SEAM: no two models on the same endpoint share a tier tag. opus5 was
     added carrying tier="large" while opus4.8 kept it too, so first-match
     resolution silently pinned `large` to opus4.8. A per-endpoint
     uniqueness invariant is what catches that class of mistake.
"""

import pytest

from app.config.models_config import (
    MODEL_CONFIGS, MODEL_ALIASES, resolve_tier_model,
)


class TestOpus55Entries:
    def test_bedrock_entry_exists_with_expected_model_ids(self):
        cfg = MODEL_CONFIGS["bedrock"].get("opus5.5")
        assert cfg is not None, "bedrock opus5.5 entry missing"
        assert cfg["model_id"] == {
            "us": "us.anthropic.claude-opus-5-5",
            "eu": "eu.anthropic.claude-opus-5-5",
            "global": "global.anthropic.claude-opus-5-5",
        }
        assert cfg["family"] == "claude"
        assert cfg["supports_vision"] is True
        # Thinking is always on for 5.5: 'none' must NOT be an offered effort.
        assert "none" not in cfg["supported_efforts"]

    def test_anthropic_entry_exists_with_expected_model_id(self):
        cfg = MODEL_CONFIGS["anthropic"].get("claude-opus-5-5")
        assert cfg is not None, "anthropic claude-opus-5-5 entry missing"
        assert cfg["model_id"] == "claude-opus-5-5"
        assert cfg["family"] == "claude"
        assert cfg["supports_vision"] is True


class TestOpusAliasTracksNewest:
    def test_bedrock_opus_alias_points_at_5_5(self):
        assert MODEL_ALIASES["bedrock"]["opus"] == "opus5.5"

    def test_anthropic_opus_alias_points_at_5_5(self):
        assert MODEL_ALIASES["anthropic"]["opus"] == "claude-opus-5-5"

    def test_alias_targets_resolve_to_real_models(self):
        for endpoint in ("bedrock", "anthropic"):
            target = MODEL_ALIASES[endpoint]["opus"]
            assert target in MODEL_CONFIGS[endpoint], (
                f"{endpoint} opus alias -> {target!r} is not a real model"
            )


class TestLargeTierTracksNewest:
    def test_bedrock_large_resolves_to_5_5(self):
        assert resolve_tier_model("bedrock", "large") == "opus5.5"

    def test_anthropic_large_resolves_to_5_5(self):
        assert resolve_tier_model("anthropic", "large") == "claude-opus-5-5"


class TestTierTagUniquePerEndpoint:
    """The seam the opus5 addition slipped through: two entries sharing a
    tier tag makes resolve_tier_model order-dependent and silently stale."""

    def test_no_duplicate_tier_tags_within_an_endpoint(self):
        for endpoint, models in MODEL_CONFIGS.items():
            seen: dict[str, str] = {}
            for name, cfg in models.items():
                t = cfg.get("tier")
                if not t:
                    continue
                assert t not in seen, (
                    f"{endpoint}: tier {t!r} tagged on both {seen[t]!r} and "
                    f"{name!r}; resolve_tier_model would silently pick the "
                    f"first and ignore the other"
                )
                seen[t] = name
