"""Saving the shell-config modal must not void a signature over an edit that
does not add privilege.

Field report (2026-10-09): with an already-signed escalation in the config
(non-floor commands), ticking a floor git operation in the modal and saving
demanded a fresh ``sudo ziya-approve``.  The signature covers the escalation
DELTA (scope_canonical.compute_delta), and a floor-only edit does not change
it, but POST /shell-config compared the raw privilege-field value sets and
dropped ZIYA_SCOPE_SIG on any change at all.

Follow-on: removing ONE signed command (a strict reduction) also forced a
re-sign, because the verifier checked the signature against the exact delta.
A narrowed save now carries a bundled signature (signed delta + signature)
that the verifier accepts for any subset of what root signed.

Route tests drive the real route against a sandboxed ~/.ziya and assert what
the modal reads back (the file's signature status) and what the subprocess is
spawned with (is_env_scope_authorized is the shell_server init gate).
"""
import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.routes import mcp_routes
from app.routes.mcp_routes import ShellConfig, update_shell_config, _compute_signature_status

ESCALATED = "zzz-escalated-cmd"   # never in the floor
ESCALATED_2 = "zzz-escalated-two"
ESCALATED_3 = "zzz-another-escalation"


@pytest.fixture
def env(tmp_path, monkeypatch):
    priv, pub = tmp_path / "k", tmp_path / "k.pub"
    key = Ed25519PrivateKey.generate()
    priv.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                                  serialization.PublicFormat.OpenSSH))
    monkeypatch.setenv("ZIYA_APPROVE_PRIVKEY", str(priv))
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub))
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
    monkeypatch.setenv("HOME", str(tmp_path))

    mgr = MagicMock()
    mgr.server_configs = {"shell": {"enabled": True}}
    mgr.clients = {}
    mgr.restart_server = AsyncMock(return_value=True)
    monkeypatch.setattr(mcp_routes, "get_mcp_manager", lambda: mgr)
    return tmp_path, mgr


def _floor_git():
    return sorted(sc._floor()["SAFE_GIT_OPERATIONS"])


def _base_cfg(git_ops, commands, persist=True):
    return ShellConfig(allowedCommands=commands, safeGitOperations=git_ops,
                       safeWritePaths=[], allowedWritePatterns=[],
                       allowedInterpreters=[], persist=persist)


def _seed_signed_file(home, git_ops, commands):
    """A config on disk carrying a signed escalation, as ziya-approve leaves it."""
    shell_env = {"ALLOW_COMMANDS": ",".join(commands),
                 "SAFE_GIT_OPERATIONS": ",".join(git_ops)}
    shell_env["ZIYA_SCOPE_SIG"] = sc.sign_delta(sc.compute_delta(sc.parse_env_scope(shell_env)))
    path = home / ".ziya" / "mcp_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {"shell": {"env": shell_env}}}))
    assert _compute_signature_status(shell_env)["authorized"] is True
    return path


def _file_env(path):
    return json.loads(path.read_text())["mcpServers"]["shell"]["env"]


def _spawned(mgr):
    return mgr.restart_server.call_args.args[1]["env"]


def _save(cfg):
    out = asyncio.new_event_loop().run_until_complete(update_shell_config(cfg))
    assert out["success"], out
    return out


# ── floor-only edits ────────────────────────────────────────────────────────

def test_adding_a_floor_git_op_keeps_the_signature(env):
    home, mgr = env
    floor = _floor_git()
    assert len(floor) >= 2
    before, added = floor[:-1], floor[-1]
    commands = ["ls", ESCALATED]
    path = _seed_signed_file(home, before, commands)

    _save(_base_cfg(before + [added], commands))

    fe = _file_env(path)
    assert added in fe["SAFE_GIT_OPERATIONS"].split(","), "the edit was not persisted"
    status = _compute_signature_status(fe)
    assert status["hasEscalation"] is True
    assert status["authorized"] is True, "floor-only edit voided the signature"
    assert sc.is_env_scope_authorized(_spawned(mgr))


def test_removing_a_floor_git_op_keeps_the_signature(env):
    home, _ = env
    floor = _floor_git()
    commands = ["ls", ESCALATED]
    path = _seed_signed_file(home, floor, commands)
    _save(_base_cfg(floor[:-1], commands))
    assert _compute_signature_status(_file_env(path))["authorized"] is True


def test_adding_a_non_floor_command_still_requires_signing(env):
    home, mgr = env
    floor = _floor_git()
    path = _seed_signed_file(home, floor, ["ls", ESCALATED])
    _save(_base_cfg(floor, ["ls", ESCALATED, ESCALATED_3]))
    fe = _file_env(path)
    assert "ZIYA_SCOPE_SIG" not in fe
    assert _compute_signature_status(fe)["authorized"] is False
    assert not sc.is_env_scope_authorized(_spawned(mgr))


# ── narrowing a signed escalation ───────────────────────────────────────────

def test_removing_a_signed_command_keeps_the_rest_authorized(env):
    home, mgr = env
    floor = _floor_git()
    path = _seed_signed_file(home, floor, ["ls", ESCALATED, ESCALATED_2])

    _save(_base_cfg(floor, ["ls", ESCALATED]))

    fe = _file_env(path)
    assert fe["ALLOW_COMMANDS"].split(",") == ["ls", ESCALATED], "the edit was not persisted"
    status = _compute_signature_status(fe)
    assert status["hasEscalation"] is True
    assert status["pendingDelta"] == {"ALLOW_COMMANDS": [ESCALATED]}
    assert status["authorized"] is True, "removing a signed command voided the rest"
    assert sc.is_env_scope_authorized(_spawned(mgr))
    # The removed command is gone from what the subprocess runs with.
    assert ESCALATED_2 not in _spawned(mgr)["ALLOW_COMMANDS"]


