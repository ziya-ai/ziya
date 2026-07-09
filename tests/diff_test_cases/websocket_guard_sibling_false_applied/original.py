from starlette.websockets import WebSocket, WebSocketDisconnect
from app.middleware.origin_guard import is_websocket_origin_allowed
from app.utils.logging_utils import logger

active_feedback_connections = {}
active_file_tree_connections = set()


@app.websocket("/ws/feedback/{conversation_id}")
async def feedback_websocket(websocket: WebSocket, conversation_id: str):
    # WebSocket upgrades bypass OriginGuardMiddleware entirely (it only
    # runs on the http ASGI scope) -- check explicitly before accept()
    # [PenPal #157, CWE-94].
    if not is_websocket_origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    logger.debug(f"FEEDBACK: WebSocket connected for conversation {conversation_id}")

    try:
        while True:
            try:
                data = await websocket.receive_json()
                _ = data.get('type')
            except WebSocketDisconnect:
                break
    finally:
        active_feedback_connections.pop(conversation_id, None)


@app.websocket("/ws/file-tree")
async def file_tree_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time file tree update notifications."""
    logger.debug("FILE_TREE: WebSocket connection attempt")
    if not is_websocket_origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    active_file_tree_connections.add(websocket)
    logger.debug("FILE_TREE: WebSocket connected")

    try:
        await websocket.send_json({'type': 'connected'})
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                logger.debug("FILE_TREE: WebSocket disconnected")
                break
    finally:
        active_file_tree_connections.discard(websocket)
        logger.debug(f"FILE_TREE: Connection removed, {len(active_file_tree_connections)} remaining")


@app.websocket("/ws/delegate-stream/{conversation_id}")
async def delegate_stream_websocket(websocket: WebSocket, conversation_id: str):
    """WebSocket endpoint for live delegate conversation streaming.

    When a user views a delegate conversation, the frontend connects here.
    DelegateManager pushes chunks via delegate_stream_relay.push(), which
    this endpoint relays to the connected client in real time.
    """
    await websocket.accept()
    logger.debug(f"DELEGATE_STREAM: WebSocket connected for {conversation_id[:8]}")

    from app.agents.delegate_stream_relay import connect, disconnect
    await connect(conversation_id, websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.debug(f"DELEGATE_STREAM: WebSocket disconnected for {conversation_id[:8]}")
    except Exception:
        logger.debug(f"DELEGATE_STREAM: Connection lost for {conversation_id[:8]}")
    finally:
        await disconnect(conversation_id, websocket)
