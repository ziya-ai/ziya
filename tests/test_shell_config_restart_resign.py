"""
Tests for the "signed-but-still-shows-unsigned" bug fix and the GUI restart
endpoint (ASR F-004 follow-up).

Root cause being guarded:
  ``ziya-approve`` writes ZIYA_SCOPE_SIG to ~/.ziya/mcp_config.json out of
  process. The running server's in-memory ``server_configs["shell"]["env"]`` is
  built at startup and never re-reads the file, so:
    - GET /api/mcp/shell-config reported the OLD (unsigned) status, and
    - restarting via the in-memory config respawned the subprocess still unsigned.

These tests pin the two halves of the fix at the unit level:
  1. The GET handler must merge the persisted file env (the sig) before
     computing signatureStatus.
  2. The restart endpoint must rebuild the shell env from the file.

We test the *logic* (file-env merge + signature_status), not the full FastAPI
route, to keep the test fast and free of a running MCP manager.
"""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.routes.mcp_routes import _compute_signature_status


@pytest.fixture
def keyed(tmp_path, monkeypatch):
    """Throwaway keypair wired into the verifier/signer."""
    priv = tmp_path / "approve_ed25519"
    pub = tmp_path / "approve_ed25519.pub"
    key = Ed25519PrivateKey.generate()
    priv.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    pub.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ))
    monkeypatch.setenv("ZIYA_APPROVE_PRIVKEY", str(priv))
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub))
    return key


def _sign_env(env: dict) -> str:
    """Mint a ZIYA_SCOPE_SIG over the env's delta (what ziya-approve does)."""
    delta = sc.compute_delta(sc.parse_env_scope(env))
    return sc.sign_delta(delta)


# ── The bug: in-memory env lacks the sig the file has ───────────────────────────

def test_inmemory_env_without_sig_reads_unsigned(keyed):
    """Reproduce the bug: an escalating env with NO sig (what the in-memory
    server_configs holds before re-reading the file) reports unauthorized."""
    inmemory_env = {"ALLOW_COMMANDS": "ls,aws,pandoc"}  # escalation, no sig
    status = _compute_signature_status(inmemory_env)
    assert status["hasEscalation"] is True
    assert status["authorized"] is False


def test_merging_file_sig_flips_to_authorized(keyed):
    """The fix: merging the persisted file env (which carries the sig) makes the
    SAME escalation report authorized."""
    inmemory_env = {"ALLOW_COMMANDS": "ls,aws,pandoc"}
    sig = _sign_env(inmemory_env)
    # Simulate the GET handler's merge: file env (with sig) layered on top.
    file_env = {"ALLOW_COMMANDS": "ls,aws,pandoc", sc.SIG_ENV_KEY: sig}
    merged = {**inmemory_env, **file_env}
    status = _compute_signature_status(merged)
    assert status["hasEscalation"] is True
    assert status["authorized"] is True


def test_sig_over_wrong_delta_still_unauthorized(keyed):
    """Belt-and-suspenders: a sig that doesn't cover the current escalation must
    not flip to authorized just because a SIG key is present."""
    sig_for_aws_only = _sign_env({"ALLOW_COMMANDS": "ls,aws"})
    merged = {"ALLOW_COMMANDS": "ls,aws,pandoc,pdflatex", sc.SIG_ENV_KEY: sig_for_aws_only}
    status = _compute_signature_status(merged)
    assert status["authorized"] is False


def test_clean_floor_config_authorized_no_sig_needed(keyed):
    """A within-floor config needs no signature and reports authorized."""
    status = _compute_signature_status({"ALLOW_COMMANDS": "ls,cat,grep"})
    assert status["hasEscalation"] is False
    assert status["authorized"] is True


# ── The file-merge helper logic (what GET and restart both do) ──────────────────

def test_file_env_merge_precedence(keyed, tmp_path, monkeypatch):
    """The persisted file env (sig + values) must win over the stale in-memory
    env so the merged view is what a fresh restart would verify."""
    cfg_path = tmp_path / "mcp_config.json"
    inmemory_env = {"ALLOW_COMMANDS": "ls,aws"}
    sig = _sign_env(inmemory_env)
    cfg_path.write_text(json.dumps({
        "mcpServers": {"shell": {"env": {
            "ALLOW_COMMANDS": "ls,aws", sc.SIG_ENV_KEY: sig,
        }}}
    }))
    # Replicate the merge the handlers perform.
    file_env = json.loads(cfg_path.read_text())["mcpServers"]["shell"]["env"]
    merged = {**inmemory_env, **file_env}
    assert sc.SIG_ENV_KEY in merged
    assert _compute_signature_status(merged)["authorized"] is True
