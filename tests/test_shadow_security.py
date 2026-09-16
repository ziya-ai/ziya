"""Security audit tests for shadow sessions (Docs/design/shadow-sessions.md §7, §10).

Each test encodes one finding from the Phase 1 security review.  They are
written to FAIL against the code as first landed, so a passing run proves
the corresponding hardening is present rather than merely asserted.

Threat model recap: the shadow host runs with the user's privileges and
wraps a terminal that may be attached to production.  Three parties feed
it bytes — the human (trusted), the wrapped program / remote host
(untrusted: a compromised host writes arbitrary bytes into the stream),
and the chat model (untrusted for control purposes: it must never be able
to type into the session or influence what the human's terminal does).
"""
import os
import re
import sys
import json
import threading
import time


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")


# ---------------------------------------------------------------------------
# Encryption at rest (§10.1).  Real ALE with a passphrase KEK in a sandboxed
# $HOME — the envelope, keyring and DEK path are exercised, not mocked.
# ---------------------------------------------------------------------------

@pytest.fixture
def ale_on(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ZIYA_ENCRYPTION_KEY", "shadow-test-passphrase")
    import app.utils.encryption as enc
    saved = enc._encryptor
    enc._encryptor = None
    try:
        if not enc.get_encryptor().is_enabled("session_data"):
            pytest.skip("ALE could not initialise in this environment (cryptography missing?)")
        yield tmp_path
    finally:
        enc._encryptor = saved


@pytest.fixture
def ale_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ZIYA_ENCRYPTION_KEY", raising=False)
    import app.utils.encryption as enc
    saved = enc._encryptor
    enc._encryptor = None
    try:
        yield tmp_path
    finally:
        enc._encryptor = saved


def test_journal_lines_are_ciphertext_when_ale_enabled(ale_on):
    from app.shadow.journal import JournalWriter, JournalReader, _ALE_LINE_PREFIX
    path = ale_on / "s.journal"
    w = JournalWriter(str(path))
    canary_cmd, canary_out = "curl -H 'X-Canary: ZQXJV-cmd'", "ZQXJV-output-line"
    w.cmd(canary_cmd)
    w.output(canary_out + "\n", 1)
    w.meta("resize", {"rows": 40, "cols": 120})

    raw = path.read_bytes()
    assert b"ZQXJV" not in raw and b'"t":' not in raw
    lines = [l for l in raw.decode().splitlines() if l]
    assert len(lines) == 3 and all(l.startswith(_ALE_LINE_PREFIX) for l in lines)

    recs = list(JournalReader(str(path))._iter())
    assert [r["t"] for r in recs] == ["cmd", "output", "meta"]
    assert recs[0]["text"] == canary_cmd and canary_out in recs[1]["text"]
    assert JournalReader(str(path)).bounds() == {"head_seq": 1, "tail_seq": 3}

    # A second writer on the same path (exec of the host) recovers seq
    # through the ciphertext.
    w2 = JournalWriter(str(path))
    assert w2.cmd("next") == 4


def test_journal_rotation_survives_encrypted_lines(ale_on, monkeypatch):
    from app.shadow import journal
    monkeypatch.setattr(journal, "ROTATION_CAP_BYTES", 4000)
    monkeypatch.setattr(journal, "ROTATION_CHECK_EVERY", 1)
    path = ale_on / "r.journal"
    w = journal.JournalWriter(str(path))
    for i in range(40):
        seq = w.cmd(f"cmd {i}")
        w.output("x" * 80 + "\n", seq)
    assert path.stat().st_size <= 4000 * 1.2
    recs = list(journal.JournalReader(str(path))._iter())
    # Oldest groups dropped, newest intact, and the cut landed on a group
    # boundary (never an orphan output before its cmd).
    assert recs and recs[0]["t"] == "cmd"
    assert recs[-1]["t"] == "output" and recs[-2]["text"] == "cmd 39"
    assert all(l.startswith(journal._ALE_LINE_PREFIX) for l in path.read_text().splitlines() if l)


def test_journal_is_plaintext_when_ale_disabled(ale_off):
    from app.shadow.journal import JournalWriter, JournalReader
    path = ale_off / "p.journal"
    w = JournalWriter(str(path))
    w.cmd("ls")
    assert path.read_text().startswith('{"t": "cmd"')
    assert [r["text"] for r in JournalReader(str(path))._iter()] == ["ls"]


def test_encrypted_journal_without_key_raises_rather_than_reading_empty(ale_on, monkeypatch):
    """A reader that cannot decrypt must not present the journal as empty."""
    from app.shadow.journal import JournalWriter, JournalReader
    import app.utils.encryption as enc
    path = ale_on / "k.journal"
    JournalWriter(str(path)).cmd("secret-ish")
    # Same file, a process with no key material.
    monkeypatch.delenv("ZIYA_ENCRYPTION_KEY")
    enc._encryptor = None
    with pytest.raises(ValueError):
        list(JournalReader(str(path))._iter())


def test_encode_line_refuses_plaintext_fallback_when_policy_requires_encryption(ale_on, monkeypatch):
    from app.shadow import journal
    import app.utils.encryption as enc
    e = enc.get_encryptor()
    # Simulate "policy says encrypt, but no DEK": encrypt() returns plaintext.
    monkeypatch.setattr(e, "encrypt", lambda pt, cat="": pt)
    with pytest.raises(RuntimeError):
        journal.encode_line({"t": "cmd", "text": "x", "seq": 1})


def test_end_to_end_read_through_tool_while_disk_is_ciphertext(ale_on):
    import asyncio, threading, time
    from app.shadow.pty_host import ShadowCore
    from app.mcp.tools.shadow_tools import ShadowReadTool
    core = ShadowCore(["sh", "-c", "echo ZQXJV-live; read x; exit 0"], label="ale", headless=True)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True); t.start()
    try:
        deadline = time.time() + 5
        transcript = ""
        while time.time() < deadline and "ZQXJV-live" not in transcript:
            r = asyncio.run(ShadowReadTool().execute(session=core.entry.session_id))
            transcript = r.get("transcript", "")
            time.sleep(0.1)
        assert "ZQXJV-live" in transcript
        raw = open(core.entry.journal, "rb").read()
        assert b"ZQXJV" not in raw and raw.startswith(b"!ale1:")
    finally:
        core.handle_input(b"go\r")
        t.join(timeout=5)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


