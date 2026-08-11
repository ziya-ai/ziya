"""Tests for app.utils.model_override -- per-request model pin validation
and prompt model_info patching (per-conversation / per-project model pins).
"""
import pytest

from app.utils.model_override import (
    validate_model_override,
    apply_model_info_override,
    resolve_override_endpoint,
)
from app.agents.models import ModelManager


def _first_bedrock_model() -> str:
    models = ModelManager.MODEL_CONFIGS.get("bedrock", {})
    assert models, "expected bedrock models in MODEL_CONFIGS"
    return next(iter(models))


class TestValidateModelOverride:
    def test_valid_model_passes(self):
        assert validate_model_override(_first_bedrock_model(), "bedrock") is None

    def test_unknown_model_rejected(self):
        err = validate_model_override("definitely-not-a-model", "bedrock")
        assert err is not None
        assert "not defined on any configured endpoint" in err


class TestCrossEndpointPins:
    """Cross-endpoint pins are honored via the executor's endpoint_override.

    This rests on model aliases being globally unique — pinned models carry
    no endpoint of their own, so a collision would route to whichever
    endpoint happened to be found first.
    """

    def test_model_aliases_are_globally_unique(self):
        owners: dict[str, list[str]] = {}
        for ep, models in ModelManager.MODEL_CONFIGS.items():
            for m in models:
                owners.setdefault(m, []).append(ep)
        dupes = {m: eps for m, eps in owners.items() if len(eps) > 1}
        assert not dupes, (
            f"Model aliases {dupes} exist on multiple endpoints. Pins identify "
            f"a model by alias alone and derive the endpoint from it, so this "
            f"collision makes pin routing ambiguous. Either rename one alias "
            f"or extend the pin payload to carry an explicit endpoint."
        )

    def test_resolve_prefers_active_endpoint(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        model = _first_bedrock_model()
        assert resolve_override_endpoint(model) == "bedrock"

    def test_resolve_finds_other_endpoint(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        google = next(iter(ModelManager.MODEL_CONFIGS.get("google", {})), None)
        if google is None:
            pytest.skip("no google models configured")
        assert resolve_override_endpoint(google) == "google"

    def test_resolve_unknown_is_none(self):
        assert resolve_override_endpoint("not-a-model-xyz") is None

    def test_cross_endpoint_pin_accepted_when_permitted(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        monkeypatch.setenv("ZIYA_ALLOW_ALL_ENDPOINTS", "1")
        google = next(iter(ModelManager.MODEL_CONFIGS.get("google", {})), None)
        if google is None:
            pytest.skip("no google models configured")
        import app.utils.provider_detection as pdmod
        monkeypatch.setattr(pdmod, "get_availability", lambda refresh=False: {"google": True})
        assert validate_model_override(google, "bedrock") is None

    def test_cross_endpoint_pin_rejected_without_credentials(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        monkeypatch.setenv("ZIYA_ALLOW_ALL_ENDPOINTS", "1")
        google = next(iter(ModelManager.MODEL_CONFIGS.get("google", {})), None)
        if google is None:
            pytest.skip("no google models configured")
        import app.utils.provider_detection as pdmod
        monkeypatch.setattr(pdmod, "get_availability", lambda refresh=False: {"google": False})
        err = validate_model_override(google, "bedrock")
        assert err is not None and "no credentials" in err

    def test_empty_model_rejected(self):
        assert validate_model_override("", "bedrock") is not None
        assert validate_model_override(None, "bedrock") is not None

    def test_non_string_rejected(self):
        assert validate_model_override(123, "bedrock") is not None  # type: ignore[arg-type]

    def test_unknown_endpoint_rejected(self):
        err = validate_model_override(_first_bedrock_model(), "no-such-endpoint")
        assert err is not None

    def test_user_allowlist_enforced(self, monkeypatch):
        """A per-request pin must not bypass the personal model allowlist."""
        import app.config.models_config as mc
        model = _first_bedrock_model()
        monkeypatch.setattr(mc, "get_user_allowed_models", lambda: ["some-other-model"])
        err = validate_model_override(model, "bedrock")
        assert err is not None
        assert "allowed model list" in err

    def test_user_allowlist_permits_listed_model(self, monkeypatch):
        import app.config.models_config as mc
        model = _first_bedrock_model()
        monkeypatch.setattr(mc, "get_user_allowed_models", lambda: [model])
        assert validate_model_override(model, "bedrock") is None

    def test_no_allowlist_means_all_configured_models(self, monkeypatch):
        import app.config.models_config as mc
        monkeypatch.setattr(mc, "get_user_allowed_models", lambda: None)
        assert validate_model_override(_first_bedrock_model(), "bedrock") is None


class TestApplyModelInfoOverride:
    def test_patches_name_and_family(self):
        model = _first_bedrock_model()
        cfg = ModelManager.MODEL_CONFIGS["bedrock"][model]
        info = {"model_name": "global-model", "model_family": "claude", "endpoint": "bedrock"}
        patched = apply_model_info_override(info, model)
        assert patched["model_name"] == model
        assert patched["model_family"] == cfg.get("family")
        assert patched["endpoint"] == "bedrock"

    def test_original_dict_not_mutated(self):
        info = {"model_name": "global-model", "model_family": "claude", "endpoint": "bedrock"}
        apply_model_info_override(info, _first_bedrock_model())
        assert info["model_name"] == "global-model"
        assert info["model_family"] == "claude"

    def test_unknown_model_yields_none_family(self):
        # Mirrors get_model_info_from_config: family is None when the
        # model has no config entry -- never inherit the OLD model's family.
        info = {"model_name": "g", "model_family": "claude", "endpoint": "bedrock"}
        patched = apply_model_info_override(info, "not-a-model")
        assert patched["model_family"] is None
        assert patched["model_name"] == "not-a-model"
