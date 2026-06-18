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


# Throttle for the opportunistic-decay sweep.  The sweep is O(N) over all
# active memories and runs inside memory_search, which is on the hot path;
# without a throttle, a burst of searches re-pays the full scan each time.
_LAST_DECAY_SWEEP: float = 0.0


class _SkipDecay(Exception):
    """Internal control-flow signal to skip the throttled decay sweep."""


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
        _conversation_id = kwargs.pop("conversation_id", None)
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
            # Throttle: the decay scan is O(N) over all active memories and
            # runs on the search hot path.  Cap it to once per 10 minutes
            # per process so high-frequency searches don't repeatedly pay it.
            global _LAST_DECAY_SWEEP
            _now = time.time()
            if _now - _LAST_DECAY_SWEEP < 600:
                raise _SkipDecay()
            _LAST_DECAY_SWEEP = _now
            today = time.strftime("%Y-%m-%d")
            _to_archive = []
            for mem in store.list_memories(status="active"):
                try:
                    days_since = (time.mktime(time.strptime(today, "%Y-%m-%d"))
                                  - time.mktime(time.strptime(mem.last_accessed, "%Y-%m-%d"))) / 86400
                except (ValueError, OverflowError):
                    days_since = 0
                if days_since >= _DECAY_THRESHOLD_DAYS and mem.importance <= _DECAY_IMPORTANCE_CEILING:
                    mem.status = "archived"
                    _to_archive.append(mem)
                    logger.info(f"🗑️ Archived stale memory {mem.id} (importance={mem.importance:.2f}, "
                                f"last_accessed={mem.last_accessed}): {mem.content[:60]}")
            if _to_archive:
                store.save_many(_to_archive)
        except _SkipDecay:
            pass
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
                        if mem.status == "contested":
                            entry = f"[{mem.id}] ({mem.layer}) [contested] {mem.content}"
                        elif mem.tags:
                            entry += f"  tags: {', '.join(mem.tags)}"
                        formatted.append(entry)
                    try:
                        from app.memory.feedback import record_load
                        record_load(_conversation_id, [m.id for m in promoted])
                    except Exception as fb_err:
                        logger.debug(f"record_load (auto-promoted) failed: {fb_err}")
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
        # Single batched write instead of one full-file rewrite per result.
        store.save_many(results)

        # Record retrieval-load for the feedback loop.  Use signal happens
        # later when the assistant's response gets scored against these.
        try:
            from app.memory.feedback import record_load
            record_load(_conversation_id, [m.id for m in results])
        except Exception as fb_err:
            logger.debug(f"record_load (search) failed: {fb_err}")

        formatted = []
        for mem in results:
            # Contested memories: surface the tag so the model can reason
            # about them rather than treating them as ground truth.
            if getattr(mem, "status", "active") == "contested":
                entry = f"[{mem.id}] ({mem.layer}) [contested] {mem.content}"
            else:
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
            from app.memory.maintenance import run_post_save_maintenance
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
        _conversation_id = kwargs.pop("conversation_id", None)
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        # Capture project context
        import os
        project_path = kwargs.pop("_workspace_path", None) or os.environ.get("ZIYA_USER_CODEBASE_DIR", "")

        from app.storage.memory import get_memory_storage
        from app.models.memory import MemoryProposal, MemoryScope

        store = get_memory_storage()
        proposal = MemoryProposal(
            content=content,
            tags=tags,
            layer=layer,
            learned_from="observation",
        )
        # Stamp the originating conversation so memory_retract_proposal can
        # verify own-session ownership before allowing a dismiss.  The agent
        # may retract a proposal it created THIS session; it may never
        # retract a prior-session proposal (conversation_id mismatch) nor a
        # legacy one (conversation_id None) — those await the user's review.
        if _conversation_id:
            proposal.conversation_id = _conversation_id
        if project_path:
            proposal.scope = MemoryScope(project_paths=[project_path])
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
        _conversation_id = kwargs.pop("conversation_id", None)

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

        # Record loads for any memories directly attached to this node.
        # Children's memories are loaded only when the model calls
        # memory_expand, so they're not counted here.
        memory_refs = node.get("memory_refs") or []
        if memory_refs:
            try:
                from app.memory.feedback import record_load
                record_load(_conversation_id, list(memory_refs))
            except Exception as fb_err:
                logger.debug(f"record_load (context) failed: {fb_err}")

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
        _conversation_id = kwargs.pop("conversation_id", None)
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

        # Record retrieval-load for the feedback loop.
        try:
            from app.memory.feedback import record_load
            record_load(_conversation_id, [m.id for m in memories])
        except Exception as fb_err:
            logger.debug(f"record_load (expand) failed: {fb_err}")

        return {"content": "\n\n".join(formatted), "count": len(memories)}