class Sink:
    """In-memory JournalWriter stand-in recording every record."""

    def __init__(self):
        self.records = []
        self._seq = 0

    def _add(self, rec):
        self._seq += 1
        rec["seq"] = self._seq
        self.records.append(rec)
        return self._seq

    def cmd(self, text):
        return self._add({"t": "cmd", "text": text})

    def output(self, text, cmd_seq):
        return [self._add({"t": "output", "text": text, "cmd_seq": cmd_seq})]

    def exit(self, cmd_seq, code):
        return self._add({"t": "exit", "cmd_seq": cmd_seq, "code": code})

    def meta(self, event, data=None):
        return self._add({"t": "meta", "event": event, "data": data or {}})

    def all_text(self):
        return "\n".join(r.get("text", "") for r in self.records)


# ---------------------------------------------------------------------------
# F1 — a masked line's *echo* must never reach the journal.
#
# Prompt-regex masking (echo ON: installers, REPLs asking for a token)
# relies on dropping the tail line at Enter.  When the secret arrives in
# the same read as Enter (paste, or a fast typist racing the echo) the echo
# has not yet been read from the PTY when on_enter runs, so it lands in the
# next output flush.  The fix suppresses output through the first newline
# after a masked Enter.
# ---------------------------------------------------------------------------

def test_masked_enter_suppresses_the_echo_that_arrives_afterwards():
    from app.shadow.segmenter import Segmenter
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(b"Enter API token: ")
    s.feed_input(b"tok_s3cr3t_value\r")
    s.on_enter(masked=True)                        # gate said: prompt-match
    s.feed_output(b"tok_s3cr3t_value\r\nAuthenticated.\r\n")  # echo, then output
    s.tick(now=time.monotonic() + 5)
    s.close()
    assert "tok_s3cr3t_value" not in sink.all_text()
    # Positive: the non-secret output after the echo line still journals.
    assert "Authenticated." in sink.all_text()


