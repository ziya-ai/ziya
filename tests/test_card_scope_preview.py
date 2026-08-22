"""
Tests for POST /api/v1/projects/{pid}/task-cards/scope-preview.

This endpoint backs the "needs signing" notice on an AI-authored task-card
proposal and in its live preview modal.  Before it existed those surfaces
asked for /task-cards/draft/scope-status (a synthetic id), took the 404,
and silently rendered nothing — so the one moment a user decides whether to
run a model-authored card was the one moment its privilege escalation was
invisible.

The load-bearing property is that preview and saved-card status derive
escalation through the SAME floor subtraction, so the notice on the
proposal cannot disagree with the banner on the saved card.  A throwaway
keypair + isolated approval store keep this off /etc/ziya and ~/.ziya.
"""

import asyncio
import time
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.utils import scope_approvals as sa
from app.models.task_card import (
    Block, ScopeEntry, TaskCard, TaskCardCreate, TaskScope,
)
from app.api import task_cards as tc


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


def _escalating_spec():
    """An unsaved spec (no block ids — exactly what the parser produces).

    One escalating leaf, one benign leaf, under a container.
    """
    return TaskCardCreate(
        name="Deploy",
        description="",
        root=Block(block_type="group", name="Steps", body=[
            Block(block_type="task", name="Push",
                  scope=TaskScope(
                      shell_commands=["git push"],
                      paths=[ScopeEntry(path="out/", is_dir=True, write=True)])),
            Block(block_type="task", name="Read",
                  scope=TaskScope(paths=[ScopeEntry(path="a.py", read=True)])),
        ]),
    )


def _preview(spec, project_id="proj1", deck_scope=None):
    """Call the REAL endpoint, stubbing only the project lookup.

    Deliberately not a re-implementation: a test that transcribes the code
    it tests verifies only its own copy (see the note in
    test_card_scope_status_endpoint.py, where exactly that went stale).
    """
    class _Project:
        settings = type("S", (), {"taskScope": deck_scope})()

    class _ProjectStorage:
        def __init__(self, *a, **k):
            pass

        def get(self, _pid):
            return _Project()

    with patch.object(tc, "ProjectStorage", _ProjectStorage):
        return asyncio.run(tc.preview_card_scope(project_id, spec))


def test_preview_reports_escalating_leaf_only(keyed_store):
    st = _preview(_escalating_spec())
    assert [b["name"] for b in st["blocks"]] == ["Push"]
    assert st["blocks"][0]["escalation"] == {
        "shell_commands": ["git push"], "writable_paths": ["out/"],
    }


def test_preview_flags_needs_signature(keyed_store):
    st = _preview(_escalating_spec())
    assert st["preview"] is True
    assert st["anyNeedsSignature"] is True
    assert st["anyUnapproved"] is True
    assert st["blocks"][0]["needsSignature"] is True
    assert st["blocks"][0]["authorized"] is False


def test_preview_offers_no_sign_command(keyed_store):
    # An unsaved spec has no persisted block id, so no ziya-approve
    # invocation is mintable.  Emitting one would hand the user a command
    # that writes a record the runtime gate never reads — the same
    # "signed but still clamped" trap the container-row bug caused.
    st = _preview(_escalating_spec())
    assert st["blocks"][0]["signCommand"] == ""


def test_preview_stages_nothing_for_the_signer(keyed_store, tmp_path, monkeypatch):
    # Previewing a proposal the user never accepts must leave no trace.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(tc, "get_ziya_home", lambda: home)
    _preview(_escalating_spec())
    assert not (home / "pending_task_approvals.json").exists()


def test_preview_ignores_floor_covered_grants(keyed_store):
    # The reason this lives server-side: a client-side guess would flag
    # this card, and crying wolf on `.ziya/` teaches users to ignore the
    # notice entirely.
    spec = TaskCardCreate(
        name="Scratch", description="",
        root=Block(block_type="task", name="Note state",
                   scope=TaskScope(paths=[
                       ScopeEntry(path=".ziya/state.json", write=True)])),
    )
    st = _preview(spec)
    assert st["blocks"] == []
    assert st["anyNeedsSignature"] is False


def test_preview_counts_ancestor_scope_toward_the_leaf(keyed_store):
    # A grant on the container reaches the leaf through merge_scopes, so it
    # must be reported against the leaf — otherwise a model could hide
    # escalation from the notice by hoisting it to a parent block.
    spec = TaskCardCreate(
        name="Nested", description="",
        root=Block(block_type="group", name="Outer",
                   scope=TaskScope(shell_commands=["npm"]),
                   body=[Block(block_type="task", name="Build")]),
    )
    st = _preview(spec)
    assert [b["name"] for b in st["blocks"]] == ["Build"]
    assert st["blocks"][0]["escalation"]["shell_commands"] == ["npm"]


