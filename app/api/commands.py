"""
Shared command dispatch endpoint.

Both the CLI and the web frontend route slash commands through this
endpoint so the implementation is surface-agnostic.  A user typing
"/goal fix all lint errors" in the web compose box or the CLI terminal
hits the same code path.

Routes:
  POST /api/v1/commands — dispatch a slash command
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

from ..utils.logging_utils import logger
from ..utils.paths import get_ziya_home, get_project_dir
from ..context import get_project_root_or_none
from ..storage.projects import ProjectStorage
from ..storage.task_cards import TaskCardStorage
from ..storage.task_bindings import TaskBindingStorage
from ..storage.task_runs import TaskRunStorage

router = APIRouter(prefix="/api/v1", tags=["commands"])


class CommandRequest(BaseModel):
    """Incoming slash command from any surface."""
    command: str = Field(..., description="Command name without slash (e.g. 'goal')")
    args: str = Field("", description="Everything after the command name")
    conversation_id: Optional[str] = Field(
        None, description="Active conversation, used for binding goals to chats"
    )
    context_summary: Optional[str] = Field(
        None, description="Optional recent conversation summary for goal context"
    )


class CommandResponse(BaseModel):
    """Response from a dispatched command."""
    type: str = Field(..., description="Response type (e.g. 'goal_launched', 'goal_status')")
    message: str = Field("", description="Human-readable result")
    data: Dict[str, Any] = Field(default_factory=dict, description="Structured payload")


@router.post("/commands", response_model=CommandResponse)
async def dispatch_command(body: CommandRequest, request: Request) -> CommandResponse:
    """Dispatch a slash command.

    Currently supported commands:
      - goal <text>       — synthesize + launch a goal card
      - goal status       — show current goal state
      - goal pause        — cancel the active goal run
      - goal clear        — cancel and unbind the goal
      - goal resume       — relaunch a paused goal
    """
    cmd = body.command.lower().strip()

    if cmd == "goal":
        return await _handle_goal_command(body, request)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown command: {cmd}. Supported: goal",
        )


# ── Goal command handler ──────────────────────────────────────────


async def _handle_goal_command(body: CommandRequest, request: Request) -> CommandResponse:
    """Route /goal subcommands."""
    args = body.args.strip()

    # Subcommands: status, pause, resume, clear
    first_word = args.split(maxsplit=1)[0].lower() if args else ""

    if not args:
        return CommandResponse(
            type="error",
            message="Usage: /goal <objective> or /goal status|pause|resume|clear",
        )

    if first_word == "status":
        return await _goal_status(body)
    elif first_word == "pause":
        return await _goal_pause(body)
    elif first_word == "resume":
        return await _goal_resume(body)
    elif first_word == "clear":
        return await _goal_clear(body)
    else:
        return await _goal_create(body, request)


async def _goal_create(body: CommandRequest, request: Request) -> CommandResponse:
    """Synthesize a task card from goal text and launch it."""
    from ..utils.goal_synthesis import synthesize_goal_card
    from .task_cards import _launch_run_for_card

    goal_text = body.args.strip()

    # Resolve project
    project_root = get_project_root_or_none()
    if not project_root:
        raise HTTPException(status_code=400, detail="No project context available")

    ps = ProjectStorage(get_ziya_home())
    project = ps.get_by_path(project_root)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_dir = get_project_dir(project.id)

    # Synthesize the card
    card_create = synthesize_goal_card(
        goal_text=goal_text,
        conversation_context=body.context_summary,
    )

    # Persist the card (with source="goal")
    card_storage = TaskCardStorage(project_dir)
    saved_card = card_storage.create(card_create, source="goal")
    logger.info(f"🎯 GOAL: Synthesized card {saved_card.id[:8]} for: {goal_text[:60]}")

    # Launch the run
    run = await _launch_run_for_card(
        project_id=project.id,
        card_id=saved_card.id,
        source_conversation_id=body.conversation_id,
    )

    # Bind to conversation if we have one
    binding_id = None
    if body.conversation_id:
        binding_storage = TaskBindingStorage(project_dir)
        binding = binding_storage.create(
            chat_id=body.conversation_id,
            card_id=saved_card.id,
            run_id=run.id,
            anchor_message_id=None,
        )
        binding_id = binding.id
        logger.info(
            f"🎯 GOAL: Bound to conversation {body.conversation_id[:8]} "
            f"→ binding {binding.id[:8]}"
        )

    return CommandResponse(
        type="goal_launched",
        message=f"🎯 Goal set: {goal_text}",
        data={
            "card_id": saved_card.id,
            "run_id": run.id,
            "binding_id": binding_id,
            "goal_text": goal_text,
        },
    )


async def _goal_status(body: CommandRequest) -> CommandResponse:
    """Return the status of the active goal for this conversation."""
    binding = await _find_active_goal_binding(body.conversation_id)
    if not binding:
        return CommandResponse(
            type="goal_status",
            message="No active goal in this conversation.",
            data={"active": False},
        )

    project_root = get_project_root_or_none()
    ps = ProjectStorage(get_ziya_home())
    project = ps.get_by_path(project_root)
    run_storage = TaskRunStorage(get_project_dir(project.id))
    run = run_storage.get(binding["run_id"])

    if not run:
        return CommandResponse(
            type="goal_status",
            message="Goal run not found.",
            data={"active": False},
        )

    return CommandResponse(
        type="goal_status",
        message=f"🎯 Goal: {binding['goal_text']}\nStatus: {run.status}",
        data={
            "active": run.status == "running",
            "status": run.status,
            "card_id": binding["card_id"],
            "run_id": binding["run_id"],
            "goal_text": binding["goal_text"],
        },
    )


async def _goal_pause(body: CommandRequest) -> CommandResponse:
    """Pause (cancel) the active goal run."""
    binding = await _find_active_goal_binding(body.conversation_id)
    if not binding:
        return CommandResponse(
            type="goal_pause",
            message="No active goal to pause.",
        )

    project_root = get_project_root_or_none()
    ps = ProjectStorage(get_ziya_home())
    project = ps.get_by_path(project_root)
    run_storage = TaskRunStorage(get_project_dir(project.id))
    run = run_storage.get(binding["run_id"])

    if not run or run.status != "running":
        return CommandResponse(
            type="goal_pause",
            message="Goal is not currently running.",
        )

    run_storage.request_cancel(binding["run_id"])
    return CommandResponse(
        type="goal_paused",
        message=f"⏸ Goal paused: {binding['goal_text']}",
        data={"run_id": binding["run_id"]},
    )


async def _goal_resume(body: CommandRequest) -> CommandResponse:
    """Resume a paused/cancelled goal by relaunching it."""
    from .task_cards import _launch_run_for_card

    binding = await _find_active_goal_binding(body.conversation_id)
    if not binding:
        return CommandResponse(
            type="goal_resume",
            message="No goal to resume.",
        )

    project_root = get_project_root_or_none()
    ps = ProjectStorage(get_ziya_home())
    project = ps.get_by_path(project_root)
    run_storage = TaskRunStorage(get_project_dir(project.id))
    old_run = run_storage.get(binding["run_id"])

    if old_run and old_run.status == "running":
        return CommandResponse(
            type="goal_resume",
            message="Goal is already running.",
        )

    # Relaunch the same card
    run = await _launch_run_for_card(
        project_id=project.id,
        card_id=binding["card_id"],
        source_conversation_id=body.conversation_id,
    )

    # Update the binding to point to the new run
    binding_storage = TaskBindingStorage(get_project_dir(project.id))
    binding_storage.update_run_id(
        body.conversation_id, binding["binding_id"], run.id,
    )

    return CommandResponse(
        type="goal_resumed",
        message=f"▶️ Goal resumed: {binding['goal_text']}",
        data={"run_id": run.id, "card_id": binding["card_id"]},
    )


async def _goal_clear(body: CommandRequest) -> CommandResponse:
    """Cancel and unbind the active goal."""
    binding = await _find_active_goal_binding(body.conversation_id)
    if not binding:
        return CommandResponse(
            type="goal_clear",
            message="No active goal to clear.",
        )

    project_root = get_project_root_or_none()
    ps = ProjectStorage(get_ziya_home())
    project = ps.get_by_path(project_root)
    project_dir = get_project_dir(project.id)

    # Cancel the run if active
    run_storage = TaskRunStorage(project_dir)
    run = run_storage.get(binding["run_id"])
    if run and run.status == "running":
        run_storage.request_cancel(binding["run_id"])

    # Remove the binding
    binding_storage = TaskBindingStorage(project_dir)
    binding_storage.delete(body.conversation_id, binding["binding_id"])

    return CommandResponse(
        type="goal_cleared",
        message=f"✕ Goal cleared: {binding['goal_text']}",
        data={"card_id": binding["card_id"]},
    )


# ── Helpers ───────────────────────────────────────────────────────


async def _find_active_goal_binding(conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Find the most recent goal-sourced binding for a conversation.

    Returns a dict with card_id, run_id, binding_id, goal_text or None.
    """
    if not conversation_id:
        return None

    project_root = get_project_root_or_none()
    if not project_root:
        return None

    ps = ProjectStorage(get_ziya_home())
    project = ps.get_by_path(project_root)
    if not project:
        return None

    project_dir = get_project_dir(project.id)
    binding_storage = TaskBindingStorage(project_dir)
    bindings = binding_storage.list_for_chat(conversation_id)

    if not bindings:
        return None

    # Find the most recent binding whose card has source="goal"
    card_storage = TaskCardStorage(project_dir)
    for binding in reversed(bindings):
        card = card_storage.get(binding.card_id)
        if card and card.source == "goal":
            goal_text = card.name
            if goal_text.startswith("Goal: "):
                goal_text = goal_text[6:]
            return {
                "card_id": binding.card_id,
                "run_id": binding.run_id,
                "binding_id": binding.id,
                "goal_text": goal_text,
            }

    return None
