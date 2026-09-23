"""§6.3 security audit of shadow line control — hostile-input tests.

Each test encodes an attack; a failure is a finding to fix, never a test to
weaken.  Surfaces: policy bypass, lease/confirm forgery, spawn argv,
dotfile write path, OSC injection via label, kill ownership.
"""
import os
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ZIYA_SHADOW_TINT", "off")
    return tmp_path


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------- policy ----

BYPASS_ATTEMPTS = [
    "ls > /etc/passwd",                 # plain redirection
    "ls >/etc/passwd",                  # fused
    "ls 2>/etc/passwd",                 # fd-prefixed
    "ls >> /etc/passwd",
    "ls >| /etc/passwd",                # clobber
    # NB: plain input redirection (`cat < f`) is deliberately allowed — it is
    # `cat f`, and paths are opaque under the remote profile.  Process
    # substitution and read-write open are not.
    "cat <(rm -rf /)",
    "ls <> /etc/passwd",
    "ls $(rm -rf /)",
    "ls `rm -rf /`",
    "ls; rm -rf /",                     # chained destructive
    "ls && rm -rf /",
    "ls | tee /etc/passwd",
    "ls\nrm -rf /",                     # embedded newline → two commands
    "ls\rrm -rf /",
    "FOO=bar rm -rf /",                 # env-prefix hides the verb
    "env rm -rf /",                     # wrapper verb
    "xargs rm < list",
    "sed -i 's/a/b/' /etc/hosts",
    "sed -ni p /etc/hosts",             # combined flags
    "python3 -c 'open(\"/etc/x\",\"w\")'",  # interpreter is a write primitive
    "ls\u00a0>\u00a0/etc/passwd",       # NBSP around operator
    "ls＞/etc/passwd",                  # fullwidth > (harmless to sh, but must not be "allowed" as a write)
    "sudo ls",
    "/usr/bin/sudo ls",                 # path-qualified always-blocked
    "s\\udo ls",                        # backslash-split verb
    "'sudo' ls",
    "\"sudo\" ls",
    "exec rm -rf /",
    "eval 'rm -rf /'",
    "command rm -rf /",
    "builtin cd /; rm -rf .",
    "nohup rm -rf / &",
    "time rm -rf /",
    ". ./evil.sh",
    "source evil.sh",
    "ls ;rm -rf /",
]


@pytest.mark.parametrize("cmd", BYPASS_ATTEMPTS)
def test_builtin_strict_denies_bypass_attempts(cmd):
    from app.shadow.policy import resolve_policy, RUN
    verdict, reason = resolve_policy("builtin", "strict").decide(cmd)
    assert verdict != RUN, (cmd, reason)


def test_named_policy_rejects_traversal_and_bad_schema(home):
    from app.shadow import policy
    with pytest.raises(ValueError):
        policy.load_named_policy("../evil")
    with pytest.raises(ValueError):
        policy.load_named_policy("a/b")
    d = policy.policies_dir()
    (d / "bad.json").write_text('{"allowedCommands": "ls"}')
    with pytest.raises(ValueError):
        policy.load_named_policy("bad")
    # A named set can never grant an always-blocked verb.
    (d / "wide.json").write_text('{"allowedCommands": ["ls", "sudo", "rm"]}')
    pol = policy.resolve_policy("named:wide", "strict")
    assert pol.decide("sudo ls")[0] != "run"
    assert pol.decide("rm -rf /")[0] != "run"


# ------------------------------------------------------- lease / confirm ----

@pytest.fixture
def ctl(home):
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-c", "while read l; do eval \"$l\"; done"],
                      label="audit", headless=True, control_ceiling="gated")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.4)
    yield core
    core.terminate_child()
    t.join(timeout=5)


