"""
Tests for app/utils/goal_synthesis.py — goal-to-task-card synthesis.
"""

import pytest
from app.utils.goal_synthesis import synthesize_goal_card, _build_instructions, GOAL_TAG


class TestSynthesizeGoalCard:
    """Test the main synthesis function."""

    def test_basic_goal(self):
        """Simple goal text produces a valid TaskCardCreate."""
        result = synthesize_goal_card("fix all lint errors")

        assert result.name == "Goal: fix all lint errors"
        assert GOAL_TAG in result.tags
        assert "auto-synthesized" in result.tags
        assert result.root.block_type == "until"
        assert result.root.until_condition == "fix all lint errors"
        assert result.root.until_mode == "model"
        assert result.root.until_max == 15  # default cap

    def test_until_block_wraps_task_block(self):
        """The root Until block contains exactly one Task block."""
        result = synthesize_goal_card("migrate database")

        assert len(result.root.body) == 1
        task = result.root.body[0]
        assert task.block_type == "task"
        assert task.name == "Goal execution"
        assert "migrate database" in task.instructions

    def test_instructions_contain_objective(self):
        """Task instructions include the OBJECTIVE header."""
        result = synthesize_goal_card("deploy to staging")

        task = result.root.body[0]
        assert "OBJECTIVE: deploy to staging" in task.instructions

    def test_conversation_context_included(self):
        """When context is provided, it appears in instructions."""
        result = synthesize_goal_card(
            "fix the auth bug",
            conversation_context="We identified a null pointer in login.py line 42",
        )

        task = result.root.body[0]
        assert "CONTEXT (from conversation before this goal was set):" in task.instructions
        assert "null pointer in login.py line 42" in task.instructions

    def test_no_context_no_context_section(self):
        """When context is None, no CONTEXT section appears."""
        result = synthesize_goal_card("run tests")

        task = result.root.body[0]
        assert "CONTEXT" not in task.instructions

    def test_empty_context_no_context_section(self):
        """Whitespace-only context is treated as no context."""
        result = synthesize_goal_card("run tests", conversation_context="   ")

        task = result.root.body[0]
        assert "CONTEXT" not in task.instructions

    def test_custom_iteration_cap(self):
        """iteration_cap overrides the default max."""
        result = synthesize_goal_card("big migration", iteration_cap=5)

        assert result.root.until_max == 5

    def test_long_goal_text_truncated_in_name(self):
        """Goal text > 80 chars is truncated in the card name."""
        long_goal = "x" * 100
        result = synthesize_goal_card(long_goal)

        assert len(result.name) < 100
        assert result.name.endswith("…")
        # But the condition retains full text
        assert result.root.until_condition == long_goal

    def test_empty_goal_raises(self):
        """Empty/whitespace goal raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            synthesize_goal_card("")

        with pytest.raises(ValueError, match="cannot be empty"):
            synthesize_goal_card("   ")

    def test_scope_passed_through(self):
        """Explicit scope is attached to the inner task block."""
        from app.models.task_card import TaskScope, ScopeEntry

        scope = TaskScope(
            paths=[ScopeEntry(path="src/", is_dir=True, read=True, write=True)],
            tools=["mcp_run_shell_command"],
        )
        result = synthesize_goal_card("fix lint", scope=scope)

        task = result.root.body[0]
        assert task.scope is not None
        assert task.scope.tools == ["mcp_run_shell_command"]
        assert len(task.scope.paths) == 1


class TestBuildInstructions:
    """Test the instruction builder helper."""

    def test_minimal(self):
        """Goal text only — produces objective + general instructions."""
        result = _build_instructions("fix bug", None)

        assert "OBJECTIVE: fix bug" in result
        assert "Work toward this objective" in result

    def test_with_context(self):
        """Goal + context — both sections present."""
        result = _build_instructions("fix bug", "stack trace shows NPE")

        assert "OBJECTIVE: fix bug" in result
        assert "stack trace shows NPE" in result
        assert "CONTEXT" in result
