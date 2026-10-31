"""
Bead storage must act on the conversation's REAL home record, not the
window's project.

Incident (2026-10-25): conversation 31fa660d (homed in KuiperEAR-switch-mgr,
project f66402b0) was being worked from a window open on ziya-0.4.0.1
(project e0fba134), where it is surfaced as a global chat.  The server log
showed ``chat_home: 31fa660d is not stored under e0fba134; using its
27-message copy in f66402b0`` for every endpoint that goes through
resolve_chat_home -- but bead storage did not.  ``_resolve_chat_storage``
resolved ChatStorage(window project) and ``.get(cid)`` returned None, so
the bead tools read an EMPTY fallback tree (~/.ziya/beads/<cid>.json),
wrote completions/creations there, and a later turn from the home window
rewrote the record and deleted the fallback file.  Net effect: the model
"closed out" the same three stale beads turn after turn and they came back
parked every time.

The 2026-10-02 chat_home fix was applied to context tools, auto-context
and handoff; this pins the same resolution for beads.

DIRECTION: against the unpatched app/storage/beads.py every test here
fails (load returns an empty tree; the completion lands in a fallback file
and the home record is untouched).
"""
import time

import pytest

from app.context import set_conversation_id, set_project_root
from app.models.project import ProjectCreate
from app.storage.beads import load_bead_tree, update_bead_status, add_bead
from app.storage.chats import ChatStorage
from app.storage.projects import ProjectStorage
from app.models.bead import Bead

CID = "conv-global-1"
STALE = "bead_stale0001"


def _write_chat(storage, chat_id, beads):
    now = int(time.time() * 1000)
    rec = {"id": chat_id, "title": "t", "createdAt": now, "lastActiveAt": now,
           "_version": now, "_beads": beads,
           "messages": [{"id": f"m{i}", "role": "human", "content": "x",
                         "timestamp": i} for i in range(5)]}
    storage._write_json(storage._chat_file(chat_id), rec)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    a_root = tmp_path / "window_root"
    b_root = tmp_path / "home_root"
    a_root.mkdir()
    b_root.mkdir()
    ps = ProjectStorage(tmp_path / "home")
    pa = ps.create(ProjectCreate(path=str(a_root), name="window"))
    pb = ps.create(ProjectCreate(path=str(b_root), name="home"))
    from app.utils.paths import get_project_dir
    sb = ChatStorage(get_project_dir(pb.id))
    (get_project_dir(pa.id) / "chats").mkdir(parents=True, exist_ok=True)
    _write_chat(sb, CID, [{"id": STALE, "content": "stale thread",
                           "status": "parked", "parent_id": None,
                           "message_index": 2, "created_at": 1}])
    # The request names the WINDOW's project; the chat lives in the other.
    set_project_root(str(a_root))
    set_conversation_id(CID)
    return sb, pa, pb, tmp_path / "home"


def test_load_sees_home_record_from_another_window(env):
    tree = load_bead_tree()
    assert [b.id for b in tree.beads] == [STALE]


def test_complete_from_another_window_lands_on_home_record(env):
    sb, _pa, _pb, ziya_home = env
    assert update_bead_status(STALE, "completed") is not None
    raw = sb._read_json(sb._chat_file(CID))
    assert raw["_beads"][0]["status"] == "completed"
    # Positive pairing: the record was rewritten, not just left alone.
    assert len(raw["messages"]) == 5
    # No disconnected fallback tree was created for this conversation.
    assert not (ziya_home / "beads" / f"{CID}.json").exists()


def test_create_from_another_window_joins_home_tree(env):
    sb, _pa, _pb, _home = env
    add_bead(Bead(content="new thread", status="parked", parent_id=None))
    raw = sb._read_json(sb._chat_file(CID))
    assert {b["content"] for b in raw["_beads"]} == {"stale thread", "new thread"}


def test_home_window_unaffected(env, tmp_path):
    # Positive control: a request from the home project resolves as before.
    set_project_root(str(tmp_path / "home_root"))
    assert [b.id for b in load_bead_tree().beads] == [STALE]
