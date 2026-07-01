"""
Escalation-config integrity gate (ASR F-004 / F-007).

The shell server trusts a set of privilege-bearing environment variables
(ALLOW_COMMANDS, the write-policy fields, SAFE_GIT_OPERATIONS, YOLO_MODE) that
flow from the parent (mcp/manager.py) into the subprocess env. Before honoring
any of them, ShellServer.__init__ verifies that any escalation BEYOND the
built-in floor carries a valid root-minted Ed25519 signature (ZIYA_SCOPE_SIG).
Absent/invalid/replayed signature -> the escalation is clamped to the floor.

This pins the whole gate end-to-end:
  - the pure delta-vs-floor + signature logic in app.config.scope_canonical, and
  - its effect on a real ShellServer instance (escalations honored only when
    signed; dropped otherwise).

The signing side is simulated with a freshly generated Ed25519 keypair; the
public key is pointed at via ZIYA_APPROVE_PUBKEY (resolved at call time by
scope_canonical.public_key_path()).
"""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app.config.scope_canonical as sc
from app.mcp_servers.shell_server import ShellServer


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _sign(priv: Ed25519PrivateKey, delta: dict) -> str:
    """Produce the base64 signature a root signer would mint over a delta."""
    return base64.b64encode(priv.sign(sc.canonical(delta))).decode("ascii")


def _pubkey_pem(priv: Ed25519PrivateKey) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.fixture
def root_key():
    """The legitimate root signing key."""
    return Ed25519PrivateKey.generate()


@pytest.fixture
def attacker_key():
    """A different key — stands in for an agent-forged signature."""
    return Ed25519PrivateKey.generate()


