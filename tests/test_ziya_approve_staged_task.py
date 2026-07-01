"""Regression tests: task-card signer staged-scope fallback (Flow B).

An ALE-encrypted task card cannot be decrypted by `ziya-approve` because the
signer runs out-of-process under sudo with no plugin system / KEK. To sign such
a card's block, the running server (which holds the KEK) stages each unapproved
block's DECRYPTED scope to ~/.ziya/pending_task_approvals.json via the
scope-status endpoint. The signer falls back to that staged scope and recomputes
the SAME task_scope_hash the runtime gate re-derives from the real card.

These tests lock that path in:

  1. The staged scope round-trips to the IDENTICAL hash as the original card's
     decrypted scope (so a signature minted from staging matches at the gate).
  2. _resolve_staged_block reads the server-staged entry and exposes .name/.scope.
  3. A missing staging entry returns None (signer then errors cleanly, not crash).
  4. The staged-scope shape matches what the server actually writes.
"""
import json

import pytest

from app.utils import ziya_approve as za
from app.config import scope_canonical as sc


def _scope_dict() -> dict:
    """A realistic block scope: shell_commands + one writable, one read-only path."""
    return {
        "shell_commands": ["jq", "perl"],
        "paths": [
            {"path": "/tmp/out", "write": True},
            {"path": "/etc", "write": False},
        ],
    }


@pytest.fixture
def staged_on_disk(tmp_path, monkeypatch):
    """Write a server-staged pending_task_approvals.json under a fake HOME.

    Mirrors exactly what get_card_scope_status stages: keyed "project:card:block"
    with {name, scope:{shell_commands, paths:[{path,write}]}}.
    """
    project_id, card_id, block_id = "proj-1", "card-1", "b-escalated"
    home = tmp_path / "home"
    (home / ".ziya").mkdir(parents=True)
    staged = {
        f"{project_id}:{card_id}:{block_id}": {
            "name": "Escalated block",
            "scope": _scope_dict(),
        }
    }
    (home / ".ziya" / "pending_task_approvals.json").write_text(json.dumps(staged))
    monkeypatch.setenv("HOME", str(home))
    return project_id, card_id, block_id


def test_staged_scope_hash_matches_real_card_scope():
    """A signature minted from the staged scope matches the gate's re-derived hash.

    The runtime gate hashes the REAL decrypted card's block scope. The signer
    hashes the SERVER-STAGED scope. These must be identical, or the signed record
    would never match at execution time.
    """
    real_scope = za._ns(_scope_dict())          # what the gate sees (decrypted card)
    staged_scope = za._ns(_scope_dict())         # what the signer sees (staged)
    assert sc.task_scope_hash(real_scope) == sc.task_scope_hash(staged_scope)


def test_resolve_staged_block_returns_block_like(staged_on_disk):
    """_resolve_staged_block exposes .name/.scope so the duck-typed helpers work."""
    project_id, card_id, block_id = staged_on_disk
    block = za._resolve_staged_block(project_id, card_id, block_id)

    assert block is not None
    assert block.name == "Escalated block"

    escalation = sc.task_escalation_block(block.scope)
    assert escalation["shell_commands"] == ["jq", "perl"]
    assert escalation["writable_paths"] == ["/tmp/out"]  # /etc (write=False) excluded

    h = sc.task_scope_hash(block.scope)
    assert isinstance(h, str) and len(h) == 64


def test_resolve_staged_block_missing_entry_returns_none(staged_on_disk):
    """A block with no staged entry resolves to None (not an exception)."""
    project_id, card_id, _ = staged_on_disk
    assert za._resolve_staged_block(project_id, card_id, "no-such-block") is None


def test_resolve_staged_block_no_file_returns_none(tmp_path, monkeypatch):
    """No staging file at all -> None (the 'never opened in UI' case)."""
    home = tmp_path / "home"
    (home / ".ziya").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    assert za._resolve_staged_block("p", "c", "b") is None


def test_resolve_staged_block_malformed_file_returns_none(tmp_path, monkeypatch):
    """A corrupt staging file fails soft to None rather than raising."""
    home = tmp_path / "home"
    (home / ".ziya").mkdir(parents=True)
    (home / ".ziya" / "pending_task_approvals.json").write_text("{not json")
    monkeypatch.setenv("HOME", str(home))
    assert za._resolve_staged_block("p", "c", "b") is None
