#!/usr/bin/env python3
"""Tier B: foreign restatement — the offline utility proxy for memory.

A memory was worth keeping if a LATER, DIFFERENT conversation states the same
fact.  This measures that over the whole chat corpus with embeddings only
(Titan via the existing provider; no Opus, no Haiku), for every proposal in
the probationary event log, and reports the rate by proposal status:

    decayed   high  -> decay is killing facts you kept needing (evidence for
                       widening FAST_TRACK_LAYERS / lowering the bar)
    promoted  low   -> fast-track admits facts nobody re-needs (a flood)
    open      high  -> lower the corroboration bar

Method
  * each proposal's content is embedded once
  * each conversation's USER messages (what you said, not what the model
    said) are embedded once per 1,500-char chunk, keyed by content hash in
    .ziya/memory_restatement/embeddings.npz so re-runs are incremental
  * a proposal is "restated" by conversation C if C.id != origin, C has a
    user message after proposed_at, and max cosine >= THRESHOLD (0.88 — the
    extractor's own paraphrase threshold)

Writes one row per proposal to .ziya/outputs/memory_restatement.jsonl (a
later run REPLACES the file, so register it fresh with dataset_register).
Time-boxed: --budget-s stops embedding cleanly before a shell timeout; run
again to continue.  Run from the repo root, never from /tmp.

    python3 scripts/memory_restatement.py --budget-s 250        # embed corpus (resumable)
    python3 scripts/memory_restatement.py --budget-s 250 --score # score once embedded
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ARGS = sys.argv[1:]
sys.argv = ["memory_restatement"]

STATE_DIR = ROOT / ".ziya" / "memory_restatement"
EMB_FILE = STATE_DIR / "embeddings.npz"
CHUNKS_FILE = STATE_DIR / "chunks.jsonl"      # one row per embedded chunk: hash, conv, ts
OUT = ROOT / ".ziya" / "outputs" / "memory_restatement.jsonl"
THRESHOLD = 0.88
CHUNK_CHARS = 1500


def _arg(name, default=None):
    if name in ARGS:
        i = ARGS.index(name)
        return ARGS[i + 1] if i + 1 < len(ARGS) else True
    return default


def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def load_emb() -> dict:
    if not EMB_FILE.exists():
        return {}
    z = np.load(EMB_FILE)
    return {k: z[k] for k in z.files}


def save_emb(emb: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = EMB_FILE.with_suffix(".tmp.npz")
    np.savez(tmp, **emb)
    tmp.replace(EMB_FILE)


def iter_chats():
    from app.utils.encryption import is_encrypted, get_encryptor
    enc = get_encryptor()
    for p in sorted((Path.home() / ".ziya" / "projects").glob("*/chats/*.json")):
        if p.name.startswith("_") or p.name.endswith(".bindings.json"):
            continue
        try:
            raw = p.read_bytes()
            if is_encrypted(raw):
                raw = enc.decrypt(raw)
            d = json.loads(raw)
        except Exception:
            continue
        yield p.stem, d


def user_chunks(chat: dict):
    """(text, ts_ms) chunks of the user's own words, in order."""
    for m in chat.get("messages") or []:
        if m.get("muted") or m.get("role") not in ("human", "user"):
            continue
        c = m.get("content")
        if not isinstance(c, str) or len(c.strip()) < 40:
            continue
        ts = m.get("timestamp") or m.get("_timestamp") or chat.get("createdAt") or 0
        for i in range(0, len(c), CHUNK_CHARS):
            yield c[i:i + CHUNK_CHARS], int(ts)


def embed_corpus(budget_s: float) -> tuple[int, int, bool]:
    """Embed un-embedded chunks until the budget is spent.  Returns
    (chunks_total, chunks_embedded_this_run, complete)."""
    from app.services.embedding_service import get_embedding_provider, NoopProvider
    prov = get_embedding_provider()
    if isinstance(prov, NoopProvider):
        raise SystemExit("embedding provider is Noop — cannot run")
    emb = load_emb()
    known = set()
    if CHUNKS_FILE.exists():
        for l in CHUNKS_FILE.read_text().splitlines():
            if l.strip():
                known.add(json.loads(l)["h"])
    t0 = time.time(); total = 0; new = 0; complete = True
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHUNKS_FILE, "a") as cf:
        for cid, chat in iter_chats():
            for text, ts in user_chunks(chat):
                total += 1
                h = _h(text)
                if h in emb and h in known:
                    continue
                if time.time() - t0 > budget_s:
                    complete = False
                    continue          # keep counting total, stop embedding
                v = prov.embed_text(text)
                if v is None:
                    continue
                emb[h] = np.asarray(v, dtype=np.float32)
                if h not in known:
                    cf.write(json.dumps({"h": h, "conv": cid, "ts": ts}) + "\n")
                    known.add(h)
                new += 1
                if new % 200 == 0:
                    save_emb(emb)
    save_emb(emb)
    return total, new, complete


