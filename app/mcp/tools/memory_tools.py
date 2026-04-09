"""
Memory MCP tools — Phase 0 of the structured memory system.

Provides three tools the model can call:
  - memory_search: keyword search across the flat store
  - memory_save: persist a memory directly (user-approved)
  - memory_propose: suggest a memory for later user approval

The model sees these alongside other tools and uses them when
conversation context triggers associative recall or when the user
teaches it something worth retaining.
"""
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger


# ---------------------------------------------------------------------------
# Tool: memory_search
# ---------------------------------------------------------------------------

class MemorySearchInput(BaseModel):
    """Input schema for memory_search."""
    query: str = Field(..., description="Keyword or phrase to search for in memories.")
    tags: Optional[List[str]] = Field(None, description="Filter by tags (any match).")
    layer: Optional[str] = Field(
        None,
        description=(
            "Filter by layer: domain_context, architecture, lexicon, "
            "decision, active_thread, process, preference, negative_constraint"
        ),
    )
    limit: int = Field(10, description="Max results to return.")


class MemorySearchTool(BaseMCPTool):
    """Search the user's persistent memory store."""

    name: str = "memory_search"
    description: str = (
        "Search the user's persistent memory store for previously saved "
        "knowledge — domain facts, architecture decisions, vocabulary, "
        "active work threads, and lessons learned.  Use this when the "
        "conversation references topics that may have been discussed in "
        "prior sessions, or when the user asks 'do you remember...'."
    )
    InputSchema = MemorySearchInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        query = kwargs.get("query", "")
        tags = kwargs.get("tags")
        layer = kwargs.get("layer")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        limit = kwargs.get("limit", 10)

        if not query and not tags and not layer:
            return {"error": True, "message": "Provide a query, tags, or layer to search."}

        from app.storage.memory import get_memory_storage
        store = get_memory_storage()

        # Opportunistic decay: archive memories that have never been
        # useful.  A memory earns importance through retrieval (+0.05
        # per search hit, starting at 0.5).  If after 90 days it's
        # still at or below its initial importance AND hasn't been
        # accessed, it's noise — archive it so it stops polluting
        # search results.  Archived memories are preserved (not
        # deleted) and can be restored via the Memory Browser.
        try:
            _DECAY_THRESHOLD_DAYS = 90
            _DECAY_IMPORTANCE_CEILING = 0.5  # initial default importance
            today = time.strftime("%Y-%m-%d")
            for mem in store.list_memories(status="active"):
                try:
                    days_since = (time.mktime(time.strptime(today, "%Y-%m-%d"))
                                  - time.mktime(time.strptime(mem.last_accessed, "%Y-%m-%d"))) / 86400
                except (ValueError, OverflowError):
                    days_since = 0
                if days_since >= _DECAY_THRESHOLD_DAYS and mem.importance <= _DECAY_IMPORTANCE_CEILING:
                    mem.status = "archived"
                    store.save(mem)
                    logger.info(f"🗑️ Archived stale memory {mem.id} (importance={mem.importance:.2f}, "
                                f"last_accessed={mem.last_accessed}): {mem.content[:60]}")
        except Exception as e:
            logger.debug(f"Memory decay check failed (non-fatal): {e}")

        # Search active memories
        if query:
            results = store.search(query, limit=limit)
        else:
            results = store.list_memories(layer=layer, tags=tags)[:limit]

        if not results:
            # Before giving up, check if any proposals match the query.
            # A search hit on a proposal is strong evidence the knowledge
            # is needed — auto-promote it to the active store.
            promoted = []
            if query:
                proposals = store.list_proposals()
                q_lower = query.lower()
                for p in proposals:
                    content_match = q_lower in p.content.lower()
                    tag_match = any(q_lower in t.lower() for t in (p.tags or []))
                    if content_match or tag_match:
                        mem = store.approve_proposal(p.id)
                        if mem:
                            promoted.append(mem)
                            logger.info(f"🧠 Auto-promoted proposal {p.id} on search hit: {p.content[:60]}")
                if promoted:
                    # Return the promoted memories as results
                    formatted = []
                    for mem in promoted:
                        entry = f"[{mem.id}] ({mem.layer}) {mem.content}"
                        if mem.tags:
                            entry += f"  tags: {', '.join(mem.tags)}"
                        formatted.append(entry)
                    return {
                        "content": "\n\n".join(formatted),
                        "count": len(promoted),
                        "auto_promoted": len(promoted),
                    }

            # Out-of-domain detection with escalation hint
            has_mindmap = len(store.list_mindmap_nodes()) > 0
            return {
                "content": (
                    "No memories found matching your search. "
                    "This topic is outside the stored knowledge — " +
                    (
                        "try `memory_context` to browse the mind-map tree for related topics. "
                        if has_mindmap else ""
                    ) +
                    "Consider using `memory_propose` if the user shares relevant facts."
                ),
                "count": 0,
                "out_of_domain": True,
            }

        # Touch last_accessed on retrieved memories
        today = time.strftime("%Y-%m-%d")
        for mem in results:
            mem.last_accessed = today
            # Maturity boost: repeated retrieval increases importance (caps at 1.0)
            mem.importance = min(1.0, mem.importance + 0.05)
            store.save(mem)

        formatted = []
        for mem in results:
            entry = f"[{mem.id}] ({mem.layer}) {mem.content}"
            if mem.tags:
                entry += f"  tags: {', '.join(mem.tags)}"
            formatted.append(entry)

        return {
            "content": "\n\n".join(formatted),
            "count": len(results),
        }


