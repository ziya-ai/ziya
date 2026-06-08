"""
Goal synthesis — convert a one-line goal statement into a launchable
Task Card structure.

The /goal command lets users type a single objective:
    /goal fix all TypeScript errors in frontend/src

This module synthesizes that into a TaskCardCreate payload:
    Until(condition=<goal_text>, max=<cap>):
        Task(instructions=<goal_text + context>)

The resulting card is functionally identical to one authored in the
card editor — it plugs directly into the existing launch, execute,
observe, and cancel infrastructure.
"""

from typing import Optional

from ..models.task_card import Block, TaskCardCreate, TaskScope


# Defaults for synthesized goal cards.
DEFAULT_ITERATION_CAP = 15
GOAL_TAG = "goal"


def synthesize_goal_card(
    goal_text: str,
    conversation_context: Optional[str] = None,
    iteration_cap: int = DEFAULT_ITERATION_CAP,
    scope: Optional[TaskScope] = None,
) -> TaskCardCreate:
    """Auto-construct a TaskCardCreate from a goal statement.

    The goal text serves as BOTH the until-condition AND the base
    instructions (augmented with conversation context if provided).

    Parameters
    ----------
    goal_text : str
        The user's stated objective, e.g. "migrate to Pydantic v2
        with all tests passing".
    conversation_context : str, optional
        Summary of recent conversation history to give the agent
        context about what was already discussed.
    iteration_cap : int
        Maximum iterations before the Until block terminates.
    scope : TaskScope, optional
        Explicit scope for the task block.  When None, the task
        inherits the default scope (all tools, cwd-relative paths).

    Returns
    -------
    TaskCardCreate
        A creation payload ready to pass to TaskCardStorage.create().
    """
    if not goal_text.strip():
        raise ValueError("Goal text cannot be empty")

    # Build task instructions
    instructions = _build_instructions(goal_text, conversation_context)

    # Inner task block — the actual work unit executed each iteration
    task_block = Block(
        block_type="task",
        name="Goal execution",
        instructions=instructions,
        scope=scope,
    )

    # Outer until block — re-evaluates the condition after each iteration
    until_block = Block(
        block_type="until",
        name="Goal condition",
        until_mode="model",
        # The model-evaluated until-condition is unreliable for
        # action-phrased goals ("add X", "fix Y") because it answers
        # "have these actions been performed?" — which is wrong in
        # the vacuously-satisfied case (no instances found, nothing
        # to do).  We rely on Artifact.self_assessment instead;
        # see design/goal-exit-conditions.md.
        until_condition="",
        until_max=iteration_cap,
        body=[task_block],
    )

    # Truncate the card name for readability
    display_name = goal_text[:80]
    if len(goal_text) > 80:
        display_name += "…"

    return TaskCardCreate(
        name=f"Goal: {display_name}",
        description=f"Auto-synthesized from /goal command",
        root=until_block,
        tags=[GOAL_TAG, "auto-synthesized"],
    )


def _build_instructions(goal_text: str, context: Optional[str]) -> str:
    """Compose the task instructions from goal + optional context."""
    parts = [
        f"OBJECTIVE: {goal_text}",
        "",
        "Work toward this objective. Use tools, edit files, run commands, "
        "and iterate as needed. When the objective is fully met, stop and "
        "report what was accomplished.",
    ]

    if context and context.strip():
        parts.extend([
            "",
            "CONTEXT (from conversation before this goal was set):",
            context.strip(),
        ])

    return "\n".join(parts)
