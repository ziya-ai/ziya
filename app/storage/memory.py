"""
Memory storage — flat JSON file store for the structured memory system.

Phase 0: Single file (~/.ziya/memory/memories.json) with full CRUD.
Profile and project hints stored alongside.  ALE encryption-aware
via the same BaseStorage pattern used by chats and skills.
"""
import math
import json
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from app.utils.logging_utils import logger
from app.models.memory import (
    Memory, MemoryProposal, MemoryProfile, ProjectHints, MindMapNode
)


# -- Module-level helpers (used by MemoryStorage.search) ---------------------

# Word tokenizer: split on non-alphanumeric, drop short/stop words
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "again",
    "further", "then", "once", "that", "this", "these", "those",
    "and", "but", "or", "nor", "not", "so", "very", "just",
    "about", "also", "than", "other", "some", "such", "only",
})


def _tokenize(text: str) -> List[str]:
    """Split text into searchable tokens, dropping stop words and short fragments."""
    words = re.findall(r'[a-z0-9_]+', text.lower())
    return [w for w in words if len(w) > 2 and w not in _STOP_WORDS]


import functools as _functools_144
import hashlib as _hashlib_144
import os as _os_144
import tempfile as _tempfile_144
from contextlib import contextmanager as _contextmanager_144

try:
    from filelock import FileLock as _FileLock_144
except ImportError:  # pragma: no cover
    _FileLock_144 = None

_MEMORY_LOCK_DIR_144 = _os_144.path.join(_tempfile_144.gettempdir(), "ziya_memory_locks")
_MEMORY_LOCK_TIMEOUT_144 = 10


def _memory_file_lock_144(data_file):
    """PenPal #144 [CWE-667]: reentrant, cross-process per-file lock guarding a
    MemoryStorage read-modify-write. Without it, two concurrent writers under
    ``uvicorn --workers N`` (or an ``await``-yielding background ``reorganize``
    task interleaving with request handlers) each read the same _memories.json
    snapshot and the last write silently discards the others. Keyed on the data
    file's realpath (hashed into the temp dir — no project-tree clutter);
    is_singleton=True gives thread-local reentrancy so any same-file RMW nested
    in another re-enters instead of self-deadlocking."""
    if _FileLock_144 is None:
        @_contextmanager_144
        def _noop():
            yield
        return _noop()
    try:
        _os_144.makedirs(_MEMORY_LOCK_DIR_144, exist_ok=True)
    except OSError:
        pass
    key = _hashlib_144.sha256(_os_144.path.realpath(str(data_file)).encode("utf-8")).hexdigest()[:16]
    return _FileLock_144(
        _os_144.path.join(_MEMORY_LOCK_DIR_144, key + ".lock"),
        timeout=_MEMORY_LOCK_TIMEOUT_144,
        is_singleton=True,
    )


def _rmw_locked(file_attr):
    """Decorator: hold the reentrant per-file lock for ``getattr(self, file_attr)``
    across the whole decorated read-modify-write method."""
    def deco(fn):
        @_functools_144.wraps(fn)
        def wrapper(self, *args, **kwargs):
            with _memory_file_lock_144(getattr(self, file_attr)):
                return fn(self, *args, **kwargs)
        return wrapper
    return deco


