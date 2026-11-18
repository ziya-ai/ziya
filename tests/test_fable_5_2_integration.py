"""
Claude Fable 5.2 lands on both Claude endpoints (verified live 2026-11-10).

  * Anthropic direct — GET /v1/models lists ``claude-fable-5-2`` (created
    2026-11-09).  A 16-token Messages call returned 200; the usage block
    reported ``output_tokens_details.thinking_tokens: 16`` with no
    ``thinking`` parameter sent (adaptive thinking on by default), and a
    request carrying ``temperature`` was refused with "`temperature` is
    deprecated for this model" — the same envelope as 5.1.

  * Bedrock — ``anthropic.claude-fable-5-2`` is ACTIVE on the control plane
    in us-east-1 and us-west-2 with ``us.`` and ``global.`` inference
    profiles (``global.`` also listed in eu-west-1 / ap-northeast-1, not
    invoke-probed).  Invoking either profile on classic bedrock-runtime
    returned the retention-mode ValidationException ("data retention mode
    'default' is not available for this model"), which is the fable5.1
    fingerprint: a nonexistent id says "model identifier is invalid".  The
    bare id is refused with "on-demand throughput isn't supported".  So the
    entry is the fable5.1 shape — bedrock-runtime, profiles, classic
    ``aws_review`` gate — not mantle.

  * Pricing — platform.claude.com/docs/en/about-claude/pricing, read
    2026-11-10: Fable 5.2 and Mythos 5.2 at $8 / $40, cache writes $10 (5m)
    / $16 (1h), cache read $0.20 (0.025x).

The anthropic ``frontier`` tier moves 5.1 -> 5.2 as it moved 5 -> 5.1; the
bedrock ``fable`` alias and ``frontier`` tier deliberately stay on mantle's
fable5 (see tests/test_fable_5_1_integration.py).
"""

import time
from pathlib import Path

from app.config.models_config import (
    MODEL_ALIASES,
    MODEL_CONFIGS,
    resolve_tier_model,
    validate_model_configs,
)
from app.config.pricing import build_catalog

NEW_KEY = "claude-fable-5-2"
PRIOR_KEY = "claude-fable-5-1"


def _rates(provider, model_id):
    cat = build_catalog(user_file=Path("/nonexistent"))
    entry = cat.lookup(provider, model_id, None, time.time())
    assert entry is not None, f"no price card for {provider}/{model_id}"
    assert entry.source == "builtin"
    return entry.unit_prices


# -- anthropic direct --------------------------------------------------------

def test_fable_5_2_entry_exists_and_holds_the_frontier_tier():
    models = MODEL_CONFIGS["anthropic"]
    assert NEW_KEY in models
    assert models[NEW_KEY]["model_id"] == "claude-fable-5-2"
    assert models[NEW_KEY]["family"] == "claude"
    assert models[NEW_KEY]["tier"] == "frontier"
    assert resolve_tier_model("anthropic", "frontier") == NEW_KEY
    holders = [n for n, c in models.items() if c.get("tier") == "frontier"]
    assert holders == [NEW_KEY]


def test_fable_5_1_retained_but_untiered():
    models = MODEL_CONFIGS["anthropic"]
    assert PRIOR_KEY in models
    assert models[PRIOR_KEY].get("tier") is None


def test_capability_parity_with_fable_5_1():
    strip = lambda d: {k: v for k, v in d.items() if k not in ("model_id", "tier")}
    assert strip(MODEL_CONFIGS["anthropic"][NEW_KEY]) == strip(MODEL_CONFIGS["anthropic"][PRIOR_KEY])


def test_live_verified_capabilities_present():
    cfg = MODEL_CONFIGS["anthropic"][NEW_KEY]
    assert cfg["token_limit"] == 1_000_000
    assert cfg["max_output_tokens"] == 128_000
    assert cfg["supports_vision"] is True
    assert cfg["supports_adaptive_thinking"] is True
    # Live: "`temperature` is deprecated for this model".
    assert set(cfg["unsupported_parameters"]) == {"temperature", "top_k", "top_p"}


def test_mythos_5_2_registered_as_restricted_preview():
    cfg = MODEL_CONFIGS["anthropic"]["claude-mythos-5-2"]
    assert cfg["model_id"] == "claude-mythos-5-2"
    assert cfg["preview"] is True
    assert cfg.get("tier") is None


# -- bedrock -----------------------------------------------------------------

def test_bedrock_fable_5_2_is_the_fable_5_1_shape():
    bedrock = MODEL_CONFIGS["bedrock"]
    assert "fable5.2" in bedrock
    cfg = bedrock["fable5.2"]
    assert cfg["model_id"] == {
        "us": "us.anthropic.claude-fable-5-2",
        "global": "global.anthropic.claude-fable-5-2",
    }
    # bedrock-runtime, not mantle: the profiles resolved there live.
    assert "endpoint_override" not in cfg
    # The only thing between this entry and a 200 is the classic account
    # switch; undeclared, startup never raises it and every call 400s.
    assert cfg["required_data_retention_mode"] == "aws_review"
    assert set(cfg["available_regions"]) == {"us-east-1", "us-west-2"}
    assert cfg["family"] == "claude"
    assert cfg.get("tier") is None
    # Same envelope as 5.1 apart from the id.
    strip = lambda d: {k: v for k, v in d.items() if k != "model_id"}
    assert strip(cfg) == strip(bedrock["fable5.1"])


def test_bedrock_mythos_5_2_mirrors_mythos_5_1():
    bedrock = MODEL_CONFIGS["bedrock"]
    cfg = bedrock["mythos5.2"]
    assert cfg["model_id"] == {
        "us": "us.anthropic.claude-mythos-5-2",
        "global": "global.anthropic.claude-mythos-5-2",
    }
    assert cfg["preview"] is True
    strip = lambda d: {k: v for k, v in d.items() if k != "model_id"}
    assert strip(cfg) == strip(bedrock["mythos5.1"])


def test_bedrock_alias_and_frontier_tier_still_on_mantle_fable_5():
    assert MODEL_ALIASES["bedrock"]["fable"] == "fable5"
    assert resolve_tier_model("bedrock", "frontier") == "fable5"


def test_no_validation_issues_from_the_5_2_entries():
    issues = validate_model_configs()
    offending = [i for i in issues if "fable" in i.lower() or "mythos" in i.lower()]
    assert offending == [], offending


# -- pricing -----------------------------------------------------------------

def test_fable_5_2_priced_on_both_endpoints_without_disturbing_5_1():
    card = {"input": 8.0, "output": 40.0, "cache_read": 0.20,
            "cache_write_5m": 10.0, "cache_write_1h": 16.0}
    assert _rates("anthropic", "claude-fable-5-2") == card
    assert _rates("anthropic", "claude-mythos-5-2") == card
    assert _rates("bedrock", "us.anthropic.claude-fable-5-2") == card
    assert _rates("bedrock", "global.anthropic.claude-fable-5-2") == card
    # Globs stay disjoint: 5.1 keeps its $10/$50 card, 5 its $1 cache read.
    assert _rates("anthropic", "claude-fable-5-1")["input"] == 10.0
    assert _rates("bedrock", "anthropic.claude-fable-5")["cache_read"] == 1.0
