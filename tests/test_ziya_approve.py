"""
Tests for the ``ziya-approve`` escalation signer (ASR F-004 / F-007, design
doc §4.0 / §4.3) — the minting half of the escalation-config integrity control.

Pairs with tests/test_scope_canonical_gate.py (the verifier half). Here we prove
the CLI produces signatures the verifier accepts, that it refuses to sign
without a usable key or a TTY, and that empty deltas need no signature.

A throwaway Ed25519 keypair is generated per test and the key paths are pointed
at it via ZIYA_APPROVE_PRIVKEY / ZIYA_APPROVE_PUBKEY, so nothing touches
/etc/ziya and no sudo is required.
"""

import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.utils import ziya_approve


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def keypair(tmp_path):
    """Generate a throwaway Ed25519 keypair on disk; return (priv_path, pub_path)."""
    priv_p = tmp_path / "approve_ed25519"
    pub_p = tmp_path / "approve_ed25519.pub"
    key = Ed25519PrivateKey.generate()
    priv_p.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    pub_p.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ))
    return str(priv_p), str(pub_p)


@pytest.fixture
def keyed_env(monkeypatch, keypair):
    """Point the signer/verifier at the throwaway keypair."""
    priv_p, pub_p = keypair
    monkeypatch.setenv("ZIYA_APPROVE_PRIVKEY", priv_p)
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", pub_p)
    return priv_p, pub_p


def _write_cfg(path, env: dict) -> None:
    path.write_text(json.dumps({"mcpServers": {"shell": {"env": env}}}))


def _read_env(path) -> dict:
    return json.loads(path.read_text())["mcpServers"]["shell"]["env"]


# ── --show (no key needed) ──────────────────────────────────────────────────────

def test_show_lists_only_the_delta(tmp_path, capsys):
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,cat,git push,/usr/bin/danger"})
    rc = ziya_approve.main(["--show", "--config", str(cfg)])
    out = capsys.readouterr().out
    assert rc == 0
    # floor commands absent, escalation present
    assert "/usr/bin/danger" in out
    assert "git push" in out
    assert "+ ALLOW_COMMANDS: ls" not in out  # ls is in the floor


