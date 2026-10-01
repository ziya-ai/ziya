"""
Gemini direct models and pricing, brought to parity with Google's published
pages on 2026-10-01 (ai.google.dev/gemini-api/docs/{models,pricing} and the
Gemini 4 Argon announcement).

What these tests pin, and why each one fails against the pre-update code:

* Every Google model Ziya ships has a built-in price (3.1 Pro / 3 Flash /
  3.5 Flash were shipped unpriced before; the 3.6-3.8 line did not exist).
* The 3.6 / 3.7 / 3.8 Flash introductory window ends 2026-12-31 and the
  rate doubles on 2027-01-01 -- asserted through the outermost surface,
  price_record(), at a timestamp on each side of the boundary.
* 2.5 Pro cache-read was carried as $0.31 / $0.625; Google's page says
  $0.125 / $0.25.
* Shut-down ids (gemini-2.0-*) are gone from MODEL_CONFIGS but still
  resolve through MODEL_ALIASES, and no service default points at them.
* The two provider-side thinking_level gates accept a gemini-4 id.
"""
from __future__ import annotations

import ast
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

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


def _catalog():
    from app.config.pricing import build_catalog
    return build_catalog(user_file=Path("/nonexistent/pricing.json"))


def _ts(day: str) -> float:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()


# 100k of each dimension: well under the 200k prompt (input + cache_read)
# threshold at which the Pro cards step up, so the default fixture prices
# at the base rate.  list_cost is therefore 0.1 x (input + output + cache).
_DIMS = {"input": 100_000, "output": 100_000, "cache_read": 100_000}


def _rec(model_id: str, ts: float, tier: str = "standard", dims=None):
    from app.storage.usage_ledger import UsageRecord
    return UsageRecord(
        id="r", ts=ts, provider="google", model_id=model_id, region=None,
        tier=tier, status="actual", dims=dims or dict(_DIMS),
    )


def _price(model_id: str, day: str = "2026-10-01", tier: str = "standard", dims=None):
    from app.cost.pricer import price_record
    from app.cost.rate_plans import PublicPlan
    return price_record(_rec(model_id, _ts(day), tier, dims), _catalog(), PublicPlan())


# ── seam: every shipped Google model is priced ──────────────────────────────

def test_every_google_model_config_has_a_builtin_price():
    from app.config.models_config import MODEL_CONFIGS
    unpriced = []
    for key, cfg in MODEL_CONFIGS["google"].items():
        mid = cfg["model_id"]
        if mid.endswith("-latest"):
            # Floating "latest" aliases resolve server-side to whatever
            # Google points them at; a pattern price would be a guess.
            # (gemini-pro-latest is also absent from the models page as of
            # 2026-10-01 -- see the note in Docs/CostAccounting.md.)
            continue
        line = _price(mid)
        if line.list_cost is None or line.is_partial:
            unpriced.append((key, mid, line.unpriced_dims))
    assert not unpriced, f"shipped Google models without a full price: {unpriced}"


def test_shut_down_ids_are_aliases_not_entries_and_no_service_default_uses_them():
    from app.config.models_config import (
        MODEL_CONFIGS, MODEL_ALIASES, DEFAULT_SERVICE_MODELS, SERVICE_MODEL_OVERRIDES,
    )
    google = MODEL_CONFIGS["google"]
    for dead in ("gemini-2.0-flash", "gemini-2.0-flash-lite"):
        assert dead not in google, f"{dead} is shut down by Google but still a config entry"
        assert dead in MODEL_ALIASES["google"], f"{dead} must keep resolving via alias"
        assert MODEL_ALIASES["google"][dead] in google
    assert DEFAULT_SERVICE_MODELS["google"] in google
    for category, by_ep in SERVICE_MODEL_OVERRIDES.items():
        if "google" in by_ep:
            assert by_ep["google"] in google, (category, by_ep["google"])
    # Argon is reachable by alias but must not be a tier rung (not public yet).
    assert MODEL_ALIASES["google"]["argon"] == "gemini-4-argon"
    assert "tier" not in google["gemini-4-argon"]


def test_google_tier_rungs_resolve_to_live_models():
    from app.config.models_config import MODEL_CONFIGS, resolve_tier_model
    google = MODEL_CONFIGS["google"]
    for rung in ("xsmall", "small", "medium"):
        assert resolve_tier_model("google", rung) in google
    assert google[resolve_tier_model("google", "xsmall")]["model_id"] == "gemini-3.1-flash-lite"


# ── parity with ai.google.dev/pricing (standard tier, per 1M tokens) ────────

@pytest.mark.parametrize("model_id, inp, out, cache", [
    ("gemini-3.8-flash", 0.75, 3.75, 0.075),
    ("gemini-3.7-flash", 0.75, 3.75, 0.075),
    ("gemini-3.6-flash", 0.75, 3.75, 0.075),
    ("gemini-3.5-flash", 1.50, 9.00, 0.15),
    ("gemini-3.5-flash-lite", 0.30, 2.50, 0.03),
    ("gemini-3.1-flash-lite", 0.25, 1.50, 0.025),
    ("gemini-3.1-pro-preview", 2.00, 12.00, 0.20),
    ("gemini-3.1-pro-preview-customtools", 2.00, 12.00, 0.20),
    ("gemini-3-flash-preview", 0.50, 3.00, 0.05),
    ("gemini-2.5-pro", 1.25, 10.00, 0.125),
    ("gemini-2.5-flash", 0.30, 2.50, 0.03),
    ("gemini-2.5-flash-lite", 0.10, 0.40, 0.01),
    ("gemini-4-argon", 2.00, 10.00, 0.10),
])
def test_standard_rates_match_google_pricing_page(model_id, inp, out, cache):
    line = _price(model_id)
    assert line.rates_applied["input"] == pytest.approx(inp), model_id
    assert line.rates_applied["output"] == pytest.approx(out), model_id
    assert line.rates_applied["cache_read"] == pytest.approx(cache), model_id
    assert not line.long_context_applied, model_id
    assert line.list_cost == pytest.approx(0.1 * (inp + out + cache))


