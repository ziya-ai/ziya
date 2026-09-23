"""
Handoff endpoints (app/api/handoff.py) — design/conversation-handoff.md.

Runs the router in a bare FastAPI app with get_project_dir patched at the
module the router imports it from (same harness as tests/test_api_chats.py).
The assertions are on the OUTERMOST surfaces the feature promises:

  - commit produces a child whose NEXT-TURN PRELUDE (the thing the model
    actually sees) carries the document, the predecessor id, and the open
    beads from the SHARED tree;
  - closing a bead via the child drops it from the SOURCE's summary
    listing (b2: one tree, not copies);
  - the source is linked forward and NOT locked — it still accepts a write;
  - a user edit of the inherited doc changes the child's next prelude
    without creating a message.
"""
import json
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.bead import Bead, BeadTree
from app.storage.beads import load_bead_tree, save_bead_tree
from app.storage.chats import ChatStorage
from app.utils.handoff_prelude import build_handoff_prelude, clear_pressure_cache

PID = "p_handoff"


@pytest.fixture
def env(tmp_path):
    project_dir = tmp_path / "projects" / PID
    (project_dir / "chats").mkdir(parents=True)
    storage = ChatStorage(project_dir)
    clear_pressure_cache()
    with patch("app.api.handoff.get_project_dir", return_value=project_dir):
        from app.api.handoff import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), storage


def _write(storage, chat_id, **extra):
    now = int(time.time() * 1000)
    rec = {"id": chat_id, "title": extra.pop("title", f"chat {chat_id}"),
           "messages": extra.pop("messages", []), "createdAt": now,
           "lastActiveAt": now, "_version": now, **extra}
    storage._write_json(storage._chat_file(chat_id), rec)
    return rec


def _msgs(n):
    return [{"id": f"m{i}", "role": "human" if i % 2 == 0 else "assistant",
             "content": f"message {i}", "timestamp": i} for i in range(n)]


def _read(storage, chat_id):
    return storage._read_json(storage._chat_file(chat_id))


def _url(chat_id, suffix=""):
    return f"/api/v1/projects/{PID}/chats/{chat_id}/handoff{suffix}"


# ── title helper ───────────────────────────────────────────────────

def test_continuation_title_increments_segment_number():
    from app.api.handoff import continuation_title
    assert continuation_title("BFD flap") == "BFD flap (2)"
    assert continuation_title("BFD flap (2)") == "BFD flap (3)"
    assert continuation_title("  ") == "Conversation (2)"


# ── GET state ──────────────────────────────────────────────────────

class TestGetState:
    def test_plain_conversation(self, env):
        tc, storage = env
        _write(storage, "src", messages=_msgs(4), additionalFiles=["a.py"])
        r = tc.get(_url("src")).json()
        assert r["draft"] is None and r["handoff"] is None
        assert r["messageCount"] == 4
        assert r["additionalFiles"] == ["a.py"]
        assert r["predecessors"] == []

    def test_missing_is_404(self, env):
        tc, _ = env
        assert tc.get(_url("nope")).status_code == 404


# ── draft edits ────────────────────────────────────────────────────

class TestDraftEdit:
    def test_user_edit_stamps_edited_at_and_next_prelude_sees_it(self, env):
        tc, storage = env
        _write(storage, "src", messages=_msgs(6))
        r = tc.patch(_url("src", "/draft"), json={"document": "## Objective\nfix it"})
        assert r.status_code == 200
        d = r.json()["draft"]
        assert d["editedAt"] and d["sourceMessageCount"] == 6
        prelude = build_handoff_prelude("src", storage=storage)
        assert "fix it" in prelude and "edited by the user" in prelude

    def test_empty_document_clears_draft(self, env):
        tc, storage = env
        _write(storage, "src", handoffDraft={"document": "x", "generatedAt": 1})
        r = tc.patch(_url("src", "/draft"), json={"document": "   "}).json()
        assert r["cleared"] is True
        assert "handoffDraft" not in _read(storage, "src")
        assert build_handoff_prelude("src", storage=storage) == ""


# ── commit ─────────────────────────────────────────────────────────

