"""
Seam coverage for the retention-mode DECLARATION layer.

Two halves have to meet for a Covered Model to be invocable:

  1. A model entry declares the retention mode it needs
     (``required_data_retention_mode``, or the legacy
     ``requires_provider_data_share`` boolean).
  2. Startup reads that declaration and raises the account switch.

``tests/test_retention_mode_ordering.py`` covers the ordering primitives in
app/utils/aws_utils.py.  This file covers the declaration accessor and, more
importantly, the WIRING — an accessor that is correct but never called by
app/main.py would leave every Covered Model 400ing at first invocation while
every unit test passed.

Live-verified facts this file encodes (2026-09-02, profile kuiper-ziya):
  * Fable 5's model card reports
    ``allowed_modes: ["aws_review", "provider_data_share"]``, confirming that
    the two modes are alternatives and not a single required value.
  * The mantle account switch reads ``provider_data_share``; the classic
    control-plane switch reads ``inherit``.  They are independent, which is
    why fable5 (mantle) works while fable5.1 (bedrock-runtime) is gated.
"""

import re
from pathlib import Path

from app.config.models_config import MODEL_CONFIGS, get_required_retention_mode
from app.utils.aws_utils import retention_mode_satisfies

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = REPO_ROOT / "app" / "main.py"


# ---------------------------------------------------------------- accessor

def test_legacy_boolean_maps_to_provider_data_share():
    # The boolean predates the documented ordering and could only ever mean
    # the single most permissive mode. Existing configs must keep their exact
    # prior behaviour.
    assert get_required_retention_mode(
        {"requires_provider_data_share": True}
    ) == "provider_data_share"


def test_legacy_boolean_false_is_unconstrained():
    assert get_required_retention_mode({"requires_provider_data_share": False}) is None


def test_absent_declaration_is_unconstrained():
    # An unconstrained model must return None, not a default mode — returning
    # a mode here would make startup raise the account switch for models that
    # never needed it.
    assert get_required_retention_mode({}) is None
    assert get_required_retention_mode({"model_id": "x", "family": "claude"}) is None


def test_explicit_string_is_returned_verbatim():
    assert get_required_retention_mode(
        {"required_data_retention_mode": "aws_review"}
    ) == "aws_review"


def test_explicit_string_wins_over_legacy_boolean():
    # Precedence matters in the downgrade direction: if the boolean won, a
    # model asking for aws_review would drive the switch to the more
    # permissive provider_data_share instead.
    cfg = {
        "required_data_retention_mode": "aws_review",
        "requires_provider_data_share": True,
    }
    assert get_required_retention_mode(cfg) == "aws_review"


# ------------------------------------------------- real config entries

def test_fable_5_1_declares_aws_review_via_the_new_key():
    cfg = MODEL_CONFIGS["bedrock"]["fable5.1"]
    assert get_required_retention_mode(cfg) == "aws_review"
    # Specifically NOT via the legacy boolean — that would drive the shared
    # classic switch to provider_data_share, which is more permissive than
    # this Covered Model actually requires.
    assert "requires_provider_data_share" not in cfg


def test_fable_5_keeps_its_legacy_declaration_unchanged():
    # Generalising the accessor must not have altered what fable5 asks for.
    cfg = MODEL_CONFIGS["bedrock"]["fable5"]
    assert cfg.get("requires_provider_data_share") is True
    assert get_required_retention_mode(cfg) == "provider_data_share"


def test_every_mantle_model_is_satisfied_by_the_hardcoded_mantle_mode():
    # app/main.py and app/agents/models.py both PUT a hardcoded
    # 'provider_data_share' on the mantle switch, which is shared by every
    # mantle model. Adding a mantle model that needs something MORE permissive
    # than that would silently 400 at first call, so assert the invariant.
    unsatisfied = []
    for name, cfg in MODEL_CONFIGS["bedrock"].items():
        if cfg.get("endpoint_override") != "bedrock-mantle":
            continue
        required = get_required_retention_mode(cfg)
        if required and not retention_mode_satisfies("provider_data_share", required):
            unsatisfied.append((name, required))
    assert unsatisfied == [], (
        f"mantle models requiring a mode above provider_data_share: {unsatisfied}"
    )


def test_no_bedrock_runtime_model_silently_requires_sharing_with_provider():
    # A bedrock-runtime model declaring the legacy boolean would raise the
    # SHARED classic switch to the most permissive mode. Covered Models only
    # need aws_review, so flag any such entry as almost certainly a mistake.
    offenders = [
        name for name, cfg in MODEL_CONFIGS["bedrock"].items()
        if cfg.get("endpoint_override") is None
        and cfg.get("requires_provider_data_share")
    ]
    assert offenders == [], (
        f"bedrock-runtime models using the legacy over-permissive boolean: {offenders}"
    )


# ------------------------------------------------------- wiring / seam

def _main_py() -> str:
    return MAIN_PY.read_text()


def test_main_actually_calls_the_accessor():
    # The "defined but never called" trap: without this, every assertion above
    # passes while startup still hardcodes a single mode.
    src = _main_py()
    assert "get_required_retention_mode" in src, (
        "app/main.py never calls get_required_retention_mode — the declaration "
        "layer is not wired into startup"
    )


def test_main_passes_the_declared_mode_rather_than_a_hardcoded_one():
    # Scope to the classic-switch call site, then assert what it forwards.
    src = _main_py()
    m = re.search(
        r"ensure_bedrock_data_retention_mode\(\s*required_mode\s*=\s*([^,\n]+)", src
    )
    assert m, "no keyword call to ensure_bedrock_data_retention_mode found"
    forwarded = m.group(1).strip()
    assert "provider_data_share" not in forwarded, (
        f"classic switch still hardcodes a mode ({forwarded}) instead of "
        f"forwarding the model's declaration"
    )
    assert "_required_mode" in forwarded


def test_the_unconditional_reset_to_inherit_is_gone():
    # The old code reset the shared classic switch to 'inherit' on any model
    # that declared nothing, which would strand an in-flight Fable 5.1 session
    # the moment anyone started Ziya on sonnet5. It must now be opt-in.
    src = _main_py()
    assert "ZIYA_RESET_BEDROCK_RETENTION" in src, (
        "the reset-to-inherit branch is not gated behind an opt-in env var"
    )
    # And the reset must be the only place that forces an exact value.
    assert "allow_downgrade=True" in src


def test_reset_branch_is_an_elif_not_an_unconditional_else():
    # A bare `else:` here would re-introduce the downgrade for every
    # unconstrained model. Anchor on the gate identifier, not line position.
    src = _main_py()
    assert re.search(
        r"elif\s+os\.environ\.get\(\s*[\"']ZIYA_RESET_BEDROCK_RETENTION[\"']", src
    ), "reset branch is not an opt-in elif"
