#!/usr/bin/env python3
"""Restore memories that the organizer's corpus cleanup hard-deleted.

Until the cleanup switched to archiving (see app/memory/organizer.py
``_archive_memory``), ``cleanup_corpus`` called ``store.delete`` on every
memory an LLM batch flagged, and discarded the reason.  The content of
promoted memories survives in the probationary log (``status=promoted``
rows keep their text, layer, tags and scope), so a promoted proposal whose
text no longer matches any live memory — neither exactly nor by token
containment >= 0.6, which would indicate it was merged into a survivor —
was deleted and can be restored.

Restores through the running server's API (POST, then PUT scope) so
encryption, locking and placement are the server's own.  Run the server on
the archiving cleanup first, or the next cleanup pass deletes them again.

Usage:
    python3 scripts/restore_cleanup_deleted_memories.py            # dry run
    python3 scripts/restore_cleanup_deleted_memories.py --apply
    ... --since 2026-09-23    (only proposals promoted on/after this date)
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TOK = re.compile(r"[a-z0-9_]{3,}")
LAYERS = {"domain_context", "architecture", "lexicon", "decision",
          "negative_constraint", "preference", "process", "active_thread"}


def _toks(s):
    return set(TOK.findall((s or "").lower()))


def _api(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def find_candidates(live, since_ms=0):
    os.environ.setdefault("ZIYA_ENABLE_MEMORY", "true")
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()                     # key material for the encrypted log
    from app.storage.proposals import get_proposals_store

    live_exact = {m["content"].strip() for m in live}
    live_toks = [_toks(m["content"]) for m in live]
    out = []
    for r in get_proposals_store().list_all():
        if r.get("status") != "promoted":
            continue
        c = (r.get("content") or "").strip()
        when = int(r.get("status_changed_at") or r.get("proposed_at") or 0)
        if not c or c in live_exact or when < since_ms:
            continue
        t = _toks(c)
        cont = max((len(t & lt) / len(t) for lt in live_toks), default=0.0) if t else 1.0
        if cont >= 0.6:
            continue
        out.append({"content": c, "layer": r.get("layer") or "domain_context",
                    "tags": list(r.get("tags") or []),
                    "learned_from": r.get("learned_from") or "observation",
                    "project_paths": (r.get("scope") or {}).get("project_paths") or [],
                    "promoted_at": when, "proposal_id": r.get("id")})
    # Two deleted memories with identical text restore once.
    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: c["promoted_at"]):
        if c["content"] not in seen:
            seen.add(c["content"])
            uniq.append(c)
    return uniq


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="restore (default: dry run)")
    ap.add_argument("--server", default="http://localhost:6969")
    ap.add_argument("--since", help="YYYY-MM-DD: only proposals promoted on/after")
    ap.add_argument("--report", default="/tmp/memory_restore_report.json")
    a = ap.parse_args()

    live = _api(a.server, "GET", "/api/v1/memory/all")
    live = live.get("memories", live) if isinstance(live, dict) else live
    since = int(dt.datetime.strptime(a.since, "%Y-%m-%d").timestamp() * 1000) if a.since else 0
    cands = find_candidates(live, since)

    by_layer, by_proj = {}, {}
    for c in cands:
        by_layer[c["layer"]] = by_layer.get(c["layer"], 0) + 1
        p = os.path.basename((c["project_paths"] or ["?"])[0].rstrip("/"))
        by_proj[p] = by_proj.get(p, 0) + 1
    print(f"live memories: {len(live)}   deleted & recoverable: {len(cands)}")
    print(f"  by layer:   {by_layer}\n  by project: {by_proj}")

    restored = []
    if a.apply:
        for c in cands:
            layer = c["layer"] if c["layer"] in LAYERS else "domain_context"
            content = c["content"][:2000]
            if len(content) < 10:
                continue
            m = _api(a.server, "POST", "/api/v1/memory",
                     {"content": content, "layer": layer, "tags": c["tags"],
                      "learned_from": c["learned_from"]})
            if c["project_paths"]:
                _api(a.server, "PUT", f"/api/v1/memory/{m['id']}",
                     {"scope": {"project_paths": c["project_paths"]}})
            restored.append({"memory_id": m["id"], **c})
        print(f"restored {len(restored)}.  Run maintenance to place them: "
              f"curl -X POST {a.server}/api/v1/memory/maintenance")
    else:
        print("dry run — re-run with --apply to restore")
    json.dump({"candidates": cands, "restored": restored}, open(a.report, "w"), indent=1)
    print(f"report: {a.report}")


if __name__ == "__main__":
    main()
