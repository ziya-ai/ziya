"""
Memory API endpoints — CRUD for the structured memory system.

Provides REST endpoints for the frontend to manage memories:
browse, search, edit, delete, and handle proposals.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel, Field

from app.utils.logging_utils import get_mode_aware_logger
from app.models.memory import MindMapNode

logger = get_mode_aware_logger(__name__)
router = APIRouter(tags=["memory"])


# -- Request models ----------------------------------------------------------

class MemorySaveRequest(BaseModel):
    content: str
    layer: str = "domain_context"
    tags: List[str] = Field(default_factory=list)
    learned_from: str = "explicit_save"


class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    layer: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None


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
