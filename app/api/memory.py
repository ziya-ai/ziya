"""
Memory API endpoints — CRUD for the structured memory system.

Provides REST endpoints for the frontend to manage memories:
browse, search, edit, delete, and handle proposals.

Handlers that await nothing are plain ``def``: every one reads or writes the
on-disk memory store, and a zero-await ``async def`` would run that I/O ON the
event loop, stalling every request.  Starlette runs ``def`` handlers on its
threadpool.  Only organize (asyncio.Lock + create_task) and the embedding
backfill (awaits) stay async.  Guarded by
tests/test_memory_not_event_loop_blocking.py.
"""
import asyncio
import time

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel, Field

from app.utils.logging_utils import get_mode_aware_logger
from app.models.memory import MindMapNode
from app.utils.memory_export import ExportSelection
from app.utils.memory_import import ImportOptions

logger = get_mode_aware_logger(__name__)
router = APIRouter(tags=["memory"])


# Module-level state for background organize task
_organize_lock = asyncio.Lock()
_organize_task_status: dict = {"running": False, "result": None, "error": None, "started_at": None}

# -- Request models ----------------------------------------------------------

class MemorySaveRequest(BaseModel):
    content: str = Field(..., min_length=10, max_length=2000,
                         description="Memory content (10-2000 chars)")
    layer: str = Field("domain_context", pattern=r"^(domain_context|architecture|lexicon|decision|"
                        r"negative_constraint|preference|process|active_thread)$")
    tags: List[str] = Field(default_factory=list)
    learned_from: str = "explicit_save"


class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    layer: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None
    scope: Optional[dict] = None


# -- Endpoints ---------------------------------------------------------------

@router.get("/api/v1/memory")
def get_memory_status():
    """Overview: counts by layer and status, pending proposal count."""
    from app.storage.memory import get_memory_storage
    from app.storage.proposals import get_proposals_store
    store = get_memory_storage()
    counts = store.count()
    # The probationary store is the queue the lifecycle engine drains;
    # the legacy proposals.json is a stale user-facing remnant.
    pending = len(get_proposals_store().list_open())
    return {**counts, "pending_proposals": pending}


@router.get("/api/v1/memory/search")
def search_memories(
    q: str = Query("", description="Search query"),
    layer: Optional[str] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search the flat memory store."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    if q:
        results = store.search(q, limit=limit)
    else:
        results = store.list_memories(layer=layer, tags=tag_list)[:limit]

    return [m.model_dump() for m in results]


@router.get("/api/v1/memory/all")
def list_all_memories():
    """Return every active memory (for the memory browser UI)."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    active = store.list_memories(status="active")
    contested = store.list_memories(status="contested")
    return [m.model_dump() for m in active + contested]


@router.post("/api/v1/memory")
def save_memory(data: MemorySaveRequest):
    """Save a new memory directly."""
    from app.storage.memory import get_memory_storage
    from app.models.memory import Memory
    store = get_memory_storage()
    memory = Memory(
        content=data.content,
        layer=data.layer,
        tags=data.tags,
        learned_from=data.learned_from,
    )
    saved = store.save(memory)
    return saved.model_dump()


@router.put("/api/v1/memory/{memory_id}")
def update_memory(memory_id: str, data: MemoryUpdateRequest):
    """Edit an existing memory."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    existing = store.get(memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Memory not found")

    update = data.model_dump(exclude_unset=True)
    # Handle scope separately — needs conversion to MemoryScope
    if 'scope' in update:
        from app.models.memory import MemoryScope
        scope_data = update.pop('scope')
        if isinstance(scope_data, dict):
            # Merge: only overwrite fields that are explicitly provided
            if 'project_paths' in scope_data:
                existing.scope.project_paths = scope_data['project_paths']
            if 'domain_node' in scope_data:
                existing.scope.domain_node = scope_data['domain_node']
    for key, value in update.items():
        setattr(existing, key, value)
    store.save(existing)
    return existing.model_dump()


