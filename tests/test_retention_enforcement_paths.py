"""
Every model-init path must enforce a Covered Model's retention gate.

The classic account retention switch was originally enforced in exactly ONE
place, ``start_server()``.  That covers the web-server path only:
``main()`` dispatches to ``cli_main()`` for ``ziya chat`` / ``ask`` / ``review``
and never runs the server startup hook, and a mid-session ``/model`` switch
re-enters ``ModelManager`` without going through ``main()`` at all.  A model
gated on the classic switch (fable5.1) therefore reached its first invocation
with the switch untouched and failed with a retention ValidationException on
those two paths, while working fine when launched as a server.

``_initialize_bedrock_model`` already carried exactly this reasoning for the
MANTLE switch ("the CLI hands off to cli_main() and never runs the server
startup hook"), so these tests assert the classic switch is now handled at the
same shared seam, and that it is RAISE-ONLY so a multi-user account never
flip-flops.

The wiring assertions read the source of app/agents/models.py rather than
executing a full model init (which needs live AWS credentials, a boto3 client
and a region round-trip).  That is deliberate: the defect being guarded is
"the call site does not exist", which is a property of the source.
"""

import inspect
import re

import pytest

from app.config.models_config import (
    DEFAULT_MODELS,
    MODEL_CONFIGS,
    get_required_retention_mode,
)
from app.utils.aws_utils import retention_mode_satisfies
import app.agents.models as models_mod
import app.main as main_mod


def _models_source() -> str:
    return inspect.getsource(models_mod)


def _init_bedrock_source() -> str:
    """Source of just _initialize_bedrock_model, the shared init seam."""
    src = _models_source().splitlines()
    start = None
    for i, line in enumerate(src):
        if re.search(r"def _initialize_bedrock_model\b", line):
            start = i
            break
    assert start is not None, "_initialize_bedrock_model not found in app/agents/models.py"
    base = len(src[start]) - len(src[start].lstrip())
    for j in range(start + 1, len(src)):
        line = src[j]
        if line.strip() and (len(line) - len(line.lstrip())) <= base and re.match(r"\s*(async )?def ", line):
            return "\n".join(src[start:j])
    return "\n".join(src[start:])


# ---------------------------------------------------------------- wiring


def test_shared_init_seam_enforces_the_classic_switch():
    """The CLI and /model-switch paths both run through this function."""
    body = _init_bedrock_source()
    assert "ensure_bedrock_data_retention_mode" in body, (
        "_initialize_bedrock_model does not enforce the classic retention "
        "switch, so `ziya chat` and mid-session /model switches reach a "
        "Covered Model with the switch untouched and 400 on the first call"
    )


def test_shared_init_seam_reads_the_requirement_from_config():
    """Must consult the accessor, not hardcode a mode."""
    body = _init_bedrock_source()
    assert "get_required_retention_mode" in body, (
        "the classic-switch enforcement must derive the mode from the model "
        "config via get_required_retention_mode, not hardcode one"
    )


def test_classic_enforcement_is_skipped_for_mantle_models():
    """A mantle model must not drive the classic switch from this seam."""
    body = _init_bedrock_source()
    assert re.search(r'endpoint_override\s*!=\s*["\']bedrock-mantle["\']', body), (
        "classic-switch enforcement must be gated on the model NOT being "
        "mantle-routed; mantle models have their own switch"
    )


def test_classic_enforcement_never_downgrades():
    """allow_downgrade must not be passed here — the default is raise-only.

    This is the multi-user invariant: a user selecting fable5.1 (aws_review)
    must never lower an account another user has on provider_data_share.
    """
    body = _init_bedrock_source()
    call = re.search(
        r"ensure_bedrock_data_retention_mode\((.*?)\)", body, re.S
    )
    assert call, "no ensure_bedrock_data_retention_mode call found"
    assert "allow_downgrade" not in call.group(1), (
        "the shared init seam must not pass allow_downgrade; a downgrade here "
        "would break another user's in-flight Covered Model session"
    )


def test_server_startup_still_enforces_it_too():
    """The server path must not have LOST enforcement while adding the seam."""
    src = inspect.getsource(main_mod)
    assert "ensure_bedrock_data_retention_mode" in src
    assert "get_required_retention_mode" in src


def test_reset_to_inherit_remains_opt_in():
    """The unconditional reset is what caused flip-flop; it must stay gated."""
    src = inspect.getsource(main_mod)
    assert "ZIYA_RESET_BEDROCK_RETENTION" in src, (
        "the reset-to-inherit path must remain gated behind an explicit env "
        "var; running it unconditionally is what broke other users' sessions"
    )
    # And the only downgrade in the codebase must be that gated one.
    downgrades = re.findall(r"allow_downgrade\s*=\s*True", src)
    assert len(downgrades) == 1, (
        f"expected exactly one opt-in downgrade call site in main.py, "
        f"found {len(downgrades)}"
    )


