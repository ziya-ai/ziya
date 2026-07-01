"""
Tests for the shell-config signature-status reporter (ASR F-004, UI support).

``_compute_signature_status`` (app/routes/mcp_routes.py) lets the shell-config
GUI show whether the running shell config carries privilege escalations beyond
the built-in floor and whether they are covered by a valid root signature —
without the frontend needing to know the floor (defined in Python). These tests
pin the contract the ShellConfigModal banner depends on:

  - floor-only config         -> hasEscalation False, authorized True
  - unsigned escalation        -> hasEscalation True,  authorized False, delta listed
  - signed escalation          -> hasEscalation True,  authorized True
  - tampered (signed+widened)  -> hasEscalation True,  authorized False
  - never raises               -> conservative "unauthorized" on bad input

A throwaway Ed25519 keypair is generated per test and pointed at via
ZIYA_APPROVE_PRIVKEY / ZIYA_APPROVE_PUBKEY, so nothing touches /etc/ziya.
"""

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.routes.mcp_routes import _compute_signature_status


@pytest.fixture
def keyed_env(tmp_path, monkeypatch):
    """Generate a throwaway keypair and point the verifier/signer at it."""
    priv_p = tmp_path / "approve_ed25519"
    pub_p = tmp_path / "approve_ed25519.pub"
    key = Ed25519PrivateKey.generate()
    priv_p.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    pub_p.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ))
    monkeypatch.setenv("ZIYA_APPROVE_PRIVKEY", str(priv_p))
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub_p))
    return key


def _sign_env(env: dict) -> str:
    """Return the ZIYA_SCOPE_SIG the signer would write for this env's delta."""
    scope = sc.parse_env_scope(env)
    delta = sc.compute_delta(scope)
    return sc.sign_delta(delta)


# ── floor / no-escalation cases ────────────────────────────────────────────────

def test_floor_only_has_no_escalation():
    status = _compute_signature_status({"ALLOW_COMMANDS": "ls,cat,grep,sed"})
    assert status["hasEscalation"] is False
    assert status["authorized"] is True
    assert status["pendingDelta"] == {}


def test_empty_env_has_no_escalation():
    status = _compute_signature_status({})
    assert status["hasEscalation"] is False
    assert status["authorized"] is True


def test_narrowing_is_not_escalation():
    # A subset of the floor is not an escalation.
    status = _compute_signature_status({"ALLOW_COMMANDS": "ls,cat"})
    assert status["hasEscalation"] is False


# ── unsigned escalation ─────────────────────────────────────────────────────────

def test_unsigned_escalation_is_unauthorized():
    status = _compute_signature_status({"ALLOW_COMMANDS": "ls,aws,/usr/bin/danger"})
    assert status["hasEscalation"] is True
    assert status["authorized"] is False
    # the beyond-floor entries are surfaced for the UI
    assert "/usr/bin/danger" in status["pendingDelta"]["ALLOW_COMMANDS"]
    assert "aws" in status["pendingDelta"]["ALLOW_COMMANDS"]
    # floor commands are NOT in the delta
    assert "ls" not in status["pendingDelta"].get("ALLOW_COMMANDS", [])


def test_yolo_escalation_surfaced():
    status = _compute_signature_status({"YOLO_MODE": "true"})
    assert status["hasEscalation"] is True
    assert status["authorized"] is False
    assert "YOLO_MODE" in status["pendingDelta"]


# ── signed escalation ───────────────────────────────────────────────────────────

def test_signed_escalation_is_authorized(keyed_env):
    env = {"ALLOW_COMMANDS": "ls,aws,pandoc"}
    env[sc.SIG_ENV_KEY] = _sign_env(env)
    status = _compute_signature_status(env)
    assert status["hasEscalation"] is True
    assert status["authorized"] is True


def test_signed_then_widened_is_unauthorized(keyed_env):
    # Sign for {aws,pandoc}, then add /usr/bin/danger without re-signing.
    env = {"ALLOW_COMMANDS": "ls,aws,pandoc"}
    env[sc.SIG_ENV_KEY] = _sign_env(env)
    env["ALLOW_COMMANDS"] = "ls,aws,pandoc,/usr/bin/danger"
    status = _compute_signature_status(env)
    assert status["hasEscalation"] is True
    assert status["authorized"] is False  # stale signature no longer covers the delta


def test_forged_signature_is_unauthorized(keyed_env):
    env = {"ALLOW_COMMANDS": "ls,aws"}
    env[sc.SIG_ENV_KEY] = base64.b64encode(b"\x00" * 64).decode("ascii")
    status = _compute_signature_status(env)
    assert status["hasEscalation"] is True
    assert status["authorized"] is False


# ── robustness: never raise ─────────────────────────────────────────────────────

def test_never_raises_on_garbage_input():
    # Non-string values / odd shapes must not blow up the config GET; the
    # reporter falls back to a conservative "unauthorized escalation".
    for bad in [{"ALLOW_COMMANDS": None}, {"YOLO_MODE": 123}, {"ALLOW_COMMANDS": ["a", "b"]}]:
        status = _compute_signature_status(bad)
        assert set(status.keys()) == {"hasEscalation", "authorized", "pendingDelta"}