@router.delete("/api/v1/memory/{memory_id}")
def delete_memory(memory_id: str):
    """Delete a memory."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    if not store.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


# -- Proposals ---------------------------------------------------------------

# These endpoints read the PROBATIONARY store, which is swept automatically by
# run_lifecycle_pass().  They exist for observability — a human can watch what
# the model is proposing and how entries earn their way in — and for optional
# intervention.  Nothing here is required: an untouched queue resolves itself.

@router.get("/api/v1/memory/proposals")
def list_proposals():
    """List probationary proposals with their promotion progress."""
    from app.storage.proposals import get_proposals_store
    from app.memory.lifecycle import (
        current_activity_count, ARCHIVAL_AGE_THRESHOLD, _evaluate_promotion,
    )
    from app.utils.memory_clock import build_context, effective_counter
    store = get_proposals_store()
    counter = current_activity_count()
    try:
        from app.storage.memory import get_memory_storage
        _key_by_id, _coll = get_memory_storage()._provenance()
        clock_ctx = build_context(_coll)
    except Exception:
        clock_ctx = None
    out = []
    for row in store.list_open():
        # Same per-domain counter the lifecycle sweep uses, so "age" and
        # "would_promote" match what the next sweep will actually do.
        row_counter = effective_counter(row, counter, clock_ctx)
        age = max(0, row_counter - int(row.get("activity_count_at_proposal", 0) or 0))
        out.append({
            **row,
            # Why this entry has not promoted yet, and when it would expire.
            "corroborations": row.get("corroborations", 0),
            "signals": [s.get("name") for s in (row.get("signals") or [])],
            "age": age,
            "expires_in": max(0, ARCHIVAL_AGE_THRESHOLD - age),
            "would_promote": _evaluate_promotion(row, row_counter),
        })
    return out


@router.post("/api/v1/memory/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str):
    """Manually promote a proposal ahead of its automatic schedule.

    Reuses the lifecycle promotion path so a hand-approved entry is
    indistinguishable from one that earned promotion: corroborations,
    scope, provenance and the re-embed under the new ID all carry over.
    """
    from app.storage.proposals import get_proposals_store
    from app.memory.lifecycle import _promote_proposal
    store = get_proposals_store()
    row = store.get(proposal_id)
    if not row or row.get("status") != "open":
        raise HTTPException(status_code=404, detail="Proposal not found")
    memory_id = _promote_proposal(row, "manual_approval")
    if not memory_id:
        raise HTTPException(status_code=500, detail="Promotion failed")
    from app.storage.memory import get_memory_storage
    mem = get_memory_storage().get(memory_id)
    return mem.model_dump() if mem else {"id": memory_id}


@router.post("/api/v1/memory/proposals/approve-all")
def approve_all_proposals():
    """Promote every open proposal now, bypassing the earn-it bar."""
    from app.storage.proposals import get_proposals_store
    from app.memory.lifecycle import _promote_proposal
    store = get_proposals_store()
    approved = []
    for row in store.list_open():
        if _promote_proposal(row, "manual_approval_bulk"):
            approved.append(row.get("id"))
    return {"approved": len(approved), "memories": approved}


@router.delete("/api/v1/memory/proposals/{proposal_id}")
def dismiss_proposal(proposal_id: str):
    """Archive a proposal now instead of waiting for it to expire."""
    from app.storage.proposals import get_proposals_store
    store = get_proposals_store()
    if not store.mark_archived(proposal_id, reason="dismissed_by_user"):
        raise HTTPException(status_code=404, detail="Proposal not found")
    return {"dismissed": True}


# -- Review (Phase 2) -------------------------------------------------------

@router.get("/api/v1/memory/pause")
def get_memory_pause():
    """Whether memory is paused after an infrastructure outage, and why."""
    from app.utils import memory_pause
    rec = memory_pause.status()
    return {"paused": rec is not None, "record": rec}


@router.post("/api/v1/memory/resume")
def resume_memory():
    """Human-in-the-loop continuation: clear the pause written when a memory
    pass hit an outage (expired credentials, network).  The next turn
    extracts normally; the lifecycle job's next tick catches up."""
    from app.utils import memory_pause
    rec = memory_pause.resume()
    return {"resumed": rec is not None, "cleared": rec}


@router.get("/api/v1/memory/review")
def get_review():
    """Surface stale memories, oversized nodes, and orphan memories for cleanup."""
    from app.storage.memory import get_memory_storage
    from app.memory import get_review_summary
    store = get_memory_storage()
    return get_review_summary(store)


@router.get("/api/v1/memory/feedback/stats")
def get_feedback_stats():
    """Best-cosine distribution of the retrieval 'used' signal vs. its threshold.

    Use this to calibrate ZIYA_MEMORY_USE_THRESHOLD: a p90 far below the
    threshold means the signal never fires; a p50 above it means it is noise.
    """
    from app.memory.feedback import feedback_stats_summary
    return feedback_stats_summary()


