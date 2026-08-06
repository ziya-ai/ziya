"""
Tests for the relay's replay/live partition (B12).

``_record`` collapses adjacent same-block ``task_text_delta`` events by
MUTATING the last buffered dict in place.  ``connect`` used to take a
shallow ``list(...)`` snapshot of that buffer and then replay it outside
the lock, so the snapshot shared its dicts with the live buffer:

  * a ``push`` landing mid-replay appended its content into an entry the
    replay had not sent yet, AND delivered the same content again as a
    live event — the client rendered the overlap twice.  Reproduced as
    "AAABBB" arriving as "AAABBBBBB".
  * conversely, an event pushed between "snapshot taken" and "socket
    registered" reached neither the replay nor the live stream, and was
    silently lost.

The invariant under test: the replay and the live stream PARTITION the
event sequence — every event is delivered exactly once, in order.

These drive the real module.  ``SlowWS.send_json`` awaits, because a real
socket always has a suspension point and it is precisely that suspension
that opened the window; a synchronous stub cannot exhibit the bug.
"""

import asyncio

import pytest

from app.agents import task_run_stream_relay as relay


@pytest.fixture(autouse=True)
def _clean_relay():
    """Isolate module-level relay state between tests."""
    relay._history.clear()
    relay._active_connections.clear()
    for task in list(relay._drop_tasks.values()):
        task.cancel()
    relay._drop_tasks.clear()
    yield
    relay._history.clear()
    relay._active_connections.clear()
    for task in list(relay._drop_tasks.values()):
        task.cancel()
    relay._drop_tasks.clear()


class SlowWS:
    """A client whose send_json suspends, as a real WebSocket does.

    Records a COPY of each event: the assertions are about the value at
    SEND time, not whatever the shared dict happens to hold once the
    test finishes — which is the very confusion the bug exploited.
    """

    def __init__(self):
        self.received = []

    async def send_json(self, event):
        await asyncio.sleep(0)
        self.received.append(dict(event))


def _rendered_text(ws: SlowWS) -> str:
    """Concatenate every delta the client received, replay then live —
    i.e. what the frontend's accumulateLive would build."""
    replayed = [e for e in ws.received if e["type"] == "task_text_delta_run"]
    live = [e for e in ws.received if e["type"] == "task_text_delta"]
    return ("".join(e.get("content", "") for e in replayed)
            + "".join(e.get("content", "") for e in live))


def _delta(block_id: str, content: str) -> dict:
    return {"type": "task_text_delta", "block_id": block_id, "content": content}


# ── the duplication half ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_push_during_replay_does_not_duplicate_text():
    """The reported shape: attach mid-run while text is still streaming.

    A leading lifecycle event is what makes this reachable — it gives the
    replay loop a suspension point BEFORE the folded delta entry, so a
    concurrent push can mutate that entry while it sits unsent.  Every
    real run opens with run_started, so this is the common case, not a
    contrived one.
    """
    run = "run-dup"
    await relay.push(run, {"type": "run_started", "run_id": run})
    await relay.push(run, _delta("b1", "AAA"))

    ws = SlowWS()

    async def interleaved():
        await asyncio.sleep(0)          # land inside connect()'s replay
        await relay.push(run, _delta("b1", "BBB"))

    await asyncio.gather(relay.connect(run, ws), interleaved())

    assert _rendered_text(ws) == "AAABBB", (
        "replay and live stream must partition the text, not overlap"
    )


@pytest.mark.asyncio
async def test_replayed_entry_is_not_mutated_after_being_sent():
    """The mechanism, asserted directly: the snapshot must not alias the
    live buffer's dicts."""
    run = "run-alias"
    await relay.push(run, {"type": "run_started", "run_id": run})
    await relay.push(run, _delta("b1", "AAA"))

    ws = SlowWS()

    async def interleaved():
        await asyncio.sleep(0)
        await relay.push(run, _delta("b1", "BBB"))

    await asyncio.gather(relay.connect(run, ws), interleaved())

    replayed = [e for e in ws.received if e["type"] == "task_text_delta_run"]
    assert len(replayed) == 1
    # The entry the client actually received carries only what had been
    # recorded when the snapshot was taken.
    assert replayed[0]["content"] == "AAA"
    assert replayed[0]["count"] == 1


@pytest.mark.asyncio
async def test_many_interleaved_pushes_are_each_delivered_once():
    """Wider version: several deltas landing across the replay window."""
    run = "run-many"
    await relay.push(run, {"type": "run_started", "run_id": run})
    for c in ("A", "B", "C"):
        await relay.push(run, _delta("b1", c))

    ws = SlowWS()

    async def interleaved():
        for c in ("D", "E", "F"):
            await asyncio.sleep(0)
            await relay.push(run, _delta("b1", c))

    await asyncio.gather(relay.connect(run, ws), interleaved())

    assert _rendered_text(ws) == "ABCDEF"


