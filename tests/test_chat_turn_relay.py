"""
Contract tests for app/agents/chat_turn_relay — the server-owned chat turn.

What is pinned here, and why each matters:

  * Partition exactness: a subscriber attaching mid-turn receives every
    frame exactly once across replay + live tail — nothing duplicated,
    nothing lost in the gap between "snapshot taken" and "queue
    registered".  This is the property that makes reload/cross-window
    attach trustworthy; the two existing relays each had a bug here once.
  * Content folding: adjacent {"content": ...} frames fold into one slot
    with identical concatenated text, and NON-content frames are never
    folded across.  A late subscriber must see the same text a live one
    did, just in fewer frames.
  * Disconnect semantics (the design decision in design/consent-runtime.md):
    a subscriber leaving does NOT cancel the turn; the last subscriber
    leaving arms a grace timer that DOES cancel; a hold() suspends the
    timer; release() re-arms it; a new subscriber disarms it.
  * Explicit cancel publishes a stream_end frame so a second window sees
    the turn end rather than hanging.
  * A source exception becomes a client-visible error frame + stream_end
    (the keepalive wrapper no longer sees the source, so this moved here).
  * Supersede: a second start_turn for the same conversation cancels the
    first.
  * Buffer trimming counts dropped frames and a late subscriber is told.

Timing-dependent cases patch the grace delay to milliseconds rather than
sleeping for real; the retention timer is likewise shortened where a test
needs a turn to be forgotten.
"""

import asyncio
import json
from typing import List
from unittest.mock import patch

import pytest

import app.agents.chat_turn_relay as relay


# ── helpers ───────────────────────────────────────────────────────────────

def sse(payload) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def content(text: str) -> str:
    return sse({"content": text})


def parse(frame: str):
    assert frame.startswith("data: ") and frame.endswith("\n\n"), frame
    return json.loads(frame[6:])


async def gen_from(frames: List[str], gate: asyncio.Event = None, gate_after: int = None):
    """Async source that optionally blocks on ``gate`` after ``gate_after`` frames."""
    for i, f in enumerate(frames):
        if gate is not None and gate_after is not None and i == gate_after:
            await gate.wait()
        yield f
        await asyncio.sleep(0)  # let subscribers run


async def collect(conversation_id: str) -> List[str]:
    out = []
    async for frame in relay.subscribe(conversation_id):
        out.append(frame)
    return out


def joined_text(frames: List[str]) -> str:
    return "".join(
        parse(f)["content"] for f in frames
        if f.startswith('data: {"content": ')
    )


