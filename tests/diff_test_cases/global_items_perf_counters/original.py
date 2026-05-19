"""
Cross-project global item collection.

When a chat or folder is marked isGlobal, it should be visible in all
projects — not just the one where it was created.  The server stores
chats per-project on disk, so this module scans sibling project
directories to find globally-shared items.
"""

import json
from collections import OrderedDict
import time
from pathlib import Path
from typing import List

from app.utils.logging_utils import logger
from ..models.chat import Chat, ChatSummary
from ..models.group import ChatGroup

# Per-file mtime cache for collect_global_chat_summaries().
# Keyed by absolute path string; value is (st_mtime, st_size, ChatSummary|None).
# A None summary caches the negative result — the whole point of this cache
# is that 99% of files in a typical workspace are not isGlobal and re-parsing
# them on every /chats poll was the dominant residual cost after the
# Pydantic-skip fix.  Process-local; no eviction (bounded by total chat
# files across all projects, which retention policy bounds further).
# Self-heals on file change because mtime+size differ.
_summary_cache = {}

# Per-file mtime cache for collect_global_chats() (full Chat objects).
# Keyed by absolute path string; value is (st_mtime, st_size, Chat|None).
# Same self-healing pattern as _summary_cache: stat is ~50µs vs the
# read+decrypt+json.loads+Chat(**data) cost of ~5-20ms per file (Pydantic
# Chat validation iterates every Message).  This is hit by include_messages=true
# listings and the get_chat cross-project fallback path; on the cold-start
# path observed in production, a single getChat call that fell through to
# this function blocked a project switch for 50 seconds.
#
# Bounded LRU because each cached Chat carries its full message array — a
# user with many large conversations could otherwise grow this cache to
# hundreds of MB.  200 entries × ~1MB worst-case = ~200MB ceiling.  The
# summary cache stays unbounded (entries are tiny).
#
# Invariant: callers must NOT mutate the returned Chat objects.  They are
# shared across requests via this cache.  Constructing a defensive deep
# copy on every hit would defeat most of the perf win; the contract is
# read-only at the call sites today and we rely on that.
_FULL_CACHE_MAX = 200
_full_cache: "OrderedDict[str, tuple]" = OrderedDict()


def _full_cache_put(path_str: str, value: tuple) -> None:
    """Insert into _full_cache with LRU eviction."""
    _full_cache[path_str] = value
    _full_cache.move_to_end(path_str)
    while len(_full_cache) > _FULL_CACHE_MAX:
        _full_cache.popitem(last=False)


def collect_global_chats(
    ziya_home: Path,
    exclude_project_id: str,
) -> List[Chat]:
    """Scan all projects (except *exclude_project_id*) for isGlobal chats.

    Returns Chat objects with full messages so callers can use them for
    both summary and full-fetch responses.
    """
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        return []

    t0 = time.perf_counter()
    n_files = 0
    n_globals = 0
    t_read = 0.0
    t_parse = 0.0
    results: List[Chat] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir() or project_dir.name == exclude_project_id:
            continue

        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            continue

        for chat_file in chats_dir.glob("*.json"):
            if chat_file.name.startswith("_"):
                continue
            if chat_file.name.endswith(".bindings.json"):
                continue
            n_files += 1
            try:
                t_s = time.perf_counter()
                st = chat_file.stat()
                t_stat += time.perf_counter() - t_s

                path_str = str(chat_file)
                cached = _full_cache.get(path_str)
                if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
                    n_hit += 1
                    _full_cache.move_to_end(path_str)
                    if cached[2] is not None:
                        results.append(cached[2])
                        n_globals += 1
                    continue
                n_miss += 1

                t_r = time.perf_counter()
                raw = chat_file.read_bytes()
                if not raw:
                    _full_cache_put(path_str, (st.st_mtime, st.st_size, None))
                    continue

                from app.utils.encryption import is_encrypted, get_encryptor
                if is_encrypted(raw):
                    raw = get_encryptor().decrypt(raw)
                t_read += time.perf_counter() - t_r

                t_p = time.perf_counter()
                data = json.loads(raw)
                if not data.get("isGlobal"):
                    _full_cache_put(path_str, (st.st_mtime, st.st_size, None))
                    t_parse += time.perf_counter() - t_p
                    continue

                chat = Chat(**data)
                _full_cache_put(path_str, (st.st_mtime, st.st_size, chat))
                results.append(chat)
                n_globals += 1
                t_parse += time.perf_counter() - t_p
            except Exception as exc:
                logger.debug(f"Skipping {chat_file} during global scan: {exc}")

    logger.info(
        f"collect_global_chats: total={(time.perf_counter()-t0)*1000:.0f}ms "
        f"stat={t_stat*1000:.0f}ms read={t_read*1000:.0f}ms parse={t_parse*1000:.0f}ms "
        f"files={n_files} globals={n_globals} hit={n_hit} miss={n_miss}"
    )
    return results


