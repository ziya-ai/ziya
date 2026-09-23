"""
Coverage for the non-Claude Bedrock additions (2026-09-23):
GPT-6 (Sol/Astra/Luna), Grok 4.6, and Kimi K3.

All five were probed live against bedrock-runtime the day they landed:
  - Each is invocable via the Converse API with us./global. CRIS profiles in
    us-east-1 and us-west-2; each accepts TEXT+IMAGE input.
  - Each REJECTS `temperature` and `topP` with a ValidationException (400).
  - GPT-6 is served natively on bedrock-runtime (not the mantle OpenAI
    Responses gateway the gpt-5.6 family uses).

These tests pin the config facts that DETERMINE routing, because the routing
is the seam: app/providers/factory.py sends a bedrock model to
NovaBedrockProvider (Converse) precisely when it has no `endpoint_override`,
no `wrapper_class == "OpenAIBedrock"`, and a non-"claude" family. Getting any
of those wrong silently reroutes the model to a provider whose wire format the
live probe did NOT verify (mantle, or the OpenAI invoke path).
"""

import pytest

from app.config.models_config import MODEL_CONFIGS, validate_model_configs

NEW_MODELS = ["gpt-6-sol", "gpt-6-astra", "gpt-6-luna", "grok-4.6", "kimi-k3"]


@pytest.fixture
def bedrock():
    return MODEL_CONFIGS["bedrock"]


class TestEntriesExist:
    @pytest.mark.parametrize("name", NEW_MODELS)
    def test_entry_present(self, bedrock, name):
        assert name in bedrock, f"bedrock/{name} missing"

    @pytest.mark.parametrize("name", NEW_MODELS)
    def test_model_id_uses_us_and_global_cris(self, bedrock, name):
        mid = bedrock[name]["model_id"]
        assert set(mid.keys()) == {"us", "global"}, (
            f"{name}: expected us/global CRIS profiles, got {sorted(mid)}"
        )
        assert mid["us"].startswith("us."), f"{name}: us profile malformed"
        assert mid["global"].startswith("global."), f"{name}: global profile malformed"
        # us. and global. must name the same underlying model.
        assert mid["us"][len("us."):] == mid["global"][len("global."):]


class TestConverseRoutingInvariants:
    """The seam: these three facts are exactly what factory.create_provider
    reads to route a bedrock model to NovaBedrockProvider (Converse) — the
    only path the live probe verified for these models."""

    @pytest.mark.parametrize("name", NEW_MODELS)
    def test_no_wrapper_class(self, bedrock, name):
        # wrapper_class == "OpenAIBedrock" would route to the OpenAI invoke
        # path (verified NOT used here).
        assert "wrapper_class" not in bedrock[name], (
            f"{name}: wrapper_class present -> would leave the Converse path"
        )

    @pytest.mark.parametrize("name", NEW_MODELS)
    def test_family_is_not_claude(self, bedrock, name):
        # family == "claude" would route to the Anthropic invoke provider.
        assert bedrock[name].get("family") != "claude", (
            f"{name}: claude family -> would route to Anthropic invoke path"
        )

    @pytest.mark.parametrize("name", NEW_MODELS)
    def test_no_mantle_override(self, bedrock, name):
        # endpoint_override / mantle_api would route to a mantle provider —
        # the gpt-5.6 family uses that, GPT-6 deliberately does not.
        cfg = bedrock[name]
        assert "endpoint_override" not in cfg, f"{name}: unexpected mantle override"
        assert "mantle_api" not in cfg, f"{name}: unexpected mantle_api"


class TestVerifiedCapabilities:
    @pytest.mark.parametrize("name", NEW_MODELS)
    def test_sampling_params_marked_unsupported(self, bedrock, name):
        # temperature and topP were live-verified to 400. top_k is grouped
        # with them (Converse has no top_k for these, and it must not leak).
        unsupported = set(bedrock[name].get("unsupported_parameters", []))
        assert {"temperature", "top_p"} <= unsupported, (
            f"{name}: temperature/top_p must be unsupported (verified 400)"
        )

    @pytest.mark.parametrize("name", NEW_MODELS)
    def test_vision(self, bedrock, name):
        assert bedrock[name].get("supports_vision") is True, (
            f"{name}: IMAGE input was verified; supports_vision must be True"
        )

    @pytest.mark.parametrize("name", NEW_MODELS)
    def test_output_ceiling_within_probed_limit(self, bedrock, name):
        # Probed hard limits: gpt-6 131072, kimi-k3 128000, grok >=200000.
        # The configured max_output_tokens must not exceed the probed cap or
        # every max-length request 400s.
        probed_cap = {
            "gpt-6-sol": 131072, "gpt-6-astra": 131072, "gpt-6-luna": 131072,
            "kimi-k3": 128000, "grok-4.6": 200000,
        }[name]
        assert bedrock[name]["max_output_tokens"] <= probed_cap, (
            f"{name}: max_output_tokens exceeds probed ceiling {probed_cap}"
        )


