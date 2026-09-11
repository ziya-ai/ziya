"""
Cost accounting API.

    GET /api/usage/summary                 catalog version, active plan, ledger stats
    GET /api/usage/rollup?by=...           grouped tokens + cost (see UsageLedger.rollup)
    GET /api/usage/records?...             raw rows with a CostLine each
    GET /api/usage/catalog                 effective catalog entries (for inspection)

Filters accepted by rollup/records: since, until (unix seconds or ISO
date), project_id, conversation_id, run_id, provider, source.

Everything is computed at read time from the ledger + catalog + plan; no
cost is ever stored.  design/CostModel.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/usage", tags=["usage"])


def _parse_ts(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        d = datetime.fromisoformat(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"bad timestamp {value!r}; use unix seconds or ISO 8601")


def _filters(since, until, project_id, conversation_id, run_id, provider, source) -> Dict[str, Any]:
    return {
        "since": _parse_ts(since), "until": _parse_ts(until),
        "project_id": project_id, "conversation_id": conversation_id,
        "run_id": run_id, "provider": provider, "source": source,
    }


@router.get("/summary")
def usage_summary():
    from app.config.pricing import get_catalog
    from app.cost.rate_plans import resolve_active_plan
    from app.storage.usage_ledger import get_ledger, ledger_path
    catalog = get_catalog()
    plan = resolve_active_plan(catalog)
    ledger = get_ledger()
    return {
        "catalog": catalog.describe(),
        "plan": plan.describe(),
        "ledger": {"path": str(ledger_path()), "records": ledger.count()},
    }


@router.get("/rollup")
def usage_rollup(
    by: str = Query("model"),
    since: Optional[str] = None, until: Optional[str] = None,
    project_id: Optional[str] = None, conversation_id: Optional[str] = None,
    run_id: Optional[str] = None, provider: Optional[str] = None,
    source: Optional[str] = None,
):
    from app.storage.usage_ledger import get_ledger
    try:
        return get_ledger().rollup(
            by=by, **_filters(since, until, project_id, conversation_id, run_id, provider, source))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"usage rollup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="usage rollup failed")


@router.get("/records")
def usage_records(
    since: Optional[str] = None, until: Optional[str] = None,
    project_id: Optional[str] = None, conversation_id: Optional[str] = None,
    run_id: Optional[str] = None, provider: Optional[str] = None,
    source: Optional[str] = None, limit: int = Query(200, ge=1, le=5000),
):
    from app.config.pricing import get_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import resolve_active_plan
    from app.storage.usage_ledger import get_ledger
    catalog = get_catalog()
    plan = resolve_active_plan(catalog)
    records = get_ledger().query(
        limit=limit, newest_first=True,
        **_filters(since, until, project_id, conversation_id, run_id, provider, source))
    return {
        "catalog_version": catalog.version,
        "plan": plan.describe(),
        "records": [
            {**r.to_dict(), "cost": price_record(r, catalog, plan).to_dict()} for r in records
        ],
    }


@router.get("/catalog")
def usage_catalog(provider: Optional[str] = None):
    from app.config.pricing import get_catalog
    catalog = get_catalog()
    entries = [e.to_dict() for e in catalog.entries if provider is None or e.provider == provider]
    return {**catalog.describe(), "entries": entries}
