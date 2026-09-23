"""
Conversation handoff endpoints (design/conversation-handoff.md).

A handoff is a CONSCIOUS move from one conversation segment to the next —
not compaction.  The source keeps its whole transcript and stays usable;
the continuation starts with an empty transcript and a handoff document
(``Chat.handoff``) injected as a per-turn prelude, plus the predecessor id
so it can retrieve any prior turn at full fidelity.

  GET   .../chats/{id}/handoff          state for the drawer / card
  PATCH .../chats/{id}/handoff/draft    user edits the living draft on a SOURCE
  PATCH .../chats/{id}/handoff          user edits the inherited doc on a CHILD
  POST  .../chats/{id}/handoff/commit   create the continuation

The model's side of the same record is the handoff_write tool
(app/mcp/tools/handoff_tools.py); both writers touch the same fields.
"""

import re
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.utils.handoff_prelude import (
    DRAFT_FIELD, HANDOFF_FIELD, LINEAGE_KIND_HANDOFF, get_context_pressure,
    predecessor_chain,
)
from app.utils.logging_utils import logger
from app.utils.paths import get_project_dir

router = APIRouter(tags=["handoff"])


def _storage(project_id: str):
    from app.storage.chats import ChatStorage
    return ChatStorage(get_project_dir(project_id))


def _load(storage, chat_id: str) -> dict:
    raw = storage._read_json(storage._chat_file(chat_id))
    if not raw:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return raw


def _save(storage, project_id: str, raw: dict, now: int) -> None:
    raw["lastActiveAt"] = now
    raw["_version"] = now
    storage._write_json(storage._chat_file(raw["id"]), raw)
    try:
        from app.storage import chat_index
        chat_index.on_chat_written(raw["id"], project_id)
    except Exception as e:
        logger.debug(f"handoff: chat_index update skipped: {e}")


def _open_beads(storage, chat_id: str) -> List[Dict[str, Any]]:
    try:
        from app.storage.beads import load_bead_tree
        tree = load_bead_tree(chat_storage=storage, conversation_id=chat_id)
    except Exception:
        return []
    return [{"id": b.id, "content": b.content, "status": b.status}
            for b in tree.beads if b.status in ("active", "parked")]


_SEG_RE = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")


def continuation_title(source_title: str) -> str:
    """'Foo' → 'Foo (2)'; 'Foo (2)' → 'Foo (3)'.  Pure; tested."""
    base = (source_title or "").strip() or "Conversation"
    m = _SEG_RE.match(base)
    if m:
        return f"{m.group(1)} ({int(m.group(2)) + 1})"
    return f"{base} (2)"


# ---------------------------------------------------------------------------

@router.get("/api/v1/projects/{project_id}/chats/{chat_id}/handoff")
async def get_handoff_state(project_id: str, chat_id: str):
    storage = _storage(project_id)
    raw = _load(storage, chat_id)
    messages = raw.get("messages") or []
    draft = raw.get(DRAFT_FIELD)
    handoff = raw.get(HANDOFF_FIELD)
    pressure = get_context_pressure(chat_id)
    predecessors = [
        {"id": p.get("id"), "title": p.get("title"),
         "messageCount": len(p.get("messages") or []) if p.get("messages") is not None else None}
        for p in predecessor_chain(storage, raw)
    ]
    return {
        "conversationId": chat_id,
        "title": raw.get("title") or "",
        "messageCount": len(messages) if isinstance(messages, list) else 0,
        "draft": draft if isinstance(draft, dict) else None,
        "handoff": handoff if isinstance(handoff, dict) else None,
        "lineageKind": raw.get("lineageKind"),
        "branchedFrom": raw.get("branchedFrom"),
        "handedOffTo": raw.get("handedOffTo"),
        "predecessors": predecessors,
        "openBeads": _open_beads(storage, chat_id),
        "additionalFiles": raw.get("additionalFiles") or [],
        "contextPressure": round(pressure, 3) if pressure is not None else None,
    }


class DocumentBody(BaseModel):
    document: str


@router.patch("/api/v1/projects/{project_id}/chats/{chat_id}/handoff/draft")
async def edit_draft(project_id: str, chat_id: str, body: DocumentBody):
    """User edit of the living draft.  An empty document CLEARS the draft
    (the one way to stop the per-turn "update the draft" instruction short
    of committing)."""
    storage = _storage(project_id)
    raw = _load(storage, chat_id)
    now = int(time.time() * 1000)
    text = (body.document or "").strip()
    if not text:
        raw.pop(DRAFT_FIELD, None)
        _save(storage, project_id, raw, now)
        return {"ok": True, "draft": None, "cleared": True}
    existing = raw.get(DRAFT_FIELD) if isinstance(raw.get(DRAFT_FIELD), dict) else {}
    messages = raw.get("messages") or []
    raw[DRAFT_FIELD] = {
        **existing,
        "document": text,
        "generatedAt": existing.get("generatedAt") or now,
        "updatedAt": now,
        "editedAt": now,
        "sourceMessageCount": len(messages) if isinstance(messages, list) else 0,
    }
    _save(storage, project_id, raw, now)
    return {"ok": True, "draft": raw[DRAFT_FIELD]}


