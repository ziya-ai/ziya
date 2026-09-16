#!/usr/bin/env python3
"""Generate the comparative appendices and PDFs for a complandscape synthesis run.

The synthesis report (REPORT.md) is a judgment layer over the corpus and keeps
only rollups.  This script projects the intermediate comparative data back into
print form WITHOUT adding judgment: every number is copied from the corpus, and
the two critics' corrections are shown beside the raw values rather than
replacing them.

Outputs, all under 60-synthesis/<run_id>/:
  REPORT-print.md   REPORT.md + inline figures + Appendices A, B, C
  REPORT.pdf        A4 portrait
  APPENDIX-A-head-to-head.md    one table per contested capability (depth run)
  APPENDIX-B-tool-scorecards.md one scorecard per roster tool
  APPENDIX-C-gap-register.md    one row per reintegrated gap
  MATRIX.md / MATRIX.pdf        full 555 x 28 matrix, A3 landscape (companion)

Usage:
  python3 scripts/complandscape_appendix.py [--root .ziya/complandscape] [--no-pdf]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import glob
import html as htmllib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from complandscape_corpus import resolve_corpus  # noqa: E402

STATUS_GLYPH = {"present": None, "absent": "\u00b7", "not_applicable": "~",
                "unknown": "?", "unresolved": "!"}
DEPTH_GLYPH = {"unknown": "?", "not_applicable": "n/a", "not_assessed": "\u2013",
               "below_threshold": "\u2013", "not_in_matrix": "\u2013"}
H2H_COLS_PER_TABLE = 8


def _j(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _esc(s: Any) -> str:
    """Escape a value for a markdown table cell."""
    return str("" if s is None else s).replace("|", "\\|").replace("\n", " ").strip()


def _short_name(display: str) -> str:
    """'ChatGPT (Code Interpreter / Data Analysis)' -> 'ChatGPT'; keeps headers narrow."""
    return re.sub(r"\s*\(.*?\)", "", display).split(" / ")[0].strip() or display


def _fmt_correction(c: Any) -> str:
    """Depth records carry ziya_score_correction as free-shape dicts; render 'from->to: reason'."""
    if not isinstance(c, dict):
        return _esc(c)
    # The 17 non-null corrections use ten different key sets; a from/to heuristic would
    # misattribute half of them, so render the record verbatim as key: value pairs.
    return _esc("; ".join(f"{k}: {v}" for k, v in c.items()))


def _short_verdict(v: Any) -> str:
    """First-run records carry prose verdicts like 'PARTIAL (~40%)'; reduce to the bare word."""
    m = re.match(r"\s*([A-Z_]+)", str(v or ""))
    return m.group(1) if m else str(v or "")


# --------------------------------------------------------------------------
# corpus loading
# --------------------------------------------------------------------------

class Corpus:
    def __init__(self, root: str):
        self.root = root
        res = resolve_corpus(root)
        self.phases = res["phases"]
        self.synth_run = open(os.path.join(root, "60-synthesis", "CURRENT_RUN")).read().strip()
        self.synth_dir = os.path.join(root, "60-synthesis", self.synth_run)
        self.matrix = _j(os.path.join(root, "30-matrix.json"))
        self.ledger = _j(os.path.join(root, "19-ziya-ledger.json"))
        self.gapq = _j(os.path.join(root, "31-gap-queue.json"))
        self.rei_summary = _j(os.path.join(root, "41-reintegration-summary.json"))
        self.roster = _j(os.path.join(root, "20-competitors", "roster.json"))
        self.dossiers = {t: _j(os.path.join(root, "20-competitors", f"{t}.json"))
                         for t in self.roster if t != "_meta"
                         and os.path.exists(os.path.join(root, "20-competitors", f"{t}.json"))}
        self.crit_z = _j(os.path.join(self.synth_dir, "60-critique-ziya.json"))
        self.crit_c = _j(os.path.join(self.synth_dir, "60-critique-competitors.json"))

        self.tools: List[str] = [t for t in self.matrix["tools"] if t != "ziya"]
        self.caps: Dict[str, dict] = {c["id"]: c for c in self.matrix["capabilities"]}
        self.domains: List[str] = self.matrix["domains"]
        self.cells: Dict[Tuple[str, str], dict] = {(c["capability_id"], c["tool"]): c
                                                    for c in self.matrix["cells"]}
        self.ledger_caps: Dict[str, dict] = {c["id"]: c for c in self.ledger["capabilities"]}
        self.gap_items: Dict[str, dict] = {g["capability_id"]: g for g in self.gapq["items"]}

        ddir = self.phases["depth"]["dir"]
        self.depth: Dict[str, dict] = {}
        for f in sorted(glob.glob(os.path.join(ddir, "*.json"))):
            d = _j(f)
            if isinstance(d, dict) and d.get("capability_id"):
                self.depth[d["capability_id"]] = d

        rdir = self.phases["reintegration"]["dir"]
        self.stage_a: Dict[str, dict] = {}
        self.dispo: Dict[str, dict] = {}
        for f in sorted(glob.glob(os.path.join(rdir, "*-stageA.json"))):
            d = _j(f)
            self.stage_a[d.get("capability_id") or os.path.basename(f)[:-len("-stageA.json")]] = d
        for f in sorted(glob.glob(os.path.join(rdir, "*-disposition.json"))):
            d = _j(f)
            self.dispo[d.get("capability_id") or os.path.basename(f)[:-len("-disposition.json")]] = d

        # ---- critic corrections, keyed for lookup ----
        self.z_inflated = {x["capability_id"]: x for x in self.crit_z.get("inflated_scores", [])}
        self.z_overreach = {x["capability_id"]: x for x in self.crit_z.get("reintegration_overreach", [])}
        self.z_effort = {x["capability_id"]: x for x in self.crit_z.get("effort_optimism", [])}
        self.z_false_uniq = {x["capability_id"]: x for x in self.crit_z.get("false_uniqueness", [])}
        self.c_discount = {x["capability_id"]: x for x in self.crit_c.get("gap_queue_entries_to_discount", [])}
        self.c_abandon = {x["tool"]: x for x in self.crit_c.get("abandonware", [])}
        self.c_overstated: Dict[Tuple[str, str], dict] = {}
        for x in self.crit_c.get("overstated_depth", []):
            for t in re.split(r"[,\s]+", x.get("tool", "")):
                if t:
                    self.c_overstated[(t, x["capability_id"])] = x
        self.c_laundered = {(x["tool"], x["capability_id"]): x
                            for x in self.crit_c.get("laundered_claims", [])}
        # CL4 ledger corrections (normalised in the summary; raw dispositions use 18 field shapes)
        self.ledger_corr = {x["capability_id"]: x for x in self.rei_summary.get("ledger_corrections", [])}

    # ---- helpers ----
    def cap_name(self, cid: str) -> str:
        c = self.caps.get(cid) or {}
        return c.get("name") or self.ledger_caps.get(cid, {}).get("name") or cid

    def cap_domain(self, cid: str) -> str:
        return (self.caps.get(cid) or {}).get("domain", "")

    def display(self, tool: str) -> str:
        return (self.roster.get(tool) or {}).get("display_name", tool)

    def ziya_raw(self, cid: str) -> Optional[int]:
        c = self.cells.get((cid, "ziya"))
        return c.get("score") if c else None

    def ziya_corrected(self, cid: str) -> Optional[int]:
        """Critic A's defensible score if it differs from the raw matrix score, else None."""
        x = self.z_inflated.get(cid)
        if x and x.get("defensible") != self.ziya_raw(cid):
            return x["defensible"]
        return None

    def ziya_score_str(self, cid: str) -> str:
        raw = self.ziya_raw(cid)
        cor = self.ziya_corrected(cid)
        if raw is None:
            return "\u2013"
        return f"{raw}\u2192**{cor}**" if cor is not None else str(raw)

    def prevalence(self, cid: str, threshold: int = 3) -> Tuple[int, int, Optional[str]]:
        """(count of competitors present at >= threshold, count present at all, best tool)."""
        n3 = n = 0
        best: Tuple[int, Optional[str]] = (-1, None)
        for t in self.tools:
            c = self.cells.get((cid, t))
            if c and c.get("status") == "present":
                n += 1
                s = c.get("score") or 0
                if s >= threshold:
                    n3 += 1
                if s > best[0]:
                    best = (s, t)
        return n3, n, (f"{self.display(best[1])} ({best[0]})" if best[1] else None)


