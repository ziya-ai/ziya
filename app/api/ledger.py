"""``/api/ledger`` — the evidence ledger over HTTP (design/evidence-ledger.md, step 2).

The one caller is a process the shell MCP server spawned: ``ziya ledger …``
or the stdlib helper in ``app/utils/ziya_ledger_client.py``.  Every request
carries the scope token the shell server put in its environment
(``X-Ziya-Ledger-Scope``).  The token names the project (which ledger) and
the provenance stamped on each record; the request body never does.  No
token, or a bad or expired one, is 401.

Thin wrappers over ``app/utils/evidence/service.py``, the same logic the
``ledger_*`` builtin tools use, so a claim recorded from a shell script and
one recorded by the model are indistinguishable except for ``provenance.via``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/ledger", tags=["ledger"])


class RecordBody(BaseModel):
    observation: Optional[Dict[str, Any]] = None
    claim: Optional[Dict[str, Any]] = None


class QueryBody(BaseModel):
    ref: Optional[str] = None
    prefix: Optional[str] = None
    namespace: Optional[str] = None
    history: bool = False


class VerifyBody(BaseModel):
    refs: Optional[List[str]] = None
    prefix: Optional[str] = None
    method: str = "rehash"


def _scope(token: Optional[str]):
    from app.utils.evidence import scope
    payload = scope.verify(token)
    if payload is None:
        return None, JSONResponse(
            status_code=401,
            content={"error": True, "message":
                     f"Missing, invalid or expired ledger scope token ({scope.HEADER}). "
                     f"Run from a shell the Ziya shell server started, which sets "
                     f"{scope.ENV_URL} and {scope.ENV_SCOPE}."},
        )
    return payload, None


def _resolver(project_root: str):
    from app.mcp.tools.fileio import _resolve_and_validate, _get_all_readable_prefixes

    def resolve(p: str):
        return _resolve_and_validate(
            p.strip().strip("'\""), project_root,
            allowed_absolute_prefixes=_get_all_readable_prefixes(),
        )
    return resolve


@router.post("/record")
async def ledger_record(body: RecordBody, x_ziya_ledger_scope: Optional[str] = Header(None)):
    payload, err = _scope(x_ziya_ledger_scope)
    if err:
        return err
    from app.utils.evidence import scope, service
    root = payload["project_root"]
    try:
        out = await asyncio.to_thread(
            service.record, project_root=root, resolve_path=_resolver(root),
            observation=body.observation, claim=body.claim,
            provenance=scope.provenance_from_scope(payload),
            namespace=payload.get("namespace"),
        )
    except Exception as e:  # noqa: BLE001 -- the caller gets the error, not a 500 page
        logger.warning(f"/api/ledger/record failed: {e}")
        return JSONResponse(status_code=400, content={"error": True, "message": str(e)})
    return JSONResponse(status_code=400 if out.get("error") else 200, content=out)


@router.post("/query")
async def ledger_query(body: QueryBody, x_ziya_ledger_scope: Optional[str] = Header(None)):
    payload, err = _scope(x_ziya_ledger_scope)
    if err:
        return err
    from app.utils.evidence import service
    out = await asyncio.to_thread(
        service.query, project_root=payload["project_root"], ref=body.ref,
        prefix=body.prefix, namespace=body.namespace, history=body.history,
    )
    return JSONResponse(status_code=404 if out.get("error") else 200, content=out)


@router.post("/verify")
async def ledger_verify(body: VerifyBody, x_ziya_ledger_scope: Optional[str] = Header(None)):
    payload, err = _scope(x_ziya_ledger_scope)
    if err:
        return err
    from app.utils.evidence import scope, service
    try:
        out = await asyncio.to_thread(
            service.verify, project_root=payload["project_root"], method=body.method,
            refs=body.refs, prefix=body.prefix,
            provenance=scope.provenance_from_scope(payload),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"/api/ledger/verify failed: {e}")
        return JSONResponse(status_code=400, content={"error": True, "message": str(e)})
    return JSONResponse(status_code=400 if out.get("error") else 200, content=out)
