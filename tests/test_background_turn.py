"""
Background turns (app/utils/background_turn.py): the handoff drawer's "Ask
the model to draft it" runs while the conversation's own turn is held
waiting for its diffs to be applied.  It must not cancel that turn, move its
diff-apply bookkeeping, or take feedback typed for it.

Three layers, each at its seam:

  * the helper's own contract;
  * the real ``stream_with_tools`` loop: a turn on the background feedback
    key leaves the conversation's staged feedback and apply ledger alone;
  * the real ``chat_endpoint``: a background request does not go through
    ``chat_turn_relay`` (which would supersede the running turn), and
    ``stream_chunks`` is told.
"""

import asyncio
import inspect
import os
from typing import List
from unittest.mock import patch

import pytest

os.environ.setdefault("ZIYA_ENABLE_MCP", "false")

import app.server as server  # noqa: E402
import app.agents.chat_turn_relay as relay  # noqa: E402
from app.utils import apply_ledger as al  # noqa: E402
from app.utils import background_turn as bt  # noqa: E402
from tests.test_await_diff_apply import (  # noqa: E402
    CONV, RESPONSE, _final, _diffs_then_end, _ok, _run, _until_waiting, _user_texts, _of,
)
from tests.test_chat_turn_endpoints import _drain, _request, parse_frames, sse  # noqa: E402

BG_KEY = bt.feedback_key(CONV)


@pytest.fixture(autouse=True)
def _clean():
    def wipe():
        al.reset_for_tests()
        relay._reset_for_tests()
        for store in (server.pending_feedback_lists, server.pending_feedback_refcounts,
                      server.feedback_queues, server.last_generator_exit):
            for k in [k for k in store if str(k).startswith(CONV) or str(k).startswith("conv-")]:
                store.pop(k, None)
    wipe()
    yield
    wipe()


# ── helper ────────────────────────────────────────────────────────────────

def test_only_a_literal_true_is_a_background_request():
    assert bt.is_background({"background": True})
    for body in ({}, {"background": "true"}, {"background": 1}, {"background": False}, None, []):
        assert not bt.is_background(body)


def test_config_turns_the_hold_off_and_is_empty_for_an_ordinary_turn():
    assert bt.config_for({"background": True}) == {"background": True, "hold_for_diff_apply": False}
    assert bt.config_for({"question": "q"}) == {}
    assert bt.in_config({"config": bt.config_for({"background": True})})
    assert not bt.in_config({"config": {}}) and not bt.in_config({"background": True})


def test_feedback_key_is_not_the_conversations():
    assert BG_KEY and BG_KEY != CONV and CONV in BG_KEY
    assert bt.feedback_key(None) is None and bt.feedback_key("") is None


def test_release_drops_the_background_stores_but_not_under_a_bound_generator():
    server.get_feedback_queue(BG_KEY)
    server.get_feedback_queue(CONV)
    server.last_generator_exit[BG_KEY] = 1.0
    server.pending_feedback_refcounts[BG_KEY] = 1
    bt.release(CONV)
    assert BG_KEY in server.feedback_queues                      # still bound
    server.pending_feedback_refcounts.pop(BG_KEY)
    bt.release(CONV)
    assert BG_KEY not in server.feedback_queues and BG_KEY not in server.last_generator_exit
    assert CONV in server.feedback_queues                        # the conversation's own is untouched


@pytest.mark.asyncio
async def test_stream_passes_frames_through_and_releases_at_the_end():
    server.get_feedback_queue(BG_KEY)

    async def source():
        yield "a"
        yield "b"

    assert [f async for f in bt.stream(source(), CONV)] == ["a", "b"]
    assert BG_KEY not in server.feedback_queues


# ── the real tool loop ────────────────────────────────────────────────────

STAGED = {"type": "feedback", "message": "for the real turn"}


@pytest.mark.asyncio
async def test_a_background_turn_does_not_take_feedback_staged_for_the_conversation():
    server.pending_feedback_lists[CONV] = [dict(STAGED)]
    provider, _ = await _run([_final(), _final()], None,
                             feedback_key=BG_KEY, hold_for_diff_apply=False)
    assert len(provider.calls) == 1
    assert not any("for the real turn" in t for t in _user_texts(provider.calls[0]))
    assert server.pending_feedback_lists.get(CONV) == [STAGED]

    # Positive control: an ordinary turn on the conversation's key does take it.
    provider, _ = await _run([_final(), _final()], None)
    assert any("for the real turn" in t for c in provider.calls for t in _user_texts(c))