# ---------------------------------------------------------------------------
# Tool: memory_save
# ---------------------------------------------------------------------------

class MemorySaveInput(BaseModel):
    """Input schema for memory_save."""
    content: str = Field(..., description="The memory to save — a distilled fact, principle, or decision.")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization and search.")
    layer: str = Field(
        "domain_context",
        description=(
            "Memory layer: domain_context, architecture, lexicon, "
            "decision, active_thread, process, preference, negative_constraint"
        ),
    )


class MemorySaveTool(BaseMCPTool):
    """Save a memory directly to the user's persistent store."""

    name: str = "memory_save"
    description: str = (
        "Save a memory to the user's persistent knowledge store.  "
        "Use this when the user explicitly asks you to remember "
        "something (e.g. '/remember ...', 'save this for next time').  "
        "The memory should be a distilled principle or fact, not raw "
        "conversation transcript.  For proposing memories without "
        "immediate save, use memory_propose instead."
    )
    InputSchema = MemorySaveInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        content = kwargs.get("content", "").strip()
        if not content:
            return {"error": True, "message": "Memory content is required."}

        tags = kwargs.get("tags", [])
        layer = kwargs.get("layer", "domain_context")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        # Capture project context from the request environment
        import os
        project_path = kwargs.pop("_workspace_path", None) or os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
        project_label = project_path.rstrip("/").split("/")[-1] if project_path else None

        from app.storage.memory import get_memory_storage
        from app.models.memory import Memory

        store = get_memory_storage()
        memory = Memory(
            content=content,
            tags=tags,
            layer=layer,
            learned_from="explicit_save",
        )
        if project_path:
            memory.scope.project_paths = [project_path]
        saved = store.save(memory)

        # Phase 2: Auto-place in mind-map + cell division + cross-links
        try:
            from app.utils.memory_maintenance import run_post_save_maintenance
            run_post_save_maintenance(saved.id)
        except Exception as e:
            logger.warning(f"Post-save maintenance failed (non-fatal): {e}")

        return {
            "success": True,
            "message": f"Memory saved: [{saved.id}] ({saved.layer}) {saved.content[:80]}",
            "memory_id": saved.id,
        }


# ---------------------------------------------------------------------------
# Tool: memory_propose
# ---------------------------------------------------------------------------

class MemoryProposeInput(BaseModel):
    """Input schema for memory_propose."""
    content: str = Field(..., description="Proposed memory content.")
    tags: List[str] = Field(default_factory=list, description="Suggested tags.")
    layer: str = Field("domain_context", description="Suggested layer.")