@router.post("/api/v1/memory/maintenance")
def run_maintenance():
    """Trigger a full maintenance pass: mind-map repair (including the
    provenance/score rebalance), then cell division + cross-links for all
    nodes."""
    from app.storage.memory import get_memory_storage
    from app.memory import maybe_divide_node, discover_cross_links
    from app.memory.maintenance import discover_cross_links_by_embedding
    store = get_memory_storage()
    results = {"repaired": store.repair_mindmap(), "divided": [], "cross_linked": []}
    _centroids = {}
    # PenPal #53: share the node list + branch cache across the loop so the
    # tag-overlap cross-linker is O(n) disk reads instead of O(n^2).
    _nodes = store.list_mindmap_nodes()
    _branch_cache = {}
    for node in _nodes:
        divided = maybe_divide_node(store, node.id)
        results["divided"].extend(divided)
        links = discover_cross_links(store, node.id, node_list=_nodes,
                                     branch_cache=_branch_cache)
        results["cross_linked"].extend(links)
        results["cross_linked"].extend(
            discover_cross_links_by_embedding(store, node.id, _centroids))
    return results


@router.post("/api/v1/memory/organize")
async def organize_memories():
    """Trigger full LLM-powered reorganization: cluster, place, relate, cross-link.

    Runs as a background task — returns immediately with status 'started'.
    Poll GET /api/v1/memory/organize/status for progress.
    """
    from app.memory import reorganize

    async with _organize_lock:
        if _organize_task_status.get("running"):
            return {"status": "already_running", "started_at": _organize_task_status.get("started_at")}
        _organize_task_status.update({"running": True, "started_at": time.time(), "result": None, "error": None})

    async def _run():
        try:
            result = await reorganize()
            async with _organize_lock:
                _organize_task_status.update({"running": False, "result": result, "error": None})
        except Exception as e:
            logger.error(f"Organization failed: {e}")
            async with _organize_lock:
                _organize_task_status.update({"running": False, "result": None, "error": str(e)})

    asyncio.create_task(_run())
    return {"status": "started"}


@router.get("/api/v1/memory/organize/status")
def organize_status():
    """Poll for organize task completion."""
    return _organize_task_status


@router.get("/api/v1/memory/organize/history")
def organize_history():
    """Bounded log of past organize runs (newest-first).

    Drives the Memory Browser's Recent Activity tab.
    """
    from app.memory import load_organize_history
    return load_organize_history()


@router.post("/api/v1/memory/embeddings/backfill")
async def backfill_embeddings():
    """Trigger embedding backfill for all memories missing vectors."""
    from app.storage.memory import get_memory_storage
    from app.services.embedding_service import get_embedding_cache, backfill_embeddings as do_backfill, get_embedding_provider, NoopProvider
    # First call probes Bedrock; the store read decrypts the whole file.
    provider = await asyncio.to_thread(get_embedding_provider)
    if isinstance(provider, NoopProvider):
        return {"status": "disabled", "message": "Embedding provider not configured"}
    store = get_memory_storage()
    memories = await asyncio.to_thread(store.list_memories, status="active")
    cache = get_embedding_cache()
    all_ids = [m.id for m in memories]
    missing = cache.missing_ids(all_ids)
    if not missing:
        return {"status": "complete", "total": len(memories), "missing": 0}
    to_embed = [(m.id, m.content) for m in memories if m.id in set(missing)]
    count = await do_backfill(to_embed)
    return {"status": "complete", "embedded": count, "total": len(memories)}


@router.get("/api/v1/memory/embeddings/status")
def embedding_status():
    """Check embedding coverage."""
    from app.storage.memory import get_memory_storage
    from app.services.embedding_service import get_embedding_cache, get_embedding_provider, NoopProvider
    provider = get_embedding_provider()
    if isinstance(provider, NoopProvider):
        return {"enabled": False, "provider": "none"}
    store = get_memory_storage()
    memories = store.list_memories(status="active")
    cache = get_embedding_cache()
    total = len(memories)
    cached = cache.count
    missing = len(cache.missing_ids([m.id for m in memories]))
    # Vectors for proposals, deleted, or archived memories.  Large values
    # here mean the semantic search leg is being diluted.
    non_active = cached - (total - missing)
    return {"enabled": True, "provider": "bedrock_titan", "total": total,
            "embedded": cached, "missing": missing, "non_active_vectors": non_active}


# -- Mind-Map ----------------------------------------------------------------

class MindMapNodeRequest(BaseModel):
    id: str
    handle: str
    parent: Optional[str] = None
    children: List[str] = Field(default_factory=list)
    cross_links: List[str] = Field(default_factory=list)
    memory_refs: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


