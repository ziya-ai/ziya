"""
Agent overlay API (design/agent-overlay.md, step 1).

  GET  /api/v1/projects/{pid}/agents?conversation_id=   installed agents + the active one
  GET  /api/v1/projects/{pid}/agents/import?name=       how one was imported
  PUT  /api/v1/projects/{pid}/agents/active             turn one on or off

The agent is named in a query or body, never a path segment, so no route
here can shadow another.  Plain ``def`` handlers: nothing is awaited.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import agent_overlay
from app.services import bench as bench_service

router = APIRouter(prefix="/api/v1/projects/{project_id}/agents", tags=["agents"])


class ActiveRequest(BaseModel):
    conversation_id: Optional[str] = None
    name: Optional[str] = None       # null turns the agent off


@router.get("")
def get_agents(project_id: str, conversation_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    return {
        "agents": [agent_overlay.summary(d) for d in agent_overlay.list_definitions()],
        "active": agent_overlay.active_agent_name(project_id, conversation_id),
    }


@router.get("/import")
def get_import(project_id: str, name: str = Query(...)) -> Dict[str, Any]:
    defn = agent_overlay.get_definition(name)
    if defn is None:
        raise HTTPException(status_code=404, detail=f"no such agent: {name}")
    return {**agent_overlay.summary(defn), "rows": agent_overlay.import_rows(project_id, defn)}


@router.put("/active")
def put_active(project_id: str, body: ActiveRequest) -> Dict[str, Any]:
    try:
        defn = agent_overlay.set_active(project_id, body.conversation_id, body.name)
    except agent_overlay.AgentNotFound as e:
        raise HTTPException(status_code=404, detail=f"no such agent: {e}")
    except agent_overlay.AgentNotRunnable as e:
        raise HTTPException(status_code=409, detail={"name": e.name, "blockers": e.blockers})
    except bench_service.ConversationRequired as e:
        raise HTTPException(status_code=400, detail=str(e))
    except bench_service.ChatNotFound as e:
        raise HTTPException(status_code=404, detail=f"conversation not found: {e}")
    return {"active": defn.name if defn else None,
            "agent": agent_overlay.summary(defn) if defn else None}
