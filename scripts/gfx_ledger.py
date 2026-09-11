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
from typing import Any, Dict, Iterable, List, Optional

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
           wave: Optional[int] = None, intent: Optional[str] = None) -> Dict[str, Any]:
    """Append one (light, dark) verdict pair to a spec's history.

    Never removes any other spec.  If the spec is new it is created; if
    it exists, ``wave``/``intent`` are only filled when previously absent
    so a re-sweep cannot silently rewrite what a spec was FOR.
    """
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
    p = sub.add_parser("snapshot"); p.add_argument("--label")
    p = sub.add_parser("show"); p.add_argument("--defect"); p.add_argument("--engine")

    a = ap.parse_args(argv)
    root = Path(a.root)
    run = a.run or _ts_label()
    try:
        if a.cmd == "record":
            out = record(root, a.engine, a.spec_id, run=run, light=a.light, dark=a.dark,
                         wave=a.wave, intent=a.intent)
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