# ------------------------------------------------------- no-flip-flop math


@pytest.mark.parametrize("current", ["aws_review", "provider_data_share"])
def test_satisfied_account_is_left_alone(current):
    """2nd..Nth user selecting a Covered Model issues no write at all."""
    required = get_required_retention_mode(MODEL_CONFIGS["bedrock"]["fable5.1"])
    assert retention_mode_satisfies(current, required) is True


def test_unset_account_is_raised():
    """Negative control: the raise DOES fire from the real starting state."""
    required = get_required_retention_mode(MODEL_CONFIGS["bedrock"]["fable5.1"])
    assert retention_mode_satisfies("inherit", required) is False
    assert retention_mode_satisfies("default", required) is False


def test_fable_5_1_cannot_downgrade_a_fable_5_account():
    """The cross-user hazard, stated directly."""
    f5 = get_required_retention_mode(MODEL_CONFIGS["bedrock"]["fable5"])
    f51 = get_required_retention_mode(MODEL_CONFIGS["bedrock"]["fable5.1"])
    # An account satisfying fable5 already satisfies fable5.1 ...
    assert retention_mode_satisfies(f5, f51) is True
    # ... but not the reverse, so fable5.1 can only ever raise.
    assert retention_mode_satisfies(f51, f5) is False


def _start_server_classic_block() -> str:
    """Source of start_server()'s CLASSIC-switch retention block.

    Sliced from the `if args.endpoint == "bedrock"` retention hook down to the
    mantle branch, so an assertion about the classic block cannot be satisfied
    by something written in the mantle block below it.
    """
    src = inspect.getsource(main_mod).splitlines()
    start = None
    for i, line in enumerate(src):
        if "Apply any model-required account-level Bedrock settings" in line:
            start = i
            break
    assert start is not None, "classic retention hook not found in app/main.py"
    for j in range(start + 1, len(src)):
        if "Apply mantle data retention" in src[j]:
            return "\n".join(src[start:j])
    return "\n".join(src[start:])


def test_server_startup_classic_block_is_gated_on_non_mantle():
    """Selecting a MANTLE model must not write the CLASSIC account switch.

    fable5/mythos5/gpt-5.6 are gated on the mantle switch, which has its own
    branch. Without an endpoint_override check here, selecting any of them also
    raised the classic switch — an unintended account-wide write that both
    contradicts the fable5 config comment ("no longer touches the classic
    switch other users' sessions rely on") and over-grants, since the legacy
    boolean resolves to provider_data_share where a bedrock-runtime Covered
    Model needs only aws_review.
    """
    block = _start_server_classic_block()
    assert re.search(r'endpoint_override["\']?\s*\)?\s*==\s*["\']bedrock-mantle["\']', block), (
        "start_server()'s classic-retention block must consult "
        "endpoint_override so mantle models skip the classic switch"
    )


def test_both_enforcement_paths_agree_on_skipping_mantle():
    """The server path and the shared init seam must not disagree.

    The seam (models.py) already gates on non-mantle. If start_server does not,
    the SAME model writes a different switch depending on whether it was
    launched as a server or via the CLI — which is exactly the kind of
    launch-path-dependent behaviour that hid the original gap.
    """
    seam_gates = bool(re.search(
        r'endpoint_override\s*!=\s*["\']bedrock-mantle["\']', _init_bedrock_source()
    ))
    server_gates = bool(re.search(
        r'endpoint_override["\']?\s*\)?\s*==\s*["\']bedrock-mantle["\']',
        _start_server_classic_block(),
    ))
    assert seam_gates and server_gates, (
        f"both paths must gate the classic switch on non-mantle; "
        f"seam={seam_gates} server={server_gates}"
    )


@pytest.mark.parametrize("name", [
    n for n, c in MODEL_CONFIGS["bedrock"].items()
    if c.get("endpoint_override") == "bedrock-mantle"
])
def test_mantle_models_are_not_bedrock_runtime_covered_models(name):
    """Negative control for the gate: every model the gate SKIPS is genuinely
    mantle-served, so skipping it cannot strand a bedrock-runtime model."""
    cfg = MODEL_CONFIGS["bedrock"][name]
    assert cfg.get("endpoint_override") == "bedrock-mantle"
    # Its requirement is satisfied by the mantle branch's hardcoded PUT.
    required = get_required_retention_mode(cfg)
    if required:
        assert retention_mode_satisfies("provider_data_share", required)


