"""Replay event stream: timeline extract events + the model's own
memory_propose / memory_save tool calls + scheduled system jobs.

* **extract** -- from ``timeline.py`` (one per assistant reply).
* **propose / save** -- ``memory_propose`` / ``memory_save`` calls recorded
  (status ok) in ``~/.ziya/audit/tool_audit_*.jsonl`` with ``eventTime`` and
  the conversation id (``args.conversation_id``, else the 12-char ``conv``
  prefix resolved against chat record names).  project_path is the
  conversation's project, which is what production passes as
  ``_workspace_path``.  The audit log truncates ``args`` strings at 500
  chars; truncated contents are restored from the real (read-only)
  ``probationary.jsonl`` / ``memories.json`` when exactly one record from the
  same conversation starts with the truncated prefix, else replayed truncated
  (counted in ``AuditStats``).
* **scheduled** -- not stored: ``Scheduler`` generates system-job tick times
  from the simulated clock, mirroring ``app.agents.system_jobs``: each job's
  interval gates its *check*; the arm's own gate decides whether it runs.

Idle-gap rule.  The real scheduler only ticks while a server is up.  The
replay treats the server as up for ``idle_gap_ms`` (default 1 h) after each
event.  A job falling due inside that window ticks at its due time; a job
falling due later -- during an idle gap -- ticks ONCE, at the first event
after the gap (before that event is processed), however many intervals
elapsed.  Recorded in design/memory-replay.md.

Payload text (proposal content) is held in memory only; nothing here writes
conversation or memory text to disk.
"""
from __future__ import annotations

import ast
import datetime as dt
import glob
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import timeline as tl

KIND_ORDER = {"propose": 0, "save": 0, "extract": 1}


@dataclass
class ReplayEvent:
    t: int
    kind: str                       # extract | propose | save
    chat_id: Optional[str]
    project_id: Optional[str]
    project_path: Optional[str]
    project_name: Optional[str] = None
    msg_index: int = -1             # extract: reply index; audit: line order
    source: str = ""                # audit file:line, or "timeline"
    payload: Dict[str, Any] = field(default_factory=dict, repr=False)  # in-memory only

    def sort_key(self) -> Tuple:
        return (self.t, KIND_ORDER.get(self.kind, 9), self.chat_id or "", self.msg_index, self.source)

    def ref(self) -> Dict[str, Any]:
        """Disk-safe reference (ids only)."""
        return {"t": self.t, "kind": self.kind, "chat": self.chat_id,
                "project": os.path.basename((self.project_path or "").rstrip("/")) or None,
                "src": self.source}


@dataclass
class AuditStats:
    seen: int = 0
    used: int = 0
    skipped_status: int = 0
    unresolved_conversation: int = 0
    truncated: int = 0
    restored: int = 0
    unrestored: int = 0

    def as_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


def _iso_ms(s: str) -> Optional[int]:
    try:
        return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


