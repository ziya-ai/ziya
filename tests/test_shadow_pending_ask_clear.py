"""A shadow-initiated ask (§8) is cleared once the chat answers via comment.

Regression: answering an ask left ``pending_ask=True`` on the registry
entry, so ``shadow_list`` and the per-turn context tag kept reporting a
"PENDING QUESTION" the chat had already answered.  A comment is the chat
speaking to the terminal, which is exactly the reply the ask waited for,
so it clears the flag.
"""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _server(entry):
    from app.shadow.journal import JournalWriter
    from app.shadow.sock_server import ShadowSocketServer
    return ShadowSocketServer(entry, JournalWriter(entry.journal))


def test_comment_clears_pending_ask(home):
    from app.shadow import registry
    entry = registry.create_session("t", ["/bin/sh"])
    try:
        entry.pending_ask = True
        entry.save()
        srv = _server(entry)

        resp = srv.dispatch({"v": 1, "op": "comment", "text": "here is your answer",
                             "provenance": {"conversation_id": "c1"}})
        assert "error" not in resp
        assert resp["cleared_ask"] is True
        assert entry.pending_ask is False

        # Reload from disk: the registry entry is what shadow_list / the
        # context tag read, so the clear must be persisted, not just in-memory.
        reloaded = registry.load_session(entry.session_id)
        assert reloaded is not None and reloaded.pending_ask is False
    finally:
        entry.remove()


def test_comment_without_pending_ask_reports_not_cleared(home):
    from app.shadow import registry
    entry = registry.create_session("t", ["/bin/sh"])
    try:
        assert entry.pending_ask is False
        srv = _server(entry)
        resp = srv.dispatch({"v": 1, "op": "comment", "text": "just a note",
                             "provenance": {}})
        assert resp["cleared_ask"] is False
        assert entry.pending_ask is False
    finally:
        entry.remove()
