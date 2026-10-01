#!/usr/bin/env python3
"""Re-queue lost memories as probationary proposals for normal reprocessing.

Two sources, both of which survived the organizer cleanup's hard deletes
(fixed: cleanup now archives, see app/memory/organizer.py _archive_memory):

  1. Promoted rows in the proposal log (probationary.jsonl) whose text no
     longer matches any live memory.  They carry their original layer,
     tags, scope and quality grade, which is kept.
  2. memory_propose / memory_save arguments in ~/.ziya/audit/tool_audit_*.jsonl.
     Scope is recovered from the conversation's project; quality is left
     unset, exactly as memory_propose creates it (such proposals promote
     on corroboration or use, not on the fast track).

Skipped: text already live (exactly, or >= 0.6 token containment, i.e.
merged into a survivor), text already open as a proposal, and duplicates
across sources.  Everything re-enters as an OPEN proposal stamped with the
current activity counter, so the lifecycle judges it afresh instead of
restoring earlier verdicts as facts.

All events are written in ONE read-modify-write of probationary.jsonl.
ProposalsStore's lock is per-process, so this keeps the window in which a
concurrent write by the running server could be lost to milliseconds; the
server's projection cache is mtime-keyed and picks the new file up.
Embeddings are not computed here (the cache is owned by the server).

Usage:
    python3 scripts/requeue_lost_memories.py            # dry run
    python3 scripts/requeue_lost_memories.py --apply
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TOK = re.compile(r"[a-z0-9_]{3,}")
LAYERS = {"domain_context", "architecture", "lexicon", "decision",
          "negative_constraint", "preference", "process", "active_thread"}


def _toks(s):
    return set(TOK.findall((s or "").lower()))


def _conv_project_paths():
    """conversation id -> [project path], from the chat record locations."""
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_ziya_home
    ps = ProjectStorage(get_ziya_home())
    home = str(get_ziya_home() / "projects")
    out, path_of = {}, {}
    for d in glob.glob(os.path.join(home, "*", "chats")):
        pid = os.path.basename(os.path.dirname(d))
        if pid not in path_of:
            try:
                p = ps.get(pid)
                path_of[pid] = getattr(p, "path", None)
            except Exception:
                path_of[pid] = None
        for f in glob.glob(os.path.join(d, "*.json")):
            if path_of[pid]:
                out[os.path.basename(f)[:-5]] = [path_of[pid]]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--server", default="http://localhost:6969")
    ap.add_argument("--report", default="/tmp/memory_requeue_report.json")
    a = ap.parse_args()

    os.environ.setdefault("ZIYA_ENABLE_MEMORY", "true")
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()                      # key material for encrypted stores
    from app.storage.proposals import get_proposals_store, EVENT_RECORD, STATUS_OPEN
    from app.memory.lifecycle import current_activity_count
    from app.models.memory import MemoryProposal

    with urllib.request.urlopen(a.server + "/api/v1/memory/all", timeout=60) as r:
        live = json.load(r)
    live = live.get("memories", live) if isinstance(live, dict) else live
    live_exact = {m["content"].strip() for m in live}
    live_toks = [_toks(m["content"]) for m in live]

    def covered(c):
        if c in live_exact:
            return True
        t = _toks(c)
        return (not t) or max((len(t & lt) / len(t) for lt in live_toks), default=0.0) >= 0.6

    store = get_proposals_store()
    rows = store.list_all()
    open_content = {(r.get("content") or "").strip() for r in rows if r.get("status") == STATUS_OPEN}

    cands, seen = [], set()

    def take(c, **kw):
        c = c.strip()[:2000]
        if len(c) < 10 or c in seen or c in open_content or covered(c):
            return
        seen.add(c)
        cands.append({"content": c, **kw})

    # 1. promoted proposal rows
    for r in rows:
        if r.get("status") == "promoted":
            take(r.get("content") or "", source="proposal_log",
                 layer=r.get("layer"), tags=list(r.get("tags") or []),
                 learned_from=r.get("learned_from") or "observation",
                 project_paths=(r.get("scope") or {}).get("project_paths") or [],
                 conversation_id=r.get("conversation_id"),
                 quality=r.get("quality"), quality_components=r.get("quality_components"))

    # 2. audit-log tool arguments
    conv_proj = _conv_project_paths()
    for f in sorted(glob.glob(os.path.expanduser("~/.ziya/audit/tool_audit_*.jsonl"))):
        for line in open(f, errors="replace"):
            if '"memory_propose"' not in line and '"memory_save"' not in line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            args = ev.get("args") or {}
            tags = args.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            cid = args.get("conversation_id")
            take(args.get("content") or "", source="audit_log",
                 layer=args.get("layer"), tags=tags,
                 learned_from="observation", conversation_id=cid,
                 project_paths=conv_proj.get(cid or "", []),
                 quality=None, quality_components=None)

    for c in cands:
        if c["layer"] not in LAYERS:
            c["layer"] = "domain_context"

    b = lambda p: os.path.basename(p.rstrip("/"))
    print(f"live {len(live)}  open proposals {len(open_content)}  to re-queue {len(cands)}")
    for src in ("proposal_log", "audit_log"):
        sub = [c for c in cands if c["source"] == src]
        print(f"  {src:13s} {len(sub):4d}  layers {dict(collections.Counter(c['layer'] for c in sub))}")
        print(f"  {'':13s}       projects {dict(collections.Counter(b((c['project_paths'] or ['(none)'])[0]) for c in sub))}")

    if a.apply and cands:
        from app.mcp.encoding_scanner import scan_and_log
        counter = current_activity_count()
        now = int(time.time() * 1000)
        events = []
        for c in cands:
            try:
                scan_and_log(c["content"], source="memory_proposal")
            except Exception:
                pass
            p = MemoryProposal(content=c["content"], layer=c["layer"], tags=c["tags"],
                               learned_from=c["learned_from"],
                               conversation_id=c.get("conversation_id"))
            data = p.model_dump()
            pid = f"prop_{__import__('hashlib').sha256(c['content'].encode('utf-8')).hexdigest()[:8]}"
            data.update(id=pid, content_hash=pid[5:], activity_count_at_proposal=counter,
                        scope={"domain_node": None, "project_paths": c["project_paths"]},
                        quality=c["quality"], quality_components=c["quality_components"],
                        requeued_from=c["source"], status=STATUS_OPEN)
            events.append({"kind": EVENT_RECORD, "id": pid, "ts": now, "data": data})
            c["proposal_id"] = pid
        with store._lock:
            existing = store._read_lines()
            store._write_lines(existing + events)
            store._cache = None
        reopened = sum(1 for r in store.list_open() if r.get("requeued_from"))
        print(f"re-queued {len(events)}; open with requeued_from after write: {reopened}")
    elif not a.apply:
        print("dry run — re-run with --apply")
    json.dump(cands, open(a.report, "w"), indent=1)
    print(f"report: {a.report}")


if __name__ == "__main__":
    main()
