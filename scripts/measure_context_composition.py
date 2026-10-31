#!/usr/bin/env python3
"""
Measure what long Ziya conversations are actually made of.

Read-only survey of the persisted chat store (~/.ziya/projects/*/chats/*.json)
that breaks each conversation's replayed history into buckets, so a decision
about lossless context management can rest on numbers rather than intuition.

Buckets per conversation (chars, with a chars/4 token estimate):

  human          user-authored text
  prose          assistant text outside tool blocks
  tool           tool-result bodies embedded in assistant messages, split by
                 tool name, and further classified into the lossless-elision
                 tiers discussed in design:
    tier0a       file_read / cat of a path that is in the chat's
                 ``additionalFiles`` (model-pinned context).  NOTE: user-pinned
                 tree files are NOT on the chat record, so this is a lower
                 bound on "already present elsewhere".
    tier0b       a read of a path that is read AGAIN later in the same chat
                 (superseded; the later copy is the recall)
    tier0c       an exact duplicate body (same tool, same bytes) that recurs
                 later in the chat
    tier1        remaining bodies above --tier1-threshold chars (candidates
                 for digest+handle paging; NOT lossless by construction)
  muted          chars in messages the user muted (already out of context)

Also reports, per conversation, the turn index at which cumulative replayed
chars crosses configurable thresholds, and aggregates across all chats with
at least --min-messages messages.

Reading uses Ziya's own parsers/decryptor so the measurement matches what the
replay path (app/utils/tool_history_rewrite.py) actually sees:

  * app.utils.encryption      — chat files may be ALE-encrypted at rest
  * find_tool_blocks          — the exact block parser used at replay time

Usage:
  python3 scripts/measure_context_composition.py [--min-messages 20]
        [--top 15] [--tier1-threshold 4000] [--json .ziya/context_survey.json]
        [--project <project_id>] [--chat <chat_id>]
        [--simulate-fold [--grace 1,2,3] [--min-calls 2] [--fold-min-chars 1500]]

--simulate-fold adds the tributary-fold report (design/tributary-fold.md):
quest-turn share split into tool blocks vs interim prose, the token-threshold
crossings with folds applied per grace value, what the single-block exemption
leaves, and a reuse proxy for how often recall would be needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make ``app`` importable when run from the repo root or from scripts/.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.utils.tool_history_rewrite import (  # noqa: E402
    find_tool_blocks, ToolBlock, payload_for_block, MIN_ELIDABLE_CHARS,
)

CHARS_PER_TOKEN = 4.0
THRESHOLDS_TOKENS = (50_000, 100_000, 200_000, 500_000, 1_000_000)

# Tools whose body is a file's contents and whose header/args name the path.
_FILE_READ_TOOLS = {"file_read", "mcp_file_read", "read_file", "mcp_read_file"}
_SHELL_TOOLS = {"run_shell_command", "mcp_run_shell_command"}

# "file read: app/x.py" (builtin label) — path is the last token.
_FILE_READ_LABEL_RE = re.compile(r"file read:\s*(\S+)", re.IGNORECASE)
# Shell body begins with the echoed command: "$ cat path" / "$ sed -n 1,40p path"
_SHELL_ECHO_RE = re.compile(r"^\$\s*(.+?)\s*$", re.MULTILINE)
_SHELL_READ_CMDS = ("cat ", "head ", "tail ", "sed -n ", "less ", "nl ")


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _ziya_home() -> Path:
    try:
        from app.utils.paths import get_ziya_home
        return Path(get_ziya_home())
    except Exception:
        return Path(os.environ.get("ZIYA_HOME", Path.home() / ".ziya"))


def _init_plugins() -> None:
    """Register plugin providers so the KEK for encrypted chats can be resolved.

    Enterprise encryption providers are only loaded when
    ZIYA_LOAD_INTERNAL_PLUGINS is set (same gate the server uses); without
    them, only ZIYA_ENCRYPTION_KEY-passphrase or plaintext chats are readable.
    """
    try:
        from app.plugins import initialize
        initialize()
    except Exception as e:
        print(f"warning: plugin initialize failed: {e}", file=sys.stderr)


def _read_chat(path: Path) -> Optional[dict]:
    """Read a chat record, transparently decrypting ALE files."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        from app.utils.encryption import is_encrypted, get_encryptor
        if is_encrypted(raw):
            raw = get_encryptor().decrypt(raw)
    except Exception as e:  # cannot decrypt — report, don't crash
        return {"_error": f"decrypt: {e}"}
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        return {"_error": f"parse: {e}"}