class TestGpt6ContextWindow:
    """GPT-6's context window was LIVE-PROBED 2026-09-23 (not inferred): a
    Converse call accepted a 900,005-token input and rejected 950,005 with
    context_length_exceeded, consistent with a 1,048,576 total window minus
    the model's reserved 131072 output budget. The first-cut config inferred
    272k from the gpt-5.6 sibling, which the probe disproved. Pin the probed
    value so a future edit can't silently regress it."""

    GPT6 = ["gpt-6-sol", "gpt-6-astra", "gpt-6-luna"]

    @pytest.mark.parametrize("name", GPT6)
    def test_window_reflects_probe_not_inferred_272k(self, bedrock, name):
        cfg = bedrock[name]
        # All three GPT-6 variants were individually probed 2026-09-23 and
        # behave identically: 900,005-token input accepted, 950,005 rejected
        # with context_length_exceeded — a 1,048,576 total window minus the
        # reserved 131072 output budget. Pin the exact probed value so a
        # future edit can't regress it (the first cut inferred 272k, which
        # the probe disproved).
        assert cfg["token_limit"] == 1_048_576, (
            f"{name}: token_limit {cfg['token_limit']} != probed 1,048,576"
        )
        # The three window fields must agree.
        assert cfg["max_input_tokens"] == cfg["token_limit"]
        assert cfg["context_window"] == cfg["token_limit"]


class TestProbedInputWindows:
    """grok-4.6 and kimi-k3 input windows were LIVE-PROBED 2026-09-23 via the
    explicit "model maximum" ValidationException, which returns the exact
    number — so these are pinned to those numbers, not inferred. Both were
    first cut at 256k (matching their k2/older-family siblings); the probe
    disproved that for both."""

    # (model, probed max, a witness input the probe ACCEPTED below it)
    CASES = [
        # grok: "prompt tokens (900019) exceed model maximum (524288)"; 500k ok.
        ("grok-4.6", 524_288, 500_000),
        # kimi: "...maximum (1048576)" at ~1.08M; 900k ok.
        ("kimi-k3", 1_048_576, 900_000),
    ]

    @pytest.mark.parametrize("name,probed_max,accepted", CASES)
    def test_window_matches_probed_maximum(self, bedrock, name, probed_max, accepted):
        cfg = bedrock[name]
        assert cfg["token_limit"] == probed_max, (
            f"{name}: token_limit {cfg['token_limit']} != probed model "
            f"maximum {probed_max}"
        )
        assert cfg["max_input_tokens"] == probed_max
        assert cfg["context_window"] == probed_max
        # Sanity: the window must clear an input the probe actually accepted,
        # and must not be the disproven 256k first cut.
        assert cfg["token_limit"] > accepted
        assert cfg["token_limit"] != 256_000, (
            f"{name}: token_limit is the disproven 256k first cut"
        )


class TestNoTierCollision:
    """These are added UNtagged: tagging any with an already-taken bedrock
    rung would make resolve_tier_model order-dependent (the exact opus5 bug).
    validate_model_configs + the per-endpoint uniqueness test in
    test_opus_5_5_models cover this globally; here we assert it directly for
    the new entries so a future tier tag on one of them fails loudly."""

    @pytest.mark.parametrize("name", NEW_MODELS)
    def test_untagged_or_unique(self, bedrock, name):
        tier = bedrock[name].get("tier")
        if tier is None:
            return
        others = [n for n, c in bedrock.items()
                  if n != name and c.get("tier") == tier]
        assert not others, (
            f"{name} shares tier {tier!r} with {others}; resolve_tier_model "
            f"would silently pick one and ignore the rest"
        )


def test_config_still_validates_clean():
    # No unknown keys / bad family refs introduced by the new entries.
    issues = [i for i in validate_model_configs()
              if any(m in i for m in NEW_MODELS)]
    assert issues == [], f"validation issues on new entries: {issues}"
