"""
Consent API (design/consent-runtime.md step 3).

    POST /api/consent/{request_id}          answer an open tool-consent request
    GET  /api/consent/open?conversation_id= open requests for a conversation
    GET  /api/consent/{request_id}          one record (for a panel to refresh)

``request_id`` is ``conv:{conversation_id}:{tool_call_id}`` for chat tool
consents.  Task-run Ask blocks keep their existing endpoint
(``POST /task-runs/{run}/ask/{block}``), which writes through the same
ledger semantics via TaskRunStorage; both surfaces are first-answer-wins.

First answer wins is reported, not errored: a double click or two windows
answering at once both succeed from the caller's point of view, and the
response carries the answer that actually landed.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.utils import consent_gate
from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)

router = APIRouter(prefix="/api/consent", tags=["consent"])


class ConsentAnswer(BaseModel):
    decision: str = Field(..., description="approve | reject")
    scope: str = Field("once", description="once | conversation | session | always")
    answer: str = ""
    answered_by: str = ""


def _public(rec) -> Dict[str, Any]:
    out = rec.public()
    out["settled"] = rec.settled
    out["closed"] = rec.closed
    out["answer"] = rec.answer
    return out


@router.get("/open")
async def list_open_consents(conversation_id: str = Query(...)) -> List[Dict[str, Any]]:
    """Open (unanswered) consent requests for a conversation.

    The reconnect handshake calls this so a consent whose ``consent_opened``
    frame has rolled out of the relay buffer is still rendered.
    """
    try:
        return [_public(r) for r in consent_gate.ledger().open_for(conversation_id)]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"consent open_for failed: {exc}")
        return []


@router.get("/{request_id:path}")
async def get_consent(request_id: str) -> Dict[str, Any]:
    rec = consent_gate.ledger().get(request_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="No such consent request")
    return _public(rec)


@router.post("/{request_id:path}")
async def answer_consent(request_id: str, body: ConsentAnswer) -> Dict[str, Any]:
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision must be 'approve' or 'reject'")
    if body.scope not in consent_gate.SCOPES:
        raise HTTPException(
            status_code=422,
            detail=f"scope must be one of {list(consent_gate.SCOPES)}",
        )
    led = consent_gate.ledger()
    rec = led.get(request_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="No such consent request")
    if rec.closed and not rec.settled:
        # Closed without an answer: the waiting turn was cancelled or the
        # request expired.  Nothing is listening for this answer.
        raise HTTPException(
            status_code=409,
            detail="This request is no longer waiting for an answer",
        )
    settled = led.record_answer(
        request_id, body.decision, scope=body.scope,
        answer=body.answer, answered_by=body.answered_by,
    )
    if settled is None:
        raise HTTPException(status_code=404, detail="No such consent request")
    return _public(settled)