def iter_chat_files(home: Path, project: Optional[str], chat: Optional[str]):
    projects_dir = home / "projects"
    if not projects_dir.exists():
        return
    for pdir in sorted(projects_dir.iterdir()):
        if not pdir.is_dir():
            continue
        if project and pdir.name != project:
            continue
        cdir = pdir / "chats"
        if not cdir.exists():
            continue
        for f in sorted(cdir.glob("*.json")):
            if f.name.startswith("_") or f.name.endswith(".bindings.json"):
                continue
            if chat and f.stem != chat:
                continue
            yield pdir.name, f


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def _norm_tool(name: str) -> str:
    n = name or ""
    while n.startswith("mcp_"):
        n = n[4:]
    return n


def _read_path_for_block(b: ToolBlock) -> Optional[str]:
    """Return the file path this block is a read of, if it is one."""
    tool = _norm_tool(b.tool)
    if tool in {_norm_tool(t) for t in _FILE_READ_TOOLS}:
        m = _FILE_READ_LABEL_RE.search(b.header or "")
        if m:
            return m.group(1).strip().rstrip(",;")
        return None
    if tool in {_norm_tool(t) for t in _SHELL_TOOLS}:
        m = _SHELL_ECHO_RE.search(b.body or "")
        cmd = (m.group(1) if m else (b.header or "")).strip()
        cmd = re.sub(r"^Shell:\s*\$?\s*", "", cmd)
        for prefix in _SHELL_READ_CMDS:
            if cmd.startswith(prefix):
                # Last non-flag token is almost always the path for these.
                toks = [t for t in cmd.split() if not t.startswith("-")]
                if len(toks) >= 2 and "|" not in cmd and ">" not in cmd:
                    return toks[-1]
    return None


def _norm_path(p: str) -> str:
    p = p.strip().strip("'\"`")
    return os.path.normpath(p).lstrip("./") if not p.startswith("/") else os.path.normpath(p)


