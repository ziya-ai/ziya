"""
Bead data model — per-conversation task-tree nodes.

A bead represents a unit of work in a conversation.  Beads form a tree:
the root bead is the user's original intent, and child beads are subtasks
or forks identified during the conversation.  The model creates beads
silently as it works; the user can inspect the tree on demand to see
what threads are active, parked, or completed.

Design:
  - Invisible to the user during normal flow (tools are is_internal=True)
  - Stored on the Chat record as `_beads` (persists through sync)
  - Tree structure via parent_id references
  - The "active" bead is the one currently being worked on
  - "Parked" beads are threads identified but not yet followed
"""
import time
import uuid
from typing import List, Optional

from pydantic import BaseModel, Field


class Bead(BaseModel):
    """A single node in the conversation's task tree."""

    id: str = Field(default_factory=lambda: f"bead_{uuid.uuid4().hex[:12]}")
    parent_id: Optional[str] = None
    content: str = Field(..., description="Short description of the task/subtask")
    status: str = Field(
        "active",
        description="One of: active, parked, completed, abandoned",
    )
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))
    message_index: Optional[int] = Field(
        None,
        description="Index of the message that spawned this bead",
    )
    context_hint: Optional[str] = Field(
        None,
        description="Brief note of what was being discussed when this was parked",
    )


class BeadTree(BaseModel):
    """The full bead tree for a conversation, plus helpers."""

    beads: List[Bead] = Field(default_factory=list)

    @property
    def active_bead(self) -> Optional[Bead]:
        """The single currently-active bead (most recently activated)."""
        active = [b for b in self.beads if b.status == "active"]
        if not active:
            return None
        # If multiple are active (shouldn't happen), pick most recent
        return max(active, key=lambda b: b.created_at)

    @property
    def parked_beads(self) -> List[Bead]:
        """All beads with status 'parked' — unfollowed threads."""
        return [b for b in self.beads if b.status == "parked"]

    def get_children(self, bead_id: str) -> List[Bead]:
        """Direct children of a bead."""
        return [b for b in self.beads if b.parent_id == bead_id]

    def get_path_to_root(self, bead_id: str) -> List[Bead]:
        """Ancestors from bead_id up to root, ordered leaf-first."""
        path = []
        by_id = {b.id: b for b in self.beads}
        current = by_id.get(bead_id)
        while current:
            path.append(current)
            current = by_id.get(current.parent_id) if current.parent_id else None
        return path
