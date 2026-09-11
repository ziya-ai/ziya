"""
Ordering-aware Bedrock data-retention mode comparison.

Background (live-verified 2026-09-02, profile kuiper-ziya):

  * AWS documents the retention modes as an ORDERED scale, least to most
    permissive: ``none < default < aws_review < provider_data_share``, and a
    model is invocable when the effective mode sits at or ABOVE what the model
    requires.  ``inherit`` is outside the scale — it defers to a broader scope
    (project -> account -> model default).

  * ``GET /v1/models/anthropic.claude-fable-5`` on bedrock-mantle us-east-1
    returned ``allowed_modes: ["aws_review", "provider_data_share"]`` with
    ``mode: "provider_data_share", source: "account"`` — i.e. the account is
    already ABOVE what the model asks for, and AWS documents that such an
    account needs no change.

  * The classic control-plane switch (``GET bedrock.us-east-1/data-retention``)
    was ``inherit``, which is why a bedrock-runtime invocation of
    ``global.anthropic.claude-fable-5-1`` failed with "data retention mode
    'default' is not available for this model" — inherit resolved down to the
    model default.

The previous comparison in ``_ensure_data_retention`` was equality-only, so a
model declaring ``aws_review`` against an account already on the more
permissive ``provider_data_share`` would have issued a needless PUT that
DOWNGRADES an account/region-wide switch shared by every model and every
concurrent session.  These tests pin the ordering semantics and that the
downgrade does not happen unless explicitly requested.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.aws_utils import (
    _RETENTION_MODE_ORDER,
    _ensure_data_retention,
    retention_mode_rank,
    retention_mode_satisfies,
)

URL = "https://bedrock.us-east-1.amazonaws.com/data-retention"


# ---------------------------------------------------------------- ordering ---

def test_documented_scale_order():
    # The order is load-bearing: it encodes the AWS-documented permissiveness
    # scale, and reordering it would silently invert every comparison below.
    assert _RETENTION_MODE_ORDER == (
        "none", "default", "aws_review", "provider_data_share",
    )


@pytest.mark.parametrize("mode,expected", [
    ("none", 0),
    ("default", 1),
    ("aws_review", 2),
    ("provider_data_share", 3),
])
def test_rank_of_each_known_mode(mode, expected):
    assert retention_mode_rank(mode) == expected


@pytest.mark.parametrize("mode", ["inherit", "", None, "AWS_REVIEW_PLUS", "bogus"])
def test_unrankable_modes_return_none(mode):
    # 'inherit' is deliberately unrankable: it expresses no opinion and defers
    # to a broader scope, so it cannot be compared against a requirement.
    assert retention_mode_rank(mode) is None


def test_rank_tolerates_case_and_whitespace():
    assert retention_mode_rank("  AWS_Review ") == retention_mode_rank("aws_review")


def test_rank_ignores_non_string_input():
    assert retention_mode_rank(123) is None  # type: ignore[arg-type]


# -------------------------------------------------------------- satisfies ---

def test_legacy_provider_data_share_satisfies_aws_review():
    # THE case that matters: this is the live account state for fable 5 on
    # mantle, and it is why Fable 5.1's aws_review requirement needs no write
    # on an account already set to the legacy mode.
    assert retention_mode_satisfies("provider_data_share", "aws_review") is True


def test_aws_review_does_not_satisfy_provider_data_share():
    # Ordering must not be symmetric — the stricter mode cannot stand in for
    # the more permissive one.
    assert retention_mode_satisfies("aws_review", "provider_data_share") is False


def test_exact_match_satisfies():
    assert retention_mode_satisfies("aws_review", "aws_review") is True


@pytest.mark.parametrize("current", ["none", "default"])
def test_modes_below_requirement_do_not_satisfy(current):
    assert retention_mode_satisfies(current, "aws_review") is False


def test_inherit_never_satisfies():
    # An account on 'inherit' resolves to the model's own default, which for
    # any model requiring an opt-in is necessarily below that requirement.
    # Treating it as insufficient is the safe direction.
    assert retention_mode_satisfies("inherit", "aws_review") is False


def test_unrankable_requirement_never_satisfied():
    assert retention_mode_satisfies("provider_data_share", "inherit") is False
    assert retention_mode_satisfies("provider_data_share", "nonsense") is False


# ------------------------------------------------- _ensure_data_retention ---

def _mock_session():
    session = MagicMock()
    creds = MagicMock()
    creds.get_frozen_credentials.return_value = MagicMock(
        access_key="AK", secret_key="SK", token=None,
    )
    session.get_credentials.return_value = creds
    return session


def _run_ensure(current_mode, required_mode, **kwargs):
    """Drive _ensure_data_retention with a stubbed GET, capturing any PUT."""
    get_resp = MagicMock(status_code=200)
    get_resp.json.return_value = {"mode": current_mode}
    put_resp = MagicMock(status_code=200, text="")

    with patch("app.utils.aws_utils.create_fresh_boto3_session", return_value=_mock_session()), \
         patch("requests.get", return_value=get_resp) as mock_get, \
         patch("requests.put", return_value=put_resp) as mock_put:
        ok, err = _ensure_data_retention(
            "Test", URL, required_mode, "us-east-1", **kwargs
        )
    return ok, err, mock_get, mock_put


def test_no_write_when_current_is_more_permissive():
    # provider_data_share (3) already satisfies aws_review (2): the switch is
    # account-wide, so rewriting it down could break another model or another
    # user's in-flight session.
    ok, err, _get, put = _run_ensure("provider_data_share", "aws_review")
    assert ok is True and err == ""
    assert put.call_count == 0, "must not downgrade an already-sufficient switch"


def test_no_write_when_current_equals_required():
    ok, err, _get, put = _run_ensure("aws_review", "aws_review")
    assert ok is True
    assert put.call_count == 0


def test_writes_when_current_is_inherit():
    # This is the real classic-plane state that blocks Fable 5.1 on
    # bedrock-runtime, so the raise MUST happen here.
    ok, err, _get, put = _run_ensure("inherit", "aws_review")
    assert ok is True and err == ""
    assert put.call_count == 1
    assert put.call_args.kwargs["data"] == '{"mode": "aws_review"}'


@pytest.mark.parametrize("current", ["none", "default"])
def test_writes_when_current_is_below_requirement(current):
    ok, _err, _get, put = _run_ensure(current, "aws_review")
    assert ok is True
    assert put.call_count == 1


def test_allow_downgrade_forces_the_exact_mode():
    # The explicit escape hatch: a caller that genuinely wants to lower the
    # switch (e.g. resetting to 'inherit') must opt in.
    ok, _err, _get, put = _run_ensure(
        "provider_data_share", "inherit", allow_downgrade=True
    )
    assert ok is True
    assert put.call_count == 1
    assert put.call_args.kwargs["data"] == '{"mode": "inherit"}'


def test_equality_only_comparison_would_fail_this_suite():
    # Negative control proving test_no_write_when_current_is_more_permissive
    # measures something real: under the previous equality-only predicate,
    # 'provider_data_share' != 'aws_review' would have fallen through to the
    # PUT.  If satisfies() ever regresses to equality, that test goes red.
    assert retention_mode_satisfies("provider_data_share", "aws_review") is not (
        "provider_data_share" == "aws_review"
    )


def test_get_failure_is_reported_and_no_write_attempted():
    get_resp = MagicMock(status_code=403, text="denied")
    with patch("app.utils.aws_utils.create_fresh_boto3_session", return_value=_mock_session()), \
         patch("requests.get", return_value=get_resp), \
         patch("requests.put") as mock_put:
        ok, err = _ensure_data_retention("Test", URL, "aws_review", "us-east-1")
    assert ok is False
    assert "403" in err
    assert mock_put.call_count == 0