def test_every_non_mantle_covered_model_is_enforceable():
    """Any bedrock-runtime model declaring a mode must be reachable via the seam.

    Guards the case where someone adds a second Covered Model with an
    endpoint_override that silently routes it away from this enforcement.
    """
    for name, cfg in MODEL_CONFIGS["bedrock"].items():
        mode = get_required_retention_mode(cfg)
        if not mode:
            continue
        override = cfg.get("endpoint_override", "")
        if override == "bedrock-mantle":
            continue
        assert override == "", (
            f"{name} declares retention mode {mode!r} but has "
            f"endpoint_override={override!r}; the classic-switch enforcement "
            f"only covers models with no override"
        )


# ------------------------------------------- no-write path (the cross-user guarantee)
#
# The tests above establish that enforcement RAISES correctly. The property that
# actually protects a colleague's in-flight Covered Model session is the inverse:
# selecting an ordinary model must write NOTHING. Verified live on 2026-09-03 —
# sonnet5, opus4.8, haiku-4.5 and opus5 all invoked successfully against an account
# at aws_review, and the account switch was still 'aws_review' afterwards.
#
# Note that fable5 is NOT a useful subject for this: it routes via bedrock-mantle
# and is governed by mantle's own separate switch, so it is unaffected by the
# classic switch no matter what happens to it. Only models that genuinely share
# the classic switch's scope exercise this path.


def test_default_bedrock_model_requires_no_retention_change():
    """Starting Ziya with no -m flag must never touch an account-wide setting."""
    default_name = DEFAULT_MODELS["bedrock"]
    cfg = MODEL_CONFIGS["bedrock"][default_name]
    assert get_required_retention_mode(cfg) is None, (
        f"the default bedrock model ({default_name}) declares a retention "
        f"requirement, so merely launching Ziya would raise an account-wide "
        f"switch shared with every other user of the AWS account"
    )


@pytest.mark.parametrize("name", ["sonnet5", "opus4.8", "haiku-4.5", "opus5"])
def test_ordinary_runtime_models_declare_no_retention_requirement(name):
    """These share the classic switch's scope, so a requirement here would
    make every routine model selection mutate account-wide state."""
    cfg = MODEL_CONFIGS["bedrock"][name]
    assert cfg.get("endpoint_override", "") == "", (
        f"{name} is expected to be a plain bedrock-runtime model; if it moved "
        f"to an override, this test is no longer testing the classic-switch scope"
    )
    assert get_required_retention_mode(cfg) is None, (
        f"{name} now declares a retention requirement; selecting it would raise "
        f"the shared account switch for everyone in the AWS account"
    )


def _guard_precedes_call(block: str, guard_var: str) -> bool:
    """True if *block* only calls the classic setter under `if <guard_var>:`."""
    guard = re.search(rf"if\s+{re.escape(guard_var)}\s*:", block)
    call = re.search(r"ensure_bedrock_data_retention_mode\s*\(", block)
    if call is None:
        return False
    return guard is not None and guard.start() < call.start()


def test_enforcement_only_fires_for_a_declared_mode():
    """Both call sites must sit behind a truthiness guard on the resolved mode.

    Without the guard, a model declaring nothing would still issue a PUT — which
    is the shape of the original defect (an unconditional write on every start).
    """
    assert _guard_precedes_call(_init_bedrock_source(), "_classic_mode"), (
        "the shared init seam must only call the classic setter under "
        "`if _classic_mode:`"
    )
    assert _guard_precedes_call(_start_server_classic_block(), "_required_mode"), (
        "start_server must only call the classic setter under `if _required_mode:`"
    )


def test_guard_detection_would_notice_an_unconditional_write():
    """Negative control for the test above.

    An unguarded call must be reported as unguarded; otherwise the assertions in
    test_enforcement_only_fires_for_a_declared_mode would pass against exactly
    the regression they exist to catch.
    """
    unguarded = (
        "    _classic_mode = get_required_retention_mode(model_config)\n"
        "    ensure_bedrock_data_retention_mode(required_mode='aws_review')\n"
    )
    assert _guard_precedes_call(unguarded, "_classic_mode") is False
    # ... and a guard placed AFTER the call must not count either.
    wrong_order = (
        "    ensure_bedrock_data_retention_mode(required_mode='aws_review')\n"
        "    if _classic_mode:\n        pass\n"
    )
    assert _guard_precedes_call(wrong_order, "_classic_mode") is False
