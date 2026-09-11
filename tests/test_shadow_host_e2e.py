"""End-to-end: ShadowCore runs a real child in a PTY, journals it, serves
it over the Unix socket, and the chat-side client reads it back.

Everything under HOME is redirected so ~/.ziya/shadow/sessions is a
temp dir.  These tests need a POSIX pty (macOS/Linux) — same floor as
the feature itself.
"""
import os
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_headless_session_journals_output_and_cleans_up(home):
    from app.shadow import registry
    from app.shadow.journal import JournalReader
    from app.shadow.pty_host import ShadowCore

    core = ShadowCore(["sh", "-c", "echo hello-shadow; exit 3"], label="t1", headless=True)
    journal_path = core.entry.journal
    core.spawn()
    assert registry.load_session(core.entry.session_id) is not None
    # Snapshot the journal before run() unlinks it on exit.
    seen = []
    core.journal.add_listener(seen.append)
    code = core.run(stdin_fd=None)

    assert code == 3
    texts = [r["text"] for r in seen if r["t"] == "output"]
    assert any("hello-shadow" in t for t in texts)
    ends = [r for r in seen if r["t"] == "meta" and r["event"] == "exit_session"]
    assert ends and ends[0]["data"]["code"] == 3
    # Unlink-on-exit (design Q2) and registry removal.
    assert not os.path.exists(journal_path)
    assert registry.load_session(core.entry.session_id) is None
    assert not os.path.exists(core.entry.socket)


def test_socket_api_end_to_end_through_client(home):
    from app.shadow import client, registry
    from app.shadow.pty_host import ShadowCore

    # A child that prints, waits for stdin, then prints again — long-lived
    # enough for the socket round-trips.
    # The child must outlive every socket assertion: a second ``read``
    # keeps it (and the socket) alive until terminate_child() below.
    core = ShadowCore(["sh", "-c", "echo first-line; read x; echo got-$x; read y; exit 0"],
                      label="e2e", headless=True)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    try:
        sid = core.entry.session_id
        # shadow_list sees it
        assert _wait(lambda: any(s["session_id"] == sid for s in client.list_sessions()))

        # info + tail reflect the first output line once it has flushed
        assert _wait(lambda: any(
            r["t"] == "output" and "first-line" in r["text"]
            for r in client.tail(sid, 5)))
        info = client.request(core.entry, "info")
        assert info["session_id"] == sid and info["tail_seq"] >= 1

        # search
        hits = client.search(sid, r"first-l\w+")
        assert hits and hits[0]["t"] == "output"

        # comment is journaled with provenance (headless: not rendered)
        resp = client.comment(sid, "looking at this now", {"conversation_id": "conv-1"})
        assert resp["rendered"] is False
        recs = client.read(sid, resp["seq"], 1)["records"]
        assert recs[0]["event"] == "comment"
        assert recs[0]["data"]["provenance"] == {"conversation_id": "conv-1"}

        # set_meta relabels; registry and list reflect it
        client.set_meta(sid, label="prod-42", data={"env": "prod"})
        entry = registry.load_session(sid)
        assert entry.label == "prod-42" and entry.meta == {"env": "prod"}
        assert client.resolve_one("prod-42").session_id == sid
        assert client.resolve_one(f"prod-42:{sid}").session_id == sid

        # phase-2/3 ops refuse with their §5 codes rather than crashing
        with pytest.raises(client.ShadowError) as ei:
            client.request(core.entry, "exec", command="ls")
        assert ei.value.code == "exec_disabled"
        with pytest.raises(client.ShadowError) as ei:
            client.request(core.entry, "send_line", lease_id="x", text="ls")
        assert ei.value.code == "control_disabled"
        with pytest.raises(client.ShadowError) as ei:
            client.request(core.entry, "bogus")
        assert ei.value.code == "bad_request"

        # version mismatch is refused explicitly
        import json, socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(core.entry.socket)
            s.sendall(json.dumps({"v": 99, "op": "ping"}).encode() + b"\n")
            assert b"version_mismatch" in s.recv(4096)

        # human-typed input path: masking gate consulted, cmd journaled
        core.handle_input(b"payload\r")
        assert _wait(lambda: any(
            r["t"] == "output" and "got-payload" in r["text"]
            for r in client.tail(sid, 5)))
        # Ordering invariant: the cmd record precedes the output it caused
        # and the output is attributed to it.  handle_input ran on this
        # thread while the PTY loop ran on another; a fast child used to
        # get its output journaled (cmd_seq=None) before the cmd record.
        recs = client.read(sid, 1, 500)["records"]
        cmd = next(r for r in recs if r["t"] == "cmd" and r["text"] == "payload")
        out = next(r for r in recs if r["t"] == "output" and "got-payload" in r["text"])
        assert cmd["seq"] < out["seq"]
        assert out["cmd_seq"] == cmd["seq"]
    finally:
        core.terminate_child()
        t.join(timeout=5)
    assert not t.is_alive()
    assert registry.load_session(sid) is None


