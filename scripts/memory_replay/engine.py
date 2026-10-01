#!/usr/bin/env python3
"""Longitudinal memory replay engine.

    python3 scripts/memory_replay/engine.py --arm <name> --run <run_id>
        [--since YYYY-MM-DD --until YYYY-MM-DD]      # until is inclusive
        [--cache-ns NS --cache-mode record|strict]
        [--overrides JSON] [--resume] [--max-seconds N]

Replays the user's real history, in timestamp order, through an arm's own
memory code into a sandbox store under ``.ziya/memory-replay/runs/<run>/``:

  extract  (per assistant reply, what app/server.py does after a stream):
           build the memory prompt section as the request path does (so
           record_load / labile bookkeeping happens), apply_feedback(chat,
           recorded reply), run_post_conversation_extraction(history before
           the reply, chat, project_name, project_path), then drain every
           task the memory code spawned (post-save maintenance, periodic
           cleanup, auto-organize) before the next event.
  propose / save
           the model's own memory_propose / memory_save calls from the audit
           log, through MemoryProposeTool / MemorySaveTool.execute (save runs
           run_post_save_maintenance itself, as in production).
  tick     app.agents.system_jobs.tick_system_jobs(now_ms=T) of the ARM, at
           the times events.Scheduler derives from the simulated clock
           (lifecycle 30 min, organize 6 h behind the arm's own gate).

Each arm runs in its own process with its tree first on sys.path.  A run dir
holds: meta.json, progress.json (cursor), checkpoint/ (sandbox home +
process state, every --checkpoint-every events and at the end),
transitions.jsonl, texts.enc (encrypted), events.jsonl (ids/counts only),
weekly_raw.jsonl, and after completion scorecard.json + weekly.jsonl.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import importlib
import io
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
REPLAY_DIR = Path(ROOT) / ".ziya" / "memory-replay"


def arm_dir(arm: str) -> Path:
    base = "baseline" if arm == "baseline" else "current"
    return REPLAY_DIR / "arms" / base


def setup_sys_path(arm: Optional[str]) -> None:
    """Arm tree first; harness dir next; repo root LAST (so timeline.py's
    ``if ROOT not in sys.path: insert(0, ROOT)`` cannot shadow the arm)."""
    if arm is not None:
        d = str(arm_dir(arm))
        sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") not in (ROOT, d)]
        sys.path.insert(0, d)
    if HERE not in sys.path:
        sys.path.insert(1, HERE)
    if ROOT not in sys.path:
        sys.path.append(ROOT)


# ---------------------------------------------------------------------------

LOGS = ("transitions.jsonl", "texts.enc", "events.jsonl", "weekly_raw.jsonl")
MEMORY_FILES_SKIP_SUFFIX = (".lock", ".tmp")


def fingerprint(home: Path) -> Dict[str, str]:
    """Plaintext-canonical sha256 of every sandbox store file (decrypted;
    npz compared by arrays because zip members carry wall-clock stamps)."""
    import numpy as np
    from app.utils.encryption import get_encryptor, is_encrypted
    enc = get_encryptor()
    out: Dict[str, str] = {}
    for p in sorted(Path(home).rglob("*")):
        if not p.is_file() or p.name.endswith(MEMORY_FILES_SKIP_SUFFIX) or p.parent.name == "audit":
            continue
        raw = p.read_bytes()
        if is_encrypted(raw):
            raw = enc.decrypt(raw)
        if p.suffix == ".npz":
            d = np.load(io.BytesIO(raw), allow_pickle=False)
            h = hashlib.sha256()
            h.update("\n".join(map(str, d["ids"])).encode())
            h.update(d["vectors"].astype("float32").tobytes())
            out[str(p.relative_to(home))] = h.hexdigest()
        else:
            out[str(p.relative_to(home))] = hashlib.sha256(raw).hexdigest()
    return out


def combined(fp: Dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()


def resolve_attr(path: str):
    """'a.b.C.X' -> (object holding X, 'X'), importing the longest module prefix."""
    parts = path.split(".")
    for i in range(len(parts) - 1, 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for p in parts[i:-1]:
            obj = getattr(obj, p)
        if not hasattr(obj, parts[-1]):
            raise AttributeError(f"override target {path} does not exist")
        return obj, parts[-1]
    raise ImportError(path)


def apply_overrides(overrides: Dict[str, Any]) -> List[Dict[str, Any]]:
    applied = []
    for k, v in sorted((overrides or {}).items()):
        obj, attr = resolve_attr(k)
        applied.append({"target": k, "old": repr(getattr(obj, attr)), "new": repr(v)})
        setattr(obj, attr, v)
    return applied


def _day_ms(s: str, end: bool = False) -> int:
    d = dt.datetime.strptime(s, "%Y-%m-%d")
    if end:
        d += dt.timedelta(days=1)
    return int(d.timestamp() * 1000)


def week_start_ms(t_ms: int) -> int:
    d = dt.datetime.fromtimestamp(t_ms / 1000)
    monday = (d - dt.timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(monday.timestamp() * 1000)


def _counts(res: Any) -> Dict[str, Any]:
    if not isinstance(res, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in res.items():
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[k] = v
        elif k in ("reason", "status") and isinstance(v, str) and len(v) <= 60:
            out[k] = v
        elif isinstance(v, (list, dict)):
            out[k + "_n"] = len(v)
    return out


@dataclass
class Config:
    arm: str
    run_dir: Path
    real_home: Path
    since_ms: int = 0
    until_ms: Optional[int] = None
    cache_root: Path = REPLAY_DIR / "cache"
    cache_ns: str = "main"
    cache_mode: str = "record"
    overrides: Dict[str, Any] = field(default_factory=dict)
    checkpoint_every: int = 200
    # Also checkpoint when this much wall time has passed since the last one.
    # A chunk killed before its first 200-event checkpoint used to lose ALL
    # its work: sep-current went through three chunks on 2026-09-27 without
    # ever writing one, each restart wiping the last.
    checkpoint_every_s: float = 300.0
    # Per-call outage window (credentials / network) before the chunk stops.
    infra_wait_s: float = 900.0
    max_seconds: Optional[float] = None
    resume: bool = False
    idle_gap_ms: int = 3600_000
    timeline_path: Path = REPLAY_DIR / "timeline.jsonl"
    events: Optional[List[Any]] = None          # tests inject
    live_call: Optional[Callable[..., Any]] = None  # tests inject a fake model
    provider: Optional[Any] = None               # tests inject a fake embedder
    guard: bool = True
    stop_after: Optional[int] = None             # tests: simulate a mid-run stop
    audit_restore: bool = True
    accept_harness_change: bool = False          # resume across a harness edit (recorded)


class Paused(Exception):
    pass


class Engine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.run_dir = Path(cfg.run_dir)
        self.home = self.run_dir / "home"
        self.ckpt = self.run_dir / "checkpoint"
        self.stats: Dict[str, Any] = {"extract": 0, "propose": 0, "save": 0, "ticks": 0,
                                      "jobs_ran": {}, "errors": {}, "feedback_loaded": 0,
                                      "feedback_used": 0}
        self.week: Optional[int] = None

    # -- meta ----------------------------------------------------------------

    def _meta(self) -> Dict[str, Any]:
        import arms
        import app
        return {
            "arm": self.cfg.arm, "arm_dir": str(arm_dir(self.cfg.arm)),
            "app_file": getattr(app, "__file__", None),
            "arm_hash": arms.tree_hash(os.path.join(str(arm_dir(self.cfg.arm)), "app"))[0]
            if arm_dir(self.cfg.arm).exists() and str(arm_dir(self.cfg.arm)) in (app.__file__ or "") else None,
            "harness_hash": arms.tree_hash(HERE)[0],
            "overrides": self.cfg.overrides, "cache_ns": self.cfg.cache_ns,
            "cache_mode": self.cfg.cache_mode, "since_ms": self.cfg.since_ms,
            "until_ms": self.cfg.until_ms, "idle_gap_ms": self.cfg.idle_gap_ms,
            "checkpoint_every": self.cfg.checkpoint_every,
            "python": sys.version.split()[0], "hashseed": os.environ.get("PYTHONHASHSEED"),
        }

    def _check_meta(self) -> None:
        meta = self._meta()
        p = self.run_dir / "meta.json"
        if p.exists():
            old = json.loads(p.read_text())
            keys = ("arm", "arm_hash", "harness_hash", "overrides", "cache_ns", "since_ms",
                    "until_ms", "idle_gap_ms", "checkpoint_every")
            diff = {k: (old.get(k), meta.get(k)) for k in keys if old.get(k) != meta.get(k)}
            if set(diff) == {"harness_hash"} and self.cfg.accept_harness_change:
                old.setdefault("harness_history", []).append(
                    {"from": old["harness_hash"], "to": meta["harness_hash"], "at_wall": time.time()})
                old["harness_hash"] = meta["harness_hash"]
                diff = {}
            if diff:
                raise SystemExit(f"run {self.run_dir.name}: meta mismatch on resume: {diff}")
            old.setdefault("modes", []).append(self.cfg.cache_mode)
            p.write_text(json.dumps(old, indent=1))
        else:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            meta["modes"] = [self.cfg.cache_mode]
            p.write_text(json.dumps(meta, indent=1))

    # -- events --------------------------------------------------------------

    def build_events(self) -> List[Any]:
        import events as E
        if self.cfg.events is not None:
            return sorted(self.cfg.events, key=E.ReplayEvent.sort_key)
        home = str(self.cfg.real_home)
        projects = E.project_info(home)
        chats = E.chat_index(home)
        self.audit_stats = E.AuditStats()
        ex = E.timeline_events(str(self.cfg.timeline_path), projects, self.cfg.since_ms, self.cfg.until_ms)
        au = E.audit_events(home, chats, projects, self.cfg.since_ms, self.cfg.until_ms,
                            self.audit_stats, restore=self.cfg.audit_restore)
        self.stats["audit"] = self.audit_stats.as_dict()
        return E.merge(ex, au)

    # -- checkpoint ----------------------------------------------------------

    def _log_sizes(self) -> Dict[str, int]:
        return {n: (self.run_dir / n).stat().st_size if (self.run_dir / n).exists() else 0 for n in LOGS}

    def _process_state(self) -> Dict[str, Any]:
        import app.memory.feedback as fb
        return {"loaded": {k: list(v) for k, v in fb._loaded_per_conversation.items()},
                "labile": dict(fb._labile_until)}

    def _restore_process_state(self, st: Dict[str, Any]) -> None:
        import app.memory.feedback as fb
        fb._loaded_per_conversation.clear()
        for k, v in (st.get("loaded") or {}).items():
            fb._loaded_per_conversation[k] = set(v)
        fb._labile_until.clear()
        fb._labile_until.update({k: int(v) for k, v in (st.get("labile") or {}).items()})

    def checkpoint(self, cursor: int, done: bool = False) -> None:
        import app.services.embedding_service as es
        if es._cache is not None:
            es._cache.flush()
        prog = {"cursor": cursor, "done": done, "n_events": self.n_events,
                "clock": self.clock.state(), "sched": self.sched.state(),
                "proc": self._process_state(), "logs": self._log_sizes(),
                "week": self.week, "stats": self.stats, "cache": self.cache.stats,
                "audit_counts": self.sandbox.audit_counts,
                "wall": time.time()}
        new = self.run_dir / "checkpoint.new"
        if new.exists():
            shutil.rmtree(new)
        shutil.copytree(self.home, new / "home")
        (new / "progress.json").write_text(json.dumps(prog, indent=1, default=str))
        old = self.run_dir / "checkpoint.old"
        if self.ckpt.exists():
            if old.exists():
                shutil.rmtree(old)
            os.rename(self.ckpt, old)
        os.rename(new, self.ckpt)
        if old.exists():
            shutil.rmtree(old)
        (self.run_dir / "progress.json").write_text(json.dumps(
            {k: prog[k] for k in ("cursor", "done", "n_events", "stats", "cache", "wall")}, indent=1, default=str))
        self._last_ckpt_cursor = cursor

    def _events_prefix_unchanged(self, evs: List[Any], cursor: int) -> bool:
        """True iff the already-replayed prefix (indices < cursor) matches
        the recorded events.jsonl exactly.  Used to accept an event stream
        that only grew by appending new events after the checkpoint cursor
        (e.g. the live audit log gained a row while ``--until`` is close to
        "now"); events.jsonl was already truncated to ``cursor`` lines by
        ``_load_checkpoint``."""
        p = self.run_dir / "events.jsonl"
        if not p.exists():
            return cursor == 0
        # events.jsonl also carries scheduler "tick" rows (no "i"); only
        # rows for a processed ReplayEvent carry it, keyed by its cursor
        # position, so index on that rather than physical line order.
        old_refs: Dict[int, Dict[str, Any]] = {}
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if "i" not in row:
                    continue
                old_refs[int(row["i"])] = {
                    "t": row.get("t"), "kind": row.get("kind"), "chat": row.get("chat"),
                    "project": row.get("project"), "src": row.get("src")}
        if len(old_refs) != cursor or cursor > len(evs):
            return False
        for i in range(cursor):
            if i not in old_refs or evs[i].ref() != old_refs[i]:
                return False
        return True

    def _load_checkpoint(self) -> Optional[Dict[str, Any]]:
        for d in (self.ckpt, self.run_dir / "checkpoint.old"):
            if (d / "progress.json").exists():
                prog = json.loads((d / "progress.json").read_text())
                if self.home.exists():
                    shutil.rmtree(self.home)
                shutil.copytree(d / "home", self.home)
                for n, size in (prog.get("logs") or {}).items():
                    p = self.run_dir / n
                    if p.exists():
                        with open(p, "r+b") as f:
                            f.truncate(size)
                return prog
        return None

    # -- structure snapshot ----------------------------------------------------

    def _snapshot(self, label_t: int, week: int) -> None:
        import metrics
        store = self.sandbox.stores["memory"]
        rows = list(store._load_memories())
        nodes = store._load_mindmap() or {}
        snap = metrics.structure_snapshot(rows, nodes)
        snap.update({"week_start": week, "t": label_t,
                     "open_proposals": len(self.sandbox.stores["proposals"].list_open())})
        with open(self.run_dir / "weekly_raw.jsonl", "a") as f:
            f.write(json.dumps(snap, sort_keys=True) + "\n")

    # -- per-event ------------------------------------------------------------

    def _log_event(self, row: Dict[str, Any]) -> None:
        with open(self.run_dir / "events.jsonl", "a") as f:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    async def _drain(self) -> None:
        me = asyncio.current_task()
        for _ in range(100):
            pending = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
            if not pending:
                return
            res = await asyncio.gather(*pending, return_exceptions=True)
            for r in res:
                if isinstance(r, BaseException):
                    self.stats["errors"][type(r).__name__] = self.stats["errors"].get(type(r).__name__, 0) + 1

    def _guard_checks(self) -> None:
        from sandbox import SandboxViolation
        from llmcache import CacheMiss
        self.sandbox.check()
        if self.cache.stats["miss"]:
            raise CacheMiss(f"{self.cache.stats['miss']} strict cache miss(es)")
        # An outage raised inside a background task is swallowed by _drain
        # (and by the memory code's fail-open handlers); the counter is what
        # makes it stop the chunk anyway.
        if self.cache.stats.get("infra_abort"):
            from llmcache import ReplayInfraError
            raise ReplayInfraError(self.cache.last_infra_error or "infrastructure unavailable")

    async def _tick(self, T: int) -> List[str]:
        import app.agents.system_jobs as sj
        self.clock.set(T)
        ran = await sj.tick_system_jobs(now_ms=T)
        await self._drain()
        self.sched.mark(T)
        self.stats["ticks"] += 1
        for j in ran:
            self.stats["jobs_ran"][j] = self.stats["jobs_ran"].get(j, 0) + 1
        self._log_event({"t": T, "kind": "tick", "ran": ran})
        return ran

    async def _extract(self, ev) -> Dict[str, Any]:
        import events as E
        from app.context import set_conversation_id
        from app.memory.feedback import apply_feedback
        from app.memory.extractor import run_post_conversation_extraction
        hist, reply = E.load_turn(ev, str(self.cfg.real_home)) if not getattr(ev, "payload", {}).get("history") \
            else (ev.payload["history"], ev.payload.get("reply", ""))
        role = {"user": "human", "assistant": "ai"}
        hist = [{"role": role.get(m["role"], m["role"]), "content": m["content"]} for m in hist]
        set_conversation_id(ev.chat_id)
        try:
            from app.memory.prompt import get_memory_prompt_sections
            get_memory_prompt_sections()
        except ImportError:
            from app.memory.prompt import get_memory_prompt_section
            get_memory_prompt_section()
        fb = await apply_feedback(ev.chat_id, reply)
        self.stats["feedback_loaded"] += int(fb.get("loaded", 0))
        self.stats["feedback_used"] += int(fb.get("used", 0))
        res = await run_post_conversation_extraction(hist, ev.chat_id, project_name=ev.project_name,
                                                     project_path=ev.project_path)
        out = _counts(res)
        out["fb_loaded"], out["fb_used"] = fb.get("loaded", 0), fb.get("used", 0)
        return out

    async def _tool(self, ev) -> Dict[str, Any]:
        from app.context import set_conversation_id
        from app.mcp.tools.memory_tools import MemoryProposeTool, MemorySaveTool
        set_conversation_id(ev.chat_id)
        p = ev.payload
        kw = {"content": p["content"], "tags": list(p.get("tags") or []), "layer": p.get("layer")}
        if ev.project_path:
            kw["_workspace_path"] = ev.project_path
        if ev.kind == "propose":
            res = await MemoryProposeTool().execute(conversation_id=ev.chat_id, **kw)
        else:
            res = await MemorySaveTool().execute(**kw)
        return {"ok": bool(isinstance(res, dict) and res.get("success")),
                "id": (res or {}).get("proposal_id") or (res or {}).get("memory_id")}

    # -- main -----------------------------------------------------------------

    async def run(self) -> Dict[str, Any]:
        import simclock
        import sandbox as S
        import llmcache
        import attribution
        import events as E

        cfg = self.cfg
        self._check_meta()
        evs = self.build_events()
        self.n_events = len(evs)
        prog = None
        if (self.ckpt / "progress.json").exists() or (self.run_dir / "checkpoint.old" / "progress.json").exists():
            if not cfg.resume:
                raise SystemExit(f"run {self.run_dir.name} has a checkpoint; pass --resume")
            prog = self._load_checkpoint()
        else:
            for n in LOGS:
                (self.run_dir / n).unlink(missing_ok=True)
            if self.home.exists():
                shutil.rmtree(self.home)
        if prog and prog.get("done"):
            return {"status": "done", "cursor": prog["cursor"], "n_events": self.n_events}
        if prog and prog.get("n_events") != self.n_events:
            old_n = prog.get("n_events")
            cursor = int(prog.get("cursor") or 0)
            if self.n_events > old_n and self._events_prefix_unchanged(evs, cursor):
                # Only new events appended at the tail (e.g. the live audit
                # log grew while `--until` is close to "now"); everything
                # already replayed (indices < cursor) is byte-identical, so
                # it is safe to keep going against the longer stream.
                self.stats.setdefault("overrides", [])
                logging.getLogger("ziya").warning(
                    "event stream grew since checkpoint (append-only, prefix "
                    "unchanged): %s -> %s; continuing", old_n, self.n_events)
            else:
                raise SystemExit(
                    f"event stream changed since checkpoint: {old_n} -> {self.n_events}")

        self.clock = simclock.SimClock(evs[0].t if evs else 0, seed=f"{cfg.arm}")
        self.sandbox = S.Sandbox(self.home, real_home=cfg.real_home, guard=cfg.guard)
        import app.agents.system_jobs as sj
        sj._reset_for_tests()
        sj._ensure_registered()
        self.sched = E.Scheduler({j.name: int(j.interval_s) * 1000 for j in sj._JOBS}, cfg.idle_gap_ms)
        start = 0
        if prog:
            self.clock.restore(prog["clock"])
            self.sched.restore(prog["sched"])
            self.stats = prog["stats"]
            self.week = prog.get("week")
            start = int(prog["cursor"])

        t0 = time.time()
        self.external = None
        self.infra_stop = None
        self._last_ckpt_wall = time.time()
        self._last_ckpt_cursor = start
        try:
            status, i = await self._run_sandboxed(evs, prog, start, t0)
        except llmcache.ReplayInfraError as e:
            # Deliberately NO checkpoint: the in-flight event may have
            # half-mutated the sandbox (some calls answered, one outage), and
            # the memory code would have failed open on the rest.  --resume
            # restarts from the last clean checkpoint, which the time-based
            # cadence keeps a few minutes old at most.
            status, i = "infra_unavailable", self._last_ckpt_cursor
            self.infra_stop = {"error": str(e)[:500], "resume_from": i}
            with open(self.run_dir / "infra_stops.jsonl", "a") as f:
                f.write(json.dumps({"wall": time.time(), **self.infra_stop}) + "\n")
        except S.ExternalStoreChange as e:
            # Checkpoint is already written; record loudly, exit non-zero.
            self.external = e.args[0] if e.args else {}
            status, i = self._last_status
            with open(self.run_dir / "external_changes.jsonl", "a") as f:
                f.write(json.dumps({"wall": time.time(), "cursor": i, **self.external}) + "\n")
        out = {"status": status, "cursor": i, "n_events": self.n_events,
               "elapsed_s": round(time.time() - t0, 1), "cache": self.cache.stats,
               "fingerprint": combined(fingerprint(self.home))}
        if self.external:
            out["external_store_change"] = self.external
        if self.infra_stop:
            out["infra_unavailable"] = self.infra_stop
        if status == "done":
            import metrics
            sc = metrics.compute(self.run_dir)
            out["scorecard"] = sc.get("final")
        return out

    async def _run_sandboxed(self, evs, prog, start, t0):
        import simclock
        import llmcache
        import attribution
        cfg = self.cfg
        self._last_status = ("running", start)
        with self.sandbox:
            if prog:
                self.sandbox.audit_counts.update(prog.get("audit_counts") or {})
            undo_clock = simclock.install(self.clock)
            self.cache = llmcache.LLMCache(cfg.cache_root, cfg.cache_ns, cfg.cache_mode,
                                           infra_wait_s=cfg.infra_wait_s)
            if prog:
                for k, v in (prog.get("cache") or {}).items():
                    self.cache.stats[k] = v
            self.cache.install(live_call=cfg.live_call, provider=cfg.provider)
            from app.storage.memory import MemoryStorage
            from app.storage.proposals import ProposalsStore
            att = attribution.Attribution(self.run_dir, self.clock)
            att.seed(list(self.sandbox.stores["memory"]._load_memories()))
            att.install(MemoryStorage, ProposalsStore)
            self.stats["overrides"] = apply_overrides(cfg.overrides)
            if prog:
                self._restore_process_state(prog.get("proc") or {})
            status = "done"
            i = start
            try:
                while i < len(evs):
                    ev = evs[i]
                    wk = week_start_ms(ev.t)
                    if self.week is not None and wk != self.week:
                        self._snapshot(self.clock.now_ms(), self.week)
                    self.week = wk
                    for T in self.sched.ticks_before(ev.t):
                        att.event_index = i
                        await self._tick(T)
                        self._guard_checks()
                    self.clock.set(ev.t)
                    att.event_index = i
                    row = {"i": i, **ev.ref()}
                    try:
                        if ev.kind == "extract":
                            row["res"] = await self._extract(ev)
                        else:
                            row["res"] = await self._tool(ev)
                        self.stats[ev.kind] = self.stats.get(ev.kind, 0) + 1
                    except Exception as e:  # production swallows these (fire-and-forget)
                        row["error"] = type(e).__name__
                        self.stats["errors"][type(e).__name__] = self.stats["errors"].get(type(e).__name__, 0) + 1
                    await self._drain()
                    self.sched.saw_event(ev.t)
                    self._log_event(row)
                    self._guard_checks()
                    i += 1
                    if cfg.stop_after is not None and i - start >= cfg.stop_after and i < len(evs):
                        status = "paused"
                        break
                    if i < len(evs) and (i % cfg.checkpoint_every == 0
                                         or time.time() - self._last_ckpt_wall >= cfg.checkpoint_every_s):
                        self.checkpoint(i)
                        self._last_ckpt_wall = time.time()
                    if cfg.max_seconds is not None and time.time() - t0 > cfg.max_seconds and i < len(evs):
                        status = "paused"
                        break
                if status == "done":
                    if self.week is not None:
                        self._snapshot(self.clock.now_ms(), self.week)
                self.checkpoint(i, done=(status == "done"))
                self._last_status = (status, i)
            finally:
                att.uninstall()
                self.cache.uninstall()
                simclock.uninstall(undo_clock)
        return status, i


# ---------------------------------------------------------------------------

def stub_api_memory() -> None:
    """``_maybe_periodic_cleanup`` reads ``app.api.memory._organize_task_status``
    to skip while a UI-triggered organize runs.  Importing the real module
    pulls in the whole API package (routes, AST indexer threads); no UI
    organize ever runs in a replay, so the faithful value is running=False."""
    import types
    if "app.api.memory" not in sys.modules:
        stub = types.ModuleType("app.api.memory")
        stub._organize_task_status = {"running": False, "result": None, "error": None, "started_at": None}
        stub.__replay_stub__ = True
        sys.modules["app.api.memory"] = stub


def bootstrap(real_home: Optional[Path] = None) -> Path:
    """Process setup shared by the CLI (not tests): env, write guard BEFORE
    plugin init, plugins, encryption check.  Returns the real Ziya home."""
    os.environ["ZIYA_ENABLE_MEMORY"] = "true"
    os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")
    from app.utils.paths import get_ziya_home
    home = Path(os.path.realpath(real_home or get_ziya_home()))
    import sandbox as S
    S._install_hook()
    S._Guard.real_home = str(home)
    S._Guard.violations = []
    S._Guard.active = True
    stub_api_memory()
    from app.plugins import initialize as init_plugins
    init_plugins()
    import logging
    for name in ("ZIYA", "boto3", "botocore", "urllib3", "app"):
        logging.getLogger(name).setLevel(logging.ERROR)
    from app.utils.encryption import get_encryptor
    if not get_encryptor().is_enabled("session_data"):
        raise SystemExit("ALE encryption disabled: refusing to run (no plaintext on disk)")
    stub_api_memory()
    if S._Guard.violations:
        raise SystemExit(f"plugin init tried to write under ~/.ziya: {S._Guard.violations[:5]}")
    return home


RUN_LOCK_EXIT = 4
INFRA_EXIT = 5
_run_lock_fd: Optional[int] = None


def acquire_run_lock(run_dir: Path) -> None:
    """Exclusive, process-lifetime lock on one run directory.

    Two engines advancing the same run interleave checkpoint, cursor and
    transition writes and corrupt it silently.  On 2026-09-27 a fan-out
    iteration that owned ``sep-current-aa`` started a second engine on
    ``sep-baseline`` while that run's own iteration was mid-chunk; only the
    timing of the chunk boundaries kept the run intact.  Card wording
    cannot guarantee this, so the engine refuses: a second process exits
    with RUN_LOCK_EXIT and names the holder.

    flock is released by the kernel when the process dies, so a crashed
    chunk never leaves a stale lock behind.
    """
    global _run_lock_fd
    import fcntl
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "engine.lock"
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            holder = os.pread(fd, 4096, 0).decode("utf-8", "replace").strip()
        except OSError:
            holder = ""
        os.close(fd)
        sys.stderr.write(
            f"*** run {run_dir.name!r} is already being advanced by another engine "
            f"process ({holder or 'holder unknown'}). Refusing to start a second "
            f"writer. If you are one iteration of a parallel fan-out, this run "
            f"belongs to a sibling: advance only your own run id. ***\n")
        raise SystemExit(RUN_LOCK_EXIT)
    os.ftruncate(fd, 0)
    os.pwrite(fd, json.dumps({"pid": os.getpid(), "started": time.time(),
                              "argv": sys.argv[1:]}).encode(), 0)
    _run_lock_fd = fd          # held until the process exits


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--cache-ns", default="main")
    ap.add_argument("--cache-mode", default="record", choices=("record", "strict"))
    ap.add_argument("--overrides", default="{}")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--checkpoint-every", type=int, default=200)
    ap.add_argument("--checkpoint-every-s", type=float, default=300.0,
                    help="also checkpoint after this many wall seconds")
    ap.add_argument("--infra-wait-s", type=float, default=900.0,
                    help="per-call outage window before the chunk stops with exit 5")
    ap.add_argument("--idle-gap-min", type=float, default=60.0)
    ap.add_argument("--accept-harness-change", action="store_true",
                    help="resume although scripts/memory_replay changed (recorded in meta)")
    a = ap.parse_args()

    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # After the re-exec (an inherited lock fd would not survive it) and
    # before anything reads or writes the run.
    acquire_run_lock(REPLAY_DIR / "runs" / a.run)
    setup_sys_path(a.arm)
    import app
    if not os.path.realpath(app.__file__).startswith(os.path.realpath(str(arm_dir(a.arm)))):
        raise SystemExit(f"app resolved to {app.__file__}, not arm {a.arm}")
    real_home = bootstrap()
    cfg = Config(arm=a.arm, run_dir=REPLAY_DIR / "runs" / a.run, real_home=real_home,
                 since_ms=_day_ms(a.since) if a.since else 0,
                 until_ms=_day_ms(a.until, end=True) if a.until else None,
                 cache_ns=a.cache_ns, cache_mode=a.cache_mode,
                 overrides=json.loads(a.overrides), checkpoint_every=a.checkpoint_every,
                 checkpoint_every_s=a.checkpoint_every_s, infra_wait_s=a.infra_wait_s,
                 max_seconds=a.max_seconds, resume=a.resume,
                 idle_gap_ms=int(a.idle_gap_min * 60_000),
                 accept_harness_change=a.accept_harness_change)
    if a.cache_mode == "record":
        from app.services.embedding_service import get_embedding_provider, NoopProvider
        if isinstance(get_embedding_provider(), NoopProvider):
            raise SystemExit("embedding provider unavailable (NoopProvider): refusing an unfaithful record run")
    else:
        # Strict never calls the provider; build it without the live smoke test.
        import app.services.embedding_service as es
        if es._provider is None and os.environ.get("ZIYA_EMBEDDING_PROVIDER", "auto").lower() != "none":
            es._provider = es.BedrockTitanProvider()
    out = asyncio.run(Engine(cfg).run())
    print(json.dumps(out, indent=1, default=str))
    if out.get("infra_unavailable"):
        sys.stderr.write(
            f"*** exiting {INFRA_EXIT}: model/embedding infrastructure unavailable for "
            f"longer than --infra-wait-s ({out['infra_unavailable']['error'][:200]}). Nothing "
            f"was cached from the outage; --resume continues from event "
            f"{out['infra_unavailable']['resume_from']}. Restore connectivity/credentials "
            f"(mwinit) and re-run the SAME command; do not switch runs. ***\n")
        sys.exit(INFRA_EXIT)
    if out.get("external_store_change"):
        sys.stderr.write("*** exiting 3: real ~/.ziya/memory changed during this chunk (external writer); "
                         "checkpoint intact, see external_changes.jsonl ***\n")
        sys.exit(3)


if __name__ == "__main__":
    main()
