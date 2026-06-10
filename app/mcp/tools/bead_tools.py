"""
Bead MCP tools — silent task-tree tracking.

These tools are marked is_internal=True so their output never renders
in the user-facing stream.  The model calls them as part of its
internal workflow management; users see beads only when they explicitly
open the bead inspector UI or use a /beads slash command.

Tools:
  - bead_create: fork a new subtask from the current active bead
  - bead_complete: mark current active bead as done, resume parent
  - bead_status: view the current bead tree (model-internal introspection)
"""
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger


# ---------------------------------------------------------------------------
# Shared ephemeral guard
# ---------------------------------------------------------------------------

_EPHEMERAL_SKIP = {"ok": True, "skipped": True, "reason": "ephemeral"}


def _is_ephemeral_context() -> bool:
    """Check if the current context is ephemeral (beads won't persist).

    Returns True when global ephemeral mode is active or the
    conversation's chat record doesn't exist on disk (frontend-only
    ephemeral conversations).
    """
    if os.environ.get("ZIYA_EPHEMERAL", "").lower() in ("1", "true", "yes"):
        logger.debug("📿 bead gate: ephemeral=True (ZIYA_EPHEMERAL set)")
        return True
    try:
        from app.storage.beads import _resolve_chat_storage
        storage, conv_id = _resolve_chat_storage()
        missing = storage.get(conv_id) is None
        if missing:
            logger.debug(
                "📿 bead gate: ephemeral=True (chat %s not on disk)",
                (conv_id or "?")[:8],
            )
        return missing
    except (ValueError, ImportError) as e:
        logger.debug("📿 bead gate: ephemeral=True (resolve failed: %s)", e)
        return True


# ---------------------------------------------------------------------------
# Tool: bead_create
# ---------------------------------------------------------------------------

class BeadCreateInput(BaseModel):
    """Input schema for bead_create."""
    content: str = Field(..., description="Short description of the subtask or fork (2-80 chars)")
    status: str = Field(
        "active",
        description="Initial status: 'active' (work on it now) or 'parked' (note for later)",
    )
    context_hint: Optional[str] = Field(
        None,
        description="Brief note of what was being discussed, for later resumption",
    )


class BeadCreateTool(BaseMCPTool):
    """Create a new bead (subtask/fork) in the conversation's task tree."""

    name: str = "bead_create"
    description: str = (
        "[INTERNAL] Track a subtask or potential fork in the conversation. "
        "Creates a bead as a child of the current active bead. Use 'active' "
        "status to switch to working on it now, or 'parked' to note it for "
        "later without switching context. The user does not see this."
    )
    is_internal: bool = True
    InputSchema = BeadCreateInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        content = kwargs.get("content", "").strip()
        if _is_ephemeral_context():
            return _EPHEMERAL_SKIP
        if not content:
            return {"ok": False, "error": "content is required"}
        if len(content) > 200:
            content = content[:200]

        status = kwargs.get("status", "active")
        if status not in ("active", "parked"):
            status = "active"
        context_hint = kwargs.get("context_hint")

        try:
            from app.models.bead import Bead
            from app.storage.beads import load_bead_tree, save_bead_tree

            tree = load_bead_tree()
            active = tree.active_bead
            parent_id = active.id if active else None

            # If creating an active bead, park the current one
            if status == "active" and active:
                active.status = "parked"

            new_bead = Bead(
                parent_id=parent_id,
                content=content,
                status=status,
                context_hint=context_hint,
            )
            tree.beads.append(new_bead)
            save_bead_tree(tree)

            logger.debug(
                f"📿 Bead created: [{new_bead.id[:8]}] {status} — {content[:60]}"
            )
            return {
                "ok": True,
                "bead_id": new_bead.id,
                "status": status,
                "tree_depth": len(tree.get_path_to_root(new_bead.id)),
                "parked_count": len(tree.parked_beads),
            }
        except Exception as e:
            logger.warning(f"bead_create failed: {e}")
            return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool: bead_complete
# ---------------------------------------------------------------------------

class BeadCompleteInput(BaseModel):
    """Input schema for bead_complete."""
    bead_id: Optional[str] = Field(
        None,
        description="ID of bead to complete. Omit to complete the current active bead.",
    )


class BeadCompleteTool(BaseMCPTool):
    """Mark a bead as completed and resume its parent."""

    name: str = "bead_complete"
    description: str = (
        "[INTERNAL] Mark the current subtask as done. Resumes the parent "
        "bead (sets it active). If the completed bead has parked siblings, "
        "they remain parked for later. The user does not see this."
    )
    is_internal: bool = True
    InputSchema = BeadCompleteInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        bead_id = kwargs.get("bead_id")
        if _is_ephemeral_context():
            return _EPHEMERAL_SKIP

        try:
            from app.storage.beads import load_bead_tree, save_bead_tree

            tree = load_bead_tree()
            if bead_id:
                target = next((b for b in tree.beads if b.id == bead_id), None)
            else:
                target = tree.active_bead

            if not target:
                return {"ok": False, "error": "No active bead to complete"}

            target.status = "completed"

            # Resume parent if it exists and is parked
            if target.parent_id:
                parent = next((b for b in tree.beads if b.id == target.parent_id), None)
                if parent and parent.status == "parked":
                    parent.status = "active"

            save_bead_tree(tree)
            active = tree.active_bead
            return {
                "ok": True,
                "completed": target.id,
                "resumed": active.id if active else None,
                "parked_count": len(tree.parked_beads),
            }
        except Exception as e:
            logger.warning(f"bead_complete failed: {e}")
            return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tool: bead_status
# ---------------------------------------------------------------------------

class BeadStatusInput(BaseModel):
    """Input schema for bead_status (no required params)."""
    pass


class BeadStatusTool(BaseMCPTool):
    """View the current bead tree — active path and parked forks."""

    name: str = "bead_status"
    description: str = (
        "[INTERNAL] Inspect the conversation's task tree. Shows the active "
        "bead, its ancestry, and any parked (unfollowed) branches. Use this "
        "to orient yourself when resuming a complex conversation. "
        "The user does not see this."
    )
    is_internal: bool = True
    InputSchema = BeadStatusInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        if _is_ephemeral_context():
            return {"ok": True, "tree": "empty", "message": "Ephemeral — no beads tracked."}
        try:
            from app.storage.beads import load_bead_tree

            tree = load_bead_tree()
            if not tree.beads:
                return {"ok": True, "tree": "empty", "message": "No beads tracked yet."}

            active = tree.active_bead
            lines = []

            # Show active path
            if active:
                path = tree.get_path_to_root(active.id)
                lines.append("ACTIVE PATH (leaf → root):")
                for i, b in enumerate(path):
                    prefix = "→ " if i == 0 else "  " * i + "↑ "
                    lines.append(f"{prefix}[{b.id[:8]}] {b.content}")

            # Show parked beads
            parked = tree.parked_beads
            if parked:
                lines.append(f"\nPARKED ({len(parked)} unfollowed):")
                for b in parked:
                    hint = f" — {b.context_hint}" if b.context_hint else ""
                    lines.append(f"  ⏸ [{b.id[:8]}] {b.content}{hint}")

            # Summary
            completed = [b for b in tree.beads if b.status == "completed"]
            lines.append(
                f"\nSummary: {len(tree.beads)} total, "
                f"1 active, {len(parked)} parked, {len(completed)} completed"
            )

            return {"ok": True, "tree": "\n".join(lines)}
        except Exception as e:
            logger.warning(f"bead_status failed: {e}")
            return {"ok": False, "error": str(e)}
