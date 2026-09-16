"""Per-turn shadow context tag (app/shadow/context.py).

The tag rides the CurrentDateTime relocation onto the user message, so it
must be cheap, never raise, and describe only what is attached to *this*
conversation (other sessions become a count, not content).
"""
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
def live(home):
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-c", "echo up; read x; exit 0"], label="ctx", headless=True)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.6)
    yield core
    core.handle_input(b"go\r")
    t.join(timeout=5)


def test_no_conversation_or_no_sessions_is_empty(home):
    from app.shadow.context import attached_sessions_tag
    assert attached_sessions_tag(None) == ""
    assert attached_sessions_tag("conv-1") == ""


def test_unattached_session_is_only_a_count(live):
    from app.shadow.context import attached_sessions_tag
    tag = attached_sessions_tag("conv-1")
    assert "1 unattached live shadow session" in tag
    assert live.entry.session_id not in tag          # no content leaks for unattached
    assert "shadow_read(" not in tag
    assert "Do not offer to attach unprompted" in tag


def test_session_attached_elsewhere_is_invisible_to_other_conversations(live):
    """A terminal bound to conversation A must produce NO tag in conversation
    B — not even a count.  Otherwise every unrelated chat sees "1 other
    session ... shadow_list to see them" and starts asking the user whether
    to attach to a terminal that is already actively owned."""
    from app.shadow import client
    from app.shadow.context import attached_sessions_tag
    client.attach(live.entry.session_id, "conv-A")
    assert attached_sessions_tag("conv-A")             # owner still sees it
    assert attached_sessions_tag("conv-B") == ""       # nobody else does


def test_attached_session_is_described_with_journal_state(live):
    from app.shadow import client
    from app.shadow.context import attached_sessions_tag
    sid = live.entry.session_id
    client.attach(sid, "conv-1")
    tag = attached_sessions_tag("conv-1")
    assert tag.startswith("<AttachedShadowSessions>") and tag.endswith("</AttachedShadowSessions>")
    assert f"ctx ({sid})" in tag
    assert "journal records" in tag and "shadow_read(" in tag
    assert "unattached" not in tag
    # A different conversation sees nothing at all: the terminal is owned.
    assert attached_sessions_tag("conv-2") == ""


def test_pending_ask_is_called_out(live):
    from app.shadow import client
    from app.shadow.context import attached_sessions_tag
    sid = live.entry.session_id
    client.attach(sid, "conv-1")
    live.entry.pending_ask = True
    live.entry.save()
    assert "PENDING QUESTION" in attached_sessions_tag("conv-1")


def test_unreachable_host_does_not_raise(live, monkeypatch):
    from app.shadow import client
    from app.shadow.context import attached_sessions_tag
    client.attach(live.entry.session_id, "conv-1")

    def boom(*a, **k):
        raise OSError("gone")
    monkeypatch.setattr(client, "request", boom)
    tag = attached_sessions_tag("conv-1")
    assert "host not responding" in tag


def test_list_table_has_chat_column():
    from app.shadow.client import format_session_table
    row = {"session_id": "a3f21e", "label": "prod-42", "segmentation": "osc133",
           "allow_exec": False, "pending_ask": False, "last_activity": "t", "started_at": "",
           "argv": ["ssh", "prod-42"], "attached": {"conversation_id": "df488630-c175"}}
    table = format_session_table([row]).splitlines()
    assert "CHAT" in table[0] and "df488630" in table[1]
    row["attached"] = None
    assert "  -  " in format_session_table([row]).splitlines()[1]
