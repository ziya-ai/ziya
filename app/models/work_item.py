"""
WorkItem — committed, user-visible unit of agreed work (work-primitives
taxonomy, design/work-primitives-taxonomy.md).

STATUS: primitive shell only.  This module defines the model, factories,
status-machine constants, and the open-count helper that the sidebar
indicator reads.  The full work-item QUEUE — per-scope storage class, the
surfacing panel, and the session->project promotion path — is deliberately
deferred (see the taxonomy doc).  Nothing writes WorkItems yet, so
count_open_work_items returns 0 for every conversation today; the sidebar's
work-item indicator is therefore a correct-but-empty shell whose wiring is
real and will light up unchanged once the queue lands.

Boundary (held from the taxonomy doc): a WorkItem is a *list* of
user-visible committed work, distinct from a *bead* (an agent-internal
noticed-debt tree) and from a Task Card (an execution engine a WorkItem may
reference, never is).
"""
from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field


# Status machine: todo -> doing -> done, with blocked / abandoned side states.
WORK_ITEM_STATUSES = ["todo", "doing", "done", "blocked", "abandoned"]

# "Open" == not terminal.  done and abandoned are closed; everything else is
# still outstanding work and counts toward the sidebar indicator.
WORK_ITEM_OPEN_STATUSES = frozenset({"todo", "doing", "blocked"})

# Field name on the chat JSON record, mirroring beads' `_beads`.  No code
# writes this yet — the count helper reads it defensively and returns 0.
WORK_ITEMS_FIELD = "_work_items"


class WorkItemScope(BaseModel):
    """Where a work item lives: a session (conversation) or a project backlog.

    The single discriminator distinguishing the two work-item scopes; `key`
    is the conversation_id (session) or project_id (project).
    """
    type: Literal["session", "project"]
    key: str


class WorkItem(BaseModel):
    """A committed, user-visible unit of agreed work.

    One model, two scopes (the `scope.type` discriminator).  Shared CRUD,
    shared status machine, and a single `session -> project` promotion path
    are all deferred to the queue implementation; this is the data shape only.
    """
    model_config = {"extra": "allow"}

    id: str = Field(default_factory=lambda: f"wi_{uuid.uuid4().hex[:12]}")
    content: str
    status: Literal["todo", "doing", "done", "blocked", "abandoned"] = "todo"
    scope: WorkItemScope
    conversation_id: str
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))
    order: Optional[int] = None
    notes: Optional[str] = None

    @classmethod
    def for_session(cls, conversation_id: str, content: str, **kwargs) -> "WorkItem":
        """Session-scoped item: do this now / in the next few hours, in this
        conversation.  May auto-clear at session end (queue policy, TBD)."""
        return cls(
            content=content,
            conversation_id=conversation_id,
            scope=WorkItemScope(type="session", key=conversation_id),
            **kwargs,
        )

    @classmethod
    def for_project(cls, project_id: str, content: str,
                    conversation_id: str = "", **kwargs) -> "WorkItem":
        """Project-scoped backlog item: no specific timing, never auto-clears.
        conversation_id records the originating conversation (may be empty
        when authored directly against the project backlog)."""
        return cls(
            content=content,
            conversation_id=conversation_id,
            scope=WorkItemScope(type="project", key=project_id),
            **kwargs,
        )


def count_open_work_items(raw_items) -> int:
    """Count work items in a non-terminal (open) state.

    Accepts the raw `_work_items` list off a chat record (list of dicts) or
    a list of WorkItem objects; tolerant of None / non-list (-> 0) so the
    summary builders can call it on every chat unconditionally.  Returns 0
    today for every conversation because nothing populates `_work_items`
    yet — that is the intended shell behavior.
    """
    if not isinstance(raw_items, list):
        return 0
    n = 0
    for item in raw_items:
        status = item.get("status") if isinstance(item, dict) else getattr(item, "status", None)
        if status in WORK_ITEM_OPEN_STATUSES:
            n += 1
    return n
