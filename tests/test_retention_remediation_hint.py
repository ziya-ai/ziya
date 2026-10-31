"""The retention-gate error tells a user on their own AWS account what to do.

Before: the error named the denied IAM action inside a raw AWS message and
stopped there.  The user had to work out that an account administrator must
either set the switch per region or grant two IAM actions, and in which
regions.  remediation_hint turns the restore error plus the diagnosis into
that instruction.  The input below is the real text from 2026-10-28.
"""
import app.providers.bedrock as bp
from app.utils.retention_diagnostics import (
    RETENTION_IAM_POLICY, describe_principal, remediation_hint,
)

ROLE_ARN = "arn:aws:sts::123456789012:assumed-role/ExampleBedrockRole/user@example.com"
REAL = ('Failed to read Bedrock data retention mode: HTTP 403 — {"Message":"User: '
        + ROLE_ARN + ' is not authorized to perform: bedrock:GetAccountDataRetention because '
        'no identity-based policy allows the bedrock:GetAccountDataRetention action"}; '
        'Failed to set Bedrock data retention mode to \'aws_review\': HTTP 403 — '
        '{"Message":"User: ' + ROLE_ARN + ' is not authorized to perform: '
        'bedrock:PutAccountDataRetention because no identity-based policy allows the '
        'bedrock:PutAccountDataRetention action"}')
MODEL = "global.anthropic.claude-fable-5-1"
DIAG = {"caller": ROLE_ARN, "account": "123456789012",
        "unsatisfied": ["us-west-2", "us-east-2"], "modes": {}}


def test_describe_principal():
    assert describe_principal(ROLE_ARN) == "IAM role 'ExampleBedrockRole' in account 123456789012"
    assert describe_principal("arn:aws:iam::1:user/alice") == "IAM user 'alice' in account 1"
    assert describe_principal(None) == "the AWS identity Ziya is using"


def test_put_denied_names_role_regions_and_policy():
    h = remediation_hint(DIAG, REAL, MODEL, "us-west-2", "aws_review")
    assert h.startswith("What to do: IAM role 'ExampleBedrockRole' in account 123456789012 "
                        "is not allowed to change")
    assert "in us-west-2, us-east-2 (one setting per region" in h
    assert RETENTION_IAM_POLICY in h
    assert '{"mode": "aws_review"}' in h
    assert "not shared with the model provider" in h


def test_read_only_denial_still_advises():
    err = REAL.split("; Failed to set")[0]
    h = remediation_hint(DIAG, err, MODEL, "us-west-2", "aws_review")
    assert "not allowed to read the data retention setting" in h
    assert RETENTION_IAM_POLICY in h


def test_diagnosis_read_403_counts_as_read_denial():
    d = dict(DIAG, modes={"us-west-2": "? (HTTP 403)"})
    assert "not allowed to read" in remediation_hint(d, "", MODEL, "us-west-2", "aws_review")


def test_scp_denial_says_model_cannot_be_enabled():
    err = ("User: " + ROLE_ARN + " is not authorized to perform: "
           "bedrock:PutAccountDataRetention with an explicit deny in a service control policy")
    h = remediation_hint(DIAG, err, MODEL, "us-west-2", "aws_review")
    assert "service control policy" in h and "cannot be enabled" in h
    assert RETENTION_IAM_POLICY not in h


def test_no_permission_problem_no_hint():
    assert remediation_hint(DIAG, "", MODEL, "us-west-2", "aws_review") == ""
    assert remediation_hint(DIAG, None, MODEL, "us-west-2", "aws_review") == ""
    assert remediation_hint({}, "HTTP 500 boom", MODEL, "us-west-2", "aws_review") == ""


def test_works_without_a_diagnosis():
    h = remediation_hint({}, REAL, MODEL, "us-west-2", "aws_review")
    assert h.startswith("What to do: the AWS identity Ziya is using is not allowed to change")
    assert "in us-west-2 (one setting per region" in h


# ── seam: the hint reaches the error the user sees ─────────────────────────

def test_hint_reaches_the_user_error(monkeypatch):
    from tests.test_bedrock_retention_heal import _FakeBedrock, _provider, _run
    from app.providers.base import ErrorEvent
    monkeypatch.setattr(bp, "_retention_heal_at", {})
    monkeypatch.setattr("app.utils.aws_utils.ensure_bedrock_data_retention_mode",
                        lambda **kw: (False, REAL))
    monkeypatch.setattr(bp, "log_retention_diagnosis", lambda *a: dict(DIAG), raising=False)
    events = _run(_provider(_FakeBedrock(failures=5),
                            {"required_data_retention_mode": "aws_review"}))
    msg = [e for e in events if isinstance(e, ErrorEvent)][0].message
    assert "What to do: IAM role 'ExampleBedrockRole'" in msg
    assert RETENTION_IAM_POLICY in msg
