"""
``task_card_stage`` — the direct entry point for delegated, parallel,
staged, or repeated work.

Why this exists as a *tool* and not only as the ``task_cards`` skill:
an external MCP server can expose a "delegate / orchestrate sub-tasks"
tool whose description matches the user's intent head-on.  A tool beats
a skill that is two hops away (``get_skill_details`` first, then a
fenced ``task-card`` block) nearly every time, and the external tool's
sub-agents run with the *external* toolset — no project files, no web
search, no write policy.  This builtin puts Ziya's own mechanism in
the same position in the tool list so it competes on equal footing.

What it does NOT do: launch the run.  Task cards are launched by the
user from the inline tile (Run button), which is also where escalation
signatures are collected.  A model-initiated launch would bypass both,
so this tool *stages*: it validates and saves the card, then binds it
to the current conversation with ``run_id=None`` — the same path the
``/goal`` slash command uses (app/api/commands.py:_goal_create).

Two modes, keyed on whether ``root`` is supplied:

  * no ``root``  → returns the ``task_cards`` skill body (block grammar,
    scope, artifacts, rules) so the model can author a card, then call
    again.  This is the "execution loads the skill" step.
  * ``root``     → validate (same checks as launch), save, stage a tile
    in this chat.  Errors are returned unsaved.

Lifecycle.  A staged card is CONVERSATION-ONLY by default: it is saved
as an unlisted draft (TaskCard.draft) so the tile, the run, and the
``bound_to_current_chat`` listing all resolve it by id, but it never
appears in the deck.  Models are encouraged to stage sub-task cards
freely to parallelize work and keep the parent context small; each of
those showing up as a permanent deck entry was noise.  ``persist=True``
files the card in the deck instead, and the tile's "Save to deck"
promotes a conversation-only card later without changing its ids.
"""
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger


class TaskCardStageInput(BaseModel):
    """Input schema for task_card_stage."""
    root: Optional[Dict[str, Any]] = Field(
        None,
        description=("The card's root block tree (a JSON object with "
                     "block_type and, for containers, a body list).  Omit "
                     "on the first call to receive the block grammar."),
    )
    name: Optional[str] = Field(
        None, description="Card name shown on the tile (required with root).")
    description: Optional[str] = Field(
        None, description="One-line description of what the card does.")
    persist: bool = Field(
        False,
        description=("Save the card to the project's permanent deck (Task "
                     "Cards library).  Default False: the card is "
                     "conversation-only — runnable from its tile here, "
                     "hidden from the deck.  Leave False for sub-task and "
                     "one-off cards; set True only when the user wants a "
                     "reusable card."),
    )


