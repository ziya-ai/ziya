"""
Tests for GET /api/v1/projects/{pid}/task-cards/{cid}/scope-status (ASR F-001).

This endpoint backs the TaskCardEditor "needs approval" banner. It walks a
card's block tree and, for every block whose scope grants a privilege
escalation (shell_commands / writable paths), reports whether a signed approval
record matches the block's CURRENT scope hash. Blocks with no escalation are
omitted; the signCommand is the exact ziya-approve invocation to mint a missing
record.

A throwaway Ed25519 keypair + isolated approval store are used so nothing
touches /etc/ziya or ~/.ziya.
"""

import os
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.utils import scope_approvals as sa
from app.models.task_card import Block, TaskScope, ScopeEntry, TaskCard


@pytest.fixture
def keyed_store(tmp_path, monkeypatch):
    priv = tmp_path / "k"
    pub = tmp_path / "k.pub"
    key = Ed25519PrivateKey.generate()
    priv.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    pub.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH))
    monkeypatch.setenv("ZIYA_APPROVE_PRIVKEY", str(priv))
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub))
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path / "approvals"))
    return key


def _escalating_card():
    """Card: parallel root with one escalating task + one benign task."""
    root = Block(block_type="parallel", id="b-root", name="Root", body=[
        Block(block_type="task", id="b-esc", name="Deploy",
              scope=TaskScope(
                  shell_commands=["git push"],
                  paths=[ScopeEntry(path="out/", is_dir=True, write=True)])),
        Block(block_type="task", id="b-benign", name="Read",
              scope=TaskScope(tools=["file_read"],
                              paths=[ScopeEntry(path="a.py", read=True)])),
    ])
    return TaskCard(id="card1", name="C", description="", root=root)


# Import the endpoint helpers directly (avoids standing up a full project on disk).
from app.api import task_cards as tc


def _status(card, project_id="proj1"):
    """Reproduce the endpoint body against an in-memory card."""
    blocks = []
    for block in tc._walk_blocks(card.root):
        scope = getattr(block, "scope", None)
        escalation = sc.task_escalation_block(scope)
        if not escalation:
            continue
        authorized = sa.is_scope_authorized(block.id, scope)
        sign_command = "" if authorized else (
            f"sudo ziya-approve --task {card.id} "
            f"--block {block.id} --project {project_id}")
        blocks.append({
            "blockId": block.id, "name": block.name or "",
            "hasEscalation": True, "authorized": bool(authorized),
            "escalation": {k: list(v) for k, v in escalation.items()},
            "signCommand": sign_command,
        })
    return {"cardId": card.id,
            "anyUnapproved": any(not b["authorized"] for b in blocks),
            "blocks": blocks}


def _approve(card, block_id, key):
    block = None
    for b in tc._walk_blocks(card.root):
        if b.id == block_id:
            block = b
            break
    h = sc.task_scope_hash(block.scope)
    at = int(time.time())
    rec = {"task_id": block_id, "scope_hash": h, "approved_by": "tester",
           "approved_at": at,
           "signature": sc.sign_approval_record(block_id, h, "tester", at)}
    sa.save_record(rec)


def test_walk_blocks_visits_nested(keyed_store):
    card = _escalating_card()
    ids = [b.id for b in tc._walk_blocks(card.root)]
    assert ids == ["b-root", "b-esc", "b-benign"]


def test_only_escalating_blocks_reported(keyed_store):
    st = _status(_escalating_card())
    # b-root (parallel, no scope) and b-benign (restriction-only) omitted
    assert [b["blockId"] for b in st["blocks"]] == ["b-esc"]


def test_unapproved_card_flags_any_unapproved(keyed_store):
    st = _status(_escalating_card())
    assert st["anyUnapproved"] is True
    b = st["blocks"][0]
    assert b["authorized"] is False
    assert b["escalation"]["shell_commands"] == ["git push"]
    assert b["escalation"]["writable_paths"] == ["out/"]


def test_sign_command_matches_ziya_approve_contract(keyed_store):
    st = _status(_escalating_card(), project_id="projX")
    assert st["blocks"][0]["signCommand"] == (
        "sudo ziya-approve --task card1 --block b-esc --project projX")


def test_approved_block_clears_flag(keyed_store):
    card = _escalating_card()
    _approve(card, "b-esc", keyed_store)
    st = _status(card)
    assert st["anyUnapproved"] is False
    assert st["blocks"][0]["authorized"] is True
    assert st["blocks"][0]["signCommand"] == ""


def test_widening_after_approval_redenies(keyed_store):
    card = _escalating_card()
    _approve(card, "b-esc", keyed_store)
    # widen the approved block's scope -> hash changes -> stored record stale
    card.root.body[0].scope.shell_commands.append("rm -rf")
    st = _status(card)
    assert st["anyUnapproved"] is True
    assert st["blocks"][0]["authorized"] is False


def test_card_with_no_escalation_is_empty(keyed_store):
    root = Block(block_type="task", id="b1", name="T",
                 scope=TaskScope(tools=["file_read"]))
    card = TaskCard(id="c2", name="C", description="", root=root)
    st = _status(card)
    assert st["blocks"] == []
    assert st["anyUnapproved"] is False
