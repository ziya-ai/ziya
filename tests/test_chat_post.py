"""app/utils/chat_post.py: one conversation posts a message into another
(design/conversation-post.md).

Against real records under a temp ZIYA_HOME.  Seams:
  * the post reaches the target's record on disk, cross-project, and the
    source record is not written;
  * a later browser bulk-sync (stale copy, or a copy with altered content)
    neither removes nor rewrites it;
  * ``submit`` into a running turn goes through the feedback channel with
    the same text the record holds, and marks the turn for the depth guard;
  * request history: a ``peer`` entry survives as human-role text
    (the /api/chat role filter would otherwise drop it).
"""
import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.chat import ChatCreate
from app.storage.chats import ChatStorage
from app.utils import chat_post as cp
from app.utils.paths import get_project_dir

A, B = "proj-a", "proj-b"


def run(coro):
    return asyncio.run(coro)


def _seed_project(home, pid):
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    now = int(time.time() * 1000)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": pid, "path": f"/tmp/{pid}",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": now, "lastAccessedAt": now}))
    return pdir


def _disk(pid, cid):
    return json.loads((get_project_dir(pid) / "chats" / f"{cid}.json").read_text())


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / ".ziya"
    home.mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(home))
    from app.storage import chat_index
    chat_index.invalidate()
    cp._reset_for_tests()
    a_dir, b_dir = _seed_project(home, A), _seed_project(home, B)
    src = ChatStorage(a_dir).create(ChatCreate(title="Source Thread"))
    dst = ChatStorage(b_dir).create(ChatCreate(title="Target Thread"))
    for pid, cid, msgs in ((A, src.id, [("human", "q-src"), ("assistant", "a-src")]),
                           (B, dst.id, [("human", "q-dst"), ("assistant", "a-dst")])):
        st = ChatStorage(get_project_dir(pid))
        raw = _disk(pid, cid)
        raw["messages"] = [{"id": f"{cid[:4]}-{i}", "role": r, "content": c, "timestamp": 1000 + i}
                           for i, (r, c) in enumerate(msgs)]
        st._write_json(st._chat_file(cid), raw)
    yield src.id, dst.id
    cp._reset_for_tests()


def _post(src, dst, text="carry this over", mode="context", **kw):
    kw.setdefault("deliver", lambda *a: pytest.fail("deliver called"))
    kw.setdefault("active_turn_id", lambda cid: None)
    kw.setdefault("start_turn", None)          # the real starter: tests/test_server_turn.py
    return run(cp.post_to_chat(src, dst, text, mode, **kw))


def test_context_post_lands_in_the_target_record_across_projects(env):
    src, dst = env
    before_src = _disk(A, src)
    before = _disk(B, dst)
    out = _post(src, dst)
    assert out["success"] and out["delivery"] == "appended" and out["mode"] == "context"

    raw = _disk(B, dst)
    assert len(raw["messages"]) == 3 and raw["messages"][:2] == before["messages"]
    m = raw["messages"][-1]
    assert m["id"] == out["messageId"] and m["role"] == "peer"
    assert m["content"].startswith(cp.POST_PREFIX) and m["content"].endswith("\n\ncarry this over")
    assert "Source Thread" in m["content"] and f"ziya://chat/{src}/1" in m["content"]
    assert m["peerPost"] == {
        "fromChatId": src, "fromTitle": "Source Thread", "fromProjectId": A,
        "fromMessageIndex": 1, "link": f"ziya://chat/{src}/1",
        "mode": "context", "text": "carry this over"}
    assert m["_serverAuthored"] is True
    assert raw["_version"] > before["_version"] and raw["lastActiveAt"] >= before["lastActiveAt"]
    assert raw["hasUnreadResponse"] is True
    assert out["target"] == {"conversationId": dst, "projectId": B, "title": "Target Thread",
                             "messageIndex": 2, "_version": raw["_version"]}
    assert _disk(A, src) == before_src          # the source is not written


def test_refusals_write_nothing(env):
    src, dst = env
    before = _disk(B, dst)
    for args, status in (((src, src), 400), ((src, "nope"), 404), (("", dst), 400)):
        with pytest.raises(cp.ChatPostError) as e:
            _post(*args)
        assert e.value.status == status
    for text in ("", "   ", "x" * (cp.MAX_POST_CHARS + 1)):
        with pytest.raises(cp.ChatPostError):
            _post(src, dst, text=text)
    with pytest.raises(cp.ChatPostError):
        _post(src, dst, mode="shout")
    assert _disk(B, dst) == before
    assert _post(src, dst)["success"]            # positive control: same args do post


def test_submit_into_a_running_turn_is_delivered_as_feedback_with_the_record_text(env):
    src, dst = env
    sent = []
    out = _post(src, dst, mode="submit", deliver=lambda cid, item: sent.append((cid, item)),
                active_turn_id=lambda cid: "turn-9" if cid == dst else None)
    m = _disk(B, dst)["messages"][-1]
    assert out["delivery"] == "live_feedback" and out["turnId"] == "turn-9"
    assert sent == [(dst, {"type": "tool_feedback", "message": m["content"], "feedback_id": m["id"]})]
    assert m["peerPost"]["mode"] == "submit"
    # A post whose whole text is a stop word must not read as a stop directive.
    from app.utils.feedback_directives import is_stop_directive
    assert is_stop_directive("stop") is True
    assert is_stop_directive(cp.build_post_message("stop", {"chatId": src}, "submit")["content"]) is False


