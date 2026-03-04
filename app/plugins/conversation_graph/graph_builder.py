"""
Build conversation graphs from message history.

Phase 0-A: Heuristic extraction via regex patterns.
Identifies ideas, decisions, questions, tasks, and branch points.

Known limitation: cannot detect topic-level branching *within* a single
message (e.g. one long AI response discussing both "questionnaire" and
"canvas").  Phase 0-B adds manual correction; future phases add LLM
extraction.
"""

import re
import time
from typing import List, Dict, Any

from app.utils.logging_utils import logger
from .types import (
    ConversationNode, ConversationGraph,
    NodeType, NodeStatus, EdgeType,
)


class ConversationGraphBuilder:
    """Extract structure from conversation messages and build a graph."""

    def __init__(self):
        self._nodes: Dict[str, ConversationNode] = {}
        self._edges: List[Dict[str, str]] = []
        self._counter = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_from_messages(
        self,
        messages: List[Any],
        conversation_id: str,
    ) -> ConversationGraph:
        """
        Parse *messages* (list of Message pydantic models or dicts) and
        return a ``ConversationGraph``.
        """
        logger.info(f"🌳 Building graph from {len(messages)} messages")

        # Root node
        first_ts = self._msg_timestamp(messages[0]) if messages else time.time()
        root_id = self._add_node(
            NodeType.ROOT, "Conversation start", "",
            author="system", timestamp=first_ts,
            status=NodeStatus.AGREED, importance=1.0,
        )

        current_id = root_id

        for msg in messages:
            content = self._msg_content(msg)
            author = self._msg_author(msg)
            ts = self._msg_timestamp(msg)

            extracted = self._extract_structure(content, author, ts)

            if extracted:
                for node_id in extracted:
                    self._add_edge(current_id, node_id, EdgeType.CONTINUES)
                    current_id = node_id
            else:
                node_id = self._add_node(
                    NodeType.IDEA,
                    self._summarize(content),
                    content,
                    author=author, timestamp=ts,
                )
                self._add_edge(current_id, node_id, EdgeType.CONTINUES)
                current_id = node_id

        graph = ConversationGraph(
            conversation_id=conversation_id,
            nodes=self._nodes,
            edges=self._edges,
            root_id=root_id,
            current_id=current_id,
            graph_mode="conversation",
        )
        logger.info(
            f"🌳 Graph complete: {len(self._nodes)} nodes, "
            f"{len(self._edges)} edges"
        )
        return graph

    # ------------------------------------------------------------------
    # Structure extraction (heuristic)
    # ------------------------------------------------------------------

    def _extract_structure(
        self, content: str, author: str, ts: float,
    ) -> List[str]:
        """Return list of newly created node IDs, or empty list."""
        created: List[str] = []

        # 1. Checkbox tasks  — [ ] / - [x]
        for m in re.finditer(
            r"^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$", content, re.MULTILINE,
        ):
            done = m.group(1).lower() == "x"
            nid = self._add_node(
                NodeType.TASK, m.group(2).strip(),
                f"Task: {m.group(2).strip()}",
                author=author, timestamp=ts,
                status=NodeStatus.AGREED if done else NodeStatus.PROPOSED,
                importance=0.8,
            )
            created.append(nid)

        # 2. Structured questions (only when ≥2 found)
        q_matches = list(re.finditer(
            r"(?:^[\s]*[\d]+\.|#+)\s*\*{0,2}(.+?\?)\*{0,2}",
            content, re.MULTILINE,
        ))
        if len(q_matches) >= 2:
            for m in q_matches[:5]:
                nid = self._add_node(
                    NodeType.QUESTION, m.group(1).strip(),
                    m.group(1).strip(),
                    author=author, timestamp=ts,
                    status=NodeStatus.OPEN_QUESTION,
                    importance=0.6,
                )
                created.append(nid)

        # 3. Decisions / agreements
        decision_kw = [
            r"\bagreed\b", r"\bdecided\b", r"\blet'?s use\b",
            r"\bconfirmed\b", r"✅",
        ]
        for pat in decision_kw:
            if re.search(pat, content, re.IGNORECASE):
                for sentence in re.split(r"[.!]\s+", content):
                    if re.search(pat, sentence, re.IGNORECASE):
                        nid = self._add_node(
                            NodeType.DECISION,
                            self._summarize(sentence),
                            sentence.strip(),
                            author=author, timestamp=ts,
                            status=NodeStatus.AGREED,
                            importance=0.9,
                        )
                        created.append(nid)
                        break
                break  # one decision extraction pass per message

        # 4. Branch indicators
        branch_kw = [
            r"\balternatively\b", r"\bor we could\b",
            r"\boption [AB]\b", r"\banother approach\b",
        ]
        for pat in branch_kw:
            if re.search(pat, content, re.IGNORECASE):
                nid = self._add_node(
                    NodeType.BRANCH_POINT,
                    "Alternative explored",
                    content,
                    author=author, timestamp=ts,
                    status=NodeStatus.EXPLORING,
                    importance=0.7,
                )
                created.append(nid)
                break

        return created

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _add_node(
        self, node_type: NodeType, content: str, full_context: str, *,
        author: str, timestamp: float,
        status: NodeStatus = NodeStatus.PROPOSED,
        importance: float | None = None,
    ) -> str:
        self._counter += 1
        nid = f"node_{self._counter}"
        if importance is None:
            importance = self._base_importance(node_type, content)
        self._nodes[nid] = ConversationNode(
            id=nid, timestamp=timestamp, type=node_type,
            content=self._summarize(content, 60),
            full_context=full_context, author=author,
            status=status, importance=importance,
        )
        return nid

    def _add_edge(self, src: str, dst: str, etype: EdgeType):
        if dst in self._nodes:
            self._nodes[dst].parent_id = src
        if src in self._nodes and dst not in self._nodes[src].child_ids:
            self._nodes[src].child_ids.append(dst)
        self._edges.append({"from": src, "to": dst, "type": etype.value})

    @staticmethod
    def _base_importance(ntype: NodeType, content: str) -> float:
        base = {
            NodeType.ROOT: 1.0, NodeType.DECISION: 0.9,
            NodeType.TASK: 0.8, NodeType.BRANCH_POINT: 0.7,
            NodeType.QUESTION: 0.6, NodeType.IDEA: 0.5,
        }.get(ntype, 0.5)
        if any(w in content.lower() for w in ("critical", "important", "must", "key")):
            base = min(1.0, base + 0.15)
        return base

    @staticmethod
    def _summarize(text: str, max_len: int = 60) -> str:
        text = re.sub(r"\*+", "", text)
        text = re.sub(r"`+", "", text)
        first = re.split(r"[.!?]\s+", text)[0].strip()
        return first if len(first) <= max_len else first[: max_len - 3] + "..."

    @staticmethod
    def _msg_content(msg: Any) -> str:
        if hasattr(msg, "content"):
            return msg.content
        return msg.get("content", "")

    @staticmethod
    def _msg_author(msg: Any) -> str:
        role = msg.role if hasattr(msg, "role") else msg.get("role", "human")
        return "user" if role == "human" else "ai" if role == "assistant" else role

    @staticmethod
    def _msg_timestamp(msg: Any) -> float:
        ts = msg.timestamp if hasattr(msg, "timestamp") else msg.get("timestamp", 0)
        # Chat model stores timestamps as milliseconds; convert to seconds
        return ts / 1000.0 if ts > 1e12 else float(ts) if ts else time.time()