def test_subscribe_pushes_new_records(home):
    import json, socket
    from app.shadow.pty_host import ShadowCore

    core = ShadowCore(["sh", "-c", "read x; echo pushed-$x"], label="sub", headless=True)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(core.entry.socket)
            s.sendall(json.dumps({"v": 1, "op": "subscribe", "from_seq": 1}).encode() + b"\n")
            buf = b""
            while b'"subscribed"' not in buf:
                buf += s.recv(4096)
            assert _wait(lambda: core.server.subscriber_count == 1)
            core.handle_input(b"now\r")
            deadline = time.time() + 5
            while time.time() < deadline and b"pushed-now" not in buf:
                buf += s.recv(4096)
            assert b"pushed-now" in buf
    finally:
        core.terminate_child()
        t.join(timeout=5)


def test_frontend_menu_prefix_handling(home):
    """C-x C-z opens the menu; C-x C-x passes a literal C-x; C-x <other>
    delivers both bytes.  Exercised with a stub core so no PTY is needed."""
    from app.shadow.pty_host import InteractiveFrontend, MENU_PREFIX, MENU_KEY

    class StubEntry:
        session_id, label, segmentation, allow_exec = "abc123", "stub", "raw", False
        display = "stub (abc123)"
        attached = None

        def save(self):
            pass

    class StubJournal:
        def __init__(self):
            self.metas = []
            self._seq = 0

        def meta(self, ev, data=None):
            self._seq += 1
            self.metas.append((ev, data))
            return self._seq

    class StubCore:
        entry = StubEntry()
        journal = StubJournal()

        def __init__(self):
            self.typed = b""
            self.frontend = None

        def handle_input(self, data):
            self.typed += data

        def terminate_child(self, *a):
            self.terminated = True

    r, w = os.pipe()
    core = StubCore()
    fe = InteractiveFrontend(core, stdin_fd=r, stdout_fd=w)

    fe.handle_keys(b"ls" + MENU_PREFIX + MENU_PREFIX + b"\r")
    assert core.typed == b"ls\x18\r"

    core.typed = b""
    fe.handle_keys(MENU_PREFIX + b"q")          # not the menu key
    assert core.typed == b"\x18q"

    core.typed = b""
    fe.handle_keys(MENU_PREFIX + MENU_KEY)      # opens menu
    assert fe._mode == "menu" and core.typed == b""
    fe.handle_keys(b"x")                        # toggle exec
    assert core.entry.allow_exec is True
    assert ("allow_exec", {"enabled": True}) in core.journal.metas
    assert fe._mode is None

    # ask composer: journals an ask record, flags pending_ask
    fe.handle_keys(MENU_PREFIX + MENU_KEY + b"a")
    assert fe._mode == "compose"
    fe.handle_keys(b"why did that fail?\r")
    asks = [d for ev, d in core.journal.metas if ev == "ask"]
    assert asks and asks[0]["question"] == "why did that fail?"
    assert core.entry.pending_ask is True
    assert core.typed == b""                    # nothing reached the child
    os.close(r); os.close(w)


def test_format_session_table_empty_and_populated():
    from app.shadow.client import format_session_table
    assert "ziya shadow" in format_session_table([])
    table = format_session_table([{
        "session_id": "a3f21e", "label": "ssh prod-42", "segmentation": "raw",
        "allow_exec": False, "pending_ask": True, "last_activity": "2026-06-11T00:41:03Z",
        "started_at": "", "argv": ["ssh", "prod-42"]}])
    lines = table.splitlines()
    assert lines[0].startswith("ID") and "a3f21e" in lines[1]
    assert "-/ask" in lines[1] and "ssh prod-42" in lines[1]
