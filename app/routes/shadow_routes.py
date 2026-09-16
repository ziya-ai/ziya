"""Shadow-session state for the chat UI (design doc §9).

One read-only endpoint: which ``ziya shadow`` terminals are attached to a
conversation, for the chip rendered on the chat input box.  It reads the
same helper the model's per-turn context tag uses, so the chip and the
model can never disagree about what is attached.  Same-UID only by
construction — the registry and sockets are 0600.
"""
import asyncio

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/shadow", tags=["shadow"])


@router.get("/attached")
async def attached(conversation_id: str = Query(..., min_length=1, max_length=128)):
    # attached_sessions() does blocking work — a registry glob + up to two
    # 0.3s AF_UNIX round-trips PER attached session (client.request 'info' and
    # 'control_status'). This handler is async def, so calling it inline runs
    # that blocking I/O directly on the event loop. Every open conversation
    # window polls this endpoint every 5s (ShadowSessionChip, POLL_MS=5000),
    # so under any window count a hung/slow shadow host — where connect/recv
    # each burn the full 0.3s timeout — stalls the loop for EVERY request in
    # the process, including the project-switch fetches that populate the
    # sidebar. Offload to a worker thread so the loop keeps turning.
    from app.shadow.context import attached_sessions
    sessions = await asyncio.to_thread(attached_sessions, conversation_id)
    return {"conversation_id": conversation_id, "sessions": sessions}
