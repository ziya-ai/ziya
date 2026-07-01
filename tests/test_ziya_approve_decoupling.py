"""Regression tests: the task-card signer must not depend on app.storage.

`ziya-approve --task` resolves a card to compute its scope hash before signing.
It originally did this via TaskCardStorage, which transitively imports the
pydantic-backed storage layer (app.storage.__init__ -> projects -> pydantic ->
... -> asyncio). That made the signer fragile to unrelated environment rot: a
stale PyPI `asyncio` backport shadowing the stdlib crashed task approval with a
SyntaxError before any signing logic ran.

_resolve_card now reads the card JSON directly and wraps it in attribute-access
namespaces, so the duck-typed block helpers work without the storage layer.
These tests lock that decoupling in:

  1. _resolve_card reads a card from disk with no storage import.
  2. It still resolves correctly when app.storage is made UNIMPORTABLE
     (simulating the poisoned-environment failure mode directly).
  3. The namespace-wrapped card round-trips through the real scope-hash helpers,
     producing the same hash a pydantic model would.
"""
import builtins
import json
import sys

import pytest

from app.utils import ziya_approve as za
from app.config import scope_canonical as sc


# A realistic on-disk task-card shape: a root block tree with one Task block
# carrying an escalation scope (shell_commands + a writable path entry).
def _card_dict(card_id: str = "card-1") -> dict:
    return {
        "id": card_id,
        "title": "Test card",
        "root": {
            "id": "root",
            "type": "group",
            "body": [
                {
                    "id": "b-escalated",
                    "type": "task",
                    "name": "Escalated block",
                    "body": "do the thing",
                    "scope": {
                        "shell_commands": ["jq", "perl"],
                        "paths": [
                            {"path": "/tmp/out", "write": True},
                            {"path": "/etc", "write": False},
                        ],
                    },
                }
            ],
        },
    }


@pytest.fixture
def card_on_disk(tmp_path, monkeypatch):
    """Write a plaintext card where _resolve_card (env-override path) reads it."""
    project_id = "proj-1"
    card_id = "card-1"
    projects_dir = tmp_path / "projects"
    card_dir = projects_dir / project_id / "task_cards"
    card_dir.mkdir(parents=True)
    (card_dir / f"{card_id}.json").write_text(json.dumps(_card_dict(card_id)))
    monkeypatch.setenv("ZIYA_APPROVE_PROJECTS_DIR", str(projects_dir))
    return project_id, card_id


def test_resolve_card_reads_without_storage_import(card_on_disk):
    """_resolve_card returns the card without importing app.storage."""
    # Drop any already-imported storage modules so we can detect a fresh import.
    for mod in list(sys.modules):
        if mod == "app.storage" or mod.startswith("app.storage."):
            del sys.modules[mod]

    project_id, card_id = card_on_disk
    card = za._resolve_card(project_id, card_id)

    assert card is not None
    assert card.id == card_id
    # The decoupling guarantee: resolving a card pulled in no storage module.
    assert not any(
        m == "app.storage" or m.startswith("app.storage.") for m in sys.modules
    ), "resolving a card must not import app.storage"


def test_resolve_card_works_when_storage_unimportable(card_on_disk, monkeypatch):
    """The poisoned-env failure mode: app.storage import raises, signer still works.

    This directly simulates the stale-asyncio-backport crash -- anything under
    app.storage blows up on import -- and asserts _resolve_card is immune.
    """
    for mod in list(sys.modules):
        if mod == "app.storage" or mod.startswith("app.storage."):
            del sys.modules[mod]

    real_import = builtins.__import__

    def poisoned_import(name, *args, **kwargs):
        if name == "app.storage" or name.startswith("app.storage."):
            raise SyntaxError("simulated poisoned environment (stale asyncio backport)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", poisoned_import)

    project_id, card_id = card_on_disk
    # Must not raise, and must resolve the card.
    card = za._resolve_card(project_id, card_id)
    assert card is not None
    assert card.id == card_id


def test_resolved_card_roundtrips_through_scope_hash(card_on_disk):
    """The namespace-wrapped card satisfies the real duck-typed hash helpers."""
    project_id, card_id = card_on_disk
    card = za._resolve_card(project_id, card_id)

    block = za._find_block(card.root, "b-escalated")
    assert block is not None
    assert block.name == "Escalated block"

    # The escalation scope is read via getattr by these helpers; a
    # SimpleNamespace tree must satisfy them exactly as a pydantic model would.
    # Both helpers take the block's SCOPE, not the block.
    escalation = sc.task_escalation_block(block.scope)
    assert escalation  # non-empty: shell_commands + one writable path
    assert escalation["shell_commands"] == ["jq", "perl"]
    assert escalation["writable_paths"] == ["/tmp/out"]  # /etc (write=False) excluded

    h = sc.task_scope_hash(block.scope)
    assert isinstance(h, str) and len(h) == 64  # sha256 hex

    # Determinism: re-resolving and re-hashing yields the same value.
    card2 = za._resolve_card(project_id, card_id)
    block2 = za._find_block(card2.root, "b-escalated")
    assert sc.task_scope_hash(block2.scope) == h


def test_resolve_card_missing_returns_none(card_on_disk):
    """A nonexistent card id resolves to None, not an exception."""
    project_id, _ = card_on_disk
    assert za._resolve_card(project_id, "does-not-exist") is None


def test_resolve_card_handles_encrypted(tmp_path, monkeypatch):
    """ALE-encrypted cards are auto-detected and decrypted inline."""
    from app.utils import encryption

    project_id = "proj-enc"
    card_id = "card-enc"
    projects_dir = tmp_path / "projects"
    card_dir = projects_dir / project_id / "task_cards"
    card_dir.mkdir(parents=True)

    plaintext = json.dumps(_card_dict(card_id))
    blob = encryption.get_encryptor().encrypt(plaintext)
    # Only meaningful if encryption is actually active in this environment.
    if not encryption.is_encrypted(blob):
        pytest.skip("encryption not active in this environment")
    if isinstance(blob, str):
        (card_dir / f"{card_id}.json").write_text(blob)
    else:
        (card_dir / f"{card_id}.json").write_bytes(blob)
    monkeypatch.setenv("ZIYA_APPROVE_PROJECTS_DIR", str(projects_dir))

    card = za._resolve_card(project_id, card_id)
    assert card is not None
    assert card.id == card_id
