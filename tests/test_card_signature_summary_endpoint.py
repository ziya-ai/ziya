"""
GET /api/v1/projects/{pid}/task-cards/signature-summary.

Feeds the header Task Cards button outline.  Asserted on the outermost
surface (the response), plus the two properties that make it safe to poll on
every window focus: it writes nothing, and it is not shadowed by the
``/{card_id}`` route declared after it.
"""

import asyncio
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.api import task_cards as tc
from app.config import scope_canonical as sc
from app.models.task_card import Block, ScopeEntry, TaskCard, TaskScope, merge_scopes
from app.utils import scope_approvals as sa


@pytest.fixture
def keyed_store(tmp_path, monkeypatch):
    priv, pub = tmp_path / "k", tmp_path / "k.pub"
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


def _escalating(card_id):
    return TaskCard(id=card_id, name=f"Card {card_id}", description="", root=Block(
        block_type="task", id=f"{card_id}-b", name="Deploy",
        scope=TaskScope(shell_commands=["git push"],
                        paths=[ScopeEntry(path="out/", is_dir=True, write=True)])))


def _benign(card_id):
    return TaskCard(id=card_id, name=f"Card {card_id}", description="", root=Block(
        block_type="task", id=f"{card_id}-b", name="Read",
        scope=TaskScope(tools=["file_read"], paths=[ScopeEntry(path="a.py", read=True)])))


def _summary(cards, ziya_home, monkeypatch):
    class _Storage:
        def list(self, **_kw):
            return cards

    class _Project:
        settings = type("S", (), {"taskScope": None})()

    class _ProjectStorage:
        def __init__(self, *a, **k): pass
        def get(self, _pid): return _Project()

    monkeypatch.setattr(tc, "_get_storage", lambda _pid: _Storage())
    monkeypatch.setattr(tc, "ProjectStorage", _ProjectStorage)
    monkeypatch.setattr(tc, "get_ziya_home", lambda: ziya_home)
    return asyncio.run(tc.get_signature_summary("proj1"))


def _approve(card):
    block = card.root
    scope = merge_scopes(None, card.scope, block.scope)
    h = sc.task_scope_hash(scope)
    at = int(time.time())
    sa.save_record({"task_id": block.id, "scope_hash": h, "approved_by": "t",
                    "approved_at": at,
                    "signature": sc.sign_approval_record(block.id, h, "t", at)})


def test_reports_only_cards_with_unsigned_escalation(keyed_store, tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    out = _summary([_escalating("esc"), _benign("ok")], home, monkeypatch)
    assert out["count"] == 1
    assert out["cardsNeedingSignature"] == [
        {"id": "esc", "name": "Card esc", "unsignedBlocks": 1}]


def test_signing_clears_the_card(keyed_store, tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    card = _escalating("esc")
    assert _summary([card], home, monkeypatch)["count"] == 1
    _approve(card)
    assert _summary([card], home, monkeypatch) == {"cardsNeedingSignature": [], "count": 0}


def test_summary_is_read_only(keyed_store, tmp_path, monkeypatch):
    """Polled on every focus, so it must NOT rewrite the signer staging file
    the way /{card_id}/scope-status does."""
    home = tmp_path / "home"; home.mkdir()
    staging = home / "pending_task_approvals.json"
    staging.write_text(json.dumps({"other:card:block": {"name": "x", "scope": {}}}))
    _summary([_escalating("esc")], home, monkeypatch)
    assert json.loads(staging.read_text()) == {"other:card:block": {"name": "x", "scope": {}}}
    assert sorted(p.name for p in home.iterdir()) == ["pending_task_approvals.json"]


def test_summary_route_not_shadowed_by_card_id():
    paths = [getattr(r, "path", "") for r in tc.router.routes]
    summary = next(i for i, p in enumerate(paths) if p.endswith("/signature-summary"))
    by_id = next(i for i, p in enumerate(paths) if p.endswith("/task-cards/{card_id}"))
    assert summary < by_id, "GET /{card_id} would capture 'signature-summary' as a card id"
