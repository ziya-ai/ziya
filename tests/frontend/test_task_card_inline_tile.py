"""
Tests for the TaskCardInlineTile integration logic.

Since the tile is a React component, these tests verify the backend
contracts it depends on: binding fetch, run status progression, and
the data shapes returned by the API.

Frontend unit tests for the component itself live in
frontend/src/components/TaskCard/__tests__/.
"""

import pytest
import time
from pathlib import Path
from unittest.mock import MagicMock

from app.models.task_binding import TaskBinding
from app.models.task_run import TaskRun, TaskRunBlockState, IterationSummary


class TestBindingDataShape:
    """Verify TaskBinding model produces the shape the frontend expects."""

    def test_binding_serializes_anchor_message_id(self):
        binding = TaskBinding(
            id="b1",
            chat_id="chat-1",
            card_id="card-1",
            run_id="run-1",
            anchor_message_id="msg-42",
            created_at=1000,
        )
        data = binding.model_dump()
        assert data["anchor_message_id"] == "msg-42"
        assert data["run_id"] == "run-1"
        assert data["chat_id"] == "chat-1"

    def test_binding_null_anchor(self):
        binding = TaskBinding(
            id="b2",
            chat_id="chat-1",
            card_id="card-1",
            run_id="run-2",
            anchor_message_id=None,
            created_at=1000,
        )
        data = binding.model_dump()
        assert data["anchor_message_id"] is None


class TestRunStatusForTile:
    """Verify TaskRun serialization matches what the tile component polls."""

    def test_running_run_shape(self):
        run = TaskRun(
            id="r1",
            card_id="c1",
            status="running",
            cancel_requested=False,
            block_states={
                "blk1": TaskRunBlockState(
                    block_id="blk1",
                    block_type="repeat",
                    status="running",
                    iteration_summaries=[
                        IterationSummary(index=0, status="passed", duration_ms=100, tokens=50),
                        IterationSummary(index=1, status="failed", duration_ms=200, tokens=80,
                                         signature="abc123"),
                    ],
                )
            },
            created_at=1000,
            updated_at=1001,
        )
        data = run.model_dump()
        assert data["status"] == "running"
        assert data["cancel_requested"] is False
        summaries = data["block_states"]["blk1"]["iteration_summaries"]
        assert len(summaries) == 2
        assert summaries[0]["status"] == "passed"
        assert summaries[1]["signature"] == "abc123"

    def test_done_run_with_artifact(self):
        from app.models.task_card import Artifact
        artifact = Artifact(
            summary="All tests passed",
            tokens=500,
            tool_calls=3,
            duration_ms=12000,
            created_at=time.time(),
        )
        run = TaskRun(
            id="r2",
            card_id="c1",
            status="done",
            cancel_requested=False,
            artifact=artifact,
            block_states={},
            created_at=1000,
            updated_at=2000,
        )
        data = run.model_dump()
        assert data["status"] == "done"
        assert data["artifact"]["summary"] == "All tests passed"
        assert data["artifact"]["tokens"] == 500
        assert data["artifact"]["tool_calls"] == 3
        assert data["artifact"]["duration_ms"] == 12000

    def test_failed_run_with_error(self):
        run = TaskRun(
            id="r3",
            card_id="c1",
            status="failed",
            error="Connection timeout",
            cancel_requested=False,
            block_states={},
            created_at=1000,
            updated_at=2000,
        )
        data = run.model_dump()
        assert data["status"] == "failed"
        assert data["error"] == "Connection timeout"

    def test_cancelled_run(self):
        run = TaskRun(
            id="r4",
            card_id="c1",
            status="cancelled",
            cancel_requested=True,
            block_states={},
            created_at=1000,
            updated_at=2000,
        )
        data = run.model_dump()
        assert data["status"] == "cancelled"
        assert data["cancel_requested"] is True


class TestIterationCountsForTile:
    """The tile derives pass/fail counts from block_states. Verify shapes."""

    def test_multiple_blocks_iteration_counts(self):
        run = TaskRun(
            id="r5",
            card_id="c1",
            status="done",
            cancel_requested=False,
            block_states={
                "repeat1": TaskRunBlockState(
                    block_id="repeat1",
                    block_type="repeat",
                    status="done",
                    iteration_summaries=[
                        IterationSummary(index=i, status="passed", duration_ms=100, tokens=10)
                        for i in range(5)
                    ] + [
                        IterationSummary(index=5, status="failed", duration_ms=50, tokens=5,
                                         signature="deadbeef"),
                    ],
                ),
                "task1": TaskRunBlockState(
                    block_id="task1",
                    block_type="task",
                    status="done",
                    iteration_summaries=[],
                ),
            },
            created_at=1000,
            updated_at=2000,
        )
        # The tile computes: iterate all block_states, sum iteration_summaries
        data = run.model_dump()
        total = 0
        passed = 0
        failed = 0
        for state in data["block_states"].values():
            for s in state["iteration_summaries"]:
                total += 1
                if s["status"] == "passed":
                    passed += 1
                if s["status"] == "failed":
                    failed += 1
        assert total == 6
        assert passed == 5
        assert failed == 1
