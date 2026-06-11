"""
Chat and chat group API endpoints.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import timedelta
import time
import uuid

from ..utils.logging_utils import get_mode_aware_logger
from ..models.chat import Chat, ChatCreate, ChatUpdate, ChatSummary, Message, ChatBulkSync, ChatGroupBulkSync
from ..models.group import ChatGroup, ChatGroupCreate, ChatGroupUpdate
from ..storage.projects import ProjectStorage
from ..storage.chats import ChatStorage
from ..storage.global_items import collect_global_chats, collect_global_chat_summaries, collect_global_groups
from ..storage.groups import ChatGroupStorage
from ..utils.paths import get_ziya_home, get_project_dir

logger = get_mode_aware_logger(__name__)
router = APIRouter(tags=["chats"])

def get_chat_storage(project_id: str) -> ChatStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return ChatStorage(get_project_dir(project_id))

def get_group_storage(project_id: str) -> ChatGroupStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return ChatGroupStorage(get_project_dir(project_id))

# Chat Groups

def _chat_to_summary(chat: Chat) -> ChatSummary:
    """Convert a full Chat to a ChatSummary for list endpoints."""
    chat_extra = chat.model_dump()
    version = chat_extra.get('_version') or chat.lastActiveAt
    return ChatSummary(
        id=chat.id,
        title=chat.title,
        groupId=chat.groupId,
        contextIds=chat.contextIds,
        skillIds=chat.skillIds,
        additionalFiles=chat.additionalFiles,
        messageCount=len(chat.messages),
        createdAt=chat.createdAt,
        lastActiveAt=chat.lastActiveAt,
        delegateMeta=chat.delegateMeta,
        **({'_version': version} if version else {}),
        **({'isGlobal': True}),
    )


# TaskPlan fields that are large and only needed while a plan is active.
# Once a plan reaches a terminal status, the frontend's polling/launch
# code paths short-circuit on `status` before reading these.  Stripping
# them on terminal plans keeps the chat-groups list response small
# (this user's project: ~587 KB → ~7 KB of taskPlan data).
_TERMINAL_PLAN_STATUSES = {"completed", "completed_partial", "cancelled"}
_TASKPLAN_HEAVY_FIELDS = ("task_list", "delegate_specs", "crystals", "task_graph")


def _strip_terminal_taskplans(groups):
    """Return a copy of `groups` with heavy taskPlan fields removed on terminal plans.

    Active (non-terminal) plans pass through untouched because the frontend
    polling loop reads delegate_specs/crystals on them.
    """
    out = []
    for g in groups:
        tp = getattr(g, "taskPlan", None)
        if tp and tp.get("status") in _TERMINAL_PLAN_STATUSES:
            slim = {k: v for k, v in tp.items() if k not in _TASKPLAN_HEAVY_FIELDS}
            # Pydantic BaseModel.copy(update=...) returns a shallow-modified copy.
            out.append(g.copy(update={"taskPlan": slim}))
        else:
            out.append(g)
    return out


@router.get("/api/v1/projects/{project_id}/chat-groups", response_model=List[ChatGroup])
async def list_chat_groups(project_id: str):
    """List all chat groups, including global groups from other projects.

    Heavy taskPlan fields (task_list, delegate_specs, crystals, task_graph)
    are stripped from groups whose plan has reached a terminal status —
    the frontend never reads them after that point.
    """
    storage = get_group_storage(project_id)
    groups = storage.list()

    # Include global groups from other projects
    existing_ids = {g.id for g in groups}
    ziya_home = get_ziya_home()
    for global_group in collect_global_groups(ziya_home, exclude_project_id=project_id):
        if global_group.id not in existing_ids:
            groups.append(global_group)
            existing_ids.add(global_group.id)

    return _strip_terminal_taskplans(sorted(groups, key=lambda g: g.order))

@router.post("/api/v1/projects/{project_id}/chat-groups", response_model=ChatGroup)
async def create_chat_group(project_id: str, data: ChatGroupCreate):
    """Create a chat group."""
    storage = get_group_storage(project_id)
    return storage.create(data)

@router.put("/api/v1/projects/{project_id}/chat-groups/{group_id}", response_model=ChatGroup)
async def update_chat_group(project_id: str, group_id: str, data: ChatGroupUpdate):
    """Update a chat group."""
    storage = get_group_storage(project_id)
    group = storage.update(group_id, data)
    
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    return group

@router.post("/api/v1/projects/{project_id}/chat-groups/{group_id}/global")
async def set_chat_group_global(project_id: str, group_id: str, body: dict):
    """Atomically set a folder's isGlobal flag.

    Single source of truth for the global flag.  Frontend calls this
    instead of toggling locally and relying on the bulk-sync debounced
    round-trip; this gives the toggle immediate, durable, race-free
    semantics (next sync cycle mirrors the on-disk state into IDB).
    """
    is_global = bool(body.get("isGlobal", False))
    storage = get_group_storage(project_id)
    groups_file = storage._read_groups_file()
    target = None
    for g in groups_file.groups:
        if g.id == group_id:
            target = g
            break
    if not target:
        raise HTTPException(status_code=404, detail="Group not found")
    # ChatGroup uses model_config = {"extra": "allow"}, so we can stamp
    # arbitrary fields including isGlobal directly on the model.
    extra = target.model_dump()
    extra["isGlobal"] = is_global
    extra["updatedAt"] = int(time.time() * 1000)
    # Re-validate as ChatGroup to catch corruption, then persist via the
    # storage layer's atomic file-rename pattern.
    updated = ChatGroup(**extra)
    for i, g in enumerate(groups_file.groups):
        if g.id == group_id:
            groups_file.groups[i] = updated
            break
    storage._write_groups_file(groups_file)
    logger.info(f"set_chat_group_global[{project_id[:8]}] {group_id[:8]} -> {is_global}")
    return updated


@router.delete("/api/v1/projects/{project_id}/chat-groups/{group_id}")
async def delete_chat_group(project_id: str, group_id: str):
    """Delete a chat group (chats become ungrouped)."""
    storage = get_group_storage(project_id)
    chat_storage = get_chat_storage(project_id)
    
    # Ungroup all chats in this group
    for chat in chat_storage.list(group_id=group_id):
        chat_storage.update(chat.id, ChatUpdate(groupId=None))
    
    if not storage.delete(group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    
    return {"deleted": True}

@router.put("/api/v1/projects/{project_id}/chat-groups/reorder")
async def reorder_chat_groups(project_id: str, ordered_ids: List[str]):
    """Reorder chat groups."""
    storage = get_group_storage(project_id)
    return storage.reorder(ordered_ids)

@router.post("/api/v1/projects/{project_id}/chat-groups/bulk-sync")
async def bulk_sync_groups(project_id: str, data: ChatGroupBulkSync):
    """
    Bulk upsert chat groups/folders from frontend (cross-port sync).
    For each group: if server version is newer, skip. Otherwise upsert.
    """
    storage = get_group_storage(project_id)
    
    results = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
    
    for group_data in data.groups:
        try:
            existing = storage.get(group_data.id)
            
            if existing:
                incoming_dump = group_data.model_dump()
                existing_dump = existing.model_dump()
                incoming_ver = incoming_dump.get('updatedAt') or existing_dump.get('createdAt') or 0
                existing_ver = existing_dump.get('updatedAt') or existing.createdAt or 0

                if incoming_ver >= existing_ver:
                    groups_file = storage._read_groups_file()
                    groups_file.groups = [
                        ChatGroup(**incoming_dump) if g.id == group_data.id else g
                        for g in groups_file.groups
                    ]
                    storage._write_groups_file(groups_file)
                    results["updated"] += 1
                else:
                    results["skipped"] += 1
            else:
                groups_file = storage._read_groups_file()
                groups_file.groups.append(ChatGroup(**group_data.model_dump()))
                storage._write_groups_file(groups_file)
                results["created"] += 1
        except Exception as e:
            results["errors"].append({"id": group_data.id, "error": str(e)})
    
    return results

# Chats

@router.get("/api/v1/projects/{project_id}/chats")
async def list_chats(
    project_id: str,
    group_id: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    offset: Optional[int] = Query(0),
    include_messages: bool = Query(False)
):
    """List all chats for a project. Use include_messages=true for full chat data."""
    t_start = time.perf_counter()
    t_phase = time.perf_counter()
    storage = get_chat_storage(project_id)
    t_storage_setup = time.perf_counter() - t_phase
    
    if include_messages:
        chats = storage.list(group_id=group_id)
        # Include global chats from other projects
        existing_ids = {c.id for c in chats}
        ziya_home = get_ziya_home()
        for global_chat in collect_global_chats(ziya_home, exclude_project_id=project_id):
            if global_chat.id not in existing_ids:
                chats.append(global_chat)
                existing_ids.add(global_chat.id)
        if limit:
            chats = chats[offset:offset + limit]
        return chats
    
    t_phase = time.perf_counter()
    summaries = storage.list_summaries(group_id=group_id)
    t_list_summaries = time.perf_counter() - t_phase

    t_phase = time.perf_counter()
    # Include global chat summaries from other projects (fast path: skips Chat validation)
    existing_ids = {s.id for s in summaries}
    ziya_home = get_ziya_home()
    n_global = 0
    for global_summary in collect_global_chat_summaries(ziya_home, exclude_project_id=project_id):
        if global_summary.id not in existing_ids:
            summaries.append(global_summary)
            existing_ids.add(global_summary.id)
            n_global += 1
    t_globals = time.perf_counter() - t_phase
    
    t_phase = time.perf_counter()
    # Apply pagination
    if limit:
        summaries = summaries[offset:offset + limit]
    t_paginate = time.perf_counter() - t_phase

    elapsed_ms = (time.perf_counter() - t_start) * 1000
    logger.debug(
        f"list_chats[{project_id[:8]}] {len(summaries)} summaries in {elapsed_ms:.0f}ms "
        f"(setup={t_storage_setup*1000:.0f}ms list_summaries={t_list_summaries*1000:.0f}ms "
        f"globals={t_globals*1000:.0f}ms[+{n_global}] paginate={t_paginate*1000:.0f}ms)"
    )
    
    return summaries

@router.post("/api/v1/projects/{project_id}/chats", response_model=Chat)
async def create_chat(project_id: str, data: ChatCreate):
    """Create a new chat."""
    storage = get_chat_storage(project_id)
    group_storage = get_group_storage(project_id)
    
    # Get default contexts/skills from group or project
    default_context_ids = None
    default_skill_ids = None
    
    if data.groupId:
        group = group_storage.get(data.groupId)
        if group:
            default_context_ids = group.defaultContextIds
            default_skill_ids = group.defaultSkillIds
    
    if default_context_ids is None:
        # Use project defaults
        project_storage = ProjectStorage(get_ziya_home())
        project = project_storage.get(project_id)
        if project:
            default_context_ids = project.settings.defaultContextIds
            default_skill_ids = project.settings.defaultSkillIds
    
    return storage.create(data, default_context_ids, default_skill_ids)

@router.post("/api/v1/projects/{project_id}/chats/bulk-sync")
async def bulk_sync_chats(project_id: str, data: ChatBulkSync):
    """
    Bulk upsert chats from frontend (IndexedDB migration).
    For each chat: if it exists on server and server version is newer, skip.
    Otherwise, create or overwrite with the provided data.
    """
    storage = get_chat_storage(project_id)
    
    results = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
    
    for chat_data in data.chats:
        try:
            # Read without retention check — bulk-sync should not trigger
            # deletion of expired chats mid-sync (causes a delete→recreate loop).
            raw = storage._read_json(storage._chat_file(chat_data.id))
            existing = Chat(**raw) if raw else None
            
            if existing:
                # Use _version (frontend's authoritative version counter) for
                # comparison, falling back to lastActiveAt for pre-_version data.
                existing_extra = existing.model_dump()
                incoming_extra = chat_data.model_dump()
                incoming_ver = incoming_extra.get('_version') or chat_data.lastActiveAt or chat_data.lastAccessedAt or 0
                existing_ver = existing_extra.get('_version') or existing.lastActiveAt or 0

                if incoming_ver >= existing_ver:
                    merged = chat_data.model_dump()
                    # Strip persisted empty assistant turns (Bedrock empty-200
                    # poison) from BOTH sides before the regression guards run.
                    # Sanitizing only the incoming side would trip the
                    # count-regression guard below, which would restore the
                    # poisoned on-disk history and silently undo the fix.
                    existing_msgs = storage.strip_empty_assistant_messages(
                        [m.model_dump() for m in existing.messages] if existing.messages else []
                    )
                    merged['messages'] = storage.strip_empty_assistant_messages(merged.get('messages', []))
                    # Message-count guard: refuse any update that reduces
                    # message count below what the server already has.
                    # The previous `> 2` threshold was a hole that allowed
                    # shells to overwrite already-damaged 2-message records,
                    # compounding history loss.  Legitimate shrinkage (user
                    # deleting a message) must go through an explicit delete
                    # endpoint, not bulk-sync.
                    existing_msg_count = len(existing_msgs)
                    incoming_msg_count = len(merged.get('messages', []))
                    if existing_msg_count >= 1 and incoming_msg_count < existing_msg_count:
                        logger.warning(
                            f"bulk-sync: blocking message regression for {chat_data.id} "
                            f"({existing_msg_count} -> {incoming_msg_count} messages)")
                        merged['messages'] = existing_msgs
                    # Content-length guard: catch same-count shell overwrites
                    # (e.g. 2 real msgs replaced by 2 blanked-content shells).
                    elif incoming_msg_count == existing_msg_count and existing_msg_count > 0:
                        existing_len = sum(len((m.get('content') or '')) for m in existing_msgs)
                        incoming_len = sum(len((m.get('content') or '')) for m in merged.get('messages', []))
                        if existing_len > 0 and incoming_len < existing_len // 4:
                            logger.warning(
                                f"bulk-sync: blocking content regression for {chat_data.id} "
                                f"({existing_len} -> {incoming_len} chars, same msg count)")
                            merged['messages'] = existing_msgs
                    if merged.get('delegateMeta') is None and existing.delegateMeta is not None:
                        merged['delegateMeta'] = existing.delegateMeta.model_dump() \
                            if hasattr(existing.delegateMeta, 'model_dump') \
                            else existing.delegateMeta
                    # Preserve backend-owned _beads across the frontend round-trip.
                    # The conversation task-tree (beads) is written ONLY by the
                    # backend bead tools onto the chat record's _beads extra field.
                    # The frontend never carries _beads (conversationToServerChat
                    # spreads the IDB conversation, which has no such field), so a
                    # bulk-sync that didn't preserve it would overwrite the chat
                    # file ~2s after every bead write and silently wipe the tree —
                    # the "no threads yet" symptom.  Mirror the delegateMeta guard:
                    # carry the on-disk value forward when the incoming payload
                    # omits it.
                    if not merged.get('_beads') and existing_extra.get('_beads'):
                        merged['_beads'] = existing_extra['_beads']
                    # Map frontend's folderId to server's groupId FIRST.  The
                    # frontend's authoritative field is folderId; groupId is
                    # absent from its payload.  This must run before the
                    # "preserve existing groupId" guard below, otherwise a
                    # move-to-folder push (folderId=<new>, groupId absent)
                    # gets reverted: the guard sees groupId=None and restores
                    # the previous groupId from disk, then this mapping is
                    # skipped because groupId is no longer None.
                    if merged.get('folderId') and not merged.get('groupId'):
                        merged['groupId'] = merged['folderId']
                    # Preserve existing groupId only when the incoming payload
                    # specified neither groupId nor folderId.
                    if merged.get('groupId') is None and existing.groupId is not None:
                        merged['groupId'] = existing.groupId
                    storage._write_json(
                        storage._chat_file(chat_data.id),
                        merged
                    )
                    results["updated"] += 1
                else:
                    results["skipped"] += 1
            else:
                # Create new — apply the same folderId → groupId mapping the
                # update branch uses.  Without this, the first push of a chat
                # the frontend calls `folderId` gets persisted with groupId=null
                # and the chat appears ungrouped until a subsequent update
                # triggers the mapping.  That intermittent "forgets folder on
                # first save" is observationally identical to the class of
                # sync bugs we've been chasing.
                payload = chat_data.model_dump()
                if payload.get('folderId') and not payload.get('groupId'):
                    payload['groupId'] = payload['folderId']
                storage._write_json(storage._chat_file(chat_data.id), payload)
                results["created"] += 1
        except Exception as e:
            results["errors"].append({"id": chat_data.id, "error": str(e)})
    
    return results
@router.get("/api/v1/projects/{project_id}/chats/{chat_id}", response_model=Chat)
async def get_chat(project_id: str, chat_id: str):
    """Get full chat including messages.

    This is a pure read — it does not update any timestamps.
    lastActiveAt is only bumped by actual mutations (add_message, update, bulk-sync).
    """
    storage = get_chat_storage(project_id)
    chat = storage.get(chat_id)
    
    if chat:
        return chat

    # Chat not in this project — check if it's a global chat in another project
    ziya_home = get_ziya_home()
    for global_chat in collect_global_chats(ziya_home, exclude_project_id=project_id):
        if global_chat.id == chat_id:
            return global_chat

    raise HTTPException(status_code=404, detail="Chat not found")

@router.post("/api/v1/projects/{project_id}/chats/bulk-get")
async def bulk_get_chats(project_id: str, body: dict):
    """Fetch many chats in a single request.

    Per-request /chats/{id} fetches show ~14ms when isolated but ~900ms
    each under 56-way parallel load — the server's per-request work
    (decryption key derivation, file-lock contention) doesn't amortize
    across parallel HTTP requests.  Bundling N reads into one call lets
    that overhead be paid once, dropping wall time by an order of
    magnitude.

    Body: {"ids": ["chat-id-1", "chat-id-2", ...]}
    Response: {"chats": [Chat, ...], "missing": ["chat-id-3", ...]}

    Missing IDs are returned separately rather than as nulls so the
    client can mark them as confirmed-empty (vs network error).
    """
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    if len(ids) > 200:
        raise HTTPException(status_code=400, detail="bulk-get limit is 200 ids per request")

    storage = get_chat_storage(project_id)
    chats: list = []
    missing: list = []
    # First pass: local project storage.
    cross_project_ids: list = []
    for cid in ids:
        chat = storage.get(cid)
        if chat:
            chats.append(chat)
        else:
            cross_project_ids.append(cid)

    # Second pass: resolve cross-project IDs via the chat index.  This
    # replaces the previous `collect_global_chats` walk which scanned
    # every project's chats directory and was the dominant cost in
    # bulk-get (2-3s per request even with concurrency=1).
    if cross_project_ids:
        from app.storage import chat_index
        from app.storage.chats import ChatStorage
        ziya_home = get_ziya_home()
        resolved, idx_missing = chat_index.lookup_many(ziya_home, cross_project_ids)
        # Group resolved IDs by owning project so we hit each project's
        # ChatStorage.get (which uses its own caches and decryption keys
        # correctly) rather than reading raw JSON ourselves.
        by_project: dict[str, list[str]] = {}
        for cid, path in resolved.items():
            owning_pid = path.parent.parent.name  # .../projects/<pid>/chats/<id>.json
            by_project.setdefault(owning_pid, []).append(cid)
        for owning_pid, owning_ids in by_project.items():
            owning_storage = get_chat_storage(owning_pid)
            for cid in owning_ids:
                chat = owning_storage.get(cid)
                if chat:
                    chats.append(chat)
                else:
                    # Index said the file existed but storage couldn't load
                    # it — treat as missing so the client can retry next sync.
                    idx_missing.append(cid)
        missing = idx_missing

    return {"chats": chats, "missing": missing}


@router.put("/api/v1/projects/{project_id}/chats/{chat_id}", response_model=Chat)
async def update_chat(project_id: str, chat_id: str, data: ChatUpdate):
    """Update chat metadata."""
    storage = get_chat_storage(project_id)
    chat = storage.update(chat_id, data)
    
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    return chat


@router.post("/api/v1/projects/{project_id}/chats/{chat_id}/global")
async def set_chat_global(project_id: str, chat_id: str, body: dict):
    """Atomically set a chat's isGlobal flag.

    Single source of truth for the global flag.  Frontend calls this
    instead of toggling locally and relying on bulk-sync; this gives
    the toggle immediate, durable, race-free semantics.
    """
    is_global = bool(body.get("isGlobal", False))
    storage = get_chat_storage(project_id)
    raw = storage._read_json(storage._chat_file(chat_id))
    if not raw:
        raise HTTPException(status_code=404, detail="Chat not found")
    raw["isGlobal"] = is_global
    # Bump _version and lastActiveAt so the next sync wins over any
    # in-flight bulk-sync that doesn't carry the new flag.
    now_ms = int(time.time() * 1000)
    raw["_version"] = now_ms
    raw["lastActiveAt"] = now_ms
    storage._write_json(storage._chat_file(chat_id), raw)
    logger.info(f"set_chat_global[{project_id[:8]}] {chat_id[:8]} -> {is_global}")
    # Invalidate both summary caches keyed by this file's path.  The
    # ChatStorage.list_summaries cache lives in app/storage/chats.py;
    # the cross-project global-summary cache lives in
    # app/storage/global_items.py.  Both are mtime-self-healing, but
    # popping is cheap and gives immediate consistency for the next read.
    try:
        path_str = str(storage._chat_file(chat_id))
        from app.storage.chats import _summary_cache as _chats_cache
        from app.storage.global_items import _summary_cache as _global_cache
        _chats_cache.pop(path_str, None)
        _global_cache.pop(path_str, None)
    except (ImportError, KeyError, TypeError):
        pass  # Cache modules not loaded or key absent
    return Chat(**raw)


# ── Retention policy endpoint ────────────────────────────────────────

@router.get("/api/v1/retention-policy")
async def get_retention_policy():
    """
    Return the effective data retention policy.

    The frontend uses this to purge expired conversations from IndexedDB
    and to display retention notices to the user.
    """
    from app.plugins.data_retention import get_retention_enforcer

    enforcer = get_retention_enforcer()
    policy = enforcer.policy

    ttl_seconds = policy.get_ttl_seconds("conversation_data")

    return {
        "conversation_data_ttl_seconds": ttl_seconds,
        "conversation_data_ttl_days": (
            ttl_seconds / 86400.0 if ttl_seconds is not None else None
        ),
        "policy_reason": policy.policy_reason,
        "has_retention_policy": ttl_seconds is not None,
    }

@router.delete("/api/v1/projects/{project_id}/chats/{chat_id}")
async def delete_chat(project_id: str, chat_id: str):
    """Delete a chat."""
    storage = get_chat_storage(project_id)
    
    if not storage.delete(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    
    return {"deleted": True}

@router.post("/api/v1/projects/{project_id}/chats/{chat_id}/messages", response_model=Chat)
async def add_message(project_id: str, chat_id: str, message_data: Message):
    """Add a message to a chat."""
    storage = get_chat_storage(project_id)
    
    # Generate ID if not provided
    if not message_data.id:
        message_data.id = str(uuid.uuid4())
    
    chat = storage.add_message(chat_id, message_data)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    return chat


# ── Timestamp repair ───────────────────────────────────────────────

@router.post("/api/v1/projects/{project_id}/chats/repair-timestamps")
async def repair_timestamps(project_id: str, dry_run: bool = Query(True)):
    """
    Repair inflated lastActiveAt / lastAccessedAt / _version timestamps.

    The touch-on-read bug caused these to jump to "now" whenever the sync
    loop fetched a conversation.  This endpoint resets them to the latest
    message timestamp in any chat where the gap exceeds 1 hour.

    Runs as dry_run=true by default; pass dry_run=false to apply.
    """
    THRESHOLD_MS = 3600 * 1000  # 1 hour

    storage = get_chat_storage(project_id)
    if not storage.chats_dir.exists():
        return {"scanned": 0, "repaired": 0, "repairs": []}

    repaired = 0
    scanned = 0
    repairs = []

    for chat_file in storage.chats_dir.glob("*.json"):
        if chat_file.name.startswith("_"):
            continue
        scanned += 1

        data = storage._read_json(chat_file)
        if not data:
            continue

        messages = data.get("messages", [])
        if not messages:
            continue

        last_msg_ts = max(
            m.get("_timestamp", m.get("timestamp", 0)) for m in messages
        )
        if last_msg_ts == 0:
            continue

        changed = False
        fixed_fields = []
        for field in ("lastActiveAt", "lastAccessedAt", "_version"):
            val = data.get(field, 0) or 0
            if (val - last_msg_ts) > THRESHOLD_MS:
                fixed_fields.append(field)
                data[field] = last_msg_ts
                changed = True

        if changed:
            title = (data.get("title") or "Untitled")[:60]
            repairs.append({"id": data.get("id"), "title": title, "fields": fixed_fields})
            if not dry_run:
                storage._write_json(chat_file, data)
            repaired += 1

    return {"scanned": scanned, "repaired": repaired, "dry_run": dry_run, "repairs": repairs}
