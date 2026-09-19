"""task_card_launch — let the model start a saved task card's run.

WHY THIS EXISTS
---------------
Every other task-card tool (stage/list/read/write/validate) authors or
inspects a card; none START one.  Launch was modelled as the human's
commit point — ``_launch_run_for_card`` stamps ``launch_context=
"interactive"`` with the comment "every caller of this helper is a user
action", and the stage tool tells the model "the USER launches it".  That
left a real gap: a fully-authored, non-escalating card (one that only
reads and writes inside the floor safe-write set) still could not be run
without a human clicking Run, even though launching it commits no
privilege the human had not already conceded by the floor policy.

THE THRESHOLD (the one knob)
----------------------------
``MODEL_LAUNCH_POLICY`` is the single decision point for what the model
may launch on its own.  It is deliberately isolated so widening the gate
is a one-line, reviewable change rather than surgery:

  * ``"non_escalating"`` (default) — launch only a card whose blocks
    request NO privilege escalation: no per-task shell grants, no writes
    outside the floor safe-write set (``.ziya/``, ``/tmp/``).  An
    escalating card is refused and the human must press Run, because
    launch is where a human commits a run's use of a signed privilege.
  * ``"signed"`` — also launch an escalating card IF every escalating
    block is already authorized by a valid ``ziya-approve`` signature.
    The signature authorised that the privilege MAY be held; this policy
    treats it as also authorising the model to spend it.  Defensible, but
    it moves the per-run human commit onto the model — opt in knowingly.
  * ``"always"`` — launch anything.  Not recommended; present so the
    escape hatch is explicit rather than a fork someone adds ad hoc.

To change the threshold, change ``MODEL_LAUNCH_POLICY`` (and nothing
else).  ``_model_launch_gate`` below reads it and is the only consumer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.task_card_tools import _resolve_card_storage
from app.utils.logging_utils import logger


# ── the one knob ────────────────────────────────────────────────────────
MODEL_LAUNCH_POLICY = "non_escalating"
"""What the model may launch unattended.  One of ``"non_escalating"``
(default), ``"signed"``, ``"always"``.  See module docstring."""


def _escalation_rows_for(card, project) -> List[Dict[str, Any]]:
    """The card's per-leaf escalation rows, via the SAME derivation the
    editor and the launch UI use — so the gate can never disagree with
    what the human is shown.  Rows exist only for blocks that escalate;
    an empty list means the card is non-escalating."""
    from app.api.task_cards import _escalation_rows

    deck_scope = (
        getattr(getattr(project, "settings", None), "taskScope", None)
        if project else None
    )
    rows, _staged = _escalation_rows(
        card, deck_scope,
        project_id=getattr(project, "id", "") or "",
        card_id=card.id,
        check_approvals=True,   # populate `authorized` for the "signed" policy
    )
    return rows


def _model_launch_gate(rows: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Apply ``MODEL_LAUNCH_POLICY`` to a card's escalation rows.

    Returns ``(allowed, reason)``.  ``reason`` is empty when allowed and
    otherwise names exactly why the model may not launch, so the tool can
    tell the user what to do instead.
    """
    escalating = [r for r in rows if r.get("hasEscalation")]
    if not escalating:
        return True, ""

    if MODEL_LAUNCH_POLICY == "always":
        return True, ""

    if MODEL_LAUNCH_POLICY == "signed":
        unsigned = [r for r in escalating if r.get("needsSignature")]
        if not unsigned:
            return True, ""
        names = ", ".join(r.get("name") or r.get("blockId") for r in unsigned)
        return False, (
            f"{len(unsigned)} escalating block(s) are not signed "
            f"({names}); under the 'signed' launch policy the model may "
            f"launch an escalating card only once every escalating block "
            f"is authorized.  The user must sign them, then launch."
        )

    # "non_escalating" (default) — any escalation at all blocks the model.
    names = ", ".join(r.get("name") or r.get("blockId") for r in escalating)
    return False, (
        f"This card has {len(escalating)} privilege-escalating block(s) "
        f"({names}).  The model launch policy is '{MODEL_LAUNCH_POLICY}', "
        f"so the model may not launch it — launch is where a human commits "
        f"a run's use of a signed privilege.  Ask the user to press Run on "
        f"the card's tile (or launch it from the Task Cards library)."
    )


