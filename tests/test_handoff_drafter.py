"""
Handoff drafter: a conversation too large to draft its own handoff gets a
separate, empty conversation that reads it in windows and writes ITS draft
(app/utils/handoff_drafter.py).

Seams under test:
  - the endpoint creates an empty drafter record carrying handoffDraftFor
    and returns the first prompt; the source is untouched;
  - handoff_write(target_conversation_id=<source>) from the drafter lands on
    the SOURCE record (and not the drafter's), and the source's next-turn
    prelude carries it;
  - the same call from an unrelated conversation is refused and writes
    nothing (handoffDraftFor is the only grant);
  - the drafter itself never gets the "draft your own handoff" instruction,
    while an ordinary record at the same pressure does (positive control).
"""
import asyncio
import json
import os
import time
from unittest.mock import patch

import pytest

from app.utils import handoff_prelude as hp
from app.utils.handoff_drafter import (
    DRAFT_FOR_FIELD, drafter_prompt, drafter_title, may_target, new_drafter_record,
)
from app.utils.handoff_prelude import build_handoff_prelude

NOW_MS = int(time.time() * 1000)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _clean_pressure():
    hp.clear_pressure_cache()
    yield
    hp.clear_pressure_cache()


# ── pure helpers ───────────────────────────────────────────────────

class TestPure:
    def test_prompt_names_the_source_and_the_windowed_tools(self):
        p = drafter_prompt("src-123", "ISL BFD flap", 812)
        assert '"ISL BFD flap"' in p and "812 messages" in p
        assert 'chat_read(chat_id="src-123"' in p
        assert 'conversation_ids=["src-123"]' in p
        assert 'handoff_write(target_conversation_id="src-123", mode="merge"' in p
        assert "(ziya://chat/src-123)" in p
        # Literal braces survive (no stray format placeholders).
        assert "sections={...}" in p

    def test_record_is_empty_unlinked_and_points_at_the_source(self):
        src = {"id": "s", "title": "Big", "folderId": "f1", "isGlobal": True,
               "additionalFiles": ["huge.py"], "lineageRootId": "root"}
        rec = new_drafter_record(src, "s", "p1", NOW_MS, drafter_id="d")
        assert rec["id"] == "d" and rec["messages"] == []
        assert rec["title"] == drafter_title("Big") == "Handoff draft: Big"
        assert rec[DRAFT_FOR_FIELD] == "s"
        assert rec["folderId"] == "f1" and rec["isGlobal"] is True
        # Does not inherit the working set or the lineage (bead tree).
        assert rec["additionalFiles"] == []
        for k in ("branchedFrom", "lineageKind", "lineageRootId"):
            assert k not in rec

    def test_may_target(self):
        assert may_target({"id": "a"}, "a")
        assert may_target({"id": "d", DRAFT_FOR_FIELD: "s"}, "s")
        assert not may_target({"id": "d", DRAFT_FOR_FIELD: "s"}, "other")
        assert not may_target({"id": "x"}, "s")
        assert not may_target(None, "s") and not may_target({"id": "a"}, None)


class TestPrelude:
    def _storage(self, tmp_path, rec):
        from app.storage.chats import ChatStorage
        (tmp_path / "chats").mkdir(exist_ok=True)
        s = ChatStorage(tmp_path)
        s._write_json(s._chat_file(rec["id"]), rec)
        return s

    def test_drafter_gets_no_auto_draft_instruction(self, tmp_path):
        rec = new_drafter_record({"title": "Big"}, "s", "p", NOW_MS, drafter_id="d")
        s = self._storage(tmp_path, rec)
        assert build_handoff_prelude("d", storage=s, pressure=0.99) == ""

    def test_positive_control_ordinary_record_does(self, tmp_path):
        rec = {"id": "o", "title": "t", "messages": [], "createdAt": NOW_MS,
               "lastActiveAt": NOW_MS, "_version": NOW_MS}
        s = self._storage(tmp_path, rec)
        assert "[Handoff draft — start one now]" in build_handoff_prelude(
            "o", storage=s, pressure=0.99)


# ── endpoint + tools (need the app/api/handoff.py and handoff_tools.py diffs) ──