# ── the loss half ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_pushed_at_attach_is_not_lost():
    """The converse gap: registration and snapshot must be atomic.

    An event pushed between them would reach neither the replay (already
    snapshotted) nor the live stream (not yet registered).
    """
    run = "run-lost"
    await relay.push(run, {"type": "run_started", "run_id": run})

    ws = SlowWS()

    async def racing_push():
        await asyncio.sleep(0)
        await relay.push(run, _delta("b1", "MUSTAPPEAR"))

    await asyncio.gather(relay.connect(run, ws), racing_push())

    assert "MUSTAPPEAR" in _rendered_text(ws), "event fell between replay and live"


@pytest.mark.asyncio
async def test_two_clients_attaching_concurrently_both_get_full_text():
    """Two reloads at once must not interfere with each other."""
    run = "run-two"
    await relay.push(run, {"type": "run_started", "run_id": run})
    await relay.push(run, _delta("b1", "AAA"))

    a, b = SlowWS(), SlowWS()

    async def interleaved():
        await asyncio.sleep(0)
        await relay.push(run, _delta("b1", "BBB"))

    await asyncio.gather(
        relay.connect(run, a), relay.connect(run, b), interleaved(),
    )

    assert _rendered_text(a) == "AAABBB"
    assert _rendered_text(b) == "AAABBB"


# ── unchanged behaviour ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quiescent_attach_replays_everything_in_order():
    """No concurrency: the ordinary path must be untouched."""
    run = "run-quiet"
    await relay.push(run, {"type": "run_started", "run_id": run})
    await relay.push(run, _delta("b1", "AAA"))
    await relay.push(run, {"type": "task_tool_call", "block_id": "b1",
                           "tool_name": "grep"})
    await relay.push(run, _delta("b1", "BBB"))

    ws = SlowWS()
    await relay.connect(run, ws)

    assert [e["type"] for e in ws.received] == [
        "run_started", "task_text_delta_run", "task_tool_call",
        "task_text_delta_run",
    ]
    # A tool call breaks the fold, so the two runs stay separate.
    assert _rendered_text(ws) == "AAABBB"


@pytest.mark.asyncio
async def test_fold_still_collapses_adjacent_deltas():
    """The fold is the reason the buffer stays bounded; keep it working."""
    run = "run-fold"
    for c in "abcdefghij":
        await relay.push(run, _delta("b1", c))

    buf = list(relay._history[run])
    assert len(buf) == 1, "ten adjacent deltas should occupy one slot"
    assert buf[0]["type"] == "task_text_delta_run"
    assert buf[0]["content"] == "abcdefghij"
    assert buf[0]["count"] == 10


@pytest.mark.asyncio
async def test_fold_does_not_span_different_blocks():
    run = "run-blocks"
    await relay.push(run, _delta("b1", "AAA"))
    await relay.push(run, _delta("b2", "BBB"))

    buf = list(relay._history[run])
    assert len(buf) == 2
    assert [e["block_id"] for e in buf] == ["b1", "b2"]


@pytest.mark.asyncio
async def test_push_with_no_clients_still_records_for_later_replay():
    """The buffer's whole purpose: attach after the fact and catch up."""
    run = "run-nobody"
    await relay.push(run, _delta("b1", "AAA"))
    assert not relay.has_clients(run)

    ws = SlowWS()
    await relay.connect(run, ws)
    assert _rendered_text(ws) == "AAA"


@pytest.mark.asyncio
async def test_a_dead_socket_during_replay_does_not_break_the_relay():
    """A client that vanishes mid-replay must not raise into the executor."""
    run = "run-dead"
    await relay.push(run, _delta("b1", "AAA"))

    class DeadWS:
        async def send_json(self, event):
            raise ConnectionResetError("client gone")

    # Must not raise.
    await relay.connect(run, DeadWS())
    # And the relay is still usable afterwards.
    await relay.safe_push(run, _delta("b1", "BBB"))


@pytest.mark.asyncio
async def test_fanout_is_not_serialized_behind_the_lock():
    """The fanout must stay OUTSIDE the lock.

    Holding it across ``send_json`` would let one unresponsive socket
    stall every push server-wide — a worse failure than either race this
    change fixes.

    Asserted via a DIFFERENT run id: a second push cannot be tested on the
    same run, since it would legitimately block on the same slow socket
    during its own fanout no matter how the lock is scoped.  Using another
    run isolates the question to "is the shared lock held across an
    await?", which is the thing that would serialize the whole server.
    """
    slow_run, other_run = "run-slow", "run-other"
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingWS:
        def __init__(self):
            self.received = []

        async def send_json(self, event):
            started.set()
            await release.wait()
            self.received.append(dict(event))

    ws = BlockingWS()
    await relay.connect(slow_run, ws)

    async def blocked_push():
        await relay.push(slow_run, _delta("b1", "AAA"))

    task = asyncio.create_task(blocked_push())
    await started.wait()          # stuck inside send_json, mid-fanout

    # A push for an UNRELATED run must not be blocked by it.  If the lock
    # were held across the fanout, this would time out.
    await asyncio.wait_for(relay.push(other_run, _delta("b2", "BBB")), timeout=1.0)
    assert list(relay._history[other_run])[0]["content"] == "BBB"

    release.set()
    await task
