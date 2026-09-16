#!/usr/bin/env python3
"""
gfx_ledger — the single writer for the GFX sweep evidence under .ziya/gfx-sweep/.

Why this exists
---------------
The Stage 1/2 task cards let each agent ``file_write`` whole shared JSON files
(per-engine blackboards, backlog.json).  Every agent re-serialised the file
from whatever it held in context, so:
  * a wave agent that lost a read-modify-write race dropped other waves'
    verdicts (mermaid.json ended at 45 of 60 specs, with orphan sidecars);
  * a consolidation pass with nothing in context wrote ``{"groups": []}``
    over a populated backlog;
  * a re-sweep replaced the prior verdict, so "did the fix work" had no
    before/after to compare.

Every mutation now goes through this script.  It takes small payloads,
file-locks, snapshots the ledger before writing, appends rather than
replaces, and refuses any write that would lose an existing record.

Layout (all under --root, default .ziya/gfx-sweep):
  <engine>.json        blackboard: specs[] each with history[] and current
  triage/<engine>.json per-engine triage written by the sweep (read here)
  backlog.json         defects keyed by stable id; status ledger; never shrinks
  retired.json         human decisions: ids/signatures excluded from the queue
  history/<ts>/        pre-write snapshot of every ledger touched
  renders/<run>/       FAILING renders passed to `record --*-png` (evidence)
  drift/<run>/         golden-tier drift report + new renders + heatmaps
                       (written by tests/gfx_render, read by the drift judge)
  ../gfx-golden/       machine-local goldens: <engine>/<spec>.<theme>.png +
                       sidecar with trust (provisional|validated); see the
                       "goldens" section below

Subcommands: record, migrate, merge-triage, set-status, retire, unretire,
             queue, snapshot, show.  Run with -h for details.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_ROOT = ".ziya/gfx-sweep"
THEMES = ("light", "dark")

# Legal status transitions for a defect.  ``set-status`` refuses anything
# else so a stale agent cannot, e.g., flip a verified defect back to open
# without going through ``regression``.
STATUSES = {
    "open", "deferred", "fix-applied", "verified", "still-broken",
    "wont-fix", "regression",
}
TRANSITIONS = {
    "open": {"deferred", "fix-applied", "wont-fix", "still-broken"},
    "deferred": {"open", "wont-fix"},
    "fix-applied": {"verified", "still-broken", "wont-fix"},
    "still-broken": {"fix-applied", "wont-fix", "open"},
    "verified": {"regression"},
    "wont-fix": {"open"},
    "regression": {"fix-applied", "wont-fix", "open"},
}
# Statuses that keep a defect OUT of the work queue.
QUEUE_EXCLUDED = {"deferred", "verified", "wont-fix"}


class LedgerError(Exception):
    pass


# ── primitives ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    # Microseconds: reconcile orders a fix-applied event against verdicts
    # recorded after it by comparing these strings, and two ledger writes
    # inside one second must not tie.  Lexical order == chronological order
    # for this fixed-width UTC form.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ts_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked(root: Path):
    """One advisory lock for the whole sweep directory.

    Coarse on purpose: parallel wave agents (max_concurrency=3) touch
    different engine files, but merge-triage touches all of them, and a
    single lock is simpler to reason about than per-file locks plus a
    global one.  Writes are small; contention is measured in milliseconds.
    """
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".ledger.lock"
    with lock_path.open("a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def snapshot(root: Path, files: Iterable[Path], label: Optional[str] = None) -> Optional[Path]:
    """Copy each existing file into history/<ts>/ preserving relative path."""
    files = [p for p in files if p.exists()]
    if not files:
        return None
    dest = root / "history" / (label or _ts_label())
    for p in files:
        rel = p.relative_to(root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    return dest


# ── blackboard (per-engine) ───────────────────────────────────────────────

def _blackboard_path(root: Path, engine: str) -> Path:
    return root / f"{engine}.json"


def _empty_blackboard(engine: str) -> Dict[str, Any]:
    return {"engine": engine, "schema": 2, "specs": [], "lessons": []}


def _verdict_shape(v: Any) -> Dict[str, Any]:
    """Normalise a theme verdict; tolerate partial input from agents."""
    if not isinstance(v, dict):
        raise LedgerError(f"theme verdict must be an object, got {type(v).__name__}")
    status = v.get("status")
    if status not in ("ok", "fail"):
        raise LedgerError(f"verdict.status must be 'ok' or 'fail', got {status!r}")
    out = {
        "status": status,
        "signature": v.get("signature") or "",
        "detail": v.get("detail") or "",
    }
    if "contrast" in v:
        out["contrast"] = v["contrast"]
    return out


def record(root: Path, engine: str, spec_id: str, *, run: str,
           light: Dict[str, Any], dark: Dict[str, Any],
           wave: Optional[int] = None, intent: Optional[str] = None,
           light_png: Optional[Path] = None, dark_png: Optional[Path] = None,
           corpus: Optional[Path] = None) -> Dict[str, Any]:
    """Append one (light, dark) verdict pair to a spec's history.

    Never removes any other spec.  If the spec is new it is created; if
    it exists, ``wave``/``intent`` are only filled when previously absent
    so a re-sweep cannot silently rewrite what a spec was FOR.

    ``light_png``/``dark_png`` are the images the verdicts were judged ON.
    A theme judged "ok" with its image attached becomes that theme's
    VALIDATED golden — the judge and the baseline saw the same bytes, which
    is the whole point.  A "fail" image is kept under renders/<run>/ as
    evidence and never touches the golden.
    """
    pngs = {"light": light_png, "dark": dark_png}
    for theme, p in pngs.items():
        if p is not None and not Path(p).exists():
            raise LedgerError(f"--{theme}-png {p}: no such file")
    path = _blackboard_path(root, engine)
    with _locked(root):
        bb = _read(path) or _empty_blackboard(engine)
        bb = _ensure_schema2(bb, engine)
        entry = {
            "run": run,
            "at": _now_iso(),
            "light": _verdict_shape(light),
            "dark": _verdict_shape(dark),
        }
        spec = next((s for s in bb["specs"] if s.get("id") == spec_id), None)
        if spec is None:
            spec = {"id": spec_id, "wave": wave, "intent": intent or "", "history": []}
            bb["specs"].append(spec)
        else:
            if spec.get("wave") is None and wave is not None:
                spec["wave"] = wave
            if not spec.get("intent") and intent:
                spec["intent"] = intent
        spec["history"].append(entry)
        spec["current"] = {"run": run, "at": entry["at"],
                           "light": entry["light"], "dark": entry["dark"]}
        snapshot(root, [path])
        _atomic_write(path, bb)
    # Golden handling sits OUTSIDE the blackboard lock: golden_capture takes
    # its own lock on gfx-golden/, and flock is not re-entrant across two
    # descriptors in one process.
    goldens: Dict[str, Any] = {}
    for theme, p in pngs.items():
        if p is None:
            continue
        data = Path(p).read_bytes()
        if entry[theme]["status"] == "ok":
            g = golden_capture(root, engine, spec_id, theme, data, trust="validated",
                               run=run, source="record", corpus=corpus)
            goldens[theme] = {"trust": g["trust"], "hash": g["hash"],
                              "in_corpus": g.get("in_corpus", False)}
        else:
            dst = _store_render_evidence(root, engine, spec_id, theme, data, run=run)
            goldens[theme] = {"evidence": str(dst)}
    if goldens:
        spec = dict(spec)
        spec["goldens"] = goldens
    return spec


def _ensure_schema2(bb: Dict[str, Any], engine: str) -> Dict[str, Any]:
    """Promote a schema-1 blackboard (one verdict per spec) to schema 2 in
    memory.  Idempotent.  Callers persist the result."""
    if bb.get("schema") == 2:
        return bb
    return migrate_blackboard(bb, engine, run_label="legacy")


def migrate_blackboard(bb: Dict[str, Any], engine: str, *, run_label: str) -> Dict[str, Any]:
    """Schema 1 → 2: each spec's flat light/dark become history[0] + current.

    Anything the old shape carried that we do not model (probes,
    specs_not_reached, _metric_notes, ...) is preserved under ``extra``
    rather than dropped — the migration must never be the thing that
    loses evidence.
    """
    out: Dict[str, Any] = {"engine": bb.get("engine") or engine, "schema": 2,
                           "specs": [], "lessons": list(bb.get("lessons") or [])}
    extra = {k: v for k, v in bb.items()
             if k not in ("engine", "specs", "lessons", "schema")}
    if extra:
        out["extra"] = extra
    for s in bb.get("specs") or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        if "history" in s:  # already migrated
            out["specs"].append(s)
            continue
        light, dark = s.get("light"), s.get("dark")
        spec = {"id": s["id"], "wave": s.get("wave"), "intent": s.get("intent") or "",
                "history": []}
        carried = {k: v for k, v in s.items()
                   if k not in ("id", "wave", "intent", "light", "dark")}
        if carried:
            spec["extra"] = carried
        if isinstance(light, dict) and isinstance(dark, dict):
            try:
                entry = {"run": run_label, "at": None,
                         "light": _verdict_shape(light), "dark": _verdict_shape(dark)}
                spec["history"].append(entry)
                spec["current"] = dict(entry)
            except LedgerError:
                spec.setdefault("extra", {})["unparsed_verdict"] = {"light": light, "dark": dark}
        out["specs"].append(spec)
    return out


def migrate(root: Path, *, run_label: str, engines: Optional[List[str]] = None) -> Dict[str, int]:
    """Migrate every schema-1 blackboard on disk.  Folds any
    ``<engine>-wave*.json`` sidecar into the main file for specs the main
    file does not have (a sidecar is a wave agent's orphaned write)."""
    counts: Dict[str, int] = {}
    with _locked(root):
        files = sorted(root.glob("*.json"))
        main = {p.stem: p for p in files
                if p.stem not in ("backlog", "retired", "inventory", "report-data")
                and "-wave" not in p.stem and not p.stem.startswith("w")}
        if engines:
            main = {k: v for k, v in main.items() if k in engines}
        to_snapshot = list(main.values())
        for eng, p in main.items():
            to_snapshot += list(root.glob(f"{eng}-wave*.json"))
        snapshot(root, to_snapshot, label=f"pre-migrate-{_ts_label()}")
        for eng, p in main.items():
            bb = _read(p)
            if not isinstance(bb, dict):
                continue
            was_v2 = bb.get("schema") == 2
            bb = migrate_blackboard(bb, eng, run_label=run_label)
            have = {s["id"] for s in bb["specs"]}
            folded = 0
            for side in sorted(root.glob(f"{eng}-wave*.json")):
                sb = _read(side)
                if not isinstance(sb, dict):
                    continue
                sb2 = migrate_blackboard(sb, eng, run_label=f"{run_label}:{side.stem}")
                for s in sb2["specs"]:
                    if s["id"] not in have:
                        bb["specs"].append(s); have.add(s["id"]); folded += 1
                bb.setdefault("lessons", []).extend(
                    l for l in sb2.get("lessons", []) if l not in bb["lessons"])
                bb.setdefault("extra", {}).setdefault("folded_sidecars", []).append(side.name)
            bb["specs"].sort(key=lambda s: s["id"])
            if not was_v2 or folded:
                _atomic_write(p, bb)
            counts[eng] = len(bb["specs"])
    return counts


# ── backlog (defects) ─────────────────────────────────────────────────────

def _backlog_path(root: Path) -> Path:
    return root / "backlog.json"


def _retired_path(root: Path) -> Path:
    return root / "retired.json"


def _empty_backlog() -> Dict[str, Any]:
    return {"schema": 2, "defects": [], "groups": [], "regression_sets": {},
            "facts": {}, "merges": []}


def _load_backlog(root: Path) -> Dict[str, Any]:
    b = _read(_backlog_path(root))
    if not isinstance(b, dict):
        return _empty_backlog()
    b.setdefault("schema", 2)
    for k, v in _empty_backlog().items():
        b.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())
    return b