def test_show_empty_delta_says_nothing_to_approve(tmp_path, capsys):
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,cat"})  # all within floor
    rc = ziya_approve.main(["--show", "--config", str(cfg)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to approve" in out.lower()


def test_show_does_not_write_a_signature(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,/usr/bin/danger"})
    ziya_approve.main(["--show", "--config", str(cfg)])
    assert sc.SIG_ENV_KEY not in _read_env(cfg)


# ── sign → verify round-trip via the real CLI ───────────────────────────────────

def test_sign_writes_verifier_accepted_signature(tmp_path, keyed_env):
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,cat,git push,/usr/bin/danger"})
    rc = ziya_approve.main(["--config", str(cfg), "--yes"])
    assert rc == 0
    env = _read_env(cfg)
    assert sc.SIG_ENV_KEY in env
    # the verifier (the OTHER half) accepts the CLI-minted signature for this env
    assert sc.is_env_scope_authorized(env) is True


def test_signed_then_widened_is_rejected(tmp_path, keyed_env):
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,/usr/bin/danger"})
    ziya_approve.main(["--config", str(cfg), "--yes"])
    env = _read_env(cfg)
    assert sc.is_env_scope_authorized(env) is True
    # widen the granted set after signing → signature no longer covers the delta
    env["ALLOW_COMMANDS"] += ",/usr/bin/worse"
    assert sc.is_env_scope_authorized(env) is False


def test_signed_yolo_is_accepted(tmp_path, keyed_env):
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls", "YOLO_MODE": "true"})
    rc = ziya_approve.main(["--config", str(cfg), "--yes"])
    assert rc == 0
    assert sc.is_env_scope_authorized(_read_env(cfg)) is True


# ── empty delta is a no-op ───────────────────────────────────────────────────────

def test_empty_delta_writes_no_signature(tmp_path, keyed_env, capsys):
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,cat"})  # within floor
    rc = ziya_approve.main(["--config", str(cfg), "--yes"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to approve" in out.lower()
    assert sc.SIG_ENV_KEY not in _read_env(cfg)


# ── failure modes ────────────────────────────────────────────────────────────────

def test_missing_private_key_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_APPROVE_PRIVKEY", str(tmp_path / "does_not_exist"))
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(tmp_path / "nope.pub"))
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,/usr/bin/danger"})
    rc = ziya_approve.main(["--config", str(cfg), "--yes"])
    assert rc == 2
    assert sc.SIG_ENV_KEY not in _read_env(cfg)


def test_no_tty_confirmation_refuses(tmp_path, keyed_env, monkeypatch):
    """Without --yes, with neither an openable /dev/tty NOR an interactive
    stdin, the signer must refuse — the agent-invocation case (piped stdin /
    no controlling terminal)."""
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,/usr/bin/danger"})

    # Force the /dev/tty open to fail, simulating no controlling terminal.
    real_open = open

    def _no_tty_open(path, *a, **k):
        if path == "/dev/tty":
            raise OSError("no tty")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", _no_tty_open)
    # And stdin is a pipe (not a TTY) — the agent shape. Both paths fail closed.
    monkeypatch.setattr(ziya_approve.sys.stdin, "isatty", lambda: False, raising=False)
    rc = ziya_approve.main(["--config", str(cfg)])  # no --yes
    assert rc == 1
    assert sc.SIG_ENV_KEY not in _read_env(cfg)


def test_interactive_stdin_fallback_signs(tmp_path, keyed_env, monkeypatch):
    """When /dev/tty cannot be opened (the macOS-under-sudo case, §8 Q5) but
    stdin IS an interactive terminal, the human confirmation is accepted via the
    stdin fallback — so a genuine operator is not refused without --yes."""
    import io
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,/usr/bin/danger"})

    real_open = open

    def _no_tty_open(path, *a, **k):
        if path == "/dev/tty":
            raise OSError("no tty")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", _no_tty_open)

    # A genuine interactive stdin: isatty() True, typed "y".
    fake_stdin = io.StringIO("y\n")
    fake_stdin.isatty = lambda: True
    monkeypatch.setattr(ziya_approve.sys, "stdin", fake_stdin)

    rc = ziya_approve.main(["--config", str(cfg)])  # no --yes
    assert rc == 0
    sig = _read_env(cfg).get(sc.SIG_ENV_KEY)
    assert sig
    # The signature must be the one the verifier accepts for this exact delta.
    delta = sc.compute_delta(sc.parse_env_scope(_read_env(cfg)))
    assert sc.verify_delta_signature(delta, sig) is True


def test_interactive_stdin_fallback_declines(tmp_path, keyed_env, monkeypatch):
    """The stdin fallback still honors a 'no' answer — typing anything but
    y/yes at an interactive prompt aborts without signing."""
    import io
    cfg = tmp_path / "mcp_config.json"
    _write_cfg(cfg, {"ALLOW_COMMANDS": "ls,/usr/bin/danger"})

    real_open = open

    def _no_tty_open(path, *a, **k):
        if path == "/dev/tty":
            raise OSError("no tty")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", _no_tty_open)
    fake_stdin = io.StringIO("n\n")
    fake_stdin.isatty = lambda: True
    monkeypatch.setattr(ziya_approve.sys, "stdin", fake_stdin)

    rc = ziya_approve.main(["--config", str(cfg)])  # no --yes
    assert rc == 1
    assert sc.SIG_ENV_KEY not in _read_env(cfg)


# ── canonicalization round-trip (sign half ↔ verify half share canonical()) ─────

@pytest.mark.parametrize("delta", [
    {"ALLOW_COMMANDS": ["/usr/bin/danger"]},
    {"ALLOW_COMMANDS": ["a", "b"], "ALLOWED_WRITE_PATTERNS": ["./out/**"]},
    {"YOLO_MODE": True},
    {"SAFE_WRITE_PATHS": ["/srv/data/"]},
])
def test_sign_delta_verifies(keyed_env, delta):
    sig = sc.sign_delta(delta)
    assert sc.verify_delta_signature(delta, sig) is True


def test_sign_delta_empty_is_noop(keyed_env):
    assert sc.sign_delta({}) == ""


def test_signature_does_not_verify_for_different_delta(keyed_env):
    sig = sc.sign_delta({"ALLOW_COMMANDS": ["/usr/bin/danger"]})
    # same sig, a different (widened) delta must not verify
    assert sc.verify_delta_signature(
        {"ALLOW_COMMANDS": ["/usr/bin/danger", "/usr/bin/worse"]}, sig
    ) is False


# ── --task / --block (task-scope approval mode, ASR F-001) ──────────────────────

def _write_card(projects_dir, project_id, card_id, block_id, scope):
    """Write a minimal card with one escalating task block nested in a repeat."""
    card_dir = projects_dir / project_id / "task_cards"
    card_dir.mkdir(parents=True, exist_ok=True)
    card = {
        "id": card_id, "name": "t",
        "root": {"block_type": "repeat", "id": "b-root", "name": "loop",
                 "repeat_count": 2,
                 "body": [{"block_type": "task", "id": block_id, "name": "step",
                           "instructions": "go", "scope": scope}]},
    }
    (card_dir / f"{card_id}.json").write_text(json.dumps(card))


@pytest.fixture
def task_env(monkeypatch, keyed_env, tmp_path):
    """keyed_env + isolated approvals store + projects dir for task-mode tests."""
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path / "approvals"))
    monkeypatch.setenv("ZIYA_APPROVE_PROJECTS_DIR", str(tmp_path / "projects"))
    return tmp_path / "projects"


def test_task_sign_then_authorize_accepts(task_env, monkeypatch):
    from app.storage.task_cards import TaskCardStorage
    from app.utils import scope_approvals as sa
    _write_card(task_env, "p1", "c1", "b-step1",
                {"shell_commands": ["make deploy"],
                 "paths": [{"path": "out/", "write": True, "is_dir": True}],
                 "tools": ["file_read"]})
    rc = ziya_approve.main(
        ["--project", "p1", "--task", "c1", "--block", "b-step1", "--yes"]
    )
    assert rc == 0
    rec = sa.get_record("b-step1")
    assert rec is not None and rec["scope_hash"] and rec["signature"]
    # the live card's block scope must now authorize
    card = TaskCardStorage(task_env / "p1").get("c1")
    blk = ziya_approve._find_block(card.root, "b-step1")
    assert sa.authorize_scope("b-step1", blk.scope) is blk.scope


def test_task_widened_after_signing_is_denied(task_env):
    from app.models.task_card import TaskScope, ScopeEntry
    from app.utils import scope_approvals as sa
    _write_card(task_env, "p1", "c1", "b-step1",
                {"shell_commands": ["make deploy"], "paths": []})
    ziya_approve.main(["--project", "p1", "--task", "c1", "--block", "b-step1", "--yes"])
    # the record approved {make deploy}; a widened scope must NOT be authorized
    widened = TaskScope(shell_commands=["make deploy", "curl"])
    out = sa.authorize_scope("b-step1", widened)
    assert out is not widened
    assert list(getattr(out, "shell_commands", [])) == []


def test_task_unknown_block_exits_2(task_env):
    _write_card(task_env, "p1", "c1", "b-step1", {"shell_commands": ["x"]})
    rc = ziya_approve.main(
        ["--project", "p1", "--task", "c1", "--block", "nope", "--yes"]
    )
    assert rc == 2


def test_task_unknown_card_exits_2(task_env):
    rc = ziya_approve.main(
        ["--project", "p1", "--task", "missing", "--block", "b", "--yes"]
    )
    assert rc == 2


def test_task_requires_all_three_flags(task_env, capsys):
    # --block without --project/--task is incomplete -> rc 2, no signing attempt
    rc = ziya_approve.main(["--block", "b-step1", "--yes"])
    assert rc == 2
    assert "requires --task, --block, and --project" in capsys.readouterr().err


def test_task_show_then_sign_is_idempotent_relationship(task_env):
    """Signing twice for the same unchanged scope yields a verifying record both
    times (re-approval is harmless, not an error)."""
    from app.utils import scope_approvals as sa
    from app.storage.task_cards import TaskCardStorage
    _write_card(task_env, "p1", "c1", "b-step1", {"shell_commands": ["make deploy"]})
    assert ziya_approve.main(["--project", "p1", "--task", "c1", "--block", "b-step1", "--yes"]) == 0
    assert ziya_approve.main(["--project", "p1", "--task", "c1", "--block", "b-step1", "--yes"]) == 0
    card = TaskCardStorage(task_env / "p1").get("c1")
    blk = ziya_approve._find_block(card.root, "b-step1")
    assert sa.authorize_scope("b-step1", blk.scope) is blk.scope


def test_task_non_escalating_block_needs_no_record(task_env, capsys):
    """A block whose scope grants no escalation reports nothing to approve (rc 0)
    and writes no record."""
    from app.utils import scope_approvals as sa
    _write_card(task_env, "p1", "c1", "b-step1",
                {"tools": ["file_read"], "paths": [{"path": "ro.txt", "read": True}]})
    rc = ziya_approve.main(["--project", "p1", "--task", "c1", "--block", "b-step1", "--yes"])
    assert rc == 0
    assert "Nothing to approve" in capsys.readouterr().out
    assert sa.get_record("b-step1") is None


# ── --cli-task mode (tasks.yaml, ASR F-001 / §6) ────────────────────────────────


def _write_tasks(proj, tasks: dict):
    """Write a project-local .ziya/tasks.yaml (JSON is valid YAML)."""
    import json as _json
    (proj / ".ziya").mkdir(parents=True, exist_ok=True)
    f = proj / ".ziya" / "tasks.yaml"
    f.write_text(_json.dumps(tasks))
    return f


def test_cli_task_sign_then_authorized(tmp_path, keyed_env, monkeypatch):
    from app.utils import scope_approvals as sa
    from app.task_runner import resolve_task_source_file
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path / "approvals"))
    proj = tmp_path / "proj"
    _write_tasks(proj, {"deploy": {"prompt": "x",
                                   "allow": {"commands": ["/usr/bin/danger"],
                                             "git_operations": ["push"]}}})
    allow = {"commands": ["/usr/bin/danger"], "git_operations": ["push"]}
    src = resolve_task_source_file("deploy", str(proj))
    key = sa.cli_task_key(str(src), "deploy")
    assert sa.is_cli_task_authorized(key, allow) is False  # before
    rc = ziya_approve.main(["--cli-task", "deploy", "--root", str(proj), "--yes"])
    assert rc == 0
    assert sa.is_cli_task_authorized(key, allow) is True   # after


