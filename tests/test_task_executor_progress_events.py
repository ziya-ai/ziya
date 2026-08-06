"""
Tests for the live-observation event stream emitted by execute_task_block
(app/agents/task_executor.py) — specifically the two bugs found in the
"task card progress notes / live view" audit:

  B3: ``task_text_delta`` events were emitted with NO ``ts`` field, so the
      frontend fell back to the CLIENT clock for the timestamp while every
      other timestamped event (task_tool_call, task_progress) and the run
      record's ``last_activity_at`` use the SERVER clock.  Any clock skew
      between browser and server corrupted the "Ns ago" age label and the
      note-source preference that compares timestamps across the two.

  B8a: a model-authored ``<progress note="..."/>`` tag and a tool-derived
      "ran <tool>: ..." note both land on the same last-write-wins
      ``task_progress`` channel, even though events already carry a
      ``source`` field ("model" vs absent/"tool") that distinguishes them.
      This test pins the CONTRACT the frontend fix (accumulateLive in
      useTaskRunStream.ts) relies on: model notes are tagged
      ``source="model"``, tool notes are not.

Exercises execute_task_block end-to-end (per the pattern in
test_task_executor_scope.py) by faking StreamingToolExecutor and
capturing every event pushed through task_run_stream_relay.safe_push.
"""

from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.models.task_card import Block


def _task(instructions: str = "do it") -> Block:
    return Block(block_type="task", id="task-1", name="T", instructions=instructions)


class _FakeExecutorTextThenTool:
    """Yields a text delta, a tool call, then another text delta before
    ending — enough to exercise both task_text_delta and task_progress
    emission sites in one run."""

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        yield {"type": "text", "content": "reviewing files"}
        yield {
            "type": "tool_display",
            "tool_name": "run_shell_command",
            "tool_id": "tc-1",
            "args": {"command": "git status"},
            "result": "clean",
        }
        yield {"type": "text", "content": "<progress note=\"reviewed 3/10\"/> more"}
        yield {"type": "stream_end"}


@pytest.fixture
def captured_events():
    """Patch the relay's safe_push to record every emitted event in order,
    keyed to a specific run_id, without touching any real WebSocket
    machinery."""
    events = []

    async def _fake_safe_push(run_id, event):
        events.append(event)

    with patch("app.streaming_tool_executor.StreamingToolExecutor",
               _FakeExecutorTextThenTool), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "fake"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
         patch("app.agents.task_run_stream_relay.safe_push", _fake_safe_push):
        yield events


class TestTaskTextDeltaTimestamp:
    """B3: task_text_delta must carry a server-clock ts, matching every
    other timestamped event on this channel."""

    @pytest.mark.asyncio
    async def test_task_text_delta_carries_ts(self, captured_events):
        block = _task()
        await task_executor.execute_task_block(block, run_id="run-b3")

        deltas = [e for e in captured_events if e.get("type") == "task_text_delta"]
        assert deltas, "no task_text_delta events were emitted"
        for d in deltas:
            assert "ts" in d, f"task_text_delta missing ts: {d}"
            assert isinstance(d["ts"], (int, float))
            assert d["ts"] > 0

    @pytest.mark.asyncio
    async def test_task_text_delta_ts_is_consistent_with_other_event_types(
        self, captured_events,
    ):
        # All timestamped event types on this run should be stamped from
        # the same clock (server time.time()), so their values are
        # directly comparable without any cross-clock correction.  This
        # doesn't prove WHICH clock, but it does prove task_text_delta is
        # no longer the odd one out relative to task_tool_call /
        # task_progress, which is the actual bug.
        block = _task()
        await task_executor.execute_task_block(block, run_id="run-b3b")

        by_type = {}
        for e in captured_events:
            if "ts" in e:
                by_type.setdefault(e["type"], []).append(e["ts"])

        assert "task_text_delta" in by_type
        assert "task_tool_call" in by_type
        assert "task_progress" in by_type

        all_ts = [t for ts_list in by_type.values() for t in ts_list]
        # Every ts in one execute_task_block call happens within a very
        # short wall-clock window — sanity check they're all close
        # together (same run, same clock), not e.g. one being epoch-0
        # or a completely different scale (ms vs s).
        assert max(all_ts) - min(all_ts) < 5.0


class TestTaskProgressSourceTagging:
    """B8a contract: a model-authored progress note is tagged
    source="model"; a tool-derived one is not.  The frontend's
    accumulateLive relies on this to keep the model note sticky instead
    of letting the very next tool call overwrite it within a second or
    two."""

    @pytest.mark.asyncio
    async def test_model_authored_note_is_tagged_source_model(
        self, captured_events,
    ):
        block = _task()
        await task_executor.execute_task_block(block, run_id="run-b8a")

        progress_events = [
            e for e in captured_events if e.get("type") == "task_progress"
        ]
        model_notes = [e for e in progress_events if e.get("source") == "model"]
        assert model_notes, (
            "expected at least one task_progress event tagged "
            "source='model' from the <progress note=.../> tag"
        )
        assert model_notes[0]["note"] == "reviewed 3/10"

    @pytest.mark.asyncio
    async def test_tool_derived_note_is_not_tagged_source_model(
        self, captured_events,
    ):
        block = _task()
        await task_executor.execute_task_block(block, run_id="run-b8a-2")

        progress_events = [
            e for e in captured_events if e.get("type") == "task_progress"
        ]
        tool_notes = [e for e in progress_events if e.get("source") != "model"]
        assert tool_notes, "expected a tool-derived task_progress event"
        assert any("run_shell_command" in (e.get("note") or "") for e in tool_notes)

    @pytest.mark.asyncio
    async def test_both_note_kinds_present_in_one_run(self, captured_events):
        # Reproduces the exact interleaving that caused the bug: a model
        # note followed shortly after by a tool note.  Order in the
        # emitted stream matters for the frontend's last-model-wins /
        # last-tool-updates-age-label split, so pin both are present and
        # in the order the executor actually yields them.
        block = _task()
        await task_executor.execute_task_block(block, run_id="run-b8a-3")

        progress_events = [
            e for e in captured_events if e.get("type") == "task_progress"
        ]
        sources = [e.get("source") for e in progress_events]
        # The tool call happens BEFORE the second text delta carrying the
        # <progress> tag in this fixture's chunk order, so the tool note
        # should appear first.
        assert sources[0] != "model"
        assert "model" in sources