# ---------------------------------------------------------------------------
# F10 — in osc133 mode `on_enter` returned before consulting `masked`, so
# with an instrumented shell the echoed bytes between OSC 133 B and C
# (the secret) were recorded verbatim as the *command* at marker C.
# ---------------------------------------------------------------------------

def test_masked_enter_in_osc133_mode_does_not_record_the_echo_as_a_command():
    from app.shadow.segmenter import Segmenter, MODE_OSC133
    sink = Sink()
    s = Segmenter(sink)
    A, B, C, D = b"\x1b]133;A\x07", b"\x1b]133;B\x07", b"\x1b]133;C\x07", b"\x1b]133;D;0\x07"
    s.feed_output(A + b"Vault token: " + B)
    assert s.mode == MODE_OSC133
    s.feed_input(b"hvs.SECRETTOKEN\r")
    s.feed_output(b"hvs.SECRETTOKEN")          # shell echo between B and C
    s.on_enter(masked=True)
    s.feed_output(C + b"\r\nok\r\n" + D + A + b"$ " + B)
    assert "hvs.SECRETTOKEN" not in sink.all_text()
    assert "ok" in sink.all_text()


# ---------------------------------------------------------------------------
# F2 — multiple lines in one input read must not be concatenated into one
# command record.  `feed_input` treated \r as a no-op, so "a\rb\r" became a
# single cmd "ab"; worse, a pasted "sudo cmd\rPASSWORD\r" recorded the
# password inside the command text because the mask gate is consulted once
# per terminator against termios state that predates the write.
# ---------------------------------------------------------------------------

def test_multiline_input_read_yields_separate_lines_not_a_concatenation():
    from app.shadow.segmenter import Segmenter
    sink = Sink()
    s = Segmenter(sink)
    s.feed_output(b"host$ ")
    s.feed_input(b"echo one\recho two\r")
    first = s.on_enter(masked=False)
    second = s.on_enter(masked=False)
    cmds = [r["text"] for r in sink.records if r["t"] == "cmd"]
    assert "echo oneecho two" not in cmds
    assert cmds[0] == "echo one"
    assert first is not None


def test_pasted_password_after_sudo_in_one_read_is_not_journaled(home):
    """End-to-end through ShadowCore.handle_input: lines after the first in a
    single read cannot be echo-off-checked (the child has not yet run), so
    they are masked fail-closed and never appear as cmd text."""
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-c", "read a; read b; echo done-$a; exit 0"],
                      label="paste", headless=True)
    core.spawn()
    seen = []
    core.journal.add_listener(seen.append)
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.3)
    core.handle_input(b"sudo -k something\rhunter2-PASSWORD\r")
    t.join(timeout=5)
    cmd_texts = [r["text"] for r in seen if r["t"] == "cmd"]
    assert not any("hunter2-PASSWORD" in c for c in cmd_texts)
    assert any(r["t"] == "meta" and r["event"] == "mask" for r in seen)


# ---------------------------------------------------------------------------
# F3 — text that the *model* controls (comment, label, provenance) is
# rendered into the human's terminal.  A raw ESC sequence there is an
# injection into the terminal emulator: DSR/CPR queries make the terminal
# answer on stdin, and those bytes are forwarded to the wrapped shell —
# a model→shell path that bypasses "nothing is typed invisibly".
# ---------------------------------------------------------------------------

def test_overlay_never_forwards_control_sequences_from_chat_text(home):
    from app.shadow.pty_host import ShadowCore, InteractiveFrontend
    core = ShadowCore(["true"], label="ov", headless=False)
    r, w = os.pipe()
    fe = InteractiveFrontend(core, stdin_fd=r, stdout_fd=w)
    hostile = "hello\x1b[6n\x1b]0;pwned\x07\x9b6n\u202eevil"
    fe.overlay(hostile)
    core._render_comment(hostile, {"conversation_id": "c\x1b[2J"})
    os.close(w)
    out = os.read(r, 65536)
    os.close(r)
    core.entry.remove()
    from app.shadow import pty_host as _ph
    body = out.replace(_ph._DIM.encode(), b"").replace(_ph._RESET.encode(), b"")  # our own colour/reset
    assert b"\x1b" not in body, out
    assert b"\x9b" not in body and "\u202e".encode() not in body
    assert b"hello" in body and b"evil" in body  # printable content survives


