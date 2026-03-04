"""
Type definitions for conversation graph structures.

Includes all types for Phase 0-A through Phase 2.
Phase 0-A only uses a subset, but all values are defined now
to prevent model changes when later phases begin.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class NodeType(Enum):
    # Phase 0-A
    ROOT = "root"
    IDEA = "idea"
    QUESTION = "question"
    DECISION = "decision"
    TASK = "task"
    BRANCH_POINT = "branch_point"
    # Phase 2
    ORCHESTRATOR = "orchestrator"
    DELEGATE = "delegate"
    CRYSTAL = "crystal"
    CONFLICT = "conflict"
    CLARIFICATION = "clarification"


class NodeStatus(Enum):
    # Phase 0-A
    PROPOSED = "proposed"
    EXPLORING = "exploring"
    AGREED = "agreed"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    OPEN_QUESTION = "open_question"
    # Phase 2
    RUNNING = "running"
    COMPACTING = "compacting"
    FAILED = "failed"


class EdgeType(Enum):
    # Phase 0-A
    CONTINUES = "continues"
    BRANCHES = "branches"
    REFINES = "refines"
    QUESTIONS = "questions"
    IMPLEMENTS = "implements"
    # Phase 2
    SPAWNS = "spawns"
    DEPENDS_ON = "depends_on"
    INJECTS = "injects"
    CONFLICTS = "conflicts"


@dataclass
class ConversationNode:
    """A node in the conversation timeline."""
    id: str
    timestamp: float
    type: NodeType
    content: str                            # Summary text (~60 chars for display)
    full_context: str                       # Complete message content
    author: str                             # 'user' | 'ai' | 'delegate' | 'orchestrator'

    # Graph structure
    parent_id: Optional[str] = None
    child_ids: List[str] = field(default_factory=list)

    # Visual / state
    status: NodeStatus = NodeStatus.PROPOSED
    importance: float = 0.5                 # 0-1, affects visual weight (node size)
    tags: List[str] = field(default_factory=list)

    # Rich content
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    linked_node_ids: List[str] = field(default_factory=list)

    # Delegate-specific fields (None for regular conversation nodes)
    branch_name: Optional[str] = None       # e.g. "D1: OAuth Provider"
    merged_into: Optional[str] = None       # Crystal merged into convergence node
    delegate_id: Optional[str] = None       # Reference to the delegate's conversation ID
    crystal: Optional[Dict[str, Any]] = None  # MemoryCrystal data when status=agreed
    delegate_color: Optional[str] = None    # Color from the delegate's auto-generated Context

    # Future-proofing
    visibility: str = "global"              # For future scope control

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict for the frontend."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "type": self.type.value,
            "content": self.content,
            "fullContext": self.full_context,
            "author": self.author,
            "parentId": self.parent_id,
            "childIds": self.child_ids,
            "status": self.status.value,
            "importance": self.importance,
            "tags": self.tags,
            "attachments": self.attachments,
            "linkedNodeIds": self.linked_node_ids,
            "branchName": self.branch_name,
            "mergedInto": self.merged_into,
            "delegateId": self.delegate_id,
            "crystal": self.crystal,
            "delegateColor": self.delegate_color,
            "visibility": self.visibility,
        }


@dataclass
class ConversationGraph:
    """Complete graph representation of a conversation or task plan."""
    conversation_id: str
    nodes: Dict[str, ConversationNode]      # id -> node
    edges: List[Dict[str, str]]             # [{from, to, type}, ...]
    root_id: str
    current_id: str
    graph_mode: str = "conversation"        # "conversation" | "task_plan"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON API response."""
        return {
            "conversationId": self.conversation_id,
            "graphMode": self.graph_mode,
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": self.edges,
            "rootId": self.root_id,
            "currentId": self.current_id,
        }
