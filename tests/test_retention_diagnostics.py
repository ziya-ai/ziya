"""RETENTION_DIAG: explain which region rejected a Covered Model call.

Reproduces the 2026-10-07 incident shape: global.anthropic.claude-fable-5-1
from us-west-2, us-west-2 on aws_review, the profile also routing to "any
region", us-east-2 on inherit.  The old log line named neither the region
nor the account; the diagnosis must name both and flag the unset region.
"""
from types import SimpleNamespace

import pytest

import app.utils.retention_diagnostics as rd

GLOBAL = "global.anthropic.claude-fable-5-1"


class _Bedrock:
    def __init__(self, arns):
        self.arns = arns

    def get_inference_profile(self, inferenceProfileIdentifier):
        return {"models": [{"modelArn": a} for a in self.arns]}


class _Session:
    def __init__(self, arns, account="123456789012"):
        self.arns, self.account = arns, account

    def get_credentials(self):
        return SimpleNamespace(get_frozen_credentials=lambda: "creds")

    def client(self, name, region_name=None):
        if name == "sts":
            return SimpleNamespace(get_caller_identity=lambda: {"Account": self.account})
        return _Bedrock(self.arns)


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setattr(rd, "_cache", {})
    modes = {"us-west-2": "aws_review", "us-east-1": "aws_review", "us-east-2": "inherit"}
    reads = []

    def fake_read(_creds, region):
        reads.append(region)
        return modes.get(region, "inherit"), None

    monkeypatch.setattr(rd, "read_retention_mode", fake_read)

    def install(arns):
        monkeypatch.setattr("app.utils.aws_utils.create_fresh_boto3_session",
                            lambda profile_name=None: _Session(arns))
    return SimpleNamespace(install=install, reads=reads)


def test_global_profile_names_unbounded_routing_and_account(aws):
    aws.install(["arn:aws:bedrock:us-west-2::foundation-model/x",
                 "arn:aws:bedrock:::foundation-model/x"])
    diag = rd.diagnose_retention_gate(GLOBAL, "us-west-2", "team-dev", "aws_review")
    assert diag["routes"] == ["us-west-2", rd.ANY_REGION]
    assert diag["account"] == "123456789012"
    line = rd.format_diagnosis(diag)
    assert "ANY-COMMERCIAL-REGION" in line and "us-west-2=aws_review" in line
    assert "profile=team-dev" in line
    s = rd.user_summary(diag)
    assert "any commercial region" in s and "unlisted" in s


def test_us_profile_flags_the_unset_region(aws):
    aws.install([f"arn:aws:bedrock:{r}::foundation-model/x"
                 for r in ("us-east-1", "us-east-2", "us-west-2")])
    diag = rd.diagnose_retention_gate("us.anthropic.claude-fable-5-1", "us-west-2",
                                      None, "aws_review")
    assert set(aws.reads) == {"us-east-1", "us-east-2", "us-west-2"}
    assert diag["unsatisfied"] == ["us-east-2"]
    assert "us-east-2" in rd.user_summary(diag)
    assert "below_required=['us-east-2']" in rd.format_diagnosis(diag)


def test_bare_model_id_checks_session_region_only(aws):
    aws.install([])
    diag = rd.diagnose_retention_gate("anthropic.claude-fable-5-1", "us-east-2",
                                      None, "aws_review")
    assert diag["routes"] is None and aws.reads == ["us-east-2"]
    assert diag["unsatisfied"] == ["us-east-2"]


def test_missing_permission_is_reported_not_raised(aws, monkeypatch):
    aws.install([])

    class _NoProfile(_Session):
        def client(self, name, region_name=None):
            if name == "bedrock":
                def boom(**_k):
                    raise Exception("AccessDeniedException: not authorized")
                return SimpleNamespace(get_inference_profile=boom)
            return super().client(name, region_name)
    monkeypatch.setattr("app.utils.aws_utils.create_fresh_boto3_session",
                        lambda profile_name=None: _NoProfile([]))
    diag = rd.diagnose_retention_gate(GLOBAL, "us-west-2", None, "aws_review")
    assert any("get_inference_profile" in e for e in diag["errors"])
    assert aws.reads == ["us-west-2"]
    assert "errors=" in rd.format_diagnosis(diag)


def test_results_are_cached(aws):
    aws.install(["arn:aws:bedrock:us-west-2::foundation-model/x"])
    rd.diagnose_retention_gate(GLOBAL, "us-west-2", None, "aws_review")
    rd.diagnose_retention_gate(GLOBAL, "us-west-2", None, "aws_review")
    assert aws.reads == ["us-west-2"]