# ---------------------------------------------------------------------------
# F4 — the wrapped program's OUTPUT routinely contains secrets on real
# hosts (`env`, `aws sts ...`, curl -v with Authorization headers, PEM
# dumps).  Input-side masking cannot see these.  A conservative output
# redactor must run before anything reaches disk.
# ---------------------------------------------------------------------------

def test_output_redactor_covers_common_credential_shapes(tmp_path):
    from app.shadow.journal import JournalWriter
    from app.shadow.journal import JournalReader
    jw = JournalWriter(str(tmp_path / "j.journal"))
    jw.output(
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig_part_here_x\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\n"
        "token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"
        "normal line stays\n", cmd_seq=None)
    jw.cmd("mysql -u root -pSuperSecret1 db")
    text = "\n".join(r.get("text", "") for r in JournalReader(str(tmp_path / "j.journal"))._iter())
    for leaked in ("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI", "eyJhbGciOiJIUzI1NiIs",
                   "MIIEowIBAAKCAQEA", "ghp_ABCDEFGHIJKLMNOP", "SuperSecret1"):
        assert leaked not in text, leaked
    assert "normal line stays" in text
    assert "[REDACTED" in text


# ---------------------------------------------------------------------------
# F5 — same-UID is the whole authn model.  File perms enforce it at the
# path; the socket should also verify the *connecting peer's* uid so a
# mis-permissioned parent directory can never widen access silently.
# ---------------------------------------------------------------------------

def test_socket_rejects_connections_from_another_uid(home, monkeypatch):
    import json
    import socket as sock
    from app.shadow import sock_server
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-c", "read x"], label="peer", headless=True)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    try:
        # Sanity: our own uid is accepted.
        with sock.socket(sock.AF_UNIX, sock.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(core.entry.socket)
            s.sendall(json.dumps({"v": 1, "op": "ping"}).encode() + b"\n")
            assert b'"ok": true' in s.recv(4096)
        # A foreign uid is refused before any request is parsed.
        monkeypatch.setattr(sock_server, "_peer_uid", lambda conn: os.getuid() + 1)
        with sock.socket(sock.AF_UNIX, sock.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(core.entry.socket)
            s.sendall(json.dumps({"v": 1, "op": "ping"}).encode() + b"\n")
            data = s.recv(4096)
        assert b'"ok": true' not in data
    finally:
        core.handle_input(b"\r")
        t.join(timeout=5)


def test_short_socket_dir_refuses_symlink_or_foreign_ownership(home, tmp_path, monkeypatch):
    import tempfile
    from app.shadow import sock_server, registry
    target = tmp_path / "elsewhere"
    target.mkdir()
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    os.symlink(target, fake_tmp / f"ziya-shadow-{os.getuid()}")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_tmp))
    entry = registry.create_session("x", ["true"])
    srv = sock_server.ShadowSocketServer(entry, journal=None)
    with pytest.raises((RuntimeError, OSError)):
        srv._short_socket_path()
    entry.remove()


# ---------------------------------------------------------------------------
# F6 — registry reaping unlinks the paths stored in the entry.  Those paths
# must be confined to the shadow directories so a corrupt/foreign entry can
# never turn `shadow_list` into an arbitrary-unlink primitive.
# ---------------------------------------------------------------------------

def test_registry_remove_only_unlinks_inside_shadow_dirs(home, tmp_path):
    from app.shadow import registry
    victim = tmp_path / "precious.txt"
    victim.write_text("keep me")
    e = registry.create_session("x", ["true"])
    e.journal = str(victim)
    e.socket = str(tmp_path / "also-precious")
    (tmp_path / "also-precious").write_text("keep me too")
    e.remove()
    assert victim.exists() and (tmp_path / "also-precious").exists()
    assert not e.path().exists()


# ---------------------------------------------------------------------------
# F7 — argv is journaled and shown in shadow_list; command lines commonly
# carry credentials (`mysql -pX`, `--token=X`, `KEY=VALUE` env prefixes).
# ---------------------------------------------------------------------------

