"""
Tests for the escalation-compliance audit (ASR F-001, surface B).

``app.utils.scope_audit`` walks BOTH escalation ledgers (Task Card block scopes
+ tasks.yaml CLI ``allow`` blocks) and reports, per escalating task, whether a
valid signed approval exists. These tests pin:

  • CLI tasks: escalating-unsigned, escalating-signed, floor-only-skipped.
  • Cards: plaintext escalating-unsigned / signed, floor-only-skipped, and that
    an encrypted (undecryptable-here) card file is counted as uninspectable
    rather than silently dropped.
  • The signed/unsigned predicate matches the runtime gate exactly (a record for
    a DIFFERENT scope hash does NOT count as signed — authorization binds to
    content).
  • collect_audit aggregates both surfaces; unsigned/escalating partitions.
"""

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.utils import scope_approvals as sa
from app.utils import scope_audit as audit


@pytest.fixture
def keyed_env(monkeypatch, tmp_path):
    """Throwaway keypair + isolated approval store + isolated projects dir."""
    priv = tmp_path / "approve_ed25519"
    pub = tmp_path / "approve_ed25519.pub"
    key = Ed25519PrivateKey.generate()
    priv.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    pub.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ))
    monkeypatch.setenv("ZIYA_APPROVE_PRIVKEY", str(priv))
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub))
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path / "approvals"))
    return key


def _sign_record(task_key: str, scope_hash: str) -> None:
    sig = sc.sign_approval_record(task_key, scope_hash, "tester", 1700000000)
    sa.save_record({
        "task_id": task_key, "scope_hash": scope_hash,
        "approved_by": "tester", "approved_at": 1700000000, "signature": sig,
    })


def _write_tasks_yaml(root: Path, body: dict) -> None:
    (root / ".ziya").mkdir(parents=True, exist_ok=True)
    import yaml
    (root / ".ziya" / "tasks.yaml").write_text(yaml.safe_dump(body))


def _write_card(projects_base: Path, project_id: str, card_id: str,
                card: dict, *, encrypted: bool = False) -> Path:
    tc = projects_base / project_id / "task_cards"
    tc.mkdir(parents=True, exist_ok=True)
    path = tc / f"{card_id}.json"
    if encrypted:
        # Emulate an ALE file the out-of-process auditor cannot decrypt: write
        # bytes carrying the ALE magic but not validly decryptable here.
        from app.utils import encryption
        # ZIYA-ALE-V1 magic prefix makes is_encrypted() true; the trailing
        # bytes are not a real ciphertext, so decrypt() raises -> uninspectable.
        path.write_bytes(b"ZIYA-ALE-V1" + b"\x00\x01\x02not-a-real-ciphertext")
    else:
        path.write_text(json.dumps(card))
    return path


# ── CLI surface ──────────────────────────────────────────────────────────────

def test_cli_escalating_unsigned(keyed_env, tmp_path):
    root = tmp_path / "proj"
    _write_tasks_yaml(root, {
        "sweep": {"allow": {"commands": ["git", "gh"]}, "prompt": "x"},
    })
    entries = audit.collect_cli_entries(str(root))
    assert len(entries) == 1
    e = entries[0]
    assert e.surface == "cli"
    assert e.label == "sweep"
    assert e.signed is False
    assert e.escalation == {"commands": ["gh", "git"]}
    assert e.store_key.startswith("cli:") and e.store_key.endswith("#sweep")


def test_cli_escalating_signed(keyed_env, tmp_path):
    root = tmp_path / "proj"
    allow = {"commands": ["git"]}
    _write_tasks_yaml(root, {"sweep": {"allow": allow, "prompt": "x"}})
    from app.task_runner import resolve_task_source_file
    src = resolve_task_source_file("sweep", str(root))
    key = sa.cli_task_key(str(src), "sweep")
    _sign_record(key, sc.cli_task_hash(allow))
    entries = audit.collect_cli_entries(str(root))
    assert entries[0].signed is True


def test_cli_floor_only_task_not_listed(keyed_env, tmp_path):
    root = tmp_path / "proj"
    # No allow block -> floor-only -> not an escalating entry.
    _write_tasks_yaml(root, {"noop": {"prompt": "just a prompt"}})
    assert audit.collect_cli_entries(str(root)) == []