# --------------------------------------------------------------------------
# Appendix A: head-to-head
# --------------------------------------------------------------------------

def build_appendix_a(cx: Corpus) -> str:
    out = ["## Appendix A \u2014 Head-to-head depth tables (contested capabilities)", ""]
    n = len(cx.depth)
    reg = next(iter(cx.depth.values()), {}).get("registry_version", "?")
    out.append(f"Depth run `{cx.phases['depth']['run_id']}`, registry `{reg}`: **{n} contested "
               f"capabilities**, one table each. Rows are the registry dimensions; cells are "
               f"`score/tier` as recorded. `?` = unknown, `n/a` = not applicable, `\u2013` = not "
               f"assessed / below contender threshold / not in matrix. Where Critic A found the "
               f"Ziya *ledger* score inflated, the header shows `raw\u2192**defensible**`; the "
               f"per-dimension scores are the depth agent's own and are not restated. Critic "
               f"notes are quoted, not applied, so the raw record stays visible.")
    out.append("")
    by_domain: Dict[str, List[str]] = collections.defaultdict(list)
    for cid in cx.depth:
        by_domain[cx.cap_domain(cid) or "unclassified"].append(cid)
    for dom in cx.domains + [d for d in by_domain if d not in cx.domains]:
        if dom not in by_domain:
            continue
        out.append(f"### A.{cx.domains.index(dom) + 1 if dom in cx.domains else '?'} {dom} "
                   f"({len(by_domain[dom])})")
        out.append("")
        for cid in sorted(by_domain[dom]):
            out.extend(_h2h_block(cx, cid))
    return "\n".join(out)


