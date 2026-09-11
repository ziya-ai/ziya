"""shadow_* builtin tools against a live headless session (design §9)."""
import asyncio
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def live_session(home):
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-c", "echo tool-visible-line; read x; echo bye"],
                      label="tools", headless=True)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and core.journal._seq < 2:
        time.sleep(0.05)
    yield core
    core.terminate_child()
    t.join(timeout=5)


def _run(coro):
    # asyncio.run(): get_event_loop() breaks once any earlier test in the
    # session has closed the default loop (e.g. via its own asyncio.run()).
    return asyncio.run(coro)


def test_tools_are_registered_in_builtin_category():
    from app.mcp.builtin_tools import (
        BUILTIN_TOOL_CATEGORIES, get_builtin_tools_for_category,
    )
    assert "shadow" in BUILTIN_TOOL_CATEGORIES
    names = {t().name for t in get_builtin_tools_for_category("shadow")}
    assert names == {"shadow_list", "shadow_read", "shadow_comment", "shadow_set_meta",
                     "shadow_attach", "shadow_detach"}


def test_list_empty_gives_hint(home):
    from app.mcp.tools.shadow_tools import ShadowListTool
    out = _run(ShadowListTool().execute())
    assert out["ok"] and out["count"] == 0 and "ziya shadow" in out["hint"]


def test_read_list_comment_set_meta_round_trip(live_session):
    from app.mcp.tools.shadow_tools import (
        ShadowListTool, ShadowReadTool, ShadowCommentTool, ShadowSetMetaTool,
    )
    sid = live_session.entry.session_id

    out = _run(ShadowListTool().execute())
    assert out["count"] == 1 and out["sessions"][0]["session_id"] == sid

    # Default tail read shows the child's output in transcript form.
    deadline = time.time() + 5
    while time.time() < deadline:
        out = _run(ShadowReadTool().execute(session=sid))
        if "tool-visible-line" in out.get("transcript", ""):
            break
        time.sleep(0.05)
    assert out["ok"] and out["mode"] == "tail"
    assert "tool-visible-line" in out["transcript"]

    # Search mode
    out = _run(ShadowReadTool().execute(session=sid, search=r"tool-visible"))
    assert out["mode"] == "search" and out["record_count"] >= 1

    # Comment carries the conversation's provenance into the journal
    out = _run(ShadowCommentTool().execute(session=sid, text="on it",
                                           conversation_id="conv-abcdef-123456"))
    assert out["ok"] and out["rendered"] is False
    page = _run(ShadowReadTool().execute(session=sid, from_seq=out["seq"], max_records=1))
    assert page["mode"] == "page" and "comment: on it" in page["transcript"]

    # Relabel, then resolve by the new label
    out = _run(ShadowSetMetaTool().execute(session=sid, label="prod-42", data={"env": "prod"}))
    assert out["ok"] and out["label"] == "prod-42"
    out = _run(ShadowReadTool().execute(session="prod-42", last_n_commands=1))
    assert out["ok"] and out["session_id"] == sid


def test_unknown_session_is_a_clean_error(home):
    from app.mcp.tools.shadow_tools import ShadowReadTool
    out = _run(ShadowReadTool().execute(session="nope"))
    assert out["ok"] is False and out["error"] == "not_found"