def analyze_chat(rec: dict, tier1_threshold: int) -> Dict[str, Any]:
    msgs: List[dict] = rec.get("messages") or []
    pinned = {_norm_path(p) for p in (rec.get("additionalFiles") or []) if isinstance(p, str)}

    per_tool: Counter = Counter()
    per_tool_n: Counter = Counter()
    buckets = Counter()  # human, prose, tool, muted
    tiers = Counter()    # tier0a, tier0b, tier0c, tier1
    tier_n = Counter()

    # First pass: collect all blocks with positions so later-occurrence checks work.
    blocks: List[Tuple[int, ToolBlock]] = []  # (msg_idx, block)
    for i, m in enumerate(msgs):
        if m.get("muted"):
            continue
        if (m.get("role") or "") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content:
            continue
        for b in find_tool_blocks(content):
            blocks.append((i, b))

    # Index: path -> list of msg indices where it was read; body hash -> indices
    reads_by_path: Dict[str, List[int]] = defaultdict(list)
    body_hash_idx: Dict[str, List[int]] = defaultdict(list)
    block_meta: List[Tuple[int, ToolBlock, Optional[str], str]] = []
    for i, b in blocks:
        rp = _read_path_for_block(b)
        rp = _norm_path(rp) if rp else None
        h = hashlib.sha1((_norm_tool(b.tool) + "\x00" + (b.body or "")).encode("utf-8", "replace")).hexdigest()
        if rp:
            reads_by_path[rp].append(i)
        body_hash_idx[h].append(i)
        block_meta.append((i, b, rp, h))

    cumulative: List[int] = []  # cumulative replayed chars per message index
    running = 0
    crossings: Dict[int, Optional[int]] = {t: None for t in THRESHOLDS_TOKENS}

    # Strict rule actually implemented in tool_history_rewrite.plan_elisions:
    # elide only if the payload is CONTAINED in a later read of the same
    # path, or an identical payload for the same tool recurs later.  The
    # loose tier0b/tier0c above ("any later read of the same path") is an
    # upper bound — it would also elide disjoint partial reads.
    payloads = [payload_for_block(b) for _, b, _, _ in block_meta]

    def _strict_redundant(k: int) -> bool:
        i, b, rp, _ = block_meta[k]
        p = payloads[k]
        if len(p) < MIN_ELIDABLE_CHARS:
            return False
        tool = _norm_tool(b.tool)
        for m in range(k + 1, len(block_meta)):
            j, bj, rpj, _ = block_meta[m]
            if j <= i:
                continue
            if rp and rpj == rp and p in payloads[m]:
                return True
            if _norm_tool(bj.tool) == tool and payloads[m] == p:
                return True
        return False

    tool_chars_by_msg: Counter = Counter()
    for k, (i, b, rp, h) in enumerate(block_meta):
        n = len(b.body or "")
        tool = _norm_tool(b.tool)
        per_tool[tool] += n
        per_tool_n[tool] += 1
        buckets["tool"] += n
        tool_chars_by_msg[i] += n
        if _strict_redundant(k):
            tiers["tier0strict"] += n; tier_n["tier0strict"] += 1
        # Tier assignment is exclusive, most-lossless-first.
        if rp and rp in pinned:
            tiers["tier0a"] += n; tier_n["tier0a"] += 1
        elif rp and any(j > i for j in reads_by_path[rp]):
            tiers["tier0b"] += n; tier_n["tier0b"] += 1
        elif any(j > i for j in body_hash_idx[h]):
            tiers["tier0c"] += n; tier_n["tier0c"] += 1
        elif n > tier1_threshold:
            tiers["tier1"] += n; tier_n["tier1"] += 1

    for i, m in enumerate(msgs):
        content = m.get("content")
        n = len(content) if isinstance(content, str) else 0
        if m.get("muted"):
            buckets["muted"] += n
            cumulative.append(running)
            continue
        role = m.get("role") or ""
        if role == "assistant":
            buckets["prose"] += max(0, n - tool_chars_by_msg.get(i, 0))
        elif role in ("human", "user"):
            buckets["human"] += n
        else:
            buckets["other"] += n
        running += n
        cumulative.append(running)
        for t in THRESHOLDS_TOKENS:
            if crossings[t] is None and running / CHARS_PER_TOKEN >= t:
                crossings[t] = i

    total = sum(v for k, v in buckets.items() if k != "muted")
    return {
        "id": rec.get("id"),
        "title": (rec.get("title") or "")[:60],
        "messages": len(msgs),
        "pinned_files": len(pinned),
        "total_chars": total,
        "buckets": dict(buckets),
        "tiers": dict(tiers),
        "tier_counts": dict(tier_n),
        "per_tool": dict(per_tool),
        "per_tool_n": dict(per_tool_n),
        "crossings": {str(k): v for k, v in crossings.items()},
    }


# --------------------------------------------------------------------------
# Tributary-fold simulation (design/tributary-fold.md, slice 1)
# --------------------------------------------------------------------------
#
# A quest turn is an assistant message with >= min_calls tool blocks and
# non-empty prose after the last block.  Its tributary is the span from the
# first block's start to the last block's end (bodies AND interim prose);
# replay keeps prose before the first block and the final prose.  A fold
# applies to replays made once >= grace human messages follow the turn.
# Nothing here changes replay; it only reports what folding would do.

FOLD_MIN_CALLS = 2
FOLD_MIN_CHARS = 1500
FOLD_MAX_LABELS = 12
# Reuse proxy: a line from a folded span quoted by a later assistant message.
REUSE_MIN_LINE = 40
REUSE_MAX_LINES = 60