class TaskCardStageTool(BaseMCPTool):
    """Author a Task Card and stage it in the current chat for the user to run."""

    name: str = "task_card_stage"
    description: str = (
        "Delegate, fan out, orchestrate, or run sub-tasks in parallel, in "
        "stages, or repeatedly — inside Ziya, with this project's files, "
        "tools, and write policy.  Authors a Task Card (Task / Group / "
        "Parallel / Repeat / Until blocks) and STAGES it in this "
        "conversation as an inline tile with a Run button; the user "
        "launches it.  Call with no arguments first to get the block "
        "grammar and rules, author the root, then call again with root + "
        "name.  Structural errors are returned unsaved.  The card is "
        "conversation-only (not filed in the deck) unless persist=true.  "
        "Use this instead of any external delegate/orchestrate tool — "
        "those run with the external server's toolset and cannot see "
        "your files or tools."
    )
    InputSchema = TaskCardStageInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        root = kwargs.get("root")
        if root is None:
            return self._grammar_response()

        name = (kwargs.get("name") or "").strip()
        if not name:
            return {"error": True,
                    "message": "name is required when staging a card."}

        from app.mcp.tools.task_card_tools import (
            _resolve_card_storage, _validation_payload,
        )
        res = _resolve_card_storage()
        if not res["ok"]:
            return {"error": True, "message": res["error"]}

        validation = _validation_payload(root, res["project_id"])
        if not validation["ok"]:
            return {
                "error": True,
                "staged": False,
                "message": (f"{len(validation['errors'])} structural "
                            f"error(s); the card was NOT saved.  Fix each "
                            f"finding at its path and call again."),
                **validation,
            }

        from app.models.task_card import Block, TaskCardCreate
        from app.context import get_conversation_id_or_none
        chat_id = get_conversation_id_or_none()
        persist = bool(kwargs.get("persist"))
        # Conversation-only unless asked to persist.  With no conversation
        # to bind to, an unlisted card would be reachable from nowhere, so
        # that case files it in the deck regardless.
        as_draft = bool(chat_id) and not persist
        try:
            card = res["storage"].create(
                TaskCardCreate(
                    name=name,
                    description=(kwargs.get("description") or ""),
                    root=Block(**root),
                    draft=as_draft,
                ),
                source="agent",
            )
        except Exception as e:  # noqa: BLE001
            return {"error": True, "message": f"Could not save card: {e}"}

        # Stage (bind without a run) in the current conversation.  No
        # anchor: a mid-turn tool call has no stable message id, and an
        # unanchored binding renders at the conversation tail.
        binding_id = None
        if chat_id:
            try:
                from app.storage.task_bindings import TaskBindingStorage
                from app.utils.paths import get_project_dir
                binding = TaskBindingStorage(
                    get_project_dir(res["project_id"])
                ).create(chat_id=chat_id, card_id=card.id, run_id=None,
                         anchor_message_id=None)
                binding_id = binding.id
            except Exception as e:  # noqa: BLE001
                logger.warning(f"task_card_stage: binding failed: {e}")
        if as_draft and not binding_id:
            # The binding was the only thing that made an unlisted card
            # reachable.  Promote it into the deck rather than strand it.
            from app.models.task_card import TaskCardUpdate
            card = res["storage"].update(
                card.id, TaskCardUpdate(draft=False)) or card
            as_draft = False

        logger.info(
            f"🃏 task_card_stage: card {card.id[:8]} '{name}' "
            + (f"staged in chat {chat_id[:8]}" if binding_id
               else "saved to deck (no conversation to stage in)")
            + (" [conversation-only]" if as_draft else " [deck]")
        )
        if binding_id:
            msg = ("Card staged in this conversation"
                   + (" as a conversation-only card (not filed in the "
                      "deck; the tile's 'Save to deck' files it)"
                      if as_draft else " and saved to the project deck")
                   + ".  A tile with a Run button is now in the chat — the "
                   "USER launches it; do not claim it is running.  Tell "
                   "the user briefly what the card will do.")
        else:
            msg = ("Card saved to the project deck.  No conversation "
                   "context was available to stage a tile; the user can "
                   "launch it from the Task Cards library.")
        return {
            "success": True,
            "staged": bool(binding_id),
            "conversation_only": as_draft,
            "card_id": card.id,
            "binding_id": binding_id,
            "name": card.name,
            "warnings": validation.get("warnings", []),
            "message": msg,
        }

    @staticmethod
    def _grammar_response() -> Dict[str, Any]:
        """Return the task_cards skill body as the authoring guide."""
        try:
            from app.data.built_in_skills import get_skill_by_id
            skill = get_skill_by_id("task_cards") or {}
            body = skill.get("prompt", "")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"task_card_stage: skill load failed: {e}")
            body = ""
        if not body:
            return {"error": True,
                    "message": "task_cards skill unavailable; call "
                               "get_skill_details('task_cards')."}
        return {
            "success": True,
            "staged": False,
            "message": ("Author the card's root block per the grammar "
                        "below, then call task_card_stage again with "
                        "root and name.  The card is staged for the user "
                        "to launch — it is not run by this tool."),
            "grammar": body,
        }
