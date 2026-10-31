"""
Conversation organisation tools — let the model arrange the sidebar.

Seven builtins over the per-project chat records and folder table
(``<ziya_home>/projects/<pid>/chats/*.json`` and ``chats/_groups.json``):

  - folder_list     : every folder in the current project with its
                      ``Parent / Child`` path, parent, pin flag and counts.
  - folder_create   : a folder, optionally nested under ``parent_id``.
  - folder_update   : rename, re-parent (``parent_id="root"`` for top
                      level) or pin / unpin a folder.  Refuses a move that
                      would make a folder its own ancestor.
  - folder_delete   : remove an EMPTY folder (no conversations, no
                      subfolders).  Never cascades.
  - chat_move       : put a conversation in a folder (or ``"root"``).
  - chat_rename     : set a conversation's title.
  - chat_fork       : copy a conversation into a new one with the same
                      lineage fields the sidebar's Fork action writes.

Only metadata is ever written; no tool here can change, remove or reorder
a message.  The one record-creating tool, ``chat_fork``, copies the source
transcript byte for byte.

Why direct writes rather than ``ChatStorage.update``: that method stamps
``lastActiveAt`` on every call, and the sidebar sorts by it.  Filing fifty
old conversations into folders must not make them all "just used", so
``chat_move`` / ``chat_rename`` bump only ``_version`` — which is what makes
the browser's next bulk-sync of its stale copy lose (``incoming_ver >=
existing_ver`` in app/api/chats.py) instead of undoing the change.

How the browser finds out: the frontend's tool_display handler (chatApi.ts)
raises a window event for every tool in this module and ChatContext answers
it by pulling folders and chat summaries from the server at once, so the
sidebar reflects the change within the same turn rather than on the 30 s
poll.  Folders carry ``updatedAt`` (serverFolderWins), chats ``_version``.

Threading: disk-bound work runs under ``asyncio.to_thread``, as the
chat_history tools do.  Results are past-record metadata, trust="medium".
"""
from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.chat_history_tools import _folder_paths, _load_chat, _resolve_scope
from app.utils.logging_utils import logger
from app.utils.ziya_links import chat_link

ROOT = "root"               # accepted wherever a folder id is expected: top level
MAX_TITLE_CHARS = 200
MAX_FOLDER_DEPTH = 20


# ---------------------------------------------------------------------------
# Helpers (sync; call under asyncio.to_thread)
# ---------------------------------------------------------------------------

def _now() -> int:
    return int(time.time() * 1000)


def _group_storage(ziya_home: Path, project_id: str):
    from app.storage.groups import ChatGroupStorage
    return ChatGroupStorage(ziya_home / "projects" / project_id)


def _chat_storage(ziya_home: Path, project_id: str):
    from app.storage.chats import ChatStorage
    return ChatStorage(ziya_home / "projects" / project_id)


def _parent_of(g: Any) -> Optional[str]:
    p = getattr(g, "parentId", None)
    if p is None:
        p = (getattr(g, "model_extra", None) or {}).get("parentId")
    return p or None


def _is_root(folder_id: Optional[str]) -> bool:
    return folder_id is None or str(folder_id).strip().lower() in ("", ROOT, "none", "null")


def _folder_rows(ziya_home: Path, project_id: str) -> List[Dict[str, Any]]:
    """Every folder of a project as the row shape all tools return."""
    groups = _group_storage(ziya_home, project_id).list()
    paths = _folder_paths(ziya_home, project_id)
    chat_counts: Counter = Counter()
    try:
        for s in _chat_storage(ziya_home, project_id).list_summaries():
            if s.groupId:
                chat_counts[s.groupId] += 1
    except Exception as e:  # noqa: BLE001 — counts are informational
        logger.debug("folder_rows: list_summaries failed for %s: %s", project_id, e)
    child_counts: Counter = Counter(_parent_of(g) for g in groups if _parent_of(g))
    rows = []
    for g in groups:
        rows.append({
            "folderId": g.id,
            "name": g.name,
            "path": paths.get(g.id, g.name),
            "parentId": _parent_of(g),
            "pinned": bool(getattr(g, "pinned", False)),
            "conversationCount": chat_counts.get(g.id, 0),
            "subfolderCount": child_counts.get(g.id, 0),
        })
    rows.sort(key=lambda r: r["path"].lower())
    return rows


