"""Cross-process observation of a task run via the on-disk event journal.

The relay's history buffer is process-local.  Before the journal, a
client viewing a running task card from a server other than the one
that launched it received the REST summary (disk-backed) but none of
the live detail — text deltas, tool calls, block events — because
that server's relay had never seen the run and had nowhere to look.

These tests drive the two halves through a single module by
*resetting* module state between them, which is exactly what a
second server process looks like to the relay: no history, no
journal handle, no connections.

  * Executor side: ``open_journal`` + ``push`` write raw events to
    ``task_runs/<run_id>/events.jsonl``; ``close_journal`` releases it.
  * Observer side: ``connect(..., journal=path)`` on a process with no
    history tails the file and delivers the events to the socket.
  * Tail continues to deliver events appended AFTER the observer
    attached (the live case, not just the catch-up case).
  * Tail restart after all observers leave does not re-deliver.
  * The executing process itself never tails its own journal.
  * The storage layer places the journal inside the run directory
    that ``delete()`` already removes.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, List

import pytest

from app.agents import task_run_stream_relay as relay
from app.storage.task_runs import TaskRunStorage


class FakeWS:
    def __init__(self):
        self.sent: List[Any] = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _reset():
    relay._active_connections.clear()
    relay._history.clear()
    for task in list(relay._drop_tasks.values()):
        task.cancel()
    relay._drop_tasks.clear()
    for task in list(relay._tail_tasks.values()):
        task.cancel()
    relay._tail_tasks.clear()
    relay._tail_offsets.clear()
    for run_id in list(relay._journals):
        relay.close_journal(run_id)


@pytest.fixture(autouse=True)
def reset_relay_state():
    _reset()
    yield
    _reset()


@pytest.fixture
def fast_tail(monkeypatch):
    monkeypatch.setattr(relay, "_TAIL_INTERVAL_SECONDS", 0.01)


async def _settle(rounds: int = 10):
    for _ in range(rounds):
        await asyncio.sleep(0.02)


def _types(ws: FakeWS) -> List[str]:
    return [e["type"] for e in ws.sent]


# ---- executor side -----------------------------------------------------

@pytest.mark.asyncio
async def test_push_appends_raw_events_to_journal(tmp_path: Path):
    journal = tmp_path / "run-A" / "events.jsonl"
    relay.open_journal("run-A", journal)
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    await relay.push("run-A", {"type": "task_text_delta", "block_id": "b1", "content": "AAA"})
    await relay.push("run-A", {"type": "task_text_delta", "block_id": "b1", "content": "BBB"})
    relay.close_journal("run-A")

    lines = [json.loads(l) for l in journal.read_text().splitlines() if l.strip()]
    # Raw, not folded: the reader folds on ingest exactly as the live
    # path does, so the journal must carry the individual deltas.
    assert [e["type"] for e in lines] == ["run_started", "task_text_delta", "task_text_delta"]
    assert lines[1]["content"] == "AAA" and lines[2]["content"] == "BBB"


@pytest.mark.asyncio
async def test_push_without_journal_writes_nothing(tmp_path: Path):
    journal = tmp_path / "run-A" / "events.jsonl"
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    assert not journal.exists()


# ---- observer side (a sibling server) -----------------------------------

@pytest.mark.asyncio
async def test_observer_with_no_history_replays_journal(tmp_path: Path, fast_tail):
    journal = tmp_path / "run-A" / "events.jsonl"
    # Server A executes: emits and journals.
    relay.open_journal("run-A", journal)
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    await relay.push("run-A", {"type": "task_text_delta", "block_id": "b1", "content": "AAA"})
    await relay.push("run-A", {"type": "task_text_delta", "block_id": "b1", "content": "BBB"})
    await relay.push("run-A", {"type": "tool_call", "block_id": "b1", "name": "ls"})
    relay.close_journal("run-A")

    # Server B: fresh process, nothing in memory.
    _reset()
    assert "run-A" not in relay._history

    ws = FakeWS()
    await relay.connect("run-A", ws, journal=journal, alive=lambda: True)
    await _settle()

    assert _types(ws) == ["run_started", "task_text_delta_run", "tool_call"]
    # Folded on ingest exactly like the live path.
    assert ws.sent[1]["content"] == "AAABBB"
    assert ws.sent[1]["count"] == 2


@pytest.mark.asyncio
async def test_observer_receives_events_appended_after_attach(tmp_path: Path, fast_tail):
    """The live case: server A keeps writing after server B's client
    attached.  Those later events must reach the socket too."""
    journal = tmp_path / "run-A" / "events.jsonl"
    relay.open_journal("run-A", journal)
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    writer = relay._journals.pop("run-A")  # keep the handle, hide it: "server B"

    ws = FakeWS()
    await relay.connect("run-A", ws, journal=journal, alive=lambda: True)
    await _settle()
    assert _types(ws) == ["run_started"]

    # Server A writes more (simulated by appending to the file directly).
    writer.write(json.dumps({"type": "block_started", "block_id": "b1"}) + "\n")
    writer.write(json.dumps({"type": "task_text_delta", "block_id": "b1", "content": "x"}) + "\n")
    writer.flush()
    await _settle()

    assert _types(ws) == ["run_started", "block_started", "task_text_delta_run"]

    writer.write(json.dumps({"type": "run_completed", "run_id": "run-A", "status": "passed"}) + "\n")
    writer.flush()
    await _settle()
    writer.close()

    assert _types(ws)[-1] == "run_completed"
    # Terminal event ends the tail.
    assert "run-A" not in relay._tail_tasks or relay._tail_tasks["run-A"].done()


@pytest.mark.asyncio
async def test_second_observer_gets_snapshot_not_duplicates(tmp_path: Path, fast_tail):
    journal = tmp_path / "run-A" / "events.jsonl"
    relay.open_journal("run-A", journal)
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    await relay.push("run-A", {"type": "block_started", "block_id": "b1"})
    relay.close_journal("run-A")
    _reset()

    ws1 = FakeWS()
    await relay.connect("run-A", ws1, journal=journal, alive=lambda: True)
    await _settle()
    ws2 = FakeWS()
    await relay.connect("run-A", ws2, journal=journal, alive=lambda: True)
    await _settle()

    assert _types(ws1) == ["run_started", "block_started"]
    assert _types(ws2) == ["run_started", "block_started"]


@pytest.mark.asyncio
async def test_tail_restart_after_disconnect_does_not_redeliver(tmp_path: Path, fast_tail):
    """Tail stops when the last observer leaves; a later observer must
    get history once via snapshot, and the restarted tail must resume
    from the recorded offset rather than re-ingest from byte 0."""
    journal = tmp_path / "run-A" / "events.jsonl"
    relay.open_journal("run-A", journal)
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    writer = relay._journals.pop("run-A")

    ws1 = FakeWS()
    await relay.connect("run-A", ws1, journal=journal, alive=lambda: True)
    await _settle()
    await relay.disconnect("run-A", ws1)
    await _settle()
    assert "run-A" not in relay._tail_tasks

    writer.write(json.dumps({"type": "block_started", "block_id": "b1"}) + "\n")
    writer.flush()
    writer.close()

    ws2 = FakeWS()
    await relay.connect("run-A", ws2, journal=journal, alive=lambda: True)
    await _settle()

    assert _types(ws2) == ["run_started", "block_started"]
    assert len(relay._history["run-A"]) == 2


@pytest.mark.asyncio
async def test_tail_stops_when_executor_dead_and_journal_quiet(tmp_path: Path, fast_tail):
    journal = tmp_path / "run-A" / "events.jsonl"
    relay.open_journal("run-A", journal)
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    relay.close_journal("run-A")
    _reset()

    ws = FakeWS()
    await relay.connect("run-A", ws, journal=journal, alive=lambda: False)
    await _settle()

    assert _types(ws) == ["run_started"]
    assert "run-A" not in relay._tail_tasks


@pytest.mark.asyncio
async def test_executor_process_does_not_tail_its_own_journal(tmp_path: Path, fast_tail):
    journal = tmp_path / "run-A" / "events.jsonl"
    relay.open_journal("run-A", journal)
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})

    ws = FakeWS()
    await relay.connect("run-A", ws, journal=journal, alive=lambda: True)
    await _settle()

    assert "run-A" not in relay._tail_tasks
    assert _types(ws) == ["run_started"]  # once, from memory


@pytest.mark.asyncio
async def test_connect_without_journal_is_unchanged(tmp_path: Path):
    await relay.push("run-A", {"type": "run_started", "run_id": "run-A"})
    ws = FakeWS()
    await relay.connect("run-A", ws)
    assert _types(ws) == ["run_started"]
    assert not relay._tail_tasks


@pytest.mark.asyncio
async def test_missing_journal_file_is_a_noop(tmp_path: Path):
    ws = FakeWS()
    await relay.connect("run-A", ws, journal=tmp_path / "nope" / "events.jsonl", alive=lambda: True)
    assert ws.sent == []
    assert not relay._tail_tasks


# ---- storage seam ------------------------------------------------------

def test_journal_path_lives_in_run_dir_removed_by_delete(tmp_path: Path):
    storage = TaskRunStorage(tmp_path)
    path = storage.journal_path("run-A")
    assert path == tmp_path / "task_runs" / "run-A" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{}\n")
    # delete() removes the run directory even when no run.json exists.
    storage.delete("run-A")
    assert not path.exists()
    assert not path.parent.exists()
