"""
Tests for portable model tiers — app.config.models_config.resolve_tier_model
(scan-based: tiers are per-model ``tier`` tags, NOT a separate table), and
their integration with ModelManager.get_model_config and TaskScope
model-selection fields.

Covers:
  - resolve_tier_model resolution, round-up degradation, and fallback
  - Per-model tier tags are valid rung names and every endpoint has the
    center rung 'medium' (also covered by validate_model_configs —
    tested here for a focused failure message)
  - ModelManager.get_model_config resolves tier names transparently
  - TaskScope model_tier/model_name/model_id_override/model_endpoint
    fields default to None and round-trip
  - merge_scopes: model fields are non-additive, most-specific-wins
    (same rule as cwd)
"""

import pytest

from app.config.models_config import (
    MODEL_TIER_NAMES, MODEL_CONFIGS, DEFAULT_MODELS,
    resolve_tier_model, validate_model_configs,
)
from app.models.task_card import TaskScope, merge_scopes


def _tagged(endpoint: str) -> dict:
    """First-seen model name per tier tag on an endpoint (mirrors the
    resolver's own scan) so tests reference the live config, not a
    hardcoded expectation that would rot with the models."""
    out: dict[str, str] = {}
    for name, cfg in MODEL_CONFIGS.get(endpoint, {}).items():
        t = cfg.get("tier")
        if t and t not in out:
            out[t] = name
    return out


class TestResolveTierModel:
    def test_known_tier_resolves_to_tagged_model(self):
        tags = _tagged("bedrock")
        assert "small" in tags, "test precondition: bedrock has a 'small'-tagged model"
        assert resolve_tier_model("bedrock", "small") == tags["small"]

    def test_center_tier_resolves(self):
        # 'medium' is the center rung: default model + fallback target.
        tags = _tagged("bedrock")
        assert resolve_tier_model("bedrock", "medium") == tags["medium"]

    def test_unmapped_tier_rounds_up(self):
        # Round-up policy: with only 'medium' (idx 2) and 'frontier'
        # (idx 4) tagged, 'large' (idx 3) is distance 1 from both, but
        # rounding up means the at-or-above rung wins → frontier (never
        # silently under-serve a task with a weaker model than requested).
        import app.config.models_config as mc
        saved = mc.MODEL_CONFIGS.get("_tiertest")
        mc.MODEL_CONFIGS["_tiertest"] = {
            "cheap": {"tier": "medium"},
            "dear": {"tier": "frontier"},
        }
        try:
            assert mc.resolve_tier_model("_tiertest", "large") == "dear"
        finally:
            if saved is None:
                del mc.MODEL_CONFIGS["_tiertest"]
            else:
                mc.MODEL_CONFIGS["_tiertest"] = saved

    def test_unmapped_tier_falls_down_when_nothing_above(self):
        # Round-up prefers at-or-above, but when the requested rung is
        # higher than anything defined, it falls to the highest available
        # below rather than failing.  Only 'xsmall'/'small' defined;
        # 'frontier' has nothing above → highest below ('small').
        import app.config.models_config as mc
        saved = mc.MODEL_CONFIGS.get("_tiertest2")
        mc.MODEL_CONFIGS["_tiertest2"] = {
            "tiny": {"tier": "xsmall"},
            "cheap": {"tier": "small"},
        }
        try:
            assert mc.resolve_tier_model("_tiertest2", "frontier") == "cheap"
        finally:
            if saved is None:
                del mc.MODEL_CONFIGS["_tiertest2"]
            else:
                mc.MODEL_CONFIGS["_tiertest2"] = saved

    def test_unmapped_tier_exact_ceiling_over_closer_below(self):
        # 'medium' (idx2) with 'large' (idx3, +1 above) and 'xsmall'
        # (idx0, -2 below) defined: round up picks the at-or-above rung
        # (large), not the below rung.  Confirms at/above always beats
        # below regardless of distance.
        import app.config.models_config as mc
        saved = mc.MODEL_CONFIGS.get("_tiertest3")
        mc.MODEL_CONFIGS["_tiertest3"] = {
            "tiny": {"tier": "xsmall"},
            "big": {"tier": "large"},
        }
        try:
            assert mc.resolve_tier_model("_tiertest3", "medium") == "big"
        finally:
            if saved is None:
                del mc.MODEL_CONFIGS["_tiertest3"]
            else:
                mc.MODEL_CONFIGS["_tiertest3"] = saved

    def test_unknown_endpoint_falls_back_to_default_models(self):
        assert resolve_tier_model("some_future_provider", "small") == \
            DEFAULT_MODELS.get("some_future_provider", DEFAULT_MODELS["bedrock"])

    def test_never_raises_on_garbage_tier(self):
        # A totally unknown tier name on a real endpoint degrades rather
        # than raising (cost-control feature must not fail task launch).
        assert isinstance(resolve_tier_model("bedrock", "ludicrous"), str)