@router.get("/api/v1/memory/mindmap")
def get_mindmap():
    """Return the full mind-map tree."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    nodes = store.list_mindmap_nodes()
    return [n.model_dump() for n in nodes]


@router.get("/api/v1/memory/mindmap/{node_id}")
def get_mindmap_node(node_id: str):
    """Return a single mind-map node with children context."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    ctx = store.get_node_with_context(node_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Node not found")
    return ctx


@router.post("/api/v1/memory/mindmap")
def create_mindmap_node(data: MindMapNodeRequest):
    """Create or update a mind-map node."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    node = MindMapNode(**data.model_dump())
    saved = store.save_mindmap_node(node)
    return saved.model_dump()


@router.delete("/api/v1/memory/mindmap/{node_id}")
def delete_mindmap_node(node_id: str):
    """Delete a mind-map node (children are reparented)."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    if not store.delete_mindmap_node(node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    return {"deleted": True}


@router.post("/api/v1/memory/mindmap/{node_id}/expand")
def expand_mindmap_node(node_id: str):
    """Return all memories under a node and its descendants."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    memories = store.expand_node(node_id)
    node = store.get_mindmap_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return {
        "node": node.model_dump(),
        "memories": [m.model_dump() for m in memories],
        "count": len(memories),
    }


# -- Export ------------------------------------------------------------------

# The one sanctioned path for memory to leave the machine (design/
# structured-memory-system.md: "never shared without explicit user action").
# Selection and sanitization live in app/utils/memory_export.py; this layer
# adds the bounds check (preview fingerprint must match) and the audit event.

class MemoryExportRequest(BaseModel):
    selection: ExportSelection
    fingerprint: str = Field(..., min_length=8, max_length=64,
                             description="Fingerprint returned by the preview.")
    acknowledge_warnings: bool = Field(
        False, description="Required when the preview reported leak warnings.")
    # Where the server should write the file: a folder or a file path (see
    # memory_export.resolve_save_path).  None returns a browser download.
    save_path: Optional[str] = Field(None, max_length=4096)
    overwrite: bool = Field(False, description="Replace an existing file at save_path.")


@router.get("/api/v1/memory/export/options")
def memory_export_options():
    """Selectable axes: domain tree, projects, unscoped count, layers,
    and the folder the save dialog starts in."""
    from app.storage.memory import get_memory_storage
    from app.utils.memory_export import default_export_dir, export_options
    return {**export_options(get_memory_storage()), "default_save_dir": default_export_dir()}


@router.post("/api/v1/memory/export/preview")
def memory_export_preview(selection: ExportSelection):
    """Resolve a selection without exporting: counts, what was held back
    and why, leak warnings, and the fingerprint the export call must echo."""
    from app.storage.memory import get_memory_storage
    from app.utils.memory_export import export_filename, plan_export
    plan, sample = plan_export(get_memory_storage(), selection, sample_size=50)
    return {**plan.model_dump(), "sample": sample,
            "suggested_filename": export_filename(plan)}


@router.post("/api/v1/memory/export")
def memory_export(data: MemoryExportRequest):
    """Produce the export bundle.

    With \`\`save_path\`\` the server writes the file there and returns
    \`\`{path, filename, total, fingerprint}\`\`.  The absolute path is the
    point: a browser download never tells the page where the file went.
    Without it the bundle is returned as a JSON download.

    Refuses with 409 when the store no longer matches the previewed
    fingerprint (a memory was added, archived or re-scoped in between, or
    the request was not preceded by a preview), with 412 when the preview
    had warnings the caller has not acknowledged, with 409
    \`\`{"code": "file_exists"}\`\` when the target exists and \`\`overwrite\`\` is
    false, and with 422 when the target folder does not exist.
    """
    import json as _json
    import os
    from fastapi.responses import Response
    from app.storage.memory import get_memory_storage
    from app.utils.memory_export import (
        build_bundle, export_filename, plan_export, resolve_save_path, write_export_file)
    from app.utils.tool_audit_log import log_security_event

    store = get_memory_storage()
    plan, _ = plan_export(store, data.selection)
    if plan.fingerprint != data.fingerprint:
        log_security_event("memory_export_refused", "memory_export", {
            "reason": "fingerprint_mismatch", "expected": plan.fingerprint,
            "got": data.fingerprint, "total": plan.total})
        raise HTTPException(
            status_code=409,
            detail="The memory store changed since the preview. Re-run the "
                   "preview and check the result before exporting.")
    if plan.warnings and not data.acknowledge_warnings:
        raise HTTPException(
            status_code=412,
            detail=f"{len(plan.warnings)} warning(s) must be acknowledged "
                   "before exporting.")
    try:
        bundle = build_bundle(store, data.selection, plan,
                              exported_by=os.environ.get("USER") or None)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    filename = export_filename(plan)
    content = _json.dumps(bundle, indent=2, ensure_ascii=False)
    saved_to = None
    if data.save_path:
        try:
            target = resolve_save_path(data.save_path, filename)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        try:
            write_export_file(target, content, overwrite=data.overwrite)
        except FileExistsError:
            raise HTTPException(status_code=409, detail={
                "code": "file_exists", "path": str(target),
                "message": f"{target} already exists."})
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Could not write {target}: {e}")
        saved_to = str(target)

    log_security_event("memory_exported", "memory_export", {
        "total": plan.total,
        "fingerprint": plan.fingerprint,
        "domains": data.selection.domains,
        "include_projects": data.selection.include_projects,
        "exclude_projects": data.selection.exclude_projects,
        "strip_provenance": data.selection.strip_provenance,
        "anonymize_paths": data.selection.anonymize_paths,
        "warnings_acknowledged": len(plan.warnings),
        "saved_to": saved_to,
    })
    if saved_to:
        return {"path": saved_to, "filename": os.path.basename(saved_to),
                "total": plan.total, "fingerprint": plan.fingerprint}
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Ziya-Export-Fingerprint": plan.fingerprint,
            "X-Ziya-Export-Count": str(plan.total),
        },
    )


# -- Import ------------------------------------------------------------------

# Merge another user's bundle into this store.  Same shape as the export:
# preview (dispositions + two fingerprints), then import echoing both, so
# the merge can never differ from what the user looked at.  Selection and
# merge logic live in app/utils/memory_import.py.

class MemoryImportPreviewRequest(BaseModel):
    bundle: dict
    options: ImportOptions = Field(default_factory=ImportOptions)


class MemoryImportRequest(BaseModel):
    bundle: dict
    options: ImportOptions = Field(default_factory=ImportOptions)
    bundle_fingerprint: str = Field(..., min_length=8, max_length=64)
    store_fingerprint: str = Field(..., min_length=8, max_length=64)
    source: Optional[str] = Field(None, max_length=255,
                                  description="File name, recorded on each memory.")


def _load_bundle_or_422(obj: dict) -> dict:
    from app.utils.memory_import import load_bundle
    try:
        return load_bundle(obj)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/api/v1/memory/import/preview")
def memory_import_preview(data: MemoryImportPreviewRequest):
    """Classify every memory in the bundle against this store without
    writing: new / identical / duplicate_content / collision_*, the
    mind-map nodes that would be created or merged, and the fingerprints
    the import call must echo."""
    from app.storage.memory import get_memory_storage
    from app.utils.memory_import import plan_import, preview_rows
    bundle = _load_bundle_or_422(data.bundle)
    plan = plan_import(get_memory_storage(), bundle, data.options)
    return {
        **plan.model_dump(exclude={"dispositions", "id_map"}),
        "total": len(bundle["memories"]),
        "exported_by": bundle.get("exported_by"),
        "exported_at": bundle.get("exported_at"),
        "sample": preview_rows(bundle, plan, limit=50),
    }


@router.post("/api/v1/memory/import")
def memory_import(data: MemoryImportRequest):
    """Apply a previewed import.

    409 when the bundle or the store no longer matches the preview's
    fingerprints; the merge is otherwise exactly the previewed plan.
    """
    from app.storage.memory import get_memory_storage
    from app.utils.memory_import import apply_import, plan_import
    from app.utils.tool_audit_log import log_security_event
    bundle = _load_bundle_or_422(data.bundle)
    store = get_memory_storage()
    plan = plan_import(store, bundle, data.options)
    if (plan.bundle_fingerprint != data.bundle_fingerprint
            or plan.store_fingerprint != data.store_fingerprint):
        log_security_event("memory_import_refused", "memory_import", {
            "reason": "fingerprint_mismatch", "total": len(bundle["memories"])})
        raise HTTPException(
            status_code=409,
            detail="The bundle or the memory store changed since the preview. "
                   "Re-run the preview and check the result before importing.")
    try:
        result = apply_import(store, bundle, plan, data.options, source=data.source)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    log_security_event("memory_imported", "memory_import", {
        "imported": result["imported"],
        "counts": result["counts"],
        "exported_by": bundle.get("exported_by"),
        "bundle_fingerprint": plan.bundle_fingerprint,
        "on_collision": data.options.on_collision,
        "source": data.source,
    })
    return result