@router.patch("/api/v1/projects/{project_id}/chats/{chat_id}/handoff")
async def edit_inherited(project_id: str, chat_id: str, body: DocumentBody):
    """User edit of a CONTINUATION's inherited document.  Takes effect on
    the child's next turn (the prelude re-reads the record); no message is
    created."""
    storage = _storage(project_id)
    raw = _load(storage, chat_id)
    existing = raw.get(HANDOFF_FIELD)
    if not isinstance(existing, dict):
        raise HTTPException(status_code=400,
                            detail="This conversation is not a handoff continuation")
    text = (body.document or "").strip()
    if not text:
        raise HTTPException(status_code=400,
                            detail="A continuation's handoff document cannot be empty")
    now = int(time.time() * 1000)
    raw[HANDOFF_FIELD] = {**existing, "document": text, "editedAt": now}
    _save(storage, project_id, raw, now)
    return {"ok": True, "handoff": raw[HANDOFF_FIELD]}


class CommitBody(BaseModel):
    # Final document text (the drawer's edited copy).  Falls back to the
    # stored draft when omitted.
    document: Optional[str] = None
    # Files the continuation opens with.  Defaults to the source's
    # additionalFiles.
    workingSet: Optional[List[str]] = None
    title: Optional[str] = None


@router.post("/api/v1/projects/{project_id}/chats/{chat_id}/handoff/commit")
async def commit_handoff(project_id: str, chat_id: str, body: CommitBody):
    storage = _storage(project_id)
    src = _load(storage, chat_id)
    now = int(time.time() * 1000)

    draft = src.get(DRAFT_FIELD) if isinstance(src.get(DRAFT_FIELD), dict) else {}
    document = (body.document if body.document is not None else draft.get("document") or "").strip()
    if not document:
        raise HTTPException(status_code=400,
                            detail="No handoff document: write a draft first")

    src_messages = src.get("messages") or []
    src_count = len(src_messages) if isinstance(src_messages, list) else 0
    user_edited = body.document is not None and body.document.strip() != (draft.get("document") or "").strip()

    child_id = str(uuid.uuid4())
    child: Dict[str, Any] = {
        "id": child_id,
        "title": (body.title or "").strip() or continuation_title(src.get("title") or ""),
        "messages": [],
        "createdAt": now,
        "lastActiveAt": now,
        "lastAccessedAt": now,
        "_version": now,
        "projectId": project_id,
        "folderId": src.get("folderId"),
        "groupId": src.get("groupId"),
        "isActive": True,
        "contextIds": src.get("contextIds") or [],
        "skillIds": src.get("skillIds") or [],
        "additionalFiles": (body.workingSet if body.workingSet is not None
                            else (src.get("additionalFiles") or [])),
        "additionalPrompt": src.get("additionalPrompt"),
        "branchedFrom": chat_id,
        "lineageKind": LINEAGE_KIND_HANDOFF,
        # Shared bead tree (b2): the continuation tracks the SAME open
        # threads as the source, not copies of them.
        "lineageRootId": src.get("lineageRootId") or chat_id,
        HANDOFF_FIELD: {
            "document": document,
            "generatedAt": draft.get("generatedAt") or now,
            "editedAt": now if user_edited else draft.get("editedAt"),
            "sourceMessageCount": src_count,
            "tokenEstimate": max(1, len(document) // 4),
        },
    }
    if src.get("modelPreference"):
        child["modelPreference"] = src["modelPreference"]
    storage._write_json(storage._chat_file(child_id), child)
    try:
        from app.storage import chat_index
        chat_index.on_chat_written(child_id, project_id)
    except Exception as e:
        logger.debug(f"handoff: chat_index update skipped: {e}")

    # Source: link forward; keep the draft (it keeps living if the user
    # continues here), reflecting a pre-commit edit into it.  Never locked.
    src["handedOffTo"] = child_id
    if user_edited:
        src[DRAFT_FIELD] = {**draft, "document": document, "editedAt": now,
                            "updatedAt": now, "generatedAt": draft.get("generatedAt") or now,
                            "sourceMessageCount": src_count}
    _save(storage, project_id, src, now)

    logger.info(f"⇢ handoff: {chat_id[:8]} ({src_count} msgs) → {child_id[:8]} "
                f"'{child['title']}' ({len(document)} chars)")
    return {
        "ok": True,
        "child": {k: v for k, v in child.items() if k != "messages"},
        "sourceId": chat_id,
        "handedOffTo": child_id,
    }