def _tags(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                x = ast.literal_eval(s)
                if isinstance(x, (list, tuple)):
                    return [str(t) for t in x]
            except Exception:
                pass
        return [t.strip() for t in s.split(",") if t.strip()]
    return []


def chat_index(home: str) -> Dict[str, str]:
    """chat id -> project id, from record file names (no decryption)."""
    out: Dict[str, str] = {}
    for f in glob.glob(os.path.join(home, "projects", "*", "chats", "*.json")):
        out[os.path.splitext(os.path.basename(f))[0]] = os.path.basename(os.path.dirname(os.path.dirname(f)))
    return out


def project_info(home: str) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """project id -> (path, name), read-only."""
    from pathlib import Path
    from app.storage.projects import ProjectStorage
    ps = ProjectStorage(Path(home))
    out: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for d in glob.glob(os.path.join(home, "projects", "*")):
        if not os.path.isdir(d):
            continue
        pid = os.path.basename(d)
        try:
            p = ps.get(pid)
            out[pid] = (getattr(p, "path", None), getattr(p, "name", None))
        except Exception:
            out[pid] = (None, None)
    return out


def _real_texts(home: str) -> List[Dict[str, Any]]:
    """(conversation_id, content) of every proposal and memory in the REAL
    store, for restoring audit-truncated contents.  Read-only; in memory."""
    from app.utils.encryption import get_encryptor, is_encrypted
    enc = get_encryptor()
    out: List[Dict[str, Any]] = []
    mem = os.path.join(home, "memory")

    def _read(p: str) -> Optional[bytes]:
        try:
            raw = open(p, "rb").read()
            return enc.decrypt(raw) if is_encrypted(raw) else raw
        except Exception:
            return None

    raw = _read(os.path.join(mem, "probationary.jsonl"))
    if raw:
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                ev = json.loads(line)
            except Exception:
                continue
            d = ev.get("data") if ev.get("kind") == "record" else None
            if isinstance(d, dict) and d.get("content"):
                out.append({"conv": d.get("conversation_id"), "content": d["content"], "src": "proposal"})
    raw = _read(os.path.join(mem, "memories.json"))
    if raw:
        try:
            data = json.loads(raw)
            rows = data.get("memories", []) if isinstance(data, dict) else data
            for m in rows or []:
                if m.get("content"):
                    out.append({"conv": m.get("learned_from_conversation"), "content": m["content"], "src": "memory"})
        except Exception:
            pass
    return out


AUDIT_LIMIT = 500


def audit_events(home: str, chats: Dict[str, str],
                 projects: Dict[str, Tuple[Optional[str], Optional[str]]],
                 since_ms: int = 0, until_ms: Optional[int] = None,
                 stats: Optional[AuditStats] = None,
                 restore: bool = True) -> List[ReplayEvent]:
    stats = stats if stats is not None else AuditStats()
    by_prefix: Dict[str, List[str]] = {}
    for cid in chats:
        by_prefix.setdefault(cid[:12], []).append(cid)
    texts = _real_texts(home) if restore else []
    out: List[ReplayEvent] = []
    for f in sorted(glob.glob(os.path.join(home, "audit", "tool_audit_*.jsonl"))):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for ln, line in enumerate(fh):
                if '"memory_propose"' not in line and '"memory_save"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                name = d.get("eventName")
                if name not in ("memory_propose", "memory_save"):
                    continue
                t = _iso_ms(d.get("eventTime", ""))
                if t is None or t < since_ms or (until_ms is not None and t >= until_ms):
                    continue
                stats.seen += 1
                if d.get("status") != "ok":
                    stats.skipped_status += 1
                    continue
                a = d.get("args") or {}
                cid = a.get("conversation_id")
                if not cid:
                    cands = by_prefix.get((d.get("conv") or "")[:12], [])
                    cid = cands[0] if len(cands) == 1 else None
                if not cid:
                    stats.unresolved_conversation += 1
                    continue
                content = str(a.get("content") or "")
                if not content.strip():
                    stats.skipped_status += 1
                    continue
                if len(content) >= AUDIT_LIMIT:
                    stats.truncated += 1
                    hits = {x["content"] for x in texts
                            if x["content"].startswith(content) and x["conv"] in (cid, None)}
                    if len(hits) == 1:
                        content = hits.pop()
                        stats.restored += 1
                    else:
                        stats.unrestored += 1
                pid = chats.get(cid)
                path, pname = projects.get(pid, (None, None)) if pid else (None, None)
                out.append(ReplayEvent(
                    t=t, kind="propose" if name == "memory_propose" else "save",
                    chat_id=cid, project_id=pid, project_path=path, project_name=pname,
                    msg_index=ln, source=f"{os.path.basename(f)}:{ln}",
                    payload={"content": content, "tags": _tags(a.get("tags")),
                             "layer": a.get("layer") or "domain_context"}))
                stats.used += 1
    return out


def timeline_events(path: str, projects: Dict[str, Tuple[Optional[str], Optional[str]]],
                    since_ms: int = 0, until_ms: Optional[int] = None) -> List[ReplayEvent]:
    out = []
    for e in tl.read_timeline(path):
        if e.t < since_ms or (until_ms is not None and e.t >= until_ms):
            continue
        pname = projects.get(e.project_id, (None, None))[1] if e.project_id else None
        out.append(ReplayEvent(t=e.t, kind="extract", chat_id=e.chat_id, project_id=e.project_id,
                               project_path=e.project_path, project_name=pname,
                               msg_index=e.msg_index, source="timeline"))
    return out


def merge(*streams: Iterable[ReplayEvent]) -> List[ReplayEvent]:
    evs = [e for s in streams for e in s]
    evs.sort(key=ReplayEvent.sort_key)
    return evs


def load_turn(ev: ReplayEvent, home: str) -> Tuple[List[Dict[str, Any]], str]:
    """(history handed to extraction, the recorded reply text) for an
    extract event.  Read-only; the text stays in memory."""
    from app.utils.encryption import get_encryptor, is_encrypted
    hits = glob.glob(os.path.join(home, "projects", ev.project_id or "*", "chats", f"{ev.chat_id}.json"))
    raw = open(hits[0], "rb").read()
    if is_encrypted(raw):
        raw = get_encryptor().decrypt(raw)
    msgs = json.loads(raw).get("messages") or []
    reply = msgs[ev.msg_index].get("content") if ev.msg_index < len(msgs) else ""
    return tl.history_for(msgs, ev.msg_index), reply if isinstance(reply, str) else ""


# -- scheduled jobs ---------------------------------------------------------

class Scheduler:
    """System-job tick times from the simulated clock.

    ``intervals``: job name -> check interval (ms), as registered by the arm's
    app.agents.system_jobs.  Mirrors ``tick_system_jobs``: a never-checked job
    checks on the first tick; afterwards a job is checked when
    ``now - last_check >= interval``, and every due job's ``last_check`` moves
    to the tick time whether or not its gate lets it run.
    """

    def __init__(self, intervals: Dict[str, int], idle_gap_ms: int = 3600_000) -> None:
        self.intervals = dict(intervals)
        self.idle_gap_ms = int(idle_gap_ms)
        self.last_check: Dict[str, int] = {}
        self.last_event_t: Optional[int] = None

    def _due_at(self, job: str) -> Optional[int]:
        lc = self.last_check.get(job)
        return None if lc is None else lc + self.intervals[job]

    def _any_due(self, t: int) -> bool:
        return any(self._due_at(j) is None or self._due_at(j) <= t for j in self.intervals)

    def mark(self, t: int) -> List[str]:
        """Record a tick at ``t``; returns the jobs that were checked."""
        checked = [j for j in self.intervals if self._due_at(j) is None or self._due_at(j) <= t]
        for j in checked:
            self.last_check[j] = t
        return checked

    def ticks_before(self, next_t: int) -> List[int]:
        """Tick times to run before the event at ``next_t`` (ascending).
        Call ``mark(T)`` for each as it runs, then ``saw_event(next_t)``."""
        if not self.intervals:
            return []
        ticks: List[int] = []
        if self.last_event_t is not None:
            up_until = self.last_event_t + self.idle_gap_ms
            probe = dict(self.last_check)
            while True:
                dues = [probe[j] + self.intervals[j] for j in self.intervals if j in probe]
                if len(dues) < len(self.intervals):
                    break
                T = min(dues)
                if T >= next_t or T > up_until:
                    break
                ticks.append(T)
                for j in self.intervals:
                    if probe[j] + self.intervals[j] <= T:
                        probe[j] = T
            # jobs still due at next_t after the in-window ticks: once, at next_t
            probe_any = any(j not in probe or probe[j] + self.intervals[j] <= next_t for j in self.intervals)
            if probe_any:
                ticks.append(next_t)
        elif self._any_due(next_t):
            ticks.append(next_t)
        return ticks

    def saw_event(self, t: int) -> None:
        self.last_event_t = t if self.last_event_t is None else max(self.last_event_t, t)

    def state(self) -> Dict[str, Any]:
        return {"last_check": dict(self.last_check), "last_event_t": self.last_event_t,
                "intervals": self.intervals, "idle_gap_ms": self.idle_gap_ms}

    def restore(self, st: Dict[str, Any]) -> None:
        self.last_check = {k: int(v) for k, v in (st.get("last_check") or {}).items()}
        self.last_event_t = st.get("last_event_t")