def test_after_narrowing_adding_an_unsigned_command_still_requires_signing(env):
    home, mgr = env
    floor = _floor_git()
    path = _seed_signed_file(home, floor, ["ls", ESCALATED, ESCALATED_2])
    _save(_base_cfg(floor, ["ls", ESCALATED]))
    _save(_base_cfg(floor, ["ls", ESCALATED, ESCALATED_3]))
    fe = _file_env(path)
    assert "ZIYA_SCOPE_SIG" not in fe
    assert _compute_signature_status(fe)["authorized"] is False
    assert not sc.is_env_scope_authorized(_spawned(mgr))


def test_restoring_the_signed_set_restores_the_plain_signature(env):
    home, _ = env
    floor = _floor_git()
    path = _seed_signed_file(home, floor, ["ls", ESCALATED, ESCALATED_2])
    original = _file_env(path)["ZIYA_SCOPE_SIG"]
    _save(_base_cfg(floor, ["ls", ESCALATED]))
    _save(_base_cfg(floor, ["ls", ESCALATED, ESCALATED_2]))
    fe = _file_env(path)
    assert fe["ZIYA_SCOPE_SIG"] == original
    assert _compute_signature_status(fe)["authorized"] is True


def test_narrowing_to_the_floor_drops_the_signature(env):
    home, mgr = env
    floor = _floor_git()
    path = _seed_signed_file(home, floor, ["ls", ESCALATED])
    _save(_base_cfg(floor, ["ls"]))
    fe = _file_env(path)
    assert "ZIYA_SCOPE_SIG" not in fe
    assert _compute_signature_status(fe) == {
        "hasEscalation": False, "authorized": True, "pendingDelta": {}}
    assert sc.is_env_scope_authorized(_spawned(mgr))


def test_non_persistent_narrowing_carries_a_signature_to_the_subprocess(env):
    home, mgr = env
    floor = _floor_git()
    path = _seed_signed_file(home, floor, ["ls", ESCALATED, ESCALATED_2])
    before = path.read_text()
    _save(_base_cfg(floor, ["ls", ESCALATED], persist=False))
    assert path.read_text() == before
    spawned = _spawned(mgr)
    assert spawned["ZIYA_SCOPE_SIG"].startswith("v2.")
    assert sc.is_env_scope_authorized(spawned)


# ── verifier: bundled signatures ────────────────────────────────────────────

def _d(*cmds):
    return {"ALLOW_COMMANDS": sorted(cmds)}


def test_bundle_authorizes_a_subset_of_what_was_signed(env):
    signed = _d(ESCALATED, ESCALATED_2)
    bundle = sc.bundle_signature(signed, sc.sign_delta(signed))
    assert sc.verify_delta_signature(_d(ESCALATED), bundle)
    assert sc.verify_delta_signature(signed, bundle)


def test_bundle_never_authorizes_beyond_what_was_signed(env):
    signed = _d(ESCALATED)
    bundle = sc.bundle_signature(signed, sc.sign_delta(signed))
    assert not sc.verify_delta_signature(_d(ESCALATED, ESCALATED_2), bundle)
    assert not sc.verify_delta_signature({**signed, "YOLO_MODE": True}, bundle)


def test_forged_bundle_fails(env):
    # Claim a wider signed delta than the signature actually covers.
    bundle = sc.bundle_signature(_d(ESCALATED, ESCALATED_2), sc.sign_delta(_d(ESCALATED)))
    assert not sc.verify_delta_signature(_d(ESCALATED), bundle)
    assert sc.rebind_signature(bundle, _d(ESCALATED, ESCALATED_2), _d(ESCALATED)) is None


def test_plain_signature_keeps_exact_semantics(env):
    signed = _d(ESCALATED, ESCALATED_2)
    plain = sc.sign_delta(signed)
    assert sc.verify_delta_signature(signed, plain)
    assert not sc.verify_delta_signature(_d(ESCALATED), plain)


@pytest.mark.parametrize("value", [
    "v2.",
    "v2.!!!.sig",
    "v2." + base64.b64encode(b"[1,2]").decode() + ".sig",
    "v2." + base64.b64encode(b'{"EVIL":["x"]}').decode() + ".sig",
    "v2." + base64.b64encode(b'{"ALLOW_COMMANDS":"x"}').decode() + ".sig",
    "v2." + base64.b64encode(b'{"YOLO_MODE":false}').decode() + ".sig",
])
def test_malformed_bundles_fail_closed(env, value):
    assert not sc.verify_delta_signature(_d(ESCALATED), value)
    assert sc.rebind_signature(value, _d(ESCALATED, ESCALATED_2), _d(ESCALATED)) is None


def test_rebind_refuses_an_invalid_plain_signature(env):
    bad = sc.sign_delta(_d(ESCALATED_3))
    assert sc.rebind_signature(bad, _d(ESCALATED, ESCALATED_2), _d(ESCALATED)) is None


def test_rebind_cases(env):
    signed = _d(ESCALATED, ESCALATED_2)
    plain = sc.sign_delta(signed)
    assert sc.rebind_signature(plain, signed, signed) == plain
    assert sc.rebind_signature(plain, signed, {}) is None
    assert sc.rebind_signature(plain, signed, _d(ESCALATED, ESCALATED_3)) is None
    narrowed = sc.rebind_signature(plain, signed, _d(ESCALATED))
    assert narrowed.startswith("v2.")
    # A bundle narrows further, and returns to plain when the full set is back.
    assert sc.rebind_signature(narrowed, _d(ESCALATED), signed) == plain