def test_session_argv_and_default_label_redact_inline_credentials(home):
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["mysql", "-u", "root", "-pHunter2Pass", "--password=AlsoSecret",
                       "API_TOKEN=abcdef123456", "db"], headless=True)
    try:
        for leaked in ("Hunter2Pass", "AlsoSecret", "abcdef123456"):
            assert leaked not in core.entry.label
            assert not any(leaked in a for a in core.entry.argv)
        # The child must still receive the real argv.
        assert "-pHunter2Pass" in core.argv
    finally:
        core.entry.remove()


# ---------------------------------------------------------------------------
# F8 — a model-supplied regex runs inside the user's terminal wrapper.
# Catastrophic backtracking there degrades the human's live session.
# ---------------------------------------------------------------------------

def test_search_refuses_pathological_or_oversized_patterns(tmp_path):
    from app.shadow.journal import JournalReader, JournalWriter
    jw = JournalWriter(str(tmp_path / "j.journal"))
    jw.output("a" * 5000, cmd_seq=None)
    rd = JournalReader(str(tmp_path / "j.journal"))
    start = time.monotonic()
    hits = rd.search(r"(a+)+$b", 5)
    assert time.monotonic() - start < 1.0
    assert hits == []
    assert rd.search("x" * 1000, 5) == []


# ---------------------------------------------------------------------------
# F9 — what the model reads is remote-host-controlled.  It must be
# classified low-trust like fetched web content, hidden-character
# sanitized, and scanned for encoded instruction payloads.
# ---------------------------------------------------------------------------

def test_shadow_read_is_low_trust_and_sanitizes_transcript(home):
    from app.mcp.tool_result_demarcation import classify_trust
    assert classify_trust("shadow_read") == "low"

    import asyncio
    from app.mcp.tools.shadow_tools import ShadowReadTool
    from app.shadow.pty_host import ShadowCore
    # Remote output carrying a bidi override and zero-width chars.  (python,
    # not sh printf: dash has no \u escapes.)
    core = ShadowCore([sys.executable, "-c",
                       "import sys; sys.stdout.write('safe\\u202egnitsil\\u200b line\\n'); "
                       "sys.stdout.flush(); sys.stdin.readline()"],
                      label="tr", headless=True)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.9)
    try:
        res = asyncio.run(ShadowReadTool().execute(session=core.entry.session_id))
        assert res["ok"], res
        assert "\u202e" not in res["transcript"] and "\u200b" not in res["transcript"]
        assert "safe" in res["transcript"]
    finally:
        core.handle_input(b"\r")
        t.join(timeout=5)


@pytest.mark.parametrize("shell", ["/bin/zsh", "/bin/bash"])
def test_line_editing_shell_typing_is_journaled_not_masked(home, shell):
    """Regression: ZLE/readline clear ECHO at every prompt, which the
    original echo-off detector read as a password prompt and masked all
    normal typing.  Typing at a real interactive shell must be journaled;
    a real read -s in the same shell must still be masked."""
    import shutil
    from app.shadow.pty_host import ShadowCore
    if not shutil.which(shell):
        pytest.skip(shell + " not present")
    flags = ["-f", "-i"] if shell.endswith("zsh") else ["--norc", "-i"]
    core = ShadowCore([shell] + flags, label="le", headless=True)
    seen = []
    core.journal.add_listener(seen.append)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.8)
    core.handle_input(b"echo watermelon-visible\r")
    assert _wait(lambda: any(r["t"] == "output" and "watermelon-visible" in r["text"]
                             for r in seen))
    masks_before = sum(1 for r in seen if r["t"] == "meta" and r["event"] == "mask")
    assert masks_before == 0, [r for r in seen if r.get("event") == "mask"]
    # Now a genuine secret read in the same shell.
    core.handle_input(b"read -s zz; echo done-reading\r")
    time.sleep(0.5)
    core.handle_input(b"Hunter2Secret\r")
    assert _wait(lambda: any(r["t"] == "output" and "done-reading" in r["text"] for r in seen))
    core.handle_input(b"exit\r")
    t.join(timeout=5)
    assert not any("Hunter2Secret" in json.dumps(r) for r in seen)
    assert any(r["t"] == "meta" and r["event"] == "mask" and r["data"]["reason"] == "echo-off"
               for r in seen)