def test_preview_counts_card_scope_toward_the_leaf(keyed_store):
    spec = TaskCardCreate(
        name="CardGrant", description="",
        scope=TaskScope(shell_commands=["npm"]),
        root=Block(block_type="task", name="Build"),
    )
    st = _preview(spec)
    assert st["blocks"][0]["escalation"]["shell_commands"] == ["npm"]


def test_preview_counts_deck_scope_toward_the_leaf(keyed_store):
    # The deck scope is a real layer of the effective scope at run time, so
    # omitting it here would under-report what the run will request.
    spec = TaskCardCreate(
        name="DeckGrant", description="",
        root=Block(block_type="task", name="Build"),
    )
    st = _preview(spec, deck_scope=TaskScope(shell_commands=["npm"]))
    assert st["blocks"][0]["escalation"]["shell_commands"] == ["npm"]


def test_preview_keys_idless_blocks_distinctly(keyed_store):
    # Every block in an unsaved spec has id "", so a naive key would
    # collide and React would drop all but one row from the notice.
    spec = TaskCardCreate(
        name="Two", description="",
        root=Block(block_type="group", name="Steps", body=[
            Block(block_type="task", name="A",
                  scope=TaskScope(shell_commands=["npm"])),
            Block(block_type="task", name="B",
                  scope=TaskScope(shell_commands=["cargo"])),
        ]),
    )
    st = _preview(spec)
    keys = [b["blockId"] for b in st["blocks"]]
    assert len(keys) == 2
    assert len(set(keys)) == 2, f"row keys collided: {keys}"


def test_clean_spec_needs_no_signature(keyed_store):
    spec = TaskCardCreate(
        name="Clean", description="",
        root=Block(block_type="task", name="Think", instructions="Reason."),
    )
    st = _preview(spec)
    assert st["blocks"] == []
    assert st["anyNeedsSignature"] is False
    assert st["anyUnapproved"] is False


# ── Parity with the saved-card path ────────────────────────────────

def _saved_status(card, project_id="proj1", deck_scope=None):
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


def test_preview_and_saved_status_agree_on_escalation(keyed_store, tmp_path, monkeypatch):
    """The whole point of sharing _escalation_rows.

    If these ever diverge, the proposal notice and the saved-card banner
    disagree about the same card — which is worse than either being wrong
    alone, because the user cannot tell which to believe.
    """
    monkeypatch.setattr(tc, "get_ziya_home", lambda: tmp_path / "home")
    spec = _escalating_spec()
    preview = _preview(spec)

    # Same tree, now persisted with block ids assigned.
    root = spec.root.model_dump()
    root["id"] = "b-root"
    root["body"][0]["id"] = "b-esc"
    root["body"][1]["id"] = "b-benign"
    saved = _saved_status(TaskCard(id="card1", name="Deploy", description="",
                                   root=Block(**root)))

    assert ([b["escalation"] for b in preview["blocks"]]
            == [b["escalation"] for b in saved["blocks"]])
    assert ([b["name"] for b in preview["blocks"]]
            == [b["name"] for b in saved["blocks"]])
    # Unsigned in both cases, but only the saved one can offer a command.
    assert preview["anyNeedsSignature"] == saved["anyNeedsSignature"] is True
    assert preview["blocks"][0]["signCommand"] == ""
    assert saved["blocks"][0]["signCommand"].startswith("sudo ziya-approve")


def test_saved_status_marks_signed_block_as_not_needing_signature(keyed_store, tmp_path, monkeypatch):
    """needsSignature must track a real approval, not just mirror hasEscalation."""
    monkeypatch.setattr(tc, "get_ziya_home", lambda: tmp_path / "home")
    leaf = Block(block_type="task", id="b-esc", name="Push",
                 scope=TaskScope(shell_commands=["git push"]))
    card = TaskCard(id="card1", name="C", description="",
                    root=Block(block_type="group", id="b-root", name="Steps",
                               body=[leaf]))

    from app.models.task_card import merge_scopes
    scope = merge_scopes(None, None, card.root.scope, leaf.scope)
    h = sc.task_scope_hash(scope)
    at = int(time.time())
    sa.save_record({
        "task_id": "b-esc", "scope_hash": h, "approved_by": "tester",
        "approved_at": at,
        "signature": sc.sign_approval_record("b-esc", h, "tester", at),
    })

    st = _saved_status(card)
    assert st["blocks"][0]["authorized"] is True
    assert st["blocks"][0]["needsSignature"] is False
    assert st["anyNeedsSignature"] is False
