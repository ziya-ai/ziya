"""
Durable chat turns and the unwatched-grace rule (option (c)).

Reported loss: a job_start + job_wait turn; the user's ssh tunnel and
browser died.  Even with the tool hold, (1) the model's follow-up after the
wait was grace-cancelled with no viewer, and (2) whatever the turn produced
lived only in the relay's memory for 5 minutes and nowhere on disk, so a
later reload -- or any server restart -- found nothing and showed the
unanswered question with Retry.

Pinned here:
  * A turn that has run a tool is not grace-cancelled for lack of a viewer
    (default), and IS when ZIYA_CHAT_TURN_UNWATCHED_GRACE_SECS is set.
    Positive control: a tool-less turn still is.
  * Every turn is written to disk; once it leaves memory, status / has_turn /
    headers_for / all_statuses / subscribe all answer from the record, and
    subscribe replays the same text a live subscriber saw.
  * A record left "active" by another boot is reported interrupted and its
    replay is terminated with a stream_end.
  * A shutdown-cancelled turn is recorded and replayable.
  * The record is encrypted with the conversation_data category when ALE is
    on (spy on the encryptor; the test sandbox has no keyring).
  * Traversal-shaped ids never touch the filesystem outside the store.

ZIYA_HOME is sandboxed per test by tests/conftest.py.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

import app.agents.chat_turn_relay as relay
import app.agents.chat_turn_store as store


def sse(payload) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def content(text: str) -> str:
    return sse({"content": text})


def joined(frames):
    return "".join(json.loads(f[6:]).get("content", "") for f in frames
                   if f.startswith('data: {"content": '))


async def gen(frames, gate=None, gate_after=None):
    for i, f in enumerate(frames):
        if gate is not None and i == gate_after:
            await gate.wait()
        yield f
        await asyncio.sleep(0)


async def collect(cid):
    return [f async for f in relay.subscribe(cid)]


async def wait_done(cid, timeout=2.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        st = relay.status(cid)
        if st is not None and not st["active"]:
            return st
        await asyncio.sleep(0.005)
    raise AssertionError(f"{cid} did not finish")


async def drop_subscriber(cid):
    agen = relay.subscribe(cid)
    await agen.__anext__()
    await agen.aclose()


async def settle(cid):
    """Wait for the pump task, which ends only after the final disk save
    (``done`` is set just before that save)."""
    turn = relay._turns.get(cid)
    if turn is not None and turn.task is not None:
        await asyncio.wait_for(asyncio.gather(turn.task, return_exceptions=True), 2)


def forget_in_memory(cid):
    """What the 5-minute retention timer (or a restart) does."""
    relay._turns.pop(cid, None)


@pytest.fixture(autouse=True)
def _clean():
    relay._reset_for_tests()
    store._reset_prune_clock_for_tests()
    yield
    relay._reset_for_tests()


# ── grace rule ────────────────────────────────────────────────────────────

class TestUnwatchedGrace:
    async def test_turn_that_ran_a_tool_is_not_grace_cancelled_by_default(self):
        gate = asyncio.Event()
        await relay.start_turn("g1", gen([content("a"), content("b")], gate, 1))
        with patch.object(relay, "_grace_seconds", return_value=0.02):
            async with relay.held("g1", "tool:t1"):
                pass                                  # tool ran and returned
            await drop_subscriber("g1")
            await asyncio.sleep(0.1)                  # many grace periods
            assert relay.status("g1")["active"] is True
            gate.set()
            st = await wait_done("g1")
        assert st["cancelled"] is False and st["ran_tool"] is True
        assert st["end_reason"] == "completed"

    async def test_unwatched_grace_env_restores_a_timeout(self):
        gate = asyncio.Event()
        await relay.start_turn("g2", gen([content("a"), content("b")], gate, 1))
        with patch.object(relay, "_unwatched_grace_seconds", return_value=0.02):
            async with relay.held("g2", "tool:t1"):
                pass
            await drop_subscriber("g2")
            st = await wait_done("g2")
        assert st["cancelled"] is True and st["end_reason"] == "unwatched"

    async def test_positive_control_toolless_turn_still_grace_cancelled(self):
        gate = asyncio.Event()
        await relay.start_turn("g3", gen([content("a"), content("b")], gate, 1))
        with patch.object(relay, "_grace_seconds", return_value=0.02):
            await drop_subscriber("g3")
            st = await wait_done("g3")
        assert st["cancelled"] is True and st["ran_tool"] is False

    async def test_consent_hold_does_not_mark_the_turn_agentic(self):
        gate = asyncio.Event()
        await relay.start_turn("g4", gen([content("a")], gate, 0))
        await relay.hold("g4", "consent:x")
        assert relay.status("g4")["ran_tool"] is False
        await relay.release("g4", "consent:x")
        gate.set()
        await wait_done("g4")

    def test_unwatched_grace_default_is_never(self):
        assert relay._unwatched_grace_seconds() is None


# ── disk record ───────────────────────────────────────────────────────────

class TestDiskRecord:
    async def test_finished_turn_is_replayable_after_leaving_memory(self):
        frames = [content("hel"), content("lo"), sse({"type": "tool_start", "tool_id": "x"}),
                  content(" world"), sse({"type": "stream_end"})]
        await relay.start_turn("p1", gen(frames), headers={"X-Ziya-Model": "m"})
        live = await collect("p1")                    # a live subscriber's view
        mem_status = await wait_done("p1")
        await settle("p1")
        forget_in_memory("p1")

        st = relay.status("p1")
        assert st is not None, "status must come from disk once memory is gone"
        assert st["persisted"] is True and st["turn_id"] == mem_status["turn_id"]
        assert st["active"] is False and st["interrupted"] is False
        assert relay.has_turn("p1") is True
        assert relay.headers_for("p1") == {"X-Ziya-Model": "m"}
        assert [s["conversation_id"] for s in relay.all_statuses()] == ["p1"]

        replay = await collect("p1")
        assert joined(replay) == joined(live) == "hello world"
        assert json.loads(replay[-1][6:]) == {"type": "stream_end"}

    async def test_running_turn_is_checkpointed_at_tool_start(self):
        # The hold at tool start writes promptly, so a crash mid-wait has
        # the pre-tool output on disk.
        gate = asyncio.Event()
        await relay.start_turn("p2", gen([content("before tool"), content("after")], gate, 1))
        await asyncio.sleep(0.01)
        await relay.hold("p2", "tool:t")
        for _ in range(100):
            rec = store.load_status("p2")
            if rec:
                break
            await asyncio.sleep(0.01)
        assert rec and rec["active"] is True and rec["ran_tool"] is True
        data = store.load_frames("p2", rec["turn_id"])
        assert joined(data["frames"]) == "before tool"
        await relay.release("p2", "tool:t")
        gate.set()
        await wait_done("p2")

    async def test_record_from_another_boot_left_active_is_interrupted(self):
        gate = asyncio.Event()
        await relay.start_turn("p3", gen([content("partial"), content("never")], gate, 1))
        await asyncio.sleep(0.01)
        await relay._checkpoint_now(relay._turns["p3"])
        # Simulate the process dying: memory gone, record says active, boot differs.
        task = relay._turns["p3"].task
        forget_in_memory("p3")
        with patch.object(relay, "_BOOT_ID", "a-later-boot"):
            st = relay.status("p3")
        assert st["active"] is False and st["interrupted"] is True
        assert st["cancelled"] is True and st["end_reason"] == "server_restart"
        replay = await collect("p3")
        assert joined(replay) == "partial"
        assert json.loads(replay[-1][6:]) == {"type": "stream_end", "reason": "interrupted"}
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_shutdown_cancelled_turn_is_recorded_and_replayable(self):
        gate = asyncio.Event()
        await relay.start_turn("p4", gen([content("work so far"), content("x")], gate, 1))
        await asyncio.sleep(0.01)
        await relay.shutdown_all()
        forget_in_memory("p4")
        st = relay.status("p4")
        assert st["cancelled"] is True and st["end_reason"] == "server_shutdown"
        assert joined(await collect("p4")) == "work so far"

    async def test_new_turn_record_replaces_old(self):
        await relay.start_turn("p5", gen([content("old")]))
        await wait_done("p5")
        await settle("p5")
        await relay.start_turn("p5", gen([content("new")]))
        new_id = (await wait_done("p5"))["turn_id"]
        await settle("p5")
        forget_in_memory("p5")
        assert relay.status("p5")["turn_id"] == new_id
        assert joined(await collect("p5")) == "new"

    async def test_unknown_conversation_still_has_nothing(self):
        assert relay.status("ghost") is None
        assert relay.has_turn("ghost") is False
        assert await collect("ghost") == []

    async def test_frames_from_a_different_turn_are_ignored(self):
        await relay.start_turn("p6", gen([content("x")]))
        st = await wait_done("p6")
        await settle("p6")
        assert store.load_frames("p6", st["turn_id"]) is not None
        assert store.load_frames("p6", "some-other-turn") is None

    def test_traversal_ids_are_refused(self):
        assert store.load_status("../../etc/passwd") is None
        with pytest.raises(ValueError):
            store.save({"conversation_id": "../escape", "turn_id": "t"}, [], 0)


class TestEncryptionAndRetention:
    async def test_record_is_encrypted_under_conversation_data(self):
        calls = []

        class Spy:
            def is_enabled(self, category=""):
                calls.append(("is_enabled", category))
                return True

            def encrypt(self, data, category=""):
                calls.append(("encrypt", category))
                return data      # leave readable for the assertion below

        with patch("app.utils.encryption.get_encryptor", return_value=Spy()):
            store.save({"conversation_id": "e1", "turn_id": "t"}, [content("x")], 0)
        assert ("encrypt", "conversation_data") in calls
        assert calls.count(("encrypt", "conversation_data")) == 2   # status + frames

    def test_prune_removes_records_past_retention(self, tmp_path):
        import os
        import time
        store.save({"conversation_id": "old", "turn_id": "t"}, [], 0)
        store.save({"conversation_id": "new", "turn_id": "t"}, [], 0)
        d = store._dir()
        past = time.time() - 30 * 24 * 3600
        os.utime(d / "old.status.json", (past, past))
        store._reset_prune_clock_for_tests()
        assert store._maybe_prune() == 1
        assert store.load_status("old") is None
        assert not (d / "old.frames.json").exists()
        assert store.load_status("new") is not None
