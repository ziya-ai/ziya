"""chat_search / chat_read / chat_list must not read chat files on the event loop.

The stall watchdog caught the loop here (2026-10-09):

    chat_history_tools.py:284 execute -> search_chats
      -> chat_search.py _read_chat_data -> Path.read_bytes

``ChatSearchTool.execute`` is ``async def`` but awaited nothing, so the
whole walk over every chat file in scope (read + decrypt + scan) ran ON
the loop, and every other request stalled behind it.  ``chat_read``
(``_load_chat``) and ``chat_list`` (``ChatStorage.list_summaries``) had
the same shape.  Each heavy call must go through ``asyncio.to_thread``.

Behavioural, not static: each tool is driven through its real
``execute()`` with the disk-bound callee swapped for a probe that records
the thread it ran on.  Against the pre-fix file the probe runs on the
loop thread and every test here fails.
"""
import asyncio
import threading
from pathlib import Path

import pytest

from app.mcp.tools import chat_history_tools as cht


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def scope(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "projects" / "p1").mkdir(parents=True)
    monkeypatch.setattr(cht, "_resolve_scope", lambda: {
        "ziya_home": home, "project_id": "p1", "conversation_id": None,
    })
    return home


class _Probe:
    """Records the thread the wrapped callable ran on."""

    def __init__(self, result):
        self.result = result
        self.thread = None
        self.calls = 0

    def __call__(self, *a, **kw):
        self.thread = threading.current_thread()
        self.calls += 1
        return self.result


def _assert_off_loop(probe: _Probe):
    assert probe.calls == 1, "probe never ran -- the seam was not exercised"
    assert probe.thread is not threading.main_thread(), (
        f"{probe!r} ran on the event-loop thread; wrap it in asyncio.to_thread")


def test_chat_search_runs_search_chats_off_the_loop(scope, monkeypatch):
    import app.storage.chat_search as cs
    probe = _Probe([])
    monkeypatch.setattr(cs, "search_chats", probe)
    r = _run(cht.ChatSearchTool().execute(query="widget"))
    assert r.get("count") == 0 and not r.get("error")
    _assert_off_loop(probe)


def test_chat_read_runs_load_chat_off_the_loop(scope, monkeypatch):
    probe = _Probe(None)
    monkeypatch.setattr(cht, "_load_chat", probe)
    r = _run(cht.ChatReadTool().execute(chat_id="abc"))
    assert r.get("error") and "abc" in r["message"]
    _assert_off_loop(probe)


def test_chat_list_runs_list_summaries_off_the_loop(scope, monkeypatch):
    from app.storage.chats import ChatStorage
    probe = _Probe([])
    monkeypatch.setattr(ChatStorage, "list_summaries", lambda self: probe())
    r = _run(cht.ChatListTool().execute())
    assert r.get("count") == 0 and not r.get("error")
    _assert_off_loop(probe)


def test_module_imports_asyncio_and_uses_to_thread_in_every_execute():
    """Structural companion: a future tool added to this module gets the
    same scrutiny even before it has a behavioural test."""
    src = Path(cht.__file__).read_text()
    assert "import asyncio" in src
    bodies = src.split("async def execute(")[1:]
    assert len(bodies) == 3, f"expected 3 execute() bodies, found {len(bodies)}"
    for i, body in enumerate(bodies):
        assert "await asyncio.to_thread(" in body, (
            f"execute() #{i + 1} awaits no to_thread -- its disk I/O runs on the loop")
