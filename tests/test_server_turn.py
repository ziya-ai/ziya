"""app/utils/server_turn.py: a chat turn the server starts, runs and saves
with no browser (design/conversation-post.md slice 2).

Real records under a temp ZIYA_HOME and the real relay; only the model
stream is replaced (``stream_factory``).  Seams:

  * post (submit, idle target) -> server turn -> the answer is in the
    target's record, with NO subscriber at any point;
  * the body handed to the stream comes from the record: target project's
    root, folded history, the post as the question, diff-apply hold off;
  * a subscriber that comes and goes does not get the turn cancelled
    (control: an ordinary turn is);
  * stop / supersede / error are written as the message's turnStatus;
  * a tool block the assembler writes is one tool_history_rewrite parses;
  * the source turn's context does not leak into the started turn.
"""
import asyncio
import json
import time
from pathlib import Path

import pytest

import app.agents.chat_turn_relay as relay
from app.models.chat import ChatCreate
from app.storage.chats import ChatStorage
from app.utils import chat_post as cp
from app.utils import server_turn as st
from app.utils.paths import get_project_dir

A, B = "proj-a", "proj-b"
ROOT = Path(__file__).resolve().parents[1]


def sse(payload) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def text(s):
    return sse({"content": s})


DONE = sse({"done": True})


def resolve(pin):
    return pin, {"X-Ziya-Model": "test-model"}


def _seed_project(home, pid):
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    now = int(time.time() * 1000)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": pid, "path": f"/work/{pid}",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": now, "lastAccessedAt": now}))
    return pdir


def _disk(pid, cid):
    return json.loads((get_project_dir(pid) / "chats" / f"{cid}.json").read_text())


def _write(pid, cid, raw):
    s = ChatStorage(get_project_dir(pid))
    s._write_json(s._chat_file(cid), raw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / ".ziya"
    home.mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(home))
    from app.storage import chat_index
    chat_index.invalidate()
    cp._reset_for_tests()
    relay._reset_for_tests()
    a_dir, b_dir = _seed_project(home, A), _seed_project(home, B)
    src = ChatStorage(a_dir).create(ChatCreate(title="Source"))
    dst = ChatStorage(b_dir).create(ChatCreate(title="Target"))
    raw = _disk(B, dst.id)
    raw["messages"] = [
        {"id": "h1", "role": "human", "content": "first q", "timestamp": 1},
        {"id": "a1", "role": "assistant", "content": "first a", "timestamp": 2},
        {"id": "hm", "role": "human", "content": "muted q", "timestamp": 3, "muted": True},
    ]
    _write(B, dst.id, raw)
    yield src.id, dst.id
    relay._reset_for_tests()
    cp._reset_for_tests()


def factory(frames, seen, gate=None, gate_after=None):
    def make(body):
        seen["body"] = body

        async def gen():
            from app.context import get_conversation_id_or_none
            seen["ctx_conversation"] = get_conversation_id_or_none()
            for i, f in enumerate(frames):
                if gate is not None and i == gate_after:
                    await gate.wait()
                yield f
                await asyncio.sleep(0)
        return gen()
    return make