_EMPTY_RESULT_RE = re.compile(
    r"^(?:\(no output\)|no output\.?|no match(?:es)?(?: found)?\.?|"
    r"not found\.?|\[\]|\{\}|null|none)$",
    re.IGNORECASE,
)


def _non_overlapping_blocks(text: str) -> List[ToolBlock]:
    """Same selection rule as tool_history_rewrite._non_overlapping."""
    out: List[ToolBlock] = []
    pos = 0
    for b in find_tool_blocks(text):
        if b.start < pos:
            continue
        out.append(b)
        pos = b.end
    return out


def _fold_label(b: ToolBlock) -> str:
    label = re.sub(r"\s+", " ", (b.header or "").replace("🔐", " ")).strip()
    return label or _norm_tool(b.tool)


def _is_empty_result(b: ToolBlock) -> bool:
    p = payload_for_block(b).strip()
    return not p or bool(_EMPTY_RESULT_RE.match(p))


def fold_placeholder(handle: str, labels: List[str], n_empty: int,
                     n_notes: int, span_chars: int) -> str:
    """The replay placeholder from the design, so savings are net of it."""
    shown = labels[:FOLD_MAX_LABELS]
    out = [f"[Ziya: tributary {handle} folded on replay — {len(labels)} tool "
           f"calls ({n_empty} returned nothing), {n_notes} interim notes, "
           f"~{span_chars / CHARS_PER_TOKEN:,.0f} tokens. Calls:"]
    out += [f"   {lab[:160]}" for lab in shown]
    if len(labels) > len(shown):
        out.append(f"   … {len(labels) - len(shown)} more")
    out.append(f" Full span: recall_tributary(handle=\"{handle}\"); "
               f"mode=\"commands\" for the calls alone, mode=\"results\" for "
               f"bodies, grep= to slice. The text that follows was written "
               f"with the whole span in view and stands.]")
    return "\n".join(out)


def classify_turn(content: str, min_calls: int = FOLD_MIN_CALLS,
                  min_chars: int = FOLD_MIN_CHARS) -> Optional[Dict[str, Any]]:
    """Classify one assistant message for folding.

    Returns None for a message with no tool blocks, otherwise a dict whose
    ``kind`` is ``quest`` (foldable), ``single`` (one block, exempt),
    ``ends_on_block`` (no final prose) or ``small`` (span under min_chars).
    """
    if not content or ("tool:" not in content and "TOOL_BLOCK_START" not in content):
        return None
    blocks = _non_overlapping_blocks(content)
    if not blocks:
        return None
    start, end = blocks[0].start, blocks[-1].end
    prose0 = content[:start]
    final = content[end:]
    span = content[start:end]
    block_chars = sum(b.end - b.start for b in blocks)
    notes = [content[a.end:b.start] for a, b in zip(blocks, blocks[1:])]
    info: Dict[str, Any] = {
        "n_calls": len(blocks),
        "span": span,
        "span_chars": len(span),
        "block_chars": block_chars,
        "interim_chars": len(span) - block_chars,
        "prose0_chars": len(prose0.strip()),
        "prose0": prose0,
        "final": final,
        "n_empty": sum(1 for b in blocks if _is_empty_result(b)),
        "n_notes": sum(1 for n in notes if n.strip()),
        "labels": [_fold_label(b) for b in blocks],
    }
    if not final.strip():
        info["kind"] = "ends_on_block"
    elif len(blocks) < min_calls:
        info["kind"] = "single"
    elif len(span) < min_chars:
        info["kind"] = "small"
    else:
        info["kind"] = "quest"
    return info


