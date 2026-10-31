"""
Resource accounting for one Task Card run.

A run's spend is scattered across three stores, none of which the tile
reads whole: the usage ledger (one row per provider call, keyed by run_id
and block_id), the run record (wall-clock per block, tool calls, the
cpu/files figures the executor stamps on each Artifact), and the disk the
run left behind (its own record directory under the project's data dir
and its scratchpad under ``<project>/.ziya/task-runs/<run_id>``).  This
module is the one place that joins them, so the tile, the inspector and
the Info tab all show the same numbers.

Two measurements are taken at WRITE time by task_executor and only read
here:

* ``cpu_children_ms`` — ``getrusage(RUSAGE_CHILDREN)`` user+system time,
  sampled before and after a task body.  It counts the CPU of every child
  this process has reaped in the window (shell commands and the scripts
  they run), which is the tool-side CPU a task caused.  It cannot see the
  model's CPU, and in a Parallel fan-out concurrent tasks share one
  process, so each task's delta includes whatever its siblings' children
  used meanwhile; the run total therefore over-counts under parallelism
  and is labelled as such.
* ``WriteTracker`` — the paths the model named to the file-write tool.
  Shell redirections and scripts that write are not visible to it; the
  declared ``file`` artifact parts cover some of that gap.  Sizes are
  taken at the end of the task, so a file written and then deleted shows
  ``exists: false``.

Everything here is best-effort: a missing ledger row, a stat() failure or
an unreadable run directory yields a smaller number, never an exception
to the caller.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.utils.logging_utils import logger

# Tool names whose ``path`` argument is a file the model wrote.
WRITE_TOOL_NAMES = frozenset({"file_write", "mcp_file_write"})


def cpu_children_ms() -> int:
    """User+system CPU, in ms, of children this process has waited for.

    Monotone for the life of the process, so two samples bracket the
    children reaped between them.  0 where ``resource`` is unavailable.
    """
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_CHILDREN)
        return int((ru.ru_utime + ru.ru_stime) * 1000)
    except Exception:  # noqa: BLE001 - Windows, or a restricted runtime
        return 0


class WriteTracker:
    """Collects file paths named to write tools during one task body."""

    def __init__(self) -> None:
        self._paths: List[str] = []

    def note(self, tool_name: Optional[str], args: Any) -> None:
        if tool_name not in WRITE_TOOL_NAMES or not isinstance(args, dict):
            return
        path = args.get("path")
        if isinstance(path, str) and path and path not in self._paths:
            self._paths.append(path)

    @property
    def paths(self) -> List[str]:
        return list(self._paths)

    def snapshot(self, root: Optional[str]) -> List[Dict[str, Any]]:
        """``[{path, bytes, exists}]`` for every noted path, sized now.

        ``path`` is kept as the model gave it (relative to the task's cwd
        when relative); ``bytes`` is 0 when the file is gone or unreadable.
        """
        out: List[Dict[str, Any]] = []
        for p in self._paths:
            full = Path(p) if os.path.isabs(p) else Path(root or ".") / p
            try:
                st = full.stat()
                out.append({"path": p, "bytes": int(st.st_size), "exists": True})
            except OSError:
                out.append({"path": p, "bytes": 0, "exists": False})
        return out


def dir_bytes(path: Optional[Path]) -> int:
    """Total size of the regular files under ``path`` (0 when absent)."""
    if not path:
        return 0
    total = 0
    try:
        if path.is_file():
            return int(path.stat().st_size)
        for dirpath, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.stat(os.path.join(dirpath, f)).st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def run_disk_usage(run_id: str, project_dir: Optional[Path],
                   project_root: Optional[str]) -> Dict[str, Any]:
    """Bytes the run occupies on disk, by location.

    ``record_bytes``: the run file plus its iterations/artifacts/events
    directory under the project's data dir (``~/.ziya/projects/<id>/
    task_runs/``).  ``scratch_bytes``: the shared run scratchpad under the
    project root, which outlives the run and is what the user sees in the
    repository.
    """
    record = 0
    scratch = 0
    record_path = None
    scratch_path = None
    if project_dir:
        runs_dir = Path(project_dir) / "task_runs"
        record_path = runs_dir / run_id
        record = dir_bytes(runs_dir / f"{run_id}.json") + dir_bytes(record_path)
    if project_root:
        scratch_path = Path(project_root) / ".ziya" / "task-runs" / run_id
        scratch = dir_bytes(scratch_path)
    return {
        "record_bytes": record,
        "record_path": str(record_path) if record_path else None,
        "scratch_bytes": scratch,
        "scratch_path": str(scratch_path) if scratch_path and scratch else None,
    }


# ── read-side join ──────────────────────────────────────────────────────

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _block_names(snapshot: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """block_id -> name from a run's card_snapshot tree."""
    names: Dict[str, str] = {}

    def walk(block: Any) -> None:
        if not isinstance(block, dict):
            return
        bid = block.get("id")
        if bid:
            names[bid] = block.get("name") or block.get("block_type") or bid
        for child in block.get("body") or []:
            walk(child)

    if snapshot:
        walk(snapshot.get("root"))
    return names


def _ms_between(start: Any, end: Any, now_ms: int) -> Optional[int]:
    if not start:
        return None
    return max(0, int((end or now_ms) - start))


def _model_time_ms(records: Iterable[Any]) -> int:
    """Sum of (actualized_ts - ts) over actualized rows: how long the
    provider calls themselves took, as distinct from wall clock."""
    total = 0.0
    for r in records:
        ts = _get(r, "ts")
        done = _get(r, "actualized_ts")
        if ts and done and done >= ts:
            total += done - ts
    return int(total * 1000)


