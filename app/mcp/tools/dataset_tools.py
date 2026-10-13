"""
Builtin ``dataset_*`` tools: tabular data by reference.

``dataset_register`` ingests a CSV/xlsx/JSON file (or inline records /
CSV text) into the session dataset store and returns a compact schema
summary plus a ``ziya://dataset/<id>`` handle.  Vega-Lite specs reference
the handle via ``data: {"url": "ziya://dataset/<id>"}``; the frontend
resolves it to inline rows at render time (see design/dataset-store.md),
so the model never transcribes data into a spec and large files never
enter the prompt.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.fileio import (
    _resolve_and_validate, _get_project_root, _get_all_readable_prefixes,
)
from app.utils.logging_utils import logger


def _conversation_id() -> Optional[str]:
    try:
        from app.context import get_conversation_id_or_none
        return get_conversation_id_or_none()
    except Exception:
        return None


def _err(message: str) -> Dict[str, Any]:
    return {"error": True, "message": message}


# --------------------------------------------------------------------------- #
# dataset_register
# --------------------------------------------------------------------------- #

class DatasetRegisterInput(BaseModel):
    path: Optional[str] = Field(
        None,
        description="Tabular file to ingest (project-relative or absolute): .csv .tsv .xlsx .xls .json .jsonl",
    )
    records: Optional[List[Dict[str, Any]]] = Field(
        None, description="Inline rows (list of objects) — e.g. a tool result you want to chart.",
    )
    csv_text: Optional[str] = Field(None, description="Inline CSV/TSV text (delimiter sniffed).")
    name: Optional[str] = Field(None, description="Display name (defaults to file name).")
    sheet: Optional[str] = Field(None, description="Workbook sheet name; required when the workbook has several.")


class DatasetRegisterTool(BaseMCPTool):
    name: str = "dataset_register"
    description: str = (
        "Load tabular data (CSV/TSV/xlsx/JSON file, inline records, or CSV text) into "
        "the session dataset store. Returns row count, per-column types "
        "(vega_type: quantitative/temporal/nominal/ordinal — trust these), null counts, "
        "ranges, a 5-row head, and vega_data: {url: 'ziya://dataset/<id>'}. Put that "
        "object in a Vega-Lite spec's `data` field instead of inlining values, and use "
        "Vega-Lite `transform` (aggregate/bin/filter/window) for computation. Never "
        "hand-copy numbers out of a file when you can register it."
    )
    InputSchema = DatasetRegisterInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils import dataset_store as ds
        project_root = _get_project_root(kwargs)
        path_arg = (kwargs.get("path") or "").strip().strip("'\"") or None
        records = kwargs.get("records")
        csv_text = kwargs.get("csv_text")
        resolved = None
        if path_arg:
            try:
                resolved = _resolve_and_validate(
                    path_arg, project_root,
                    allowed_absolute_prefixes=_get_all_readable_prefixes(),
                )
            except ValueError:
                return _err(f"Path '{path_arg}' is outside the allowed directories.")
            if not resolved.is_file():
                return _err(f"Not a file: {path_arg}")
        try:
            # pandas parsing of a large file is CPU-bound; keep it off the loop.
            return await asyncio.to_thread(
                ds.register_dataset,
                path=resolved,
                records=records,
                csv_text=csv_text,
                name=kwargs.get("name"),
                sheet=kwargs.get("sheet"),
                project_root=project_root,
                conversation_id=_conversation_id(),
            )
        except ds.DatasetError as e:
            return _err(str(e))
        except Exception as e:  # pandas/openpyxl surface many error types
            logger.warning(f"dataset_register failed: {e}")
            return _err(f"Could not register dataset: {e}")


# --------------------------------------------------------------------------- #
# dataset_describe
# --------------------------------------------------------------------------- #

class DatasetIdInput(BaseModel):
    id: str = Field(..., description="Dataset id (ds_…) from dataset_register or dataset_list.")


class DatasetDescribeTool(BaseMCPTool):
    name: str = "dataset_describe"
    description: str = (
        "Schema summary for a registered dataset: columns with vega_type, nulls, "
        "ranges/samples, row count, 5-row head, and its ziya://dataset URL."
    )
    InputSchema = DatasetIdInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils import dataset_store as ds
        _get_project_root(kwargs)  # strip routing metadata
        try:
            return ds.public_describe(ds.load_meta(kwargs.get("id", "")))
        except ds.DatasetError as e:
            return _err(str(e))


# --------------------------------------------------------------------------- #
# dataset_preview
# --------------------------------------------------------------------------- #

class DatasetPreviewInput(BaseModel):
    id: str = Field(..., description="Dataset id (ds_…).")
    limit: int = Field(20, ge=1, le=200, description="Rows to return (max 200).")
    offset: int = Field(0, ge=0, description="Row offset.")
    columns: Optional[List[str]] = Field(None, description="Subset of columns.")


class DatasetPreviewTool(BaseMCPTool):
    name: str = "dataset_preview"
    description: str = (
        "Return up to 200 rows of a dataset for spot-checking values or column "
        "formats. Not for reading a whole file into context — chart from the "
        "ziya://dataset URL and compute with Vega-Lite transforms instead."
    )
    InputSchema = DatasetPreviewInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils import dataset_store as ds
        _get_project_root(kwargs)
        try:
            limit = min(int(kwargs.get("limit") or 20), ds.MAX_PREVIEW_ROWS)
            return await asyncio.to_thread(
                ds.read_rows,
                kwargs.get("id", ""),
                limit=limit,
                offset=int(kwargs.get("offset") or 0),
                columns=kwargs.get("columns"),
                hard_max=ds.MAX_PREVIEW_ROWS,
            )
        except ds.DatasetError as e:
            return _err(str(e))
        except (TypeError, ValueError) as e:
            return _err(f"Bad arguments: {e}")


# --------------------------------------------------------------------------- #
# dataset_list
# --------------------------------------------------------------------------- #

class DatasetListInput(BaseModel):
    all_conversations: bool = Field(
        False, description="Include datasets registered by other conversations.",
    )


class DatasetListTool(BaseMCPTool):
    name: str = "dataset_list"
    description: str = (
        "List registered datasets (id, name, row count, columns, source). "
        "Defaults to this conversation's datasets plus unscoped ones."
    )
    InputSchema = DatasetListInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils import dataset_store as ds
        _get_project_root(kwargs)
        conv = None if kwargs.get("all_conversations") else _conversation_id()
        items = await asyncio.to_thread(ds.list_datasets, conversation_id=conv)
        for item in items:
            item["vega_data"] = {"url": ds.dataset_url(item["id"])}
        return {"count": len(items), "datasets": items}


# --------------------------------------------------------------------------- #
# analyze (design/local-analytics.md)
# --------------------------------------------------------------------------- #

def _analyze_description() -> str:
    from app.utils.analytics.analyze import tool_description
    return tool_description()


class AnalyzeInput(BaseModel):
    sql: str = Field(..., description="One read-only SELECT (or WITH ... SELECT), DuckDB dialect.")
    inputs: Optional[Dict[str, str]] = Field(
        None,
        description="{alias: dataset id (ds_...) or file path}. Datasets named in the SQL by id need no entry.",
    )
    name: Optional[str] = Field(None, description="Name for the result dataset.")
    max_inline_rows: int = Field(200, ge=0, le=1000, description="Return rows inline up to this many (and 32 KB).")
    entity: Optional[str] = Field(
        None, description="Entity column for the distribution block; overrides the name heuristic.",
    )


class AnalyzeTool(BaseMCPTool):
    name: str = "analyze"
    description: str = _analyze_description()
    InputSchema = AnalyzeInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils.analytics.analyze import run_analyze
        project_root = _get_project_root(kwargs)

        def resolve(p: str):
            return _resolve_and_validate(
                p.strip().strip("'\""), project_root,
                allowed_absolute_prefixes=_get_all_readable_prefixes(),
            )

        try:
            # DuckDB and pandas work is CPU-bound; keep it off the loop.
            return await asyncio.to_thread(
                run_analyze,
                sql=kwargs.get("sql") or "",
                inputs=kwargs.get("inputs") or {},
                name=kwargs.get("name"),
                max_inline_rows=int(kwargs.get("max_inline_rows", 200) or 0),
                entity=kwargs.get("entity"),
                resolve_path=resolve,
                project_root=project_root,
                conversation_id=_conversation_id(),
            )
        except Exception as e:  # noqa: BLE001 -- the model gets the error, not a crash
            logger.warning(f"analyze failed: {e}")
            return _err(f"analyze failed: {e}")


__all__ = [
    "DatasetRegisterTool",
    "DatasetDescribeTool",
    "DatasetPreviewTool",
    "DatasetListTool",
    "AnalyzeTool",
]