def test_cli_signature_for_different_hash_is_unsigned(keyed_env, tmp_path):
    """A record signed for an OLD allow block must not authorize a widened one
    (authorization binds to content) — the audit reports it unsigned."""
    root = tmp_path / "proj"
    old_allow = {"commands": ["git"]}
    _write_tasks_yaml(root, {"sweep": {"allow": old_allow, "prompt": "x"}})
    from app.task_runner import resolve_task_source_file
    src = resolve_task_source_file("sweep", str(root))
    key = sa.cli_task_key(str(src), "sweep")
    _sign_record(key, sc.cli_task_hash(old_allow))
    # Widen the allow block AFTER signing.
    _write_tasks_yaml(root, {"sweep": {"allow": {"commands": ["git", "rm"]},
                                       "prompt": "x"}})
    assert audit.collect_cli_entries(str(root))[0].signed is False


# ── Card surface ─────────────────────────────────────────────────────────────

def _card(block_id: str, *, shell=None, write_paths=None) -> dict:
    paths = [{"path": p, "write": True} for p in (write_paths or [])]
    return {
        "name": "TestCard",
        "root": {
            "block_type": "task", "id": block_id, "name": "do it",
            "scope": {"shell_commands": shell or [], "paths": paths},
            "body": [],
        },
    }


def test_card_escalating_unsigned(keyed_env, tmp_path):
    base = tmp_path / "projects"
    _write_card(base, "p1", "c1", _card("b-aaa", shell=["dd"]))
    entries, enc = audit.collect_card_entries(base)
    assert enc == 0
    assert len(entries) == 1
    assert entries[0].surface == "card"
    assert entries[0].store_key == "b-aaa"
    assert entries[0].signed is False
    assert entries[0].escalation == {"shell_commands": ["dd"]}


def test_card_escalating_signed(keyed_env, tmp_path):
    base = tmp_path / "projects"
    card = _card("b-bbb", shell=["dd"])
    _write_card(base, "p1", "c1", card)
    # Sign the block's CURRENT scope hash.
    from types import SimpleNamespace
    scope = SimpleNamespace(shell_commands=["dd"], paths=[])
    _sign_record("b-bbb", sc.task_scope_hash(scope))
    entries, _ = audit.collect_card_entries(base)
    assert entries[0].signed is True


def test_card_floor_only_block_not_listed(keyed_env, tmp_path):
    base = tmp_path / "projects"
    _write_card(base, "p1", "c1", _card("b-ccc"))  # no shell, no write paths
    entries, enc = audit.collect_card_entries(base)
    assert entries == []
    assert enc == 0


def test_card_encrypted_file_counted_uninspectable(keyed_env, tmp_path):
    base = tmp_path / "projects"
    _write_card(base, "p1", "enc", {}, encrypted=True)
    entries, enc = audit.collect_card_entries(base)
    assert entries == []          # cannot inspect -> no entry
    assert enc == 1               # but counted as a known gap


def test_card_walks_nested_blocks(keyed_env, tmp_path):
    base = tmp_path / "projects"
    nested = {
        "name": "Nested",
        "root": {
            "block_type": "repeat", "id": "b-root", "body": [
                {"block_type": "task", "id": "b-inner",
                 "scope": {"shell_commands": ["dd"], "paths": []}, "body": []},
            ],
        },
    }
    _write_card(base, "p1", "c1", nested)
    entries, _ = audit.collect_card_entries(base)
    assert [e.store_key for e in entries] == ["b-inner"]


# ── Aggregate ────────────────────────────────────────────────────────────────

def test_collect_audit_aggregates_both_surfaces(keyed_env, tmp_path):
    base = tmp_path / "projects"
    _write_card(base, "p1", "c1", _card("b-aaa", shell=["dd"]))
    root = tmp_path / "proj"
    _write_tasks_yaml(root, {"sweep": {"allow": {"commands": ["git"]},
                                       "prompt": "x"}})
    result = audit.collect_audit(root=str(root), projects_base=base)
    surfaces = sorted(e.surface for e in result.escalating)
    assert surfaces == ["card", "cli"]
    # Both unsigned in this fixture.
    assert len(result.unsigned) == 2


def test_collect_audit_unsigned_excludes_encrypted_note(keyed_env, tmp_path):
    # An encrypted card file bumps the counter but is NOT an unsigned entry
    # (we cannot assert it's unsigned — it's uninspectable here).
    base = tmp_path / "projects"
    _write_card(base, "p1", "enc", {}, encrypted=True)
    root = tmp_path / "proj"
    _write_tasks_yaml(root, {"noop": {"prompt": "floor only"}})
    result = audit.collect_audit(root=str(root), projects_base=base)
    assert result.escalating == []
    assert result.unsigned == []
    assert result.encrypted_card_files == 1