def _load_retired(root: Path) -> Dict[str, Any]:
    r = _read(_retired_path(root))
    if not isinstance(r, dict):
        return {"schema": 1, "defect_ids": {}, "signatures": {}}
    r.setdefault("defect_ids", {}); r.setdefault("signatures", {})
    return r


def _next_defect_id(defects: List[Dict[str, Any]]) -> str:
    n = 0
    for d in defects:
        try:
            n = max(n, int(str(d.get("id", "D-0")).split("-")[1]))
        except (IndexError, ValueError):
            pass
    return f"D-{n + 1:03d}"


_PATH_RE = None


def suspect_paths(entries: Iterable[Any]) -> set:
    """Reduce free-text suspect_files entries to bare source paths.

    Triage agents write things like
      "frontend/src/plugins/d3/vegaPlugin.ts (postRenderSizing() ~L1133)"
      "app/services/diagram_renderer.py :: compute_capture_fit, CAPTURE_MAX=6000"
    which never compare equal, so grouping by verbatim overlap yields one
    group per defect.  Every path-looking token in the entry is extracted;
    an entry with none is kept verbatim so it still participates.
    """
    global _PATH_RE
    if _PATH_RE is None:
        import re
        _PATH_RE = re.compile(r"[A-Za-z0-9_./-]+\.(?:tsx?|jsx?|py|css|scss|json|html|tex)\b")
    out: set = set()
    for e in entries or []:
        if not isinstance(e, str):
            continue
        found = _PATH_RE.findall(e)
        out.update(found if found else [e.strip()])
    return out


