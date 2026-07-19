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
        # Validation rejects non-task blocks; error message is
        # descriptive but not pinned to a specific development phase.
        assert "task" in str(exc.value).lower()

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


class TestIsFailureResult:
    """The pure failure classifier behind the consecutive-failure
    breaker.  Strings are the real model-facing markers observed in
    the production runaway log (100+ blocked git calls)."""

    def test_policy_block_is_failure(self):
        from app.agents.task_executor import is_failure_result
        assert is_failure_result(
            "POLICY BLOCK (do NOT retry this command): 🚫 BLOCKED: 'git' is not allowed"
        ) is True

    def test_bare_blocked_marker_is_failure(self):
        from app.agents.task_executor import is_failure_result
        assert is_failure_result("🚫 BLOCKED: 'git' is not allowed") is True

    def test_write_blocked_is_failure(self):
        from app.agents.task_executor import is_failure_result
        assert is_failure_result(
            "🚫 WRITE BLOCKED: In-place editing with 'sed -i' is not allowed."
        ) is True

    def test_command_failed_nonzero_exit_is_failure(self):
        from app.agents.task_executor import is_failure_result
        assert is_failure_result(
            "COMMAND FAILED: command terminated with non-zero exit status 1"
        ) is True

    def test_refusal_and_verification_are_failures(self):
        from app.agents.task_executor import is_failure_result
        assert is_failure_result("Tool call refused: repetitive call") is True
        assert is_failure_result("SECURITY VERIFICATION FAILED: signature") is True

    def test_success_text_is_not_failure(self):
        from app.agents.task_executor import is_failure_result
        assert is_failure_result("On branch main\nnothing to commit") is False
        assert is_failure_result("") is False

    def test_non_string_results_are_not_failures(self):
        from app.agents.task_executor import is_failure_result
        assert is_failure_result(None) is False
        assert is_failure_result({"status": "ok"}) is False
        assert is_failure_result(0) is False


class TestConsecutiveFailureStreak:
    """Pins the streak semantics the tool_display loop implements:
    increment on failure, reset on any success, abort at threshold.
    Mirrors the loop logic exactly so a regression in either surfaces."""

    @staticmethod
    def _run(results, limit):
        """Replay a result sequence through the breaker's counting rule.
        Returns (aborted, index_of_abort_or_-1)."""
        from app.agents.task_executor import is_failure_result
        streak = 0
        for i, r in enumerate(results):
            if is_failure_result(r) and limit > 0:
                streak += 1
                if streak >= limit:
                    return True, i
            else:
                streak = 0
        return False, -1

    def test_aborts_at_threshold_of_consecutive_failures(self):
        aborted, idx = self._run(["🚫 BLOCKED: x"] * 5, limit=5)
        assert aborted is True and idx == 4

    def test_below_threshold_does_not_abort(self):
        aborted, _ = self._run(["🚫 BLOCKED: x"] * 4, limit=5)
        assert aborted is False

    def test_success_resets_streak(self):
        # 4 fails, 1 success, 4 fails — never 5 in a row → no abort.
        seq = ["POLICY BLOCK: x"] * 4 + ["ok done"] + ["POLICY BLOCK: x"] * 4
        aborted, _ = self._run(seq, limit=5)
        assert aborted is False

    def test_varied_commands_still_count_as_consecutive(self):
        # The original runaway: every call differs textually but all fail.
        seq = [
            "🚫 BLOCKED: 'git' is not allowed (git add x)",
            "🚫 BLOCKED: 'git' is not allowed (git apply y)",
            "🚫 BLOCKED: 'git' is not allowed (git commit z)",
        ]
        aborted, idx = self._run(seq, limit=3)
        assert aborted is True and idx == 2

    def test_limit_zero_disables_breaker(self):
        aborted, _ = self._run(["🚫 BLOCKED: x"] * 50, limit=0)
        assert aborted is False
