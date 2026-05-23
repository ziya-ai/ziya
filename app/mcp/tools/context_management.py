"""
Builtin MCP tools for model-driven context management.

Lets the model add/remove/list files in the current chat's
\`additionalFiles\` so it can curate its own context across turns.
This complements user-controlled context: the user pins files via the
file tree; the model can pin more via these tools when it realises it
needs them.

Lifecycle:
  - \`context_add_file\`: validates the path, persists it to the chat's
    \`additionalFiles\` (server-side), tags it with a sentinel
    \`_modelAddedFiles\` list for removal-scope enforcement, AND
    immediately reads the file's content into the tool result so the
    model sees it on this turn (ephemeral — next turn picks it up via
    the normal context pipeline).
  - \`context_remove_file\`: removes the path *only if* the model added
    it.  User-pinned files are off-limits to keep the trust model clear.
  - \`context_list_files\`: returns the current chat's \`additionalFiles\`
    with ownership tags so the model can see what it's already added.

Frontend live-sync:
  The frontend's SSE handler watches for \`tool_display\` events with
  these tool names and dispatches the existing \`syncContextFromBackend\`
  CustomEvent — same mechanism used by the diff-validation context-add
  flow.  No new SSE event type is introduced.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.fileio import _resolve_and_validate, _get_safe_write_paths
from app.utils.logging_utils import logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Sentinel field on the chat record naming files the *model* added via
# context_add_file (vs files the user pinned via the file tree).  Used
# only for removal-scope enforcement; not surfaced to the frontend
# beyond what context_list_files returns.
_OWNERSHIP_FIELD = "_modelAddedFiles"

# Maximum file size we'll inject ephemerally on add.  Larger files still
# get added to the chat's context (so next turn's pipeline picks them
# up), but the immediate inline content is truncated with a notice.
_MAX_INLINE_BYTES = 64 * 1024


def _resolve_chat_for_request(_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve the chat record for the current streaming request.

    Returns a dict with ok=True plus chat handles, or ok=False plus an
    error message.  Walks: ContextVar conversation_id → request-scoped
    project_root → ProjectStorage.get_by_path → ChatStorage(project_dir).
    """
    from app.context import get_conversation_id_or_none, get_project_root_or_none
    from app.storage.projects import ProjectStorage
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    conversation_id = get_conversation_id_or_none()
    if not conversation_id:
        return {"ok": False,
                "error": ("This tool only works inside a chat session — no "
                          "conversation_id is set in the current request "
                          "context.")}

    project_root = get_project_root_or_none() or os.environ.get("ZIYA_USER_CODEBASE_DIR")
    if not project_root:
        return {"ok": False,
                "error": "Cannot determine project root for the current request."}

    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get_by_path(project_root)
    if not project:
        return {"ok": False,
                "error": f"Project not registered for path: {project_root}"}

    storage = ChatStorage(get_project_dir(project.id))
    chat_file = storage._chat_file(conversation_id)
    chat_data = storage._read_json(chat_file)
    if not chat_data:
        return {"ok": False,
                "error": (f"Chat {conversation_id[:8]} not found in project "
                          f"{project.name} — it may belong to a different "
                          f"project or be a brand-new conversation that "
                          f"hasn't been persisted yet.")}

    return {
        "ok": True,
        "project_id": project.id,
        "project_root": project_root,
        "chat_id": conversation_id,
        "chat_data": chat_data,
        "storage": storage,
        "chat_file": chat_file,
    }


def _validate_relative_path(path_str: str, project_root: str) -> Path:
    """
    Resolve \`path_str\` relative to the project root with traversal
    rejection.  Permits absolute paths under safe-write-paths.
    """
    return _resolve_and_validate(
        path_str,
        project_root,
        allowed_absolute_prefixes=_get_safe_write_paths(),
    )


# ---------------------------------------------------------------------------
# Tool: context_add_file
# ---------------------------------------------------------------------------

