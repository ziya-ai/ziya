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
THRESHOLDS_TOKENS = (50_000, 100_000, 150_000, 200_000)

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
    args = ap.parse_args()

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
        results.append(r)

    print_report(results, errors, args.top, args.min_messages, args.tier1_threshold)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=1))
        print(f"\nPer-chat results written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