# ---------------------------------------------------------------------------
# Tool: memory_retract_proposal
# ---------------------------------------------------------------------------

class MemoryRetractProposalInput(BaseModel):
    """Input schema for memory_retract_proposal."""
    proposal_id: str = Field(
        ...,
        description="The prop_* id of a proposal THIS conversation created "
                    "(returned by memory_propose). Only own-session proposals "
                    "may be retracted.",
    )


class MemoryRetractProposalTool(BaseMCPTool):
    """Retract a memory proposal the agent created this session.

    Deliberately narrow.  The proposal queue is the human-ownership gate
    for durable memory: the agent proposes, the user reviews and approves.
    This tool restores symmetry for ONE half of that contract only — the
    agent may withdraw its OWN premature suggestion (e.g. a design decision
    about in-flight work that, per the work-primitives taxonomy, should
    never have been proposed as durable knowledge).  It must NOT be able to:
      - approve any proposal (that is the user's gate, always),
      - dismiss a proposal from a prior session or another conversation
        (those are pending the user's review and are not the agent's to
        clear).
    Ownership is proven by matching the proposal's stamped conversation_id
    against the current conversation.
    """

    name: str = "memory_retract_proposal"
    description: str = (
        "Retract (dismiss) a memory proposal that YOU created earlier in "
        "THIS conversation — for example, a proposal you now realize was "
        "premature, redundant, or was in-flight work state rather than "
        "durable knowledge. Pass the prop_* id returned by memory_propose. "
        "You may only retract proposals from the current session; proposals "
        "from prior sessions are awaiting the user's review and are not "
        "yours to dismiss. This never approves a proposal — approval is "
        "always the user's decision."
    )
    InputSchema = MemoryRetractProposalInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        proposal_id = (kwargs.get("proposal_id") or "").strip()
        _conversation_id = kwargs.pop("conversation_id", None)
        if not proposal_id:
            return {"error": True, "message": "proposal_id is required."}

        from app.storage.memory import get_memory_storage
        store = get_memory_storage()

        # Locate the proposal in the review queue.
        target = None
        for p in store.list_proposals():
            if p.id == proposal_id:
                target = p
                break
        if target is None:
            return {
                "error": True,
                "message": f"No pending proposal with id '{proposal_id}'. "
                           f"It may have already been approved, dismissed, "
                           f"or never existed.",
            }

        # Ownership gate: only the conversation that created the proposal
        # may retract it.  Fails closed — a missing stamp (legacy proposal,
        # or one created before this field was set) is NOT retractable.
        owner = getattr(target, "conversation_id", None)
        if not _conversation_id or owner != _conversation_id:
            logger.info(
                f"🔒 memory_retract_proposal denied for {proposal_id}: "
                f"owner={owner!r} current={_conversation_id!r} (not own-session)"
            )
            return {
                "error": True,
                "message": (
                    f"Proposal '{proposal_id}' was not created in this "
                    f"conversation, so it can't be retracted here — it is "
                    f"awaiting the user's review. Only proposals you created "
                    f"this session may be retracted."
                ),
            }

        if store.dismiss_proposal(proposal_id):
            logger.info(f"↩️ Retracted own-session proposal {proposal_id}: {target.content[:60]}")
            return {
                "success": True,
                "message": f"Retracted proposal {proposal_id}.",
                "proposal_id": proposal_id,
            }
        return {
            "error": True,
            "message": f"Proposal '{proposal_id}' could not be retracted "
                       f"(already removed?).",
        }