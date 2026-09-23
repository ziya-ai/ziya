"""
Bench API (design/capabilities-hub.md, slice 3).

  GET  /api/v1/projects/{pid}/bench?conversation_id=   the view-model
  PUT  /api/v1/projects/{pid}/bench/place              one placement

The router owns no ``/{id}`` route, so ``/place`` cannot be shadowed; the
prefix has two segments after ``{project_id}`` so the projects router's
``/{project_id}`` cannot capture it either (see test_bench_api).

The lens switch is client-only: GET returns every layer's placement per item
and the per-lens delta counts, and the client re-derives ghost vs pin.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.models.activation import PlacementRejected, WRITABLE_LAYERS
from app.services import bench as bench_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/projects/{project_id}/bench", tags=["bench"])


class PlaceRequest(BaseModel):
    key: str
    layer: str
    state: Optional[str] = None      # null clears the placement at that layer
    conversation_id: Optional[str] = None


def _view_model(items: List[bench_service.BenchItem], result, conversation_id: Optional[str]) -> Dict[str, Any]:
    meta = {i.spec.key: i for i in items}
    out_items = []
    weight = {"always": 0, "ondemand": 0, "environment": 0}
    deltas = {layer: 0 for layer in WRITABLE_LAYERS}
    for r in result.items:
        m = meta[r.key]
        d = r.model_dump()
        d.update(name=m.name, provenance=m.provenance, tokens=m.tokens, health=m.health)
        out_items.append(d)
        if r.tier == "environment":
            weight["environment"] += m.tokens
        elif r.effective in weight:
            weight[r.effective] += m.tokens
        for layer, state in r.placements.items():
            if state is not None:
                deltas[layer] += 1
    lenses = [l for l in WRITABLE_LAYERS if l != "conversation" or conversation_id]
    return {
        "items": out_items,
        "dropped": [d.model_dump() for d in result.dropped],
        "lenses": lenses,
        "deltas": deltas,
        "weight": weight,
        "conversation_id": conversation_id,
    }


@router.get("")
async def get_bench(project_id: str, conversation_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    try:
        items = bench_service.collect_items(project_id)
    except HTTPException:
        raise
    result = bench_service.resolve_bench(project_id, conversation_id, items)
    return _view_model(items, result, conversation_id)


@router.put("/place")
async def put_place(project_id: str, body: PlaceRequest) -> Dict[str, Any]:
    items = bench_service.collect_items(project_id)
    try:
        bench_service.place(project_id, body.conversation_id, body.key, body.layer, body.state, items)
    except PlacementRejected as e:
        raise HTTPException(status_code=409, detail={"reason": e.reason, "key": e.key, "message": str(e)})
    except bench_service.ConversationRequired as e:
        raise HTTPException(status_code=400, detail=str(e))
    except bench_service.ChatNotFound as e:
        raise HTTPException(status_code=404, detail=f"conversation not found: {e}")

    # A user-layer MCP placement maps onto the existing enable switch: ``off``
    # means not spawned at all; anything else (or clearing) re-enables.
    if body.layer == "user" and body.key.startswith("mcp:"):
        await _sync_mcp_enabled(body.key.partition(":")[2], body.state != "off")

    result = bench_service.resolve_bench(project_id, body.conversation_id, items)
    return _view_model(items, result, body.conversation_id)


async def _sync_mcp_enabled(server_name: str, enabled: bool) -> None:
    try:
        from app.mcp.manager import get_mcp_manager
        mgr = get_mcp_manager()
        if mgr.is_initialized and mgr.server_configs.get(server_name, {}).get("enabled", True) != enabled:
            await mgr.set_server_enabled(server_name, enabled)
    except Exception as e:
        logger.warning("bench: could not sync MCP enabled state for %s: %s", server_name, e)
