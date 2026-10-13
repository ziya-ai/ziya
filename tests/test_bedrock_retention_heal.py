"""A retention switch lowered mid-session is restored in place.

The classic Bedrock data-retention switch is account/region-wide and shared
by every user of the account.  Ziya raises it at model init, but another
user (an older Ziya that reset it to 'inherit' on start, or a manual PUT)
can lower it afterwards.  Every call to a Covered Model then failed with
"data retention mode 'default' is not available for this model" and the
only advice was to restart.  BedrockProvider.stream_response now raises the
switch (raise-only) and retries once, throttled per (profile, region).
"""
import asyncio

import pytest

import app.providers.bedrock as bp
from app.providers.base import StreamEnd, ErrorEvent

RET_ERR = ("An error occurred (ValidationException) when calling the "
           "InvokeModelWithResponseStream operation: data retention mode 'default' "
           "is not available for this model")


class _Router:
    enabled = False


class _FakeBedrock:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def invoke_model_with_response_stream(self, **_kw):
        self.calls += 1
        if self.calls <= self.failures:
            raise Exception(RET_ERR)
        return {"body": [], "ResponseMetadata": {"RequestId": "r1"}}


def _provider(fake, model_config):
    p = bp.BedrockProvider.__new__(bp.BedrockProvider)
    p.model_id = "us.anthropic.claude-fable-5-1"
    p.model_config = model_config
    p._region = "us-east-1"
    p._aws_profile = "shared"
    p.bedrock = fake
    p._region_router = _Router()
    p._build_request_body = lambda *a, **k: {}

    async def _parse(_resp, _cfg):
        yield StreamEnd()
    p._parse_stream = _parse
    return p


def _run(p):
    async def go():
        return [e async for e in p.stream_response([], None, [], None)]
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def _reset_throttle(monkeypatch):
    monkeypatch.setattr(bp, "_retention_heal_at", {})
    # The retention diagnostic makes live AWS reads; stub it everywhere here.
    monkeypatch.setattr(bp, "log_retention_diagnosis", lambda *a: {}, raising=False)


def test_diagnosis_reaches_the_user_error_and_log_names_region(monkeypatch):
    monkeypatch.setattr("app.utils.aws_utils.ensure_bedrock_data_retention_mode",
                        lambda **kw: (False, "denied"))
    seen = []
    monkeypatch.setattr(bp, "log_retention_diagnosis",
                        lambda *a: seen.append(a) or {
                            "model_id": a[0], "session_region": a[1], "required": a[3],
                            "account": "123456789012", "routes": ["us-east-1", "us-east-2"],
                            "unsatisfied": ["us-east-2"], "modes": {}})
    # ModeAwareLogger does not propagate, so caplog cannot see it; capture directly.
    warnings = []
    monkeypatch.setattr(bp.logger, "warning", lambda m, *a, **k: warnings.append(str(m)))
    events = _run(_provider(_FakeBedrock(failures=5),
                            {"required_data_retention_mode": "aws_review"}))
    assert seen == [("us.anthropic.claude-fable-5-1", "us-east-1", "shared", "aws_review")]
    msg = [e for e in events if isinstance(e, ErrorEvent)][0].message
    assert "regions below 'aws_review' in account 123456789012: us-east-2" in msg
    assert "the region this call was sent to" in msg
    assert any("model=us.anthropic.claude-fable-5-1 region=us-east-1 profile=shared" in w
               for w in warnings)


def test_lowered_switch_is_raised_and_call_retried(monkeypatch):
    calls = []
    monkeypatch.setattr("app.utils.aws_utils.ensure_bedrock_data_retention_mode",
                        lambda **kw: calls.append(kw) or (True, ""))
    fake = _FakeBedrock(failures=1)
    events = _run(_provider(fake, {"required_data_retention_mode": "aws_review"}))

    assert calls == [{"required_mode": "aws_review", "region": "us-east-1",
                      "profile_name": "shared"}]
    assert fake.calls == 2
    assert not any(isinstance(e, ErrorEvent) for e in events)
    assert any(isinstance(e, StreamEnd) for e in events)


def test_legacy_boolean_raises_to_provider_data_share(monkeypatch):
    calls = []
    monkeypatch.setattr("app.utils.aws_utils.ensure_bedrock_data_retention_mode",
                        lambda **kw: calls.append(kw) or (True, ""))
    _run(_provider(_FakeBedrock(failures=1), {"requires_provider_data_share": True}))
    assert calls[0]["required_mode"] == "provider_data_share"


def test_failed_restore_surfaces_reason_and_does_not_retry(monkeypatch):
    monkeypatch.setattr("app.utils.aws_utils.ensure_bedrock_data_retention_mode",
                        lambda **kw: (False, "HTTP 403 AccessDenied"))
    fake = _FakeBedrock(failures=5)
    events = _run(_provider(fake, {"required_data_retention_mode": "aws_review"}))

    assert fake.calls == 1
    err = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(err) == 1
    assert "HTTP 403 AccessDenied" in err[0].message
    assert "'aws_review' or higher in us-east-1" in err[0].message


def test_restore_is_throttled_per_profile_region(monkeypatch):
    calls = []
    monkeypatch.setattr("app.utils.aws_utils.ensure_bedrock_data_retention_mode",
                        lambda **kw: calls.append(kw) or (True, ""))
    # Someone keeps lowering it: every call fails even after the restore.
    fake = _FakeBedrock(failures=99)
    cfg = {"required_data_retention_mode": "aws_review"}
    first = _run(_provider(fake, cfg))
    second = _run(_provider(fake, cfg))

    assert len(calls) == 1, "second failure within the interval must not write again"
    assert "already attempted in the last minute" in \
        [e for e in second if isinstance(e, ErrorEvent)][0].message
    assert [e for e in first if isinstance(e, ErrorEvent)]


def test_unrelated_validation_error_does_not_touch_the_switch(monkeypatch):
    calls = []
    monkeypatch.setattr("app.utils.aws_utils.ensure_bedrock_data_retention_mode",
                        lambda **kw: calls.append(kw) or (True, ""))

    class _Other(_FakeBedrock):
        def invoke_model_with_response_stream(self, **_kw):
            self.calls += 1
            raise Exception("ValidationException: max_tokens too large")

    fake = _Other(failures=0)
    events = _run(_provider(fake, {"required_data_retention_mode": "aws_review"}))
    assert calls == [] and fake.calls == 1
    assert any(isinstance(e, ErrorEvent) for e in events)