def collect_global_chat_summaries(
    ziya_home: Path,
    exclude_project_id: str,
) -> List[ChatSummary]:
    """Scan all projects (except *exclude_project_id*) for isGlobal chats and
    return ChatSummary objects directly.

    This is the fast path for ``GET /chats`` (summary listing).  It skips
    ``Chat(**data)`` validation entirely — for the 99% of files that are
    not global we only pay read+decrypt+json.loads+dict.get; for the few
    that are global we build ``ChatSummary`` from the raw dict, never
    instantiating ``Chat`` or ``Message``.  Mirrors the Pydantic-skip
    pattern used by ``ChatStorage.list_summaries``.
    """
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        return []

    t0 = time.perf_counter()
    n_files = 0
    n_globals = 0
    n_hit = 0
    n_miss = 0
    t_stat = 0.0
    t_read = 0.0
    t_parse = 0.0
    results: List[ChatSummary] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir() or project_dir.name == exclude_project_id:
            continue

        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            continue

        for chat_file in chats_dir.glob("*.json"):
            if chat_file.name.startswith("_"):
                continue
            if chat_file.name.endswith(".bindings.json"):
                continue
            n_files += 1
            try:
                t_s = time.perf_counter()
                st = chat_file.stat()
                t_stat += time.perf_counter() - t_s

                path_str = str(chat_file)
                cached = _summary_cache.get(path_str)
                if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
                    n_hit += 1
                    if cached[2] is not None:
                        results.append(cached[2])
                        n_globals += 1
                    continue
                n_miss += 1

                t_r = time.perf_counter()
                raw = chat_file.read_bytes()
                if not raw:
                    _summary_cache[path_str] = (st.st_mtime, st.st_size, None)
                    continue

                from app.utils.encryption import is_encrypted, get_encryptor
                if is_encrypted(raw):
                    raw = get_encryptor().decrypt(raw)
                t_read += time.perf_counter() - t_r

                t_p = time.perf_counter()
                data = json.loads(raw)
                if not data.get("isGlobal"):
                    _summary_cache[path_str] = (st.st_mtime, st.st_size, None)
                    t_parse += time.perf_counter() - t_p
                    continue

                messages = data.get("messages") or []
                version = data.get("_version") or data.get("lastActiveAt")
                summary = ChatSummary(
                    id=data["id"],
                    title=data.get("title") or "",
                    groupId=data.get("groupId"),
                    contextIds=data.get("contextIds") or [],
                    skillIds=data.get("skillIds") or [],
                    additionalFiles=data.get("additionalFiles") or [],
                    messageCount=len(messages) if isinstance(messages, list) else 0,
                    createdAt=data.get("createdAt") or 0,
                    lastActiveAt=data.get("lastActiveAt") or 0,
                    delegateMeta=data.get("delegateMeta"),
                    **({"_version": version} if version else {}),
                    **({"isGlobal": True}),
                )
                _summary_cache[path_str] = (st.st_mtime, st.st_size, summary)
                results.append(summary)
                n_globals += 1
                t_parse += time.perf_counter() - t_p
            except Exception as exc:
                logger.debug(f"Skipping {chat_file} during global summary scan: {exc}")

    logger.info(
        f"collect_global_chat_summaries: total={(time.perf_counter()-t0)*1000:.0f}ms "
        f"stat={t_stat*1000:.0f}ms read={t_read*1000:.0f}ms parse={t_parse*1000:.0f}ms "
        f"files={n_files} globals={n_globals} hit={n_hit} miss={n_miss}"
    )
    return results


def collect_global_groups(
    ziya_home: Path,
    exclude_project_id: str,
) -> List[ChatGroup]:
    """Scan all projects (except *exclude_project_id*) for isGlobal groups/folders."""
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        return []

    results: List[ChatGroup] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir() or project_dir.name == exclude_project_id:
            continue

        groups_file = project_dir / "chats" / "_groups.json"
        if not groups_file.exists():
            continue

        try:
            raw = groups_file.read_bytes()

            from app.utils.encryption import is_encrypted, get_encryptor
            if is_encrypted(raw):
                raw = get_encryptor().decrypt(raw)

            data = json.loads(raw)
            for group_data in data.get("groups", []):
                if group_data.get("isGlobal"):
                    results.append(ChatGroup(**group_data))
        except Exception as exc:
            logger.debug(f"Skipping {groups_file} during global scan: {exc}")

    return results