class MemoryStorage:
    """File-based memory store under ~/.ziya/memory/."""

    def __init__(self, memory_dir: Optional[Path] = None):
        if memory_dir is None:
            from app.utils.paths import get_ziya_home
            memory_dir = get_ziya_home() / "memory"
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # In-memory cache to avoid re-reading/decrypting on every search.
        # Invalidated on any write operation.
        self._memories_cache: Optional[List[dict]] = None
        self._memories_cache_mtime: float = 0

    # -- File paths ----------------------------------------------------------

    @property
    def _memories_file(self) -> Path:
        return self._dir / "memories.json"

    @property
    def _proposals_file(self) -> Path:
        return self._dir / "proposals.json"

    @property
    def _mindmap_file(self) -> Path:
        return self._dir / "mindmap.json"

    @property
    def _profile_file(self) -> Path:
        return self._dir / "profile.json"

    # -- Low-level I/O (ALE-aware) ------------------------------------------

    def _read_json(self, filepath: Path) -> Any:
        if not filepath.exists():
            return None
        try:
            raw = filepath.read_bytes()
            if not raw:
                return None
            from app.utils.encryption import is_encrypted, get_encryptor
            if is_encrypted(raw):
                plaintext = get_encryptor().decrypt(raw)
                return json.loads(plaintext)
            return json.loads(raw)
        except Exception as e:
            logger.error(f"Error reading {filepath}: {e}")
            return None

    def _write_json(self, filepath: Path, data: Any) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        temp = filepath.with_suffix(".tmp")
        try:
            from app.utils.encryption import get_encryptor
            enc = get_encryptor()
            if enc.is_enabled("session_data"):
                temp.write_bytes(enc.encrypt(plaintext, "session_data"))
            else:
                temp.write_bytes(plaintext)
            temp.rename(filepath)
        except Exception:
            if temp.exists():
                temp.unlink()
            raise

    # -- Flat store ----------------------------------------------------------

    def _load_memories(self) -> List[dict]:
        # Return cached data if the file hasn't changed
        try:
            mtime = self._memories_file.stat().st_mtime if self._memories_file.exists() else 0
        except OSError:
            mtime = 0
        if self._memories_cache is not None and mtime == self._memories_cache_mtime:
            return self._memories_cache

        data = self._read_json(self._memories_file)
        if isinstance(data, list):
            self._memories_cache = data
            self._memories_cache_mtime = mtime
            return data
        if isinstance(data, dict) and "memories" in data:
            self._memories_cache = data["memories"]
            self._memories_cache_mtime = mtime
            return data["memories"]
        self._memories_cache = []
        self._memories_cache_mtime = mtime
        return []

    def _save_memories(self, memories: List[dict]) -> None:
        self._write_json(self._memories_file, memories)
        # Invalidate cache so next read picks up the write
        self._memories_cache = None
        self._memories_cache_mtime = 0

    def list_memories(
        self,
        layer: Optional[str] = None,
        tags: Optional[List[str]] = None,
        status: str = "active",
    ) -> List[Memory]:
        raw = self._load_memories()
        results = []
        for m in raw:
            if status and m.get("status", "active") != status:
                continue
            if layer and m.get("layer") != layer:
                continue
            if tags and not set(tags).intersection(set(m.get("tags", []))):
                continue
            results.append(Memory(**m))
        return results

    def get(self, memory_id: str) -> Optional[Memory]:
        for m in self._load_memories():
            if m.get("id") == memory_id:
                return Memory(**m)
        return None

    @_rmw_locked('_memories_file')
    def save(self, memory: Memory) -> Memory:
        """Create or update a memory in the flat store."""
        # ASR NF-003: scan memory content for encoded instruction payloads
        # on write (covers the direct memory_save tool path, which does not
        # pass through the proposals store).  Detection only — flag + audit.
        try:
            from app.mcp.encoding_scanner import scan_and_log
            scan_and_log(memory.content, source="memory_save")
        except Exception:
            pass  # Detection must never break memory persistence
        memories = self._load_memories()
        existing_idx = next(
            (i for i, m in enumerate(memories) if m.get("id") == memory.id),
            None,
        )
        dump = memory.model_dump()
        if existing_idx is not None:
            memories[existing_idx] = dump
        else:
            memories.append(dump)
            # NF-010: provenance audit on memory CREATION (LPCI Phase 2 —
            # Storage).  Always-on: creation is rare (not per-turn) and is
            # core forensic provenance.  Respects ZIYA_DISABLE_AUDIT_LOG via
            # log_security_event.  Only the create branch fires — updates
            # (re-embedding, corroboration bumps) are not new provenance.
            try:
                from app.utils.tool_audit_log import log_security_event
                log_security_event(
                    "memory_created",
                    source_tool="memory_store",
                    details={
                        "memory_id": memory.id,
                        "layer": memory.layer,
                        "learned_from": memory.learned_from,
                        "learned_from_conversation": memory.learned_from_conversation or "",
                    },
                )
            except Exception:
                pass  # Provenance audit must never break memory persistence
        self._save_memories(memories)
        logger.info(f"💾 Memory saved: {memory.id} [{memory.layer}] {memory.content[:60]}")

        # Embed asynchronously — non-blocking, best-effort
        try:
            from app.services.embedding_service import embed_and_cache
            embed_and_cache(memory.id, memory.content)
        except Exception as e:
            logger.debug(f"Embedding on save failed (non-fatal): {e}")

        return memory

    @_rmw_locked('_memories_file')
    def save_many(self, memories: List[Memory]) -> None:
        """Persist multiple memories in a single file rewrite.

        ``save()`` rewrites the entire JSON store per call; calling it in
        a loop over N memories costs N full serialize+encrypt+write cycles.
        This batches all updates into one write.  Embedding refresh is
        still per-memory (best-effort, non-blocking).
        """
        if not memories:
            return
        store = self._load_memories()
        by_id = {m.get("id"): i for i, m in enumerate(store)}
        for memory in memories:
            dump = memory.model_dump()
            idx = by_id.get(memory.id)
            if idx is not None:
                store[idx] = dump
            else:
                by_id[memory.id] = len(store)
                store.append(dump)
        self._save_memories(store)
        logger.debug(f"💾 Batch-saved {len(memories)} memories in one write")

        # Refresh embeddings best-effort (non-fatal, per-memory).
        try:
            from app.services.embedding_service import embed_and_cache
            for memory in memories:
                embed_and_cache(memory.id, memory.content)
        except Exception as e:
            logger.debug(f"Batch embedding refresh failed (non-fatal): {e}")

    @_rmw_locked('_memories_file')
    def delete(self, memory_id: str) -> bool:
        memories = self._load_memories()
        before = len(memories)
        memories = [m for m in memories if m.get("id") != memory_id]
        if len(memories) == before:
            return False
        self._save_memories(memories)
        logger.info(f"🗑️ Memory deleted: {memory_id}")

        # Keep mindmap.json consistent: a node advertising a ref to a
        # deleted memory inflates the system-prompt counts and makes
        # memory_expand return nothing for a domain that claims content.
        try:
            self._detach_from_mindmap(memory_id)
        except Exception as e:
            logger.warning(f"Mind-map detach failed for {memory_id}: {e}")

        # Remove embedding from cache
        try:
            from app.services.embedding_service import remove_embedding
            remove_embedding(memory_id)
        except Exception as e:
            logger.debug(f"Embedding removal failed (non-fatal): {e}")

        return True

    def search(self, query: str, limit: int = 20) -> List[Memory]:
        """Hybrid semantic + keyword search.

        Combines embedding-based semantic similarity with keyword IDF
        scoring via Reciprocal Rank Fusion (RRF).  Falls back to
        keyword-only when embeddings are unavailable.

        Includes both "active" and "contested" memories.  Contested
        entries are excluded from system-prompt injection (which uses
        list_memories(status="active") directly), but the model can
        still find them via memory_search and reason about them.
        """
        raw = self._load_memories()
        active = [m for m in raw if m.get("status") in (None, "active", "contested")]
        if not active:
            return []

        # -- Semantic search path --
        semantic_ranked: List[Tuple[str, float]] = []
        try:
            from app.services.embedding_service import semantic_search, get_embedding_cache
            cache = get_embedding_cache()
            # Auto-backfill: if cache is mostly empty, trigger sync backfill
            active_ids = [m.get("id") for m in active if m.get("id")]
            missing = cache.missing_ids(active_ids)
            if missing and len(missing) > len(active_ids) * 0.5:
                # Don't block the search path with synchronous embedding.
                # The backfill endpoint and post-save hook handle population;
                # here we just skip semantic search gracefully when the cache
                # is cold rather than blocking for potentially minutes.
                logger.info(
                    f"Embedding cache cold ({len(missing)}/{len(active_ids)} missing) "
                    f"— skipping semantic search, using keyword fallback"
                )
                semantic_ranked = []  # fall through to keyword path
            else:
                # The cache also holds vectors for proposals and for
                # memories that have since been deleted or archived.
                # Restrict ranking to the ids this search can return so
                # top_k is not consumed by rows that get filtered below.
                semantic_ranked = semantic_search(
                    query, top_k=limit * 2, include_ids=set(active_ids))
        except Exception as e:
            logger.debug(f"Semantic search unavailable: {e}")

        # -- Keyword search path (existing logic) --
        q_lower = query.lower()
        q_tokens = _tokenize(q_lower)
        if not q_tokens and not semantic_ranked:
            return []

        keyword_ranked: List[Tuple[str, float]] = []
        if q_tokens:
            doc_freq: Dict[str, int] = {}
            for m in active:
                words = set(_tokenize(m.get("content", "").lower()))
                words.update(t.lower() for t in m.get("tags", []))
                for w in words:
                    doc_freq[w] = doc_freq.get(w, 0) + 1
            n_docs = len(active)

            today = time.strftime("%Y-%m-%d")
            keyword_scored: List[tuple] = []

            for m in active:
                content_lower = m.get("content", "").lower()
                content_tokens = set(_tokenize(content_lower))
                mem_tags = {t.lower() for t in m.get("tags", [])}

                word_score = sum(
                    math.log(n_docs / doc_freq.get(qt, 1)) + 1.0
                    for qt in q_tokens if qt in content_tokens
                )
                # iter-5 [H13 retrievability]: a snake_case identifier such as
                # ``run_post_conversation_extraction`` survives _tokenize as ONE
                # opaque token (underscores are the only separator _tokenize
                # keeps), so a natural-language query ("post conversation
                # extraction") token-matches none of them and the memory that
                # actually NAMES the queried entity scores 0 on word_score and
                # never surfaces top-3.  Expand each underscore-compound content
                # token into its parts and award PARTIAL (half-IDF) credit when a
                # query token matches only via a part.  Purely ADDITIVE: it can
                # only RAISE the score of a memory that names the queried entity
                # via a compound identifier — it never removes an existing full
                # match nor touches memories whose content has no compound token,
                # so ranking of non-compound content is unchanged.
                content_subtokens = {
                    part
                    for tok in content_tokens if "_" in tok
                    for part in tok.split("_")
                    if len(part) > 2 and part not in content_tokens
                }
                subtoken_score = sum(
                    0.5 * (math.log(n_docs / doc_freq.get(qt, 1)) + 1.0)
                    for qt in q_tokens
                    if qt not in content_tokens and qt in content_subtokens
                ) if content_subtokens else 0.0
                tag_score = sum(
                    3.0 for qt in q_tokens
                    if any(qt == tag or qt in tag for tag in mem_tags)
                )
                phrase_score = 5.0 if q_lower in content_lower else 0.0
                layer_score = 1.0 if q_lower in m.get("layer", "") else 0.0

                raw_score = (word_score + subtoken_score + tag_score
                             + phrase_score + layer_score)
                if raw_score <= 0:
                    continue

                importance = m.get("importance", 0.5)
                last_accessed = m.get("last_accessed", today)
                try:
                    days_since = (time.mktime(time.strptime(today, "%Y-%m-%d"))
                                  - time.mktime(time.strptime(last_accessed, "%Y-%m-%d"))) / 86400
                except (ValueError, OverflowError):
                    days_since = 0
                recency = math.exp(-0.01 * max(days_since, 0))
                final_score = raw_score * (0.5 + importance) * (0.3 + 0.7 * recency)
                keyword_scored.append((m.get("id"), final_score))

            keyword_scored.sort(key=lambda x: x[1], reverse=True)
            keyword_ranked = keyword_scored[:limit * 2]

        # -- Reciprocal Rank Fusion --
        if semantic_ranked and keyword_ranked:
            RRF_K = 60
            rrf_scores: Dict[str, float] = {}
            for rank, (mid, _) in enumerate(semantic_ranked):
                rrf_scores[mid] = rrf_scores.get(mid, 0) + 1.0 / (RRF_K + rank + 1)
            for rank, (mid, _) in enumerate(keyword_ranked):
                rrf_scores[mid] = rrf_scores.get(mid, 0) + 1.0 / (RRF_K + rank + 1)
            fused = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
            result_ids = [mid for mid, _ in fused[:limit]]
        elif semantic_ranked:
            result_ids = [mid for mid, _ in semantic_ranked[:limit]]
        else:
            result_ids = [mid for mid, _ in keyword_ranked[:limit]]

        # Resolve IDs to Memory objects
        id_to_raw = {m.get("id"): m for m in active}
        return [Memory(**id_to_raw[mid]) for mid in result_ids if mid in id_to_raw]


    def count(self) -> Dict[str, int]:
        """Return counts by layer and status."""
        raw = self._load_memories()
        by_layer: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        for m in raw:
            layer = m.get("layer", "unknown")
            status = m.get("status", "active")
            by_layer[layer] = by_layer.get(layer, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
        return {"total": len(raw), "by_layer": by_layer, "by_status": by_status}

    # -- Proposals -----------------------------------------------------------

    def list_proposals(self) -> List[MemoryProposal]:
        data = self._read_json(self._proposals_file)
        if not isinstance(data, list):
            return []
        return [MemoryProposal(**p) for p in data]

    @_rmw_locked('_proposals_file')
    def add_proposal(self, proposal: MemoryProposal) -> MemoryProposal:
        proposals = self._read_json(self._proposals_file) or []
        proposals.append(proposal.model_dump())
        self._write_json(self._proposals_file, proposals)
        return proposal

    @_rmw_locked('_proposals_file')
    def approve_proposal(self, proposal_id: str) -> Optional[Memory]:
        """Move a proposal to the flat store as an active memory."""
        proposals = self._read_json(self._proposals_file) or []
        target = None
        remaining = []
        for p in proposals:
            if p.get("id") == proposal_id:
                target = p
            else:
                remaining.append(p)
        if not target:
            return None
        self._write_json(self._proposals_file, remaining)
        memory = Memory(
            content=target["content"],
            layer=target.get("layer", "domain_context"),
            tags=target.get("tags", []),
            learned_from=target.get("learned_from", "observation"),
        )
        return self.save(memory)

    @_rmw_locked('_proposals_file')
    def dismiss_proposal(self, proposal_id: str) -> bool:
        proposals = self._read_json(self._proposals_file) or []
        before = len(proposals)
        proposals = [p for p in proposals if p.get("id") != proposal_id]
        if len(proposals) == before:
            return False
        self._write_json(self._proposals_file, proposals)
        return True

    # -- Mind-Map ------------------------------------------------------------

    def _load_mindmap(self) -> Dict[str, dict]:
        """Load mind-map as a dict keyed by node ID."""
        data = self._read_json(self._mindmap_file)
        if isinstance(data, dict) and "nodes" in data:
            return {n["id"]: n for n in data["nodes"] if "id" in n}
        if isinstance(data, list):
            return {n["id"]: n for n in data if "id" in n}
        return {}

    def _save_mindmap(self, nodes: Dict[str, dict]) -> None:
        self._write_json(self._mindmap_file, {"nodes": list(nodes.values())})

    def get_mindmap_node(self, node_id: str) -> Optional[MindMapNode]:
        nodes = self._load_mindmap()
        raw = nodes.get(node_id)
        return MindMapNode(**raw) if raw else None

    def list_mindmap_nodes(self) -> List[MindMapNode]:
        return [MindMapNode(**n) for n in self._load_mindmap().values()]

    def get_root_nodes(self) -> List[MindMapNode]:
        """Return nodes with no parent (Level 0)."""
        return [n for n in self.list_mindmap_nodes() if n.parent is None]

    def get_children(self, node_id: str) -> List[MindMapNode]:
        """Return direct children of a node."""
        all_nodes = self.list_mindmap_nodes()
        return [n for n in all_nodes if n.parent == node_id]

    def get_node_with_context(self, node_id: str) -> Dict[str, Any]:
        """Return a node's handle plus its children's handles (one level)."""
        node = self.get_mindmap_node(node_id)
        if not node:
            return {}
        children = self.get_children(node_id)
        # Touch access stats
        node.access_count += 1
        node.last_accessed = time.strftime("%Y-%m-%d")
        nodes = self._load_mindmap()
        nodes[node_id] = node.model_dump()
        self._save_mindmap(nodes)
        return {
            "node": node.model_dump(),
            "children": [{"id": c.id, "handle": c.handle, "tags": c.tags,
                          "memory_count": len(c.memory_refs)} for c in children],
        }

    def expand_node(self, node_id: str, max_depth: int = 5) -> List[Memory]:
        """Return all memories under this node and its descendants."""
        visited: set = set()
        ref_ids: list = []

        def _collect(nid: str, depth: int):
            if nid in visited or depth > max_depth:
                return
            visited.add(nid)
            node = self.get_mindmap_node(nid)
            if not node:
                return
            ref_ids.extend(node.memory_refs)
            for child_id in node.children:
                _collect(child_id, depth + 1)

        _collect(node_id, 0)

        # Resolve memory refs to actual memories
        unique_ids = list(dict.fromkeys(ref_ids))  # Preserve order, dedup
        memories = []
        for mid in unique_ids:
            mem = self.get(mid)
            if mem:
                memories.append(mem)
        return memories

    def save_mindmap_node(self, node: MindMapNode) -> MindMapNode:
        """Create or update a mind-map node."""
        nodes = self._load_mindmap()
        nodes[node.id] = node.model_dump()
        # Ensure parent's children list includes this node
        if node.parent and node.parent in nodes:
            parent_data = nodes[node.parent]
            if node.id not in parent_data.get("children", []):
                parent_data.setdefault("children", []).append(node.id)
        self._save_mindmap(nodes)
        return node

    def delete_mindmap_node(self, node_id: str) -> bool:
        """Delete a node, reparenting its children to the deleted node's parent."""
        nodes = self._load_mindmap()
        if node_id not in nodes:
            return False
        node_data = nodes[node_id]
        parent_id = node_data.get("parent")
        # Reparent children
        for child_id in node_data.get("children", []):
            if child_id in nodes:
                nodes[child_id]["parent"] = parent_id
                if parent_id and parent_id in nodes:
                    if child_id not in nodes[parent_id].get("children", []):
                        nodes[parent_id].setdefault("children", []).append(child_id)
        # Remove from parent's children list
        if parent_id and parent_id in nodes:
            children = nodes[parent_id].get("children", [])
            nodes[parent_id]["children"] = [c for c in children if c != node_id]
        del nodes[node_id]
        self._save_mindmap(nodes)
        return True

    # Placement tuning.  Tag overlap is normalised by the node's tag-set
    # size, so a node cannot win placement merely by having accumulated
    # tags (a 500-tag root overlaps nearly every memory on a raw count,
    # and every memory it wins adds more tags — a gravity well).
    # Provenance gating keeps a memory off nodes populated by other
    # projects when a node of its own project fits at all.  The gate is
    # soft: with no compatible candidate, the best topical node wins.
    PLACEMENT_PROVENANCE_MIN_SHARE = 0.2
    # A placed memory is moved by repair only when a compatible node beats
    # its current node's score by this factor (or the current node is
    # provenance-incompatible), so near-ties do not churn.
    REBALANCE_SCORE_MARGIN = 2.0
    # Part C: recompute node tags from members on repair, so tags cannot
    # accrete without bound (app/utils/memory_collections.member_node_tags).
    # Off by default until the replay measures it; the harness flips it per
    # arm through --overrides.
    NODE_TAGS_FROM_MEMBERS = False
    NODE_TAGS_MIN_SHARE = 0.5
    NODE_TAGS_MAX = 12

    def _provenance(self, rows: Optional[List[dict]] = None,
                    nodes: Optional[Dict[str, dict]] = None
                    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """``(memory id -> collection key, project -> collection key)``.

        Provenance is compared by *collection* (projects inferred to hold
        the same knowledge; see app/utils/memory_collections.py), not by
        raw project path: gating on paths judged a topic spread across
        several repos incompatible with its own node."""
        from app.utils.memory_collections import infer_collections, keys_by_memory_id
        proj_by_id = self._project_by_memory_id(rows)
        coll = infer_collections(self._load_mindmap() if nodes is None else nodes, proj_by_id)
        return keys_by_memory_id(proj_by_id, coll), coll

    def _project_by_memory_id(self, rows: Optional[List[dict]] = None) -> Dict[str, str]:
        """Map memory id -> its first ``scope.project_paths`` entry."""
        rows = self._load_memories() if rows is None else rows
        out: Dict[str, str] = {}
        for r in rows:
            pp = (r.get("scope") or {}).get("project_paths") or []
            if pp and r.get("id"):
                out[r["id"]] = pp[0]
        return out

    @classmethod
    def _provenance_compatible(cls, ndata: dict, mem_projects: set,
                               proj_by_id: Dict[str, str]) -> bool:
        """True when ``mem_projects`` hold at least
        PLACEMENT_PROVENANCE_MIN_SHARE of the node's members with known
        provenance.  A memory or node without provenance is compatible
        with everything."""
        if not mem_projects:
            return True
        known = [proj_by_id[r] for r in ndata.get("memory_refs", []) if r in proj_by_id]
        if not known:
            return True
        share = sum(1 for p in known if p in mem_projects) / len(known)
        return share >= cls.PLACEMENT_PROVENANCE_MIN_SHARE

    def _rank_placement(self, memory: Memory, nodes: Dict[str, dict],
                        proj_by_id: Dict[str, str],
                        coll_of_project: Optional[Dict[str, str]] = None
                        ) -> List[Tuple[float, bool, str]]:
        """Candidate nodes for ``memory``, best first, as
        ``(score, provenance_compatible, node_id)``.

        score = |mem_tags ∩ node_tags| / sqrt(|node_tags|).  Compatible
        nodes rank ahead of incompatible ones; then higher score; then the
        node with fewer tags.  Nodes with no tag overlap are omitted.
        """
        mem_tags = set(t.lower() for t in memory.tags)
        if not mem_tags:
            return []
        from app.utils.memory_collections import memory_keys
        mem_projects = memory_keys(memory.scope.project_paths, coll_of_project or {})
        cands: List[Tuple[bool, float, int, str]] = []
        for nid, ndata in nodes.items():
            node_tags = set(t.lower() for t in ndata.get("tags", []))
            parent = nodes.get(ndata.get("parent") or "")
            if parent:
                # Cell division copies parent tags onto a child; they mark
                # the child as belonging to its parent, not what it is
                # about.  Scored on them, a small child out-ranks its
                # large parent for every generic parent-topic memory.
                own = node_tags - {t.lower() for t in parent.get("tags", [])}
                if own:
                    node_tags = own
            overlap = len(mem_tags & node_tags)
            if not overlap:
                continue
            score = overlap / math.sqrt(len(node_tags))
            compat = self._provenance_compatible(ndata, mem_projects, proj_by_id)
            cands.append((compat, score, -len(node_tags), nid))
        cands.sort(reverse=True)
        return [(score, compat, nid) for compat, score, _neg, nid in cands]

    def place_memory_in_mindmap(self, memory: Memory,
                                _prov: Optional[Tuple[Dict[str, str], Dict[str, str]]] = None
                                ) -> Optional[str]:
        """Auto-place a memory in the mind-map by normalised tag overlap,
        preferring nodes whose members share the memory's project.

        Returns the node ID it was placed under, or None if no match.
        """
        if not memory.tags:
            return None
        nodes = self._load_mindmap()
        if not nodes:
            return None
        proj_by_id, coll = self._provenance(nodes=nodes) if _prov is None else _prov
        ranked = self._rank_placement(memory, nodes, proj_by_id, coll)
        if not ranked:
            return None
        best_id = ranked[0][2]
        if memory.id not in nodes[best_id].get("memory_refs", []):
            nodes[best_id].setdefault("memory_refs", []).append(memory.id)
            self._save_mindmap(nodes)
        # Also update the memory's scope.domain_node
        memory.scope.domain_node = best_id
        self.save(memory)
        return best_id

    @staticmethod
    def _move_ref(nodes: Dict[str, dict], mid: str, src: str, dst: str) -> None:
        nodes[src]["memory_refs"] = [r for r in nodes[src].get("memory_refs", []) if r != mid]
        refs = nodes[dst].setdefault("memory_refs", [])
        if mid not in refs:
            refs.append(mid)

    def _retag_from_members(self, rows: List[dict]) -> int:
        """Replace each populated node's tags with the tags its members
        share (part C).  Returns the number of nodes whose tags changed.
        Nodes with no tagged members keep their tags."""
        from app.utils.memory_collections import member_node_tags
        nodes = self._load_mindmap()
        tags_by_id = {r.get("id"): list(r.get("tags") or []) for r in rows}
        changed = 0
        for n in nodes.values():
            new = member_node_tags(
                [tags_by_id[r] for r in n.get("memory_refs", []) if r in tags_by_id],
                self.NODE_TAGS_MIN_SHARE, self.NODE_TAGS_MAX)
            if new is not None and set(new) != {t.lower() for t in n.get("tags", [])}:
                n["tags"] = new
                changed += 1
        if changed:
            self._save_mindmap(nodes)
        return changed

    def _split_foreign_collections(self, rows: List[dict], key_by_id: Dict[str, str],
                                   coll_of_project: Dict[str, str]) -> int:
        """Move every group of members whose collection is not their node's
        owner.  Each member goes to the best tag-matching node that its own
        collection owns; the rest of the group, when still >= 2, becomes a
        new root whose tags and handle come from the group's own tags.
        Returns the number of memories moved.

        This is what drains a gravity well: a foreign collection that holds
        the majority of a node's members is provenance-compatible there, so
        neither the placement gate nor the rebalance margin ever moves it.
        """
        from app.utils.memory_collections import (
            CATCH_ALL_NODE_IDS, MIN_SPLIT_GROUP, derive_tags, handle_from_tags,
            owner_collection, plan_collection_splits)
        nodes = self._load_mindmap()
        plans = plan_collection_splits(nodes, key_by_id)
        if not plans:
            return 0
        row_by_id = {r.get("id"): r for r in rows}
        owners = {nid: owner_collection(nid, nodes, key_by_id) for nid in nodes}
        moved: Dict[str, str] = {}
        for src, coll, mids in plans:
            leftovers = []
            for mid in mids:
                row = row_by_id.get(mid)
                if row is None or mid in moved:
                    continue
                ranked = self._rank_placement(Memory(**row), nodes, key_by_id, coll_of_project)
                dst = next((nid for _score, _compat, nid in ranked
                            if nid != src and owners.get(nid) == coll
                            and nid not in CATCH_ALL_NODE_IDS), None)
                if dst:
                    self._move_ref(nodes, mid, src, dst)
                    moved[mid] = dst
                else:
                    leftovers.append(mid)
            if len(leftovers) < MIN_SPLIT_GROUP:
                continue
            tags = derive_tags(row_by_id[m].get("tags", []) for m in leftovers)
            handle = handle_from_tags(tags)
            base = "domain_" + (re.sub(r"[^a-z0-9]+", "_", handle.lower()).strip("_")[:30] or "split")
            nid, i = base, 0
            while nid in nodes:
                i += 1
                nid = f"{base}_{i}"
            nodes[nid] = MindMapNode(id=nid, handle=handle, tags=tags).model_dump()
            owners[nid] = coll
            for m in leftovers:
                self._move_ref(nodes, m, src, nid)
                moved[m] = nid
        if moved:
            self._save_mindmap(nodes)
            for mid, dst in moved.items():
                mem = self.get(mid)
                if mem and mem.scope.domain_node != dst:
                    mem.scope.domain_node = dst
                    self.save(mem)
        return len(moved)

    def _detach_from_mindmap(self, memory_id: str) -> int:
        """Remove ``memory_id`` from every node's memory_refs.

        Returns the number of nodes touched.  A memory may be referenced
        by more than one node (cross-placement), so all nodes are swept.
        """
        nodes = self._load_mindmap()
        touched = 0
        for n in nodes.values():
            refs = n.get("memory_refs", [])
            if memory_id in refs:
                n["memory_refs"] = [r for r in refs if r != memory_id]
                touched += 1
        if touched:
            self._save_mindmap(nodes)
        return touched

    def repair_mindmap(self) -> Dict[str, int]:
        """Restore the invariant  ``memory_refs ⊆ active memory ids``.

        Six idempotent passes:
          1. drop refs to memories that are deleted or not active;
          1b. merge root nodes whose handles are the same domain
              (case/whitespace-insensitive) into one node;
          2. place active memories that no node references (before
             pruning, so a tag-matching empty node receives them);
          2b. rebalance placed memories whose node is populated by other
              projects, or that a same-project node clearly out-scores,
              so a node cannot keep what it won by mass alone;
          2c. split out of each node every group (>= 2) of members whose
              collection is not the node's owner: to a node that
              collection owns when one shares a tag, else to a new root
              built from the group's own tags;
          3. prune nodes with no refs and no children, repeating until
             stable so a subtree that only ever referenced deleted
             memories disappears root and all.
        """
        result = {"dangling_refs_removed": 0, "empty_nodes_removed": 0,
                  "placed": 0, "roots_merged": 0, "rebalanced": 0,
                  "collection_splits": 0}
        nodes = self._load_mindmap()
        if not nodes:
            return result
        active_rows = [m for m in self._load_memories()
                       if m.get("status", "active") == "active"]
        active_ids = {m.get("id") for m in active_rows}

        # 1. Dangling refs (and children entries pointing at missing nodes).
        for n in nodes.values():
            refs = n.get("memory_refs", [])
            kept = [r for r in refs if r in active_ids]
            result["dangling_refs_removed"] += len(refs) - len(kept)
            n["memory_refs"] = kept
            n["children"] = [c for c in n.get("children", []) if c in nodes]
        self._save_mindmap(nodes)

        # 1b. Duplicate roots.
        result["roots_merged"] = self._merge_duplicate_roots(nodes)
        self._save_mindmap(nodes)

        # 2. Place unplaced active memories.
        prov = self._provenance(active_rows, nodes)
        proj_by_id, coll = prov
        placed_ids = {r for n in nodes.values() for r in n.get("memory_refs", [])}
        for row in active_rows:
            if row.get("id") not in placed_ids:
                if self.place_memory_in_mindmap(Memory(**row), _prov=prov):
                    result["placed"] += 1

        from app.utils.memory_collections import CATCH_ALL_NODE_IDS
        # 2b. Rebalance.  Decisions are made against one snapshot so the
        #     pass is deterministic; shares shift as memories move, so a
        #     large migration converges over successive repairs.
        #     Memories referenced by more than one node (cross-placed)
        #     are left alone.
        nodes = self._load_mindmap()
        holders: Dict[str, List[str]] = {}
        for nid, n in nodes.items():
            for r in n.get("memory_refs", []):
                holders.setdefault(r, []).append(nid)
        moves: List[Tuple[str, str, str]] = []
        for row in active_rows:
            mid = row.get("id")
            if len(holders.get(mid, [])) != 1:
                continue
            cur = holders[mid][0]
            mem = Memory(**row)
            # The catch-all root never receives a memory from a real
            # topic node (see memory_collections.CATCH_ALL_NODE_IDS).
            ranked = [c for c in self._rank_placement(mem, nodes, proj_by_id, coll)
                      if c[2] == cur or c[2] not in CATCH_ALL_NODE_IDS]
            if not ranked:
                continue
            best_score, best_compat, best_id = ranked[0]
            if best_id == cur or not best_compat:
                continue
            cur_entry = next((c for c in ranked if c[2] == cur), None)
            cur_compat = self._provenance_compatible(
                nodes[cur], {coll.get(p, p) for p in mem.scope.project_paths or []},
                proj_by_id)
            # No tag overlap with the current node means it was placed by
            # something other than tags (organizer); only provenance may
            # move it.
            clearly_better = cur_entry is not None and \
                best_score >= cur_entry[0] * self.REBALANCE_SCORE_MARGIN
            if not cur_compat or clearly_better:
                moves.append((mid, cur, best_id))
        for mid, src, dst in moves:
            nodes[src]["memory_refs"] = [r for r in nodes[src].get("memory_refs", []) if r != mid]
            nodes[dst].setdefault("memory_refs", []).append(mid)
        if moves:
            self._save_mindmap(nodes)
            for mid, _src, dst in moves:
                mem = self.get(mid)
                if mem and mem.scope.domain_node != dst:
                    mem.scope.domain_node = dst
                    self.save(mem)
        result["rebalanced"] = len(moves)

        # 2c. Collection split.
        result["collection_splits"] = self._split_foreign_collections(
            active_rows, proj_by_id, coll)

        # 2d. Member-derived node tags (part C; see NODE_TAGS_FROM_MEMBERS).
        result["retagged"] = (self._retag_from_members(active_rows)
                              if self.NODE_TAGS_FROM_MEMBERS else 0)

        # 3. Prune empty leaves until stable.
        nodes = self._load_mindmap()
        while True:
            empty = [nid for nid, n in nodes.items()
                     if not n.get("memory_refs") and not n.get("children")]
            if not empty:
                break
            for nid in empty:
                parent = nodes[nid].get("parent")
                if parent in nodes:
                    nodes[parent]["children"] = [
                        c for c in nodes[parent].get("children", []) if c != nid]
                del nodes[nid]
            result["empty_nodes_removed"] += len(empty)
        if result["empty_nodes_removed"]:
            self._save_mindmap(nodes)
        return result

    @staticmethod
    def _normalize_handle(handle: str) -> str:
        return " ".join((handle or "").lower().split())

    def _merge_duplicate_roots(self, nodes: Dict[str, dict]) -> int:
        """Fold roots sharing a normalized handle into one.  Mutates ``nodes``
        in place (caller saves).  Returns the number of roots absorbed.

        Survivor: the root whose id does not carry a ``_N`` suffix if one
        exists, else the first by id.  Refs, tags, children and cross_links
        are unioned; access stats take the max; children are reparented;
        memories whose ``scope.domain_node`` pointed at an absorbed root are
        re-pointed at the survivor.
        """
        groups: Dict[str, List[str]] = {}
        for nid, n in nodes.items():
            if n.get("parent") is None:
                groups.setdefault(self._normalize_handle(n.get("handle")), []).append(nid)

        absorbed_to_survivor: Dict[str, str] = {}
        for _, ids in groups.items():
            if len(ids) < 2:
                continue
            ids.sort()
            unsuffixed = [i for i in ids if not re.search(r"_\d+$", i)]
            survivor_id = unsuffixed[0] if unsuffixed else ids[0]
            surv = nodes[survivor_id]
            for nid in ids:
                if nid == survivor_id:
                    continue
                other = nodes[nid]
                for key in ("memory_refs", "tags", "children", "cross_links"):
                    merged = list(dict.fromkeys(surv.get(key, []) + other.get(key, [])))
                    surv[key] = [x for x in merged if x != survivor_id]
                for child in other.get("children", []):
                    if child in nodes:
                        nodes[child]["parent"] = survivor_id
                surv["access_count"] = max(surv.get("access_count", 0),
                                           other.get("access_count", 0))
                surv["last_accessed"] = max(surv.get("last_accessed", ""),
                                            other.get("last_accessed", ""))
                absorbed_to_survivor[nid] = survivor_id
                del nodes[nid]

        if absorbed_to_survivor:
            # Re-point memory scopes at the survivor.
            memories = self._load_memories()
            changed = []
            for row in memories:
                dn = (row.get("scope") or {}).get("domain_node")
                if dn in absorbed_to_survivor:
                    row["scope"]["domain_node"] = absorbed_to_survivor[dn]
                    changed.append(row)
            if changed:
                self._save_memories(memories)
        return len(absorbed_to_survivor)
    # -- Profile -------------------------------------------------------------

    def get_profile(self) -> MemoryProfile:
        data = self._read_json(self._profile_file)
        if data:
            return MemoryProfile(**data)
        return MemoryProfile()

    def save_profile(self, profile: MemoryProfile) -> None:
        self._write_json(self._profile_file, profile.model_dump())


# -- Singleton ---------------------------------------------------------------

_instance: Optional[MemoryStorage] = None


def get_memory_storage() -> MemoryStorage:
    global _instance
    if _instance is None:
        _instance = MemoryStorage()
    return _instance