def test_submit_to_an_idle_target_without_a_starter_says_no_turn_started(env):
    src, dst = env
    out = _post(src, dst, mode="submit")
    assert out["delivery"] == "appended" and out["turnStarted"] is False
    assert "no turn was started" in out["message"].lower()
    assert _disk(B, dst)["messages"][-1]["role"] == "peer"


def test_submit_to_an_idle_target_uses_the_starter_when_given(env):
    src, dst = env
    calls = []

    async def starter(cid, message):
        calls.append((cid, message["id"]))
        return "turn-new"
    out = _post(src, dst, mode="submit", start_turn=starter)
    assert out["delivery"] == "turn_started" and out["turnId"] == "turn-new"
    assert calls == [(dst, out["messageId"])]
    assert cp.is_peer_driven(dst, lambda cid: "turn-new")


def test_depth_guard_a_peer_driven_turn_cannot_submit_onward(env):
    src, dst = env
    live = {dst: "turn-9"}
    _post(src, dst, mode="submit", deliver=lambda *a: None, active_turn_id=live.get)
    n_src = len(_disk(A, src)["messages"])
    # dst's turn, driven by the post, tries to submit back.
    with pytest.raises(cp.ChatPostError) as e:
        _post(dst, src, mode="submit", active_turn_id=live.get)
    assert e.value.status == 409 and len(_disk(A, src)["messages"]) == n_src
    # Context is still allowed from that turn.
    assert _post(dst, src, mode="context", active_turn_id=live.get)["success"]
    # Its next turn is not peer-driven.
    live[dst] = "turn-10"
    assert _post(dst, src, mode="submit", active_turn_id=live.get)["success"]
    assert cp.is_peer_driven(dst, live.get) is False


def test_bulk_sync_neither_removes_nor_rewrites_the_post(env):
    src, dst = env
    out = _post(src, dst)
    posted = _disk(B, dst)["messages"][-1]
    from app.api.chats import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    def push(messages, version):
        base = _disk(B, dst)
        body = {**base, "messages": messages, "_version": version, "lastActiveAt": version}
        r = client.post(f"/api/v1/projects/{B}/chats/bulk-sync", json={"chats": [body]})
        assert r.status_code == 200 and not r.json()["errors"], r.text

    stale = _disk(B, dst)["messages"][:2] + [
        {"id": "h3", "role": "human", "content": "next q", "timestamp": 5000}]
    push(stale, int(time.time() * 1000) + 10_000)            # newer copy without the post
    msgs = _disk(B, dst)["messages"]
    assert [m["id"] for m in msgs if m["id"] == out["messageId"]] == [out["messageId"]]
    assert any(m["id"] == "h3" for m in msgs)                 # the push itself was applied

    tampered = [dict(m, content="rewritten", peerPost=None) if m["id"] == posted["id"] else m
                for m in msgs]
    push(tampered, int(time.time() * 1000) + 20_000)
    kept = next(m for m in _disk(B, dst)["messages"] if m["id"] == posted["id"])
    assert kept["content"] == posted["content"] and kept["peerPost"] == posted["peerPost"]


# ── request history ───────────────────────────────────────────────────────

P1 = cp.POST_PREFIX + "\u201cS\u201d (ziya://chat/s) at t.]\n\none"
P2 = cp.POST_PREFIX + "\u201cS\u201d (ziya://chat/s) at t.]\n\ntwo"


def test_fold_after_a_human_entry_appends_to_it():
    out = cp.fold_peer_posts([["human", "q", "", "5"], ["peer", P1, "", "6"], ["assistant", "a"]])
    assert out == [["human", "q\n\n" + P1, "", "5"], ["assistant", "a"]]


def test_fold_before_a_human_entry_prepends_to_it():
    out = cp.fold_peer_posts([["assistant", "a"], ["peer", P1], ["peer", P2], ["human", "q"]])
    assert out == [["assistant", "a"], ["human", P1 + "\n\n" + P2 + "\n\nq"]]


def test_fold_between_assistant_entries_or_at_the_end_stands_as_a_human_turn():
    out = cp.fold_peer_posts([["assistant", "a"], ["peer", P1, "", "7"], ["assistant", "b"],
                              {"role": "peer", "content": P2, "peerPost": {"x": 1}}])
    assert out == [["assistant", "a"], ["human", P1, "", "7"], ["assistant", "b"],
                   {"role": "human", "content": P2, "peerPost": {"x": 1}}]


def test_fold_leaves_other_history_alone_and_does_not_mutate_input():
    msgs = [["human", "q"], ["system", "note"], ["assistant", "a"], ["peer", P1]]
    snapshot = json.loads(json.dumps(msgs))
    out = cp.fold_peer_posts(msgs)
    assert msgs == snapshot and out[:3] == snapshot[:3]
    assert cp.fold_peer_posts("x") == "x"
    # Every surviving entry has a role the /api/chat filter keeps or already dropped.
    assert all(m[0] != "peer" for m in out)


def test_server_history_builder_calls_the_fold():
    """The seam: without this call a peer post never reaches the model."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "server.py").read_text()
    at = src.index("fold_project_change_notes(messages_to_process)")
    assert "fold_peer_posts(messages_to_process)" in src[at:at + 600]