def _reuse_lines(info: Dict[str, Any]) -> List[str]:
    """Distinctive span lines that replay would NOT keep anyway."""
    kept = info["prose0"] + info["final"]
    out: List[str] = []
    seen: set = set()
    for line in info["span"].splitlines():
        s = line.strip()
        if len(s) < REUSE_MIN_LINE or s in seen or s in kept:
            continue
        if s.startswith("```") or "tool_result" in s or "TOOL_BLOCK" in s:
            continue
        if len(set(s)) < 8:  # rules, separators, repeated characters
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= REUSE_MAX_LINES:
            break
    return out


def simulate_fold(rec: dict, graces: Tuple[int, ...] = (2,),
                  min_calls: int = FOLD_MIN_CALLS,
                  min_chars: int = FOLD_MIN_CHARS) -> Dict[str, Any]:
    """Per-chat fold simulation over the non-muted (replayed) messages."""
    msgs: List[dict] = rec.get("messages") or []
    live = [(i, m) for i, m in enumerate(msgs) if not m.get("muted")]
    sizes: List[int] = []
    humans_upto: List[int] = []   # human messages in live[0..k], inclusive
    h = 0
    kinds: Counter = Counter()
    quests: List[Tuple[int, int, Dict[str, Any]]] = []  # (pos, saving, info)
    agg = Counter()
    prose0_lens: List[int] = []

    for pos, (_i, m) in enumerate(live):
        content = m.get("content")
        text = content if isinstance(content, str) else ""
        sizes.append(len(text))
        role = m.get("role") or ""
        if role in ("human", "user"):
            h += 1
        humans_upto.append(h)
        if role != "assistant":
            continue
        info = classify_turn(text, min_calls, min_chars)
        if info is None:
            continue
        kinds[info["kind"]] += 1
        if info["kind"] == "single":
            agg["single_chars"] += info["span_chars"]
            if info["span_chars"] >= min_chars:
                agg["single_large_chars"] += info["span_chars"]
                agg["single_large_n"] += 1
        if info["kind"] != "quest":
            continue
        handle = "tb-" + hashlib.sha1(
            info["span"].encode("utf-8", "replace")).hexdigest()[:10]
        ph = fold_placeholder(handle, info["labels"], info["n_empty"],
                              info["n_notes"], info["span_chars"])
        saving = max(0, info["span_chars"] - len(ph))
        quests.append((pos, saving, info))
        agg["span_chars"] += info["span_chars"]
        agg["block_chars"] += info["block_chars"]
        agg["interim_chars"] += info["interim_chars"]
        agg["placeholder_chars"] += len(ph)
        agg["calls"] += info["n_calls"]
        agg["empty_calls"] += info["n_empty"]
        prose0_lens.append(info["prose0_chars"])

    total = sum(sizes)
    last = len(live) - 1

    def _between(p: int, k: int) -> int:
        return humans_upto[k] - humans_upto[p]

    per_grace: Dict[str, Any] = {}
    for g in graces:
        pending = list(quests)
        saved = 0
        running = 0
        crossings: Dict[int, Optional[int]] = {t: None for t in THRESHOLDS_TOKENS}
        for k in range(len(live)):
            still = []
            for q in pending:
                if q[0] < k and _between(q[0], k) >= g:
                    saved += q[1]
                else:
                    still.append(q)
            pending = still
            running += sizes[k]
            for t in THRESHOLDS_TOKENS:
                if crossings[t] is None and (running - saved) / CHARS_PER_TOKEN >= t:
                    crossings[t] = live[k][0]
        steady = sum(q[1] for q in quests
                     if last >= 0 and _between(q[0], last) >= g)
        per_grace[str(g)] = {
            "steady_saved_chars": steady,
            "crossings": {str(t): v for t, v in crossings.items()},
        }

    # Reuse proxy, bucketed by how many human turns separate the quest from
    # the later assistant message that quotes it.  0 = same turn, skipped.
    reuse_buckets: Counter = Counter()
    for pos, _saving, info in quests:
        lines = _reuse_lines(info)
        if not lines:
            continue
        hit: set = set()
        for k in range(pos + 1, len(live)):
            m = live[k][1]
            if (m.get("role") or "") != "assistant":
                continue
            hb = _between(pos, k)
            if hb == 0:
                continue
            bucket = min(hb, 3)
            if bucket in hit:
                continue
            content = m.get("content")
            if isinstance(content, str) and any(l in content for l in lines):
                hit.add(bucket)
        for b in hit:
            reuse_buckets[str(b)] += 1
        if hit:
            reuse_buckets["min_" + str(min(hit))] += 1

    return {
        "total_chars": total,
        "kinds": dict(kinds),
        "agg": dict(agg),
        "prose0_lens": prose0_lens,
        "n_quests": len(quests),
        "per_grace": per_grace,
        "reuse": dict(reuse_buckets),
    }