def _h2h_block(cx: Corpus, cid: str) -> List[str]:
    d = cx.depth[cid]
    dims = d.get("dimensions") or []
    out = [f"#### {cx.cap_name(cid)}", "",
           f"`{cid}` \u00b7 verdict **{d.get('verdict')}** ({d.get('confidence')} confidence) "
           f"\u00b7 Ziya ledger score {cx.ziya_score_str(cid)}"
           + (f" \u00b7 depth-agent Ziya correction: {_fmt_correction(d['ziya_score_correction'])}"
              if d.get("ziya_score_correction") else ""), ""]
    # contenders: every tool that appears in any dimension, ordered by mean scored value desc
    tool_scores: Dict[str, List[int]] = collections.defaultdict(list)
    tool_seen: Dict[str, int] = collections.Counter()
    informative: set = set()  # tools with at least one scored / unknown / n-a cell
    for dim in dims:
        for comp in dim.get("competitors") or []:
            tool_seen[comp["tool"]] += 1
            if comp.get("status") in ("scored", "unknown", "not_applicable"):
                informative.add(comp["tool"])
            if comp.get("status") == "scored" and comp.get("score") is not None:
                tool_scores[comp["tool"]].append(comp["score"])
    tools = sorted(informative, key=lambda t: (-(sum(tool_scores[t]) / len(tool_scores[t])
                                                if tool_scores[t] else -1), t))
    skipped = sorted(set(tool_seen) - informative)
    if not tools:
        out.append("*No contender was scored, unknown or not-applicable on any dimension; all "
                   f"{len(skipped)} listed tools were below the contender threshold or not in the matrix.*")
        out.append("")
    for chunk_i in range(0, len(tools), H2H_COLS_PER_TABLE):
        chunk = tools[chunk_i:chunk_i + H2H_COLS_PER_TABLE]
        hdr = "| dimension | Ziya | " + " | ".join(_short_name(cx.display(t)) for t in chunk) + " |"
        out.append(hdr)
        out.append("|---|:---:|" + ":---:|" * len(chunk))
        for dim in dims:
            label = dim["dimension_id"].split("::", 1)[-1] if "::" in dim["dimension_id"] \
                else dim["dimension_id"]
            z = dim.get("ziya") or {}
            zcell = f"{z.get('score')}/{z.get('evidence_tier', '')}" if z.get("score") is not None else "\u2013"
            comps = {c["tool"]: c for c in dim.get("competitors") or []}
            row = [f"`{_esc(label)}`", f"**{zcell}**"]
            for t in chunk:
                c = comps.get(t)
                if not c:
                    row.append("\u2013")
                elif c.get("status") == "scored":
                    row.append(f"{c.get('score')}/{c.get('evidence_tier', '')}")
                else:
                    row.append(DEPTH_GLYPH.get(c.get("status"), c.get("status")))
            out.append("| " + " | ".join(row) + " |")
        if len(tools) > H2H_COLS_PER_TABLE:
            out.append("")
            out.append(f"*(contenders {chunk_i + 1}\u2013{chunk_i + len(chunk)} of {len(tools)})*")
        out.append("")
    if skipped and tools:
        out.append(f"*Not assessed here (below contender threshold or not in matrix): "
                   f"{len(skipped)} tools \u2014 {', '.join(_short_name(cx.display(t)) for t in skipped)}.*")
        out.append("")
    for key, label in (("specific_ziya_advantage", "Ziya advantage"),
                       ("specific_leader_advantage", "Leader advantage"),
                       ("evidence_asymmetry", "Evidence asymmetry")):
        if d.get(key):
            out.append(f"- **{label}:** {_esc(d[key])}")
    crit = []
    if cid in cx.z_inflated:
        x = cx.z_inflated[cid]
        crit.append(f"Critic A (inflated score {x['claimed']}\u2192{x['defensible']}): {_esc(x['reasoning'])}")
    if cid in cx.z_overreach:
        crit.append(f"Critic A (reintegration overreach): {_esc(cx.z_overreach[cid]['why_the_match_is_wrong'])}")
    if cid in cx.z_false_uniq:
        x = cx.z_false_uniq[cid]
        crit.append(f"Critic A (false uniqueness \u2014 counterexample {x['counterexample_tool']}): {_esc(x['citation'])}")
    for (t, c), x in cx.c_overstated.items():
        if c == cid:
            crit.append(f"Critic B (overstated depth, {cx.display(t)}): {_esc(x['issue'])}")
    for (t, c), x in cx.c_laundered.items():
        if c == cid:
            crit.append(f"Critic B (laundered claim, {cx.display(t)}): {_esc(x['what_verification_showed'])}")
    for c in dict.fromkeys(crit):
        out.append(f"- \u26a0 {c}")
    out.append("")
    return out