def score() -> dict:
    from app.services.embedding_service import get_embedding_provider
    from app.storage.proposals import get_proposals_store
    prov = get_embedding_provider()
    emb = load_emb()
    chunks = [json.loads(l) for l in CHUNKS_FILE.read_text().splitlines() if l.strip()]
    chunks = [c for c in chunks if c["h"] in emb]
    M = np.stack([emb[c["h"]] for c in chunks])
    M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    convs = np.array([c["conv"] for c in chunks])
    tss = np.array([c["ts"] for c in chunks], dtype=np.int64)

    rows = get_proposals_store().list_all()
    out = []
    summary = {}
    for r in rows:
        content = r.get("content") or ""
        v = emb.get("prop:" + _h(content))
        if v is None:
            v = prov.embed_text(content)
            if v is None:
                continue
            emb["prop:" + _h(content)] = np.asarray(v, dtype=np.float32)
        v = v / (np.linalg.norm(v) + 1e-9)
        sims = M @ v
        origin = r.get("conversation_id")
        after = tss > int(r.get("proposed_at") or 0)
        foreign = convs != origin
        mask = after & foreign
        best_foreign_later = float(sims[mask].max()) if mask.any() else 0.0
        best_self = float(sims[convs == origin].max()) if (convs == origin).any() else 0.0
        restaters = sorted(set(convs[mask & (sims >= THRESHOLD)].tolist()))
        row = {
            "proposal_id": r.get("id"), "status": r.get("status"),
            "archive_reason": r.get("archive_reason"), "layer": r.get("layer"),
            "corroborations": r.get("corroborations", 0),
            "best_foreign_later_cos": round(best_foreign_later, 4),
            "best_self_cos": round(best_self, 4),
            "restated": best_foreign_later >= THRESHOLD,
            "n_restating_conversations": len(restaters),
            "content": content[:160],
        }
        out.append(row)
        key = row["archive_reason"] if row["status"] == "archived" else row["status"]
        s = summary.setdefault(key, {"n": 0, "restated": 0})
        s["n"] += 1; s["restated"] += int(row["restated"])
    save_emb(emb)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(o) + "\n" for o in out))
    for s in summary.values():
        s["rate"] = round(s["restated"] / s["n"], 3) if s["n"] else None
    return {"proposals_scored": len(out), "chunks": len(chunks), "threshold": THRESHOLD,
            "by_status": summary, "out": str(OUT)}


def _check_credentials() -> None:
    """Fail in seconds, not forty minutes in.

    The first run of this script died 2,258 s in with a botocore traceback
    buried in a job journal, after a Midway session expired mid-corpus.
    A single embed_text call up front surfaces an invalid token immediately,
    and names the profile so the fix is obvious.  The provider resolves
    ZIYA_AWS_PROFILE / AWS_PROFILE / "default" — pass the right one in the
    environment; "default" is frequently the stale one.
    """
    import os
    from app.services.embedding_service import get_embedding_provider, NoopProvider
    prov = get_embedding_provider()
    if isinstance(prov, NoopProvider):
        raise SystemExit("embedding provider is Noop — set an embedding provider first")
    profile = os.environ.get("ZIYA_AWS_PROFILE") or os.environ.get("AWS_PROFILE") or "default"
    try:
        v = prov.embed_text("credential pre-check")
    except Exception as e:  # noqa: BLE001 -- the point is to report, not classify
        raise SystemExit(f"embedding call failed under AWS profile {profile!r}: "
                         f"{type(e).__name__}: {str(e)[:160]}\n"
                         f"Refresh credentials (mwinit / ada) or set AWS_PROFILE to a "
                         f"valid profile, then re-run; cached embeddings are kept.")
    if v is None:
        raise SystemExit(f"embedding provider returned None under profile {profile!r}")
    print(f"credentials OK (profile={profile})", flush=True)


def main() -> int:
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    _check_credentials()
    budget = float(_arg("--budget-s", 250))
    total, new, complete = embed_corpus(budget)
    print(f"corpus chunks={total} embedded_this_run={new} complete={complete}", flush=True)
    if not complete:
        print("budget exhausted — run again to continue embedding", flush=True)
    if _arg("--score") and complete:
        print(json.dumps(score(), indent=1), flush=True)
    elif _arg("--score"):
        print("not scoring: corpus embedding incomplete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