def _cluster_matches(cluster: Dict[str, Any], defect: Dict[str, Any], engine: str) -> bool:
    """Same signature AND (already lists this engine OR shares a suspect file).

    Signature equality alone is too loose across engines (two engines can
    coin the same label for unrelated bugs), so demand a second witness.
    """
    if (cluster.get("signature") or "") != (defect.get("signature") or ""):
        return False
    if engine in (defect.get("engines") or []):
        return True
    return bool(suspect_paths(cluster.get("suspect_files")) & suspect_paths(defect.get("suspect_files")))


def merge_triage(root: Path, *, run: str) -> Dict[str, Any]:
    """Fold triage/<engine>.json into backlog.json without losing anything.

    * A cluster matching an existing defect UPDATES that defect's engines,
      spec_ids, themes and suspect_files (union) and leaves status,
      attempts, verification, notes and history untouched.
    * A cluster matching a VERIFIED defect flips it to 'regression' —
      the fix did not hold — rather than reopening silently.
    * A cluster matching a RETIRED signature is recorded under
      ``facts.retired_reappearances`` and NOT added to the queue.
    * Otherwise a new defect is minted with status 'open' (or 'deferred'
      if severity is low).
    * Refuses to write if the result has fewer defect ids than before.
    """
    tri_dir = root / "triage"
    with _locked(root):
        backlog = _load_backlog(root)
        retired = _load_retired(root)
        before_ids = {d["id"] for d in backlog["defects"]}
        stats = {"clusters": 0, "matched": 0, "new": 0, "regressions": 0,
                 "retired_reappearances": 0, "deferred": 0, "engines": 0}
        for tpath in sorted(tri_dir.glob("*.json")) if tri_dir.exists() else []:
            tri = _read(tpath)
            if not isinstance(tri, dict):
                continue
            engine = tri.get("engine") or tpath.stem
            stats["engines"] += 1
            rs = tri.get("regression_set")
            if isinstance(rs, dict):
                rs = rs.get("spec_ids")
            if isinstance(rs, list):
                # union, so a later sweep can only grow the safety net
                cur = set(backlog["regression_sets"].get(engine, []))
                backlog["regression_sets"][engine] = sorted(cur | set(rs))
            for cl in tri.get("clusters") or []:
                if not isinstance(cl, dict):
                    continue
                stats["clusters"] += 1
                sig = cl.get("signature") or ""
                if sig in retired["signatures"]:
                    stats["retired_reappearances"] += 1
                    backlog["facts"].setdefault("retired_reappearances", []).append(
                        {"run": run, "engine": engine, "signature": sig,
                         "spec_ids": cl.get("spec_ids") or []})
                    continue
                match = next((d for d in backlog["defects"]
                              if _cluster_matches(cl, d, engine)), None)
                if match is None:
                    sev = cl.get("severity") or "medium"
                    d = {
                        "id": _next_defect_id(backlog["defects"]),
                        "kind": cl.get("kind") or "structural",
                        "engines": [engine],
                        "themes_affected": sorted(set(cl.get("themes_affected") or [])),
                        "signature": sig,
                        "spec_ids": {engine: sorted(set(cl.get("spec_ids") or []))},
                        "hypothesis": cl.get("hypothesis") or "",
                        "suspect_files": sorted(set(cl.get("suspect_files") or [])),
                        "severity": sev,
                        "confidence": cl.get("confidence"),
                        "worst_measured_ratio": cl.get("worst_measured_ratio"),
                        "status": "deferred" if sev == "low" else "open",
                        "attempts": 0,
                        "first_seen": run,
                        "last_seen": run,
                        "verification": {},
                        "history": [{"at": _now_iso(), "run": run, "event": "created",
                                     "status": "deferred" if sev == "low" else "open"}],
                    }
                    if d["id"] in retired["defect_ids"]:
                        # id collision with a retired record would be a bug
                        raise LedgerError(f"minted id {d['id']} collides with retired id")
                    backlog["defects"].append(d)
                    stats["new"] += 1
                    if sev == "low":
                        stats["deferred"] += 1
                else:
                    stats["matched"] += 1
                    if engine not in match["engines"]:
                        match["engines"].append(engine)
                    ids = set(match["spec_ids"].get(engine, [])) | set(cl.get("spec_ids") or [])
                    match["spec_ids"][engine] = sorted(ids)
                    match["themes_affected"] = sorted(
                        set(match.get("themes_affected") or []) | set(cl.get("themes_affected") or []))
                    match["suspect_files"] = sorted(
                        set(match.get("suspect_files") or []) | set(cl.get("suspect_files") or []))
                    match["last_seen"] = run
                    if match.get("status") == "verified":
                        match["status"] = "regression"
                        match["history"].append({"at": _now_iso(), "run": run,
                                                 "event": "reappeared-after-verified",
                                                 "status": "regression", "engine": engine})
                        stats["regressions"] += 1
        after_ids = {d["id"] for d in backlog["defects"]}
        if not before_ids <= after_ids:
            raise LedgerError(f"merge would drop defect ids {sorted(before_ids - after_ids)}")
        backlog["merges"].append({"at": _now_iso(), "run": run, **stats})
        snapshot(root, [_backlog_path(root)])
        _atomic_write(_backlog_path(root), backlog)
        return stats


def set_status(root: Path, defect_id: str, status: str, *, run: str,
               note: str = "", files_touched: Optional[List[str]] = None,
               test_added: Optional[str] = None,
               verification: Optional[Dict[str, Any]] = None,
               bump_attempts: bool = False) -> Dict[str, Any]:
    if status not in STATUSES:
        raise LedgerError(f"unknown status {status!r}; one of {sorted(STATUSES)}")
    with _locked(root):
        backlog = _load_backlog(root)
        d = next((x for x in backlog["defects"] if x.get("id") == defect_id), None)
        if d is None:
            raise LedgerError(f"no defect {defect_id}")
        cur = d.get("status", "open")
        if status != cur and status not in TRANSITIONS.get(cur, set()):
            raise LedgerError(f"illegal transition {cur} -> {status} for {defect_id}")
        ev: Dict[str, Any] = {"at": _now_iso(), "run": run, "event": "status",
                              "from": cur, "status": status}
        if note:
            ev["note"] = note
        if files_touched:
            ev["files_touched"] = files_touched
            d.setdefault("files_touched", [])
            d["files_touched"] = sorted(set(d["files_touched"]) | set(files_touched))
        if test_added:
            ev["test_added"] = test_added
            d.setdefault("tests", [])
            if test_added not in d["tests"]:
                d["tests"].append(test_added)
        if verification:
            # verification["<engine>"] = {"light": ok|fail, "dark": ok|fail, "detail"}
            d.setdefault("verification", {}).update(verification)
            ev["verification"] = verification
        if bump_attempts:
            d["attempts"] = int(d.get("attempts", 0)) + 1
        d["status"] = status
        d.setdefault("history", []).append(ev)
        paths = [_backlog_path(root)]
        if status == "wont-fix":
            # System retirement: a declined defect must not be re-minted by
            # the next sweep as a fresh 'open' with attempts=0.  Its
            # signature goes to retired.json so merge-triage logs the
            # reappearance instead.  Nothing for a human to do.
            retired = _load_retired(root)
            rec = {"at": _now_iso(), "by": "system", "run": run,
                   "reason": note or "wont-fix", "defect_id": defect_id}
            retired["defect_ids"][defect_id] = {**rec, "signature": d.get("signature")}
            if d.get("signature"):
                retired["signatures"][d["signature"]] = rec
            paths.append(_retired_path(root))
            snapshot(root, paths)
            _atomic_write(_retired_path(root), retired)
        else:
            snapshot(root, paths)
        _atomic_write(_backlog_path(root), backlog)
        return d