# --------------------------------------------------------------------------
# Appendix B: per-tool scorecards
# --------------------------------------------------------------------------

def build_appendix_b(cx: Corpus) -> str:
    out = ["## Appendix B \u2014 Per-tool scorecards", "",
           f"One card per roster tool ({len(cx.tools)}). Domain rows compare the tool's mean "
           f"score on cells it is *present* in against Ziya's mean on the same capabilities "
           f"(so the two means are over the same rows). P/A/N/U = the tool's present / absent / "
           f"not_applicable / unknown cell counts in that domain. The head-to-head line counts "
           f"depth-run dimensions on which both were scored. Dossier lists are the CL2 "
           f"researcher's words; Critic B notes are quoted beneath.", ""]
    # depth h2h per tool
    h2h: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for d in cx.depth.values():
        for dim in d.get("dimensions") or []:
            zs = (dim.get("ziya") or {}).get("score")
            if zs is None:
                continue
            for c in dim.get("competitors") or []:
                if c.get("status") == "scored" and c.get("score") is not None:
                    h2h[c["tool"]]["ahead" if c["score"] > zs else "behind" if c["score"] < zs else "parity"] += 1
    for t in cx.tools:
        r = cx.roster.get(t) or {}
        dz = cx.dossiers.get(t) or {}
        prof = dz.get("profile") or {}
        rec = dz.get("recency") or {}
        out.append(f"### {cx.display(t)} (`{t}`)")
        out.append("")
        out.append(f"**{_esc(r.get('category'))}** \u00b7 {_esc(r.get('vendor') or prof.get('vendor'))} "
                   f"\u00b7 {_esc(r.get('license_hosting'))} \u00b7 local-first: {r.get('local_first')} "
                   f"\u00b7 BYO model: {r.get('byo_model')}")
        out.append("")
        if prof.get("one_liner"):
            out.append(_esc(prof["one_liner"]))
            out.append("")
        out.append(f"- **Last release:** {_esc(rec.get('last_release') or r.get('last_release'))}")
        if rec.get("activity"):
            out.append(f"- **Activity:** {_esc(rec['activity'])}")
        if t in cx.c_abandon:
            out.append(f"- \u26a0 **Critic B (abandonware):** {_esc(cx.c_abandon[t]['implication'])}")
        hc = h2h.get(t)
        if hc:
            tot = sum(hc.values())
            out.append(f"- **Head-to-head (depth run, {tot} scored dimensions):** "
                       f"{cx.display(t)} ahead {hc['ahead']} \u00b7 parity {hc['parity']} \u00b7 "
                       f"Ziya ahead {hc['behind']}")
        out.append("")
        out.append("| domain | caps | tool present | tool mean | Ziya mean (same caps) | \u0394 | A | N | U |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        tot_t: List[int] = []
        tot_z: List[int] = []
        for dom in cx.domains:
            cids = [c for c in cx.caps if cx.caps[c]["domain"] == dom]
            st = collections.Counter()
            ts: List[int] = []
            zs: List[int] = []
            for cid in cids:
                c = cx.cells.get((cid, t))
                if not c:
                    continue
                st[c["status"]] += 1
                if c["status"] == "present" and c.get("score") is not None:
                    ts.append(c["score"])
                    zc = cx.cells.get((cid, "ziya"))
                    if zc and zc.get("status") == "present" and zc.get("score") is not None:
                        zs.append(zc["score"])
            tot_t += ts
            tot_z += zs
            tm = sum(ts) / len(ts) if ts else None
            zm = sum(zs) / len(zs) if zs else None
            delta = f"{tm - zm:+.2f}" if tm is not None and zm is not None else "\u2013"
            out.append(f"| {dom} | {len(cids)} | {st['present']} | "
                       f"{tm:.2f} | {zm:.2f} | {delta} | " if tm is not None and zm is not None
                       else f"| {dom} | {len(cids)} | {st['present']} | "
                            f"{'%.2f' % tm if tm is not None else '\u2013'} | "
                            f"{'%.2f' % zm if zm is not None else '\u2013'} | {delta} | ")
            out[-1] += f"{st['absent']} | {st['not_applicable']} | {st['unknown']} |"
        if tot_t and tot_z:
            out.append(f"| **all** | {len(cx.caps)} | {len(tot_t)} | **{sum(tot_t) / len(tot_t):.2f}** | "
                       f"**{sum(tot_z) / len(tot_z):.2f}** | **{sum(tot_t) / len(tot_t) - sum(tot_z) / len(tot_z):+.2f}** | | | |")
        out.append("")
        for key, label in (("beats_ziya_on", "Beats Ziya on (dossier)"),
                           ("weaker_than_ziya_on", "Weaker than Ziya on (dossier)")):
            items = dz.get(key) or []
            if items:
                out.append(f"**{label}:**")
                out.append("")
                for it in items:
                    out.append(f"- {_esc(it)}")
                out.append("")
        if dz.get("design_bet"):
            out.append(f"**Design bet:** {_esc(dz['design_bet'])}")
            out.append("")
        notes = [f"overstated depth on `{c}`: {_esc(x['issue'])}" for (tt, c), x in cx.c_overstated.items() if tt == t]
        notes += [f"laundered claim on `{c}`: {_esc(x['what_verification_showed'])}" for (tt, c), x in cx.c_laundered.items() if tt == t]
        for nte in notes:
            out.append(f"- \u26a0 Critic B \u2014 {nte}")
        if notes:
            out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Appendix C: gap register
# --------------------------------------------------------------------------

def build_appendix_c(cx: Corpus) -> str:
    ids = sorted(set(cx.stage_a) | set(cx.dispo))
    out = ["## Appendix C \u2014 Gap register (reintegration run)", "",
           f"Reintegration run `{cx.phases['reintegration']['run_id']}`: **{len(ids)} investigated gaps**. "
           f"Stage A verdict and disposition are the CL4 records' own. *Prev \u22653* = competitors "
           f"present at score \u22653 in the current matrix (computed, not copied). Ziya score is "
           f"the current matrix cell, with a CL4 ledger correction shown as `old\u2192new` and a "
           f"Critic A downgrade as `raw\u2192**defensible**`. Effort is the disposition's "
           f"`stretch.effort_class`; a \u26a0 marks a Critic A effort-optimism finding and a "
           f"\u2298 marks a gap Critic B recommends discounting entirely; both are quoted after the table.", ""]
    out.append("| capability | domain | Stage A | disposition | effort | prev \u22653 / present | best competitor | Ziya score | flags |")
    out.append("|---|---|---|---|---|---:|---|:---:|---|")
    order = {"BUILD_CANDIDATE": 0, "LEDGER_CORRECTION": 1, "DELIBERATE_NON_GOAL": 2}
    notes: List[str] = []

    def sort_key(cid: str):
        dp = cx.dispo.get(cid) or {}
        g = cx.gap_items.get(cid) or {}
        return (order.get(_short_verdict(dp.get("disposition")), 9), -(g.get("priority_score") or 0), cid)

    for cid in sorted(ids, key=sort_key):
        sa = cx.stage_a.get(cid) or {}
        dp = cx.dispo.get(cid) or {}
        st = dp.get("stretch") or {}
        n3, n, best = cx.prevalence(cid)
        lc = cx.ledger_corr.get(cid)
        zscore = cx.ziya_score_str(cid)
        if lc and cx.ziya_corrected(cid) is None:
            zscore = f"{lc.get('old_score') if lc.get('old_score') is not None else '\u2205'}\u2192{lc.get('corrected_score')}"
        flags = []
        if cid in cx.z_effort:
            flags.append("\u26a0")
            notes.append(f"- \u26a0 `{cid}` (claimed {cx.z_effort[cid]['claimed_effort']}): {_esc(cx.z_effort[cid]['problem'])}")
        if cid in cx.c_discount:
            flags.append("\u2298")
            notes.append(f"- \u2298 `{cid}`: {_esc(cx.c_discount[cid]['reason'])}")
        if cid in cx.z_overreach:
            flags.append("A-overreach")
            notes.append(f"- A-overreach `{cid}`: {_esc(cx.z_overreach[cid]['why_the_match_is_wrong'])}")
        out.append(f"| {_esc(cx.cap_name(cid))} `{cid}` | {cx.cap_domain(cid)} | "
                   f"{_short_verdict(sa.get('verdict'))} | {_short_verdict(dp.get('disposition'))} | "
                   f"{_esc(_short_verdict(st.get('effort_class')) or '\u2013')} | {n3} / {n} | "
                   f"{_esc(best) or '\u2013'} | {zscore} | {' '.join(flags)} |")
    out.append("")
    if notes:
        out.append("**Critic notes referenced above:**")
        out.append("")
        out.extend(dict.fromkeys(notes))
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Matrix companion
# --------------------------------------------------------------------------

def build_matrix(cx: Corpus) -> str:
    codes = {t: (cx.roster.get(t) or {}).get("display_name", t)[:10] for t in cx.tools}
    out = [f"# Ziya Competitive Landscape \u2014 Full Capability Matrix", "",
           f"Companion to the synthesis report (run `{cx.synth_run}`). Matrix `30-matrix.json` "
           f"schema {cx.matrix.get('schema_version')}, cells run `{cx.phases['cells']['run_id']}`: "
           f"{len(cx.caps)} capabilities \u00d7 {len(cx.tools)} competitor tools, every cell determined.", "",
           "**Cell key:** a digit is the recorded score (0\u20135) for a *present* cell; "
           "`\u00b7` absent (determined not to have it); `~` not applicable (presupposes an "
           "architecture the tool lacks \u2014 not evidence of a Ziya lead); `?` unknown (looked, "
           "could not tell); `!` unresolved. Ziya column: matrix score, with Critic A's "
           "defensible score as `raw\u2192**def**` where they differ, and a CL4 ledger correction as "
           "`old\u2192new`. Evidence tiers and citations are in the JSON and are not reproduced here.", ""]
    for dom in cx.domains:
        cids = sorted(c for c in cx.caps if cx.caps[c]["domain"] == dom)
        out.append(f"## {dom} ({len(cids)})")
        out.append("")
        out.append("| capability | Ziya | " + " | ".join(codes[t] for t in cx.tools) + " |")
        out.append("|---|:---:|" + ":---:|" * len(cx.tools))
        for cid in cids:
            z = cx.ziya_score_str(cid)
            lc = cx.ledger_corr.get(cid)
            if lc and cx.ziya_corrected(cid) is None:
                z = f"{lc.get('old_score') if lc.get('old_score') is not None else '\u2205'}\u2192{lc.get('corrected_score')}"
            zc = cx.cells.get((cid, "ziya"))
            if zc and zc.get("status") != "present" and cx.ziya_corrected(cid) is None and not lc:
                z = STATUS_GLYPH.get(zc["status"], zc["status"])
            row = [f"{_esc(cx.cap_name(cid))} `{cid}`", z]
            for t in cx.tools:
                c = cx.cells.get((cid, t))
                if not c:
                    row.append("")
                elif c["status"] == "present":
                    row.append(str(c.get("score", "")))
                else:
                    row.append(STATUS_GLYPH.get(c["status"], c["status"]))
            out.append("| " + " | ".join(row) + " |")
        out.append("")
    # legend of tool codes
    out.append("## Tool key")
    out.append("")
    out.append("| column | tool | category |")
    out.append("|---|---|---|")
    for t in cx.tools:
        out.append(f"| {codes[t]} | {cx.display(t)} (`{t}`) | {_esc((cx.roster.get(t) or {}).get('category'))} |")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# print copy + rendering
# --------------------------------------------------------------------------

FIGURES = {
    "cell-status-by-domain": ("fig_cell_status_by_domain.png",
                              "Figure 5 \u2014 Competitor cell statuses per domain (absent / not_applicable / unknown / present), with `unknown` drawn explicitly."),
    "maturity-distribution": ("fig_maturity_distribution.png", "Figure 2 \u2014 Ziya ledger maturity histogram."),
    "gap-dispositions": ("fig_gap_dispositions.png",
                         "Figure 4 \u2014 128 investigated gaps by disposition; terminology artifacts split into critic-solid vs critic-flagged."),
    "domain-comparison": ("fig_domain_comparison.png", "Figure 1 \u2014 Ziya vs field mean maturity by domain."),
    "effort-vs-value": ("fig_effort_vs_value.png",
                        "Figure 3 \u2014 Improvement candidates: corrected effort vs corrected priority."),
}


def build_print_copy(cx: Corpus, appendices: List[str]) -> str:
    src = open(os.path.join(cx.synth_dir, "REPORT.md"), encoding="utf-8").read()
    # The machine-readable provenance comment is for check-report; with HTML passthrough off
    # it would print as literal text, and the human-readable corpus line follows it anyway.
    src = re.sub(r"<!--\s*corpus-provenance\s*\{.*?\}\s*-->\n?", "", src, flags=re.S)
    placed = set()
    out: List[str] = []
    for ln in src.split("\n"):
        out.append(ln)
        for key, (fn, cap) in FIGURES.items():
            if key in placed or ln.startswith("#") or ln.lstrip()[:2] in ("1.", "2.", "3.", "4.", "5."):
                continue
            if re.search(r"figures/" + re.escape(key), ln) and os.path.exists(os.path.join(cx.synth_dir, "figures", fn)):
                out += ["", f"![{cap}](figures/{fn})", ""]
                placed.add(key)
    body = "\n".join(out).rstrip() + "\n\n---\n\n" + "\n\n---\n\n".join(appendices) + "\n"
    return body


CSS_BASE = """
html { font-size: %(font)s; }
body { font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; line-height: 1.4; color: #1a1a1a; margin: 0; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 15pt; margin-top: 1.6em; border-bottom: 1px solid #bbb; padding-bottom: 2px; page-break-after: avoid; page-break-before: %(h2break)s; }
h3 { font-size: 12pt; margin-top: 1.2em; page-break-after: avoid; }
h4 { font-size: 10.5pt; margin-top: 1.1em; margin-bottom: 0.3em; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
code { font-family: Menlo, Consolas, monospace; font-size: 85%%; background: #f3f3f3; padding: 0 2px; border-radius: 2px; }
pre { background: #f6f6f6; padding: 8px; font-size: 8pt; white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid #ddd; }
pre code { background: none; }
table { border-collapse: collapse; width: 100%%; font-size: %(tfont)s; margin: 0.6em 0; }
th, td { border: 1px solid #bbb; padding: 2px 4px; vertical-align: top; text-align: left; }
th { background: #e9e9e9; }
tr { page-break-inside: avoid; }
td small { color: #666; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%%; max-height: 120mm; height: auto; border: 1px solid #ddd; }
figcaption { font-size: 8.5pt; color: #444; margin-top: 4px; font-style: italic; }
blockquote { border-left: 3px solid #ccc; margin-left: 0; padding-left: 10px; color: #444; }
hr { border: 0; border-top: 1px solid #ccc; }
a { color: #1f4e9c; text-decoration: none; word-break: break-all; }
"""


def md_to_html(md_path: str, title: str, css: str) -> str:
    from markdown_it import MarkdownIt
    base = os.path.dirname(md_path)
    # html=False: report prose quotes literal tags such as "<script>" and "<think>"; with HTML
    # passthrough on, Chromium treats the first as an unclosed <script> and swallows the rest
    # of the document (the first render lost 22 of 33 pages this way).
    md = MarkdownIt("gfm-like", {"html": False}).disable("linkify")

    def render_image(self, tokens, idx, options, env):
        t = tokens[idx]
        src = t.attrGet("src")
        alt = self.renderInlineAsText(t.children, options, env)
        with open(os.path.join(base, src), "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        return (f'<figure><img src="data:image/png;base64,{b64}" alt="{htmllib.escape(alt)}"/>'
                f'<figcaption>{htmllib.escape(alt)}</figcaption></figure>')

    md.add_render_rule("image", render_image)
    body = md.render(open(md_path, encoding="utf-8").read())
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{htmllib.escape(title)}</title>'
            f'<style>{css}</style></head><body>{body}</body></html>')