def test_cli_task_sign_then_widened_denied(tmp_path, keyed_env, monkeypatch):
    from app.utils import scope_approvals as sa
    from app.task_runner import resolve_task_source_file
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path / "approvals"))
    proj = tmp_path / "proj"
    _write_tasks(proj, {"deploy": {"prompt": "x",
                                   "allow": {"commands": ["/usr/bin/danger"]}}})
    rc = ziya_approve.main(["--cli-task", "deploy", "--root", str(proj), "--yes"])
    assert rc == 0
    src = resolve_task_source_file("deploy", str(proj))
    key = sa.cli_task_key(str(src), "deploy")
    widened = {"commands": ["/usr/bin/danger", "/usr/bin/worse"]}
    assert sa.is_cli_task_authorized(key, widened) is False


def test_cli_task_unknown_name_exits_2(tmp_path, keyed_env, monkeypatch):
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path / "approvals"))
    proj = tmp_path / "proj"
    _write_tasks(proj, {"deploy": {"prompt": "x", "allow": {"commands": ["c"]}}})
    rc = ziya_approve.main(["--cli-task", "ghost", "--root", str(proj), "--yes"])
    assert rc == 2


def test_cli_task_no_escalation_is_noop(tmp_path, keyed_env, monkeypatch, capsys):
    from app.utils import scope_approvals as sa
    from app.task_runner import resolve_task_source_file
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path / "approvals"))
    proj = tmp_path / "proj"
    _write_tasks(proj, {"noop": {"prompt": "x"}})  # no allow block
    rc = ziya_approve.main(["--cli-task", "noop", "--root", str(proj), "--yes"])
    assert rc == 0
    assert "Nothing to approve" in capsys.readouterr().out
    src = resolve_task_source_file("noop", str(proj))
    assert sa.get_record(sa.cli_task_key(str(src), "noop")) is None