async def wait_done(cid, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = relay.status(cid)
        if s is not None and not s["active"]:
            return s
        await asyncio.sleep(0.005)
    raise AssertionError("turn did not finish")


async def wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


async def submit(src, dst, frames, seen, **fkw):
    return await cp.post_to_chat(
        src, dst, "please look at X", "submit", active_turn_id=lambda c: None,
        start_turn=lambda cid, msg: st.start_server_turn(
            cid, {"kind": "peer_post", "messageId": msg["id"]},
            stream_factory=factory(frames, seen, **fkw), resolve_model=resolve))


def _answer(dst):
    return [m for m in _disk(B, dst)["messages"] if m.get("serverTurn")]


# ── the whole path ────────────────────────────────────────────────────────

async def test_submit_to_an_idle_target_runs_and_saves_with_no_subscriber(env):
    src, dst = env
    from app.context import get_conversation_id_or_none, set_conversation_id
    set_conversation_id(src)
    seen = {}
    out = await submit(src, dst, [text("Looked at X. "), text("It is fine."), DONE], seen)
    assert out["delivery"] == "turn_started" and out["turnId"]
    status = await wait_done(dst)
    assert status["server_authored"] is True and status["subscribers"] == 0
    assert status["turn_id"] == out["turnId"] and status["end_reason"] == "completed"

    msgs = _disk(B, dst)["messages"]
    assert [m["role"] for m in msgs] == ["human", "assistant", "human", "peer", "assistant"]
    ans = msgs[-1]
    assert ans["content"] == "Looked at X. It is fine."
    assert ans["turnStatus"] == "complete" and ans["_serverAuthored"] is True
    assert ans["serverTurn"] == {"turnId": out["turnId"], "kind": "peer_post",
                                 "messageId": out["messageId"]}
    assert _disk(B, dst)["hasUnreadResponse"] is True

    body = seen["body"]
    assert body["conversation_id"] == dst
    assert body["config"]["project_root"] == f"/work/{B}"          # the TARGET's project
    assert body["config"]["hold_for_diff_apply"] is False
    assert body["question"].startswith(cp.POST_PREFIX) and body["question"].endswith("please look at X")
    assert [(h["type"], h["content"]) for h in body["chat_history"]] == [
        ("human", "first q"), ("ai", "first a")]                    # muted message left out
    # The started turn does not run in the source turn's context.
    assert get_conversation_id_or_none() == src                     # control: it was set here
    assert seen["ctx_conversation"] is None
    assert cp.is_peer_driven(dst, lambda c: out["turnId"])


async def test_default_starter_is_the_server_turn_and_a_failure_is_reported(env, monkeypatch):
    src, dst = env
    calls = []

    async def fake(cid, trigger=None, **kw):
        calls.append((cid, trigger))
        return "turn-x"
    monkeypatch.setattr(st, "start_server_turn", fake)
    out = await cp.post_to_chat(src, dst, "one", "submit", active_turn_id=lambda c: None)
    assert out["delivery"] == "turn_started" and out["turnId"] == "turn-x"
    assert calls == [(dst, {"kind": "peer_post", "messageId": out["messageId"]})]

    async def broken(cid, trigger=None, **kw):
        raise st.ServerTurnError("No streaming handler for model 'm'.")
    monkeypatch.setattr(st, "start_server_turn", broken)
    out = await cp.post_to_chat(src, dst, "two", "submit", active_turn_id=lambda c: None)
    assert out["success"] and out["delivery"] == "appended" and out["turnStarted"] is False
    assert "No streaming handler" in out["message"]
    assert _disk(B, dst)["messages"][-1]["peerPost"]["text"] == "two"   # the post was kept


# ── no viewer needed ──────────────────────────────────────────────────────

async def test_a_subscriber_leaving_does_not_cancel_a_server_turn(env, monkeypatch):
    src, dst = env
    monkeypatch.setattr(relay, "_grace_seconds", lambda: 0.02)

    async def come_and_go(cid):
        agen = relay.subscribe(cid)
        await agen.__anext__()
        await agen.aclose()

    # Control: an ordinary turn left without a viewer IS cancelled.
    gate0 = asyncio.Event()

    async def plain():
        yield text("a")
        await gate0.wait()
        yield text("b")
    await relay.start_turn("plain", plain())
    await come_and_go("plain")
    assert (await wait_done("plain"))["end_reason"] == "unwatched"

    gate = asyncio.Event()
    await submit(src, dst, [text("a"), text("b"), DONE], {}, gate=gate, gate_after=1)
    await come_and_go(dst)
    await asyncio.sleep(0.15)
    assert relay.status(dst)["active"] is True
    gate.set()
    assert (await wait_done(dst))["end_reason"] == "completed"
    assert _answer(dst)[0]["content"] == "ab"


# ── how it ended ──────────────────────────────────────────────────────────

async def test_stop_and_supersede_save_the_partial_with_its_status(env):
    src, dst = env
    gate = asyncio.Event()
    await submit(src, dst, [text("partial "), text("never"), DONE], {}, gate=gate, gate_after=1)
    await wait_for(lambda: relay.status(dst)["buffered_frames"] >= 1)
    assert await relay.cancel_turn(dst) is True
    await wait_done(dst)
    (ans,) = _answer(dst)
    assert ans["content"] == "partial " and ans["turnStatus"] == "stopped"

    gate2 = asyncio.Event()
    await submit(src, dst, [text("second "), text("never"), DONE], {}, gate=gate2, gate_after=1)
    first_turn = relay.status(dst)["turn_id"]
    await wait_for(lambda: relay.status(dst)["buffered_frames"] >= 1)

    async def browser_turn():
        yield text("from the browser")
    await relay.start_turn(dst, browser_turn())                 # the user sent a message
    # Not just "a second message exists": the mid-turn checkpoint also
    # writes one, with turnStatus 'streaming'.
    await wait_for(lambda: len(_answer(dst)) == 2 and _answer(dst)[-1]["turnStatus"] != "streaming")
    second = _answer(dst)[-1]
    assert second["content"] == "second " and second["turnStatus"] == "superseded"
    assert second["serverTurn"]["turnId"] == first_turn
    await wait_done(dst)
    assert len(_answer(dst)) == 2                               # the browser's turn is not saved here


async def test_an_error_frame_is_saved_as_an_error_turn(env):
    src, dst = env
    await submit(src, dst, [sse({"error": "throttled", "error_type": "x"}), DONE], {})
    await wait_done(dst)
    (ans,) = _answer(dst)
    assert ans["turnStatus"] == "error" and ans["turnError"] == "throttled"
    assert "throttled" in ans["content"]


async def test_a_turn_that_says_nothing_saves_nothing(env):
    src, dst = env
    await submit(src, dst, [sse({"heartbeat": True, "type": "heartbeat"}), DONE], {})
    await wait_done(dst)
    assert _answer(dst) == []
    assert _disk(B, dst)["messages"][-1]["role"] == "peer"       # control: the post is there


async def test_a_checkpoint_is_written_mid_turn_and_a_deleted_one_stays_deleted(env):
    src, dst = env
    gate = asyncio.Event()
    frames = [text("working"),
              sse({"tool_start": {"tool_id": "t1", "tool_name": "mcp_file_read",
                                  "display_header": "file read: a.py", "args": {"path": "a.py"}}}),
              sse({"tool_result": {"type": "tool_display", "tool_id": "t1",
                                   "tool_name": "mcp_file_read", "result": "x = 1"}}),
              text("after"), DONE]
    await submit(src, dst, frames, {}, gate=gate, gate_after=3)
    # The tool result is saved even though the first text write may still
    # have been in flight when it arrived (a dropped checkpoint here left a
    # turn parked in its next tool call showing only "working").
    partial = await wait_for(lambda: [m for m in _answer(dst) if "x = 1" in m["content"]])
    assert partial[0]["turnStatus"] == "streaming" and partial[0]["content"].startswith("working")
    # The user deletes the partial message while the turn is still running.
    raw = _disk(B, dst)
    raw["messages"] = [m for m in raw["messages"] if not m.get("serverTurn")]
    _write(B, dst, raw)
    gate.set()
    await wait_done(dst)
    assert _answer(dst) == []


# ── the assembler ─────────────────────────────────────────────────────────

def _tool(tid, name, result, **start):
    return [sse({"tool_start": {"tool_id": tid, "tool_name": name, **start}}),
            sse({"tool_result": {"type": "tool_display", "tool_id": tid, "tool_name": name,
                                 "result": result, "verified": True}})]


def test_assembler_tool_block_is_one_the_history_rewrite_reads():
    from app.utils.tool_history_rewrite import _FENCE_RE, rewrite_assistant_text
    asm = st.TurnAssembler()
    for f in [text("Checking.")] + _tool("t1", "run_shell_command", "a.py\nb.py",
                                         args={"command": "ls"}, display_header="x") + [text("Two files.")]:
        asm.feed(f)
    m = _FENCE_RE.search(asm.content)
    assert m and m.group(2) == "mcp_run_shell_command"
    assert m.group(3) == "🔐 Shell: ls" and m.group(5) == "$ ls\na.py\nb.py"
    assert asm.content.startswith("Checking.\n\n````tool:") and asm.content.endswith("````\n\nTwo files.")
    replayed = rewrite_assistant_text(asm.content)
    assert "````tool:" not in replayed and "a.py\nb.py" in replayed and "tool_result" in replayed


def test_assembler_fence_outgrows_backticks_in_the_body_and_caps_the_body():
    asm = st.TurnAssembler()
    for f in _tool("t1", "file_read", "````\ninner\n````"):
        asm.feed(f)
    assert "`````tool:mcp_file_read|🔐 File Read|text\n````\ninner\n````\n`````" in asm.content
    asm = st.TurnAssembler()
    for f in _tool("t2", "file_read", "y" * (st.TOOL_BODY_MAX_CHARS + 50)):
        asm.feed(f)
    assert "(50 more characters not saved)" in asm.content
    assert asm.content.count("y") == st.TOOL_BODY_MAX_CHARS


def test_assembler_rewinds_and_ignores_what_is_not_content():
    asm = st.TurnAssembler()
    asm.feed(text("l0\nl1\nl2 part"))
    assert asm.feed(sse({"type": "continuation_rewind", "rewind_line": 2})) == "rewind"
    assert asm.content == "l0\nl1"
    assert asm.feed(sse({"type": "continuation_rewind", "rewind_line": 99})) is None
    asm.feed(text("\nl2 whole<!-- REWIND_MARKER: 7 -->\n"))
    assert asm.content == "l0\nl1\nl2 whole"                    # marker comments never saved
    assert asm.feed(sse({"type": "rewind", "to_marker": "nope", "content": "x"})) is None
    for junk in ("", "nonsense", sse({"heartbeat": True, "type": "heartbeat"}), DONE,
                 sse({"type": "processing_state", "content": "not text"}),
                 sse({"type": "tool_group_end"})):
        assert asm.feed(junk) is None
    assert asm.content == "l0\nl1\nl2 whole"
    # An internal tool (beads) leaves no block.
    for f in _tool("b1", "bead_create", "{}", is_internal=True):
        asm.feed(f)
    assert "bead_create" not in asm.content


# ── the body ──────────────────────────────────────────────────────────────

def test_history_folds_notes_and_posts_and_needs_something_to_answer():
    note = "[Ziya: this conversation was moved from project “a” to project “b” at t.]"
    post = cp.POST_PREFIX + "“S” (ziya://chat/s) at t.]\n\ndo it"
    raw = {"id": "c", "messages": [
        {"role": "human", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "system", "content": note},
        {"role": "system", "content": "local notice", "muted": True},
        {"role": "peer", "content": post, "timestamp": 9},
    ]}
    q, hist = st.history_from_record(raw)
    assert q == post
    assert hist == [{"type": "human", "content": "q1", "images": None, "_timestamp": None},
                    {"type": "ai", "content": "a1\n\n" + note, "images": None, "_timestamp": None}]
    # A post after an unanswered human message joins it: one question, not two human turns.
    raw["messages"] = [{"role": "human", "content": "q1"}, {"role": "peer", "content": post}]
    q, hist = st.history_from_record(raw)
    assert q == "q1\n\n" + post and hist == []
    for msgs in ([], [{"role": "human", "content": "q"}, {"role": "assistant", "content": "a"}],
                 [{"role": "human", "content": "q", "muted": True}]):
        with pytest.raises(st.ServerTurnError):
            st.history_from_record({"id": "c", "messages": msgs})


def test_body_carries_the_pin_and_the_record_prompt():
    raw = {"id": "c", "additionalPrompt": "be brief",
           "messages": [{"role": "human", "content": "q"}]}
    body = st.body_from_record(raw, "/work/p", "opus4.5")
    assert body["config"]["modelSelection"] == {"model": "opus4.5"}
    assert body["config"]["systemPromptAddition"] == "be brief"
    assert body["files"] == [] and body["config"]["files"] == []
    assert st.body_from_record(raw, "/work/p")["config"]["modelSelection"] == {}

    class _P:
        class settings:
            modelPreference = "proj-model"
    assert st.model_pin_for({"modelPreference": "conv-model"}, _P, "p") == "conv-model"
    assert st.model_pin_for({}, _P, "p") == "proj-model"
    assert st.model_pin_for({}, None, "p") is None


# ── seams in code this module depends on ──────────────────────────────────

def test_stream_chunks_takes_the_diff_hold_from_the_body():
    src = (ROOT / "app" / "server.py").read_text()
    start = src.index("async def stream_chunks(body):")
    body = src[start:src.index("yield f\"data: {json.dumps({'done': True})}", start)]
    assert "hold_for_diff_apply=True" not in body
    assert 'hold_for_diff_apply=body.get("config", {}).get("hold_for_diff_apply", True)' in body


def test_tool_can_be_put_behind_the_consent_gate_by_configuration():
    """mode is the consent op, so permissions.json can set submit to ask."""
    from app.mcp.tools.chat_post_tool import ChatPostTool
    from app.utils.consent_gate import declared_mode
    tool = ChatPostTool()
    assert getattr(tool, "consent_op_key", None) == "mode"
    assert declared_mode(tool, "submit") == "off"                 # ungated by default
    assert cp.submit_policy("a", "b") is None
