"""
Session-level dataset store: tabular data held outside the prompt.

See design/dataset-store.md.  A dataset is registered once (from a file,
a list of records, or CSV text), stored under ``~/.ziya/datasets/<id>/``
as JSON-lines plus a ``meta.json`` describing its schema, and thereafter
referenced by handle: the model reads a compact ``describe`` and Vega-Lite
specs point at ``ziya://dataset/<id>`` instead of inlining rows.

Storage: data is written as JSON-lines (no pickle -- untrusted
deserialisation risk).  When the optional analytics engine is installed
(``ziya[data]``), each dataset is converted to ``data.parquet`` with DuckDB's
own writer (pyarrow is not needed), verified, and only then is the JSONL
removed (design/local-analytics.md, Storage format).  Readers take whichever
file exists, preferring Parquet.  Datetime columns are recorded in
``meta.json`` and restored on load.  ``meta.json`` also carries the data
file's ``sha256`` and, for derived datasets, their ``lineage``.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logging_utils import logger
from app.utils.paths import get_ziya_home

DATASET_ID_RE = re.compile(r"^ds_[0-9a-f]{16}$")
DATASET_URL_SCHEME = "ziya://dataset/"

MAX_INGEST_ROWS = 500_000
MAX_INGEST_TEXT_BYTES = 50 * 1024 * 1024  # csv_text / records payload ceiling
DEFAULT_SERVE_ROWS = 50_000
MAX_SERVE_ROWS = 100_000
MAX_PREVIEW_ROWS = 200
DESCRIBE_HEAD_ROWS = 5
DESCRIBE_SAMPLE_VALUES = 6
ORDINAL_DISTINCT_HINT = 12

TABULAR_EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json", ".jsonl", ".ndjson"}

# pandas' default NA token set includes the bare string "NA", which silently
# nulls out legitimate categorical values (North America, sodium, "not
# applicable" as a real category).  Keep the rest of the default set.
CSV_NA_VALUES = [
    "", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan",
    "1.#IND", "1.#QNAN", "<NA>", "N/A", "NULL", "NaN", "None", "n/a", "nan", "null",
]


class DatasetError(Exception):
    """User-facing dataset error (bad id, bad source, too large)."""


def is_valid_dataset_id(dataset_id: Any) -> bool:
    return isinstance(dataset_id, str) and bool(DATASET_ID_RE.match(dataset_id))


def new_dataset_id() -> str:
    return "ds_" + secrets.token_hex(8)


def dataset_url(dataset_id: str) -> str:
    return f"{DATASET_URL_SCHEME}{dataset_id}"


def parse_dataset_url(url: Any) -> Optional[str]:
    """Return the dataset id if *url* is a ``ziya://dataset/<id>`` reference."""
    if not isinstance(url, str) or not url.startswith(DATASET_URL_SCHEME):
        return None
    candidate = url[len(DATASET_URL_SCHEME):]
    return candidate if is_valid_dataset_id(candidate) else None


def store_root() -> Path:
    root = get_ziya_home() / "datasets"
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _dataset_dir(dataset_id: str) -> Path:
    if not is_valid_dataset_id(dataset_id):
        raise DatasetError(f"Invalid dataset id: {dataset_id!r}")
    return store_root() / dataset_id


# --------------------------------------------------------------------------- #
# Type inference
# --------------------------------------------------------------------------- #

def _vega_type_for(series) -> str:
    """Map a pandas Series to a Vega-Lite field type."""
    import pandas as pd
    from pandas.api import types as ptypes

    if ptypes.is_datetime64_any_dtype(series):
        return "temporal"
    if ptypes.is_bool_dtype(series):
        return "nominal"
    if ptypes.is_numeric_dtype(series):
        if ptypes.is_integer_dtype(series):
            distinct = series.dropna().nunique()
            if 0 < distinct <= ORDINAL_DISTINCT_HINT:
                vals = series.dropna().unique()
                # Small consecutive integer ranges read as ranks/levels.
                if len(vals) and (vals.max() - vals.min()) < ORDINAL_DISTINCT_HINT:
                    return "ordinal"
        return "quantitative"
    # Object column: attempt datetime parse on a sample; otherwise nominal.
    non_null = series.dropna()
    if len(non_null) and non_null.map(lambda v: isinstance(v, str)).all():
        sample = non_null.head(50)
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
        except (TypeError, ValueError):
            parsed = pd.to_datetime(sample, errors="coerce")
        if parsed.notna().mean() > 0.9 and sample.str.contains(r"\d{4}|\d{1,2}[/-]\d{1,2}", regex=True).mean() > 0.9:
            return "temporal"
    return "nominal"


