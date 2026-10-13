"""The ``analyze`` tool's logic (design/local-analytics.md, Tool).

Kept out of ``app/mcp/tools/dataset_tools.py`` so the tool class stays a thin
wrapper and this module can be tested without the MCP layer.

One call: resolve inputs (dataset ids, paths, and ``ds_…`` ids named in the
SQL), run the guarded query with the primitives installed, store the result
as a new dataset with lineage, and return its describe, plus the rows when
they are small and a per-entity distribution when the result has one entity
column.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

MAX_INLINE_BYTES = 32 * 1024
MAX_DISTRIBUTION_COLUMNS = 8

_DS_IN_SQL_RE = re.compile(r"\bds_[0-9a-f]{16}\b")
# design/local-analytics.md, Decisions 3.
_ENTITY_NAME_RE = re.compile(r"(_id$|^id$|^entity|^device|^host)", re.IGNORECASE)


def _err(kind: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"error": True, "kind": kind, "message": message, **extra}


def _quote_lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _resolve_inputs(
    sql: str,
    inputs: Mapping[str, str],
    resolve_path: Callable[[str], Path],
    project_root: Optional[str],
    conversation_id: Optional[str],
) -> tuple:
    """Return ({alias: data file}, [lineage entries]).  Raises ValueError."""
    from app.utils import dataset_store as ds

    spec: Dict[str, str] = {str(k): str(v) for k, v in (inputs or {}).items()}
    for did in _DS_IN_SQL_RE.findall(sql):
        if did not in spec and ds.is_valid_dataset_id(did):
            spec[did] = did

    files: Dict[str, Path] = {}
    lineage: List[Dict[str, Any]] = []
    for alias, ref in spec.items():
        ref = ref.strip()
        if ds.is_valid_dataset_id(ref):
            try:
                ds.load_meta(ref)
            except ds.DatasetError:
                raise ValueError(f"Input {alias!r}: unknown dataset {ref}.") from None
            entry: Dict[str, Any] = {"alias": alias, "id": ref}
        else:
            try:
                path = resolve_path(ref)
            except ValueError:
                raise ValueError(f"Input {alias!r}: path {ref!r} is outside the allowed directories.") from None
            if not path.is_file():
                raise ValueError(f"Input {alias!r}: not a file: {ref}")
            # A path input becomes a dataset first, so it has an id and a
            # describe, and its lineage entry names a stored file.
            try:
                reg = ds.register_dataset(path=path, project_root=project_root,
                                          conversation_id=conversation_id)
            except ds.DatasetError as e:
                raise ValueError(f"Input {alias!r}: {e}") from None
            entry = {"alias": alias, "id": reg["id"], "path": str(path)}
        ds.ensure_parquet(entry["id"])
        files[alias] = ds.data_file(entry["id"])
        entry["sha256"] = ds.load_meta(entry["id"]).get("sha256")
        lineage.append(entry)
    return files, lineage


def _entity_column(df, explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit if explicit in df.columns else None
    candidates = [c for c in df.columns if _ENTITY_NAME_RE.search(str(c))]
    return candidates[0] if len(candidates) == 1 else None


def _metric_columns(df, entity: str) -> List[str]:
    import pandas.api.types as pt
    return [c for c in df.columns
            if c != entity and pt.is_numeric_dtype(df[c]) and not pt.is_bool_dtype(df[c])]


def _distribution(result_file: Path, df, entity: str) -> Dict[str, Any]:
    """Per-metric ``entity_distribution`` over the stored result.

    Uses the same macro the model would call, so the automatic block and a
    hand-written query cannot disagree.
    """
    from .primitives import run
    out: Dict[str, Any] = {}
    for col in _metric_columns(df, entity)[:MAX_DISTRIBUTION_COLUMNS]:
        r = run(f"SELECT * FROM entity_distribution(r, {_quote_lit(entity)}, {_quote_lit(col)})",
                {"r": result_file})
        if r.rows:
            out[col] = {k: _json_num(v) for k, v in zip(r.columns, r.rows[0])}
    return out


def _json_num(v: Any) -> Any:
    try:
        import math
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
    except Exception:  # noqa: BLE001
        pass
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            pass
    if v is not None and not isinstance(v, (int, float, str, bool)):
        try:
            return float(v)
        except (TypeError, ValueError):
            return str(v)
    return v


def run_analyze(
    *,
    sql: str,
    inputs: Optional[Mapping[str, str]] = None,
    name: Optional[str] = None,
    max_inline_rows: int = 200,
    entity: Optional[str] = None,
    resolve_path: Callable[[str], Path],
    project_root: Optional[str] = None,
    conversation_id: Optional[str] = None,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Run one ``analyze`` call; always returns a JSON-safe dict."""
    from . import INSTALL_HINT, available
    if not available():
        return _err("not_installed", INSTALL_HINT)

    import pandas as pd

    from app.utils import dataset_store as ds
    from .engine import AnalyticsError
    from .primitives import run

    if not isinstance(sql, str) or not sql.strip():
        return _err("rejected", "The query is empty.")
    try:
        files, lineage_inputs = _resolve_inputs(
            sql, inputs or {}, resolve_path, project_root, conversation_id)
    except ValueError as e:
        return _err("input", str(e))

    kw = {"timeout_s": timeout_s} if timeout_s else {}
    try:
        res = run(sql, files, **kw)
    except AnalyticsError as e:
        extra = {"columns": e.columns} if e.columns else {}
        return _err(e.kind, str(e), **extra)

    df = pd.DataFrame.from_records(res.rows, columns=res.columns)
    lineage = {
        "kind": "derivation",
        "engine": "duckdb",
        "engine_version": res.engine_version,
        "sql": sql,
        "inputs": lineage_inputs,
    }
    try:
        describe = ds.register_dataframe(
            df, name=name or "analyze result", source={"kind": "analyze"},
            project_root=project_root, conversation_id=conversation_id,
            lineage=lineage,
        )
    except ds.DatasetError as e:
        return _err("too_large", str(e))

    out: Dict[str, Any] = {**describe, "elapsed_s": round(res.elapsed_s, 3)}
    limit = max(0, int(max_inline_rows))
    if res.row_count <= limit:
        rows = ds.read_rows(describe["id"], limit=limit)["rows"]
        if len(json.dumps(rows, default=str)) <= MAX_INLINE_BYTES:
            out["rows"] = rows
    if "rows" not in out:
        out["rows_note"] = (
            f"{res.row_count:,} rows stored, not inlined. Chart from vega_data.url, "
            f"query it again with analyze (FROM {describe['id']}), or dataset_preview it."
        )

    ent = _entity_column(df, entity)
    if entity and ent is None:
        out["distribution_note"] = f"entity column {entity!r} is not in the result."
    elif ent is not None and len(df) and _metric_columns(df, ent):
        try:
            out["distribution"] = {"entity": ent,
                                   "metrics": _distribution(ds.data_file(describe["id"]), df, ent)}
        except AnalyticsError as e:
            out["distribution_note"] = f"distribution not computed: {e}"
    return out


def tool_description() -> str:
    from .primitives import DESCRIPTIONS
    lines = "\n".join(f"- {d}" for d in DESCRIPTIONS.values())
    return (
        "Run one read-only SQL query (DuckDB dialect: SELECT or WITH … SELECT) over "
        "registered datasets and data files. Address a dataset by its id "
        "(FROM ds_…) or give inputs as {alias: ds_id or file path} (Parquet, CSV, "
        "TSV, JSON, JSONL). The result is stored as a new dataset with its SQL and "
        "inputs recorded, and returned as its describe plus the rows when small. "
        "When the result has one entity column (*_id, entity*, device*, host*, or "
        "entity=…), a per-entity distribution of each numeric column comes back "
        "too: report it alongside any fleet total.\n\n"
        "Use these table macros instead of re-deriving the logic. Column arguments "
        "are quoted strings; table arguments are bare input or WITH names:\n"
        f"{lines}"
    )
