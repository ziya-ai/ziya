"""
Cross-project chat-id index.

Maps chat_id -> owning project_id so cross-project chat lookups can go
directly to the right file rather than walking every project's chats
directory.  Resolves the bulk-get hot path: walking ~10 projects with
~850 chats took 2-3s per request even when most lookups hit local
storage, because the global-chat scan and its bounded cache thrashed.

Index is lazy: built on first lookup miss by scanning all project
chats dirs once, then maintained incrementally on chat create/delete.
Process-local — survives across requests but not restarts.  A cold
start pays one full scan; subsequent lookups are O(1).

Thread-safety: the index dict is read/written from FastAPI workers.
For CPython we rely on the GIL: dict get/set is atomic and the worst
race is a stale entry, which \`_resolve_path()\` self-heals by
re-scanning if the cached file is gone.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from app.utils.logging_utils import logger

# chat_id -> project_id.  None values are not stored; absence == unknown.
_index: dict[str, str] = {}
_built: bool = False


def _scan_all_projects(ziya_home: Path) -> None:
    """Populate the index by walking every project's chats directory.

    Called once on first miss, or when an explicit rebuild is requested.
    Cost is O(total chat files); on a workspace with 850 chats this is
    ~30ms (just stat + filename — no file reads needed because the
    chat_id IS the filename stem).
    """
    global _built
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        _built = True
        return

    n_projects = 0
    n_chats = 0
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            continue
        n_projects += 1
        project_id = project_dir.name
        for chat_file in chats_dir.glob("*.json"):
            name = chat_file.name
            if name.startswith("_") or name.endswith(".bindings.json"):
                continue
            chat_id = chat_file.stem
            _index[chat_id] = project_id
            n_chats += 1
    _built = True
    logger.debug(f"chat_index: built initial index — {n_chats} chats across {n_projects} projects")


def _resolve_path(ziya_home: Path, chat_id: str, project_id: str) -> Optional[Path]:
    """Return the on-disk path for (chat_id, project_id) if it still exists."""
    p = ziya_home / "projects" / project_id / "chats" / f"{chat_id}.json"
    return p if p.exists() else None


def lookup(ziya_home: Path, chat_id: str) -> Optional[Tuple[str, Path]]:
    """Find which project owns *chat_id*, returning (project_id, path).

    Returns None if the chat doesn't exist anywhere.  Self-heals stale
    entries: if the cached project no longer has the file, we drop the
    entry and the caller treats it as not-found.  Callers should
    invalidate explicitly on writes/moves to keep the index accurate.
    """
    if not _built:
        _scan_all_projects(ziya_home)

    pid = _index.get(chat_id)
    if pid is not None:
        path = _resolve_path(ziya_home, chat_id, pid)
        if path is not None:
            return (pid, path)
        # Stale entry — file was moved or deleted out from under us.
        _index.pop(chat_id, None)

    return None


def lookup_many(ziya_home: Path, chat_ids: list[str]) -> tuple[dict[str, Path], list[str]]:
    """Bulk lookup.  Returns (resolved: chat_id -> path, missing: ids).

    Missing IDs include both genuinely-unknown ones and stale entries
    we couldn't resolve.  Caller can decide whether to rebuild or to
    treat as not-found.
    """
    if not _built:
        _scan_all_projects(ziya_home)

    resolved: dict[str, Path] = {}
    missing: list[str] = []
    for cid in chat_ids:
        pid = _index.get(cid)
        if pid is None:
            missing.append(cid)
            continue
        path = _resolve_path(ziya_home, cid, pid)
        if path is None:
            _index.pop(cid, None)
            missing.append(cid)
            continue
        resolved[cid] = path
    return resolved, missing


def on_chat_written(chat_id: str, project_id: str) -> None:
    """Update index after a chat is created or moved.  Idempotent."""
    _index[chat_id] = project_id


def on_chat_deleted(chat_id: str) -> None:
    """Remove a chat from the index after deletion."""
    _index.pop(chat_id, None)


def invalidate() -> None:
    """Force a rebuild on next lookup."""
    global _built
    _index.clear()
    _built = False
