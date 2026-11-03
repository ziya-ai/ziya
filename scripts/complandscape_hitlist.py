#!/usr/bin/env python3
"""Stack-ranked build hitlist from the competitive-landscape corpus.

WHY
---
REPORT.md §10 ranks eleven items in prose.  The corpus scored far more:
76 BUILD_CANDIDATE dispositions with effort classes, 54 gap-queue entries
with priority scores, 17 ZIYA_BEHIND head-to-head verdicts, and two
critics' corrections.  None of it was joined into one table a person can
read top-down and start on.  This script is that join.

WHAT IT IS / IS NOT
-------------------
A PROJECTION with one explicit, tunable formula -- not new judgment.
Every input is a corpus field; every correction is a critic record; the
formula is printed in the output so a reader can disagree with it.

    prevalence_live  = competitors scoring >=3 in the matrix, minus tools
                       Critic B marked abandonware (continue)
    gap              = best competitor score - Ziya score (matrix)
    effort_weight    = TRIVIAL 1 · SMALL 2 · MEDIUM 4 · LARGE 8 · ARCH 16,
                       read from the FIRST class in the disposition's
                       effort_class (the reduced increment), then bumped
                       one class where Critic A flagged effort optimism
    conflict_penalty = 0.7 if the disposition lists architectural conflicts
    fit_factor       = 1.0 if the disposition's philosophy_fit says FITS,
                       0.6 if it says FIGHTS / tension / orthogonal,
                       0.8 otherwise (split, neutral, unstated)
    score            = prevalence_live * (1 + gap/5) * conflict_penalty
                       * fit_factor / effort_weight

Also ranked: the six FOUND verdicts Critic A reopened as partial matches
(reintegration_overreach) -- CL4 closed them as LEDGER_CORRECTION, so
they are not BUILD_CANDIDATEs on disk, but the gap they closed is still
open at the critic's defensible score.  Marked `reopened`.

Critic-B "discount entirely" entries are listed separately, not ranked.
ZIYA_BEHIND depth verdicts (quality gaps in things Ziya already has) are
a second table with their own ordering (confidence, then dimension deficit).

Usage:  python3 scripts/complandscape_hitlist.py   (writes into the
        resolved 60-synthesis run dir: HITLIST.md and HITLIST.json)
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import defaultdict

ROOT = ".ziya/complandscape"
EFFORT_W = {"TRIVIAL": 1, "SMALL": 2, "MEDIUM": 4, "LARGE": 8,
            "ARCHITECTURAL": 16, "SPLIT": 4}
EFFORT_ORDER = ["TRIVIAL", "SMALL", "MEDIUM", "LARGE", "ARCHITECTURAL"]

# §10 of REPORT.md, so the table can show where the synthesizer placed
# an item and the reader can see what the formula moved.
REPORT_TIER = {
    "lint-test-fix-repair-loop": "T1#1", "lifecycle-hooks": "T1#2",
    "github-repo-sync-export": "T1#3", "auto-context-compaction": "T1#4",
    "sandboxed-execution-runtime": "T2#6",
    "git-worktree-parallel-isolation": "T2#7",
    "multi-model-comparison-arena": "T2#8", "llm-observability-tracing": "T2#9",
    "browser-automation-computer-use": "T2#10", "native-mobile-client": "T2#11",
}


def _load(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _run_dir(base):
    cur = os.path.join(ROOT, base, "CURRENT_RUN")
    with open(cur) as fh:
        return os.path.join(ROOT, base, fh.read().strip())


def _first_effort(text):
    """First recognised class in a possibly-compound effort string."""
    for tok in re.findall(r"[A-Z]+", str(text or "")):
        if tok in EFFORT_W:
            return tok
    return "MEDIUM"


def _bump(cls):
    if cls not in EFFORT_ORDER:
        return cls
    i = EFFORT_ORDER.index(cls)
    return EFFORT_ORDER[min(i + 1, len(EFFORT_ORDER) - 1)]


def main():
    rei_dir = _run_dir("50-reintegration")
    depth_dir = _run_dir("50-depth")
    syn_dir = _run_dir("60-synthesis")
    matrix = _load(os.path.join(ROOT, "30-matrix.json"))
    critic_a = _load(os.path.join(syn_dir, "60-critique-ziya.json"))
    critic_b = _load(os.path.join(syn_dir, "60-critique-competitors.json"))
    roster = _load(os.path.join(ROOT, "20-competitors", "roster.json"))

    capname = {c["id"]: c["name"] for c in matrix["capabilities"]}
    capdom = {c["id"]: c["domain"] for c in matrix["capabilities"]}
    cells = defaultdict(dict)
    for c in matrix["cells"]:
        cells[c["capability_id"]][c["tool"]] = c
    display = {s: (v.get("display_name") or s) for s, v in roster.items()
               if s != "_meta"}

    abandon = {a["tool"] for a in critic_b.get("abandonware", [])}
    discount = {d["capability_id"]: d["reason"]
                for d in critic_b.get("gap_queue_entries_to_discount", [])}
    optimism = {e["capability_id"]: e["problem"]
                for e in critic_a.get("effort_optimism", [])}
    reopened = {e["capability_id"]: e["why_the_match_is_wrong"]
                for e in critic_a.get("reintegration_overreach", [])
                if not e["capability_id"].startswith("SYSTEMIC")}
    defensible = {e["capability_id"]: e["defensible"]
                  for e in critic_a.get("inflated_scores", [])}

    def _fit_factor(text):
        t = str(text or "").lower()
        fights = bool(re.search(r"\bfights?\b|tension|orthogonal|cuts against", t))
        fits = bool(re.search(r"\bfits\b|reinforces", t))
        if fits and fights:
            return 0.8      # split: the reduced increment fits, the full one fights
        if fights:
            return 0.6
        if fits:
            return 1.0
        return 0.8

    rows, discounted = [], []
    for f in sorted(glob.glob(os.path.join(rei_dir, "*-disposition.json"))):
        d = _load(f)
        cid = d["capability_id"]
        is_reopened = cid in reopened
        if d.get("disposition") != "BUILD_CANDIDATE" and not is_reopened:
            continue
        st = d.get("stretch") or {}
        row_cells = cells.get(cid, {})
        z = row_cells.get("ziya", {})
        zscore = z.get("score") or 0
        if is_reopened and cid in defensible:
            zscore = defensible[cid]   # critic-corrected, not the CL4 FOUND score
        comp = [(t, c) for t, c in row_cells.items()
                if t != "ziya" and c.get("status") == "present"]
        live3 = sorted(
            ((c.get("score") or 0, t) for t, c in comp
             if (c.get("score") or 0) >= 3 and t not in abandon),
            reverse=True)
        best = max([c.get("score") or 0 for _, c in comp] or [0])
        eff_raw = st.get("effort_class")
        eff = _first_effort(eff_raw)
        eff_used = _bump(eff) if cid in optimism else eff
        conflicts = st.get("architectural_conflicts") or []
        if isinstance(conflicts, str):
            conflicts = [conflicts]
        conflicts = [c for c in conflicts
                     if not re.match(r"\s*none\b", str(c), re.I)]
        penalty = 0.7 if conflicts else 1.0
        fit = _fit_factor((st.get("value") or {}).get("philosophy_fit"))
        gap = max(best - zscore, 0)
        prev = len(live3)
        score = prev * (1 + gap / 5) * penalty * fit / EFFORT_W[eff_used]
        r = {
            "capability_id": cid, "name": capname.get(cid, cid),
            "domain": capdom.get(cid, "?"),
            "reopened_by_critic_a": reopened.get(cid),
            "fit_factor": fit,
            "stage_a_verdict": d.get("stage_a_verdict"),
            "ziya_score": zscore, "best_competitor": best,
            "prevalence_live_3plus": prev,
            "leaders": [f"{display.get(t, t)} ({s})" for s, t in live3[:3]],
            "effort_class_raw": str(eff_raw or ""),
            "effort_used": eff_used,
            "critic_a_effort_optimism": optimism.get(cid),
            "architectural_conflicts": len(conflicts),
            "reduced_version": str(st.get("reduced_version") or "")[:400],
            "report_tier": REPORT_TIER.get(cid),
            "score": round(score, 2),
        }
        if cid in discount:
            r["critic_b_discount"] = discount[cid]
            discounted.append(r)
        else:
            rows.append(r)
    rows.sort(key=lambda r: (-r["score"], -r["prevalence_live_3plus"]))

    # Quality gaps: contested capabilities where the depth run said BEHIND.
    behind = []
    for f in sorted(glob.glob(os.path.join(depth_dir, "*.json"))):
        d = _load(f)
        if d.get("verdict") != "ZIYA_BEHIND":
            continue
        deficits = []
        for dim in d.get("dimensions", []):
            zs = (dim.get("ziya") or {}).get("score")
            top = max([c.get("score") or 0 for c in dim.get("competitors", [])
                       if c.get("status") == "scored"] or [0])
            if zs is not None and top > zs:
                deficits.append(top - zs)
        behind.append({
            "capability_id": d["capability_id"],
            "name": capname.get(d["capability_id"], d["capability_id"]),
            "domain": capdom.get(d["capability_id"], "?"),
            "confidence": d.get("confidence"),
            "dims_behind": len(deficits), "dims_total": len(d.get("dimensions", [])),
            "total_deficit": sum(deficits),
            "leader_advantage": str(d.get("specific_leader_advantage") or "")[:300],
        })
    conf_rank = {"high": 0, "medium": 1, "low": 2}
    behind.sort(key=lambda b: (conf_rank.get(b["confidence"], 3), -b["total_deficit"]))

    # ── write ──
    out_json = {"formula": __doc__.split("WHAT IT IS / IS NOT")[1].strip(),
                "build_candidates_ranked": rows,
                "discounted_by_critic_b": discounted,
                "quality_gaps_ziya_behind": behind}
    with open(os.path.join(syn_dir, "HITLIST.json"), "w", encoding="utf-8") as fh:
        json.dump(out_json, fh, indent=1)

    L = ["# Build hitlist — stack-ranked from the corpus", "",
         f"Source runs: reintegration `{os.path.basename(rei_dir)}`, depth "
         f"`{os.path.basename(depth_dir)}`, synthesis `{os.path.basename(syn_dir)}`. "
         "Generated by `scripts/complandscape_hitlist.py`; a projection of "
         "corpus fields with one explicit formula, not new judgment.", "",
         "```", *__doc__.split("WHAT IT IS / IS NOT")[1].strip().splitlines(), "```", "",
         f"## A. Build candidates, ranked ({len(rows)})", "",
         "Score = live prevalence × gap factor × conflict penalty ÷ effort. "
         "`§10` shows where REPORT.md placed the item (blank = not in its "
         "top eleven). `eff` is the effort class used; `↑` = bumped one class "
         "for Critic-A effort optimism; `⚠` = architectural conflicts listed.", "",
         "| # | capability | domain | Ziya→best | live ≥3 | leaders | eff | §10 | score |",
         "|--:|---|---|:-:|--:|---|---|---|--:|"]
    for i, r in enumerate(rows, 1):
        eff = r["effort_used"] + ("↑" if r["critic_a_effort_optimism"] else "") \
            + (" ⚠" if r["architectural_conflicts"] else "") \
            + (f" fit {r['fit_factor']}" if r["fit_factor"] < 1 else "") \
            + (" reopened" if r["reopened_by_critic_a"] else "")
        L.append(f"| {i} | **{r['name']}** `{r['capability_id']}` | {r['domain']} | "
                 f"{r['ziya_score']}→{r['best_competitor']} | {r['prevalence_live_3plus']} | "
                 f"{', '.join(r['leaders'])} | {eff} | {r['report_tier'] or ''} | {r['score']} |")
    L += ["", "### Reduced versions (the increment the score prices)", ""]
    for i, r in enumerate(rows[:25], 1):
        if r["reduced_version"]:
            L.append(f"{i}. **{r['name']}** — {r['reduced_version']}")
            if r["critic_a_effort_optimism"]:
                L.append(f"   - *Critic A on effort:* {r['critic_a_effort_optimism'][:300]}")
    L += ["", f"## B. Quality gaps — contested capabilities where Ziya is BEHIND ({len(behind)})", "",
          "Ziya has these; the head-to-head says a rival does them better. "
          "Ordered by verdict confidence, then total dimension deficit.", "",
          "| # | capability | domain | conf | dims behind | deficit | leader advantage |",
          "|--:|---|---|---|:-:|--:|---|"]
    for i, b in enumerate(behind, 1):
        L.append(f"| {i} | **{b['name']}** `{b['capability_id']}` | {b['domain']} | {b['confidence']} | "
                 f"{b['dims_behind']}/{b['dims_total']} | {b['total_deficit']} | {b['leader_advantage']} |")
    L += ["", f"## C. Discounted by Critic B — do not build on these numbers ({len(discounted)})", ""]
    for r in discounted:
        L.append(f"- **{r['name']}** `{r['capability_id']}` — {r['critic_b_discount'][:300]}")
    L.append("")
    with open(os.path.join(syn_dir, "HITLIST.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print(f"wrote {syn_dir}/HITLIST.md and HITLIST.json: {len(rows)} ranked, "
          f"{len(discounted)} discounted, {len(behind)} quality gaps")


if __name__ == "__main__":
    sys.exit(main())