class TestCommit:
    def test_no_draft_and_no_document_is_400(self, env):
        tc, storage = env
        _write(storage, "src", messages=_msgs(2))
        assert tc.post(_url("src", "/commit"), json={}).status_code == 400

    def test_commit_seam_child_prelude_and_shared_beads(self, env):
        tc, storage = env
        _write(storage, "src", title="ISL BFD flap", messages=_msgs(10),
               additionalFiles=["app/net/bfd.c"], contextIds=["ctx1"],
               handoffDraft={"document": "## Objective\nroot-cause the flap",
                             "generatedAt": 5})
        save_bead_tree(BeadTree(beads=[
            Bead(id="b1", content="verify FDB learns payload", status="parked"),
            Bead(id="b2", content="done thing", status="completed"),
        ]), chat_storage=storage, conversation_id="src")

        r = tc.post(_url("src", "/commit"), json={})
        assert r.status_code == 200, r.text
        body = r.json()
        child_id = body["handedOffTo"]
        child = _read(storage, child_id)

        # Child record shape.
        assert child["title"] == "ISL BFD flap (2)"
        assert child["messages"] == []
        assert child["branchedFrom"] == "src"
        assert child["lineageKind"] == "handoff"
        assert child["lineageRootId"] == "src"
        assert child["additionalFiles"] == ["app/net/bfd.c"]
        assert child["contextIds"] == ["ctx1"]
        assert child["handoff"]["document"] == "## Objective\nroot-cause the flap"
        assert child["handoff"]["sourceMessageCount"] == 10
        assert child["handoff"]["editedAt"] is None  # not user-edited

        # Source: linked forward, draft retained, still writable.
        src = _read(storage, "src")
        assert src["handedOffTo"] == child_id
        assert src["handoffDraft"]["document"].startswith("## Objective")
        assert tc.patch(_url("src", "/draft"), json={"document": "still here"}).status_code == 200

        # THE seam: what the child's model actually sees next turn.
        prelude = build_handoff_prelude(child_id, storage=storage)
        assert "root-cause the flap" in prelude
        assert "src" in prelude                      # predecessor id
        assert "[parked] verify FDB learns payload" in prelude
        assert "done thing" not in prelude
        # A continuation carries NO living-draft instruction until asked /
        # pressured.
        assert "Handoff draft — living" not in prelude

        # b2: close the bead THROUGH THE CHILD → gone from the SOURCE listing.
        tree = load_bead_tree(chat_storage=storage, conversation_id=child_id)
        for b in tree.beads:
            if b.id == "b1":
                b.status = "completed"
        save_bead_tree(tree, chat_storage=storage, conversation_id=child_id)
        src_summary = next(s for s in storage.list_summaries() if s.id == "src")
        assert src_summary.openBeadCount == 0
        assert "verify FDB" not in build_handoff_prelude(child_id, storage=storage)

    def test_commit_with_edited_document_reflects_into_source_draft(self, env):
        tc, storage = env
        _write(storage, "src", messages=_msgs(2),
               handoffDraft={"document": "model text", "generatedAt": 5})
        r = tc.post(_url("src", "/commit"),
                    json={"document": "user text", "workingSet": ["x.py"],
                          "title": "Custom"}).json()
        child = _read(storage, r["handedOffTo"])
        assert child["title"] == "Custom"
        assert child["handoff"]["document"] == "user text"
        assert child["handoff"]["editedAt"]
        assert child["additionalFiles"] == ["x.py"]
        assert _read(storage, "src")["handoffDraft"]["document"] == "user text"

    def test_chained_commit_walks_predecessors(self, env):
        tc, storage = env
        _write(storage, "a", title="T", messages=_msgs(2),
               handoffDraft={"document": "doc a", "generatedAt": 1})
        b = tc.post(_url("a", "/commit"), json={}).json()["handedOffTo"]
        # Continue in b, draft there, hand off again.
        tc.patch(_url(b, "/draft"), json={"document": "doc b"})
        c = tc.post(_url(b, "/commit"), json={}).json()["handedOffTo"]
        assert _read(storage, c)["title"] == "T (3)"
        assert _read(storage, c)["lineageRootId"] == "a"
        state = tc.get(_url(c)).json()
        assert [p["id"] for p in state["predecessors"]] == [b, "a"]
        assert state["handedOffTo"] is None
        assert tc.get(_url(b)).json()["handedOffTo"] == c


# ── inherited-doc edit on the child ────────────────────────────────

class TestInheritedEdit:
    def test_edit_changes_next_prelude_without_a_message(self, env):
        tc, storage = env
        _write(storage, "src", messages=_msgs(2),
               handoffDraft={"document": "v1", "generatedAt": 1})
        child = tc.post(_url("src", "/commit"), json={}).json()["handedOffTo"]
        r = tc.patch(_url(child), json={"document": "v2 corrected"})
        assert r.status_code == 200 and r.json()["handoff"]["editedAt"]
        rec = _read(storage, child)
        assert rec["messages"] == []
        p = build_handoff_prelude(child, storage=storage)
        assert "v2 corrected" in p
        assert "\nv1\n" not in p

    def test_non_continuation_is_400(self, env):
        tc, storage = env
        _write(storage, "src")
        assert tc.patch(_url("src"), json={"document": "x"}).status_code == 400

    def test_empty_inherited_doc_is_400(self, env):
        tc, storage = env
        _write(storage, "src", handoffDraft={"document": "v1", "generatedAt": 1})
        child = tc.post(_url("src", "/commit"), json={}).json()["handedOffTo"]
        assert tc.patch(_url(child), json={"document": ""}).status_code == 400
