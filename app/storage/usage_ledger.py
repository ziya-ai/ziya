"""
Usage ledger: one row per provider call, in ``~/.ziya/usage.db``.

Global (not per project) because the questions it answers — "what did
September cost across everything", "which project is the expensive one" —
cross project boundaries.  Rows carry project / conversation / run
attribution so per-project views are a filter, not a separate store.

Lifecycle of a row (design/CostModel.md, "Estimate vs. actual"):

    append(status=estimate)   at dispatch, with Ziya's pre-flight count
    actualize(id, dims)       when the provider's usage block arrives

A row that is never actualized (throttled, aborted, provider sent no
usage) stays ``estimate`` forever and renders italic.  Tokens are the
durable fact; cost is computed at read time by app.cost.pricer against
the catalog entry in force at ``ts`` — rows are never re-priced in place.

Thread-safety: one connection per ledger guarded by a lock; SQLite WAL so
readers (the rollup API) do not block the streaming writer.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.utils.logging_utils import logger

LEDGER_FILENAME = "usage.db"

SOURCES = ("chat", "task", "delegate", "cli", "memory", "other")
GROUP_KEYS = ("user", "project", "conversation", "run", "model", "provider",
              "tier", "source", "day", "month", "status")


@dataclass
class UsageRecord:
    id: str
    ts: float
    provider: str
    model_id: str
    region: Optional[str]
    tier: str
    status: str                      # estimate | actual
    dims: Dict[str, int] = field(default_factory=dict)
    user: Optional[str] = None
    project_id: Optional[str] = None
    project_root: Optional[str] = None
    conversation_id: Optional[str] = None
    run_id: Optional[str] = None
    block_id: Optional[str] = None
    source: str = "chat"
    iteration: Optional[int] = None
    actualized_ts: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "ts": self.ts, "provider": self.provider, "model_id": self.model_id,
            "region": self.region, "tier": self.tier, "status": self.status, "dims": dict(self.dims),
            "user": self.user, "project_id": self.project_id, "project_root": self.project_root,
            "conversation_id": self.conversation_id, "run_id": self.run_id, "block_id": self.block_id,
            "source": self.source, "iteration": self.iteration, "actualized_ts": self.actualized_ts,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_records (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    region TEXT,
    tier TEXT NOT NULL DEFAULT 'standard',
    status TEXT NOT NULL,
    dims_json TEXT NOT NULL,
    user TEXT,
    project_id TEXT,
    project_root TEXT,
    conversation_id TEXT,
    run_id TEXT,
    block_id TEXT,
    source TEXT NOT NULL DEFAULT 'chat',
    iteration INTEGER,
    actualized_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_records(ts);
CREATE INDEX IF NOT EXISTS idx_usage_conversation ON usage_records(conversation_id);
CREATE INDEX IF NOT EXISTS idx_usage_project ON usage_records(project_id);
CREATE INDEX IF NOT EXISTS idx_usage_run ON usage_records(run_id);
"""

_COLUMNS = ("id", "ts", "provider", "model_id", "region", "tier", "status", "dims_json",
            "user", "project_id", "project_root", "conversation_id", "run_id", "block_id",
            "source", "iteration", "actualized_ts")


def _row_to_record(row: sqlite3.Row) -> UsageRecord:
    try:
        dims = json.loads(row["dims_json"]) or {}
    except (TypeError, ValueError):
        dims = {}
    return UsageRecord(
        id=row["id"], ts=row["ts"], provider=row["provider"], model_id=row["model_id"],
        region=row["region"], tier=row["tier"], status=row["status"],
        dims={str(k): int(v) for k, v in dims.items()},
        user=row["user"], project_id=row["project_id"], project_root=row["project_root"],
        conversation_id=row["conversation_id"], run_id=row["run_id"], block_id=row["block_id"],
        source=row["source"], iteration=row["iteration"], actualized_ts=row["actualized_ts"],
    )


class UsageLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._conn.executescript(_SCHEMA)

    # ── writes ──────────────────────────────────────────────────────────
    def append(self, record: UsageRecord) -> str:
        if not record.id:
            record.id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                f"INSERT INTO usage_records ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                (record.id, record.ts, record.provider, record.model_id, record.region, record.tier,
                 record.status, json.dumps(record.dims, sort_keys=True), record.user, record.project_id,
                 record.project_root, record.conversation_id, record.run_id, record.block_id,
                 record.source, record.iteration, record.actualized_ts),
            )
        return record.id

    def actualize(self, record_id: str, dims: Dict[str, int]) -> bool:
        """Replace the estimated dims with provider-reported ones and flip
        status to ``actual``.  Idempotent; returns False if no such row."""
        clean = {str(k): int(v) for k, v in dims.items() if v is not None}
        with self._lock:
            cur = self._conn.execute(
                "UPDATE usage_records SET dims_json = ?, status = 'actual', actualized_ts = ? WHERE id = ?",
                (json.dumps(clean, sort_keys=True), time.time(), record_id),
            )
            return cur.rowcount > 0

    # ── reads ───────────────────────────────────────────────────────────
    def get(self, record_id: str) -> Optional[UsageRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM usage_records WHERE id = ?", (record_id,)).fetchone()
        return _row_to_record(row) if row else None

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0])

    def query(self, *, since: Optional[float] = None, until: Optional[float] = None,
              project_id: Optional[str] = None, conversation_id: Optional[str] = None,
              run_id: Optional[str] = None, provider: Optional[str] = None,
              source: Optional[str] = None, limit: Optional[int] = None,
              newest_first: bool = False) -> List[UsageRecord]:
        clauses: List[str] = []
        params: List[Any] = []
        for col, val in (("project_id", project_id), ("conversation_id", conversation_id),
                         ("run_id", run_id), ("provider", provider), ("source", source)):
            if val is not None:
                clauses.append(f"{col} = ?")
                params.append(val)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(float(since))
        if until is not None:
            clauses.append("ts < ?")
            params.append(float(until))
        sql = "SELECT * FROM usage_records"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts " + ("DESC" if newest_first else "ASC")
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def rollup(self, by: str = "model", catalog: Any = None, plan: Any = None,
               **filters: Any) -> Dict[str, Any]:
        """Group records and price them.

        Returns ``{"by", "groups": [...], "totals": {...}, "catalog_version",
        "plan"}``.  Each group carries token sums per dimension, an
        estimate/actual record split, list and effective cost (null when
        every line is unknown), a provenance breakdown, the set of
        dimensions that went unpriced, and ``is_partial`` when any line was
        partial.  Cost is computed here, at read time — see module doc.
        """
        if by not in GROUP_KEYS:
            raise ValueError(f"unknown rollup key {by!r}; expected one of {GROUP_KEYS}")
        from app.config.pricing import get_catalog
        from app.cost.pricer import price_record
        from app.cost.rate_plans import resolve_active_plan
        catalog = catalog or get_catalog()
        plan = plan or resolve_active_plan(catalog)

        records = self.query(**filters)
        groups: Dict[str, Dict[str, Any]] = {}
        totals = _new_bucket("__total__")
        for rec in records:
            key = _group_key(rec, by)
            bucket = groups.get(key)
            if bucket is None:
                bucket = groups[key] = _new_bucket(key)
                if by == "model":
                    bucket["provider"] = rec.provider
            line = price_record(rec, catalog, plan)
            for b in (bucket, totals):
                _accumulate(b, rec, line)
        out_groups = sorted(
            (_finalize(b) for b in groups.values()),
            key=lambda g: (g["effective_cost"] is None, -(g["effective_cost"] or 0.0), g["key"]),
        )
        return {
            "by": by,
            "groups": out_groups,
            "totals": _finalize(totals),
            "catalog_version": catalog.version,
            "plan": plan.describe(),
            "record_count": len(records),
        }

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


def _group_key(rec: UsageRecord, by: str) -> str:
    if by == "user":
        return rec.user or "(unknown)"
    if by == "project":
        return rec.project_id or rec.project_root or "(none)"
    if by == "conversation":
        return rec.conversation_id or "(none)"
    if by == "run":
        return rec.run_id or "(none)"
    if by == "model":
        return f"{rec.provider}/{rec.model_id}"
    if by == "provider":
        return rec.provider
    if by == "tier":
        return rec.tier
    if by == "source":
        return rec.source
    if by == "status":
        return rec.status
    d = datetime.fromtimestamp(rec.ts, tz=timezone.utc)
    return d.strftime("%Y-%m-%d") if by == "day" else d.strftime("%Y-%m")


def _new_bucket(key: str) -> Dict[str, Any]:
    return {
        "key": key, "records": 0, "estimate_records": 0, "actual_records": 0,
        "dims": {}, "estimate_dims": {}, "actual_dims": {},
        "list_cost": 0.0, "effective_cost": 0.0, "priced_records": 0, "unknown_records": 0,
        "partial_records": 0, "provenance": {}, "unpriced_dims": set(),
        "first_ts": None, "last_ts": None,
    }


def _accumulate(b: Dict[str, Any], rec: UsageRecord, line: Any) -> None:
    b["records"] += 1
    b[f"{rec.status}_records"] = b.get(f"{rec.status}_records", 0) + 1
    for dim, n in rec.dims.items():
        if not n:
            continue
        b["dims"][dim] = b["dims"].get(dim, 0) + int(n)
        sub = b["actual_dims"] if rec.status == "actual" else b["estimate_dims"]
        sub[dim] = sub.get(dim, 0) + int(n)
    b["provenance"][line.provenance] = b["provenance"].get(line.provenance, 0) + 1
    if line.effective_cost is None:
        b["unknown_records"] += 1
    else:
        b["priced_records"] += 1
        b["effective_cost"] += line.effective_cost
        if line.list_cost is not None:
            b["list_cost"] += line.list_cost
    if line.is_partial:
        b["partial_records"] += 1
    b["unpriced_dims"].update(line.unpriced_dims)
    b["first_ts"] = rec.ts if b["first_ts"] is None else min(b["first_ts"], rec.ts)
    b["last_ts"] = rec.ts if b["last_ts"] is None else max(b["last_ts"], rec.ts)


def _finalize(b: Dict[str, Any]) -> Dict[str, Any]:
    priced = b["priced_records"] > 0
    return {
        **{k: v for k, v in b.items() if k not in ("unpriced_dims", "list_cost", "effective_cost")},
        "list_cost": round(b["list_cost"], 6) if priced else None,
        "effective_cost": round(b["effective_cost"], 6) if priced else None,
        "unpriced_dims": sorted(b["unpriced_dims"]),
        # True when any line was estimated: render the group italic.
        "has_estimates": b["estimate_records"] > 0,
        "is_partial": b["partial_records"] > 0 or b["unknown_records"] > 0,
    }


# ── singleton ───────────────────────────────────────────────────────────────
_ledger: Optional[UsageLedger] = None
_ledger_lock = threading.Lock()


def ledger_path() -> Path:
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / LEDGER_FILENAME


def get_ledger() -> UsageLedger:
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = UsageLedger(ledger_path())
        return _ledger


def reset_ledger_for_tests() -> None:
    global _ledger
    with _ledger_lock:
        if _ledger is not None:
            _ledger.close()
        _ledger = None