def test_lite_and_variant_globs_do_not_bleed_into_base_cards():
    # The base-model glob must not swallow the cheaper -lite id (or vice
    # versa), independent of catalog list order.
    assert _price("gemini-3.5-flash-lite").rates_applied["input"] == pytest.approx(0.30)
    assert _price("gemini-3.5-flash").rates_applied["input"] == pytest.approx(1.50)
    assert _price("gemini-2.5-flash-lite").rates_applied["input"] == pytest.approx(0.10)
    assert _price("gemini-2.5-flash").rates_applied["input"] == pytest.approx(0.30)
    # TTS / image siblings are different products and must stay unpriced.
    for other in ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-lite-image"):
        assert _price(other).list_cost is None, other


@pytest.mark.parametrize("tier, factor", [("batch", 0.5), ("flex", 0.5), ("priority", 1.8)])
def test_google_tier_factors(tier, factor):
    std = _price("gemini-3.8-flash", tier="standard").list_cost
    tiered = _price("gemini-3.8-flash", tier=tier)
    assert tiered.tier_factor == pytest.approx(factor)
    assert tiered.list_cost == pytest.approx(std * factor)
    # Spot-check the published Priority cells: 3.5 Flash $2.70 / $16.20.
    pri = _price("gemini-3.5-flash", tier="priority")
    assert pri.rates_applied["input"] == pytest.approx(2.70)
    assert pri.rates_applied["output"] == pytest.approx(16.20)


def test_gemini_3x_flash_introductory_window_ends_2026_12_31():
    intro = _price("gemini-3.8-flash", "2026-12-31")
    full = _price("gemini-3.8-flash", "2027-01-01")
    assert intro.rates_applied == pytest.approx({"input": 0.75, "output": 3.75, "cache_read": 0.075})
    assert full.rates_applied == pytest.approx({"input": 1.50, "output": 7.50, "cache_read": 0.15})
    assert full.list_cost == pytest.approx(2 * intro.list_cost)
    # Same window on the two siblings.
    for sib in ("gemini-3.7-flash", "gemini-3.6-flash"):
        assert _price(sib, "2027-01-01").rates_applied["output"] == pytest.approx(7.50), sib


def test_gemini_3_1_pro_long_context_step_up():
    small = _price("gemini-3.1-pro-preview", dims={"input": 150_000, "output": 1_000})
    big = _price("gemini-3.1-pro-preview", dims={"input": 250_000, "output": 1_000})
    assert not small.long_context_applied
    assert big.long_context_applied
    assert big.rates_applied["input"] == pytest.approx(4.0)
    assert big.rates_applied["output"] == pytest.approx(18.0)


def test_gemini_2_5_pro_cache_read_corrected_including_long_context():
    # The >200k threshold counts the whole prompt (input + cache_read).
    small = _price("gemini-2.5-pro", dims={"input": 10_000, "cache_read": 100_000})
    big = _price("gemini-2.5-pro", dims={"input": 10_000, "cache_read": 250_000})
    assert small.rates_applied["cache_read"] == pytest.approx(0.125)
    assert big.rates_applied["cache_read"] == pytest.approx(0.25)


def test_argon_pricing_starts_at_announcement_and_catalog_revision_bumped():
    from app.config.pricing import CATALOG_REVISION
    assert CATALOG_REVISION >= "2026-10-01"
    assert _price("gemini-4-argon", "2026-09-29").list_cost is None
    assert _price("gemini-4-argon", "2026-09-30").list_cost is not None


# ── provider seam: the thinking_level gate accepts the new generation ───────

def _gate_source(path: str) -> str:
    src = Path(path).read_text()
    tree = ast.parse(src)
    # Return the source of every `if` test that mentions "gemini-3".
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "gemini-3" in ast.get_source_segment(src, node.test):
            out.append(ast.get_source_segment(src, node.test))
        if isinstance(node, ast.Assign) and "gemini-3" in (ast.get_source_segment(src, node.value) or ""):
            out.append(ast.get_source_segment(src, node.value))
    return "\n".join(out)


@pytest.mark.parametrize("path", [
    "app/providers/google_direct.py",
    "app/agents/wrappers/google_direct.py",
])
def test_thinking_level_gate_accepts_gemini_4(path):
    gate = _gate_source(path)
    assert gate, f"no gemini-3 gate found in {path}; test needs updating"
    assert re.search(r'["\']gemini-4["\']', gate), (
        f"{path}: thinking_level gate still keys only on 'gemini-3'; "
        f"an Argon id would drop its level:\n{gate}"
    )


def test_argon_and_3x_flash_publish_thinking_level_capability():
    """model_routes publishes supports_thinking_level only for family gemini-3."""
    from app.config.models_config import MODEL_CONFIGS
    google = MODEL_CONFIGS["google"]
    for key in ("gemini-4-argon", "gemini-3.8-flash", "gemini-3.7-flash",
                "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite"):
        assert google[key]["family"] == "gemini-3", key
        assert google[key]["thinking_level"] in ("low", "medium", "high"), key
        assert google[key]["token_limit"] == 1048576, key
    assert google["gemini-4-argon"]["max_output_tokens"] == 1_000_000
