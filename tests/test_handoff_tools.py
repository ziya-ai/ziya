"""
handoff_read / handoff_write tools (design/conversation-handoff.md, step 3).

Seam under test: a handoff_write call changes what the NEXT turn's prelude
says — the draft is persisted on the current conversation's record, the
record's _version bumps (so the frontend's sync pulls it), and
build_handoff_prelude on the same record now carries the draft and the
per-turn merge instruction instead of a pressure nudge.

Also: the bulk-sync guard in app/api/chats.py must not wipe a tool-written
draft when the frontend round-trips a record that never mentioned it, but
MUST honour an explicit clear.

Runs against a real ChatStorage under a tmp ziya home with the request
ContextVars set, exactly like tests/test_chat_history_tools.py.
"""
import asyncio
import json
import os
import time

import pytest

from app.utils import handoff_prelude as hp
from app.utils.handoff_prelude import build_handoff_prelude, record_context_pressure

NOW_MS = int(time.time() * 1000)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _clean_pressure():
    hp.clear_pressure_cache()
    yield
    hp.clear_pressure_cache()


@pytest.fixture
def env(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    ziya_home = tmp_path / "ziya_home"
    projects_dir = ziya_home / "projects"
    pid = "p_h_" + os.urandom(3).hex()
    (projects_dir / pid / "chats").mkdir(parents=True)
    (projects_dir / pid / "project.json").write_text(json.dumps({
        "id": pid, "name": "H", "path": str(project_root.resolve()),
        "createdAt": NOW_MS, "lastAccessedAt": NOW_MS,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    (projects_dir / "_path_index.json").write_text(
        json.dumps({str(project_root.resolve()): pid}))

    cid = "c_src_" + os.urandom(3).hex()
    rec = {"id": cid, "title": "ISL BFD flap", "groupId": None, "contextIds": [],
           "skillIds": [], "additionalFiles": [], "additionalPrompt": None,
           "messages": [{"id": "m1", "role": "human", "content": "start", "timestamp": NOW_MS},
                        {"id": "m2", "role": "assistant", "content": "ok", "timestamp": NOW_MS}],
           "createdAt": NOW_MS - 1000, "lastActiveAt": NOW_MS - 1000, "_version": NOW_MS - 1000}
    (projects_dir / pid / "chats" / f"{cid}.json").write_text(json.dumps(rec))

    monkeypatch.setattr("app.utils.paths.get_ziya_home", lambda: ziya_home)
    from app.storage import chat_index
    chat_index.invalidate()

    from app import context as _ctx
    t1 = _ctx._request_conversation_id.set(cid)
    t2 = _ctx._request_project_root.set(str(project_root.resolve()))

    from app.storage.chats import ChatStorage
    from app.utils.paths import get_project_dir
    storage = ChatStorage(get_project_dir(pid))
    yield {"cid": cid, "pid": pid, "storage": storage, "ziya_home": ziya_home}

    _ctx._request_project_root.reset(t2)
    _ctx._request_conversation_id.reset(t1)


def _record(env):
    return env["storage"]._read_json(env["storage"]._chat_file(env["cid"]))


class TestHandoffWrite:
    def test_create_then_merge_changes_next_turn_prelude(self, env):
        from app.mcp.tools.handoff_tools import HandoffWriteTool, HandoffReadTool
        storage, cid = env["storage"], env["cid"]

        # Before: under pressure, no draft → nudge.
        record_context_pressure(cid, 160_000, 200_000)
        assert "[Context pressure]" in build_handoff_prelude(cid, storage=storage)

        before_version = _record(env)["_version"]
        r = run(HandoffWriteTool().execute(
            sections={"objective": "Find the BFD flap root cause",
                      "decisions": "ruled out Guardian BPF (turn 18)"},
            mode="replace"))
        assert r.get("ok") and r["action"] == "created"
        rec = _record(env)
        assert rec["handoffDraft"]["document"].startswith("## Objective")
        assert rec["handoffDraft"]["sourceMessageCount"] == 2
        assert rec["_version"] > before_version

        # After: same pressure, draft present → merge instruction, no nudge.
        pre = build_handoff_prelude(cid, storage=storage)
        assert "[Handoff draft — living]" in pre
        assert "ruled out Guardian BPF" in pre
        assert "[Context pressure]" not in pre

        # Merge touches one section, keeps the other.
        r2 = run(HandoffWriteTool().execute(
            sections={"decisions": "ruled out BPF; suspect missing Tunnel Start (turns 41-44)"}))
        assert r2["action"] == "updated" and r2["mode"] == "merge"
        pre2 = build_handoff_prelude(cid, storage=storage)
        assert "Find the BFD flap root cause" in pre2
        assert "Tunnel Start" in pre2
        assert "ruled out Guardian BPF (turn 18)" not in pre2

        # handoff_read sees the same record and the pressure number.
        rd = run(HandoffReadTool().execute())
        assert rd["draft"]["document"] == _record(env)["handoffDraft"]["document"]
        assert rd["context_pressure"] == pytest.approx(0.8)
        assert rd["inherited_handoff"] is None

    def test_rejects_unknown_section_and_empty_input(self, env):
        from app.mcp.tools.handoff_tools import HandoffWriteTool
        assert run(HandoffWriteTool().execute(sections={"vibes": "x"}))["error"]
        assert run(HandoffWriteTool().execute())["error"]
        assert run(HandoffWriteTool().execute(document="   "))["error"]
        assert "handoffDraft" not in _record(env)

    def test_bad_mode_is_rejected_without_writing(self, env):
        from app.mcp.tools.handoff_tools import HandoffWriteTool
        r = run(HandoffWriteTool().execute(document="x", mode="upsert"))
        assert r["error"]
        assert "handoffDraft" not in _record(env)

    def test_read_on_continuation_reports_inherited_and_predecessor(self, env):
        from app.mcp.tools.handoff_tools import HandoffReadTool
        rec = _record(env)
        rec.update({"branchedFrom": "c_prev", "lineageKind": "handoff",
                    "handoff": {"document": "Objective: continue", "generatedAt": 1,
                                "sourceMessageCount": 9}})
        env["storage"]._write_json(env["storage"]._chat_file(env["cid"]), rec)
        rd = run(HandoffReadTool().execute())
        assert rd["inherited_handoff"]["document"] == "Objective: continue"
        assert rd["predecessor_id"] == "c_prev"
        assert rd["draft"] is None


class TestBulkSyncPreservesDraft:
    """handoffDraft / handoff / handedOffTo are SERVER-OWNED on the sync path,
    like _beads: the model (handoff_write) and the handoff endpoints write
    them; the frontend's copy is a read-only mirror that goes stale the
    moment the model merges.  Whatever the frontend sends for them in a
    bulk-sync — absent, null, or a stale value — the on-disk value stays.
    User edits and clears go through the PATCH endpoints, never through
    bulk-sync."""

    @pytest.fixture
    def client(self, env):
        # Same harness as tests/test_api_chats.py: a bare router app with the
        # path helpers patched at the router module.
        from unittest.mock import patch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.chats import router
        project_dir = env["ziya_home"] / "projects" / env["pid"]
        with patch("app.api.chats.get_ziya_home", return_value=env["ziya_home"]), \
             patch("app.api.chats.get_project_dir", return_value=project_dir):
            app = FastAPI()
            app.include_router(router)
            yield TestClient(app)

    def _sync(self, client, env, payload_over):
        rec = _record(env)
        payload = {k: v for k, v in rec.items() if k not in ("handoffDraft",)}
        payload["_version"] = rec["_version"] + 10
        payload["lastActiveAt"] = payload["_version"]
        payload["projectId"] = env["pid"]
        payload.update(payload_over)
        r = client.post(f"/api/v1/projects/{env['pid']}/chats/bulk-sync",
                        json={"chats": [payload]})
        assert r.status_code == 200, r.text
        return _record(env)

    def test_omitted_field_is_carried_forward(self, client, env):
        from app.mcp.tools.handoff_tools import HandoffWriteTool
        run(HandoffWriteTool().execute(document="## Objective\nkeep me", mode="replace"))
        after = self._sync(client, env, {})
        assert after["handoffDraft"]["document"] == "## Objective\nkeep me"

    def test_stale_present_value_does_not_overwrite_model_write(self, client, env):
        """The real-world sequence: the frontend full-fetched the record (so
        its IDB copy carries draft v1), the model merged v2 mid-turn, then
        the end-of-turn bulk-sync arrives with a NEWER _version and the
        stale v1 present in the payload.  v2 must survive."""
        from app.mcp.tools.handoff_tools import HandoffWriteTool
        run(HandoffWriteTool().execute(document="## Objective\nv1", mode="replace"))
        stale = dict(_record(env)["handoffDraft"])
        run(HandoffWriteTool().execute(sections={"decisions": "D2"}, mode="merge"))
        assert "D2" in _record(env)["handoffDraft"]["document"]
        after = self._sync(client, env, {"handoffDraft": stale})
        assert "D2" in after["handoffDraft"]["document"]

    def test_explicit_null_does_not_clear(self, client, env):
        """Clearing is the PATCH …/handoff/draft with an empty document, not a
        null through bulk-sync — a null here is indistinguishable from a
        client that simply has no copy."""
        from app.mcp.tools.handoff_tools import HandoffWriteTool
        run(HandoffWriteTool().execute(document="## Objective\nstay", mode="replace"))
        after = self._sync(client, env, {"handoffDraft": None})
        assert after["handoffDraft"]["document"] == "## Objective\nstay"

    def test_handed_off_to_survives_stale_sync(self, client, env):
        rec = _record(env)
        rec["handedOffTo"] = "child-1"
        env["storage"]._write_json(env["storage"]._chat_file(rec["id"]), rec)
        after = self._sync(client, env, {"handedOffTo": None})
        assert after["handedOffTo"] == "child-1"
