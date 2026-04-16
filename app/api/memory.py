"""
Memory API endpoints — CRUD for the structured memory system.

Provides REST endpoints for the frontend to manage memories:
browse, search, edit, delete, and handle proposals.
"""
import asyncio
import time

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel, Field

from app.utils.logging_utils import get_mode_aware_logger
from app.models.memory import MindMapNode

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
async def get_memory_status():
    """Overview: counts by layer and status, pending proposal count."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    counts = store.count()
    pending = len(store.list_proposals())
    return {**counts, "pending_proposals": pending}


@router.get("/api/v1/memory/search")
async def search_memories(
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
async def list_all_memories():
    """Return every active memory (for the memory browser UI)."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    return [m.model_dump() for m in store.list_memories()]


@router.post("/api/v1/memory")
async def save_memory(data: MemorySaveRequest):
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
async def update_memory(memory_id: str, data: MemoryUpdateRequest):
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
async def delete_memory(memory_id: str):
    """Delete a memory."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    if not store.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


# -- Proposals ---------------------------------------------------------------

@router.get("/api/v1/memory/proposals")
async def list_proposals():
    """List pending memory proposals."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    return [p.model_dump() for p in store.list_proposals()]


@router.post("/api/v1/memory/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str):
    """Approve a pending proposal, moving it to the flat store."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    memory = store.approve_proposal(proposal_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return memory.model_dump()


@router.post("/api/v1/memory/proposals/approve-all")
async def approve_all_proposals():
    """Approve all pending proposals at once."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    proposals = store.list_proposals()
    approved = []
    for p in proposals:
        mem = store.approve_proposal(p.id)
        if mem:
            approved.append(mem.model_dump())
    return {"approved": len(approved), "memories": approved}


@router.delete("/api/v1/memory/proposals/{proposal_id}")
async def dismiss_proposal(proposal_id: str):
    """Dismiss a pending proposal."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    if not store.dismiss_proposal(proposal_id):
        raise HTTPException(status_code=404, detail="Proposal not found")
    return {"dismissed": True}


# -- Review (Phase 2) -------------------------------------------------------

@router.get("/api/v1/memory/review")
async def get_review():
    """Surface stale memories, oversized nodes, and orphan memories for cleanup."""
    from app.storage.memory import get_memory_storage
    from app.utils.memory_maintenance import get_review_summary
    store = get_memory_storage()
    return get_review_summary(store)


@router.post("/api/v1/memory/maintenance")
async def run_maintenance():
    """Trigger a full maintenance pass: cell division + cross-links for all nodes."""
    from app.storage.memory import get_memory_storage
    from app.utils.memory_maintenance import maybe_divide_node, discover_cross_links
    store = get_memory_storage()
    results = {"divided": [], "cross_linked": []}
    for node in store.list_mindmap_nodes():
        divided = maybe_divide_node(store, node.id)
        results["divided"].extend(divided)
        links = discover_cross_links(store, node.id)
        results["cross_linked"].extend(links)
    return results


@router.post("/api/v1/memory/organize")
async def organize_memories():
    """Trigger full LLM-powered reorganization: cluster, place, relate, cross-link.

    Runs as a background task — returns immediately with status 'started'.
    Poll GET /api/v1/memory/organize/status for progress.
    """
    from app.utils.memory_organizer import reorganize

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
async def organize_status():
    """Poll for organize task completion."""
    return _organize_task_status


@router.post("/api/v1/memory/embeddings/backfill")
async def backfill_embeddings():
    """Trigger embedding backfill for all memories missing vectors."""
    from app.storage.memory import get_memory_storage
    from app.services.embedding_service import get_embedding_cache, backfill_embeddings as do_backfill, get_embedding_provider, NoopProvider
    provider = get_embedding_provider()
    if isinstance(provider, NoopProvider):
        return {"status": "disabled", "message": "Embedding provider not configured"}
    store = get_memory_storage()
    memories = store.list_memories(status="active")
    cache = get_embedding_cache()
    all_ids = [m.id for m in memories]
    missing = cache.missing_ids(all_ids)
    if not missing:
        return {"status": "complete", "total": len(memories), "missing": 0}
    to_embed = [(m.id, m.content) for m in memories if m.id in set(missing)]
    count = await do_backfill(to_embed)
    return {"status": "complete", "embedded": count, "total": len(memories)}


@router.get("/api/v1/memory/embeddings/status")
async def embedding_status():
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
    return {"enabled": True, "provider": "bedrock_titan", "total": total,
            "embedded": cached, "missing": total - cached}


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
async def get_mindmap():
    """Return the full mind-map tree."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    nodes = store.list_mindmap_nodes()
    return [n.model_dump() for n in nodes]


@router.get("/api/v1/memory/mindmap/{node_id}")
async def get_mindmap_node(node_id: str):
    """Return a single mind-map node with children context."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    ctx = store.get_node_with_context(node_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Node not found")
    return ctx


@router.post("/api/v1/memory/mindmap")
async def create_mindmap_node(data: MindMapNodeRequest):
    """Create or update a mind-map node."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    node = MindMapNode(**data.model_dump())
    saved = store.save_mindmap_node(node)
    return saved.model_dump()


@router.delete("/api/v1/memory/mindmap/{node_id}")
async def delete_mindmap_node(node_id: str):
    """Delete a mind-map node (children are reparented)."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()
    if not store.delete_mindmap_node(node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    return {"deleted": True}


@router.post("/api/v1/memory/mindmap/{node_id}/expand")
async def expand_mindmap_node(node_id: str):
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
