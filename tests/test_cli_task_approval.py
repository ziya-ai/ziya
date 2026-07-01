"""
CLI-task scope authorization (ASR F-001, design §4.2/§6).

Covers the path that routes ``ziya task <name>`` escalations through the SAME
signed approval ledger as Task Cards: ``scope_canonical.cli_task_hash`` /
``cli_escalation_block`` (the CLI ``allow``-block projection),
``scope_approvals.cli_task_key`` / ``is_cli_task_authorized`` (store lookup keyed
on the realpath of the defining tasks file), and
``task_runner.resolve_task_source_file`` (provenance resolution honoring the
builtin < global < project precedence).

The security property mirrors the card gate: an escalating CLI task runs at the
default floor unless a signed approval record matches its CURRENT allow hash; the
agent cannot mint one (root key). The store key uses os.path.realpath so symlink
aliases of the same tasks file collapse to one key.
"""

import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.utils import scope_approvals as sa
from app import task_runner


@pytest.fixture
def keyed_env(monkeypatch, tmp_path):
    """Throwaway keypair + isolated approval store."""
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


def _sign_and_store(allow, task_key):
    """Helper: mint a signed approval record for an allow block + key."""
    h = sc.cli_task_hash(allow)
    sig = sc.sign_approval_record(task_key, h, "tester", 1700000000)
    sa.save_record({
        "task_id": task_key, "scope_hash": h,
        "approved_by": "tester", "approved_at": 1700000000, "signature": sig,
    })


# ── cli_escalation_block / cli_task_hash ────────────────────────────────────────

def test_empty_allow_has_no_hash():
    assert sc.cli_task_hash(None) == ""
    assert sc.cli_task_hash({}) == ""
    assert sc.cli_task_hash({"commands": []}) == ""


def test_escalating_allow_has_hash():
    assert sc.cli_task_hash({"commands": ["/usr/bin/danger"]}) != ""


def test_allow_hash_order_independent():
    a = sc.cli_task_hash({"commands": ["b", "a"], "git_operations": ["push"]})
    b = sc.cli_task_hash({"git_operations": ["push"], "commands": ["a", "b"]})
    assert a == b


def test_widening_changes_hash():
    a = sc.cli_task_hash({"commands": ["a"]})
    b = sc.cli_task_hash({"commands": ["a", "b"]})
    assert a != b


def test_only_privilege_fields_count():
    # A non-privilege key in the allow block must not affect the hash.
    a = sc.cli_task_hash({"commands": ["a"]})
    b = sc.cli_task_hash({"commands": ["a"], "description": "ignored"})
    assert a == b


def test_escalation_block_dedups_and_sorts():
    blk = sc.cli_escalation_block({"commands": ["b", "a", "a", "  "]})
    assert blk == {"commands": ["a", "b"]}


# ── cli_task_key (realpath-based) ───────────────────────────────────────────────

def test_cli_task_key_uses_realpath(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    f = real / "tasks.yaml"; f.write_text("{}")
    link = tmp_path / "link"
    os.symlink(real, link)
    via_real = sa.cli_task_key(str(f), "deploy")
    via_link = sa.cli_task_key(str(link / "tasks.yaml"), "deploy")
    assert via_real == via_link  # symlink alias collapses to one key
    assert via_real.startswith("cli:")
    assert via_real.endswith("#deploy")


# ── is_cli_task_authorized ──────────────────────────────────────────────────────

def test_unapproved_escalation_denied(keyed_env):
    allow = {"commands": ["/usr/bin/danger"]}
    assert sa.is_cli_task_authorized("cli:/x/tasks.yaml#deploy", allow) is False


def test_non_escalating_always_authorized(keyed_env):
    # No allow escalation -> nothing to authorize -> True (runs at floor anyway).
    assert sa.is_cli_task_authorized("cli:/x/tasks.yaml#noop", {}) is True
    assert sa.is_cli_task_authorized("cli:/x/tasks.yaml#noop", None) is True


def test_approved_escalation_authorized(keyed_env):
    allow = {"commands": ["/usr/bin/danger"], "git_operations": ["push"]}
    key = "cli:/x/tasks.yaml#deploy"
    _sign_and_store(allow, key)
    assert sa.is_cli_task_authorized(key, allow) is True


def test_approved_then_widened_denied(keyed_env):
    allow = {"commands": ["/usr/bin/danger"]}
    key = "cli:/x/tasks.yaml#deploy"
    _sign_and_store(allow, key)
    widened = {"commands": ["/usr/bin/danger", "/usr/bin/worse"]}
    assert sa.is_cli_task_authorized(key, widened) is False


def test_record_for_wrong_key_denied(keyed_env):
    allow = {"commands": ["/usr/bin/danger"]}
    _sign_and_store(allow, "cli:/x/tasks.yaml#deploy")
    # same allow, different task key -> no record at that key -> denied
    assert sa.is_cli_task_authorized("cli:/y/tasks.yaml#deploy", allow) is False


def test_forged_signature_denied(keyed_env, tmp_path):
    allow = {"commands": ["/usr/bin/danger"]}
    key = "cli:/x/tasks.yaml#deploy"
    h = sc.cli_task_hash(allow)
    # sign with a DIFFERENT key
    other = Ed25519PrivateKey.generate()
    sig = sc.sign_approval_record(key, h, "attacker", 1700000000, private_key=other)
    sa.save_record({
        "task_id": key, "scope_hash": h,
        "approved_by": "attacker", "approved_at": 1700000000, "signature": sig,
    })
    assert sa.is_cli_task_authorized(key, allow) is False


# ── resolve_task_source_file (provenance / precedence) ──────────────────────────

def test_resolve_project_task(tmp_path):
    proj = tmp_path / "proj"; (proj / ".ziya").mkdir(parents=True)
    f = proj / ".ziya" / "tasks.yaml"
    f.write_text(json.dumps({"deploy": {"prompt": "x"}}))
    got = task_runner.resolve_task_source_file("deploy", str(proj))
    assert got is not None
    assert os.path.realpath(str(got)) == os.path.realpath(str(f))


def test_resolve_unknown_task_returns_none(tmp_path):
    proj = tmp_path / "proj"; (proj / ".ziya").mkdir(parents=True)
    (proj / ".ziya" / "tasks.yaml").write_text(json.dumps({"deploy": {"prompt": "x"}}))
    assert task_runner.resolve_task_source_file("ghost", str(proj)) is None


def test_resolve_no_tasks_file_returns_none(tmp_path):
    proj = tmp_path / "proj"; proj.mkdir()
    assert task_runner.resolve_task_source_file("deploy", str(proj)) is None
