#!/usr/bin/env python3
"""Judge-free longitudinal metrics from a replay run directory.

    python3 scripts/memory_replay/metrics.py <run_dir>

Reads transitions.jsonl, events.jsonl, weekly_raw.jsonl and (for relearn)
the encrypted texts.enc; writes scorecard.json and weekly.jsonl -- counts,
ids-free aggregates and shares only.

* retention     of memories ever active, share still active; by layer,
                project, domain group; losses by cause.
* relearn       share of NEW proposals whose tokens have >= 0.6 containment
                (|A & B| / min(|A|, |B|)) with a memory the run held and lost
                BEFORE the proposal.
* fairness      each project's / group's share of the active store vs its
                share of extract events (groups for REPORTING ONLY:
                ziya-* -> ziya, rest -> other; ZIYA_REPLAY_GROUPS adds
                groups as "name=prefix,prefix;name=..." matched on the
                project directory name).
* structure     node/root counts, largest-node share, max root tag count,
                per-node project-mix entropy, orphan rate, depth.
* promotion     proposal -> active latency in extract events and in domain
                sessions (distinct (conversation, day) in the proposal's
                project).
* volume        new proposals, corroborations, memories created, per 100
                extract events.
"""
from __future__ import annotations

import bisect
import collections
import datetime as dt
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

RELEARN_CONTAINMENT = 0.6
_TOK = re.compile(r"[a-z0-9_]{3,}")
_STOP = frozenset("the and for with that this from are was were have has not but you your its into when then than they them".split())


def _extra_groups() -> List[Tuple[str, Tuple[str, ...]]]:
    """Reporting groups from ZIYA_REPLAY_GROUPS: ``name=prefix,prefix;...``.
    Read per call so a test can set it with monkeypatch."""
    out: List[Tuple[str, Tuple[str, ...]]] = []
    for part in os.environ.get("ZIYA_REPLAY_GROUPS", "").split(";"):
        if "=" not in part:
            continue
        name, prefixes = part.split("=", 1)
        pre = tuple(x.strip() for x in prefixes.split(",") if x.strip())
        if name.strip() and pre:
            out.append((name.strip(), pre))
    return out


def group_of(project: Optional[str]) -> str:
    p = (project or "").strip()
    for name, prefixes in _extra_groups():
        if any(p.startswith(x) or p == x for x in prefixes):
            return name
    if p.lower().startswith("ziya"):
        return "ziya"
    return "other"


def tokens(text: str) -> Set[str]:
    return {t for t in _TOK.findall((text or "").lower()) if t not in _STOP}