def test_heartbeat_and_release_require_matching_conversation(ctl):
    from app.shadow import client
    from app.shadow.client import ShadowError
    sid = ctl.entry.session_id
    r = client.control_acquire(sid, "owner", restriction="strict")
    lid = r["lease_id"]
    # Another conversation cannot keep the lease alive with the id alone.
    with pytest.raises(ShadowError):
        client.request(ctl.entry, "control_heartbeat", lease_id=lid, conversation_id="thief")
    # ...nor release it.
    with pytest.raises(ShadowError):
        client.request(ctl.entry, "control_release", lease_id=lid, conversation_id="thief")
    with pytest.raises(ShadowError):   # ...nor anonymously
        client.request(ctl.entry, "control_release", lease_id=lid)
    st = client.control_status(sid)["lease"]
    assert st["conversation_id"] == "owner"
    assert "lease_id" not in st                        # status never leaks the token
    # The lease id is a bearer token for send_line, so it must never be
    # readable from the journal, which every same-UID chat can shadow_read.
    import json
    from app.shadow.journal import JournalReader
    dump = json.dumps(JournalReader(ctl.entry.journal).read(1, 500)["records"])
    assert lid not in dump, "raw lease id leaked into the journal"
    assert "lease_ref" in dump                          # correlation still possible


def test_grant_with_stale_lease_id_does_not_grant_new_lease(ctl):
    from app.shadow import client
    sid = ctl.entry.session_id
    # Interactive path: pretend headless is False for the grant logic.
    ctl.entry.headless = False
    old = client.control_acquire(sid, "a", restriction="gated")["lease_id"]
    new = client.control_acquire(sid, "a", restriction="gated")["lease_id"]  # supersedes
    assert old != new
    assert ctl.server.grant_active_lease(old) is None       # stale key must not grant
    assert ctl.server.current_lease().granted is False


def test_send_line_rejects_multiline_and_control_chars(ctl):
    from app.shadow import client
    from app.shadow.client import ShadowError
    sid = ctl.entry.session_id
    # (ceiling is gated, so `none` would be refused — an existing guard.)
    lid = client.control_acquire(sid, "c", restriction="gated", policy="builtin")["lease_id"]
    for txt in ("echo a\necho b", "echo a\recho b", "echo a\x1b[2J", "echo a\x00b", "echo a\x03"):
        with pytest.raises(ShadowError, match="bad_request|command_denied|control"):
            client.send_line(sid, lid, txt)


def test_provenance_cannot_impersonate_owner_on_attach_detach(ctl):
    from app.shadow import client
    from app.shadow.client import ShadowError
    sid = ctl.entry.session_id
    client.attach(sid, "owner")
    with pytest.raises(ShadowError):
        client.request(ctl.entry, "detach", conversation_id="thief",
                       provenance={"conversation_id": "owner"})
    assert ctl.entry.attached["conversation_id"] == "owner"


# ---------------------------------------------------------------- spawn ----

def test_spawn_rejects_argv_that_is_not_a_plain_command(home):
    from app.shadow import client
    from app.shadow.client import ShadowError
    # A shell-ish string is not tokenised; the whole thing is argv[0].
    with pytest.raises(ShadowError):
        client.spawn_headless(["sh -c 'rm -rf /'"], spawned_by={"conversation_id": "c"})
    # Options must not be smuggled through ssh to run remote commands with
    # a local proxy: ProxyCommand runs a LOCAL shell.
    with pytest.raises(ShadowError):
        client.spawn_headless(["ssh", "-o", "ProxyCommand=rm -rf /", "host"],
                              spawned_by={"conversation_id": "c"})


def test_kill_refuses_interactive_and_foreign_sessions(home):
    from app.shadow.pty_host import ShadowCore
    from app.shadow import client
    from app.shadow.client import ShadowError
    core = ShadowCore(["sh", "-c", "read x"], label="human", headless=False)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    try:
        with pytest.raises(ShadowError):
            client.kill_session(core.entry.session_id, conversation_id="anyone")
    finally:
        core.terminate_child(); t.join(timeout=5)


# ------------------------------------------------ dotfile / OSC injection ----

def test_prompt_marker_install_refuses_symlinked_rc(home):
    from app.shadow import prompt_marker as pm
    target = home / "victim"
    target.write_text("keep\n")
    os.symlink(target, home / ".zshrc")
    with pytest.raises(OSError):
        pm.install("zsh")
    assert target.read_text() == "keep\n"


def test_label_cannot_inject_osc_into_title_or_overlay(home):
    from app.shadow.title import TitleRewriter
    from app.shadow.redaction import safe_terminal_text
    hostile = 'x\x07\x1b]0;pwned\x07\x1b[6n\x1b]52;c;ZXZpbA==\x07'
    t = TitleRewriter(f"⏺ {hostile}")
    body = t.emit()
    assert body.count(b"\x1b") == 1 and body.count(b"\x07") == 1, body
    assert b"\x1b" not in safe_terminal_text(hostile).encode()
