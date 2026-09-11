"""
Integration coverage for Claude Fable 5.1.

Live-verified on 2026-09-01 by querying both services directly:

  * Anthropic direct — GET /v1/models listed ``claude-fable-5-1``; a
    /v1/models/claude-fable-5-1 retrieve returned display_name
    "Claude Fable 5.1" (max_input 1M, max_output 128k, efforts low..max,
    adaptive thinking, image input); a smoke /v1/messages call returned
    stop_reason=end_turn.  Capabilities are identical to fable 5, so the
    new entry mirrors ``claude-fable-5`` exactly except the model_id.

  * Bedrock Mantle — still does NOT serve 5.1 (re-probed 2026-09-02 19:34):
    every name variant 404s with "does not exist" — ``anthropic.claude-fable-5-1``,
    ``claude-fable-5-1``, ``claude-fable-5.1`` and the ``us.``/``global.``
    prefixed forms — while ``anthropic.claude-fable-5`` returns 200 in
    us-east-1.  ``fable5`` therefore stays on mantle.

  * Bedrock **runtime** (classic, NOT mantle) — DOES serve 5.1, per the AWS
    announcement and confirmed live 2026-09-02: invoking
    ``global.anthropic.claude-fable-5-1`` and ``us.anthropic.claude-fable-5-1``
    returns a *retention-mode* ValidationException ("data retention mode
    'default' is not available for this model"), whereas a genuinely unknown
    id (``global.anthropic.claude-fable-9-9``) returns "The provided model
    identifier is invalid" — so the profiles resolve and the only gate is the
    account retention mode.  The bedrock ``fable5.1`` entry is consequently
    routed through bedrock-runtime with no ``endpoint_override``, and declares
    ``required_data_retention_mode: "aws_review"``.

Fable 5.1 is the new ``frontier`` holder on the anthropic endpoint; the prior
``claude-fable-5`` entry is retained (still selectable by explicit name) but
loses its tier tag so tier resolution lands on 5.1.

These assertions describe the intended post-change state, so they FAIL against
the pre-change config and PASS once the claude-fable-5-1 entry is in place.
"""

from app.config.models_config import (
    MODEL_CONFIGS,
    MODEL_ALIASES,
    resolve_tier_model,
    validate_model_configs,
)

ENDPOINT = "anthropic"
NEW_KEY = "claude-fable-5-1"
NEW_MODEL_ID = "claude-fable-5-1"
PRIOR_KEY = "claude-fable-5"


def test_fable_5_1_entry_exists_with_correct_id():
    models = MODEL_CONFIGS[ENDPOINT]
    assert NEW_KEY in models, f"{NEW_KEY} missing from MODEL_CONFIGS['{ENDPOINT}']"
    assert models[NEW_KEY]["model_id"] == NEW_MODEL_ID
    assert models[NEW_KEY]["tier"] == "frontier"
    assert models[NEW_KEY]["family"] == "claude"


def test_fable_5_1_is_the_frontier_tier():
    # The portable 'frontier' rung on anthropic must resolve to 5.1, not 5.
    assert resolve_tier_model(ENDPOINT, "frontier") == NEW_KEY


def test_exactly_one_frontier_holder_on_anthropic():
    holders = [n for n, c in MODEL_CONFIGS[ENDPOINT].items()
               if c.get("tier") == "frontier"]
    assert holders == [NEW_KEY], (
        f"anthropic should have exactly one frontier holder ({NEW_KEY}); got {holders}"
    )


def test_prior_fable_5_retained_but_untiered():
    models = MODEL_CONFIGS[ENDPOINT]
    assert PRIOR_KEY in models, f"{PRIOR_KEY} should remain selectable by explicit name"
    assert models[PRIOR_KEY].get("tier") is None, (
        f"{PRIOR_KEY} must drop its tier tag so tier resolution picks {NEW_KEY}"
    )


def test_capability_parity_with_fable_5():
    # 5.1 mirrors 5 exactly except the model_id (and the tier, which moved).
    new = {k: v for k, v in MODEL_CONFIGS[ENDPOINT][NEW_KEY].items()
           if k not in ("model_id", "tier")}
    old = {k: v for k, v in MODEL_CONFIGS[ENDPOINT][PRIOR_KEY].items()
           if k not in ("model_id", "tier")}
    assert new == old, (
        f"{NEW_KEY} should match {PRIOR_KEY} capabilities; "
        f"diff={set(new.items()) ^ set(old.items())}"
    )


def test_live_verified_capabilities_present():
    # Values confirmed against the live Anthropic model card for 5.1.
    # (The anthropic entry deliberately carries no supported_efforts key —
    # effort defaults are handled provider-side — so it is not asserted here.)
    cfg = MODEL_CONFIGS[ENDPOINT][NEW_KEY]
    assert cfg["token_limit"] == 1_000_000
    assert cfg["max_output_tokens"] == 128_000
    assert cfg["supports_vision"] is True
    assert cfg["supports_adaptive_thinking"] is True
    # 5.1 rejects temperature/top_k/top_p (steer via effort).
    assert set(cfg["unsupported_parameters"]) == {"temperature", "top_k", "top_p"}


def test_bedrock_fable_5_1_routes_via_bedrock_runtime_not_mantle():
    # 5.1 is reachable on classic bedrock-runtime but NOT on mantle, so the
    # bedrock entry must carry no endpoint_override. Routing it to mantle (by
    # cloning fable5) would 404 at invocation time.
    bedrock = MODEL_CONFIGS["bedrock"]
    assert "fable5.1" in bedrock, "bedrock fable5.1 entry missing"
    cfg = bedrock["fable5.1"]
    assert "endpoint_override" not in cfg, (
        "fable5.1 must NOT be routed through bedrock-mantle — every 5.1 name "
        "variant 404s there (re-probed 2026-09-02)"
    )
    # fable5 by contrast stays on mantle, which is where it IS served.
    assert bedrock["fable5"]["endpoint_override"] == "bedrock-mantle"


def test_bedrock_fable_5_1_declares_the_covered_model_retention_gate():
    # The ONLY thing standing between this entry and a working invocation is
    # the classic account retention switch, so the requirement must be
    # declared or startup will not raise it and every call 400s.
    cfg = MODEL_CONFIGS["bedrock"]["fable5.1"]
    assert cfg["required_data_retention_mode"] == "aws_review"


def test_bedrock_alias_and_frontier_tier_deliberately_stay_on_fable_5():
    # Moving the bedrock `fable` alias or `frontier` tier to 5.1 would make
    # tier/alias resolution depend on the CLASSIC account switch being
    # raisable to aws_review, which an SCP can forbid outright; fable5 on
    # mantle works under the mantle switch Ziya already manages. 5.1 stays
    # selectable by explicit name. Revisit only as a deliberate decision.
    assert MODEL_ALIASES["bedrock"]["fable"] == "fable5"
    assert resolve_tier_model("bedrock", "frontier") == "fable5"


def test_no_new_validation_issues_from_fable_entries():
    # The claude-fable-5-1 addition must not introduce any
    # validate_model_configs issue mentioning fable.
    issues = validate_model_configs()
    offending = [i for i in issues if "fable" in i.lower()]
    assert offending == [], f"fable-related validation issues: {offending}"