def _spec_verdict(bb: Dict[str, Any], spec_id: str) -> Optional[Dict[str, Any]]:
    for s in bb.get("specs", []):
        if s.get("id") == spec_id:
            return s.get("current")
    return None


def reconcile(root: Path, *, run: str) -> Dict[str, Any]:
    """Derive defect status from render evidence — the system-side close.

    For every defect that is not deferred or wont-fix, read the CURRENT
    verdict of each spec it names on each engine it affects:

    * all named specs present and ok in BOTH themes on every engine ->
      'verified' (verification[] filled from the evidence).  This is what
      removes fixed work from the queue; no agent has to remember to
      say so, it just has to re-render and ``record``.
    * a 'verified' defect with any named spec now failing in either
      theme -> 'regression' (re-queued, ranked first).  A verified defect
      had all-ok evidence when it closed, so a fail on a current verdict is
      by construction newer evidence.
    * a 'fix-applied' defect whose named specs ALL carry a verdict
      recorded AFTER the fix-applied event, with any failing ->
      'still-broken'.  The timestamp guard is what stops the sweep that
      found the defect from "disproving" a fix that has not been
      rendered yet.
    * anything with a spec that has no verdict yet is left alone —
      absence of evidence is not a pass.

    Idempotent: re-running on the same evidence changes nothing.
    """
    with _locked(root):
        backlog = _load_backlog(root)
        boards: Dict[str, Dict[str, Any]] = {}
        stats = {"checked": 0, "verified": 0, "regressed": 0, "still_broken": 0,
                 "incomplete": 0, "unchanged": 0}
        for d in backlog["defects"]:
            st = d.get("status", "open")
            if st in ("deferred", "wont-fix"):
                continue
            stats["checked"] += 1
            evidence: Dict[str, Dict[str, Any]] = {}
            incomplete = False
            any_fail = False
            # When was the fix applied?  Verdicts at or before this cannot
            # speak to it.  Empty string sorts before every ISO timestamp.
            fix_at = max((h.get("at") or "" for h in d.get("history", [])
                          if h.get("status") == "fix-applied"), default="")
            all_after_fix = True
            for engine, ids in (d.get("spec_ids") or {}).items():
                if engine not in boards:
                    boards[engine] = _read(_blackboard_path(root, engine)) or {}
                light_ok = dark_ok = True
                failing: List[str] = []
                newest_run = None
                for sid in ids or []:
                    cur = _spec_verdict(boards[engine], sid)
                    if not cur:
                        incomplete = True
                        continue
                    newest_run = cur.get("run") or newest_run
                    if (cur.get("at") or "") <= fix_at:
                        all_after_fix = False
                    lo = (cur.get("light") or {}).get("status") == "ok"
                    do = (cur.get("dark") or {}).get("status") == "ok"
                    light_ok &= lo
                    dark_ok &= do
                    if not (lo and do):
                        failing.append(sid)
                evidence[engine] = {
                    "light": "ok" if light_ok else "fail",
                    "dark": "ok" if dark_ok else "fail",
                    "detail": ("all named specs ok in both themes" if not failing
                               else f"failing: {', '.join(failing)}"),
                    "evidence_run": newest_run,
                }
                any_fail |= bool(failing)
            if incomplete:
                stats["incomplete"] += 1
                continue
            if not evidence:
                stats["unchanged"] += 1
                continue
            ev = {"at": _now_iso(), "run": run, "event": "reconciled",
                  "from": st, "verification": evidence}
            if not any_fail and st != "verified":
                d["status"] = "verified"
                d["verification"] = evidence
                ev["status"] = "verified"
                d.setdefault("history", []).append(ev)
                stats["verified"] += 1
                # The moment a defect is known-fixed is the moment its specs
                # become regression tests: promote into the committed corpus
                # so tests/gfx_render/ guards the fix on every code change,
                # not only on the next task-card run.  Best-effort — a
                # corpus write failure must not block the ledger update.
                try:
                    ev["promoted_specs"] = promote_defect(root, d)
                    stats["promoted"] = stats.get("promoted", 0) + ev["promoted_specs"]
                except Exception as exc:  # noqa: BLE001
                    ev["promote_error"] = str(exc)
            elif any_fail and st == "verified":
                d["status"] = "regression"
                d["severity"] = "high"
                d["verification"] = evidence
                ev["status"] = "regression"
                d.setdefault("history", []).append(ev)
                stats["regressed"] += 1
            elif any_fail and st == "fix-applied" and fix_at and all_after_fix:
                d["status"] = "still-broken"
                d["verification"] = evidence
                ev["status"] = "still-broken"
                d.setdefault("history", []).append(ev)
                stats["still_broken"] += 1
            else:
                stats["unchanged"] += 1
        snapshot(root, [_backlog_path(root)])
        _atomic_write(_backlog_path(root), backlog)
        return stats


# ── Regression corpus promotion ───────────────────────────────────────────
# The GFX sweep's evidence lives under .ziya/ (gitignored) and is judged by a
# model.  ``promote`` copies the specs that matter into a COMMITTED corpus at
# tests/gfx_corpus/, with per-spec expectations derived from the defect's
# signature, so tests/gfx_render/ can re-render them deterministically on
# every code change without a task-card run.  Called automatically when
# reconcile flips a defect to verified (the moment "this used to be broken,
# now it renders" is known), and by hand for the Stage 1 regression sets.

CORPUS_DIRNAME = "gfx_corpus"
# Invariants the render suite understands.  Keep this list in sync with
# tests/gfx_render/test_render_smoke.py::CHECKS.
INVARIANT_RENDERS = "renders"            # terminal 'complete', no timeout/error
INVARIANT_NO_CONSOLE_ERRORS = "no_console_errors"
INVARIANT_INK = "ink_present"            # PNG is not blank
DEFAULT_INVARIANTS = [INVARIANT_RENDERS, INVARIANT_NO_CONSOLE_ERRORS, INVARIANT_INK]


def corpus_root(root: Path) -> Path:
    """tests/gfx_corpus/ relative to the repo that owns ``root`` (.ziya/gfx-sweep)."""
    return root.parent.parent / "tests" / CORPUS_DIRNAME


