"""
Regression tests for task-approval expiry + enterprise TTL bound.

Covers:
  - Backward compat: a record signed WITHOUT expires_at still verifies
    (existing on-disk approvals keep working).
  - Temporal enforcement (scope_canonical.verify_approval_record): a validly
    signed record whose expires_at is in the past is rejected; a future one
    passes.
  - Tamper resistance: adding/altering expires_at on a record signed without
    it (or with a different value) breaks the signature.
  - Policy bound (scope_approvals._within_policy_bound): no policy -> allow;
    policy + unbounded record -> deny; policy + within-bound -> allow;
    policy + over-bound -> deny.
"""

import time
import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from app.config import scope_canonical as sc


@pytest.fixture
def signing_keys(tmp_path, monkeypatch):
    """Provision a throwaway Ed25519 keypair and point scope_canonical at it."""
    priv = Ed25519PrivateKey.generate()
    priv_path = tmp_path / "approve_ed25519"
    pub_path = tmp_path / "approve_ed25519.pub"
    priv_path.write_bytes(priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    pub_path.write_bytes(priv.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ))
    monkeypatch.setenv("ZIYA_APPROVE_PRIVKEY", str(priv_path))
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub_path))
    return priv_path, pub_path


def _record(task_id="b-test", scope_hash="a" * 64, approved_by="dcohn",
            approved_at=None, expires_at=None):
    if approved_at is None:
        approved_at = int(time.time())
    sig = sc.sign_approval_record(task_id, scope_hash, approved_by,
                                  approved_at, expires_at=expires_at)
    rec = {
        "task_id": task_id, "scope_hash": scope_hash,
        "approved_by": approved_by, "approved_at": approved_at,
        "signature": sig,
    }
    if expires_at is not None:
        rec["expires_at"] = expires_at
    return rec


class TestBackwardCompat:
    def test_record_without_expiry_still_verifies(self, signing_keys):
        rec = _record()  # no expires_at — the pre-expiry record shape
        assert "expires_at" not in rec
        assert sc.verify_approval_record(rec) is True


class TestTemporalEnforcement:
    def test_future_expiry_verifies(self, signing_keys):
        rec = _record(expires_at=int(time.time()) + 3600)
        assert sc.verify_approval_record(rec) is True

    def test_past_expiry_denied(self, signing_keys):
        rec = _record(approved_at=int(time.time()) - 7200,
                      expires_at=int(time.time()) - 3600)
        assert sc.verify_approval_record(rec) is False


class TestTamperResistance:
    def test_adding_expiry_to_unbounded_record_breaks_sig(self, signing_keys):
        rec = _record()  # signed WITHOUT expires_at
        rec["expires_at"] = int(time.time()) + 999999  # forge a far-future exp
        assert sc.verify_approval_record(rec) is False

    def test_extending_expiry_breaks_sig(self, signing_keys):
        rec = _record(expires_at=int(time.time()) + 3600)
        rec["expires_at"] = int(time.time()) + 999999  # try to extend
        assert sc.verify_approval_record(rec) is False


class TestPolicyBound:
    def _reload(self):
        # _within_policy_bound reads get_max_approval_ttl lazily; import fresh.
        from app.utils import scope_approvals
        return scope_approvals

    def test_no_policy_allows_unbounded(self, monkeypatch):
        sa = self._reload()
        monkeypatch.setattr("app.plugins.get_max_approval_ttl", lambda: None,
                            raising=False)
        rec = {"approved_at": int(time.time())}  # no expires_at
        assert sa._within_policy_bound(rec) == (True, None)

    def test_policy_denies_unbounded(self, monkeypatch):
        sa = self._reload()
        monkeypatch.setattr("app.plugins.get_max_approval_ttl",
                            lambda: 30 * 86400, raising=False)
        rec = {"approved_at": int(time.time())}  # no expires_at under a policy
        ok, reason = sa._within_policy_bound(rec)
        assert ok is False
        assert reason == f"unbounded_approval_requires_expiry:{30 * 86400}"

    def test_policy_allows_within_bound(self, monkeypatch):
        sa = self._reload()
        monkeypatch.setattr("app.plugins.get_max_approval_ttl",
                            lambda: 30 * 86400, raising=False)
        now = int(time.time())
        rec = {"approved_at": now, "expires_at": now + 10 * 86400}
        assert sa._within_policy_bound(rec) == (True, None)

    def test_policy_denies_over_bound(self, monkeypatch):
        sa = self._reload()
        monkeypatch.setattr("app.plugins.get_max_approval_ttl",
                            lambda: 30 * 86400, raising=False)
        now = int(time.time())
        rec = {"approved_at": now, "expires_at": now + 60 * 86400}
        ok, reason = sa._within_policy_bound(rec)
        assert ok is False
        assert reason.startswith("approval_lifetime_exceeds_policy:")