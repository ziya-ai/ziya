"""Attachment: bind a live shadow session to a chat conversation (§6.1, phase 2).

Exercises the socket attach/detach ops through a real headless ShadowCore,
the chat-side tools (which take the binding identity from the injected
conversation_id, not a user argument), and the frontend title chat-slot.
"""
import asyncio
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


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _headless(argv, label):
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(argv, label=label, headless=True)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    return core, t


def test_attach_binds_journals_and_shows_in_list(home):
    from app.shadow import registry
    from app.mcp.tools.shadow_tools import (
        ShadowAttachTool, ShadowListTool, ShadowDetachTool,
    )
    core, t = _headless(["sh", "-c", "echo up; read x; exit 0"], "worker")
    sid = core.entry.session_id
    try:
        assert _wait(lambda: any(s["session_id"] == sid for s in _run(ShadowListTool().execute())["sessions"]))

        att = _run(ShadowAttachTool().execute(session=sid, conversation_id="conv-XYZ-123456"))
        assert att["ok"] and att["attached"]["conversation_id"] == "conv-XYZ-123456"
        assert att["attached"]["mode"] == "observe"

        # Registry entry persists it; list surfaces it.
        entry = registry.load_session(sid)
        assert entry.attached["conversation_id"] == "conv-XYZ-123456"
        row = next(s for s in _run(ShadowListTool().execute())["sessions"] if s["session_id"] == sid)
        assert row["attached"]["conversation_id"] == "conv-XYZ-123456"

        # Journaled with provenance.
        from app.shadow.journal import JournalReader
        recs = JournalReader(entry.journal)._iter()
        assert any(r.get("event") == "attach" and r["data"]["conversation_id"] == "conv-XYZ-123456"
                   for r in recs)

        det = _run(ShadowDetachTool().execute(session=sid, conversation_id="conv-XYZ-123456"))
        assert det["ok"] and det["attached"] is None
        assert registry.load_session(sid).attached is None
    finally:
        core.terminate_child()
        t.join(timeout=5)


def test_attach_requires_conversation_context(home):
    from app.mcp.tools.shadow_tools import ShadowAttachTool
    core, t = _headless(["sh", "-c", "read x"], "w")
    try:
        r = _run(ShadowAttachTool().execute(session=core.entry.session_id))  # no conversation_id
        assert r["ok"] is False and r["error"] == "no_conversation"
    finally:
        core.terminate_child(); t.join(timeout=5)


def test_takeover_is_reported_and_wrong_conversation_cannot_detach(home):
    from app.shadow import client
    from app.mcp.tools.shadow_tools import ShadowAttachTool, ShadowDetachTool
    core, t = _headless(["sh", "-c", "read x"], "w")
    sid = core.entry.session_id
    try:
        _run(ShadowAttachTool().execute(session=sid, conversation_id="conv-A"))
        r2 = _run(ShadowAttachTool().execute(session=sid, conversation_id="conv-B"))
        assert r2["attached"]["conversation_id"] == "conv-B"
        assert r2["replaced"] == "conv-A" and r2["note"] and "conv-A"[:8] in r2["note"]

        # conv-A can no longer detach conv-B's binding.
        with pytest.raises(client.ShadowError) as ei:
            client.detach(sid, conversation_id="conv-A")
        assert ei.value.code == "not_attached"
        # conv-B can.
        assert client.detach(sid, conversation_id="conv-B")["attached"] is None
    finally:
        core.terminate_child(); t.join(timeout=5)


def test_title_prefix_carries_chat_slot_only_when_attached(home):
    """InteractiveFrontend._title_prefix reflects the entry's attachment."""
    from app.shadow.pty_host import InteractiveFrontend

    class StubJournal:
        _seq = 0
        def meta(self, *a, **k): return 0

    class StubEntry:
        session_id, label, segmentation, allow_exec = "abc123", "prod-42", "raw", False
        attached = None
        def save(self): pass

    class StubCore:
        def __init__(self):
            self.entry = StubEntry()
            self.journal = StubJournal()

    r, w = os.pipe()
    fe = InteractiveFrontend(StubCore(), stdin_fd=r, stdout_fd=w)
    assert fe._title_prefix() == "⏺ prod-42"
    fe.core.entry.attached = {"conversation_id": "conv-deadbeef-tail", "mode": "observe"}
    assert fe._title_prefix() == "⏺ prod-42 · chat conv-dea"
    os.close(r); os.close(w)


def test_headless_attach_callback_is_safe_without_frontend(home):
    """_on_attach must no-op when there is no terminal (headless)."""
    from app.shadow import client
    core, t = _headless(["sh", "-c", "read x"], "w")
    try:
        # frontend is None on a headless core; attach must not raise.
        assert client.attach(core.entry.session_id, "conv-Z")["attached"]["conversation_id"] == "conv-Z"
    finally:
        core.terminate_child(); t.join(timeout=5)