def _median(xs: List[int]) -> Optional[int]:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def _p(xs: List[int], q: float) -> Optional[int]:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def print_fold_report(results: List[Dict[str, Any]], min_messages: int,
                      graces: Tuple[int, ...], min_calls: int,
                      min_chars: int) -> None:
    long_chats = [r for r in results
                  if r["messages"] >= min_messages and "fold" in r]
    if not long_chats:
        print("\nFold simulation: no long chats.")
        return
    folds = [r["fold"] for r in long_chats]
    total = sum(f["total_chars"] for f in folds)
    kinds = Counter(); agg = Counter(); reuse = Counter()
    prose0: List[int] = []
    n_q = 0
    for f in folds:
        kinds.update(f["kinds"]); agg.update(f["agg"]); reuse.update(f["reuse"])
        prose0 += f["prose0_lens"]; n_q += f["n_quests"]

    print(f"\n=== Tributary fold simulation over {len(long_chats)} long chats "
          f"(min_calls={min_calls}, min_chars={min_chars}) ===")
    print(f"  replayed history: {_k(total)} chars ≈ {_tok(total)} tokens")
    print(f"  assistant messages with tool blocks, by kind:")
    for k in ("quest", "single", "small", "ends_on_block"):
        print(f"    {k:14s} {kinds.get(k, 0):6d}")
    span = agg.get("span_chars", 0)
    print(f"\n  quest tributary spans: {_k(span)} chars  {_pct(span, total)} of all replayed")
    print(f"    tool blocks       {_k(agg.get('block_chars', 0)):>9s}  "
          f"{_pct(agg.get('block_chars', 0), span)} of span")
    print(f"    interim prose     {_k(agg.get('interim_chars', 0)):>9s}  "
          f"{_pct(agg.get('interim_chars', 0), span)} of span   <- unreachable by aging")
    print(f"    placeholders      {_k(agg.get('placeholder_chars', 0)):>9s}  "
          f"(net cost of folding)")
    print(f"    calls {agg.get('calls', 0)}, returned nothing {agg.get('empty_calls', 0)}")
    sl = agg.get("single_large_chars", 0)
    print(f"  left by single-block exemption (>= min_chars): "
          f"{agg.get('single_large_n', 0)} turns, {_k(sl)} chars  {_pct(sl, total)} of all")
    if prose0:
        empty = sum(1 for x in prose0 if x == 0)
        print(f"  prose₀ on quest turns: median {_median(prose0)} chars, "
              f"p90 {_p(prose0, 0.9)}, empty in {_pct(empty, len(prose0)).strip()}")

    base = {t: [r["crossings"][str(t)] for r in long_chats
                if r["crossings"][str(t)] is not None] for t in THRESHOLDS_TOKENS}
    for g in graces:
        saved = sum(f["per_grace"][str(g)]["steady_saved_chars"] for f in folds)
        print(f"\n  grace = {g} human turn(s):")
        print(f"    steady-state reclaimed (net of placeholders): {_k(saved)} chars  "
              f"{_pct(saved, total)} of all replayed")
        print(f"    {'threshold':>9s}  {'no fold':>22s}  {'with fold':>22s}")
        for t in THRESHOLDS_TOKENS:
            xs = [f["per_grace"][str(g)]["crossings"][str(t)] for f in folds]
            xs = [x for x in xs if x is not None]
            b = base[t]
            bt = f"{len(b):4d} cross, med #{_median(b)}" if b else "none"
            ft = f"{len(xs):4d} cross, med #{_median(xs)}" if xs else "none"
            print(f"    {t//1000:>8d}K  {bt:>22s}  {ft:>22s}")

    print(f"\n  reuse proxy ({n_q} quest turns): a later assistant message quotes a")
    print(f"  distinctive (>= {REUSE_MIN_LINE}-char) span line, by human turns elapsed:")
    for b, lab in (("1", "1 (the next turn)"), ("2", "2"), ("3", ">= 3")):
        print(f"    {lab:18s} {reuse.get(b, 0):6d}  {_pct(reuse.get(b, 0), n_q)}")
    for g in graces:
        need = sum(reuse.get(f"min_{b}", 0) for b in ("1", "2", "3") if int(b) >= g)
        # A quest whose FIRST reuse falls at >= g was reused only after the fold.
        print(f"    grace {g}: first reuse after the fold in {need} "
              f"({_pct(need, n_q).strip()}) — recall would be needed")
    print("  Caveats: reuse includes re-reads and re-runs that reproduce a line, which")
    print("  is the recall case; prose paraphrase is not detected; chars/4 tokens.")


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def _k(n: float) -> str:
    return f"{n/1000:,.0f}K"