class TestTierTagsValid:
    def test_every_endpoint_has_a_center_rung(self):
        # 'medium' is the center/default rung and the resolver fallback
        # target; every endpoint that tags anything must define it.
        for endpoint in MODEL_CONFIGS:
            tags = _tagged(endpoint)
            if tags:  # only meaningful for endpoints that tag anything
                assert "medium" in tags, f"{endpoint} has tier tags but no 'medium'"

    def test_all_tier_tags_are_known_rung_names(self):
        for endpoint, models in MODEL_CONFIGS.items():
            for name, cfg in models.items():
                t = cfg.get("tier")
                if t is not None:
                    assert t in MODEL_TIER_NAMES, (
                        f"{endpoint}/{name} has unknown tier {t!r}"
                    )

    def test_validate_model_configs_reports_no_tier_issues(self):
        issues = validate_model_configs()
        tier_issues = [i for i in issues if i.startswith("[tier/")]
        assert tier_issues == [], f"unexpected tier issues: {tier_issues}"


class TestModelManagerTierResolution:
    def test_get_model_config_resolves_tier_transparently(self):
        from app.agents.models import ModelManager
        resolved_name = _tagged("bedrock")["small"]
        via_tier = ModelManager.get_model_config("bedrock", "small")
        via_name = ModelManager.get_model_config("bedrock", resolved_name)
        assert via_tier.get("model_id") == via_name.get("model_id")

    def test_tier_name_is_not_itself_a_real_model(self):
        # Exact-match models take priority over tier resolution; assert
        # the tier names don't collide with real model keys so the
        # transparent resolution can't be shadowed.
        for tier in MODEL_TIER_NAMES:
            assert tier not in MODEL_CONFIGS["bedrock"]


class TestTaskScopeModelFields:
    def test_defaults_are_none(self):
        scope = TaskScope()
        assert scope.model_tier is None
        assert scope.model_name is None
        assert scope.model_id_override is None
        assert scope.model_endpoint is None

    def test_round_trip(self):
        scope = TaskScope(model_tier="small", model_name=None,
                          model_id_override=None, model_endpoint="bedrock")
        data = scope.model_dump()
        restored = TaskScope(**data)
        assert restored.model_tier == "small"
        assert restored.model_endpoint == "bedrock"


class TestMergeScopesModelFields:
    def test_leaf_overrides_ancestor_tier(self):
        deck = TaskScope(model_tier="large")
        leaf = TaskScope(model_tier="small")
        merged = merge_scopes(deck, leaf)
        assert merged.model_tier == "small"

    def test_ancestor_tier_applies_when_leaf_sets_nothing(self):
        deck = TaskScope(model_tier="large")
        leaf = TaskScope()
        merged = merge_scopes(deck, leaf)
        assert merged.model_tier == "large"

    def test_model_id_override_independent_of_tier(self):
        deck = TaskScope(model_tier="small")
        leaf = TaskScope(model_id_override="arn:aws:bedrock:...:inference-profile/x")
        merged = merge_scopes(deck, leaf)
        # Both fields are independently non-additive; leaf only set
        # model_id_override, so the deck's tier still flows through.
        assert merged.model_tier == "small"
        assert merged.model_id_override == "arn:aws:bedrock:...:inference-profile/x"

    def test_none_layers_do_not_clear_model_fields(self):
        deck = TaskScope(model_tier="large")
        merged = merge_scopes(deck, None)
        assert merged.model_tier == "large"
