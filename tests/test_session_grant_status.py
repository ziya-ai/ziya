"""Unit tests for app.utils.session_grant_status (temporary-grant indicator).

The indicator must satisfy one invariant: it reports "active" IFF the shell
subprocess would honor the same grant. So every case here is built with the
real signing/verification primitives from scope_canonical — a genuinely
signed grant for the right nonce reports active; a stale-nonce, wrong-key,
or malformed record reports None.
"""

import base64
import json
import time

import pytest

from app.config import scope_canonical as sc
from app.utils.session_grant_status import session_grant_status


@pytest.fixture()
def keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode("ascii")
    return priv, pub_b64


def _mint(nonce, delta, priv):
    record = sc.sign_session_grant(
        nonce, delta, sc.EPHEMERAL_PROVIDER,
        granted_by="test-user", granted_at=int(time.time()),
        private_key=priv,
    )
    return json.dumps(record)


DELTA = {"ALLOW_COMMANDS": ["ffmpeg", "fluidsynth"]}


def test_valid_grant_reports_active_with_delta(keypair):
    priv, pub_b64 = keypair
    status = session_grant_status(_mint("nonce-1", DELTA, priv), "nonce-1", pub_b64)
    assert status is not None
    assert status["active"] is True
    assert status["provider"] == sc.EPHEMERAL_PROVIDER
    assert status["grantedBy"] == "test-user"
    assert status["delta"] == {"ALLOW_COMMANDS": ["ffmpeg", "fluidsynth"]}


def test_no_grant_reports_none():
    assert session_grant_status(None, "nonce-1") is None
    assert session_grant_status("", "nonce-1") is None


def test_stale_nonce_reports_none(keypair):
    """The 2026-08-22 incident shape: a grant bound to a previous server
    session (or another server's nonce) must NOT show as active."""
    priv, pub_b64 = keypair
    grant = _mint("old-nonce", DELTA, priv)
    assert session_grant_status(grant, "new-nonce", pub_b64) is None


def test_wrong_key_reports_none(keypair):
    priv, _ = keypair
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    other_pub = base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    ).decode("ascii")
    grant = _mint("nonce-1", DELTA, priv)
    assert session_grant_status(grant, "nonce-1", other_pub) is None


def test_tampered_delta_reports_none(keypair):
    """Widening the delta after signing must fail (scope_hash mismatch)."""
    priv, pub_b64 = keypair
    record = json.loads(_mint("nonce-1", DELTA, priv))
    record["delta"]["ALLOW_COMMANDS"].append("rm")
    assert session_grant_status(json.dumps(record), "nonce-1", pub_b64) is None


def test_malformed_records_report_none():
    for bad in ("not json", "[1,2]", json.dumps({"delta": "notadict"}),
                json.dumps({"delta": {}}), json.dumps({"no": "delta"})):
        assert session_grant_status(bad, "nonce-1", "AAAA") is None


def test_missing_nonce_reports_none(keypair):
    priv, pub_b64 = keypair
    grant = _mint("nonce-1", DELTA, priv)
    assert session_grant_status(grant, None, pub_b64) is None
    assert session_grant_status(grant, "", pub_b64) is None


def test_scalar_delta_values_are_listified(keypair):
    priv, pub_b64 = keypair
    delta = {"YOLO_MODE": True}
    status = session_grant_status(_mint("n", delta, priv), "n", pub_b64)
    assert status is not None
    assert status["delta"] == {"YOLO_MODE": ["True"]}


def test_never_raises_on_hostile_input():
    # bytes-ish garbage, huge strings, wrong types — advisory surface
    assert session_grant_status("\x00\xff", "n") is None
    assert session_grant_status(json.dumps({"delta": {"A": ["x"]}}), "n") is None