class ContextAddFileInput(BaseModel):
    """Input schema for context_add_file."""
    path: str = Field(
        ...,
        description=("Project-relative path to a file to add to the "
                     "current conversation's persistent context.  The "
                     "file content is also returned inline in this "
                     "tool's result so you can see it immediately."),
    )


class ContextAddFileTool(BaseMCPTool):
    """Add a file to the current chat's persistent context."""

    name: str = "context_add_file"
    description: str = (
        "Add a file to the CURRENT conversation's persistent context.  "
        "The file is saved on the chat record, so it remains in context "
        "on every subsequent turn until you remove it.  The file's "
        "current content is also returned inline in this tool's result "
        "for immediate use this turn.  "
        "Use this when you realise mid-conversation that you need a "
        "file you didn't initially have access to.  Only files you "
        "added with this tool can later be removed with "
        "context_remove_file — files the user pinned are protected."
    )
    InputSchema = ContextAddFileInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        ctx = _resolve_chat_for_request(kwargs)
        if not ctx["ok"]:
            return {"error": True, "message": ctx["error"]}

        path_str: str = kwargs.get("path", "").strip()
        if not path_str:
            return {"error": True, "message": "path must not be empty"}

        try:
            resolved = _validate_relative_path(path_str, ctx["project_root"])
        except ValueError as e:
            return {"error": True, "message": str(e)}

        if not resolved.exists():
            return {"error": True,
                    "message": f"File not found: {path_str}"}
        if not resolved.is_file():
            return {"error": True,
                    "message": f"Not a regular file: {path_str}"}

        # Normalise: store the project-relative form when possible.
        try:
            rel_path = str(resolved.relative_to(Path(ctx["project_root"]).resolve()))
        except ValueError:
            # Absolute path under safe-write prefix; keep original string.
            rel_path = path_str

        chat_data = ctx["chat_data"]
        additional = list(chat_data.get("additionalFiles") or [])
        owned = list(chat_data.get(_OWNERSHIP_FIELD) or [])

        if rel_path in additional:
            return {
                "success": True,
                "already_in_context": True,
                "path": rel_path,
                "message": (f"File '{rel_path}' is already in the "
                            f"conversation context."),
            }

        additional.append(rel_path)
        if rel_path not in owned:
            owned.append(rel_path)
        chat_data["additionalFiles"] = additional
        chat_data[_OWNERSHIP_FIELD] = owned

        # Bump version so a sibling tab's stale chat copy doesn't
        # clobber this on the next sync.
        import time as _time
        _now_ms = int(_time.time() * 1000)
        chat_data["lastActiveAt"] = _now_ms
        chat_data["_version"] = _now_ms

        try:
            ctx["storage"]._write_json(ctx["chat_file"], chat_data)
        except Exception as e:
            logger.error(f"context_add_file: persist failed: {e}")
            return {"error": True,
                    "message": f"Could not persist context update: {e}"}

        # Ephemeral content injection — read the file and include its
        # text in the result so the model can use it this turn.
        inline_content: Optional[str] = None
        truncated = False
        size_bytes = 0
        try:
            size_bytes = resolved.stat().st_size
            if size_bytes > _MAX_INLINE_BYTES:
                with resolved.open("r", encoding="utf-8", errors="replace") as f:
                    inline_content = f.read(_MAX_INLINE_BYTES)
                truncated = True
            else:
                inline_content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"context_add_file: inline read failed for {rel_path}: {e}")
            inline_content = None

        result: Dict[str, Any] = {
            "success": True,
            "path": rel_path,
            "size_bytes": size_bytes,
            "added_to_context": True,
            "message": (f"Added '{rel_path}' to conversation context "
                        f"(persists across turns until removed)."),
        }
        if inline_content is not None:
            result["content"] = inline_content
            if truncated:
                result["content_truncated"] = True
                result["content_note"] = (
                    f"Inline content truncated at {_MAX_INLINE_BYTES} "
                    f"bytes; full file is in context for next turn."
                )
        return result