class MemoryProposeTool(BaseMCPTool):
    """Propose a memory for later user approval."""

    name: str = "memory_propose"
    description: str = (
        "Propose a memory for the user to review and approve later.  "
        "Use this at natural conversation pauses when you notice the "
        "user has shared knowledge that would be valuable to retain "
        "across sessions — domain facts, architecture decisions, "
        "vocabulary definitions, or lessons learned.  Proposals are "
        "batched and shown to the user at their convenience."
    )
    InputSchema = MemoryProposeInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        content = kwargs.get("content", "").strip()
        if not content:
            return {"error": True, "message": "Proposal content is required."}

        tags = kwargs.get("tags", [])
        layer = kwargs.get("layer", "domain_context")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        # Capture project context
        import os
        project_path = kwargs.pop("_workspace_path", None) or os.environ.get("ZIYA_USER_CODEBASE_DIR", "")

        from app.storage.memory import get_memory_storage
        from app.models.memory import MemoryProposal

        store = get_memory_storage()
        proposal = MemoryProposal(
            content=content,
            tags=tags,
            layer=layer,
            learned_from="observation",
        )
        if project_path:
            proposal.scope = {"project_paths": [project_path]}
        store.add_proposal(proposal)
        pending_count = len(store.list_proposals())
        return {
            "success": True,
            "message": f"Memory proposed for review ({pending_count} pending).",
            "proposal_id": proposal.id,
        }


# ---------------------------------------------------------------------------
# Tool: memory_context  (Phase 1 — mind-map traversal)
# ---------------------------------------------------------------------------

class MemoryContextInput(BaseModel):
    """Input schema for memory_context."""
    node_id: Optional[str] = Field(
        None,
        description=(
            "Mind-map node ID to inspect. Omit to get the root-level "
            "overview (Level 0 domains)."
        ),
    )


class MemoryContextTool(BaseMCPTool):
    """Browse the mind-map tree — handles and children for a node."""

    name: str = "memory_context"
    description: str = (
        "Browse the mind-map tree of the user's persistent memory.  "
        "Returns the handle (compact summary) of a node plus its "
        "children's handles.  Omit node_id to see the root-level "
        "domain overview.  Use this to self-route into the relevant "
        "branch before calling memory_expand for full detail."
    )
    InputSchema = MemoryContextInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        node_id = kwargs.get("node_id")

        from app.storage.memory import get_memory_storage
        store = get_memory_storage()

        if not node_id:
            # Return Level 0: all root nodes
            roots = store.get_root_nodes()
            if not roots:
                return {"content": "No mind-map nodes exist yet. Use memory_search to find memories directly.", "nodes": []}
            formatted = []
            for r in roots:
                mem_count = len(r.memory_refs)
                child_count = len(r.children)
                formatted.append(f"[{r.id}] {r.handle}  ({mem_count} memories, {child_count} sub-topics)")
            return {"content": "Memory domains:\n\n" + "\n".join(formatted), "nodes": [r.model_dump() for r in roots]}

        ctx = store.get_node_with_context(node_id)
        if not ctx:
            return {"error": True, "message": f"Node '{node_id}' not found."}

        node = ctx["node"]
        children = ctx["children"]

        lines = [f"**{node['handle']}**  (tags: {', '.join(node.get('tags', []))})"]
        if children:
            lines.append("\nSub-topics:")
            for c in children:
                lines.append(f"  [{c['id']}] {c['handle']}  ({c['memory_count']} memories)")
        else:
            lines.append("\n(No sub-topics)")

        return {"content": "\n".join(lines), "node": node, "children": children}


# ---------------------------------------------------------------------------
# Tool: memory_expand  (Phase 1 — load depth)
# ---------------------------------------------------------------------------

class MemoryExpandInput(BaseModel):
    """Input schema for memory_expand."""
    node_id: str = Field(
        ...,
        description="Mind-map node ID to expand. Returns all memories under this node and its descendants.",
    )


class MemoryExpandTool(BaseMCPTool):
    """Load all memories under a mind-map node and its descendants."""

    name: str = "memory_expand"
    description: str = (
        "Load all memories attached to a mind-map node and its "
        "descendants.  Call this after using memory_context to "
        "identify the relevant branch.  Returns the full memory "
        "content for deep context on a specific topic."
    )
    InputSchema = MemoryExpandInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        node_id = kwargs.get("node_id", "")
        if not node_id:
            return {"error": True, "message": "node_id is required."}

        from app.storage.memory import get_memory_storage
        store = get_memory_storage()

        memories = store.expand_node(node_id)
        if not memories:
            node = store.get_mindmap_node(node_id)
            if not node:
                return {"error": True, "message": f"Node '{node_id}' not found."}
            return {"content": f"No memories stored under '{node.handle}'.", "count": 0}

        formatted = []
        for mem in memories:
            entry = f"[{mem.id}] ({mem.layer}) {mem.content}"
            if mem.tags:
                entry += f"  tags: {', '.join(mem.tags)}"
            formatted.append(entry)

        return {"content": "\n\n".join(formatted), "count": len(memories)}