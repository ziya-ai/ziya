#!/usr/bin/env python3
"""
Render a markdown report from cached memory-eval verdicts.

Reads ~/.ziya/memory-eval/verdicts.jsonl (or another path via --input),
aggregates the data into actionable summaries, and writes markdown to
stdout (default) or to --output.

The report is the actionable companion to scripts/run_memory_eval.py.
Where the runner produces verdicts as a side effect, this script
digests them into prompt-tuning and heuristic-tuning material.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

VERDICT_PATH = Path.home() / ".ziya" / "memory-eval" / "verdicts.jsonl"


def _load_verdicts(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    # Latest-wins per chat_id (re-evaluations append; we want the newest)
    by_chat: Dict[str, Dict[str, Any]] = {}
    for r in out:
        chat_id = r.get("chat_id", "")
        if not chat_id:
            continue
        existing = by_chat.get(chat_id)
        if existing is None or r.get("evaluated_at", 0) >= existing.get("evaluated_at", 0):
            by_chat[chat_id] = r
    return list(by_chat.values())


def _format_table(rows: List[List[str]], headers: List[str]) -> str:
    """Render a small markdown table.  Cells are stringified as-is."""
    widths = [max(len(h), max((len(str(r[i])) for r in rows), default=0))
              for i, h in enumerate(headers)]
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    head = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)) + " |"
    body = "\n".join("| " + " | ".join(str(c).ljust(w) for c, w in zip(r, widths)) + " |"
                     for r in rows)
    return f"{head}\n{sep}\n{body}"


def _section_summary(verdicts: List[Dict[str, Any]]) -> str:
    """Top-line counts."""
    n = len(verdicts)
    if n == 0:
        return "No verdicts found."
    long_n = sum(1 for v in verdicts if v.get("msg_count", 0) >= 50)
    salience_agreement = Counter()
    salience_parse_fails = 0
    for v in verdicts:
        s = v.get("salience")
        if not s:
            continue
        if not s.get("parse_ok", True):
            salience_parse_fails += 1
            continue
        salience_agreement[s.get("agreement", "?")] += 1
    extracted_total = sum(v.get("pipeline_extracted_count", 0) for v in verdicts)
    proposed_total = sum(v.get("pipeline_proposed_count", 0) for v in verdicts)
    candidate_total = sum(len(v.get("candidates", [])) for v in verdicts)
    missed_total = sum(len(v.get("missed", [])) for v in verdicts)
    skipped = Counter(v.get("pipeline_skipped_reason") or "(ran)" for v in verdicts)
    out = ["## Summary", ""]
    out.append(f"- **Conversations evaluated**: {n} ({long_n} long >=50 msgs, {n - long_n} short)")
    out.append(f"- **Extraction**: {extracted_total} raw candidates, "
               f"{proposed_total} after gate+dedup, {candidate_total} graded by Opus")
    out.append(f"- **Missed extractions per Opus**: {missed_total} total")
    if salience_parse_fails:
        out.append(f"- **Salience parse failures**: {salience_parse_fails}/{n}")
    out.append("")
    out.append("### Salience agreement")
    out.append("")
    rows = [[k, str(v), f"{100*v/sum(salience_agreement.values()):.0f}%"]
            for k, v in salience_agreement.most_common()]
    out.append(_format_table(rows, ["agreement", "count", "share"]))
    out.append("")
    if any(k != "(ran)" for k in skipped):
        out.append("### Pipeline skip reasons")
        out.append("")
        rows = [[k, str(v)] for k, v in skipped.most_common()]
        out.append(_format_table(rows, ["reason", "count"]))
        out.append("")
    return "\n".join(out)


def _section_rating_distribution(verdicts: List[Dict[str, Any]]) -> str:
    """Rating histogram, broken down by layer."""
    by_layer: Dict[str, Counter] = defaultdict(Counter)
    overall = Counter()
    for v in verdicts:
        for c in v.get("candidates", []):
            r = int(c.get("rating", 0))
            layer = c.get("candidate_layer", "?")
            by_layer[layer][r] += 1
            overall[r] += 1
    if not overall:
        return ""
    out = ["## Candidate ratings (Opus 1-5)", "",
           "*Rated against the extractor's own published gate rules.*", ""]
    rows = []
    for r in [5, 4, 3, 2, 1]:
        rows.append([f"rating={r}", str(overall[r]),
                     f"{100*overall[r]/sum(overall.values()):.0f}%"])
    out.append(_format_table(rows, ["rating", "count", "share"]))
    out.append("")
    out.append("### By layer (rating distribution)")
    out.append("")
    headers = ["layer", "n", "5", "4", "3", "2", "1", "avg"]
    rows = []
    for layer, counts in sorted(by_layer.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(counts.values())
        avg = sum(r * c for r, c in counts.items()) / n if n else 0
        rows.append([layer, str(n), str(counts[5]), str(counts[4]),
                     str(counts[3]), str(counts[2]), str(counts[1]),
                     f"{avg:.2f}"])
    out.append(_format_table(rows, headers))
    out.append("")
    return "\n".join(out)


def _section_gate_violations(verdicts: List[Dict[str, Any]]) -> str:
    """Which extractor gates trip most often?"""
    gates = Counter()
    for v in verdicts:
        for c in v.get("candidates", []):
            g = c.get("gate_violation")
            if g:
                gates[g] += 1
    if not gates:
        return ""
    out = ["## Gate violations", "",
           "*Which gates the small-tier extractor most often fails to apply.*", ""]
    rows = [[g, str(n), f"{100*n/sum(gates.values()):.0f}%"]
            for g, n in gates.most_common()]
    out.append(_format_table(rows, ["gate", "count", "share"]))
    out.append("")
    return "\n".join(out)


def _section_examples(verdicts: List[Dict[str, Any]]) -> str:
    """Top-rated and worst-rated candidates with full content."""
    all_cands = []
    for v in verdicts:
        for c in v.get("candidates", []):
            all_cands.append((c, v.get("chat_id", "")[:8]))
    all_cands.sort(key=lambda cv: (-cv[0].get("rating", 0),
                                    cv[0].get("candidate_layer", "")))
    top = [cv for cv in all_cands if cv[0].get("rating", 0) >= 4][:15]
    bot = [cv for cv in all_cands if cv[0].get("rating", 0) <= 2][:25]
    out = []
    if top:
        out.append("## High-rated candidates (worth keeping)")
        out.append("")
        for c, cid in top:
            out.append(f"- **[{c.get('rating')}] [{c.get('candidate_layer')}]** "
                       f"{c.get('candidate_content', '').strip()}  *(from `{cid}`)*")
        out.append("")
    if bot:
        out.append("## Low-rated candidates (extractor noise)")
        out.append("")
        out.append("*These are concrete material for prompt tightening.  "
                   "Each shows the candidate, its rating, and the gate Opus thinks it should have failed.*")
        out.append("")
        for c, cid in bot:
            gate = c.get("gate_violation") or "(no gate cited)"
            out.append(f"- **[{c.get('rating')}] [{c.get('candidate_layer')}]** "
                       f"{c.get('candidate_content', '').strip()}  *(from `{cid}`, {gate})*")
            r = c.get("rationale", "").strip()
            if r:
                out.append(f"  > {r[:300]}")
        out.append("")
    return "\n".join(out)


def _section_salience_disagreements(verdicts: List[Dict[str, Any]]) -> str:
    """List salience false positives with Opus rationales -- heuristic-tuning material."""
    fps = []
    for v in verdicts:
        s = v.get("salience")
        if not s or not s.get("parse_ok", True):
            continue
        if s.get("agreement") == "false_positive":
            fps.append((v.get("chat_id", "")[:8], v.get("title", "")[:80],
                        s.get("heuristic_hit_count", 0), s.get("opus_rationale", "")))
    if not fps:
        return ""
    out = ["## Salience false positives (heuristic fired, Opus said no)", "",
           "*Each line is a heuristic-tuning candidate.  If the rationale "
           "describes a recurring class of conversation that doesn't have signal, "
           "the regex patterns triggering on those conversations probably need "
           "tightening.*", ""]
    for cid, title, hits, rat in fps[:30]:
        out.append(f"- **`{cid}`** ({hits} hits): *{title}*")
        out.append(f"  > {rat}")
    out.append("")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=VERDICT_PATH)
    p.add_argument("--output", type=Path, default=None,
                   help="Write to file instead of stdout")
    args = p.parse_args()
    verdicts = _load_verdicts(args.input)
    if not verdicts:
        print(f"No verdicts found at {args.input}", file=sys.stderr)
        return 1
    sections = [
        f"# Memory eval report",
        "",
        f"*Generated {time.strftime('%Y-%m-%d %H:%M')} from {args.input}.*",
        "",
        _section_summary(verdicts),
        _section_rating_distribution(verdicts),
        _section_gate_violations(verdicts),
        _section_examples(verdicts),
        _section_salience_disagreements(verdicts),
    ]
    md = "\n".join(s for s in sections if s)
    if args.output:
        args.output.write_text(md)
        print(f"Wrote {len(md):,} bytes to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
