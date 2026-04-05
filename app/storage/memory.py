"""
Memory storage — flat JSON file store for the structured memory system.

Phase 0: Single file (~/.ziya/memory/memories.json) with full CRUD.
Profile and project hints stored alongside.  ALE encryption-aware
via the same BaseStorage pattern used by chats and skills.
"""
import json
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

from app.utils.logging_utils import logger
from app.models.memory import (
    Memory, MemoryProposal, MemoryProfile, ProjectHints, MindMapNode
)


class MemoryStorage:
    """File-based memory store under ~/.ziya/memory/."""

    def __init__(self, memory_dir: Optional[Path] = None):
        if memory_dir is None:
            from app.utils.paths import get_ziya_home
            memory_dir = get_ziya_home() / "memory"
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

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
        data = self._read_json(self._memories_file)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "memories" in data:
            return data["memories"]
        return []

    def _save_memories(self, memories: List[dict]) -> None:
        self._write_json(self._memories_file, memories)

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

    def search(self, query: str, limit: int = 20) -> List[Memory]:
        """Keyword search across content, tags, and layer."""
        q = query.lower()
        raw = self._load_memories()
        scored: List[tuple] = []
        for m in raw:
            if m.get("status") not in (None, "active"):
                continue
            score = 0
            content = m.get("content", "").lower()
            tags = " ".join(m.get("tags", [])).lower()
            if q in content:
                score += 2
            if q in tags:
                score += 3
            if q in m.get("layer", ""):
                score += 1
            if score > 0:
                scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [Memory(**m) for _, m in scored[:limit]]

    def get(self, memory_id: str) -> Optional[Memory]:
        for m in self._load_memories():
            if m.get("id") == memory_id:
                return Memory(**m)
        return None

    def save(self, memory: Memory) -> Memory:
        """Create or update a memory in the flat store."""
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
        self._save_memories(memories)
        logger.info(f"💾 Memory saved: {memory.id} [{memory.layer}] {memory.content[:60]}")
        return memory

    def delete(self, memory_id: str) -> bool:
        memories = self._load_memories()
        before = len(memories)
        memories = [m for m in memories if m.get("id") != memory_id]
        if len(memories) == before:
            return False
        self._save_memories(memories)
        logger.info(f"🗑️ Memory deleted: {memory_id}")
        return True

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

    def add_proposal(self, proposal: MemoryProposal) -> MemoryProposal:
        proposals = self._read_json(self._proposals_file) or []
        proposals.append(proposal.model_dump())
        self._write_json(self._proposals_file, proposals)
        return proposal

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

    def place_memory_in_mindmap(self, memory: Memory) -> Optional[str]:
        """Auto-place a memory in the mind-map based on tag overlap.

        Returns the node ID it was placed under, or None if no match.
        """
        if not memory.tags:
            return None
        nodes = self._load_mindmap()
        if not nodes:
            return None
        mem_tags = set(t.lower() for t in memory.tags)
        best_id, best_score = None, 0
        for nid, ndata in nodes.items():
            node_tags = set(t.lower() for t in ndata.get("tags", []))
            overlap = len(mem_tags & node_tags)
            if overlap > best_score:
                best_score = overlap
                best_id = nid
        if best_id and best_score > 0:
            if memory.id not in nodes[best_id].get("memory_refs", []):
                nodes[best_id].setdefault("memory_refs", []).append(memory.id)
                self._save_mindmap(nodes)
            # Also update the memory's scope.domain_node
            memory.scope.domain_node = best_id
            self.save(memory)
            return best_id
        return None
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