def _tok(chars: float) -> str:
    return _k(chars / CHARS_PER_TOKEN)


def _pct(part: float, whole: float) -> str:
    return f"{100*part/whole:5.1f}%" if whole else "   - "


def print_report(results: List[Dict[str, Any]], errors: Counter, top: int,
                 min_messages: int, tier1_threshold: int) -> None:
    long_chats = [r for r in results if r["messages"] >= min_messages]
    print(f"\nChats read: {len(results)}   unreadable: {sum(errors.values())}   "
          f"long (>= {min_messages} msgs): {len(long_chats)}")
    if errors:
        for k, v in errors.most_common(5):
            print(f"  unreadable: {v:4d}  {k}")
    if not long_chats:
        print("No long chats to analyze.")
        return

    agg_b = Counter(); agg_t = Counter(); agg_tool = Counter(); agg_tool_n = Counter()
    for r in long_chats:
        agg_b.update(r["buckets"]); agg_t.update(r["tiers"])
        agg_tool.update(r["per_tool"]); agg_tool_n.update(r["per_tool_n"])
    total = sum(v for k, v in agg_b.items() if k != "muted")

    print(f"\n=== Aggregate over {len(long_chats)} long chats "
          f"({_k(total)} chars ≈ {_tok(total)} tokens replayed) ===")
    print(f"  {'bucket':10s} {'chars':>10s} {'~tokens':>9s}  share")
    for k in ("human", "prose", "tool", "other"):
        v = agg_b.get(k, 0)
        print(f"  {k:10s} {_k(v):>10s} {_tok(v):>9s}  {_pct(v, total)}")
    print(f"  {'muted':10s} {_k(agg_b.get('muted',0)):>10s} {_tok(agg_b.get('muted',0)):>9s}  (already out of context)")

    tool_total = agg_b.get("tool", 0)
    print(f"\n  Lossless-elision tiers (share of TOOL chars, then of ALL replayed chars):")
    for k, label in (("tier0a", "read of model-pinned file (lower bound)"),
                     ("tier0b", "read superseded by later read"),
                     ("tier0c", "exact duplicate body recurs later"),
                     ("tier1", f"other body > {tier1_threshold} chars (paging candidate)")):
        v = agg_t.get(k, 0)
        print(f"    {k:7s} {_k(v):>9s} {_pct(v, tool_total)} of tool  "
              f"{_pct(v, total)} of all   {label}")
    t0 = sum(agg_t.get(k, 0) for k in ("tier0a", "tier0b", "tier0c"))
    print(f"    {'tier0*':7s} {_k(t0):>9s} {_pct(t0, tool_total)} of tool  "
          f"{_pct(t0, total)} of all   <- loose upper bound (any later read of same path)")
    ts = agg_t.get("tier0strict", 0)
    print(f"    {'strict':7s} {_k(ts):>9s} {_pct(ts, tool_total)} of tool  "
          f"{_pct(ts, total)} of all   <- IMPLEMENTED rule: contained in / identical to a later result")

    print(f"\n  Tool bodies by tool (top {top}):")
    print(f"    {'tool':32s} {'calls':>6s} {'chars':>10s} {'avg':>8s}  share of tool")
    for tool, v in agg_tool.most_common(top):
        n = agg_tool_n[tool]
        print(f"    {tool[:32]:32s} {n:6d} {_k(v):>10s} {_k(v/n) if n else '-':>8s}  {_pct(v, tool_total)}")

    print(f"\n  Turn at which cumulative replay crosses N tokens (median over chats that cross):")
    for t in THRESHOLDS_TOKENS:
        xs = sorted(r["crossings"][str(t)] for r in long_chats if r["crossings"][str(t)] is not None)
        if xs:
            med = xs[len(xs)//2]
            print(f"    {t//1000:>4d}K tokens: {len(xs):4d} chats cross; median at message #{med}"
                  f"  (min {xs[0]}, max {xs[-1]})")
        else:
            print(f"    {t//1000:>4d}K tokens: no chat crosses")

    print(f"\n=== Largest {top} long chats ===")
    print(f"  {'~tokens':>8s} {'msgs':>5s} {'tool%':>6s} {'tier0%':>7s} {'tier1%':>7s}  title")
    for r in sorted(long_chats, key=lambda r: r["total_chars"], reverse=True)[:top]:
        tot = r["total_chars"] or 1
        t0 = sum(r["tiers"].get(k, 0) for k in ("tier0a", "tier0b", "tier0c"))
        print(f"  {_tok(tot):>8s} {r['messages']:5d} {_pct(r['buckets'].get('tool',0), tot):>6s} "
              f"{_pct(t0, tot):>7s} {_pct(r['tiers'].get('tier1',0), tot):>7s}  {r['title']}")

    print("\nCaveats:")
    print("  * Token counts are chars/4 estimates.")
    print("  * Files pinned from the tree by the USER are not on the chat record and are")
    print("    not counted anywhere here; they add to every turn on top of these numbers.")
    print("  * tier0a therefore only sees model-pinned files (context_add_file).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-messages", type=int, default=20)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--tier1-threshold", type=int, default=4000)
    ap.add_argument("--json", type=Path, default=None, help="write per-chat results here")
    ap.add_argument("--project", default=None)
    ap.add_argument("--chat", default=None)
    ap.add_argument("--simulate-fold", action="store_true",
                    help="also simulate tributary folding (design/tributary-fold.md)")
    ap.add_argument("--grace", default="1,2,3",
                    help="comma-separated grace values (human turns) to compare")
    ap.add_argument("--min-calls", type=int, default=FOLD_MIN_CALLS)
    ap.add_argument("--fold-min-chars", type=int, default=FOLD_MIN_CHARS)
    args = ap.parse_args()
    graces = tuple(int(g) for g in str(args.grace).split(",") if g.strip())

    _init_plugins()
    home = _ziya_home()
    results: List[Dict[str, Any]] = []
    errors: Counter = Counter()
    for project_id, f in iter_chat_files(home, args.project, args.chat):
        rec = _read_chat(f)
        if rec is None:
            errors["unreadable"] += 1
            continue
        if "_error" in rec:
            errors[rec["_error"][:60]] += 1
            continue
        r = analyze_chat(rec, args.tier1_threshold)
        r["project"] = project_id
        if args.simulate_fold:
            r["fold"] = simulate_fold(rec, graces, args.min_calls,
                                      args.fold_min_chars)
        results.append(r)

    print_report(results, errors, args.top, args.min_messages, args.tier1_threshold)
    if args.simulate_fold:
        print_fold_report(results, args.min_messages, graces, args.min_calls,
                          args.fold_min_chars)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=1))
        print(f"\nPer-chat results written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