async def _pdf(html_path: str, pdf_path: str, footer: str, fmt: str, landscape: bool) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        await pg.goto(f"file://{os.path.abspath(html_path)}", wait_until="load")
        await pg.pdf(path=pdf_path, format=fmt, landscape=landscape, print_background=True,
                     display_header_footer=True, header_template="<div></div>",
                     footer_template=f'<div style="font-size:8px;color:#666;width:100%;text-align:center;">'
                                     f'{htmllib.escape(footer)} &nbsp;&middot;&nbsp; page '
                                     f'<span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                     margin={"top": "14mm", "bottom": "18mm", "left": "12mm", "right": "12mm"})
        await b.close()


def render(md_path: str, title: str, footer: str, css: str, fmt: str, landscape: bool,
           pdf_path: Optional[str] = None) -> str:
    html_path = md_path[:-3] + ".html"
    pdf_path = pdf_path or md_path[:-3] + ".pdf"
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(md_to_html(md_path, title, css))
    asyncio.run(_pdf(html_path, pdf_path, footer, fmt, landscape))
    return pdf_path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".ziya/complandscape")
    ap.add_argument("--no-pdf", action="store_true", help="write markdown only")
    a = ap.parse_args(argv)
    cx = Corpus(a.root)
    parts = {"APPENDIX-A-head-to-head.md": build_appendix_a(cx),
             "APPENDIX-B-tool-scorecards.md": build_appendix_b(cx),
             "APPENDIX-C-gap-register.md": build_appendix_c(cx)}
    for name, text in parts.items():
        with open(os.path.join(cx.synth_dir, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    print_md = os.path.join(cx.synth_dir, "REPORT-print.md")
    with open(print_md, "w", encoding="utf-8") as fh:
        fh.write(build_print_copy(cx, list(parts.values())))
    matrix_md = os.path.join(cx.synth_dir, "MATRIX.md")
    with open(matrix_md, "w", encoding="utf-8") as fh:
        fh.write(build_matrix(cx))
    print(f"wrote {', '.join(parts)}, REPORT-print.md, MATRIX.md in {cx.synth_dir}")
    print(f"  depth records: {len(cx.depth)}  tools: {len(cx.tools)}  gaps: {len(set(cx.stage_a) | set(cx.dispo))}  caps: {len(cx.caps)}")
    if a.no_pdf:
        return 0
    css_main = "@page { size: A4; margin: 16mm 14mm 18mm 14mm; }" + CSS_BASE % {
        "font": "10.5pt", "tfont": "7.8pt", "h2break": "auto"}
    css_matrix = "@page { size: A3 landscape; margin: 12mm; }" + CSS_BASE % {
        "font": "8pt", "tfont": "6.6pt", "h2break": "always"} + \
        "table td:first-child { min-width: 55mm; } h1 + p, h1 ~ p { page-break-before: auto; }"
    p1 = render(print_md, "Ziya Competitive Landscape \u2014 Final Report",
                f"Ziya Competitive Landscape \u2014 synthesis run {cx.synth_run}", css_main, "A4", False,
                pdf_path=os.path.join(cx.synth_dir, "REPORT.pdf"))
    p2 = render(matrix_md, "Ziya Competitive Landscape \u2014 Full Capability Matrix",
                f"Ziya Competitive Landscape \u2014 full matrix, cells run {cx.phases['cells']['run_id']}",
                css_matrix, "A3", True)
    for p in (p1, p2):
        print(f"  {p}  {os.path.getsize(p):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
