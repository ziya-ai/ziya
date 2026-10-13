"""Isolated DuckDB execution for ``analyze`` (design/local-analytics.md, Engine).

One connection per call; no state survives it.  Inputs are exposed as views
over their files, and only those files (plus the call's scratch directory)
are in ``allowed_paths``/``allowed_directories``.  External access is then
disabled and the configuration locked, so the SQL cannot read, write or
fetch anything else even if it gets past the guard.

Probed on DuckDB 1.5.6 (recorded in the spec's Decisions): a view over a
file survives the lock only when the file is in ``allowed_paths``; paths
must be ``realpath``ed (macOS ``/var`` is ``/private/var``).
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .guard import GuardResult, SqlRejected, check_sql

MAX_RESULT_ROWS = 500_000  # the phase-1 ingest cap
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MEMORY_LIMIT = "2GB"
DEFAULT_THREADS = 4

_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_READERS = {
    ".parquet": "read_parquet",
    ".csv": "read_csv",
    ".tsv": "read_csv",
    ".jsonl": "read_json",
    ".ndjson": "read_json",
    ".json": "read_json",
}


class AnalyticsError(Exception):
    """A query that did not produce a result.

    ``kind`` is one of: rejected, input, binder, timeout, too_large, engine.
    ``columns`` (binder errors only) maps each referenced input to its columns,
    so the model can fix the query without a separate describe call.
    """

    def __init__(self, kind: str, message: str,
                 columns: Optional[Dict[str, List[str]]] = None):
        super().__init__(message)
        self.kind = kind
        self.columns = columns or {}


@dataclass
class QueryResult:
    columns: List[str]
    types: List[str]
    rows: List[tuple]
    elapsed_s: float
    engine_version: str
    referenced: GuardResult = field(default_factory=GuardResult)

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _reader_for(path: Path) -> str:
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        raise AnalyticsError(
            "input",
            f"Unsupported input format {path.suffix!r} for {path.name}; "
            f"supported: {', '.join(sorted(_READERS))}.",
        )
    return reader


def _view_sql(alias: str, path: Path) -> str:
    reader = _reader_for(path)
    args = _quote_str(str(path))
    if path.suffix.lower() == ".tsv":
        args += ", delim='\\t'"
    return f"CREATE VIEW {_quote_ident(alias)} AS SELECT * FROM {reader}({args})"


def run_query(
    sql: str,
    inputs: Mapping[str, Path],
    *,
    macros: Sequence[str] = (),
    macro_names: Iterable[str] = (),
    macro_table_args: Optional[Mapping[str, Sequence[int]]] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_rows: int = MAX_RESULT_ROWS,
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
    threads: int = DEFAULT_THREADS,
    skip_guard: bool = False,
) -> QueryResult:
    """Run one guarded read-only query over ``inputs`` (alias -> file path).

    ``macros`` are CREATE MACRO statements installed by the server after the
    lock; ``macro_names`` are the table macros the guard should permit.
    ``skip_guard`` exists only so tests can prove the engine layer holds on
    its own; no production caller sets it.
    """
    import duckdb

    resolved: Dict[str, Path] = {}
    for alias, raw in inputs.items():
        if not isinstance(alias, str) or not _ALIAS_RE.match(alias):
            raise AnalyticsError("input", f"Invalid input alias {alias!r}.")
        p = Path(os.path.realpath(str(raw)))
        if not p.is_file():
            raise AnalyticsError("input", f"Input {alias!r} not found: {raw}")
        _reader_for(p)
        resolved[alias] = p

    referenced = GuardResult()
    if not skip_guard:
        try:
            referenced = check_sql(sql, resolved.keys(), macro_names, macro_table_args)
        except SqlRejected as e:
            raise AnalyticsError("rejected", str(e)) from None

    scratch = Path(os.path.realpath(tempfile.mkdtemp(prefix="ziya-analyze-")))
    con = duckdb.connect(":memory:")
    timer: Optional[threading.Timer] = None
    timed_out = threading.Event()
    try:
        paths_list = "[" + ", ".join(_quote_str(str(p)) for p in resolved.values()) + "]"
        if resolved:
            con.execute(f"SET allowed_paths={paths_list}")
        con.execute(f"SET allowed_directories=[{_quote_str(str(scratch))}]")
        con.execute(f"SET temp_directory={_quote_str(str(scratch))}")
        con.execute("SET enable_external_access=false")
        con.execute("SET autoload_known_extensions=false")
        con.execute("SET autoinstall_known_extensions=false")
        con.execute(f"SET memory_limit={_quote_str(memory_limit)}")
        con.execute(f"SET threads={int(threads)}")
        # Bins of zoned timestamps must not depend on the host's zone.
        con.execute("SET TimeZone='UTC'")
        con.execute("SET lock_configuration=true")
        for alias, p in resolved.items():
            con.execute(_view_sql(alias, p))
        for m in macros:
            con.execute(m)

        def _interrupt() -> None:
            timed_out.set()
            con.interrupt()

        timer = threading.Timer(timeout_s, _interrupt)
        timer.daemon = True
        started = time.monotonic()
        timer.start()
        try:
            cur = con.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            types = [str(d[1]) for d in (cur.description or [])]
            rows = cur.fetchmany(max_rows + 1)
        except Exception as e:  # noqa: BLE001
            if timed_out.is_set():
                raise AnalyticsError(
                    "timeout", f"The query exceeded the {timeout_s:g}s limit and was stopped."
                ) from None
            raise _classify(e, con, referenced, resolved) from None
        finally:
            timer.cancel()
        elapsed = time.monotonic() - started
        if len(rows) > max_rows:
            raise AnalyticsError(
                "too_large",
                f"The result has more than {max_rows:,} rows. Aggregate further "
                f"or add a LIMIT.",
            )
        return QueryResult(columns, types, rows, elapsed, duckdb.__version__, referenced)
    finally:
        if timer is not None:
            timer.cancel()
        con.close()
        shutil.rmtree(scratch, ignore_errors=True)


def _classify(exc: Exception, con, referenced: GuardResult,
              resolved: Mapping[str, Path]) -> AnalyticsError:
    import duckdb

    msg = str(exc).strip()
    if isinstance(exc, duckdb.BinderException):
        columns: Dict[str, List[str]] = {}
        names = referenced.tables or set(resolved)
        for alias in resolved:
            if alias.lower() not in names:
                continue
            try:
                columns[alias] = [r[0] for r in con.execute(
                    f"DESCRIBE {_quote_ident(alias)}").fetchall()]
            except Exception:  # noqa: BLE001
                pass
        return AnalyticsError("binder", msg, columns)
    if isinstance(exc, duckdb.PermissionException):
        return AnalyticsError("rejected", msg)
    return AnalyticsError("engine", msg)
