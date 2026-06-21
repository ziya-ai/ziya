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
from .beads import count_open_beads_for_conversation
from ..models.work_item import count_open_work_items

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


# Per-project effective-global group-id cache.
# Keyed by absolute _groups.json path; value is (st_mtime, st_size, frozenset[str]).
# Holds the set of group ids in that project that are effectively global — own
# isGlobal flag OR any ancestor group global (folder-inheritance, mirroring the
# frontend folderIsEffectivelyGlobal).  Recomputed only when _groups.json
# changes; this is what makes a folder-global toggle take effect for
# cross-project surfacing WITHOUT touching individual chat-file mtimes (the
# per-chat caches below cache decision *inputs*, not the surfacing decision).
_group_global_cache = {}


def _effective_global_group_ids(project_dir: Path) -> frozenset:
    """Return the set of group ids in *project_dir* that are effectively
    global: own isGlobal OR any ancestor group (via parentId) is global.

    Mirrors the frontend folderIsEffectivelyGlobal ancestor walk — cycle-safe
    (visited set) and depth-bounded.  Result is mtime-cached per project so a
    steady-state /chats poll pays one stat() per project, not a decrypt+parse.
    """
    groups_file = project_dir / "chats" / "_groups.json"
    try:
        st = groups_file.stat()
    except OSError:
        return frozenset()

    path_str = str(groups_file)
    cached = _group_global_cache.get(path_str)
    if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]

    try:
        raw = groups_file.read_bytes()
        if not raw:
            result = frozenset()
            _group_global_cache[path_str] = (st.st_mtime, st.st_size, result)
            return result

        from app.utils.encryption import is_encrypted, get_encryptor
        if is_encrypted(raw):
            raw = get_encryptor().decrypt(raw)
        data = json.loads(raw)

        by_id = {}
        own_global = set()
        for g in data.get("groups", []) or []:
            gid = g.get("id")
            if not gid:
                continue
            by_id[gid] = g
            if g.get("isGlobal"):
                own_global.add(gid)

        eff = set()
        for gid in by_id:
            cur = gid
            seen = set()
            depth = 0
            while cur and cur not in seen and depth < 100:
                seen.add(cur)
                if cur in own_global:
                    eff.add(gid)
                    break
                parent = by_id.get(cur)
                if not parent:
                    break
                cur = parent.get("parentId")
                depth += 1

        result = frozenset(eff)
        _group_global_cache[path_str] = (st.st_mtime, st.st_size, result)
        return result
    except Exception as exc:
        logger.debug(f"_effective_global_group_ids: skipping {groups_file}: {exc}")
        return frozenset()


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
    n_hit = 0
    n_miss = 0
    t_stat = 0.0
    t_read = 0.0
    t_parse = 0.0
    results: List[Chat] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir() or project_dir.name == exclude_project_id:
            continue

        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            continue

        # Group ids in THIS project that are effectively global (own flag or
        # inherited from an ancestor folder).  A chat surfaces cross-project if
        # its own isGlobal is set OR its groupId is in this set.
        eff_groups = _effective_global_group_ids(project_dir)

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
                    own_g, grp_id, built = cached[2], cached[3], cached[4]
                    surface = own_g or (grp_id is not None and grp_id in eff_groups)
                    if not surface:
                        continue
                    if built is not None:
                        results.append(built)
                        n_globals += 1
                        continue
                    # Newly effective-global via a folder toggle (chat file
                    # unchanged, so we cached inputs but never built the Chat).
                    # Rare — fall through to read+build, then cache the object.
                n_miss += 1

                t_r = time.perf_counter()
                raw = chat_file.read_bytes()
                if not raw:
                    _full_cache_put(path_str, (st.st_mtime, st.st_size, False, None, None))
                    continue

                from app.utils.encryption import is_encrypted, get_encryptor
                if is_encrypted(raw):
                    raw = get_encryptor().decrypt(raw)
                t_read += time.perf_counter() - t_r

                t_p = time.perf_counter()
                data = json.loads(raw)
                own_g = bool(data.get("isGlobal"))
                grp_id = data.get("groupId")
                if not (own_g or (grp_id is not None and grp_id in eff_groups)):
                    _full_cache_put(path_str, (st.st_mtime, st.st_size, own_g, grp_id, None))
                    t_parse += time.perf_counter() - t_p
                    continue

                chat = Chat(**data)
                _full_cache_put(path_str, (st.st_mtime, st.st_size, own_g, grp_id, chat))
                results.append(chat)
                n_globals += 1
                t_parse += time.perf_counter() - t_p
            except Exception as exc:
                logger.debug(f"Skipping {chat_file} during global scan: {exc}")

    logger.debug(
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

        # See collect_global_chats: own-global OR group-inherited-global.
        eff_groups = _effective_global_group_ids(project_dir)

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
                    own_g, grp_id, built = cached[2], cached[3], cached[4]
                    surface = own_g or (grp_id is not None and grp_id in eff_groups)
                    if not surface:
                        continue
                    if built is not None:
                        results.append(built)
                        n_globals += 1
                        continue
                    # Newly effective-global via folder toggle — chat file
                    # unchanged, so we cached inputs but not the summary.
                    # Fall through to read+build (rare).
                n_miss += 1

                t_r = time.perf_counter()
                raw = chat_file.read_bytes()
                if not raw:
                    _summary_cache[path_str] = (st.st_mtime, st.st_size, False, None, None)
                    continue

                from app.utils.encryption import is_encrypted, get_encryptor
                if is_encrypted(raw):
                    raw = get_encryptor().decrypt(raw)
                t_read += time.perf_counter() - t_r

                t_p = time.perf_counter()
                data = json.loads(raw)
                own_g = bool(data.get("isGlobal"))
                grp_id = data.get("groupId")
                if not (own_g or (grp_id is not None and grp_id in eff_groups)):
                    _summary_cache[path_str] = (st.st_mtime, st.st_size, own_g, grp_id, None)
                    t_parse += time.perf_counter() - t_p
                    continue

                messages = data.get("messages") or []
                version = data.get("_version") or data.get("lastActiveAt")
                open_beads = count_open_beads_for_conversation(data, data.get("id"))
                open_work_items = count_open_work_items(data.get("_work_items"))
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
                    openBeadCount=open_beads,
                    openWorkItemCount=open_work_items,
                    **({"_version": version} if version else {}),
                    # Stamp the TRUE owner projectId so the client never
                    # re-homes a global chat under the viewing project. An
                    # owner-less global summary is stamped with the current
                    # project by syncMerge, then the dual-write persists a
                    # SHADOW copy into the current project's dir, which then
                    # shadows the real owner copy in list_chats — the substrate
                    # that let an ASR-folder chat be demoted to root/private.
                    # Prefer the on-disk projectId; fall back to the owning
                    # project directory name (always correct in this scan).
                    **({"projectId": data.get("projectId") or project_dir.name}),
                    **({"isGlobal": True}),
                )
                _summary_cache[path_str] = (st.st_mtime, st.st_size, own_g, grp_id, summary)
                results.append(summary)
                n_globals += 1
                t_parse += time.perf_counter() - t_p
            except Exception as exc:
                logger.debug(f"Skipping {chat_file} during global summary scan: {exc}")

    logger.debug(
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

        # Surface effectively-global groups (own flag OR inherited), so a
        # child folder of a global folder crosses over and nests under it.
        eff_groups = _effective_global_group_ids(project_dir)

        try:
            raw = groups_file.read_bytes()

            from app.utils.encryption import is_encrypted, get_encryptor
            if is_encrypted(raw):
                raw = get_encryptor().decrypt(raw)

            data = json.loads(raw)
            for group_data in data.get("groups", []):
                if group_data.get("isGlobal") or group_data.get("id") in eff_groups:
                    results.append(ChatGroup(**group_data))
        except Exception as exc:
            logger.debug(f"Skipping {groups_file} during global scan: {exc}")

    return results
