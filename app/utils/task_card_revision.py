"""
Where a task card's tile goes when the MODEL revises the card.

A model-staged tile is placed by its binding's ``created_at``
(utils/taskBindingPlacement.ts), so ``task_card_write`` -- which edits the
card and nothing else -- left the tile wherever ``task_card_stage`` first
put it, possibly a hundred turns up, showing nothing to say it changed.

``place_revised_card`` runs after a successful model write in a
conversation:

  * the chat has a STAGED binding of the card (the newest one, if several):
    re-stage it -- ``created_at`` = now, anchor cleared -- so it renders at
    the end of the turn that revised it.  Its previous placement is appended
    to ``revisions`` so the client can leave a "revised later" marker where
    it used to be, and ``revision`` counts the moves;
  * the chat has only LAUNCHED bindings of the card: those tiles are run
    history and stay put; a new staged binding is created so the revised
    definition appears here with a Run button (``revision_after_run_id``
    names the run it follows);
  * the chat has no binding of the card: nothing.  Editing a deck card from
    an unrelated chat does not spawn a tile.

Edits made in the deck editor do not come through here: those are the user
editing a tile in place, which refreshes where it is.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Bound on the per-binding placement history: each entry is one marker.
MAX_REVISIONS = 20


def place_revised_card(project_dir: Path, chat_id: Optional[str], card_id: str,
                       now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Move or create the card's staged tile in ``chat_id``.

    Returns ``{"binding_id", "action": "moved"|"created", "revision"}`` or
    None when the chat has no tile of this card.
    """
    if not chat_id or not card_id:
        return None
    from app.storage.task_bindings import TaskBindingStorage
    storage = TaskBindingStorage(project_dir)
    bindings = storage.list_for_chat(chat_id)
    mine = [b for b in bindings if b.card_id == card_id]
    if not mine:
        return None
    now = int(now_ms if now_ms is not None else time.time() * 1000)

    staged = [b for b in mine if not b.run_id]
    if staged:
        target = max(staged, key=lambda b: b.created_at or 0)
        prior = list(getattr(target, "revisions", None) or [])
        prior.append({"created_at": target.created_at,
                      "anchor_message_id": target.anchor_message_id})
        target.revisions = prior[-MAX_REVISIONS:]
        target.revision = int(getattr(target, "revision", 0) or 0) + 1
        target.created_at = now
        target.anchor_message_id = None
        _save(storage, chat_id, bindings)
        return {"binding_id": target.id, "action": "moved", "revision": target.revision}

    last_run = max(mine, key=lambda b: b.created_at or 0)
    created = storage.create(chat_id=chat_id, card_id=card_id, run_id=None,
                             anchor_message_id=None)
    bindings = storage.list_for_chat(chat_id)
    for b in bindings:
        if b.id == created.id:
            b.created_at = now
            b.revision = 1
            b.revisions = []
            b.revision_after_run_id = last_run.run_id
    _save(storage, chat_id, bindings)
    return {"binding_id": created.id, "action": "created", "revision": 1}


def _save(storage, chat_id: str, bindings) -> None:
    # TaskBindingStorage.update is deliberately unimplemented (bindings are
    # pointers); the list file is rewritten whole, as create/delete do.
    storage._write_json(storage._bindings_file(chat_id), [b.model_dump() for b in bindings])
