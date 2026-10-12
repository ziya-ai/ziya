"""
Read-only serving endpoint for the session dataset store.

The frontend Vega-Lite plugin resolves ``ziya://dataset/<id>`` references
by fetching ``/api/datasets/<id>/rows``.  Ids are validated against the
strict store pattern and every read goes through ``dataset_store``, which
only ever opens files inside ``~/.ziya/datasets/<id>/`` — so this endpoint
cannot be turned into a file reader, and the Vega SSRF hardening
(PenPal #83) is preserved: the only URL scheme the renderer resolves maps
to this endpoint, which serves only registered datasets.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.utils import dataset_store as ds

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _validate_id(dataset_id: str) -> str:
    if not ds.is_valid_dataset_id(dataset_id):
        raise HTTPException(status_code=400, detail="Invalid dataset id")
    return dataset_id


def _split_columns(columns: Optional[str]):
    if not columns:
        return None
    cols = [c.strip() for c in columns.split(",") if c.strip()]
    return cols or None


@router.get("")
async def list_datasets(conversation_id: Optional[str] = None):
    items = await asyncio.to_thread(ds.list_datasets, conversation_id=conversation_id)
    return {"count": len(items), "datasets": items}


@router.get("/{dataset_id}")
async def get_dataset(dataset_id: str):
    _validate_id(dataset_id)
    try:
        meta = await asyncio.to_thread(ds.load_meta, dataset_id)
    except ds.DatasetError:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    return ds.public_describe(meta)


@router.get("/{dataset_id}/rows")
async def get_rows(
    dataset_id: str,
    limit: Optional[int] = Query(None, ge=0, le=ds.MAX_SERVE_ROWS),
    offset: int = Query(0, ge=0),
    columns: Optional[str] = Query(None, description="Comma-separated column subset"),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    _validate_id(dataset_id)
    cols = _split_columns(columns)
    try:
        if format == "csv":
            text = await asyncio.to_thread(ds.rows_as_csv, dataset_id, limit=limit, columns=cols)
            return PlainTextResponse(
                text, media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{dataset_id}.csv"'},
            )
        return await asyncio.to_thread(
            ds.read_rows, dataset_id, limit=limit, offset=offset, columns=cols,
        )
    except ds.DatasetError as e:
        msg = str(e)
        if msg.startswith("Unknown dataset") or "has no data file" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.delete("/{dataset_id}")
async def delete_dataset(dataset_id: str):
    _validate_id(dataset_id)
    removed = await asyncio.to_thread(ds.drop_dataset, dataset_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    return {"success": True, "id": dataset_id}


__all__ = ["router"]