@pytest.fixture
def env(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    pid = "p_d_" + os.urandom(3).hex()
    pdir = ziya_home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "D", "path": str(project_root.resolve()),
        "createdAt": NOW_MS, "lastAccessedAt": NOW_MS,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (ziya_home / "projects" / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): pid}))
    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: ziya_home)
    from app.storage import chat_index
    chat_index.invalidate()
    from app.storage.chats import ChatStorage
    storage = ChatStorage(pdir)

    def write(cid, **extra):
        rec = {"id": cid, "title": extra.pop("title", cid), "groupId": None,
               "contextIds": [], "skillIds": [], "additionalFiles": [],
               "additionalPrompt": None,
               "messages": extra.pop("messages", [
                   {"id": "m1", "role": "human", "content": "q", "timestamp": NOW_MS},
                   {"id": "m2", "role": "assistant", "content": "a", "timestamp": NOW_MS}]),
               "createdAt": NOW_MS, "lastActiveAt": NOW_MS, "_version": NOW_MS, **extra}
        storage._write_json(storage._chat_file(cid), rec)
        return rec

    from app import context as _ctx
    tokens = []

    def as_conversation(cid):
        tokens.append(_ctx._request_conversation_id.set(cid))
        tokens.append(_ctx._request_project_root.set(str(project_root.resolve())))

    yield {"pid": pid, "pdir": pdir, "storage": storage, "write": write,
           "as": as_conversation}
    for t in reversed(tokens):
        t.var.reset(t)


def _read(env, cid):
    return env["storage"]._read_json(env["storage"]._chat_file(cid))


class TestEndpoint:
    def test_creates_drafter_and_leaves_source_alone(self, env):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        env["write"]("src", title="Big one", folderId="f9")
        before = _read(env, "src")
        with patch("app.api.handoff.get_project_dir", return_value=env["pdir"]):
            from app.api.handoff import router
            app = FastAPI()
            app.include_router(router)
            r = TestClient(app).post(
                f"/api/v1/projects/{env['pid']}/chats/src/handoff/drafter")
        assert r.status_code == 200, r.text
        body = r.json()
        d = body["drafter"]
        assert d[DRAFT_FOR_FIELD] == "src" and d["folderId"] == "f9"
        assert d["title"] == "Handoff draft: Big one"
        assert 'chat_read(chat_id="src"' in body["prompt"] and "2 messages" in body["prompt"]
        on_disk = _read(env, d["id"])
        assert on_disk[DRAFT_FOR_FIELD] == "src" and on_disk["messages"] == []
        assert _read(env, "src") == before


class TestTargetedTools:
    def test_drafter_writes_the_source_draft(self, env):
        from app.mcp.tools.handoff_tools import HandoffReadTool, HandoffWriteTool
        env["write"]("src")
        env["write"]("drf", messages=[], **{DRAFT_FOR_FIELD: "src"})
        env["as"]("drf")
        r = run(HandoffWriteTool().execute(
            target_conversation_id="src", mode="merge",
            sections={"objective": "find the flap", "state": "read 0-40"}))
        assert r.get("ok"), r
        assert r["conversation_id"] == "src"
        assert "find the flap" in _read(env, "src")["handoffDraft"]["document"]
        assert "handoffDraft" not in _read(env, "drf")
        # The source's next turn carries it.
        assert "find the flap" in build_handoff_prelude("src", storage=env["storage"])
        # A second merge from the drafter keeps the earlier section.
        run(HandoffWriteTool().execute(target_conversation_id="src",
                                       sections={"state": "read 0-120"}))
        doc = _read(env, "src")["handoffDraft"]["document"]
        assert "find the flap" in doc and "read 0-120" in doc
        rd = run(HandoffReadTool().execute(target_conversation_id="src"))
        assert rd["conversation_id"] == "src" and rd["draft"]["document"] == doc

    def test_unrelated_conversation_is_refused(self, env):
        from app.mcp.tools.handoff_tools import HandoffReadTool, HandoffWriteTool
        env["write"]("src")
        env["write"]("other")
        env["as"]("other")
        r = run(HandoffWriteTool().execute(target_conversation_id="src",
                                           sections={"objective": "x"}))
        assert r.get("error")
        assert "handoffDraft" not in _read(env, "src")
        assert "handoffDraft" not in _read(env, "other")
        assert run(HandoffReadTool().execute(target_conversation_id="src")).get("error")

    def test_no_target_still_writes_own_draft(self, env):
        from app.mcp.tools.handoff_tools import HandoffWriteTool
        env["write"]("own")
        env["as"]("own")
        r = run(HandoffWriteTool().execute(sections={"objective": "mine"}, mode="replace"))
        assert r.get("ok") and r["conversation_id"] == "own"
        assert "mine" in _read(env, "own")["handoffDraft"]["document"]