def containment(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _read_jsonl(p: Path) -> List[Dict[str, Any]]:
    if not p.exists():
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


def _project_of_row(r: Dict[str, Any]) -> Optional[str]:
    paths = ((r.get("scope") or {}).get("project_paths") or [])
    return os.path.basename(str(paths[0]).rstrip("/")) if paths else None


def _entropy(counter: collections.Counter) -> float:
    n = sum(counter.values())
    return -sum((c / n) * math.log2(c / n) for c in counter.values() if c) if n else 0.0


def structure_snapshot(rows: List[Dict[str, Any]], nodes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Counts-only structure metrics of one store state."""
    active = [r for r in rows if (r.get("status") or "active") == "active"]
    aid = {r["id"] for r in active}
    proj = {r["id"]: _project_of_row(r) for r in active}
    nodes = {k: (v if isinstance(v, dict) else dict(v)) for k, v in (nodes or {}).items()}
    referenced: Set[str] = set()
    sizes, ent = [], []
    for n in nodes.values():
        refs = [m for m in (n.get("memory_refs") or []) if m in aid]
        referenced.update(refs)
        sizes.append(len(refs))
        if len(refs) >= 2:
            ent.append(_entropy(collections.Counter(proj.get(m) or "(none)" for m in refs)))
    roots = [n for n in nodes.values() if not n.get("parent")]

    def depth(nid: str) -> int:
        d, seen = 0, set()
        while nid and nid in nodes and nid not in seen:
            seen.add(nid)
            d += 1
            nid = nodes[nid].get("parent")
        return d

    by_layer = collections.Counter(r.get("layer") for r in active)
    by_proj = collections.Counter(proj.get(r["id"]) or "(none)" for r in active)
    return {
        "active": len(active),
        "archived": sum(1 for r in rows if r.get("status") == "archived"),
        "contested": sum(1 for r in rows if r.get("status") == "contested"),
        "nodes": len(nodes), "roots": len(roots),
        "largest_node_share": (max(sizes) / len(active)) if sizes and active else 0.0,
        "max_root_tags": max((len(r.get("tags") or []) for r in roots), default=0),
        "node_mix_entropy_mean": statistics.fmean(ent) if ent else 0.0,
        "node_mix_entropy_max": max(ent, default=0.0),
        "orphan_rate": (len(aid - referenced) / len(aid)) if aid else 0.0,
        "max_depth": max((depth(k) for k in nodes), default=0),
        "active_by_layer": dict(by_layer),
        "active_by_project": dict(by_proj),
        "active_by_group": dict(collections.Counter(group_of(p) for p in by_proj.elements())),
    }


def _pct(xs: List[float]) -> Dict[str, Any]:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "median": statistics.median(s), "mean": round(statistics.fmean(s), 2),
            "p90": s[min(len(s) - 1, int(0.9 * len(s)))], "max": s[-1]}


def _share(c: collections.Counter) -> Dict[str, float]:
    n = sum(c.values())
    return {k: round(v / n, 4) for k, v in c.most_common()} if n else {}


class Run:
    def __init__(self, run_dir: Path, with_text: bool = True) -> None:
        self.dir = Path(run_dir)
        self.trans = _read_jsonl(self.dir / "transitions.jsonl")
        self.events = _read_jsonl(self.dir / "events.jsonl")
        self.weekly_raw = _read_jsonl(self.dir / "weekly_raw.jsonl")
        self.texts: List[Dict[str, Any]] = []
        if with_text and (self.dir / "texts.enc").exists():
            from attribution import EncLog
            self.texts = EncLog.read(self.dir / "texts.enc")
        self.mem_trans = [t for t in self.trans if t.get("kind") == "memory"]
        self.prop_trans = [t for t in self.trans if t.get("kind") == "proposal"]
        self.extract = [e for e in self.events if e.get("kind") == "extract"]
        # prefix count of extract events by event index
        self._ex_idx = sorted(e["i"] for e in self.extract)

    # retention as of time T (exclusive)
    def retention(self, until: Optional[int] = None) -> Dict[str, Any]:
        state: Dict[str, str] = {}
        meta: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        ever: Set[str] = set()
        losses = collections.Counter()
        loss_rows = []
        for t in self.mem_trans:
            if until is not None and t["t"] >= until:
                break
            mid = t["id"]
            meta[mid] = (t.get("layer") or meta.get(mid, (None, None))[0],
                         t.get("project") or meta.get(mid, (None, None))[1])
            if t["to"] == "active":
                ever.add(mid)
            if t.get("from") == "active" and t["to"] != "active":
                losses[t["cause"]] += 1
                loss_rows.append(t)
            state[mid] = t["to"]
        still = {m for m in ever if state.get(m) == "active"}

        def by(fn):
            tot, keep = collections.Counter(), collections.Counter()
            for m in ever:
                k = fn(m)
                tot[k] += 1
                keep[k] += m in still
            return {k: {"ever": tot[k], "active": keep[k], "retention": round(keep[k] / tot[k], 4)}
                    for k in sorted(tot, key=lambda x: str(x))}

        final_cause = collections.Counter()
        last_loss = {}
        for t in loss_rows:
            last_loss[t["id"]] = t["cause"]
        for m in ever - still:
            final_cause[last_loss.get(m, "unknown")] += 1
        return {
            "ever_active": len(ever), "active": len(still),
            "retention": round(len(still) / len(ever), 4) if ever else None,
            "by_layer": by(lambda m: meta.get(m, (None, None))[0]),
            "by_project": by(lambda m: meta.get(m, (None, None))[1] or "(none)"),
            "by_group": by(lambda m: group_of(meta.get(m, (None, None))[1])),
            "loss_events_by_cause": dict(losses),
            "lost_now_by_cause": dict(final_cause),
            "_active_ids": still, "_meta": meta,
        }

    def relearn(self, until: Optional[int] = None) -> Dict[str, Any]:
        lost = [(x["t"], tokens(x.get("content", ""))) for x in self.texts if x.get("kind") == "lost"]
        lost.sort(key=lambda x: x[0])
        props = [x for x in self.texts if x.get("kind") == "proposal" and x.get("new")
                 and (until is None or x["t"] < until)]
        n = hits = 0
        for p in props:
            n += 1
            pt = tokens(p.get("content", ""))
            j = bisect.bisect_left([l[0] for l in lost], p["t"])
            if any(containment(pt, lt) >= RELEARN_CONTAINMENT for _, lt in lost[:j]):
                hits += 1
        return {"new_proposals": n, "relearned": hits, "relearn_rate": round(hits / n, 4) if n else None,
                "lost_texts": len(lost)}

    def fairness(self, active_ids: Set[str], meta: Dict[str, Tuple], until: Optional[int] = None) -> Dict[str, Any]:
        ev = collections.Counter(e.get("project") or "(none)" for e in self.extract
                                 if until is None or e["t"] < until)
        act = collections.Counter(meta.get(m, (None, None))[1] or "(none)" for m in active_ids)
        evg = collections.Counter()
        for k, v in ev.items():
            evg[group_of(k)] += v
        actg = collections.Counter()
        for k, v in act.items():
            actg[group_of(k)] += v

        def table(e, a):
            es, as_ = _share(e), _share(a)
            keys = sorted(set(es) | set(as_), key=lambda k: -e.get(k, 0))
            return {k: {"event_share": es.get(k, 0.0), "active_share": as_.get(k, 0.0),
                        "ratio": round(as_.get(k, 0.0) / es[k], 3) if es.get(k) else None} for k in keys}

        return {"by_group": table(evg, actg), "by_project": table(ev, act)}

    def promotion(self) -> Dict[str, Any]:
        first = {}
        for x in self.texts:
            if x.get("kind") == "proposal" and x.get("new") and x["id"] not in first:
                first[x["id"]] = x
        sessions: Dict[Optional[str], List[Tuple[int, Tuple[str, str]]]] = collections.defaultdict(list)
        for e in self.extract:
            day = dt.datetime.fromtimestamp(e["t"] / 1000).strftime("%Y-%m-%d")
            sessions[e.get("project")].append((e["t"], (e.get("chat") or "", day)))
        lat_ev, lat_sess, lat_h = [], [], []
        for t in self.prop_trans:
            if t["to"] != "promoted" or t["id"] not in first:
                continue
            p = first[t["id"]]
            a, b = p.get("ev", -1), t.get("ev", -1)
            lat_ev.append(bisect.bisect_right(self._ex_idx, b) - bisect.bisect_right(self._ex_idx, a))
            proj = p.get("project")
            seen = {s for (ts, s) in sessions.get(proj, []) if p["t"] < ts <= t["t"]}
            lat_sess.append(len(seen))
            lat_h.append(round((t["t"] - p["t"]) / 3600_000, 2))
        archived = collections.Counter(t["cause"] for t in self.prop_trans if t["to"] == "archived")
        return {"promoted": len(lat_ev), "archived_by_reason": dict(archived),
                "latency_extract_events": _pct(lat_ev), "latency_domain_sessions": _pct(lat_sess),
                "latency_hours": _pct(lat_h)}

    def volume(self, until: Optional[int] = None) -> Dict[str, Any]:
        props = [x for x in self.texts if x.get("kind") == "proposal" and (until is None or x["t"] < until)]
        new = sum(1 for x in props if x.get("new"))
        n_ex = sum(1 for e in self.extract if until is None or e["t"] < until)
        created = collections.Counter(t["cause"] for t in self.mem_trans
                                      if t.get("from") is None and (until is None or t["t"] < until))
        kinds = collections.Counter(e.get("kind") for e in self.events if until is None or e["t"] < until)
        ran = collections.Counter(j for e in self.events if e.get("kind") == "tick"
                                  and (until is None or e["t"] < until) for j in e.get("ran") or [])
        errors = collections.Counter(e["error"] for e in self.events if e.get("error"))
        return {"extract_events": n_ex, "events_by_kind": dict(kinds), "jobs_ran": dict(ran),
                "new_proposals": new, "corroborations": len(props) - new,
                "new_proposals_per_100_extract": round(100 * new / n_ex, 2) if n_ex else None,
                "memories_created_by_cause": dict(created), "event_errors": dict(errors)}


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def compute(run_dir: Path, write: bool = True) -> Dict[str, Any]:
    run = Run(Path(run_dir))
    weeks = []
    for i, snap in enumerate(run.weekly_raw):
        until = run.weekly_raw[i + 1]["week_start"] if i + 1 < len(run.weekly_raw) else None
        ret = run.retention(until)
        weeks.append({
            "week_start": dt.datetime.fromtimestamp(snap["week_start"] / 1000).strftime("%Y-%m-%d"),
            "retention": _clean(ret), "relearn": run.relearn(until),
            "fairness": run.fairness(ret["_active_ids"], ret["_meta"], until),
            "structure": {k: v for k, v in snap.items() if k not in ("week_start", "t")},
            "volume": run.volume(until),
        })
    ret = run.retention()
    final = {
        "retention": _clean(ret), "relearn": run.relearn(),
        "fairness": run.fairness(ret["_active_ids"], ret["_meta"]),
        "structure": ({k: v for k, v in run.weekly_raw[-1].items() if k not in ("week_start", "t")}
                      if run.weekly_raw else {}),
        "promotion": run.promotion(), "volume": run.volume(),
    }
    out = {"run": Path(run_dir).name, "weeks": len(weeks), "final": final}
    if write:
        (Path(run_dir) / "scorecard.json").write_text(json.dumps(out, indent=1, sort_keys=True))
        with open(Path(run_dir) / "weekly.jsonl", "w") as f:
            for w in weeks:
                f.write(json.dumps(w, sort_keys=True) + "\n")
    return out


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    print(json.dumps(compute(Path(sys.argv[1]))["final"], indent=1))
