"""
ASR INFO-01 — ``/api/info`` must not return account identifiers or key material.

The endpoint is unauthenticated and is *not* debug-only: it backs the
user-visible Info panel (SystemInfo.tsx) and the PDF export header
(pdfExport.ts). Gating it behind a debug flag would break two normal
affordances to remove two fields, so the fields were removed instead.

``get_caller_identity()`` is still called -- its success or failure IS the
credential-validity signal users need for troubleshooting -- but neither the
Account nor the access-key prefix is returned. ``status`` carries no secret.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ACCOUNT_ID = "123456789012"
ACCESS_KEY = "AKIAEXAMPLE1234567890"


class _FakeCredentials:
    access_key = ACCESS_KEY
    secret_key = "wJalrXUtnFEMI-EXAMPLE-KEY"


class _FakeSts:
    def __init__(self, calls, error=None):
        self._calls = calls
        self._error = error

    def get_caller_identity(self):
        self._calls.append("get_caller_identity")
        if self._error:
            raise self._error
        return {
            "Account": ACCOUNT_ID,
            "Arn": f"arn:aws:iam::{ACCOUNT_ID}:user/example",
            "UserId": "AIDAEXAMPLE",
        }


@pytest.fixture
def info_client(monkeypatch):
    """Build a client over just the debug router, with boto3 stubbed.

    Returns (client_factory, calls) — the factory takes an optional STS error
    so the Valid and Expired branches can be exercised separately.
    """
    import boto3

    calls = []

    def _make(error=None):
        class _FakeSession:
            def __init__(self, profile_name=None, region_name=None):
                self.profile_name = profile_name
                self.region_name = region_name

            def get_credentials(self):
                return _FakeCredentials()

            def client(self, name, region_name=None):
                return _FakeSts(calls, error=error)

        monkeypatch.setattr(boto3, "Session", _FakeSession)
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")

        from app.routes.debug_routes import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    return _make, calls


def _info(client):
    resp = client.get("/api/info")
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestCredentialFieldsRemoved:
    def test_account_id_not_returned(self, info_client):
        make, _ = info_client
        info = _info(make())
        assert "aws" in info, "the AWS branch must have run for this to mean anything"
        assert "account_id" not in info["aws"]

    def test_access_key_not_returned(self, info_client):
        make, _ = info_client
        info = _info(make())
        assert "access_key" not in info["aws"]

    def test_values_absent_from_the_entire_payload(self, info_client):
        """Scoped on the values, not the old key names.

        Asserting only ``"account_id" not in info["aws"]`` would still pass if
        the same data reappeared under a different key or nested elsewhere.
        """
        make, _ = info_client
        body = json.dumps(_info(make()))
        assert ACCOUNT_ID not in body
        assert ACCESS_KEY not in body
        assert ACCESS_KEY[:8] not in body

    def test_secret_key_never_appears(self, info_client):
        make, _ = info_client
        assert "wJalrXUtnFEMI-EXAMPLE-KEY" not in json.dumps(_info(make()))


class TestTroubleshootingSignalPreserved:
    """The reason for trimming rather than gating: the panel must keep working."""

    def test_status_valid_when_credentials_resolve(self, info_client):
        make, _ = info_client
        assert _info(make())["aws"]["status"] == "Valid"

    def test_caller_identity_still_probed(self, info_client):
        """Positive control that the code path ran -- a handler that simply
        skipped the STS call would satisfy every absence assertion above while
        silently dropping the credential-validity signal."""
        make, calls = info_client
        _info(make())
        assert calls == ["get_caller_identity"]

    def test_expired_token_reported_without_key_prefix(self, info_client):
        """The Expired branch previously returned the key prefix as well."""
        make, _ = info_client
        info = _info(make(error=Exception("ExpiredToken: the token has expired")))
        assert info["aws"]["status"] == "Expired"
        assert "access_key" not in info["aws"]
        assert ACCESS_KEY not in json.dumps(info)

    def test_invalid_credentials_reported(self, info_client):
        make, _ = info_client
        info = _info(make(error=Exception("InvalidClientTokenId: bad key")))
        assert info["aws"]["status"] == "Invalid credentials"

    def test_non_secret_context_still_present(self, info_client):
        """Profile and region are what actually help a user diagnose a
        credential problem, and are not secrets."""
        make, _ = info_client
        aws = _info(make())["aws"]
        assert "profile" in aws
        assert "region" in aws


class TestEnvVarMaskingStillApplies:
    """Neighbouring control (ASR T0-5): credential-adjacent ZIYA_* env vars are
    masked in the same payload. Pinned here so the INFO-01 edit is not credited
    with a masking regression it would otherwise hide.
    """

    def test_credential_adjacent_env_var_masked(self, info_client, monkeypatch):
        monkeypatch.setenv("ZIYA_TEST_API_KEY", "super-secret-value-12345")
        make, _ = info_client
        info = _info(make())
        env = info.get("environment_variables", {})
        assert "ZIYA_TEST_API_KEY" in env
        assert "super-secret-value-12345" not in json.dumps(env)
