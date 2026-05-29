"""Tests for the bounded history buffer in task_run_stream_relay.

The relay used to be fire-and-forget: a WebSocket reconnecting
mid-run got only future events, losing every chunk that streamed
while the client was disconnected (Bug 2 from the d3 post-mortem).

These tests cover the new history buffer:
  * Replay on connect.
  * Bounded growth (cap enforced via deque).
  * On-the-fly collapse of adjacent task_text_delta events for the
    same block_id, mirroring the frontend's collapseEventRuns
    semantics so the buffer doesn't churn through its cap on
    long delta streams.
  * Grace-period drop on run_completed so terminated runs stop
    consuming memory but still serve late connectors.
"""

import asyncio
from typing import Any, List

import pytest

from app.agents import task_run_stream_relay as relay


class FakeWS:
    def __init__(self):
        self.sent: List[Any] = []
        self.closed = False

    async def send_json(self, payload):
        if self.closed:
            raise RuntimeError("closed")
        self.sent.append(payload)


@pytest.fixture(autouse=True)
def reset_relay_state():
    """Reset module-level state between tests so they don't leak.

    The relay holds run_id → connections + history at module scope;
    without this fixture the order of test execution affects the
    deque contents seen by later tests.
    """
    relay._active_connections.clear()
    relay._history.clear()
    # Cancel any pending grace-period drops too.
    for task in list(relay._drop_tasks.values()):
        task.cancel()
    relay._drop_tasks.clear()
    yield
    relay._active_connections.clear()
    relay._history.clear()
    for task in list(relay._drop_tasks.values()):
        task.cancel()
    relay._drop_tasks.clear()


@pytest.mark.asyncio
async def test_push_records_in_history_when_no_clients():
    """Even with zero connected clients, push must record so a
    later-connecting client can replay what it missed."""
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    await relay.push("run-A", {"type": "block_started", "block_id": "b1"})
    assert "run-A" in relay._history
    assert len(relay._history["run-A"]) == 2


@pytest.mark.asyncio
async def test_connect_replays_history():
    """Connecting after events were pushed must deliver them in order."""
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    await relay.push("run-A", {"type": "block_started", "block_id": "b1"})
    ws = FakeWS()
    await relay.connect("run-A", ws)
    assert [e["type"] for e in ws.sent] == ["run_started", "block_started"]


@pytest.mark.asyncio
async def test_history_capped():
    """Buffer must not grow unbounded — a long run shouldn't consume
    arbitrary memory waiting for a possible reconnect."""
    cap = relay._HISTORY_CAP
    # Push more events than the cap.  Use non-delta types so the
    # collapse path doesn't fold them.
    for i in range(cap + 50):
        await relay.push("run-A", {"type": "block_started", "block_id": f"b{i}"})
    assert len(relay._history["run-A"]) == cap
    # Oldest events were dropped — the first surviving block_id is
    # offset by 50.
    first = relay._history["run-A"][0]
    assert first["block_id"] == "b50"


@pytest.mark.asyncio
async def test_delta_collapse_same_block():
    """Adjacent task_text_delta for the same block fold into one
    aggregate entry rather than each consuming a slot in the cap."""
    for i in range(100):
        await relay.push("run-A", {
            "type": "task_text_delta",
            "block_id": "b1",
            "content": f"chunk{i} ",
        })
    # All 100 deltas fold into a single entry.
    assert len(relay._history["run-A"]) == 1
    entry = relay._history["run-A"][0]
    assert entry["type"] == "task_text_delta_run"
    assert entry["block_id"] == "b1"
    assert entry["count"] == 100
    # Concatenated content is preserved verbatim — replay must be
    # equivalent to the originally streamed text.
    expected = "".join(f"chunk{i} " for i in range(100))
    assert entry["content"] == expected


@pytest.mark.asyncio
async def test_delta_collapse_breaks_on_block_change():
    """A delta for a different block must not fold into the previous
    block's run — that would corrupt block-scoped reasoning."""
    await relay.push("run-A", {"type": "task_text_delta", "block_id": "b1", "content": "x"})
    await relay.push("run-A", {"type": "task_text_delta", "block_id": "b1", "content": "y"})
    await relay.push("run-A", {"type": "task_text_delta", "block_id": "b2", "content": "z"})
    assert len(relay._history["run-A"]) == 2
    assert relay._history["run-A"][0]["block_id"] == "b1"
    assert relay._history["run-A"][0]["content"] == "xy"
    assert relay._history["run-A"][1]["block_id"] == "b2"
    assert relay._history["run-A"][1]["content"] == "z"


@pytest.mark.asyncio
async def test_delta_collapse_breaks_on_intervening_event():
    """A non-delta event between two deltas must split the run."""
    await relay.push("run-A", {"type": "task_text_delta", "block_id": "b1", "content": "x"})
    await relay.push("run-A", {"type": "task_tool_call", "block_id": "b1", "tool_name": "fs"})
    await relay.push("run-A", {"type": "task_text_delta", "block_id": "b1", "content": "y"})
    assert len(relay._history["run-A"]) == 3
    types = [e["type"] for e in relay._history["run-A"]]
    assert types == ["task_text_delta_run", "task_tool_call", "task_text_delta_run"]


@pytest.mark.asyncio
async def test_replay_delivers_collapsed_run_to_late_connector():
    """A client connecting mid-run sees the collapsed delta entry
    rather than a hundred individual deltas."""
    for i in range(50):
        await relay.push("run-A", {
            "type": "task_text_delta",
            "block_id": "b1",
            "content": f"{i} ",
        })
    ws = FakeWS()
    await relay.connect("run-A", ws)
    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "task_text_delta_run"
    assert ws.sent[0]["count"] == 50


@pytest.mark.asyncio
async def test_run_completed_schedules_grace_drop():
    """Terminal events schedule history cleanup, but the buffer
    survives long enough for late connectors during the grace
    period."""
    # Use a very short grace period for the test.
    original = relay._GRACE_PERIOD_SECONDS
    relay._GRACE_PERIOD_SECONDS = 0.05
    try:
        await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
        await relay.push("run-A", {
            "type": "run_completed", "run_id": "run-A", "status": "ok",
        })
        # History still present immediately after run_completed.
        assert "run-A" in relay._history
        assert len(relay._history["run-A"]) == 2

        # A late connector during the grace period still gets the
        # full replay.
        ws = FakeWS()
        await relay.connect("run-A", ws)
        assert len(ws.sent) == 2

        # After the grace period elapses, history is dropped.
        await asyncio.sleep(0.15)
        assert "run-A" not in relay._history
    finally:
        relay._GRACE_PERIOD_SECONDS = original


@pytest.mark.asyncio
async def test_disconnect_does_not_drop_history():
    """Disconnect of the last client must not clear history — the
    next reconnect needs to replay."""
    ws = FakeWS()
    await relay.connect("run-A", ws)
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    await relay.disconnect("run-A", ws)
    # No clients, but history must persist.
    assert "run-A" not in relay._active_connections
    assert "run-A" in relay._history
    assert len(relay._history["run-A"]) == 1


@pytest.mark.asyncio
async def test_connect_with_replay_failure_does_not_corrupt_state():
    """If the replay send_json fails (closed socket), connect must
    still leave the run's history intact for the next attempt."""
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    bad_ws = FakeWS()
    bad_ws.closed = True
    # connect() shouldn't raise even if replay fails.
    await relay.connect("run-A", bad_ws)
    # History is still available for the next connector.
    good_ws = FakeWS()
    await relay.connect("run-A", good_ws)
    assert len(good_ws.sent) == 1