def _merge_files(artifacts: Iterable[Any]) -> List[Dict[str, Any]]:
    """Union of files_written across leaf artifacts; the last report of a
    path wins, since a later task may have rewritten it."""
    by_path: Dict[str, Dict[str, Any]] = {}
    for art in artifacts:
        for f in (_get(art, "files_written", None) or []):
            if isinstance(f, dict) and f.get("path"):
                by_path[f["path"]] = dict(f)
    return list(by_path.values())


def build_run_resources(run: Any, *, project_id: Optional[str] = None,
                        project_root: Optional[str] = None,
                        project_dir: Optional[Path] = None,
                        card_name: Optional[str] = None,
                        ledger: Any = None, now: Optional[float] = None) -> Dict[str, Any]:
    """The run's resource report.  See the module docstring for sources.

    ``ledger`` defaults to the process ledger; tests pass a stub exposing
    ``rollup(by=..., run_id=...)`` and ``query(run_id=...)``.
    """
    now_ms = int((now or time.time()) * 1000)
    run_id = _get(run, "id") or ""
    if ledger is None:
        try:
            from app.storage.usage_ledger import get_ledger
            ledger = get_ledger()
        except Exception as e:  # noqa: BLE001
            logger.debug("run_resources: ledger unavailable: %s", e)

    by_block: Dict[str, Dict[str, Any]] = {}
    totals: Optional[Dict[str, Any]] = None
    tiers: List[Dict[str, Any]] = []
    model_time = 0
    if ledger is not None and run_id:
        try:
            r = ledger.rollup(by="block", run_id=run_id)
            totals = r.get("totals")
            by_block = {g["key"]: g for g in r.get("groups", [])}
            tiers = [{"tier": g["key"], "records": g["records"]}
                     for g in ledger.rollup(by="tier", run_id=run_id).get("groups", [])]
            model_time = _model_time_ms(ledger.query(run_id=run_id))
        except Exception as e:  # noqa: BLE001
            logger.debug("run_resources: ledger read failed for %s: %s", run_id, e)

    names = _block_names(_get(run, "card_snapshot"))
    states = _get(run, "block_states", {}) or {}
    blocks: List[Dict[str, Any]] = []
    leaf_artifacts: List[Any] = []
    cpu_total = 0
    tool_calls_total = 0
    for bid, st in states.items():
        art = _get(st, "artifact")
        btype = _get(st, "block_type", "")
        is_leaf = btype == "task"
        if is_leaf and art is not None:
            leaf_artifacts.append(art)
            cpu = _get(art, "cpu_ms", None)
            if isinstance(cpu, int):
                cpu_total += cpu
            tool_calls_total += int(_get(art, "tool_calls", 0) or 0)
        ledger_g = by_block.get(bid) or {}
        iters = _get(st, "iteration_summaries", []) or []
        blocks.append({
            "block_id": bid,
            "name": names.get(bid, bid),
            "block_type": btype,
            "status": _get(st, "status", "queued"),
            "wall_ms": _ms_between(_get(st, "started_at"), _get(st, "completed_at"), now_ms),
            "tool_calls": int(_get(art, "tool_calls", 0) or 0) if art is not None else None,
            "cpu_ms": _get(art, "cpu_ms", None) if art is not None else None,
            "model": _get(art, "model", None) if art is not None else None,
            "files_written": list(_get(art, "files_written", None) or []) if art is not None else [],
            "iterations": len(iters),
            "turns": ledger_g.get("records", 0),
            "dims": ledger_g.get("dims", {}),
            "effective_cost": ledger_g.get("effective_cost"),
            "has_estimates": bool(ledger_g.get("has_estimates")),
        })
    # Deterministic order: by start time, then id, so the table reads in
    # execution order rather than dict order.
    blocks.sort(key=lambda b: (_get(states.get(b["block_id"]), "started_at", None) or 0, b["block_id"]))

    started = _get(run, "started_at")
    completed = _get(run, "completed_at")
    files = _merge_files(leaf_artifacts)
    disk = run_disk_usage(run_id, project_dir, project_root)
    return {
        "run_id": run_id,
        "card_id": _get(run, "card_id"),
        "card_name": card_name,
        "status": _get(run, "status"),
        "attempt": _get(run, "attempt", 1),
        "started_at": started,
        "completed_at": completed,
        "wall_ms": _ms_between(started, completed, now_ms),
        "model_time_ms": model_time,
        # Shared-process caveat, surfaced as a flag rather than buried in a
        # docstring: true when any two leaf tasks overlapped in time.
        "cpu_ms": cpu_total,
        "cpu_shared": _any_overlap(states),
        "tool_calls": tool_calls_total,
        "turns": (totals or {}).get("records", 0),
        "dims": (totals or {}).get("dims", {}),
        "effective_cost": (totals or {}).get("effective_cost"),
        "list_cost": (totals or {}).get("list_cost"),
        "has_estimates": bool((totals or {}).get("has_estimates")),
        "estimate_records": (totals or {}).get("estimate_records", 0),
        "tiers": tiers,
        "blocks": blocks,
        "files_written": files,
        "files_bytes": sum(int(f.get("bytes") or 0) for f in files),
        "disk": disk,
    }


def _any_overlap(states: Dict[str, Any]) -> bool:
    spans = []
    for st in states.values():
        if _get(st, "block_type", "") != "task":
            continue
        s, e = _get(st, "started_at"), _get(st, "completed_at")
        if s and e:
            spans.append((s, e))
    spans.sort()
    for (s1, e1), (s2, _e2) in zip(spans, spans[1:]):
        if s2 < e1:
            return True
    return False
