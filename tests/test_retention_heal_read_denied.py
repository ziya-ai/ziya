"""A role that may PUT the retention switch but not GET it can still heal.

Observed 2026-10-28: a call to global.anthropic.claude-fable-5-1 in
us-west-2 was rejected ("data retention mode 'default' is not available"),
the heal fired, and it failed with "Failed to read Bedrock data retention
mode: HTTP 403 ... not authorized to perform bedrock:GetAccountDataRetention".
_ensure_data_retention returned on the failed read and never tried the
write.  On the heal path the rejection already proves the mode is below the
requirement, so the read is redundant: assume_below=True writes anyway.
Startup / model-init keep the read-first behaviour (a blind write there
could lower a more permissive setting).
"""
import pytest

import app.utils.aws_utils as au

DENIED = ('{"Message":"User: arn:aws:sts::123456789012:assumed-role/ExampleBedrockRole/x '
          'is not authorized to perform: bedrock:GetAccountDataRetention"}')


class _Resp:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text, self._payload = status, text, payload or {}

    def json(self):
        return self._payload


class _Creds:
    access_key, secret_key, token = "AK", "SK", None

    def get_frozen_credentials(self):
        return self


class _Session:
    def get_credentials(self):
        return _Creds()


@pytest.fixture
def http(monkeypatch):
    import requests
    calls = {"get": 0, "put": [], "get_resp": _Resp(403, DENIED), "put_resp": _Resp(200)}
    monkeypatch.setattr(au, "create_fresh_boto3_session", lambda **kw: _Session())

    def _get(url, headers=None, timeout=None):
        calls["get"] += 1
        return calls["get_resp"]

    def _put(url, headers=None, data=None, timeout=None):
        calls["put"].append((url, data))
        return calls["put_resp"]
    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "put", _put)
    return calls


def test_read_denied_without_assume_below_does_not_write(http):
    """Startup / model-init with a role that cannot GET the switch.

    Observed 2026-10-30: bedrock-chatter could not start Ziya on fable5.1
    although us-west-2 was already 'aws_review'.  A denied read must not be
    fatal, and must not write (a blind write could lower a permissive mode).
    """
    ok, err = au.ensure_bedrock_data_retention_mode("aws_review", region="us-west-2")
    assert (ok, err) == (True, "")
    assert http["get"] == 1 and http["put"] == []


def test_read_denied_on_downgrade_still_fails(http):
    """A reset/downgrade must know the current mode, so it cannot proceed blind."""
    ok, err = au.ensure_bedrock_data_retention_mode(
        "inherit", region="us-west-2", allow_downgrade=True)
    assert not ok and "GetAccountDataRetention" in err
    assert http["put"] == []


def test_read_failure_other_than_denial_still_fails(http):
    """A 5xx is not a permission answer; keep reporting it."""
    http["get_resp"] = _Resp(503, "Service Unavailable")
    ok, err = au.ensure_bedrock_data_retention_mode("aws_review", region="us-west-2")
    assert not ok and "503" in err
    assert http["put"] == []


def test_startup_does_not_exit_when_read_is_denied():
    """Seam: app/main.py exits only on (False, ...); a denied read now returns True.

    Guards against main.py being changed to treat the warning path as fatal.
    """
    import pathlib
    src = pathlib.Path(au.__file__).resolve().parents[1].joinpath("main.py").read_text()
    i = src.index("DATA RETENTION SETUP FAILED")
    assert "if not _ok:" in src[max(0, i - 400):i]


def test_read_denied_with_assume_below_writes_the_mode(http):
    ok, err = au.ensure_bedrock_data_retention_mode(
        "aws_review", region="us-west-2", assume_below=True)
    assert (ok, err) == (True, "")
    assert http["get"] == 1
    assert len(http["put"]) == 1
    url, body = http["put"][0]
    assert url == "https://bedrock.us-west-2.amazonaws.com/data-retention"
    assert '"aws_review"' in body


def test_both_denied_reports_both(http):
    http["put_resp"] = _Resp(403, "not authorized to perform: bedrock:PutAccountDataRetention")
    ok, err = au.ensure_bedrock_data_retention_mode(
        "aws_review", region="us-west-2", assume_below=True)
    assert not ok
    assert "GetAccountDataRetention" in err and "PutAccountDataRetention" in err


def test_assume_below_still_respects_a_readable_permissive_mode(http):
    """If the read DOES succeed and shows a sufficient mode, nothing is written."""
    http["get_resp"] = _Resp(200, payload={"mode": "provider_data_share"})
    ok, _ = au.ensure_bedrock_data_retention_mode(
        "aws_review", region="us-west-2", assume_below=True)
    assert ok and http["put"] == []


def test_heal_passes_assume_below(monkeypatch):
    """Seam: BedrockProvider's heal path must opt in."""
    import app.providers.bedrock as bp
    seen = []
    monkeypatch.setattr("app.utils.aws_utils.ensure_bedrock_data_retention_mode",
                        lambda **kw: seen.append(kw) or (True, ""))
    bp.heal_retention_gate({"required_data_retention_mode": "aws_review"}, "us-west-2", None)
    assert seen and seen[0].get("assume_below") is True
