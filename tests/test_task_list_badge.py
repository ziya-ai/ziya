"""
Tests for the escalation/approval badge on ``ziya task --list`` (ASR F-001,
surface A).

The badge reuses the shared ``scope_audit.collect_cli_entries`` walk (same
predicate the runtime gate and ``ziya-approve --list`` use), so these tests pin
two things:

  • ``_task_escalation_badge`` — the pure None/True/False → badge mapping
    (floor-only → no badge; signed → ⚡; unsigned → 🔒).
  • End-to-end: the name→signed map A builds from ``collect_cli_entries``
    correctly classifies a floor-only task (absent), an unsigned escalation
    (present, False), and a signed escalation (present, True) — so the badge
    rendered for each row matches what actually runs.
"""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.cli import _task_escalation_badge
from app.config import scope_canonical as sc
from app.utils import scope_approvals as sa
from app.utils.scope_audit import collect_cli_entries


# ── pure badge mapping ───────────────────────────────────────────────────────

def test_badge_floor_only_is_empty():
    assert _task_escalation_badge(None) == ""


def test_badge_signed_shows_signed():
    b = _task_escalation_badge(True)
    assert "signed" in b and "🔒" not in b


def test_badge_unsigned_shows_lock():
    b = _task_escalation_badge(False)
    assert "unsigned" in b and "🔒" in b


# ── end-to-end name→signed map (what cmd_task --list keys the badge on) ──────

@pytest.fixture
def keyed_env(monkeypatch, tmp_path):
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


def _write_tasks(root, body):
    (root / ".ziya").mkdir(parents=True, exist_ok=True)
    import yaml
    (root / ".ziya" / "tasks.yaml").write_text(yaml.safe_dump(body))


def test_floor_only_task_absent_from_audit_map(keyed_env, tmp_path):
    root = tmp_path / "proj"
    _write_tasks(root, {"noop": {"prompt": "no allow block"}})
    by_name = {e.label: e.signed for e in collect_cli_entries(str(root))}
    # Floor-only task is not escalating → absent → badge is "" (None lookup).
    assert "noop" not in by_name
    assert _task_escalation_badge(by_name.get("noop")) == ""


def test_unsigned_escalation_maps_to_lock_badge(keyed_env, tmp_path):
    root = tmp_path / "proj"
    _write_tasks(root, {"sweep": {"allow": {"commands": ["git"]},
                                  "prompt": "x"}})
    by_name = {e.label: e.signed for e in collect_cli_entries(str(root))}
    assert by_name["sweep"] is False
    assert "🔒" in _task_escalation_badge(by_name.get("sweep"))


def test_signed_escalation_maps_to_signed_badge(keyed_env, tmp_path):
    root = tmp_path / "proj"
    allow = {"commands": ["git"]}
    _write_tasks(root, {"sweep": {"allow": allow, "prompt": "x"}})
    from app.task_runner import resolve_task_source_file
    src = resolve_task_source_file("sweep", str(root))
    key = sa.cli_task_key(str(src), "sweep")
    h = sc.cli_task_hash(allow)
    sig = sc.sign_approval_record(key, h, "tester", 1700000000)
    sa.save_record({"task_id": key, "scope_hash": h,
                    "approved_by": "tester", "approved_at": 1700000000,
                    "signature": sig})
    by_name = {e.label: e.signed for e in collect_cli_entries(str(root))}
    assert by_name["sweep"] is True
    assert "signed" in _task_escalation_badge(by_name.get("sweep"))
    assert "🔒" not in _task_escalation_badge(by_name.get("sweep"))