def invariants_for_signature(signature: str) -> List[str]:
    """Map a defect signature to the render-suite invariants it implies.

    Every promoted spec gets the default set.  Signatures whose cause is a
    missing/blank/timed-out render need nothing more — those ARE the
    defaults.  Contrast, collision and sizing signatures are recorded as
    ``visual:*`` tags so a later measured tier can pick them up; the smoke
    tier ignores tags it does not know rather than failing on them.
    """
    inv = list(DEFAULT_INVARIANTS)
    s = (signature or "").lower()
    if any(k in s for k in ("contrast", "invisible", "canvas-aware", "hardcoded",
                            "palette", "background", "foreground", "ink")):
        inv.append("visual:contrast")
    if any(k in s for k in ("collide", "overlap", "truncat", "clipped", "crop", "lost")):
        inv.append("visual:layout")
    if any(k in s for k in ("subpixel", "illegib", "shrunk", "thumbnail")):
        inv.append("visual:min_text_size")
    return inv


def promote(root: Path, *, engine: str, spec_id: str, origin: str, signature: str = "",
            themes: Iterable[str] = ("light", "dark"),
            corpus: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Copy one spec into the committed corpus with expectations.

    Idempotent: an existing corpus entry keeps its spec bytes (the spec is
    the thing under test; a sweep must not silently rewrite it) and only
    gains new origins/invariants.  Returns the expectations record, or None
    when the spec file does not exist on disk (nothing to promote).
    """
    src = root / "specs" / engine / f"{spec_id}.json"
    spec = _read(src)
    if not isinstance(spec, dict):
        return None
    corpus = corpus or corpus_root(root)
    eng_dir = corpus / engine
    eng_dir.mkdir(parents=True, exist_ok=True)
    spec_dst = eng_dir / f"{spec_id}.json"
    if not spec_dst.exists():
        _atomic_write(spec_dst, {"type": spec.get("type", engine),
                                 "definition": spec.get("definition"),
                                 "intent": spec.get("intent", "")})
    exp_path = eng_dir / "expectations.json"
    exp = _read(exp_path)
    if not isinstance(exp, dict):
        exp = {"schema": 1, "engine": engine, "specs": {}}
    rec = exp["specs"].setdefault(spec_id, {
        "themes": sorted(set(themes)), "invariants": [], "origins": [],
    })
    rec["themes"] = sorted(set(rec.get("themes", [])) | set(themes))
    for i in invariants_for_signature(signature):
        if i not in rec["invariants"]:
            rec["invariants"].append(i)
    origin_rec = {"origin": origin, "signature": signature, "at": _now_iso()}
    if not any(o.get("origin") == origin and o.get("signature") == signature
               for o in rec["origins"]):
        rec["origins"].append(origin_rec)
    # Carry a validated golden's hash into the committed record so another
    # machine can recognise the judged image.  Provisional goldens are not
    # committed: they assert nothing.
    for theme in rec["themes"]:
        g = golden_get(root, engine, spec_id, theme)
        if g and g.get("trust") == "validated":
            rec.setdefault("golden", {})[theme] = {"hash": g["hash"],
                                                   "validated_at": g.get("at")}
    _atomic_write(exp_path, exp)
    return rec


def promote_defect(root: Path, defect: Dict[str, Any],
                   corpus: Optional[Path] = None) -> int:
    """Promote every spec a defect names, on every engine it lists."""
    n = 0
    for engine, ids in (defect.get("spec_ids") or {}).items():
        for sid in ids:
            if promote(root, engine=engine, spec_id=sid, origin=defect.get("id", "?"),
                       signature=defect.get("signature", ""), corpus=corpus):
                n += 1
    return n


def promote_regression_sets(root: Path, corpus: Optional[Path] = None) -> Dict[str, int]:
    """Seed the corpus with the Stage 1 both-theme-passing regression sets.

    These carry only the default invariants: they were never broken, so
    there is no signature to derive a visual tag from — the point is that
    they keep rendering.
    """
    backlog = _load_backlog(root)
    out: Dict[str, int] = {}
    for engine, ids in (backlog.get("regression_sets") or {}).items():
        out[engine] = sum(
            1 for sid in ids
            if promote(root, engine=engine, spec_id=sid, origin="regression_set",
                       corpus=corpus)
        )
    return out


def promote_verified(root: Path, corpus: Optional[Path] = None) -> Dict[str, int]:
    """Promote every currently-verified defect's specs (one-time backfill)."""
    backlog = _load_backlog(root)
    stats = {"defects": 0, "specs": 0}
    for d in backlog["defects"]:
        if d.get("status") == "verified":
            stats["defects"] += 1
            stats["specs"] += promote_defect(root, d, corpus=corpus)
    return stats


# ── goldens (machine-local validated renders) ─────────────────────────────
#
# A golden is the PNG a spec rendered to in one theme, plus a sidecar that
# says how far it can be trusted:
#
#   provisional  the render suite captured it because none existed.  It
#                asserts nothing about correctness — only that later
#                renders can be compared against it (STABLE / CHANGED).
#   validated    a judge looked at THESE bytes and recorded status "ok"
#                (Stage 1/2 `record --light-png/--dark-png`), or the bytes
#                hash-match a validated render committed in the corpus, or a
#                human ran `golden rebaseline` with a reason.
#
# Goldens live under .ziya/gfx-golden/ (gitignored: ~1.5k PNGs, and the
# pixels are Chromium-build-specific).  What IS committed is the pixel hash
# of each validated golden, in tests/gfx_corpus/<engine>/expectations.json,
# so a fresh clone can tell "you rendered something other than the image a
# judge approved" — a hint, not a failure — and can trust a local render
# that hashes identically (identical pixels to a judged-good image ARE
# judged-good).  Trust only ever moves through the functions below.

GOLDEN_DIRNAME = "gfx-golden"
GOLDEN_TRUST = ("provisional", "validated")


def golden_root(root: Path) -> Path:
    """.ziya/gfx-golden/ beside the sweep root (.ziya/gfx-sweep/)."""
    return root.parent / GOLDEN_DIRNAME


def golden_paths(root: Path, engine: str, spec_id: str, theme: str) -> Tuple[Path, Path]:
    """(png, sidecar) for one spec/theme golden."""
    if theme not in THEMES:
        raise LedgerError(f"theme must be one of {THEMES}, got {theme!r}")
    d = golden_root(root) / engine
    return d / f"{spec_id}.{theme}.png", d / f"{spec_id}.{theme}.json"


def pixel_hash(png_bytes: bytes) -> str:
    """sha256 over the decoded RGBA buffer plus its dimensions.

    Decoded pixels rather than file bytes: two PNG encodings of the same
    image (different compression level, different chunk order) must hash
    equal, and two images that differ by one pixel must not.
    """
    import hashlib
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    h = hashlib.sha256()
    h.update(f"{im.width}x{im.height}:".encode())
    h.update(im.tobytes())
    return h.hexdigest()


def golden_get(root: Path, engine: str, spec_id: str, theme: str) -> Optional[Dict[str, Any]]:
    """The sidecar for a golden, or None when no golden (png+sidecar) exists."""
    png, side = golden_paths(root, engine, spec_id, theme)
    if not (png.exists() and side.exists()):
        return None
    rec = _read(side)
    return rec if isinstance(rec, dict) else None


def _expectations_path(corpus: Path, engine: str) -> Path:
    return corpus / engine / "expectations.json"


def _sync_expectations_golden(root: Path, engine: str, spec_id: str, theme: str,
                              digest: str, *, corpus: Optional[Path] = None) -> bool:
    """Record a validated golden's hash in the committed corpus, if the spec is there.

    Returns False when the spec is not in the corpus (nothing to sync);
    the golden is still valid locally.
    """
    corpus = corpus or corpus_root(root)
    exp_path = _expectations_path(corpus, engine)
    exp = _read(exp_path)
    if not isinstance(exp, dict) or spec_id not in exp.get("specs", {}):
        return False
    rec = exp["specs"][spec_id]
    g = rec.setdefault("golden", {})
    g[theme] = {"hash": digest, "validated_at": _now_iso()}
    _atomic_write(exp_path, exp)
    return True


def golden_capture(root: Path, engine: str, spec_id: str, theme: str, png_bytes: bytes, *,
                   trust: str, run: str, source: str, note: str = "",
                   corpus: Optional[Path] = None) -> Dict[str, Any]:
    """Store a render as the golden for (engine, spec, theme).

    Trust may only stay level or rise here: a provisional capture never
    replaces a validated golden (the render suite must not silently move a
    judged baseline), and a validated capture replaces anything.  The
    previous png/sidecar go to gfx-golden/history/<ts>/ so a rebaseline is
    reversible.  A validated capture also writes its hash into the corpus
    expectations when the spec is committed there.
    """
    if trust not in GOLDEN_TRUST:
        raise LedgerError(f"trust must be one of {GOLDEN_TRUST}, got {trust!r}")
    if not png_bytes or len(png_bytes) < 8:
        raise LedgerError("golden_capture: empty image bytes")
    png, side = golden_paths(root, engine, spec_id, theme)
    groot = golden_root(root)
    with _locked(groot):
        prev = golden_get(root, engine, spec_id, theme)
        if prev is not None and prev.get("trust") == "validated" and trust == "provisional":
            raise LedgerError(
                f"{engine}/{spec_id}[{theme}] already has a VALIDATED golden; a "
                f"provisional capture cannot replace it (use `golden rebaseline`)")
        digest = pixel_hash(png_bytes)
        entry = {"at": _now_iso(), "run": run, "source": source, "trust": trust,
                 "hash": digest, "note": note}
        history = list(prev.get("history", [])) if prev else []
        if prev is not None:
            snapshot(groot, [png, side])
            history.append({k: prev.get(k) for k in ("at", "run", "source", "trust", "hash")})
        rec = {"engine": engine, "spec_id": spec_id, "theme": theme,
               "trust": trust, "hash": digest, "at": entry["at"], "run": run,
               "source": source, "note": note, "history": history}
        png.parent.mkdir(parents=True, exist_ok=True)
        tmp = png.with_suffix(".png.tmp")
        tmp.write_bytes(png_bytes)
        os.replace(tmp, png)
        _atomic_write(side, rec)
    if trust == "validated":
        rec["in_corpus"] = _sync_expectations_golden(root, engine, spec_id, theme, digest,
                                                     corpus=corpus)
    return rec


def golden_set_trust(root: Path, engine: str, spec_id: str, theme: str, *, run: str,
                     source: str, note: str = "", corpus: Optional[Path] = None
                     ) -> Dict[str, Any]:
    """Promote an existing provisional golden to validated without new bytes."""
    png, side = golden_paths(root, engine, spec_id, theme)
    groot = golden_root(root)
    with _locked(groot):
        rec = golden_get(root, engine, spec_id, theme)
        if rec is None:
            raise LedgerError(f"no golden for {engine}/{spec_id}[{theme}]")
        if rec.get("trust") == "validated":
            return rec
        rec.setdefault("history", []).append(
            {k: rec.get(k) for k in ("at", "run", "source", "trust", "hash")})
        rec.update({"trust": "validated", "at": _now_iso(), "run": run,
                    "source": source, "note": note})
        _atomic_write(side, rec)
    rec["in_corpus"] = _sync_expectations_golden(root, engine, spec_id, theme, rec["hash"],
                                                 corpus=corpus)
    return rec


def golden_trust_from_hash(root: Path, *, run: str, corpus: Optional[Path] = None
                           ) -> Dict[str, Any]:
    """Validate every provisional golden whose pixels hash-match the committed hash.

    The one shortcut allowed: the corpus hash came from bytes a judge
    approved, and identical pixels are the same image.  A provisional
    golden with a DIFFERENT hash stays provisional and is listed so the
    operator can see what this machine renders differently.
    """
    corpus = corpus or corpus_root(root)
    out = {"validated": 0, "already": 0, "mismatch": [], "no_committed_hash": 0}
    for side in sorted(golden_root(root).glob("*/*.json")):
        rec = _read(side)
        if not isinstance(rec, dict) or "spec_id" not in rec:
            continue
        engine, sid, theme = rec["engine"], rec["spec_id"], rec["theme"]
        if rec.get("trust") == "validated":
            out["already"] += 1
            continue
        exp = _read(_expectations_path(corpus, engine))
        committed = (((exp or {}).get("specs", {}).get(sid, {}) or {})
                     .get("golden", {}).get(theme, {}).get("hash"))
        if not committed:
            out["no_committed_hash"] += 1
            continue
        if committed == rec.get("hash"):
            golden_set_trust(root, engine, sid, theme, run=run, source="trust-from-hash",
                             corpus=corpus)
            out["validated"] += 1
        else:
            out["mismatch"].append(f"{engine}/{sid}[{theme}]")
    return out


def golden_rebaseline(root: Path, engine: str, spec_id: str, theme: str, *, png_path: Path,
                      reason: str, run: str, by: str = "human",
                      corpus: Optional[Path] = None) -> Dict[str, Any]:
    """Human decision: these bytes are the new validated golden.

    The only way to move a validated baseline other than a fresh judged
    `record`.  A drift verdict of "equivalent" is NOT enough on its own —
    forty equivalents in a row would ratchet a baseline somewhere nobody
    accepted in one step — so the judge records, and a person calls this.
    """
    if not reason.strip():
        raise LedgerError("rebaseline needs --reason")
    data = Path(png_path).read_bytes()
    return golden_capture(root, engine, spec_id, theme, data, trust="validated", run=run,
                          source=f"rebaseline:{by}", note=reason, corpus=corpus)


def golden_status(root: Path, *, corpus: Optional[Path] = None) -> Dict[str, Any]:
    """Counts per engine: goldens present by trust, corpus cases without one."""
    corpus = corpus or corpus_root(root)
    per: Dict[str, Dict[str, int]] = {}
    for side in golden_root(root).glob("*/*.json"):
        rec = _read(side)
        if not isinstance(rec, dict) or "spec_id" not in rec:
            continue
        e = per.setdefault(rec["engine"], {"provisional": 0, "validated": 0, "missing": 0})
        e[rec.get("trust", "provisional")] += 1
    if corpus.exists():
        for eng_dir in corpus.iterdir():
            exp = _read(eng_dir / "expectations.json") if eng_dir.is_dir() else None
            if not isinstance(exp, dict):
                continue
            e = per.setdefault(eng_dir.name, {"provisional": 0, "validated": 0, "missing": 0})
            for sid, rec in exp.get("specs", {}).items():
                for theme in rec.get("themes", list(THEMES)):
                    if golden_get(root, eng_dir.name, sid, theme) is None:
                        e["missing"] += 1
    totals = {"provisional": 0, "validated": 0, "missing": 0}
    for e in per.values():
        for k in totals:
            totals[k] += e[k]
    return {"golden_root": str(golden_root(root)), "engines": per, "totals": totals}


def _store_render_evidence(root: Path, engine: str, spec_id: str, theme: str,
                           png_bytes: bytes, *, run: str) -> Path:
    """Keep a FAILING render's pixels beside the verdict (never a golden)."""
    dst = root / "renders" / run / engine / f"{spec_id}.{theme}.png"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(png_bytes)
    return dst


def retire(root: Path, *, defect_id: Optional[str] = None, signature: Optional[str] = None,
           reason: str, by: str = "human") -> Dict[str, Any]:
    """Human decision: exclude from the queue permanently.  Separate from
    status so a re-consolidation cannot resurrect it; a reappearance is
    recorded as a fact, never re-queued."""
    if not (defect_id or signature):
        raise LedgerError("retire needs --defect or --signature")
    if not reason:
        raise LedgerError("retire needs --reason")
    with _locked(root):
        retired = _load_retired(root)
        rec = {"at": _now_iso(), "by": by, "reason": reason}
        if defect_id:
            backlog = _load_backlog(root)
            d = next((x for x in backlog["defects"] if x.get("id") == defect_id), None)
            if d is None:
                raise LedgerError(f"no defect {defect_id}")
            retired["defect_ids"][defect_id] = {**rec, "signature": d.get("signature")}
            if signature is None and d.get("signature"):
                signature = d["signature"]
        if signature:
            retired["signatures"][signature] = rec
        snapshot(root, [_retired_path(root)])
        _atomic_write(_retired_path(root), retired)
        return retired


def unretire(root: Path, *, defect_id: Optional[str] = None,
             signature: Optional[str] = None) -> Dict[str, Any]:
    with _locked(root):
        retired = _load_retired(root)
        if defect_id:
            retired["defect_ids"].pop(defect_id, None)
        if signature:
            retired["signatures"].pop(signature, None)
        snapshot(root, [_retired_path(root)])
        _atomic_write(_retired_path(root), retired)
        return retired


def _severity_rank(s: Optional[str]) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(s or "", 0)


# Largest group a single fan-out agent is handed.  Bigger groups are split
# by severity into chunks; the card's own contract is "one shared root
# cause, at most ~4 distinct code changes" per agent.
MAX_GROUP = 6


def _group_key(d: Dict[str, Any], freq: Dict[str, int]) -> str:
    """The defect's MOST SPECIFIC suspect file: the one named by the fewest
    live defects.  Grouping on this rather than on any shared file keeps
    groups cohesive — a defect that names both its own plugin and the
    shared capture path is grouped with its plugin, because the capture
    path is named by everything and is therefore never the rarest.  A
    defect with no path-like suspect falls back to its signature."""
    paths = suspect_paths(d.get("suspect_files"))
    if not paths:
        return "sig:" + (d.get("signature") or d["id"])
    return "file:" + min(paths, key=lambda f: (freq.get(f, 0), f))


def queue(root: Path, *, max_group: int = MAX_GROUP) -> Dict[str, Any]:
    """Work queue: defects not excluded by status or retirement, grouped by
    their most specific suspect file, split to at most ``max_group``
    defects per group, ordered by (engines x severity) desc.  Recomputed
    every call so a retirement or status change is reflected immediately;
    group ids are stable hashes of member defect ids so the same
    membership yields the same id across calls."""
    backlog = _load_backlog(root)
    retired = _load_retired(root)
    live = [d for d in backlog["defects"]
            if d.get("status") not in QUEUE_EXCLUDED
            and d.get("id") not in retired["defect_ids"]
            and (d.get("signature") or "") not in retired["signatures"]]
    from collections import Counter
    freq: Counter = Counter()
    for d in live:
        freq.update(suspect_paths(d.get("suspect_files")))
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for d in live:
        groups.setdefault(_group_key(d, freq), []).append(d)
    # Split oversize components into bounded chunks, worst severity first,
    # so the chunk an agent receives first is the one that matters most.
    chunks: List[tuple] = []
    for key, members in groups.items():
        members = sorted(members, key=lambda m: (-_severity_rank(m.get("severity")), m["id"]))
        for i in range(0, len(members), max(1, max_group)):
            chunks.append((key, members[i:i + max_group]))
    import hashlib
    out = []
    for key, members in chunks:
        ids = sorted(m["id"] for m in members)
        gid = "G-" + hashlib.sha1(",".join(ids).encode()).hexdigest()[:6]
        engines = sorted({e for m in members for e in m.get("engines") or []})
        sev = max(_severity_rank(m.get("severity")) for m in members)
        files = sorted({f for m in members for f in suspect_paths(m.get("suspect_files"))})
        out.append({"group_id": gid, "key": key, "defect_ids": ids, "engines": engines,
                    "severity_rank": sev, "suspect_files": files,
                    "rank": len(engines) * sev,
                    "regressions": [m["id"] for m in members if m.get("status") == "regression"]})
    # regressions first, then by rank
    out.sort(key=lambda g: (-len(g["regressions"]), -g["rank"], g["group_id"]))
    return {"group_ids": [g["group_id"] for g in out], "groups": out,
            "max_group": max_group,
            "live_defects": len(live), "total_defects": len(backlog["defects"]),
            "excluded_by_status": sum(1 for d in backlog["defects"] if d.get("status") in QUEUE_EXCLUDED),
            "retired": len(retired["defect_ids"]) + len(retired["signatures"])}


def show(root: Path, defect_id: Optional[str] = None, engine: Optional[str] = None) -> Any:
    if defect_id:
        backlog = _load_backlog(root)
        return next((d for d in backlog["defects"] if d.get("id") == defect_id), None)
    if engine:
        return _read(_blackboard_path(root, engine))
    backlog = _load_backlog(root)
    from collections import Counter
    return {"defects": len(backlog["defects"]),
            "by_status": dict(Counter(d.get("status") for d in backlog["defects"])),
            "merges": len(backlog["merges"]),
            "engines_with_regression_sets": sorted(backlog["regression_sets"])}


# ── CLI ───────────────────────────────────────────────────────────────────

def _json_arg(s: str) -> Any:
    """Accept inline JSON or @path-to-file."""
    if s.startswith("@"):
        with open(s[1:], "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(s)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="gfx_ledger", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--run", default=None,
                    help="run label to stamp on writes (default: UTC timestamp)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="append a light+dark verdict for one spec")
    p.add_argument("engine"); p.add_argument("spec_id")
    p.add_argument("--light", required=True, type=_json_arg)
    p.add_argument("--dark", required=True, type=_json_arg)
    p.add_argument("--wave", type=int); p.add_argument("--intent")
    p.add_argument("--light-png", help="the PNG the light verdict was judged on "
                                       "(status ok -> validated golden)")
    p.add_argument("--dark-png", help="the PNG the dark verdict was judged on")

    p = sub.add_parser("golden", help="machine-local validated renders under .ziya/gfx-golden/")
    p.add_argument("action", choices=("status", "show", "capture", "trust-from-hash",
                                      "rebaseline"))
    p.add_argument("--engine"); p.add_argument("--spec"); p.add_argument("--theme")
    p.add_argument("--png", help="image file (capture / rebaseline)")
    p.add_argument("--trust", choices=GOLDEN_TRUST, default="provisional",
                   help="capture only; rebaseline is always validated")
    p.add_argument("--reason", default="", help="rebaseline: why these bytes are right")
    p.add_argument("--by", default="human")
    p.add_argument("--corpus", help="override corpus dir (default tests/gfx_corpus)")

    p = sub.add_parser("migrate", help="promote schema-1 blackboards, fold sidecars")
    p.add_argument("--label", default="legacy-2026-09-01")
    p.add_argument("--engine", action="append")

    sub.add_parser("merge-triage", help="fold triage/*.json into backlog.json (no-clobber)")

    p = sub.add_parser("set-status")
    p.add_argument("defect_id"); p.add_argument("status", choices=sorted(STATUSES))
    p.add_argument("--note", default="")
    p.add_argument("--files", nargs="*")
    p.add_argument("--test")
    p.add_argument("--verification", type=_json_arg,
                   help='{"<engine>": {"light":"ok|fail","dark":"ok|fail","detail":"..."}}')
    p.add_argument("--bump-attempts", action="store_true")

    sub.add_parser("reconcile", help="derive verified/regression from current render evidence")
    p = sub.add_parser("retire", help="OVERRIDE ONLY: normal retirement is automatic "
                                       "(reconcile -> verified; set-status wont-fix)")
    p.add_argument("--defect"); p.add_argument("--signature")
    p.add_argument("--reason", required=True); p.add_argument("--by", default="human")

    p = sub.add_parser("unretire")
    p.add_argument("--defect"); p.add_argument("--signature")

    sub.add_parser("queue", help="print ordered fix groups (JSON)")
    p = sub.add_parser("promote", help="copy specs into the committed regression corpus "
                                        "(tests/gfx_corpus/) — automatic on verified; "
                                        "use --regression-sets / --verified to seed")
    p.add_argument("--regression-sets", action="store_true",
                   help="promote every Stage 1 regression_set spec")
    p.add_argument("--verified", action="store_true",
                   help="promote every currently-verified defect's specs")
    p.add_argument("--defect", help="promote one defect's specs")
    p.add_argument("--corpus", help="override corpus dir (default tests/gfx_corpus)")
    p = sub.add_parser("snapshot"); p.add_argument("--label")
    p = sub.add_parser("show"); p.add_argument("--defect"); p.add_argument("--engine")

    a = ap.parse_args(argv)
    root = Path(a.root)
    run = a.run or _ts_label()
    try:
        if a.cmd == "record":
            out = record(root, a.engine, a.spec_id, run=run, light=a.light, dark=a.dark,
                         wave=a.wave, intent=a.intent,
                         light_png=Path(a.light_png) if a.light_png else None,
                         dark_png=Path(a.dark_png) if a.dark_png else None)
        elif a.cmd == "golden":
            corpus = Path(a.corpus) if a.corpus else None

            def _need(*names):
                missing = [n for n in names if not getattr(a, n)]
                if missing:
                    raise LedgerError(f"golden {a.action} needs --" + " --".join(missing))
            if a.action == "status":
                out = golden_status(root, corpus=corpus)
            elif a.action == "show":
                _need("engine", "spec", "theme")
                out = golden_get(root, a.engine, a.spec, a.theme) or {"golden": None}
            elif a.action == "capture":
                _need("engine", "spec", "theme", "png")
                out = golden_capture(root, a.engine, a.spec, a.theme,
                                     Path(a.png).read_bytes(), trust=a.trust, run=run,
                                     source=f"cli:{a.by}", note=a.reason, corpus=corpus)
            elif a.action == "trust-from-hash":
                out = golden_trust_from_hash(root, run=run, corpus=corpus)
            else:  # rebaseline
                _need("engine", "spec", "theme", "png", "reason")
                out = golden_rebaseline(root, a.engine, a.spec, a.theme, png_path=Path(a.png),
                                        reason=a.reason, run=run, by=a.by, corpus=corpus)
        elif a.cmd == "migrate":
            out = migrate(root, run_label=a.label, engines=a.engine)
        elif a.cmd == "merge-triage":
            out = merge_triage(root, run=run)
        elif a.cmd == "set-status":
            out = set_status(root, a.defect_id, a.status, run=run, note=a.note,
                             files_touched=a.files, test_added=a.test,
                             verification=a.verification, bump_attempts=a.bump_attempts)
        elif a.cmd == "reconcile":
            out = reconcile(root, run=run)
        elif a.cmd == "retire":
            out = retire(root, defect_id=a.defect, signature=a.signature,
                         reason=a.reason, by=a.by)
        elif a.cmd == "unretire":
            out = unretire(root, defect_id=a.defect, signature=a.signature)
        elif a.cmd == "queue":
            out = queue(root)
        elif a.cmd == "promote":
            corpus = Path(a.corpus) if a.corpus else None
            out = {}
            if a.regression_sets:
                out["regression_sets"] = promote_regression_sets(root, corpus=corpus)
            if a.verified:
                out["verified"] = promote_verified(root, corpus=corpus)
            if a.defect:
                backlog = _load_backlog(root)
                d = next((x for x in backlog["defects"] if x.get("id") == a.defect), None)
                if d is None:
                    raise LedgerError(f"no defect {a.defect}")
                out["defect"] = {a.defect: promote_defect(root, d, corpus=corpus)}
            if not out:
                raise LedgerError("promote needs --regression-sets, --verified or --defect")
            out["corpus"] = str(corpus or corpus_root(root))
        elif a.cmd == "snapshot":
            files = [p for p in root.glob("*.json")] + list((root / "triage").glob("*.json"))
            dest = snapshot(root, files, label=a.label)
            out = {"snapshot": str(dest) if dest else None}
        elif a.cmd == "show":
            out = show(root, defect_id=a.defect, engine=a.engine)
        else:
            ap.error("unknown command")
    except LedgerError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
