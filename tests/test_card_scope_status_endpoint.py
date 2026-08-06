"""
Tests for GET /api/v1/projects/{pid}/task-cards/{cid}/scope-status (ASR F-001).

This endpoint backs the TaskCardEditor "needs approval" banner. It walks a
card's block tree and, for every block whose EFFECTIVE scope (deck-level
project scope + card scope + every ancestor block's scope + its own, merged
additively) grants a privilege escalation (shell_commands / writable paths),
reports whether a signed approval record matches the block's CURRENT
effective-scope hash. Blocks with no escalation are omitted; the signCommand
is the exact ziya-approve invocation to mint a missing record.

A throwaway Ed25519 keypair + isolated approval store are used so nothing
touches /etc/ziya or ~/.ziya.
"""

import asyncio
import os
import time
from unittest.mock import patch

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


def _status(card, project_id="proj1", deck_scope=None):
    """Call the REAL endpoint against an in-memory card.

    This previously re-implemented the endpoint body inline, and the copy
    went stale without anyone noticing: when the endpoint was corrected to
    skip container blocks (only leaf tasks are gated at runtime), the
    transcription kept reporting them — so three tests here failed against
    a CORRECT implementation, and would have kept passing against a broken
    one.  A test that re-implements what it is testing verifies only its
    own copy.

    Only the two external dependencies are stubbed — the card lookup and
    the project record that supplies the deck scope — so every assertion
    below now exercises the shipped code path.
    """
    class _Storage:
        def get(self, _cid):
            return card

    class _Project:
        settings = type("S", (), {"taskScope": deck_scope})()

    class _ProjectStorage:
        def __init__(self, *a, **k):
            pass

        def get(self, _pid):
            return _Project()

    with patch.object(tc, "_get_storage", lambda _pid: _Storage()), \
         patch.object(tc, "ProjectStorage", _ProjectStorage):
        return asyncio.run(tc.get_card_scope_status(project_id, card.id))


def _effective_scope(card, block_id, deck_scope=None):
    """Compute the effective (merged) scope for a block by id."""
    from app.models.task_card import merge_scopes
    for block, ancestor_scopes in tc._walk_blocks(card.root):
        if block.id == block_id:
            return merge_scopes(deck_scope, card.scope, *ancestor_scopes,
                                 getattr(block, "scope", None))
    return None


def _approve(card, block_id, key, deck_scope=None):
    scope = _effective_scope(card, block_id, deck_scope=deck_scope)
    h = sc.task_scope_hash(scope)
    at = int(time.time())
    rec = {"task_id": block_id, "scope_hash": h, "approved_by": "tester",
           "approved_at": at,
           "signature": sc.sign_approval_record(block_id, h, "tester", at)}
    sa.save_record(rec)


def test_walk_blocks_visits_nested(keyed_store):
    card = _escalating_card()
    ids = [b.id for b, _ancestors in tc._walk_blocks(card.root)]
    assert ids == ["b-root", "b-esc", "b-benign"]


def test_walk_blocks_ancestor_chain_grows_with_depth(keyed_store):
    card = _escalating_card()
    chains = {b.id: ancestors for b, ancestors in tc._walk_blocks(card.root)}
    assert chains["b-root"] == ()
    # Root's own scope (None here) is the sole ancestor entry for its children.
    assert chains["b-esc"] == (card.root.scope,)
    assert chains["b-benign"] == (card.root.scope,)


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


# ── New: deck / card / ancestor scope hierarchy ───────────────────────────


def test_card_level_scope_contributes_escalation(keyed_store):
    """A card-level scope granting a writable path escalates an otherwise
    benign leaf task, and the reported escalation reflects the union."""
    root = Block(block_type="task", id="b1", name="T",
                 scope=TaskScope(tools=["file_read"]))
    card = TaskCard(id="c3", name="C", description="", root=root,
                     scope=TaskScope(paths=[ScopeEntry(path="out/", is_dir=True, write=True)]))
    st = _status(card)
    assert [b["blockId"] for b in st["blocks"]] == ["b1"]
    assert st["blocks"][0]["escalation"]["writable_paths"] == ["out/"]


def test_deck_level_scope_contributes_escalation(keyed_store):
    """A deck (project-wide) scope granting a shell command escalates a
    leaf task that otherwise has no scope at all."""
    root = Block(block_type="task", id="b1", name="T")
    card = TaskCard(id="c4", name="C", description="", root=root)
    deck = TaskScope(shell_commands=["pytest"])
    st = _status(card, deck_scope=deck)
    assert [b["blockId"] for b in st["blocks"]] == ["b1"]
    assert st["blocks"][0]["escalation"]["shell_commands"] == ["pytest"]


