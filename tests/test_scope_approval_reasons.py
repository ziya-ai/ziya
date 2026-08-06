"""
Tests for the denial-reason plumbing added to app/utils/scope_approvals.py
(is_scope_authorized_with_reason / is_cli_task_authorized_with_reason).

Motivation: a signed-but-still-shows-unsigned card was undiagnosable from the
UI because the gate only returned a bare bool. These reason codes are surfaced
through GET /task-cards/{id}/scope-status (see app/api/task_cards.py) so the
editor's approval banner can show WHY, not just THAT, an escalation is denied.
"""

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.utils import scope_approvals as sa
from app.models.task_card import TaskScope


@pytest.fixture
def root_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def env(tmp_path, root_key, monkeypatch):
    pub = root_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    pub_path = tmp_path / "approve_ed25519.pub"
    pub_path.write_bytes(pub)
    store = tmp_path / "scope_approvals"
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub_path))
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(store))
    return {"pub": str(pub_path), "store": store}


def _sign(task_id, scope, signing_key, *, approved_by="dcohn", expires_at=None):
    h = sc.task_scope_hash(scope)
    approved_at = int(time.time())
    sig = sc.sign_approval_record(task_id, h, approved_by, approved_at,
                                  signing_key, expires_at=expires_at)
    record = {
        "task_id": task_id, "scope_hash": h, "approved_by": approved_by,
        "approved_at": approved_at, "signature": sig,
    }
    if expires_at is not None:
        record["expires_at"] = expires_at
    sa.save_record(record)
    return record


# ``wget`` rather than ``curl`` for TaskScope-based cases below:
# ``task_escalation_block`` subtracts the privilege floor, and ``curl`` is
# in the base shell allowlist with no further runtime gate, so a
# curl-only scope hashes to "" and needs no approval — which would make
# each denial assertion here vacuously false.  (The CLI-task mirror at
# the bottom of this file keeps ``curl``: ``cli_escalation_block`` does
# NOT perform that subtraction, so a curl grant is still escalating
# there.  Verified: cli_task_hash({'commands':['curl']}) is non-empty.)
def test_no_record_reason(env):
    scope = TaskScope(shell_commands=["wget"])
    ok, reason = sa.is_scope_authorized_with_reason("b-1", scope)
    assert ok is False
    assert reason == "no_record"


def test_scope_hash_mismatch_reason(env, root_key):
    _sign("b-1", TaskScope(shell_commands=["curl"]), root_key)
    ok, reason = sa.is_scope_authorized_with_reason(
        "b-1", TaskScope(shell_commands=["curl", "wget"]))
    assert ok is False
    assert reason == "scope_hash_mismatch"


def test_signature_invalid_reason(env, root_key):
    """A record whose signature was forged with a different key."""
    other_key = Ed25519PrivateKey.generate()
    _sign("b-1", TaskScope(shell_commands=["wget"]), other_key)
    ok, reason = sa.is_scope_authorized_with_reason(
        "b-1", TaskScope(shell_commands=["wget"]))
    assert ok is False
    assert reason == "signature_invalid"


def test_authorized_has_no_reason(env, root_key):
    scope = TaskScope(shell_commands=["curl"])
    _sign("b-1", scope, root_key)
    ok, reason = sa.is_scope_authorized_with_reason("b-1", scope)
    assert ok is True
    assert reason is None


def test_non_escalating_scope_has_no_reason(env):
    scope = TaskScope(tools=["file_read"])
    ok, reason = sa.is_scope_authorized_with_reason("b-1", scope)
    assert ok is True
    assert reason is None


def test_is_scope_authorized_still_returns_plain_bool(env, root_key):
    """Backward compatibility: existing boolean-only callers (the runtime
    gate) must be unaffected by the reason-returning refactor."""
    scope = TaskScope(shell_commands=["wget"])
    assert sa.is_scope_authorized("b-1", scope) is False
    _sign("b-1", scope, root_key)
    assert sa.is_scope_authorized("b-1", scope) is True


def test_unbounded_approval_denied_reason_under_policy(env, root_key, monkeypatch):
    """An approval signed with no expires_at, checked under an enterprise
    policy that mandates a bound, must report the specific reason code with
    the policy's max TTL embedded — this is the exact bug reported: a valid
    signature that still shows unsigned because of a missing expiry."""
    scope = TaskScope(shell_commands=["git add"])
    _sign("b-1", scope, root_key)  # no expires_at
    monkeypatch.setattr(sa, "_within_policy_bound", sa._within_policy_bound)  # no-op, keep real fn
    monkeypatch.setattr("app.plugins.get_max_approval_ttl", lambda: 7776000)
    ok, reason = sa.is_scope_authorized_with_reason("b-1", scope)
    assert ok is False
    assert reason == "unbounded_approval_requires_expiry:7776000"


def test_bounded_approval_within_policy_is_authorized(env, root_key, monkeypatch):
    scope = TaskScope(shell_commands=["git add"])
    approved_at = int(time.time())
    expires_at = approved_at + 1000
    _sign("b-1", scope, root_key, expires_at=expires_at)
    monkeypatch.setattr("app.plugins.get_max_approval_ttl", lambda: 7776000)
    ok, reason = sa.is_scope_authorized_with_reason("b-1", scope)
    assert ok is True
    assert reason is None


def test_approval_lifetime_exceeds_policy_reason(env, root_key, monkeypatch):
    scope = TaskScope(shell_commands=["git add"])
    approved_at = int(time.time())
    expires_at = approved_at + 200  # lifetime 200s
    _sign("b-1", scope, root_key, expires_at=expires_at)
    monkeypatch.setattr("app.plugins.get_max_approval_ttl", lambda: 100)  # policy allows only 100s
    ok, reason = sa.is_scope_authorized_with_reason("b-1", scope)
    assert ok is False
    assert reason.startswith("approval_lifetime_exceeds_policy:")


def test_no_policy_bound_means_unbounded_approval_ok(env, root_key, monkeypatch):
    scope = TaskScope(shell_commands=["git add"])
    _sign("b-1", scope, root_key)  # no expires_at
    monkeypatch.setattr("app.plugins.get_max_approval_ttl", lambda: None)
    ok, reason = sa.is_scope_authorized_with_reason("b-1", scope)
    assert ok is True
    assert reason is None


# ── CLI-task mirror ───────────────────────────────────────────────────────────

def test_cli_task_no_record_reason(env):
    from app.models.task_card import TaskScope as _TS  # allow block reuses same shape
    allow = {"commands": ["curl"]}
    ok, reason = sa.is_cli_task_authorized_with_reason("cli:t#name", allow)
    assert ok is False
    assert reason == "no_record"


def test_cli_task_authorized_bool_unaffected(env, root_key):
    allow = {"commands": ["curl"]}
    h = sc.cli_task_hash(allow)
    approved_at = int(time.time())
    sig = sc.sign_approval_record("cli:t#name", h, "dcohn", approved_at, root_key)
    sa.save_record({
        "task_id": "cli:t#name", "scope_hash": h, "approved_by": "dcohn",
        "approved_at": approved_at, "signature": sig,
    })
    assert sa.is_cli_task_authorized("cli:t#name", allow) is True