# ---------------------------------------------------------------------------
# Tool: context_remove_file
# ---------------------------------------------------------------------------

class ContextRemoveFileInput(BaseModel):
    """Input schema for context_remove_file."""
    path: str = Field(
        ...,
        description=("Project-relative path to remove from the current "
                     "conversation's context.  Only files added by the "
                     "model itself can be removed; user-pinned files "
                     "are protected."),
    )


class ContextRemoveFileTool(BaseMCPTool):
    """Remove a model-added file from the current chat's context."""

    name: str = "context_remove_file"
    description: str = (
        "Remove a file from the CURRENT conversation's persistent "
        "context.  Only files you previously added via "
        "context_add_file can be removed — files the user pinned via "
        "the file tree are protected.  Use this to free up context "
        "budget when a file is no longer needed."
    )
    InputSchema = ContextRemoveFileInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        ctx = _resolve_chat_for_request(kwargs)
        if not ctx["ok"]:
            return {"error": True, "message": ctx["error"]}

        path_str: str = kwargs.get("path", "").strip()
        if not path_str:
            return {"error": True, "message": "path must not be empty"}

        chat_data = ctx["chat_data"]
        additional = list(chat_data.get("additionalFiles") or [])
        owned = list(chat_data.get(_OWNERSHIP_FIELD) or [])

        match: Optional[str] = None
        for candidate in (path_str, path_str.lstrip("./")):
            if candidate in additional:
                match = candidate
                break
        if match is None:
            return {"error": True,
                    "message": (f"File '{path_str}' is not in the "
                                f"conversation context.")}

        if match not in owned:
            return {"error": True,
                    "message": (f"File '{match}' was pinned by the user, "
                                f"not added by the model — refusing to "
                                f"remove.  Only model-added files can be "
                                f"removed via this tool.")}

        additional.remove(match)
        owned.remove(match)
        chat_data["additionalFiles"] = additional
        chat_data[_OWNERSHIP_FIELD] = owned

        import time as _time
        _now_ms = int(_time.time() * 1000)
        chat_data["lastActiveAt"] = _now_ms
        chat_data["_version"] = _now_ms

        try:
            ctx["storage"]._write_json(ctx["chat_file"], chat_data)
        except Exception as e:
            logger.error(f"context_remove_file: persist failed: {e}")
            return {"error": True,
                    "message": f"Could not persist context update: {e}"}

        return {
            "success": True,
            "path": match,
            "removed_from_context": True,
            "message": f"Removed '{match}' from conversation context.",
        }


# ---------------------------------------------------------------------------
# Tool: context_list_files
# ---------------------------------------------------------------------------

class ContextListFilesInput(BaseModel):
    """Input schema for context_list_files (no arguments)."""
    pass


class ContextListFilesTool(BaseMCPTool):
    """List all files currently in the chat's persistent context."""

    name: str = "context_list_files"
    description: str = (
        "List all files currently in the CURRENT conversation's "
        "persistent context, with ownership info (model-added vs "
        "user-pinned).  Useful for checking what's already in context "
        "before adding more."
    )
    InputSchema = ContextListFilesInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        ctx = _resolve_chat_for_request(kwargs)
        if not ctx["ok"]:
            return {"error": True, "message": ctx["error"]}

        chat_data = ctx["chat_data"]
        additional = list(chat_data.get("additionalFiles") or [])
        owned_set = set(chat_data.get(_OWNERSHIP_FIELD) or [])

        files = [
            {
                "path": p,
                "owner": "model" if p in owned_set else "user",
                "removable": p in owned_set,
            }
            for p in additional
        ]

        return {
            "success": True,
            "count": len(files),
            "files": files,
            "message": (f"{len(files)} file(s) in current conversation "
                        f"context."),
        }