class TaskCardLaunchInput(BaseModel):
    """Input schema for task_card_launch."""
    card_id: str = Field(
        ..., description="The id of the task card to launch (from "
                         "task_card_list).")
    parameter_overrides: Optional[Dict[str, Any]] = Field(
        None, description="Optional run-scoped parameter overrides, same "
                          "shape the launch endpoint accepts.")


class TaskCardLaunchTool(BaseMCPTool):
    """Launch a saved, non-escalating task card and return its run id."""

    name: str = "task_card_launch"
    description: str = (
        "[DIRECT] Launch a SAVED task card — create a run and start it "
        "executing in the background — and return the run id immediately "
        "(poll the task-run views for status and the final artifact).  "
        "Use task_card_list to find the card_id.  The model may launch "
        "only a NON-ESCALATING card (no shell grants, no writes outside "
        ".ziya//tmp); an escalating card is refused and must be launched "
        "by the user pressing Run, since launch is where a human commits "
        "the use of a signed privilege.  This does not stage a tile — it "
        "starts the run; use task_card_stage when you want the user to "
        "launch."
    )
    InputSchema = TaskCardLaunchInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        card_id = kwargs.get("card_id")
        if not card_id:
            return {"error": True, "message": "card_id is required."}

        res = _resolve_card_storage()
        if not res["ok"]:
            return {"error": True, "message": res["error"]}
        storage = res["storage"]
        project = res["project"]
        project_id = res["project_id"]

        card = storage.get(card_id)
        if not card:
            return {"error": True,
                    "message": f"Task card not found: {card_id}"}

        # Policy gate — the single threshold, applied before anything runs.
        try:
            rows = _escalation_rows_for(card, project)
        except Exception as e:  # noqa: BLE001 — a gate fault must FAIL CLOSED
            logger.warning(f"task_card_launch: escalation check failed: {e}")
            return {"error": True,
                    "message": ("Could not determine the card's escalation "
                                f"status ({e}); refusing to launch.  The "
                                "user can launch it from the tile.")}
        allowed, reason = _model_launch_gate(rows)
        if not allowed:
            return {"success": False, "launched": False,
                    "card_id": card_id, "name": card.name,
                    "policy": MODEL_LAUNCH_POLICY, "reason": reason,
                    "message": reason}

        from app.api.task_cards import _launch_run_for_card
        from app.context import get_conversation_id_or_none
        chat_id = get_conversation_id_or_none()
        try:
            run = await _launch_run_for_card(
                project_id=project_id, card_id=card_id,
                source_conversation_id=chat_id,
                parameter_overrides=kwargs.get("parameter_overrides") or {},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"task_card_launch: launch failed: {e}")
            return {"error": True, "message": f"Launch failed: {e}"}

        run_id = getattr(run, "id", None)
        status = getattr(run, "status", None)

        # Bind the run into the launching conversation.  The chat renders
        # task monitors from TaskBindings, not from runs — every human
        # launch path (tile Run button, /goal, the bindings endpoint)
        # records one, and without it a model-launched run executes
        # invisibly.  Same unanchored binding task_card_stage uses; the
        # frontend re-fetches bindings on this tool's result.
        binding_id = None
        if chat_id and run_id:
            try:
                from app.storage.task_bindings import TaskBindingStorage
                from app.utils.paths import get_project_dir
                binding = TaskBindingStorage(
                    get_project_dir(project_id)
                ).create(chat_id=chat_id, card_id=card_id, run_id=run_id,
                         anchor_message_id=None)
                binding_id = binding.id
            except Exception as e:  # noqa: BLE001
                logger.warning(f"task_card_launch: binding failed: {e}")

        logger.info(
            f"🚀 task_card_launch: card {card_id[:8]} '{card.name}' "
            f"→ run {str(run_id)[:8]} ({status})"
            + (f", bound to chat {chat_id[:8]}" if binding_id
               else ", no conversation binding"))
        where = ("Its monitor tile is in this conversation."
                 if binding_id else
                 "No conversation context was available to bind it; "
                 "find it under the project's task runs.")
        return {
            "success": True, "launched": True,
            "card_id": card_id, "name": card.name,
            "run_id": run_id, "status": str(status) if status else None,
            "binding_id": binding_id,
            "message": (f"Launched '{card.name}' as run {run_id}.  It is "
                        f"executing in the background.  {where}  Poll the "
                        "task-run views for status and the final artifact."),
        }