def test_ancestor_container_scope_contributes_escalation(keyed_store):
    """A Repeat/Parallel container's own scope escalates every leaf task
    beneath it, even when the leaf's own scope carries no escalation."""
    root = Block(block_type="parallel", id="b-root", name="Root",
                 scope=TaskScope(shell_commands=["make test"]),
                 body=[Block(block_type="task", id="b-leaf", name="Leaf")])
    card = TaskCard(id="c5", name="C", description="", root=root)
    st = _status(card)
    assert [b["blockId"] for b in st["blocks"]] == ["b-leaf"]
    assert st["blocks"][0]["escalation"]["shell_commands"] == ["make test"]


def test_container_block_is_never_reported_even_with_its_own_escalation(keyed_store):
    """Only ids the RUNTIME gates may be reported.

    authorize_scope is called from execute_task_block alone, which
    block_executor invokes for block_type == "task" — so a container's
    scope is never hashed under the container's own id.  Reporting one
    produced a signCommand that wrote a record nothing reads: the
    operator saw "✓ Signed" while the card kept running at the floor.

    The duplicate was also silent-by-construction: an ancestor's scope
    merges into its descendants, so container and leaf hash IDENTICALLY
    and the editor rendered the same escalation twice.
    """
    leaf = Block(block_type="task", id="b-leaf", name="Leaf")
    root = Block(block_type="repeat", id="b-root", name="Loop",
                 repeat_mode="count", repeat_count=2,
                 scope=TaskScope(shell_commands=["make test"]),
                 body=[leaf])
    card = TaskCard(id="c-container", name="C", description="", root=root)
    st = _status(card)
    ids = [b["blockId"] for b in st["blocks"]]
    assert "b-root" not in ids, (
        "a container id must not be reported: nothing gates it at runtime"
    )
    assert ids == ["b-leaf"]
    # The grant is not lost — it reaches the leaf via the ancestor chain.
    assert st["blocks"][0]["escalation"]["shell_commands"] == ["make test"]


def test_no_signCommand_names_a_non_task_block(keyed_store):
    """Every emitted signCommand must target a gated id.  Guards the
    whole class of bug rather than the one nesting shape above."""
    inner = Block(block_type="task", id="b-inner", name="Inner",
                  scope=TaskScope(shell_commands=["dd"]))
    mid = Block(block_type="parallel", id="b-mid", name="Mid",
                scope=TaskScope(shell_commands=["wget"]), body=[inner])
    root = Block(block_type="group", id="b-grp", name="Grp", body=[mid])
    card = TaskCard(id="c-deep", name="C", description="", root=root)
    st = _status(card)
    for b in st["blocks"]:
        if b["signCommand"]:
            assert f"--block {b['blockId']}" in b["signCommand"]
        assert b["blockId"] not in ("b-grp", "b-mid")
def test_approval_binds_to_full_effective_hash_not_leaf_alone(keyed_store):
    """Approving a leaf's own scope hash does NOT authorize it once a
    deck/card/ancestor layer adds escalation the approval never covered —
    the effective hash differs, so the stored record no longer matches."""
    root = Block(block_type="task", id="b1", name="T",
                 scope=TaskScope(shell_commands=["pytest"]))
    card = TaskCard(id="c6", name="C", description="", root=root)
    # Sign only the leaf's OWN scope hash (simulating a stale/incomplete record).
    leaf_only_hash = sc.task_scope_hash(root.scope)
    at = int(time.time())
    sa.save_record({
        "task_id": "b1", "scope_hash": leaf_only_hash, "approved_by": "tester",
        "approved_at": at,
        "signature": sc.sign_approval_record("b1", leaf_only_hash, "tester", at),
    })
    # Now a deck scope adds a writable path -> effective hash changes.
    deck = TaskScope(paths=[ScopeEntry(path="out/", is_dir=True, write=True)])
    st = _status(card, deck_scope=deck)
    assert st["blocks"][0]["authorized"] is False


def test_approving_full_effective_scope_authorizes(keyed_store):
    """Approving the TRUE effective (deck+card+ancestor+leaf) hash clears
    the unapproved flag."""
    root = Block(block_type="task", id="b1", name="T",
                 scope=TaskScope(shell_commands=["pytest"]))
    card = TaskCard(id="c7", name="C", description="", root=root)
    deck = TaskScope(paths=[ScopeEntry(path="out/", is_dir=True, write=True)])
    _approve(card, "b1", keyed_store, deck_scope=deck)
    st = _status(card, deck_scope=deck)
    assert st["anyUnapproved"] is False
    assert st["blocks"][0]["authorized"] is True