@pytest.fixture
def pubkey_file(tmp_path, root_key):
    """Write the root public key where the verifier will look for it."""
    p = tmp_path / "approve_ed25519.pub"
    p.write_bytes(_pubkey_pem(root_key))
    return str(p)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Clear every escalation-bearing var so each test starts at the floor."""
    for key in sc.ESCALATION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ZIYA_APPROVE_PUBKEY", raising=False)


# ---------------------------------------------------------------------------
# Unit: the pure scope_canonical logic
# ---------------------------------------------------------------------------

def test_empty_delta_within_floor_needs_no_signature():
    # A config at/within the floor produces an empty delta -> authorized live.
    scope = sc.parse_env_scope({})
    assert sc.compute_delta(scope) == {}
    assert sc.is_env_scope_authorized({}) is True


def test_narrowing_is_not_an_escalation():
    # Requesting a SUBSET of the floor commands is not an escalation.
    scope = sc.parse_env_scope({"ALLOW_COMMANDS": "ls,cat"})
    assert sc.compute_delta(scope) == {}
    assert sc.is_env_scope_authorized({"ALLOW_COMMANDS": "ls,cat"}) is True


def test_command_beyond_floor_is_an_escalation():
    scope = sc.parse_env_scope({"ALLOW_COMMANDS": "ls,/usr/bin/danger"})
    delta = sc.compute_delta(scope)
    assert delta == {"ALLOW_COMMANDS": ["/usr/bin/danger"]}


def test_yolo_true_is_an_escalation():
    scope = sc.parse_env_scope({"YOLO_MODE": "true"})
    assert sc.compute_delta(scope) == {"YOLO_MODE": True}


def test_valid_signature_authorizes(monkeypatch, root_key, pubkey_file):
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", pubkey_file)
    env = {"ALLOW_COMMANDS": "/usr/bin/danger"}
    delta = sc.compute_delta(sc.parse_env_scope(env))
    env[sc.SIG_ENV_KEY] = _sign(root_key, delta)
    assert sc.is_env_scope_authorized(env) is True


def test_missing_signature_is_unauthorized(monkeypatch, pubkey_file):
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", pubkey_file)
    env = {"ALLOW_COMMANDS": "/usr/bin/danger"}  # no SIG
    assert sc.is_env_scope_authorized(env) is False


def test_attacker_signature_is_unauthorized(monkeypatch, attacker_key, pubkey_file):
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", pubkey_file)
    env = {"ALLOW_COMMANDS": "/usr/bin/danger"}
    delta = sc.compute_delta(sc.parse_env_scope(env))
    env[sc.SIG_ENV_KEY] = _sign(attacker_key, delta)  # wrong key
    assert sc.is_env_scope_authorized(env) is False


def test_replayed_signature_on_widened_delta_is_unauthorized(
    monkeypatch, root_key, pubkey_file
):
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", pubkey_file)
    # Legitimately sign one command...
    approved = {"ALLOW_COMMANDS": "/usr/bin/danger"}
    sig = _sign(root_key, sc.compute_delta(sc.parse_env_scope(approved)))
    # ...then try to reuse that signature for a WIDER set.
    widened = {"ALLOW_COMMANDS": "/usr/bin/danger,/usr/bin/worse", sc.SIG_ENV_KEY: sig}
    assert sc.is_env_scope_authorized(widened) is False


def test_missing_pubkey_fails_closed(monkeypatch):
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", "/nonexistent/key.pub")
    env = {"ALLOW_COMMANDS": "/usr/bin/danger", sc.SIG_ENV_KEY: "ZHVtbXk="}
    assert sc.is_env_scope_authorized(env) is False


def test_strip_escalations_clamps_but_keeps_narrowing():
    env = {
        "ALLOW_COMMANDS": "ls,/usr/bin/danger",  # ls within floor, danger beyond
        "YOLO_MODE": "true",
        sc.SIG_ENV_KEY: "whatever",
    }
    out = sc.strip_escalations(env)
    kept = [c for c in out["ALLOW_COMMANDS"].split(",") if c]
    assert "ls" in kept                     # narrowing preserved
    assert "/usr/bin/danger" not in kept    # escalation dropped
    assert out["YOLO_MODE"] == "false"      # YOLO forced off
    assert sc.SIG_ENV_KEY not in out        # signature var removed


def test_escalation_env_keys_cover_all_list_fields_plus_sig():
    # Single-source-of-truth invariant: the propagated key set must include
    # every privilege-bearing list field, YOLO, and the signature var.
    assert all(f in sc.ESCALATION_ENV_KEYS for f in sc._LIST_FIELDS)
    assert "YOLO_MODE" in sc.ESCALATION_ENV_KEYS
    assert sc.SIG_ENV_KEY in sc.ESCALATION_ENV_KEYS


# ---------------------------------------------------------------------------
# Integration: the gate's effect on a real ShellServer
# ---------------------------------------------------------------------------

def test_shellserver_clean_env_runs_at_floor(monkeypatch, pubkey_file):
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", pubkey_file)
    srv = ShellServer()
    assert srv.yolo_mode is False
    assert "/usr/bin/danger" not in srv.allowed_commands


def test_shellserver_drops_unsigned_escalation(monkeypatch, pubkey_file):
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", pubkey_file)
    monkeypatch.setenv("ALLOW_COMMANDS", "/usr/bin/danger")
    monkeypatch.setenv("YOLO_MODE", "true")
    srv = ShellServer()
    assert "/usr/bin/danger" not in srv.allowed_commands  # unsigned -> dropped
    assert srv.yolo_mode is False                          # unsigned YOLO -> off


def test_shellserver_honors_signed_escalation(monkeypatch, root_key, pubkey_file):
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", pubkey_file)
    monkeypatch.setenv("ALLOW_COMMANDS", "/usr/bin/danger")
    delta = sc.compute_delta(sc.parse_env_scope({"ALLOW_COMMANDS": "/usr/bin/danger"}))
    monkeypatch.setenv(sc.SIG_ENV_KEY, _sign(root_key, delta))
    srv = ShellServer()
    assert "/usr/bin/danger" in srv.allowed_commands  # signed -> honored


def test_shellserver_drops_forged_escalation(monkeypatch, attacker_key, pubkey_file):
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", pubkey_file)
    monkeypatch.setenv("ALLOW_COMMANDS", "/usr/bin/danger")
    delta = sc.compute_delta(sc.parse_env_scope({"ALLOW_COMMANDS": "/usr/bin/danger"}))
    monkeypatch.setenv(sc.SIG_ENV_KEY, _sign(attacker_key, delta))  # wrong key
    srv = ShellServer()
    assert "/usr/bin/danger" not in srv.allowed_commands  # forged -> dropped
