"""
Tests for GET /api/v1/projects/{pid}/task-cards/{cid}/scope-status (ASR F-001).

Verifies the per-block escalation-approval status walk that drives the task-card
"needs approval" UX: escalating blocks are reported (signed/unsigned), benign
blocks are omitted, and the sign command is surfaced. Reuses the same signed
approval store the gate enforces.

A throwaway Ed25519 keypair + temp approvals dir keep this hermetic.
"""

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.utils import scope_approvals as sa
from app.models.task_card import Block, TaskScope, ScopeEntry
from app.api import task_cards


@pytest.fixture
def keyed(tmp_path, monkeypatch):
    """Throwaway keypair + isolated approvals store."""
    priv = tmp_path / "k"
    pub = tmp_path / "k.pub"
    key = Ed25519PrivateKey.generate()
    priv.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    pub.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH))
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub))
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path / "approvals"))
    return key


def _esc_block():
    return Block(block_type="task", id="b-esc", name="Deploy",
                 scope=TaskScope(shell_commands=["npx jest"],
                                 paths=[ScopeEntry(path="out/", is_dir=True, write=True)]))


def _benign_block():
    return Block(block_type="task", id="b-ok", name="Read",
                 scope=TaskScope(tools=["file_read"],
                                 paths=[ScopeEntry(path="a.py", read=True)]))


def _card(root):
    # Minimal stand-in matching what _get_storage(...).get() returns: an object
    # with a .root Block. We patch the storage getter to return this.
    class _C:
        def __init__(self, root):
            self.root = root
    return _C(root)


def _patch_storage(monkeypatch, card):
    class _Storage:
        def get(self, _cid):
            return card
    monkeypatch.setattr(task_cards, "_get_storage", lambda _pid: _Storage())


@pytest.mark.asyncio
async def test_escalating_block_unsigned_reported_unauthorized(keyed, monkeypatch):
    root = Block(block_type="parallel", id="b-root", name="Root",
                 body=[_esc_block(), _benign_block()])
    _patch_storage(monkeypatch, _card(root))
    res = await task_cards.get_card_scope_status("p1", "c1")
    assert res["anyUnapproved"] is True
    # only the escalating block appears; benign omitted
    assert [b["blockId"] for b in res["blocks"]] == ["b-esc"]
    b = res["blocks"][0]
    assert b["authorized"] is False
    assert b["escalation"]["shell_commands"] == ["npx jest"]
    assert b["escalation"]["writable_paths"] == ["out/"]
    assert "sudo ziya-approve --task c1 --block b-esc --project p1" == b["signCommand"]


@pytest.mark.asyncio
async def test_signed_block_reported_authorized(keyed, monkeypatch):
    esc = _esc_block()
    h = sc.task_scope_hash(esc.scope)
    at = int(time.time())
    sa.save_record({
        "task_id": "b-esc", "scope_hash": h, "approved_by": "dcohn",
        "approved_at": at,
        "signature": sc.sign_approval_record("b-esc", h, "dcohn", at, private_key=keyed),
    })
    root = Block(block_type="parallel", id="b-root", name="Root", body=[esc])
    _patch_storage(monkeypatch, _card(root))
    res = await task_cards.get_card_scope_status("p1", "c1")
    assert res["anyUnapproved"] is False
    assert res["blocks"][0]["authorized"] is True


@pytest.mark.asyncio
async def test_benign_card_has_no_escalation_entries(keyed, monkeypatch):
    root = Block(block_type="task", id="b-only", name="Read",
                 scope=TaskScope(tools=["file_read"]))
    _patch_storage(monkeypatch, _card(root))
    res = await task_cards.get_card_scope_status("p1", "c1")
    assert res["anyUnapproved"] is False
    assert res["blocks"] == []


@pytest.mark.asyncio
async def test_editing_scope_voids_prior_approval(keyed, monkeypatch):
    """Sign one scope, then widen it: the stored hash no longer matches, so the
    block flips back to unauthorized (authorization binds to content)."""
    esc = _esc_block()
    h = sc.task_scope_hash(esc.scope)
    at = int(time.time())
    sa.save_record({
        "task_id": "b-esc", "scope_hash": h, "approved_by": "dcohn",
        "approved_at": at,
        "signature": sc.sign_approval_record("b-esc", h, "dcohn", at, private_key=keyed),
    })
    # widen the scope after signing
    widened = Block(block_type="task", id="b-esc", name="Deploy",
                    scope=TaskScope(shell_commands=["npx jest", "rm"],
                                    paths=[ScopeEntry(path="out/", is_dir=True, write=True)]))
    _patch_storage(monkeypatch, _card(widened))
    res = await task_cards.get_card_scope_status("p1", "c1")
    assert res["anyUnapproved"] is True
    assert res["blocks"][0]["authorized"] is False
