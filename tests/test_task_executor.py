"""Tests for the task executor's validation and scope handling.

Full integration (actual model execution) is skipped — those tests
are covered by the existing delegate integration suites, since the
executor delegates to the same StreamingToolExecutor.  These tests
focus on the executor's own logic: validation, error surfacing.
"""

import pytest
from app.models.task_card import Block, TaskScope
from app.agents.task_executor import (
    validate_root_for_slice_c,
    TaskExecutorError,
)


class TestValidation:
    def test_rejects_repeat_block(self):
        block = Block(
            block_type="repeat",
            name="loop",
            repeat_mode="count",
            repeat_count=5,
            body=[Block(block_type="task", name="x", instructions="y")],
        )
        with pytest.raises(TaskExecutorError) as exc:
            validate_root_for_slice_c(block)
        assert "Slice D" in str(exc.value)

    def test_rejects_parallel_block(self):
        block = Block(block_type="parallel", name="p")
        with pytest.raises(TaskExecutorError):
            validate_root_for_slice_c(block)

    def test_rejects_task_without_instructions(self):
        block = Block(block_type="task", name="empty", instructions="")
        with pytest.raises(TaskExecutorError) as exc:
            validate_root_for_slice_c(block)
        assert "non-empty instructions" in str(exc.value)

    def test_rejects_task_with_whitespace_only_instructions(self):
        block = Block(block_type="task", name="ws", instructions="   \n  ")
        with pytest.raises(TaskExecutorError):
            validate_root_for_slice_c(block)

    def test_accepts_valid_task(self):
        block = Block(
            block_type="task",
            name="ok",
            instructions="do the thing",
            scope=TaskScope(tools=["render_diagram"]),
        )
        # Should not raise
        validate_root_for_slice_c(block)

    def test_accepts_task_without_scope(self):
        block = Block(
            block_type="task", name="ok",
            instructions="do the thing",
        )
        validate_root_for_slice_c(block)  # should not raise