def _coerce_temporal_columns(df):
    """Convert object columns that look like dates into datetime64."""
    import pandas as pd
    for col in df.columns:
        if df[col].dtype == object and _vega_type_for(df[col]) == "temporal":
            try:
                df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
            except (TypeError, ValueError):
                try:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                except (TypeError, ValueError):
                    pass
    return df


def _json_safe(value: Any) -> Any:
    import math
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        np = None
    if value is None:
        return None
    if np is not None and isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def describe_dataframe(df) -> Dict[str, Any]:
    """Compact schema summary the model can act on without seeing rows."""
    from pandas.api import types as ptypes

    columns: List[Dict[str, Any]] = []
    for col in df.columns:
        s = df[col]
        vt = _vega_type_for(s)
        info: Dict[str, Any] = {
            "name": str(col),
            "dtype": str(s.dtype),
            "vega_type": vt,
            "nulls": int(s.isna().sum()),
        }
        non_null = s.dropna()
        if vt in ("quantitative", "temporal", "ordinal") and len(non_null):
            try:
                info["min"] = _json_safe(non_null.min())
                info["max"] = _json_safe(non_null.max())
            except (TypeError, ValueError):
                pass
        if vt in ("nominal", "ordinal"):
            try:
                distinct = int(non_null.nunique())
                info["distinct"] = distinct
                if distinct <= 50:
                    info["sample"] = [_json_safe(v) for v in non_null.unique()[:DESCRIBE_SAMPLE_VALUES]]
            except TypeError:
                # Unhashable cell values (lists/dicts from JSON ingest).
                info["distinct"] = None
        elif ptypes.is_numeric_dtype(s) and len(non_null):
            info["mean"] = _json_safe(round(float(non_null.mean()), 4))
        columns.append(info)

    head_records = [
        {str(k): _json_safe(v) for k, v in row.items()}
        for row in df.head(DESCRIBE_HEAD_ROWS).to_dict(orient="records")
    ]
    return {"row_count": int(len(df)), "columns": columns, "head": head_records}


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #

def _read_path(path: Path, sheet: Optional[str] = None) -> Any:
    """Load a tabular file into a DataFrame.  Raises DatasetError."""
    import pandas as pd

    ext = path.suffix.lower()
    if ext not in TABULAR_EXTENSIONS:
        raise DatasetError(
            f"Unsupported dataset file type {ext!r}. Supported: "
            + ", ".join(sorted(TABULAR_EXTENSIONS))
        )
    try:
        if ext in (".xlsx", ".xls"):
            xl = pd.ExcelFile(path)
            if sheet is None:
                if len(xl.sheet_names) > 1:
                    raise DatasetError(
                        "Workbook has multiple sheets; pass sheet=<name>. "
                        f"Available: {xl.sheet_names}"
                    )
                sheet = xl.sheet_names[0]
            if sheet not in xl.sheet_names:
                raise DatasetError(f"Sheet {sheet!r} not found. Available: {xl.sheet_names}")
            return xl.parse(sheet_name=sheet), sheet
        if ext in (".json", ".jsonl", ".ndjson"):
            lines = ext != ".json"
            if not lines:
                text = path.read_text(encoding="utf-8", errors="replace").lstrip()
                lines = bool(text) and text[0] not in "[{"
            return pd.read_json(path, lines=lines, orient="records" if not lines else None), None
        sep = "\t" if ext == ".tsv" else None  # None -> python engine sniffs
        return pd.read_csv(
            path, sep=sep, engine="python" if sep is None else "c",
            keep_default_na=False, na_values=CSV_NA_VALUES,
        ), None
    except DatasetError:
        raise
    except Exception as e:  # pandas raises a wide variety of parser errors
        raise DatasetError(f"Could not parse {path.name}: {e}") from e


