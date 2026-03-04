"""
API routes for conversation graph visualization.

Reads from existing chat storage (app/storage/chats.py) and builds
a graph representation of the conversation structure.
"""

from fastapi import APIRouter, HTTPException, Query

from app.utils.logging_utils import logger
from app.utils.paths import get_project_dir
from app.storage.chats import ChatStorage
from app.plugins.conversation_graph.graph_manager import get_graph_manager

router = APIRouter(tags=["graph"])


@router.get("/api/v1/projects/{project_id}/chats/{chat_id}/graph")
async def get_conversation_graph(
    project_id: str,
    chat_id: str,
    force_rebuild: bool = Query(False),
):
    """
    Build and return a graph representation of the conversation.

    The graph is cached in SQLite; pass ``force_rebuild=true`` to
    regenerate from the raw messages.
    """
    try:
        logger.info(f"🌳 Graph requested: {project_id}/{chat_id}")

        # Load chat via the existing storage layer
        project_dir = get_project_dir(project_id)
        storage = ChatStorage(project_dir)
        chat = storage.get(chat_id)

        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found")

        messages = chat.messages
        if not messages:
            return {
                "conversationId": chat_id,
                "graphMode": "conversation",
                "nodes": [],
                "edges": [],
                "rootId": None,
                "currentId": None,
            }

        manager = get_graph_manager()
        return manager.get_serialized(
            project_id, chat_id, messages, force_rebuild,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"🌳 Graph build failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to build graph: {exc}",
        )
