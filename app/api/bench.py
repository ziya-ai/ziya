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
    via: str = "hub"                 # hub | migration | palette | api


class EnvironmentRequest(BaseModel):
    category: str
    enabled: bool


class ToolRequest(BaseModel):
    server: str
    tool: str
    enabled: bool


class ReauthorizeRequest(BaseModel):
    server: str
    force: bool = False              # human has reviewed the flagged descriptions


class SourceRequest(BaseModel):
    id: str
    kind: str                        # folder | git
    name: Optional[str] = None
    path: Optional[str] = None       # folder
    url: Optional[str] = None        # git
    ref: str = ""
    subdir: str = ""


class InstallRequest(BaseModel):
    source: str
    id: str
    layer: str = "user"              # user | project (where the files land)
    state: Optional[str] = None      # optional placement at that layer after install
    conversation_id: Optional[str] = None


def _view_model(items: List[bench_service.BenchItem], result, conversation_id: Optional[str],
                placement_meta: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    meta = {i.spec.key: i for i in items}
    placement_meta = placement_meta or {}
    out_items = []
    weight = {"always": 0, "ondemand": 0, "environment": 0}
    deltas = {layer: 0 for layer in WRITABLE_LAYERS}
    for r in result.items:
        m = meta[r.key]
        d = r.model_dump()
        d.update(name=m.name, provenance=m.provenance, tokens=m.tokens, health=m.health)
        # Per-layer writer info for every layer that holds a placement.
        d["meta"] = {layer: placement_meta.get(layer, {}).get(r.key)
                     for layer, state in r.placements.items() if state is not None}
        # A gated item keeps its placement but is not sent: placement says
        # where it sits, the gate says whether it may run.
        gate = m.health.get("gate")
        d["held"] = bool(gate)
        out_items.append(d)
        if r.tier == "environment":
            # A toggled-off category is listed but costs nothing.
            if m.health.get("enabled", True):
                weight["environment"] += m.tokens
        elif r.effective in weight and not gate:
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
def get_bench(project_id: str, conversation_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    # Plain ``def`` (Starlette worker pool), like the other handlers here that
    # await nothing: collect_items reads and decrypts every skill file, and as
    # a zero-await ``async def`` that ran on the event loop.  The stall
    # watchdog caught the loop inside SkillStorage.list under this handler
    # (2026-10-09) while two folder scans held the GIL -- the bench is fetched
    # on every conversation open, so nothing was served while it waited.
    # ``get_browse`` / ``put_place`` stay ``async`` because they await.
    # tests/test_bench_api_not_event_loop_blocking.py enforces this.
    try:
        items = bench_service.collect_items(project_id)
    except HTTPException:
        raise
    result = bench_service.resolve_bench(project_id, conversation_id, items)
    return _view_model(items, result, conversation_id, bench_service.placement_meta(project_id))


@router.get("/detail")
def get_detail(project_id: str, key: str = Query(...)) -> Dict[str, Any]:
    """Card back: description, body or tools, path.  Loaded when a card turns."""
    try:
        return bench_service.item_detail(project_id, key)
    except bench_service.ItemNotFound:
        raise HTTPException(status_code=404, detail=f"not on this bench: {key}")


@router.put("/environment")
def put_environment(project_id: str, body: EnvironmentRequest,
                          conversation_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Toggle a builtin tool category (user-wide; environment is not a lens)."""
    try:
        bench_service.set_environment_enabled(body.category, body.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown builtin category: {body.category}")
    items = bench_service.collect_items(project_id)
    result = bench_service.resolve_bench(project_id, conversation_id, items)
    vm = _view_model(items, result, conversation_id, bench_service.placement_meta(project_id))
    if body.category == bench_service.SKILLS_CATEGORY and not body.enabled:
        vm["warning"] = ("Skill discovery is off: the model can no longer load within-reach skills, "
                         "so their catalog entries are dead weight until it is re-enabled.")
    return vm


@router.put("/tool")
def put_tool(project_id: str, body: ToolRequest,
                   conversation_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Enable/disable one tool on an MCP server.

    Returns ``{"detail": ..., "view": ...}``: the drawer needs the new rows and
    every counter (card, gauge, chip bar) needs the re-costed view.  One
    round trip, so they move in the same frame.
    """
    key = f"mcp:{body.server}"
    try:
        bench_service.item_detail(project_id, key)          # 404 before writing
        bench_service.set_tool_enabled(body.server, body.tool, body.enabled)
        detail = bench_service.item_detail(project_id, key)
    except bench_service.ItemNotFound:
        raise HTTPException(status_code=404, detail=f"not on this bench: {key}")
    items = bench_service.collect_items(project_id)
    result = bench_service.resolve_bench(project_id, conversation_id, items)
    return {"detail": detail,
            "view": _view_model(items, result, conversation_id, bench_service.placement_meta(project_id))}


@router.post("/reauthorize")
def post_reauthorize(project_id: str, body: ReauthorizeRequest,
                           conversation_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Lift a rug-pull quarantine from the card's drawer.

    ``result`` is the manager's verdict: ``success``, or a refusal carrying
    ``blocking_tools`` / ``advisory_tools`` / ``can_force`` so the drawer can
    show the flagged descriptions and offer the override.  ``detail`` and
    ``view`` ride along so the gate chip, the tokens and the gauge move in
    the same frame (the put_tool pattern).
    """
    key = f"mcp:{body.server}"
    try:
        bench_service.item_detail(project_id, key)          # 404 before acting
        result = bench_service.reauthorize_mcp(body.server, body.force)
        detail = bench_service.item_detail(project_id, key)
    except bench_service.ItemNotFound:
        raise HTTPException(status_code=404, detail=f"not on this bench: {key}")
    items = bench_service.collect_items(project_id)
    resolved = bench_service.resolve_bench(project_id, conversation_id, items)
    return {"result": result, "detail": detail,
            "view": _view_model(items, resolved, conversation_id, bench_service.placement_meta(project_id))}


# -- sources / browse / install (slice 8a) -----------------------------------

def _source_dict(s) -> Dict[str, Any]:
    return {"id": s.identifier, "name": s.name, "kind": s.kind, "location": s.location}


@router.get("/sources")
def get_sources(project_id: str) -> Dict[str, Any]:
    from app.services import skill_sources as ss
    return {"sources": [_source_dict(s) for s in ss.list_sources()]}


@router.post("/sources")
def post_source(project_id: str, body: SourceRequest) -> Dict[str, Any]:
    from app.services import skill_sources as ss
    try:
        ss.add_source(body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"sources": [_source_dict(s) for s in ss.list_sources()]}


@router.delete("/sources/{source_id}")
def delete_source(project_id: str, source_id: str) -> Dict[str, Any]:
    from app.services import skill_sources as ss
    ss.remove_source(source_id)
    return {"sources": [_source_dict(s) for s in ss.list_sources()]}


@router.get("/browse")
async def get_browse(project_id: str, q: str = Query(""), source: Optional[str] = Query(None),
                     n: int = Query(8, ge=1, le=50)) -> Dict[str, Any]:
    """Find view (slice 8b).  Empty ``q``: the bench-derived panels (needs /
    updates / pairs with).  With ``q``: one column per source, top-``n`` hits
    and the source's total.  Never enumerates a catalog (services/bench_find).
    """
    from app.services import bench_find
    if not q.strip():
        return bench_find.home(project_id)
    return await bench_find.search(project_id, q.strip(), n, source)


@router.post("/install")
def post_install(project_id: str, body: InstallRequest) -> Dict[str, Any]:
    """Fetch + write + (optionally) place in one gesture."""
    from app.services import skill_sources as ss
    if body.layer not in ("user", "project"):
        raise HTTPException(status_code=400, detail="layer must be user or project")
    project_root = None
    if body.layer == "project":
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home
        p = ProjectStorage(get_ziya_home()).get(project_id)
        project_root = p.path if p else None
    try:
        rec = ss.install_skill(body.source, body.id, ss.install_root(project_root, body.layer))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"unknown source or skill: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    items = bench_service.collect_items(project_id)      # re-discovers the new skill
    if body.state:
        try:
            bench_service.place(project_id, body.conversation_id, f"skill:{rec['name']}", body.layer, body.state,
                                items, via="hub")
        except PlacementRejected as e:
            raise HTTPException(status_code=409, detail={"reason": e.reason, "key": e.key, "message": str(e)})
    result = bench_service.resolve_bench(project_id, body.conversation_id, items)
    return {"installed": rec, "missing_requires": ss.missing_requirements(rec.get("requires", [])),
            "view": _view_model(items, result, body.conversation_id, bench_service.placement_meta(project_id))}


@router.put("/place")
async def put_place(project_id: str, body: PlaceRequest) -> Dict[str, Any]:
    items = bench_service.collect_items(project_id)
    try:
        bench_service.place(project_id, body.conversation_id, body.key, body.layer, body.state, items,
                            via=body.via)
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
    return _view_model(items, result, body.conversation_id, bench_service.placement_meta(project_id))


async def _sync_mcp_enabled(server_name: str, enabled: bool) -> None:
    try:
        from app.mcp.manager import get_mcp_manager
        mgr = get_mcp_manager()
        if mgr.is_initialized and mgr.server_configs.get(server_name, {}).get("enabled", True) != enabled:
            await mgr.set_server_enabled(server_name, enabled)
    except Exception as e:
        logger.warning("bench: could not sync MCP enabled state for %s: %s", server_name, e)
