"""
Conversation graph state management — build, cache, and retrieve.

Uses a dedicated SQLite database at ~/.ziya/conversation_graphs.db.
Creates all four tables at startup (conversation_graphs,
node_status_overrides, node_questions, delegate_graphs) even though
Phase 0-A only uses the first.  This avoids migration headaches later.
"""

import json
import time

try:
    import sqlite3
    _HAS_SQLITE3 = True
except ImportError:
    _HAS_SQLITE3 = False
from typing import Optional, List, Dict, Any

from app.utils.logging_utils import logger
from app.utils.paths import get_ziya_home
from .graph_builder import ConversationGraphBuilder
from .types import ConversationGraph, ConversationNode, NodeType, NodeStatus


_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS conversation_graphs (
    project_id TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    graph_data TEXT NOT NULL,
    last_updated INTEGER NOT NULL,
    version    INTEGER DEFAULT 1,
    node_count INTEGER DEFAULT 0,
    PRIMARY KEY (project_id, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_graphs_updated
    ON conversation_graphs(last_updated);

CREATE TABLE IF NOT EXISTS node_status_overrides (
    project_id TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (project_id, chat_id, node_id)
);

CREATE TABLE IF NOT EXISTS node_questions (
    question_id   TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    chat_id       TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    question_text TEXT NOT NULL,
    question_type TEXT NOT NULL,
    options       TEXT,
    answer        TEXT,
    answered_at   INTEGER,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS delegate_graphs (
    project_id   TEXT NOT NULL,
    group_id     TEXT NOT NULL,
    graph_data   TEXT NOT NULL,
    last_updated INTEGER NOT NULL,
    PRIMARY KEY (project_id, group_id)
);
"""


class GraphManager:
    """Build, cache, and serve conversation graphs."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(get_ziya_home() / "conversation_graphs.db")
        self.db_path = db_path
        self._sqlite_available = _HAS_SQLITE3
        if self._sqlite_available:
            try:
                self._ensure_schema()
            except Exception as exc:
                logger.warning(f"🌳 SQLite unavailable, graphs will not be persisted: {exc}")
                self._sqlite_available = False
        else:
            logger.info("🌳 sqlite3 not available — conversation graphs will not be persisted (rebuild-on-demand)")

    def _ensure_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_CREATE_TABLES)
            conn.commit()
        logger.info("🌳 Conversation-graphs schema ready")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build_graph(
        self,
        project_id: str,
        chat_id: str,
        messages: list,
        force_rebuild: bool = False,
    ) -> ConversationGraph:
        if not force_rebuild:
            cached = self._load_cached(project_id, chat_id)
            if cached is not None:
                logger.info(f"🌳 Cache hit for {project_id}/{chat_id}")
                return cached

        builder = ConversationGraphBuilder()
        graph = builder.build_from_messages(messages, chat_id)
        self._save(project_id, chat_id, graph)
        return graph

    def get_serialized(
        self,
        project_id: str,
        chat_id: str,
        messages: list,
        force_rebuild: bool = False,
    ) -> Dict[str, Any]:
        """Convenience: build + serialize in one call."""
        return self.build_graph(
            project_id, chat_id, messages, force_rebuild,
        ).to_dict()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_cached(self, pid: str, cid: str) -> Optional[ConversationGraph]:
        if not self._sqlite_available:
            return None
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT graph_data FROM conversation_graphs "
                    "WHERE project_id = ? AND chat_id = ?",
                    (pid, cid),
                ).fetchone()
            if row:
                return self._deserialize(json.loads(row[0]))
        except Exception as exc:
            logger.warning(f"🌳 Cache read failed: {exc}")
        return None

    def _save(self, pid: str, cid: str, graph: ConversationGraph):
        if not self._sqlite_available:
            return
        try:
            data = json.dumps(graph.to_dict())
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO conversation_graphs "
                    "(project_id, chat_id, graph_data, last_updated, version, node_count) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (pid, cid, data, int(time.time()), len(graph.nodes)),
                )
                conn.commit()
            logger.info(f"🌳 Saved graph ({len(graph.nodes)} nodes) for {pid}/{cid}")
        except Exception as exc:
            logger.error(f"🌳 Cache write failed: {exc}")

    # ------------------------------------------------------------------
    # (De)serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _deserialize(data: Dict[str, Any]) -> ConversationGraph:
        nodes: Dict[str, ConversationNode] = {}
        for nd in data.get("nodes", []):
            nodes[nd["id"]] = ConversationNode(
                id=nd["id"],
                timestamp=nd.get("timestamp", 0),
                type=NodeType(nd.get("type", "idea")),
                content=nd.get("content", ""),
                full_context=nd.get("fullContext", ""),
                author=nd.get("author", "ai"),
                parent_id=nd.get("parentId"),
                child_ids=nd.get("childIds", []),
                status=NodeStatus(nd.get("status", "proposed")),
                importance=nd.get("importance", 0.5),
                tags=nd.get("tags", []),
                attachments=nd.get("attachments", []),
                linked_node_ids=nd.get("linkedNodeIds", []),
                branch_name=nd.get("branchName"),
                merged_into=nd.get("mergedInto"),
                delegate_id=nd.get("delegateId"),
                crystal=nd.get("crystal"),
                delegate_color=nd.get("delegateColor"),
                visibility=nd.get("visibility", "global"),
            )
        return ConversationGraph(
            conversation_id=data.get("conversationId", ""),
            nodes=nodes,
            edges=data.get("edges", []),
            root_id=data.get("rootId", ""),
            current_id=data.get("currentId", ""),
            graph_mode=data.get("graphMode", "conversation"),
        )


_instance: Optional[GraphManager] = None


def get_graph_manager() -> GraphManager:
    global _instance
    if _instance is None:
        _instance = GraphManager()
    return _instance