def _row_for(ziya_home: Path, project_id: str, folder_id: str) -> Optional[Dict[str, Any]]:
    for r in _folder_rows(ziya_home, project_id):
        if r["folderId"] == folder_id:
            return r
    return None


def _would_cycle(by_id: Dict[str, Any], folder_id: str, new_parent: str) -> bool:
    """True when parenting ``folder_id`` under ``new_parent`` makes it its own ancestor."""
    cur: Optional[str] = new_parent
    hops = 0
    while cur is not None and hops < MAX_FOLDER_DEPTH:
        if cur == folder_id:
            return True
        g = by_id.get(cur)
        cur = _parent_of(g) if g is not None else None
        hops += 1
    return False


def _write_chat_fields(ziya_home: Path, project_id: str, chat_id: str,
                       fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Read-modify-write ``fields`` onto a chat record, bumping only ``_version``.

    ``lastActiveAt`` is deliberately left alone (see module docstring).
    Returns the written record, or None when the chat is missing.
    """
    from app.utils.chat_message_ops import chat_record_lock
    storage = _chat_storage(ziya_home, project_id)
    with chat_record_lock(storage):
        path = storage._chat_file(chat_id)
        raw = storage._read_json(path)
        if not raw:
            return None
        raw.update(fields)
        raw["_version"] = _now()
        storage._write_json(path, raw)
    return raw


def _resolve_project(scope: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    pid = scope.get("project_id")
    if not pid:
        return None, {"error": True,
                      "message": ("No registered project for the current request "
                                  "root; folders live per project.")}
    return pid, None


def _chat_summary(raw: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    cid = raw.get("id") or ""
    return {
        "conversationId": cid,
        "link": chat_link(cid),
        "title": raw.get("title") or "",
        "projectId": raw.get("projectId") or project_id,
        "folderId": raw.get("groupId") or raw.get("folderId"),
        "messageCount": len(raw.get("messages") or []),
        "_version": raw.get("_version"),
    }


# ---------------------------------------------------------------------------
# folder_list
# ---------------------------------------------------------------------------

class FolderListInput(BaseModel):
    """No parameters — lists the current project's folders."""


class FolderListTool(BaseMCPTool):
    name: str = "folder_list"
    description: str = (
        "List every conversation folder in the current project: folderId, "
        "name, full path (e.g. 'Infra / Parsers'), parentId, pinned flag, "
        "and how many conversations and subfolders it holds.  Use it to find "
        "the folderId for chat_move / folder_update, or to see what "
        "structure already exists before creating folders."
    )
    InputSchema = FolderListInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        scope = _resolve_scope()
        pid, err = _resolve_project(scope)
        if err:
            return err
        rows = await asyncio.to_thread(_folder_rows, scope["ziya_home"], pid)
        return {"projectId": pid, "count": len(rows), "folders": rows}


# ---------------------------------------------------------------------------
# folder_create
# ---------------------------------------------------------------------------

class FolderCreateInput(BaseModel):
    name: str = Field(..., description="Folder name as it will appear in the sidebar.")
    parent_id: Optional[str] = Field(None, description=(
        "folderId of the parent to nest under (from folder_list).  Omit or "
        "pass 'root' for a top-level folder."))


class FolderCreateTool(BaseMCPTool):
    name: str = "folder_create"
    description: str = (
        "Create a conversation folder in the current project, optionally "
        "nested under parent_id.  Returns the new folderId and its path.  "
        "Check folder_list first so you reuse an existing folder instead of "
        "creating a near-duplicate."
    )
    InputSchema = FolderCreateInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        name = (kwargs.get("name") or "").strip()
        if not name:
            return {"error": True, "message": "name is required."}
        parent_id = kwargs.get("parent_id")
        scope = _resolve_scope()
        pid, err = _resolve_project(scope)
        if err:
            return err

        def _do() -> Dict[str, Any]:
            from app.models.group import ChatGroupCreate
            storage = _group_storage(scope["ziya_home"], pid)
            parent: Optional[str] = None
            if not _is_root(parent_id):
                if storage.get(parent_id) is None:
                    return {"error": True,
                            "message": f"No folder with id {parent_id!r} in this project."}
                parent = parent_id
            group = storage.create(ChatGroupCreate(name=name, parentId=parent))
            row = _row_for(scope["ziya_home"], pid, group.id)
            return {"success": True, "created": True, "folder": row,
                    "message": f"Created folder '{row['path']}'."}

        return await asyncio.to_thread(_do)


# ---------------------------------------------------------------------------
# folder_update
# ---------------------------------------------------------------------------

class FolderUpdateInput(BaseModel):
    folder_id: str = Field(..., description="folderId to change (from folder_list).")
    name: Optional[str] = Field(None, description="New name.  Omit to keep.")
    parent_id: Optional[str] = Field(None, description=(
        "New parent folderId, or 'root' to move to the top level.  Omit to "
        "keep the current parent."))
    pinned: Optional[bool] = Field(None, description=(
        "true pins the folder to the top of the sidebar, false unpins.  "
        "Omit to keep."))


class FolderUpdateTool(BaseMCPTool):
    name: str = "folder_update"
    description: str = (
        "Rename a folder, move it under another folder (parent_id; 'root' "
        "for top level) or pin / unpin it.  Only the fields you pass change.  "
        "A move that would make a folder its own ancestor is refused."
    )
    InputSchema = FolderUpdateInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        folder_id = (kwargs.get("folder_id") or "").strip()
        if not folder_id:
            return {"error": True, "message": "folder_id is required."}
        name = kwargs.get("name")
        parent_given = "parent_id" in kwargs and kwargs.get("parent_id") is not None
        parent_id = kwargs.get("parent_id")
        pinned = kwargs.get("pinned")
        if name is None and not parent_given and pinned is None:
            return {"error": True,
                    "message": "Pass at least one of name, parent_id, pinned."}
        if name is not None and not str(name).strip():
            return {"error": True, "message": "name must not be empty."}
        scope = _resolve_scope()
        pid, err = _resolve_project(scope)
        if err:
            return err

        def _do() -> Dict[str, Any]:
            from app.models.group import ChatGroupUpdate
            storage = _group_storage(scope["ziya_home"], pid)
            groups = storage.list()
            by_id = {g.id: g for g in groups}
            if folder_id not in by_id:
                return {"error": True,
                        "message": f"No folder with id {folder_id!r} in this project."}
            fields: Dict[str, Any] = {}
            if name is not None:
                fields["name"] = str(name).strip()
            if parent_given:
                if _is_root(parent_id):
                    fields["parentId"] = None
                else:
                    if parent_id not in by_id:
                        return {"error": True,
                                "message": f"No folder with id {parent_id!r} in this project."}
                    if parent_id == folder_id or _would_cycle(by_id, folder_id, parent_id):
                        return {"error": True,
                                "message": ("Refused: moving this folder under "
                                            f"{parent_id!r} would make it its own "
                                            "ancestor.")}
                    fields["parentId"] = parent_id
            if pinned is not None:
                fields["pinned"] = bool(pinned)
            group = storage.update(folder_id, ChatGroupUpdate.model_validate(fields))
            if group is None:
                return {"error": True, "message": f"Folder {folder_id!r} vanished mid-update."}
            row = _row_for(scope["ziya_home"], pid, folder_id)
            return {"success": True, "updated": sorted(fields), "folder": row,
                    "message": f"Folder is now '{row['path']}'."}

        return await asyncio.to_thread(_do)


# ---------------------------------------------------------------------------
# folder_delete
# ---------------------------------------------------------------------------

class FolderDeleteInput(BaseModel):
    folder_id: str = Field(..., description="folderId to delete (from folder_list).")


class FolderDeleteTool(BaseMCPTool):
    name: str = "folder_delete"
    description: str = (
        "Delete an EMPTY folder.  Refused when the folder still holds "
        "conversations or subfolders — move them out first with chat_move / "
        "folder_update.  Never cascades."
    )
    InputSchema = FolderDeleteInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        folder_id = (kwargs.get("folder_id") or "").strip()
        if not folder_id:
            return {"error": True, "message": "folder_id is required."}
        scope = _resolve_scope()
        pid, err = _resolve_project(scope)
        if err:
            return err

        def _do() -> Dict[str, Any]:
            row = _row_for(scope["ziya_home"], pid, folder_id)
            if row is None:
                return {"error": True,
                        "message": f"No folder with id {folder_id!r} in this project."}
            if row["conversationCount"] or row["subfolderCount"]:
                return {"error": True,
                        "message": (f"Refused: '{row['path']}' still holds "
                                    f"{row['conversationCount']} conversation(s) and "
                                    f"{row['subfolderCount']} subfolder(s).")}
            ok = _group_storage(scope["ziya_home"], pid).delete(folder_id)
            return {"success": bool(ok), "deleted": folder_id, "path": row["path"]}

        return await asyncio.to_thread(_do)


# ---------------------------------------------------------------------------
# chat_move / chat_rename
# ---------------------------------------------------------------------------

def _load_target(ziya_home: Path, chat_id: str) -> Tuple[Optional[Tuple[str, dict]], Optional[Dict[str, Any]]]:
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return None, {"error": True, "message": "chat_id is required."}
    loaded = _load_chat(ziya_home, chat_id)
    if loaded is None:
        return None, {"error": True,
                      "message": f"No conversation found with id {chat_id!r}."}
    return loaded, None


class ChatMoveInput(BaseModel):
    chat_id: str = Field(..., description=(
        "conversationId (from chat_list / chat_search, or the current one)."))
    folder_id: str = Field(..., description=(
        "Destination folderId (from folder_list), or 'root' for the top level."))


class ChatMoveTool(BaseMCPTool):
    name: str = "chat_move"
    description: str = (
        "Move a conversation into a folder of its project ('root' for the "
        "top level).  Changes only the folder; messages, title and recency "
        "are untouched.  Cross-project moves are not supported here."
    )
    InputSchema = ChatMoveInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        folder_id = kwargs.get("folder_id")
        scope = _resolve_scope()

        def _do() -> Dict[str, Any]:
            loaded, err = _load_target(scope["ziya_home"], kwargs.get("chat_id"))
            if err:
                return err
            pid, raw = loaded
            target: Optional[str] = None
            if not _is_root(folder_id):
                if _group_storage(scope["ziya_home"], pid).get(folder_id) is None:
                    return {"error": True,
                            "message": (f"No folder with id {folder_id!r} in the "
                                        f"conversation's project {pid}.")}
                target = folder_id
            # groupId is canonical; folderId is the legacy twin the frontend
            # also reads (mergeServerChat) — keep them in step.
            written = _write_chat_fields(scope["ziya_home"], pid, raw["id"],
                                         {"groupId": target, "folderId": target})
            if written is None:
                return {"error": True, "message": f"Conversation {raw['id']!r} vanished mid-move."}
            path = _folder_paths(scope["ziya_home"], pid).get(target or "") if target else None
            return {"success": True, "conversation": _chat_summary(written, pid),
                    "folder": path or ROOT,
                    "message": f"Moved '{written.get('title')}' to {path or 'the top level'}."}

        return await asyncio.to_thread(_do)


class ChatRenameInput(BaseModel):
    chat_id: str = Field(..., description="conversationId to rename.")
    title: str = Field(..., description=f"New title (1–{MAX_TITLE_CHARS} characters).")


class ChatRenameTool(BaseMCPTool):
    name: str = "chat_rename"
    description: str = (
        "Set a conversation's title.  Changes only the title; messages, "
        "folder and recency are untouched."
    )
    InputSchema = ChatRenameInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        title = " ".join(str(kwargs.get("title") or "").split())
        if not title:
            return {"error": True, "message": "title must not be empty."}
        if len(title) > MAX_TITLE_CHARS:
            return {"error": True,
                    "message": f"title is {len(title)} chars; max is {MAX_TITLE_CHARS}."}
        scope = _resolve_scope()

        def _do() -> Dict[str, Any]:
            loaded, err = _load_target(scope["ziya_home"], kwargs.get("chat_id"))
            if err:
                return err
            pid, raw = loaded
            previous = raw.get("title")
            written = _write_chat_fields(scope["ziya_home"], pid, raw["id"], {"title": title})
            if written is None:
                return {"error": True, "message": f"Conversation {raw['id']!r} vanished mid-rename."}
            return {"success": True, "conversation": _chat_summary(written, pid),
                    "previousTitle": previous,
                    "message": f"Renamed '{previous}' to '{title}'."}

        return await asyncio.to_thread(_do)


# ---------------------------------------------------------------------------
# chat_fork
# ---------------------------------------------------------------------------

# Lineage fields a plain fork sets or clears — the server twin of
# frontend/src/utils/lineage.ts forkLineageFields.  A fork of a branch must
# not inherit the branch's seam; a fork of a handed-off source is not itself
# handed off; the shared bead tree lives on the lineage root.
def _fork_lineage(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "branchedFrom": source["id"],
        "branchedAtMessageIndex": None,
        "branchedFromLabel": None,
        "lineageKind": "fork",
        "lineageRootId": source.get("lineageRootId") or source["id"],
        "handedOffTo": None,
    }


class ChatForkInput(BaseModel):
    chat_id: str = Field(..., description="conversationId to fork.")
    title: Optional[str] = Field(None, description=(
        "Title for the fork.  Default: 'Fork: <source title>'."))
    folder_id: Optional[str] = Field(None, description=(
        "Folder for the fork ('root' for top level).  Default: the source's folder."))


class ChatForkTool(BaseMCPTool):
    name: str = "chat_fork"
    description: str = (
        "Fork a conversation: a new conversation with a copy of the whole "
        "transcript, linked to its source (branchedFrom, lineageKind='fork') "
        "exactly as the sidebar's Fork action does.  The source is untouched.  "
        "Returns the new conversationId and link."
    )
    InputSchema = ChatForkInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        title = " ".join(str(kwargs.get("title") or "").split()) or None
        if title and len(title) > MAX_TITLE_CHARS:
            return {"error": True,
                    "message": f"title is {len(title)} chars; max is {MAX_TITLE_CHARS}."}
        folder_id = kwargs.get("folder_id")
        scope = _resolve_scope()

        def _do() -> Dict[str, Any]:
            loaded, err = _load_target(scope["ziya_home"], kwargs.get("chat_id"))
            if err:
                return err
            pid, src = loaded
            group = src.get("groupId") or src.get("folderId")
            if folder_id is not None:
                if _is_root(folder_id):
                    group = None
                elif _group_storage(scope["ziya_home"], pid).get(folder_id) is None:
                    return {"error": True,
                            "message": (f"No folder with id {folder_id!r} in the "
                                        f"conversation's project {pid}.")}
                else:
                    group = folder_id
            now = _now()
            fork = copy.deepcopy(src)
            fork.update({
                "id": str(uuid.uuid4()),
                "title": title or f"Fork: {src.get('title') or 'Untitled'}",
                "groupId": group,
                "folderId": group,
                "projectId": src.get("projectId") or pid,
                "createdAt": now,
                "lastActiveAt": now,
                "lastAccessedAt": now,
                "_version": now,
                "hasUnreadResponse": False,
                "isActive": True,
                **_fork_lineage(src),
            })
            # Transient sidebar-shell markers never belong on a record.
            fork.pop("_isShell", None)
            fork.pop("_fullMessageCount", None)
            from app.utils.chat_message_ops import chat_record_lock
            storage = _chat_storage(scope["ziya_home"], pid)
            with chat_record_lock(storage):
                storage._write_json(storage._chat_file(fork["id"]), fork)
            try:
                from app.storage import chat_index
                chat_index.on_chat_written(fork["id"], pid)
            except Exception as e:  # noqa: BLE001
                logger.debug("chat_fork: index update failed: %s", e)
            return {"success": True,
                    "conversation": _chat_summary(fork, pid),
                    "sourceConversationId": src["id"],
                    "message": (f"Forked '{src.get('title')}' into '{fork['title']}' "
                                f"({len(fork.get('messages') or [])} messages)."),
                    }

        return await asyncio.to_thread(_do)


ORGANIZE_TOOLS = [FolderListTool, FolderCreateTool, FolderUpdateTool, FolderDeleteTool,
                  ChatMoveTool, ChatRenameTool, ChatForkTool]
# BaseMCPTool is a plain ABC, not a pydantic model: ``name`` is a class
# attribute.  ``t.model_fields`` raised at import and took every builtin
# tool down with it (TOOL_LOADING: 0 tools).
ORGANIZE_TOOL_NAMES = frozenset(t.name for t in ORGANIZE_TOOLS)

__all__ = ["ORGANIZE_TOOLS", "ORGANIZE_TOOL_NAMES", "ROOT"] + [t.__name__ for t in ORGANIZE_TOOLS]