def _read_records(records: Any) -> Any:
    import pandas as pd
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise DatasetError("records must be a list of objects (one per row)")
    if len(json.dumps(records)) > MAX_INGEST_TEXT_BYTES:
        raise DatasetError("records payload too large")
    return pd.DataFrame.from_records(records)


def _read_csv_text(text: Any) -> Any:
    import pandas as pd
    if not isinstance(text, str) or not text.strip():
        raise DatasetError("csv_text must be a non-empty string")
    if len(text.encode("utf-8")) > MAX_INGEST_TEXT_BYTES:
        raise DatasetError("csv_text payload too large")
    try:
        return pd.read_csv(
            io.StringIO(text), sep=None, engine="python",
            keep_default_na=False, na_values=CSV_NA_VALUES,
        )
    except Exception as e:
        raise DatasetError(f"Could not parse csv_text: {e}") from e


def _finalize(df):
    if len(df) > MAX_INGEST_ROWS:
        raise DatasetError(
            f"Dataset has {len(df):,} rows; the ingest limit is {MAX_INGEST_ROWS:,}. "
            "Pre-aggregate or filter the source first."
        )
    df.columns = [str(c) for c in df.columns]
    return _coerce_temporal_columns(df)


def register_dataset(
    *,
    path: Optional[Path] = None,
    records: Optional[List[Dict[str, Any]]] = None,
    csv_text: Optional[str] = None,
    name: Optional[str] = None,
    sheet: Optional[str] = None,
    project_root: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Ingest one source into the store; return the describe block with id."""
    sources = [s for s in (path, records, csv_text) if s is not None]
    if len(sources) != 1:
        raise DatasetError("Provide exactly one of path, records, csv_text")

    source: Dict[str, Any]
    if path is not None:
        path = Path(path)  # accept str or Path from any caller
        df, used_sheet = _read_path(path, sheet)
        source = {"kind": "file", "path": str(path)}
        if used_sheet is not None:
            source["sheet"] = used_sheet
        default_name = path.name + (f":{used_sheet}" if used_sheet else "")
    elif records is not None:
        df = _read_records(records)
        source = {"kind": "records"}
        default_name = "records"
    else:
        df = _read_csv_text(csv_text)
        source = {"kind": "csv_text"}
        default_name = "csv_text"

    return register_dataframe(
        df, name=name or default_name, source=source,
        project_root=project_root, conversation_id=conversation_id,
    )


def register_dataframe(
    df,
    *,
    name: str,
    source: Dict[str, Any],
    project_root: Optional[str] = None,
    conversation_id: Optional[str] = None,
    lineage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Store an in-memory DataFrame as a new dataset; return its describe.

    The common write path for ingested sources and for derived results
    (``analyze``), which pass ``lineage``.
    """
    df = _finalize(df)
    dataset_id = new_dataset_id()
    ddir = _dataset_dir(dataset_id)
    ddir.mkdir(parents=True, exist_ok=False)
    try:
        os.chmod(ddir, 0o700)
    except OSError:
        pass

    datetime_cols = [c for c in df.columns if str(df[c].dtype).startswith("datetime64")]
    data_path = ddir / "data.jsonl"
    df.to_json(data_path, orient="records", lines=True, date_format="iso", force_ascii=False)

    describe = describe_dataframe(df)
    meta = {
        "id": dataset_id,
        "name": name,
        "created_at": time.time(),
        "source": source,
        "project_root": project_root,
        "conversation_id": conversation_id,
        "datetime_columns": datetime_cols,
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "storage": "jsonl",
        "sha256": _file_sha256(data_path),
        **({"lineage": lineage} if lineage else {}),
        **describe,
    }
    _write_meta(dataset_id, meta)
    logger.info(f"dataset_store: registered {dataset_id} ({meta['name']}, {meta['row_count']} rows)")
    try:
        from app.utils.analytics import available
        if available():
            ensure_parquet(dataset_id)
            meta = load_meta(dataset_id)
    except Exception as e:  # noqa: BLE001 -- the JSONL copy is complete on its own
        logger.warning(f"dataset_store: parquet conversion of {dataset_id} skipped: {e}")
    return public_describe(meta)


# --------------------------------------------------------------------------- #
# Storage files
# --------------------------------------------------------------------------- #

_migrate_locks: Dict[str, threading.Lock] = {}
_migrate_locks_guard = threading.Lock()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_meta(dataset_id: str, meta: Dict[str, Any]) -> None:
    path = _dataset_dir(dataset_id) / "meta.json"
    tmp = path.with_name("meta.json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def data_file(dataset_id: str) -> Path:
    """The dataset's current data file: Parquet if present, else JSONL."""
    ddir = _dataset_dir(dataset_id)
    for fname in ("data.parquet", "data.jsonl"):
        p = ddir / fname
        if p.is_file():
            return p
    raise DatasetError(f"Dataset {dataset_id} has no data file")


def _parquet_matches(path: Path, row_count: int, columns: List[str]) -> bool:
    """Read the written Parquet back and compare row count and column order."""
    import duckdb
    con = duckdb.connect(":memory:")
    try:
        lit = "'" + str(path).replace("'", "''") + "'"
        n = con.execute(f"SELECT count(*) FROM read_parquet({lit})").fetchone()[0]
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({lit})").fetchall()]
    finally:
        con.close()
    return n == row_count and cols == list(columns)


def _has_nested(df) -> bool:
    """True when some column holds JSON objects or arrays (dicts / lists)."""
    for c in df.columns:
        if str(df[c].dtype) != "object":
            continue
        for v in df[c].head(50):
            if isinstance(v, (dict, list)):
                return True
    return False


def ensure_parquet(dataset_id: str) -> Path:
    """Convert a JSONL dataset to Parquet, verified; return the data file.

    The JSONL is removed only after the Parquet file reads back with the
    same row count and columns.  If verification fails, the Parquet file is
    discarded and the JSONL path is returned unchanged.  Without DuckDB this
    is a no-op.
    """
    ddir = _dataset_dir(dataset_id)
    pq, jl = ddir / "data.parquet", ddir / "data.jsonl"
    if pq.is_file():
        return pq
    from app.utils.analytics import available
    if not available():
        return data_file(dataset_id)
    with _migrate_locks_guard:
        lock = _migrate_locks.setdefault(dataset_id, threading.Lock())
    with lock:
        if pq.is_file():
            return pq
        import duckdb
        df, meta = load_dataframe(dataset_id)
        tmp = ddir / "data.parquet.tmp"
        con = duckdb.connect(":memory:")
        try:
            lit = "'" + str(tmp).replace("'", "''") + "'"
            src = "'" + str(jl).replace("'", "''") + "'"
            cols = ", ".join('"' + str(c).replace('"', '""') + '"' for c in df.columns)
            # Convert from the JSONL with DuckDB's reader, not from the
            # pandas frame: a nested JSON object is a Python dict in pandas
            # and registers as a string, so ``data.verdict`` on a stored
            # finding (app/utils/output_store.py) failed to bind.  read_json
            # keeps it a STRUCT.  Columns are selected in the frame's order
            # so the verification below still holds.
            # Only when a column actually holds objects: for flat data the
            # frame's types are the contract (a date-like string stays a
            # string; read_json would promote it to TIMESTAMP and change
            # what rows read back as).
            if _has_nested(df):
                try:
                    con.execute(
                        f"COPY (SELECT {cols} FROM read_json({src}, "
                        f"format='newline_delimited')) TO {lit} (FORMAT parquet)")
                except duckdb.Error as e:
                    logger.debug(f"dataset_store: read_json conversion fell back: {e}")
                    con.register("__src", df)
                    con.execute(f"COPY __src TO {lit} (FORMAT parquet)")
            else:
                con.register("__src", df)
                con.execute(f"COPY __src TO {lit} (FORMAT parquet)")
        finally:
            con.close()
        if not _parquet_matches(tmp, len(df), list(df.columns)):
            tmp.unlink(missing_ok=True)
            logger.warning(f"dataset_store: parquet verification failed for {dataset_id}; keeping JSONL")
            return jl
        os.replace(tmp, pq)
        meta["storage"] = "parquet"
        meta["sha256"] = _file_sha256(pq)
        _write_meta(dataset_id, meta)
        jl.unlink(missing_ok=True)
        logger.info(f"dataset_store: {dataset_id} converted to parquet")
        return pq


def public_describe(meta: Dict[str, Any]) -> Dict[str, Any]:
    """The subset of meta returned to the model / API."""
    return {
        "id": meta["id"],
        "name": meta.get("name"),
        "row_count": meta.get("row_count"),
        "columns": meta.get("columns", []),
        "head": meta.get("head", []),
        "source": meta.get("source"),
        "conversation_id": meta.get("conversation_id"),
        "sha256": meta.get("sha256"),
        **({"lineage": meta["lineage"]} if meta.get("lineage") else {}),
        "vega_data": {"url": dataset_url(meta["id"])},
    }


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #

def load_meta(dataset_id: str) -> Dict[str, Any]:
    ddir = _dataset_dir(dataset_id)
    meta_path = ddir / "meta.json"
    if not meta_path.is_file():
        raise DatasetError(f"Unknown dataset: {dataset_id}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def load_dataframe(dataset_id: str):
    import pandas as pd
    meta = load_meta(dataset_id)
    data_path = data_file(dataset_id)
    if data_path.suffix == ".parquet":
        import duckdb
        con = duckdb.connect(":memory:")
        try:
            lit = "'" + str(data_path).replace("'", "''") + "'"
            df = con.execute(f"SELECT * FROM read_parquet({lit})").df()
        finally:
            con.close()
        return df, meta
    if data_path.stat().st_size == 0:
        return pd.DataFrame(columns=list(meta.get("dtypes", {}).keys())), meta
    df = pd.read_json(data_path, lines=True, dtype=False, convert_dates=False)
    for col in meta.get("datetime_columns", []):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df, meta


def read_rows(
    dataset_id: str,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
    columns: Optional[List[str]] = None,
    hard_max: int = MAX_SERVE_ROWS,
) -> Dict[str, Any]:
    """Return ``{rows, row_count, offset, limit, truncated, columns}``."""
    df, meta = load_dataframe(dataset_id)
    total = len(df)
    offset = max(0, int(offset or 0))
    if limit is None:
        limit = DEFAULT_SERVE_ROWS
    limit = max(0, min(int(limit), hard_max))
    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise DatasetError(f"Unknown columns: {missing}. Available: {list(df.columns)}")
        df = df[columns]
    window = df.iloc[offset: offset + limit]
    rows = [
        {k: _json_safe(v) for k, v in row.items()}
        for row in window.to_dict(orient="records")
    ]
    return {
        "id": dataset_id,
        "name": meta.get("name"),
        "row_count": total,
        "offset": offset,
        "limit": limit,
        "returned": len(rows),
        "truncated": offset + len(rows) < total,
        "columns": list(df.columns),
        "rows": rows,
    }


def rows_as_csv(dataset_id: str, *, limit: Optional[int] = None, columns: Optional[List[str]] = None) -> str:
    df, _ = load_dataframe(dataset_id)
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    if limit is not None:
        df = df.head(min(int(limit), MAX_SERVE_ROWS))
    return df.to_csv(index=False)


def list_datasets(conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    root = store_root()
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or not is_valid_dataset_id(entry.name):
            continue
        try:
            meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if conversation_id and meta.get("conversation_id") not in (None, conversation_id):
            continue
        out.append({
            "id": meta["id"],
            "name": meta.get("name"),
            "row_count": meta.get("row_count"),
            "columns": [c["name"] for c in meta.get("columns", [])],
            "source": meta.get("source"),
            "conversation_id": meta.get("conversation_id"),
            "created_at": meta.get("created_at"),
        })
    out.sort(key=lambda m: m.get("created_at") or 0, reverse=True)
    return out


def drop_dataset(dataset_id: str) -> bool:
    ddir = _dataset_dir(dataset_id)
    if not ddir.is_dir():
        return False
    shutil.rmtree(ddir, ignore_errors=True)
    return True