@pytest.mark.asyncio
async def test_a_background_turn_leaves_a_held_turn_waiting_and_its_ledger_alone():
    seen = {}

    async def user(provider):
        await _until_waiting(provider)
        assert len(provider.calls) == 1, "turn did not hold"
        start = al.turn_start_seq(CONV)
        al.record(CONV, "app/x.py", _ok())
        # The draft request, as stream_chunks sends it for a background turn.
        bg, bg_events = await _run([_final()], None, feedback_key=BG_KEY, hold_for_diff_apply=False)
        seen["bg_calls"] = len(bg.calls)
        seen["bg_text"] = " ".join(_user_texts(bg.calls[0]))
        seen["bg_waits"] = _of(bg_events, "diff_wait")
        assert al.turn_start_seq(CONV) == start, "the background turn moved the turn start"
        await asyncio.sleep(0.5)
        assert len(provider.calls) == 1, "the held turn resumed or ended under the draft"
        al.record(CONV, "tests/test_x.py", _ok(("1",)))

    provider, events = await _run([_diffs_then_end(), _final()], user, hold_for_diff_apply=True)
    assert seen["bg_calls"] == 1 and seen["bg_waits"] == []
    assert "[Diff apply" not in seen["bg_text"]            # no note, and none marked as shown
    # The held turn still gets its full report, including the apply made
    # before the draft ran.
    assert len(provider.calls) == 2
    report = _user_texts(provider.calls[1])[-1]
    assert "were applied" in report and "app/x.py" in report and "not_applied" not in report
    assert _of(events, "diff_wait")[-1]["outcome"] == "complete"


# ── the real endpoint ─────────────────────────────────────────────────────

@pytest.fixture
def chat_stream():
    gate = asyncio.Event()
    seen: List[dict] = []

    async def fake_stream_chunks(body):
        seen.append(body)
        if body["config"].get("background"):
            yield sse({"content": "draft saved"})
            yield sse({"type": "done"})
            return
        yield sse({"content": "hello "})
        await gate.wait()
        yield sse({"content": "world"})
        yield sse({"type": "done"})

    with patch.object(server, "stream_chunks", fake_stream_chunks), \
         patch("app.providers.factory.is_endpoint_supported", return_value=True), \
         patch.object(server.ModelManager, "get_model_alias", return_value="test-model"), \
         patch.object(server.ModelManager, "get_model_config", return_value={}):
        yield gate, seen


def _text(chunks):
    return "".join(f["content"] for f in parse_frames(chunks) if "content" in f)


@pytest.mark.asyncio
async def test_a_background_request_does_not_supersede_the_running_turn(chat_stream):
    gate, seen = chat_stream
    first_resp = await server.chat_endpoint(
        _request("POST", "/api/chat", {"question": "q", "conversation_id": "conv-bg"}))
    first_out: List[str] = []
    first_drain = asyncio.create_task(_drain(first_resp, first_out))
    await asyncio.sleep(0.05)
    running = relay._turns["conv-bg"]

    bg_resp = await server.chat_endpoint(_request("POST", "/api/chat", {
        "question": "draft", "conversation_id": "conv-bg", "background": True}))
    assert bg_resp.status_code == 200
    bg_out: List[str] = []
    await asyncio.wait_for(_drain(bg_resp, bg_out), 5)
    assert _text(bg_out) == "draft saved"

    assert relay._turns["conv-bg"] is running and relay.status("conv-bg")["active"] is True
    assert running.end_reason != "superseded"
    cfg = seen[-1]["config"]
    assert cfg["background"] is True and cfg["hold_for_diff_apply"] is False
    assert seen[-1]["conversation_id"] == "conv-bg"
    assert "background" not in seen[0]["config"]

    gate.set()
    await asyncio.wait_for(first_drain, 5)
    assert _text(first_out) == "hello world"


@pytest.mark.asyncio
async def test_an_ordinary_second_request_still_supersedes(chat_stream):
    """Positive control for the test above: the relay path does cancel."""
    gate, _ = chat_stream
    await server.chat_endpoint(
        _request("POST", "/api/chat", {"question": "q", "conversation_id": "conv-sup"}))
    await asyncio.sleep(0.05)
    running = relay._turns["conv-sup"]
    await server.chat_endpoint(
        _request("POST", "/api/chat", {"question": "again", "conversation_id": "conv-sup"}))
    await asyncio.sleep(0.05)
    assert relay._turns["conv-sup"] is not running and running.end_reason == "superseded"
    gate.set()


@pytest.mark.asyncio
async def test_a_background_turn_on_an_idle_conversation_is_not_registered_with_the_relay(chat_stream):
    resp = await server.chat_endpoint(_request("POST", "/api/chat", {
        "question": "draft", "conversation_id": "conv-idle", "background": True}))
    out: List[str] = []
    await asyncio.wait_for(_drain(resp, out), 5)
    assert _text(out) == "draft saved"
    assert not relay.has_turn("conv-idle")      # nothing for a reloading tab to reattach to


def test_stream_chunks_honours_the_flag():
    """stream_chunks is too entangled to drive; the three places it must
    read the flag are asserted in its source."""
    src = inspect.getsource(server.stream_chunks)
    assert "background_turn.in_config(body)" in src
    assert "if conversation_id and not _background:" in src                  # stale-feedback discard
    assert "feedback_key=background_turn.feedback_key(conversation_id) if _background else None" in src
    assert "ENABLE_DIFF_VALIDATION and not _background" in src