async def wait_done(conversation_id: str, timeout: float = 2.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        st = relay.status(conversation_id)
        if st is not None and not st["active"]:
            return st
        await asyncio.sleep(0.005)
    raise AssertionError(f"turn for {conversation_id} did not finish within {timeout}s")


@pytest.fixture(autouse=True)
def _clean_relay():
    relay._reset_for_tests()
    yield
    relay._reset_for_tests()


# ── basic lifecycle ───────────────────────────────────────────────────────

class TestBasicDelivery:
    async def test_live_subscriber_receives_every_frame_in_order(self):
        frames = [sse({"type": "tool_start"}), content("a"), content("b"), sse({"type": "x"})]
        gate = asyncio.Event()
        await relay.start_turn("c1", gen_from(frames, gate, gate_after=0))
        task = asyncio.create_task(collect("c1"))
        await asyncio.sleep(0.01)
        gate.set()
        got = await asyncio.wait_for(task, 2)
        assert got == frames

    async def test_late_subscriber_gets_full_replay_after_finish(self):
        frames = [content("hello "), content("world"), sse({"type": "done"})]
        await relay.start_turn("c2", gen_from(frames))
        await wait_done("c2")
        got = await collect("c2")
        # Folded: two content frames became one, text preserved.
        assert joined_text(got) == "hello world"
        assert [parse(f) for f in got if "content" not in parse(f)] == [{"type": "done"}]

    async def test_subscribe_to_unknown_conversation_yields_nothing(self):
        assert await collect("nope") == []
        assert relay.status("nope") is None

    async def test_status_reports_active_then_finished(self):
        gate = asyncio.Event()
        await relay.start_turn("c3", gen_from([content("x")], gate, gate_after=0))
        st = relay.status("c3")
        assert st["active"] is True and st["cancelled"] is False
        gate.set()
        st = await wait_done("c3")
        assert st["active"] is False and st["finished_at"] is not None

    async def test_headers_are_retained_for_reattach(self):
        await relay.start_turn("c4", gen_from([content("x")]),
                               headers={"X-Ziya-Model": "m", "X-Ziya-Model-Source": "pin"})
        assert relay.headers_for("c4") == {"X-Ziya-Model": "m", "X-Ziya-Model-Source": "pin"}
        assert relay.headers_for("absent") == {}


# ── the property that makes reattach trustworthy ──────────────────────────

class TestPartitionExactness:
    async def test_mid_turn_subscriber_sees_each_frame_exactly_once(self):
        """Attach while frames are flowing: replay + live must partition the
        sequence exactly.  Non-content frames carry a sequence number so a
        duplicate or a gap is detectable regardless of folding."""
        n = 60
        frames = [sse({"type": "seq", "i": i}) for i in range(n)]

        async def slow_source():
            for f in frames:
                yield f
                await asyncio.sleep(0.001)

        await relay.start_turn("c5", slow_source())
        await asyncio.sleep(0.02)  # let some frames buffer first
        got = await asyncio.wait_for(collect("c5"), 5)
        seqs = [parse(f)["i"] for f in got if parse(f).get("type") == "seq"]
        assert seqs == list(range(n)), f"dup or gap in replay/live partition: {seqs}"

    async def test_two_subscribers_see_identical_text(self):
        frames = [content(ch) for ch in "the quick brown fox"] + [sse({"type": "end"})]
        gate = asyncio.Event()
        await relay.start_turn("c6", gen_from(frames, gate, gate_after=5))
        early = asyncio.create_task(collect("c6"))
        await asyncio.sleep(0.01)
        late = asyncio.create_task(collect("c6"))
        await asyncio.sleep(0.01)
        gate.set()
        a, b = await asyncio.wait_for(asyncio.gather(early, late), 2)
        assert joined_text(a) == "the quick brown fox"
        assert joined_text(b) == "the quick brown fox"


# ── folding ───────────────────────────────────────────────────────────────

class TestFolding:
    async def test_adjacent_content_frames_fold_into_one_slot(self):
        await relay.start_turn("f1", gen_from([content(ch) for ch in "abcdef"]))
        st = await wait_done("f1")
        assert st["buffered_frames"] == 1
        got = await collect("f1")
        assert got == [content("abcdef")]

    async def test_folding_never_crosses_a_non_content_frame(self):
        frames = [content("a"), content("b"), sse({"tool_start": {"n": 1}}), content("c")]
        await relay.start_turn("f2", gen_from(frames))
        await wait_done("f2")
        got = await collect("f2")
        assert [parse(f) for f in got] == [
            {"content": "ab"}, {"tool_start": {"n": 1}}, {"content": "c"},
        ]

    async def test_multi_key_content_frame_is_not_folded(self):
        """A frame that happens to start with the content prefix but carries
        more keys is a different event and must be preserved verbatim."""
        odd = 'data: {"content": "x", "type": "validation_retry"}\n\n'
        frames = [content("a"), odd, content("b")]
        await relay.start_turn("f3", gen_from(frames))
        await wait_done("f3")
        got = await collect("f3")
        assert got == frames


# ── buffer bounds ─────────────────────────────────────────────────────────

class TestTrimming:
    async def test_oldest_frames_dropped_and_late_subscriber_is_told(self):
        frames = [sse({"type": "seq", "i": i}) for i in range(10)]
        with patch.object(relay, "_MAX_FRAMES", 4):
            await relay.start_turn("t1", gen_from(frames))
            st = await wait_done("t1")
            assert st["dropped_frames"] == 6
            got = await collect("t1")
        first = parse(got[0])
        assert first == {"type": "relay_replay_truncated", "dropped_frames": 6}
        assert [parse(f)["i"] for f in got[1:]] == [6, 7, 8, 9]

    async def test_single_oversized_frame_is_never_dropped(self):
        big = content("x" * 5000)
        with patch.object(relay, "_MAX_BYTES", 100):
            await relay.start_turn("t2", gen_from([big]))
            st = await wait_done("t2")
            assert st["buffered_frames"] == 1 and st["dropped_frames"] == 0


# ── disconnect semantics ──────────────────────────────────────────────────

class TestDisconnectSemantics:
    async def test_subscriber_leaving_does_not_cancel_turn(self):
        gate = asyncio.Event()
        await relay.start_turn("d1", gen_from([content("a"), content("b")], gate, gate_after=1))
        agen = relay.subscribe("d1")
        assert await agen.__anext__() == content("a")
        await agen.aclose()  # client disconnects
        await asyncio.sleep(0.01)
        assert relay.status("d1")["active"] is True, "leaving must not cancel"
        gate.set()
        st = await wait_done("d1")
        assert st["cancelled"] is False

    async def test_last_subscriber_leaving_arms_grace_and_cancels(self):
        gate = asyncio.Event()  # never set: source hangs like a long tool call
        await relay.start_turn("d2", gen_from([content("a"), content("b")], gate, gate_after=1))
        with patch.object(relay, "_grace_seconds", return_value=0.02):
            agen = relay.subscribe("d2")
            await agen.__anext__()
            await agen.aclose()
            st = await wait_done("d2", timeout=1.0)
        assert st["cancelled"] is True

    async def test_new_subscriber_disarms_grace(self):
        gate = asyncio.Event()
        await relay.start_turn("d3", gen_from([content("a"), content("b")], gate, gate_after=1))
        with patch.object(relay, "_grace_seconds", return_value=0.05):
            agen = relay.subscribe("d3")
            await agen.__anext__()
            await agen.aclose()
            # Reattach well inside the grace window.
            second = asyncio.create_task(collect("d3"))
            await asyncio.sleep(0.1)  # longer than the grace delay
            assert relay.status("d3")["active"] is True, "reattach must disarm grace"
            gate.set()
            got = await asyncio.wait_for(second, 2)
        assert joined_text(got) == "ab"

    async def test_hold_suspends_grace_and_release_rearms_it(self):
        gate = asyncio.Event()
        await relay.start_turn("d4", gen_from([content("a"), content("b")], gate, gate_after=1))
        with patch.object(relay, "_grace_seconds", return_value=0.02):
            assert await relay.hold("d4", "consent:abc")
            agen = relay.subscribe("d4")
            await agen.__anext__()
            await agen.aclose()
            await asyncio.sleep(0.08)  # several grace periods
            st = relay.status("d4")
            assert st["active"] is True and st["held"] is True
            assert st["hold_reasons"] == ["consent:abc"]
            assert await relay.release("d4", "consent:abc")
            st = await wait_done("d4", timeout=1.0)
        assert st["cancelled"] is True

    async def test_overlapping_holds_compose(self):
        gate = asyncio.Event()
        await relay.start_turn("d5", gen_from([content("a")], gate, gate_after=0))
        with patch.object(relay, "_grace_seconds", return_value=0.02):
            await relay.hold("d5", "r1")
            await relay.hold("d5", "r2")
            await relay.release("d5", "r1")
            await asyncio.sleep(0.06)
            assert relay.status("d5")["active"] is True, "r2 still holds"
            await relay.release("d5", "r2")
            st = await wait_done("d5", timeout=1.0)
        assert st["cancelled"] is True

    async def test_hold_on_unknown_or_finished_turn_is_false(self):
        assert await relay.hold("ghost", "r") is False
        await relay.start_turn("d6", gen_from([content("a")]))
        await wait_done("d6")
        assert await relay.hold("d6", "r") is False


# ── explicit cancel / supersede / errors ─────────────────────────────────

class TestCancelAndErrors:
    async def test_cancel_turn_ends_second_window_with_stream_end(self):
        gate = asyncio.Event()
        await relay.start_turn("e1", gen_from([content("a"), content("b")], gate, gate_after=1))
        watcher = asyncio.create_task(collect("e1"))
        await asyncio.sleep(0.01)
        assert await relay.cancel_turn("e1") is True
        got = await asyncio.wait_for(watcher, 2)
        assert parse(got[-1]) == {"type": "stream_end", "reason": "cancelled"}
        assert relay.status("e1")["cancelled"] is True

    async def test_cancel_on_absent_or_finished_turn_is_false(self):
        assert await relay.cancel_turn("nothing") is False
        await relay.start_turn("e2", gen_from([content("a")]))
        await wait_done("e2")
        assert await relay.cancel_turn("e2") is False

    async def test_new_turn_supersedes_running_one(self):
        gate = asyncio.Event()
        await relay.start_turn("e3", gen_from([content("old"), content("x")], gate, gate_after=1))
        first_id = relay.status("e3")["turn_id"]
        await relay.start_turn("e3", gen_from([content("new")]))
        assert relay.status("e3")["turn_id"] != first_id
        st = await wait_done("e3")
        got = await collect("e3")
        assert joined_text(got) == "new"
        assert st["cancelled"] is False  # the NEW turn was not cancelled

    async def test_source_exception_becomes_error_frame_and_stream_end(self):
        async def boom():
            yield content("partial")
            raise RuntimeError("provider exploded")

        await relay.start_turn("e4", boom())
        st = await wait_done("e4")
        assert st["error"], "status must record the error"
        got = await collect("e4")
        parsed = [parse(f) for f in got]
        assert parsed[0] == {"content": "partial"}
        assert parsed[1]["error_type"] == "stream_error"
        assert parsed[2] == {"type": "stream_end"}

    async def test_source_generator_is_closed_after_pump(self):
        closed = asyncio.Event()

        async def src():
            try:
                yield content("a")
                yield content("b")
            finally:
                closed.set()

        await relay.start_turn("e5", src())
        await wait_done("e5")
        assert closed.is_set()


class TestShutdown:
    async def test_shutdown_all_cancels_running_turns(self):
        gate = asyncio.Event()
        await relay.start_turn("s1", gen_from([content("a"), content("b")], gate, gate_after=1))
        await relay.start_turn("s2", gen_from([content("a"), content("b")], gate, gate_after=1))
        await asyncio.sleep(0.01)
        await asyncio.wait_for(relay.shutdown_all(), 2)
        assert relay.status("s1")["cancelled"] is True
        assert relay.status("s2")["cancelled"] is True


class TestLazyImportsResolve:
    """Every function-local import in the relay must resolve.

    The error path imported ``app.utils.error_sanitizer`` (nonexistent; the
    function is in ``app.utils.error_handlers``).  Because that import sits
    INSIDE the ``except`` handler it only runs when a provider fails, so a
    test run in which no source raises passes cleanly and the client, on a
    real failure, sees the stream go silent instead of ending with an error
    frame.  Resolving each lazy import statically closes that gap regardless
    of which branches a given run exercises.
    """

    def test_all_function_local_imports_import(self):
        import ast
        import importlib
        import inspect

        tree = ast.parse(inspect.getsource(relay))
        lazy = [n for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.col_offset > 0 and n.module]
        assert lazy, "expected at least one function-local import (positive control)"
        for node in lazy:
            mod = importlib.import_module(node.module)
            for alias in node.names:
                assert hasattr(mod, alias.name), (
                    f"line {node.lineno}: {node.module} has no attribute {alias.name!r}")
