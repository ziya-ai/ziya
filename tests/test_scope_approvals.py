"""
Tests for the signed task-scope approval gate (ASR F-001, design §4.1/§4.2).

Covers the store + chokepoint in app/utils/scope_approvals.py and the
task-scope hashing / record signing in app/config/scope_canonical.py:

  - task_scope_hash: empty for non-escalating scopes, stable + order-independent
    for escalating ones, changes when privileges widen.
  - is_scope_authorized / authorize_scope: the fail-closed gate — no record,
    hash-mismatch (scope edited after approval), and forged-signature all deny;
    a valid signed record matching the current hash authorizes.
  - _UnauthorizedScope: the floor-only stand-in strips shell_commands and write
    flags while preserving tools/skills/cwd/readable paths.

The root keypair is faked with a generated Ed25519 key and the
ZIYA_APPROVE_PUBKEY / ZIYA_SCOPE_APPROVALS_DIR env overrides, so no real
/etc/ziya provisioning is needed.
"""

import json
import time

import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from app.config import scope_canonical as sc
from app.utils import scope_approvals as sa
from app.models.task_card import TaskScope, ScopeEntry


# ── keypair + env fixtures ────────────────────────────────────────────────────

@pytest.fixture
def root_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def wrong_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def env(tmp_path, root_key, monkeypatch):
    """Write the public key + point the store at a tmp dir."""
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


def _write_signed_record(task_id, scope, signing_key, *, approved_by="dcohn"):
    """Mint + persist a signed approval record (what `sudo ziya-approve` does)."""
    h = sc.task_scope_hash(scope)
    approved_at = int(time.time())
    sig = sc.sign_approval_record(task_id, h, approved_by, approved_at, signing_key)
    record = {
        "task_id": task_id,
        "scope_hash": h,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "signature": sig,
    }
    sa.save_record(record)
    return record


# ── task_scope_hash ───────────────────────────────────────────────────────────

def test_empty_scope_has_no_hash():
    assert sc.task_scope_hash(TaskScope()) == ""


def test_restrictions_only_have_no_hash():
    # tools/skills/readable-paths are restrictions, not escalations -> no hash.
    scope = TaskScope(
        tools=["file_read"], skills=["k"], cwd="sub",
        paths=[ScopeEntry(path="a.py", read=True)],
    )
    assert sc.task_scope_hash(scope) == ""


def test_shell_commands_make_a_hash():
    scope = TaskScope(shell_commands=["pytest"])
    assert sc.task_scope_hash(scope) != ""


def test_writable_path_makes_a_hash_readonly_does_not():
    ro = TaskScope(paths=[ScopeEntry(path="a", read=True)])
    rw = TaskScope(paths=[ScopeEntry(path="a", write=True)])
    assert sc.task_scope_hash(ro) == ""
    assert sc.task_scope_hash(rw) != ""


def test_hash_is_order_independent():
    a = TaskScope(shell_commands=["pytest", "make test"])
    b = TaskScope(shell_commands=["make test", "pytest"])
    assert sc.task_scope_hash(a) == sc.task_scope_hash(b)


def test_widening_changes_hash():
    narrow = TaskScope(shell_commands=["pytest"])
    wide = TaskScope(shell_commands=["pytest", "curl"])
    assert sc.task_scope_hash(narrow) != sc.task_scope_hash(wide)


# ── the gate: is_scope_authorized / authorize_scope ───────────────────────────

def test_non_escalating_scope_authorized_without_record(env):
    scope = TaskScope(tools=["file_read"])
    assert sa.is_scope_authorized("b-1", scope) is True
    assert sa.authorize_scope("b-1", scope) is scope  # returned unchanged


def test_escalating_scope_denied_without_record(env):
    scope = TaskScope(shell_commands=["curl"])
    assert sa.is_scope_authorized("b-1", scope) is False
    authd = sa.authorize_scope("b-1", scope)
    assert authd is not scope
    assert authd.shell_commands == []  # floor


def test_signed_record_authorizes(env, root_key):
    scope = TaskScope(shell_commands=["curl"], paths=[ScopeEntry(path="o/", write=True, is_dir=True)])
    _write_signed_record("b-1", scope, root_key)
    assert sa.is_scope_authorized("b-1", scope) is True
    assert sa.authorize_scope("b-1", scope) is scope


def test_editing_scope_after_approval_denies(env, root_key):
    # Approve a narrow scope, then widen it -> stored hash no longer matches.
    approved = TaskScope(shell_commands=["pytest"])
    _write_signed_record("b-1", approved, root_key)
    widened = TaskScope(shell_commands=["pytest", "curl"])
    assert sa.is_scope_authorized("b-1", widened) is False


def test_forged_signature_denied(env, wrong_key):
    # A record signed by a non-root key must not verify against the real pubkey.
    scope = TaskScope(shell_commands=["curl"])
    _write_signed_record("b-1", scope, wrong_key)
    assert sa.is_scope_authorized("b-1", scope) is False


def test_record_for_other_task_does_not_authorize(env, root_key):
    scope = TaskScope(shell_commands=["curl"])
    _write_signed_record("b-OTHER", scope, root_key)
    # Same scope, different task_id -> no record under b-1.
    assert sa.is_scope_authorized("b-1", scope) is False


def test_deleted_record_denies(env, root_key):
    scope = TaskScope(shell_commands=["curl"])
    rec = _write_signed_record("b-1", scope, root_key)
    assert sa.is_scope_authorized("b-1", scope) is True
    sa._record_path("b-1").unlink()
    assert sa.is_scope_authorized("b-1", scope) is False


def test_record_path_rejects_traversal(env):
    # A malicious/hand-edited task id must not escape the store dir.
    p = sa._record_path("../../etc/passwd")
    assert sa._store_dir() in p.parents


# ── _UnauthorizedScope floor stand-in ─────────────────────────────────────────

def test_unauthorized_scope_strips_escalations_keeps_restrictions(env):
    orig = TaskScope(
        shell_commands=["curl", "make deploy"],
        tools=["file_read"], skills=["k"], cwd="sub",
        paths=[
            ScopeEntry(path="out/", write=True, is_dir=True),
            ScopeEntry(path="a.py", read=True),
        ],
    )
    floor = sa.authorize_scope("b-1", orig)  # no record -> floor
    assert floor is not orig
    # escalations stripped
    assert floor.shell_commands == []
    assert all(not getattr(e, "write", False) for e in floor.paths)
    # restrictions preserved
    assert set(floor.tools) == {"file_read"}
    assert floor.skills == ["k"]
    assert floor.cwd == "sub"
    # readable path still present (path kept, write flag cleared)
    assert sorted(e.path for e in floor.paths) == ["a.py", "out/"]
# NOTE: CLI-task (tasks.yaml allow-block) authorization is covered in its own
# file, tests/test_cli_task_approval.py — kept separate from the card-scope
# tests here so the two authorization shapes (card scope vs CLI allow block)
# stay legible.

