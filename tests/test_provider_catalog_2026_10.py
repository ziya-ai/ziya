"""
Provider model-table and price parity pass of 2026-10-01 (every endpoint
except Google, which tests/test_gemini_catalog_2026_10.py covers).

Sources, all read 2026-10-01:
  * platform.claude.com model-deprecations + per-model pages
  * platform.openai.com/docs/{deprecations,pricing,models}
  * docs.aws.amazon.com/bedrock model-lifecycle-legacy + model cards
  * docs.z.ai/guides/overview/pricing
  * dev.meta.ai/docs/pricing-rate-limits
  * the live /models endpoints of OpenAI, Anthropic, z.ai and Meta, using
    the keys in ~/.env -- captured below as LIVE_IDS so the test asserts the
    seam between what Ziya ships and what the provider actually serves.

What fails against the pre-update table, and why it matters:
  * gpt-5.5-mini / gpt-5.5-nano were registered but never existed -- they
    were the openai `small` / `xsmall` rungs AND the openai service default,
    so every background task on that endpoint 404'd.
  * Bedrock claude-3-haiku / claude-3-sonnet are past EOL and claude-sonnet-4
    dies 2026-10-14; the `haiku` / `sonnet` entry names shadowed the aliases
    of the same name, so "haiku" selected a dead model.
  * o3 / o3-mini / o4-mini / gpt-4o / gpt-5.4-nano and claude-sonnet-4-5 all
    have provider shutdown dates.
  * claude-sonnet-5-5, gpt-6*, glm-5.3*, muse-spark-1.3* were missing, and
    every OpenAI 5.4+ / z.ai 5.x / Meta model was unpriced.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.config.models_config import (
    DEFAULT_MODELS,
    DEFAULT_SERVICE_MODELS,
    MODEL_ALIASES,
    MODEL_CONFIGS,
    SERVICE_MODEL_OVERRIDES,
    resolve_tier_model,
    validate_model_configs,
)

pytestmark = pytest.mark.usefixtures("isolated_home")


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "ziya_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("ZIYA_HOME", str(home))
    from app.config import pricing
    pricing.reset_catalog_cache()
    yield home
    pricing.reset_catalog_cache()


# ── live /models snapshots (2026-10-01) ──────────────────────────────────────

LIVE_IDS = {
    "openai": {
        "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "gpt-4o-mini",
        "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro", "gpt-5.1", "gpt-5.2",
        "gpt-5.2-pro", "gpt-5.3-codex", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
        "gpt-5.4-pro", "gpt-5.5", "gpt-5.5-pro",
        "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra",
        "gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol",
        "o1", "o1-pro", "o3", "o3-mini", "o4-mini",
    },
    "anthropic": {
        "claude-fable-5", "claude-fable-5-1", "claude-haiku-4-5-20251001",
        "claude-opus-4-5-20251101", "claude-opus-4-6", "claude-opus-4-7",
        "claude-opus-4-8", "claude-opus-5", "claude-opus-5-5",
        "claude-sonnet-4-5-20250929", "claude-sonnet-4-6", "claude-sonnet-5",
        "claude-sonnet-5-5",
        # Released 2026-10-07; added by the 2026-10-09 sweep.
        "claude-haiku-5-5",
    },
    "zai": {
        "glm-4.5", "glm-4.5-air", "glm-4.6", "glm-4.7", "glm-5", "glm-5-turbo",
        "glm-5.1", "glm-5.2", "glm-5.3", "glm-5.3-flash", "glm-5.3-flashx",
    },
    "meta": {
        "muse-image-1.0", "muse-spark-1.1", "muse-spark-1.2",
        "muse-spark-1.2-contributor", "muse-spark-1.3",
        "muse-spark-1.3-contributor", "muse-voice-transcribe-1.0", "sam-3.1",
    },
}

# Restricted-access models the provider serves but does not list for this
# account; registered on the strength of the provider's model page.
RESTRICTED = {"anthropic": {"claude-mythos-5", "claude-mythos-5-1"}}

# Ids with a provider-announced shutdown (or already past it).
DEPRECATED = {
    "openai": {"o3", "o3-mini", "o4-mini", "gpt-4o", "gpt-5.4-nano",
               "gpt-5.5-mini", "gpt-5.5-nano"},   # last two never existed
    "anthropic": {"claude-sonnet-4-5-20250929"},
    "bedrock": {"anthropic.claude-3-haiku-20240307-v1:0",
                "anthropic.claude-3-sonnet-20240229-v1:0",
                "anthropic.claude-sonnet-4-20250514-v1:0",
                "anthropic.claude-opus-4-1-20250805-v1:0"},
}


def _ids(endpoint):
    out = {}
    for key, cfg in MODEL_CONFIGS[endpoint].items():
        mid = cfg["model_id"]
        out[key] = set(mid.values()) if isinstance(mid, dict) else {mid}
    return out


# ── seam: shipped == served ─────────────────────────────────────────────────

@pytest.mark.parametrize("endpoint", sorted(LIVE_IDS))
def test_every_shipped_model_is_served_by_the_provider(endpoint):
    missing = []
    for key, ids in _ids(endpoint).items():
        for mid in ids:
            if mid not in LIVE_IDS[endpoint] and mid not in RESTRICTED.get(endpoint, set()):
                missing.append((key, mid))
    assert not missing, f"{endpoint}: configured but not on /models: {missing}"


@pytest.mark.parametrize("endpoint", sorted(DEPRECATED))
def test_no_deprecated_id_remains_a_config_entry(endpoint):
    present = []
    for key, ids in _ids(endpoint).items():
        for mid in ids:
            bare = mid.split(".", 1)[1] if endpoint == "bedrock" and mid.count(".") >= 2 else mid
            if bare in DEPRECATED[endpoint] or mid in DEPRECATED[endpoint]:
                present.append((key, mid))
    assert not present, f"{endpoint}: deprecated ids still registered: {present}"


@pytest.mark.parametrize("endpoint, old", [
    ("bedrock", "sonnet4.0"), ("bedrock", "opus4.1"), ("bedrock", "opus4"),
    ("bedrock", "sonnet"), ("bedrock", "haiku"),
    ("openai", "gpt-5.5-mini"), ("openai", "gpt-5.5-nano"), ("openai", "gpt-5.4-nano"),
    ("openai", "o3"), ("openai", "o3-mini"), ("openai", "o4-mini"), ("openai", "gpt-4o"),
    ("anthropic", "claude-sonnet-4-5"),
])
def test_removed_names_resolve_through_aliases_to_live_entries(endpoint, old):
    """A saved setting naming a retired model must land on a live one.
    `sonnet` / `haiku` were entry names that shadowed aliases of the same
    name; now the alias is what resolves."""
    from app.agents.models import ModelManager
    assert old not in MODEL_CONFIGS[endpoint], f"{old} should no longer be an entry"
    target = MODEL_ALIASES[endpoint][old]
    assert target in MODEL_CONFIGS[endpoint]
    resolved = ModelManager.get_model_config(endpoint, old)
    assert resolved["model_id"] == MODEL_CONFIGS[endpoint][target]["model_id"], (old, target)
    # Positive: the target is itself live (not another retiree).
    tgt_ids = _ids(endpoint)[target]
    if endpoint in LIVE_IDS:
        assert tgt_ids & LIVE_IDS[endpoint], (target, tgt_ids)
    assert not (tgt_ids & DEPRECATED.get(endpoint, set()))


def test_bedrock_haiku_and_sonnet_aliases_now_win():
    from app.agents.models import ModelManager
    assert "haiku-4-5" in ModelManager.get_model_config("bedrock", "haiku")["model_id"]["us"]
    assert "sonnet-4-6" in ModelManager.get_model_config("bedrock", "sonnet")["model_id"]["us"]


# ── additions ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("endpoint, key, mid", [
    ("bedrock", "sonnet5.5", "us.anthropic.claude-sonnet-5-5"),
    ("bedrock", "mythos5.1", "us.anthropic.claude-mythos-5-1"),
    ("bedrock", "gpt-6.1-sol", "us.openai.gpt-6.1-sol"),
    ("anthropic", "claude-sonnet-5-5", "claude-sonnet-5-5"),
    ("anthropic", "claude-mythos-5-1", "claude-mythos-5-1"),
    ("openai", "gpt-6-astra", "gpt-6-astra"), ("openai", "gpt-6.1-sol", "gpt-6.1-sol"),
    ("openai", "gpt-6-sol", "gpt-6-sol"), ("openai", "gpt-6-luna", "gpt-6-luna"),
    ("openai", "gpt-5.6-sol", "gpt-5.6-sol"), ("openai", "gpt-5.6-terra", "gpt-5.6-terra"),
    ("openai", "gpt-5.6-luna", "gpt-5.6-luna"),
    ("zai", "glm-5.3", "glm-5.3"), ("zai", "glm-5.3-flash", "glm-5.3-flash"),
    ("meta", "muse-spark-1.3", "muse-spark-1.3"),
    ("meta", "muse-spark-1.3-contributor", "muse-spark-1.3-contributor"),
])
def test_new_entries_registered_with_expected_id(endpoint, key, mid):
    cfg = MODEL_CONFIGS[endpoint][key]
    ids = set(cfg["model_id"].values()) if isinstance(cfg["model_id"], dict) else {cfg["model_id"]}
    assert mid in ids


def test_new_claude_entries_carry_1m_context_and_sampling_restriction():
    for ep, key in (("bedrock", "sonnet5.5"), ("bedrock", "mythos5.1"),
                    ("anthropic", "claude-sonnet-5"), ("anthropic", "claude-sonnet-5-5"),
                    ("anthropic", "claude-mythos-5-1")):
        cfg = MODEL_CONFIGS[ep][key]
        assert cfg["token_limit"] == 1_000_000, (ep, key)
        assert cfg["max_output_tokens"] == 128_000, (ep, key)
        assert cfg["supports_adaptive_thinking"], (ep, key)
        assert set(cfg["unsupported_parameters"]) == {"temperature", "top_k", "top_p"}, (ep, key)


def test_contributor_tiers_are_never_routable_implicitly():
    meta = MODEL_CONFIGS["meta"]
    for key in ("muse-spark-1.2-contributor", "muse-spark-1.3-contributor"):
        assert meta[key]["shares_data_for_training"] is True
        assert "tier" not in meta[key]
    assert not any(v.endswith("-contributor") for k, v in MODEL_ALIASES["meta"].items()
                   if k != "muse-contributor")


# ── defaults, rungs, service models ─────────────────────────────────────────

@pytest.mark.parametrize("endpoint, expected", [
    ("openai", "gpt-6.1-sol"), ("anthropic", "claude-sonnet-5-5"),
    ("zai", "glm-5.3"), ("meta", "muse-spark-1.3"), ("bedrock", "opus4.8"),
])
def test_endpoint_defaults(endpoint, expected):
    assert DEFAULT_MODELS[endpoint] == expected
    assert expected in MODEL_CONFIGS[endpoint]


@pytest.mark.parametrize("endpoint", ["bedrock", "openai", "anthropic", "zai", "meta", "google"])
def test_every_rung_resolves_and_tiers_are_unique(endpoint):
    by_tier = {}
    for key, cfg in MODEL_CONFIGS[endpoint].items():
        t = cfg.get("tier")
        if t:
            assert t not in by_tier, f"{endpoint}: {key} and {by_tier[t]} both claim {t!r}"
            by_tier[t] = key
    for rung in ("xsmall", "small", "medium", "large", "frontier"):
        assert resolve_tier_model(endpoint, rung) in MODEL_CONFIGS[endpoint], (endpoint, rung)


def test_openai_rungs_land_on_the_gpt6_line():
    assert resolve_tier_model("openai", "medium") == "gpt-6.1-sol"
    assert resolve_tier_model("openai", "large") == "gpt-6-astra"
    assert resolve_tier_model("openai", "small") == "gpt-6-luna"
    assert resolve_tier_model("openai", "xsmall") == "gpt-6-luna"   # rounds up


def test_service_models_are_real_model_ids():
    """DEFAULT_SERVICE_MODELS / SERVICE_MODEL_OVERRIDES hold raw model ids;
    each must be the model_id of some entry on its endpoint (and be served)."""
    def served(ep, mid):
        for ids in _ids(ep).values():
            if mid in ids:
                return mid in LIVE_IDS.get(ep, {mid})
        return False
    for ep, mid in DEFAULT_SERVICE_MODELS.items():
        if ep == "local":
            continue
        assert served(ep, mid), ("DEFAULT_SERVICE_MODELS", ep, mid)
    for cat, by_ep in SERVICE_MODEL_OVERRIDES.items():
        for ep, mid in by_ep.items():
            if ep == "bedrock":
                # Bedrock service ids are historically written without the
                # us. prefix in some rows; accept either form.
                assert any(mid in ids or any(i.endswith(mid) for i in ids)
                           for ids in _ids(ep).values()), (cat, ep, mid)
            else:
                assert served(ep, mid), (cat, ep, mid)


def test_config_validates_clean_for_new_entries():
    new = ("sonnet5.5", "mythos5.1", "gpt-6.1-sol", "claude-sonnet-5-5", "claude-mythos-5-1",
           "gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-5.6-", "glm-5.3", "muse-spark-1.3")
    issues = [i for i in validate_model_configs() if any(n in i for n in new)]
    assert not issues, issues


# ── pricing parity ──────────────────────────────────────────────────────────

def _price(provider, model_id, dims=None, ts=None):
    from app.config.pricing import build_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import PublicPlan
    from app.storage.usage_ledger import UsageRecord
    rec = UsageRecord(id="r", ts=ts or time.time(), provider=provider, model_id=model_id,
                      region=None, tier="standard", status="actual",
                      dims=dims or {"input": 100_000, "output": 100_000, "cache_read": 100_000})
    return price_record(rec, build_catalog(user_file=Path("/nonexistent")), PublicPlan())


@pytest.mark.parametrize("provider, model_id, inp, out, cache", [
    ("openai", "gpt-6-astra", 10.0, 50.0, 1.0),
    ("openai", "gpt-6.1-sol", 2.0, 10.0, 0.10),
    ("openai", "gpt-6-sol", 2.0, 10.0, 0.20),
    ("openai", "gpt-6-luna", 0.10, 0.50, 0.01),
    ("openai", "gpt-5.6-sol", 4.0, 20.0, 0.40),
    ("openai", "gpt-5.6-terra", 2.0, 12.0, 0.20),
    ("openai", "gpt-5.6-luna", 0.20, 1.20, 0.02),
    ("openai", "gpt-5.5", 5.0, 30.0, 0.50),
    ("openai", "gpt-5.4", 2.50, 15.0, 0.25),
    ("openai", "gpt-5.4-mini", 0.75, 4.50, 0.075),
    ("zai", "glm-5.3", 1.40, 4.40, 0.26),
    ("zai", "glm-5.2", 1.40, 4.40, 0.26),
    ("zai", "glm-5.3-flash", 0.15, 0.50, 0.03),
    ("zai", "glm-5.3-flashx", 0.37, 1.25, 0.075),
    ("zai", "glm-5", 1.0, 3.20, 0.20),
    ("zai", "glm-4.7", 0.60, 2.20, 0.11),
    ("meta", "muse-spark-1.3", 1.25, 4.25, 0.15),
    ("meta", "muse-spark-1.2", 1.25, 4.25, 0.15),
    ("meta", "muse-spark-1.1", 1.25, 4.25, 0.15),
    ("meta", "muse-spark-1.3-contributor", 0.10, 0.20, 0.002),
    ("meta", "muse-spark-1.2-contributor", 0.10, 0.20, 0.002),
    # Cache read is 0.05x on Sonnet 5.5 (pricing page, re-read 2026-10-09);
    # the 2026-10-01 read showed no cell and assumed Sonnet 5's 0.20.
    ("anthropic", "claude-sonnet-5-5", 2.0, 10.0, 0.10),
    ("anthropic", "claude-sonnet-5", 2.0, 10.0, 0.20),
    ("anthropic", "claude-mythos-5-1", 10.0, 50.0, 0.25),
])
def test_standard_rates_match_provider_pricing_pages(provider, model_id, inp, out, cache):
    line = _price(provider, model_id)
    assert line.provenance == "list" and not line.is_partial, (model_id, line.unpriced_dims)
    assert line.rates_applied["input"] == pytest.approx(inp), model_id
    assert line.rates_applied["output"] == pytest.approx(out), model_id
    assert line.rates_applied["cache_read"] == pytest.approx(cache), model_id
    assert not line.long_context_applied


def test_openai_pro_cards_have_no_cached_rate_and_say_so():
    for mid in ("gpt-5.5-pro", "gpt-5.4-pro"):
        line = _price("openai", mid)
        assert line.rates_applied["input"] == pytest.approx(30.0)
        assert line.rates_applied["output"] == pytest.approx(180.0)
        assert line.is_partial and line.unpriced_dims == ["cache_read"], mid


def test_openai_272k_step_up_doubles_input_and_cache_and_1_5x_output():
    base = _price("openai", "gpt-6-astra", dims={"input": 200_000, "output": 10_000,
                                                  "cache_read": 10_000, "cache_write_5m": 1_000})
    big = _price("openai", "gpt-6-astra", dims={"input": 300_000, "output": 10_000,
                                                 "cache_read": 10_000, "cache_write_5m": 1_000})
    assert not base.long_context_applied and big.long_context_applied
    assert base.rates_applied["cache_write_5m"] == pytest.approx(12.50)
    assert big.rates_applied == pytest.approx({"input": 20.0, "output": 75.0,
                                               "cache_read": 2.0, "cache_write_5m": 25.0})
    # The threshold counts the whole prompt, not just uncached input.
    mixed = _price("openai", "gpt-6.1-sol", dims={"input": 100_000, "cache_read": 200_000, "output": 0})
    assert mixed.long_context_applied and mixed.rates_applied["input"] == pytest.approx(4.0)


def test_openai_tier_factors_apply_to_new_cards():
    from app.storage.usage_ledger import UsageRecord
    from app.config.pricing import build_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import PublicPlan
    cat = build_catalog(user_file=Path("/nonexistent"))
    std = price_record(UsageRecord(id="r", ts=time.time(), provider="openai", model_id="gpt-6-luna",
                                   region=None, tier="standard", status="actual",
                                   dims={"input": 1_000_000}), cat, PublicPlan()).list_cost
    for tier, f in (("batch", 0.5), ("flex", 0.5), ("priority", 2.0)):
        line = price_record(UsageRecord(id="r", ts=time.time(), provider="openai", model_id="gpt-6-luna",
                                        region=None, tier=tier, status="actual",
                                        dims={"input": 1_000_000}), cat, PublicPlan())
        assert line.list_cost == pytest.approx(std * f), tier


def test_globs_stay_disjoint_across_sibling_ids():
    # z.ai: exact names, so 5.3 must not price its -flash / -flashx siblings.
    assert _price("zai", "glm-5.3").rates_applied["input"] == pytest.approx(1.40)
    assert _price("zai", "glm-5.3-flash").rates_applied["input"] == pytest.approx(0.15)
    assert _price("zai", "glm-4.7-flash").list_cost == 0.0
    # Meta: standard vs contributor must not depend on list order.
    assert _price("meta", "muse-spark-1.3").rates_applied["input"] == pytest.approx(1.25)
    assert _price("meta", "muse-spark-1.3-contributor").rates_applied["input"] == pytest.approx(0.10)
    # Non-text Meta products stay unpriced.
    assert _price("meta", "muse-image-1.0").list_cost is None
    # OpenAI: the 5.5 glob must not catch 5.5-pro, nor 6-sol catch 6.1-sol.
    assert _price("openai", "gpt-5.5-pro").rates_applied["input"] == pytest.approx(30.0)
    assert _price("openai", "gpt-6.1-sol").rates_applied["cache_read"] == pytest.approx(0.10)
    assert _price("openai", "gpt-6-sol").rates_applied["cache_read"] == pytest.approx(0.20)


@pytest.mark.parametrize("endpoint", ["openai", "anthropic", "zai", "meta"])
def test_every_shipped_model_on_direct_endpoints_has_a_full_price(endpoint):
    unpriced = []
    for key, cfg in MODEL_CONFIGS[endpoint].items():
        line = _price(endpoint, cfg["model_id"])
        if line.list_cost is None or line.is_partial:
            # The two OpenAI pro cards publish no cached-input rate; that is
            # a documented partial, not a gap.
            if endpoint == "openai" and key.endswith("-pro") and line.unpriced_dims == ["cache_read"]:
                continue
            unpriced.append((key, cfg["model_id"], line.unpriced_dims))
    assert not unpriced, unpriced


def test_catalog_revision_bumped_for_this_pass():
    from app.config.pricing import CATALOG_REVISION
    assert CATALOG_REVISION > "2026-10-01"


# ── 2026-10-09 sweep: live-verified additions and the OpenAI seams ──────────

@pytest.mark.parametrize("endpoint, key, mid", [
    ("bedrock", "haiku-5.5", "us.anthropic.claude-haiku-5-5"),
    ("bedrock", "grok-4.7", "us.xai.grok-4.7"),
    ("bedrock", "glm-5.3", "us.zai.glm-5.3"),
    ("anthropic", "claude-haiku-5-5", "claude-haiku-5-5"),
])
def test_2026_10_09_additions(endpoint, key, mid):
    cfg = MODEL_CONFIGS[endpoint][key]
    ids = set(cfg["model_id"].values()) if isinstance(cfg["model_id"], dict) else {cfg["model_id"]}
    assert mid in ids
    # New models must not take a rung or alias implicitly (decision pending).
    assert "tier" not in cfg
    assert key not in MODEL_ALIASES[endpoint].values()


def test_profile_only_bedrock_models_use_converse_not_the_invoke_wrapper():
    """glm-5.3 / grok-4.7 / gpt-6.1-sol exist only as us./global. profiles;
    the OpenAIBedrock invoke wrapper (glm and kimi families) cannot take them."""
    from app.agents.models import ModelManager
    for key in ("glm-5.3", "grok-4.7", "gpt-6.1-sol", "kimi-k3"):
        cfg = ModelManager.get_model_config("bedrock", key)
        assert cfg.get("wrapper_class") is None, (key, cfg.get("wrapper_class"))


def test_temperature_rejecting_models_declare_it():
    """Live 2026-10-09: each of these 400s on a non-default temperature."""
    for ep, key in (("bedrock", "haiku-5.5"), ("bedrock", "grok-4.7"),
                    ("bedrock", "gpt-6.1-sol"), ("anthropic", "claude-haiku-5-5"),
                    ("anthropic", "claude-sonnet-5-5"), ("openai", "gpt-6.1-sol"),
                    ("openai", "gpt-6-luna"), ("openai", "gpt-5.6-terra"),
                    ("openai", "gpt-5.5")):
        assert "temperature" in MODEL_CONFIGS[ep][key]["unsupported_parameters"], (ep, key)
    # Positive: glm-5.3 accepts it and must keep sending it.
    assert "temperature" not in MODEL_CONFIGS["bedrock"]["glm-5.3"].get("unsupported_parameters", [])


def test_openai_direct_request_uses_max_completion_tokens_and_drops_temperature():
    """The request OpenAIDirectProvider builds from the config ModelManager
    hands it (ENDPOINT_DEFAULTS merged).  GPT-5.x/6.x 400 on max_tokens."""
    import sys
    from unittest.mock import MagicMock, patch
    from app.agents.models import ModelManager
    from app.providers.base import ProviderConfig
    with patch.dict(sys.modules, {"openai": MagicMock()}):
        from app.providers.openai_direct import OpenAIDirectProvider
        for key in ("gpt-6.1-sol", "gpt-4.1"):
            cfg = ModelManager.get_model_config("openai", key)
            p = OpenAIDirectProvider(model_id=cfg["model_id"], model_config=cfg, api_key="sk-x")
            req = p._build_request([{"role": "user", "content": "hi"}], None, [],
                                   ProviderConfig(max_output_tokens=64, temperature=0.3))
            assert req["max_completion_tokens"] == 64 and "max_tokens" not in req, key
            if key == "gpt-6.1-sol":
                assert "temperature" not in req
            else:
                assert req["temperature"] == 0.3   # 4.1 still takes it


def test_non_openai_compatible_endpoints_keep_max_tokens():
    import sys
    from unittest.mock import MagicMock, patch
    from app.agents.models import ModelManager
    from app.providers.base import ProviderConfig
    with patch.dict(sys.modules, {"openai": MagicMock()}):
        from app.providers.openai_direct import OpenAIDirectProvider
        cfg = ModelManager.get_model_config("zai", "glm-5.3")
        p = OpenAIDirectProvider(model_id=cfg["model_id"], model_config=cfg, api_key="k")
        req = p._build_request([{"role": "user", "content": "hi"}], None, [],
                               ProviderConfig(max_output_tokens=64, temperature=0.3))
        assert req["max_tokens"] == 64 and req["temperature"] == 0.3


@pytest.mark.asyncio
async def test_service_call_on_openai_sends_neither_max_tokens_nor_temperature(monkeypatch):
    """The background path (memory extraction, intent judge) to gpt-6-luna."""
    import sys
    from unittest.mock import MagicMock
    from app.services import model_resolver
    sent = {}
    client = MagicMock()

    def create(**kw):
        sent.update(kw)
        r = MagicMock()
        r.choices = [MagicMock(message=MagicMock(content="ok"))]
        return r
    client.chat.completions.create.side_effect = create
    fake = MagicMock()
    fake.OpenAI.return_value = client
    monkeypatch.setitem(sys.modules, "openai", fake)
    out = await model_resolver._call_openai_compatible(
        {"endpoint": "openai", "model_id": "gpt-6-luna"}, "s", "u", 50, 0.2)
    assert out == "ok"
    assert sent["max_completion_tokens"] == 50
    assert "max_tokens" not in sent and "temperature" not in sent


def test_haiku_5_5_prices_with_its_100k_step_up():
    small = _price("anthropic", "claude-haiku-5-5", dims={"input": 50_000, "output": 1_000})
    assert small.rates_applied["input"] == pytest.approx(0.10) and not small.long_context_applied
    big = _price("anthropic", "claude-haiku-5-5", dims={"input": 150_000, "output": 1_000})
    assert big.long_context_applied
    assert big.rates_applied["input"] == pytest.approx(0.50)
    assert big.rates_applied["output"] == pytest.approx(2.50)
    # Bedrock ids hit the same card.
    assert _price("bedrock", "global.anthropic.claude-haiku-5-5",
                  dims={"input": 1_000}).rates_applied["input"] == pytest.approx(0.10)
