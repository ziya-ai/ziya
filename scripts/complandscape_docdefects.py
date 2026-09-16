#!/usr/bin/env python3
"""Extract the documentation-defect worklist from the complandscape corpus.

WHY THIS EXISTS
---------------
This is WS0 of ``design/docs-self-description-remediation.md``: a purely
MECHANICAL projection of the corpus into the single checklist every later
work-stream ticks off.  It reads the resolved corpus (never a typed phase
path) and emits, into the synthesis run directory:

  DOC-DEFECTS.json   {generated, runs, rows:[...], reconciliation:{...}}
  DOC-DEFECTS.md     the same rows as a readable table, grouped by class
                     then subsystem.

One row per (capability, defect class).  A capability may yield several rows
when it belongs to several classes -- e.g. an id that is both a Critic-A
inflated score and carries an under-claim in its ledger ``doc_discrepancy``.

DISCIPLINE
----------
Same as ``complandscape_appendix.py``: EXTRACT and CLASSIFY, never judge or
rewrite.  Every string is copied verbatim from the corpus; the only inference
is keyword classification of the free-text ``doc_discrepancy`` field, and
every row that came from a keyword match is marked ``auto=true`` so a human
reviewer can confirm or re-file it.  The curated classes (terminology,
non-goal from the reintegration summary; inflated, partial from Critic A)
are ``auto=false`` because they came from an explicit id list, not a guess.

The classes:
  terminology  -- the 24 ``terminology_defects_capability_ids``
  non-goal     -- the 28 ``deliberate_non_goals_capability_ids``
  inflated     -- Critic A ``inflated_scores`` (corrected_maturity = defensible)
  partial      -- Critic A ``reintegration_overreach`` (real capability ids)
  under        -- doc_discrepancy keyword: undocumented / not documented /
                  under-claim / not mentioned / invisible / omits
  over         -- doc_discrepancy keyword: over-claim / overstat / stale /
                  aspirational / not implemented / not wired / misleading
  other        -- has discrepancy text but matched no keyword (auto, keep for
                  a human to classify)
A discrepancy that reads null / none / no discrepancy / accurate / matches
yields NO row.

Usage:
  python3 scripts/complandscape_docdefects.py extract [--root .ziya/complandscape]
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from complandscape_corpus import resolve_corpus  # noqa: E402

# --------------------------------------------------------------------------
# classification vocabulary
# --------------------------------------------------------------------------

#: Discrepancy text that means "the docs are fine here" -> no row.
OK_KEYWORDS = ("null", "none", "no discrepancy", "accurate", "matches")

#: Under-claim: a shipped capability the docs do not surface.
UNDER_KEYWORDS = (
    "undocumented", "not documented", "under-claim", "under_claim",
    "underclaim", "not mentioned", "invisible", "omit",
)

#: Over-claim: the docs describe more than the code delivers.
OVER_KEYWORDS = (
    "over-claim", "over_claim", "overclaim", "overstat", "stale",
    "aspirational", "not implemented", "not wired", "misleading",
)

#: Expected per-class figures from the design doc, for the reconciliation.
EXPECTED = {
    "under": 123, "over": 20, "terminology": 24, "non-goal": 28,
    "inflated": 12, "partial": 6,
}

#: Doc files the plan cares about; a bare "FeatureInventory.md:12" in the
#: free text is normalised to "Docs/FeatureInventory.md" so target_docs
#: frequencies aggregate cleanly.  README.md stays at the repo root.
KNOWN_DOCS = {
    "FeatureInventory.md", "Capabilities.md", "competitive-analysis.md",
    "Skills.md", "Enterprise.md", "CLITasks.md", "DesignPhilosophy.md",
    "CapabilityGlossary.md", "CONTRIBUTING.md", "AGENTS.md",
    "Configuration.md", "Architecture.md", "Delegates.md", "MCP.md",
}

_DOC_TOKEN_RE = re.compile(r"(Docs/)?([A-Za-z][\w.-]*\.md)(?::(\d+))?")


def _classify_freetext(text: str) -> Optional[str]:
    """Classify a doc_discrepancy string, or None if it means 'no defect'.

    Precedence: an explicit under/over signal wins over an incidental 'ok'
    word, because a note like "...is accurate but under-claims..." is an
    under-claim, not a confirmation.  Only when no under/over keyword fires
    does an ok keyword suppress the row; anything left over is 'other'.
    """
    low = (text or "").strip().lower()
    if not low or low in ("null", "none"):
        return None
    if any(k in low for k in UNDER_KEYWORDS):
        return "under"
    if any(k in low for k in OVER_KEYWORDS):
        return "over"
    if any(k in low for k in OK_KEYWORDS):
        return None
    return "other"


def parse_target_docs(text: str) -> Tuple[List[str], Optional[int]]:
    """Pull Docs/... and README.md paths and the first cited line out of text.

    A token like ``FeatureInventory.md:12`` or ``Docs/Capabilities.md:795-825``
    contributes the path (normalised) and, for cited_line, the first integer
    (the start of any range).  Bare doc filenames are normalised to Docs/
    only when they are known plan documents, so stray ``GOVERNANCE.md``
    mentions in disposition prose do not pollute the target list.
    """
    docs: List[str] = []
    cited: Optional[int] = None
    for m in _DOC_TOKEN_RE.finditer(text or ""):
        prefixed, name, line = m.group(1), m.group(2), m.group(3)
        if prefixed:
            path = "Docs/" + name
        elif name == "README.md":
            path = "README.md"
        elif name in KNOWN_DOCS:
            path = "Docs/" + name
        else:
            continue
        if path not in docs:
            docs.append(path)
        if line and cited is None:
            cited = int(line)
    return docs, cited


# --------------------------------------------------------------------------
# corpus loading
# --------------------------------------------------------------------------

def _j(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class DocDefectCorpus:
    """The narrow slice of the corpus WS0 needs, resolved and indexed."""

    def __init__(self, root: str):
        self.root = root
        res = resolve_corpus(root)
        self.phases = res["phases"]

        self.synth_run = open(
            os.path.join(root, "60-synthesis", "CURRENT_RUN"),
            encoding="utf-8").read().strip()
        self.synth_dir = os.path.join(root, "60-synthesis", self.synth_run)
        self.rei_dir = self.phases["reintegration"]["dir"]
        self.rei_run = self.phases["reintegration"]["run_id"]

        self.ledger = _j(os.path.join(root, "19-ziya-ledger.json"))
        self.matrix = _j(os.path.join(root, "30-matrix.json"))
        self.summary = _j(os.path.join(root, "41-reintegration-summary.json"))
        self.crit_z = _j(os.path.join(self.synth_dir, "60-critique-ziya.json"))

        self.ledger_caps: Dict[str, dict] = {
            c["id"]: c for c in self.ledger["capabilities"]}
        self.matrix_caps: Dict[str, dict] = {
            c["id"]: c for c in self.matrix["capabilities"]}

        # curated id sets
        self.terminology_ids: List[str] = list(
            self.summary.get("terminology_defects_capability_ids") or [])
        self.nongoal_ids: List[str] = list(
            self.summary.get("deliberate_non_goals_capability_ids") or [])
        self.inflated: Dict[str, dict] = {
            x["capability_id"]: x
            for x in self.crit_z.get("inflated_scores") or []}
        # reintegration_overreach carries a trailing SYSTEMIC pseudo-entry
        # whose "capability_id" is not a real capability; keep only ids that
        # resolve to a real capability.  A partial-match id may be
        # competitor-sourced (e.g. lint-test-fix-repair-loop) and therefore
        # present in the matrix universe but not the Ziya ledger, so accept
        # either.
        self.overreach: Dict[str, dict] = {
            x["capability_id"]: x
            for x in self.crit_z.get("reintegration_overreach") or []
            if x.get("capability_id") in self.ledger_caps
            or x.get("capability_id") in self.matrix_caps}

        # Stage A / disposition records, indexed by capability id.
        self.stage_a: Dict[str, dict] = {}
        self.dispo: Dict[str, dict] = {}
        if self.rei_dir and os.path.isdir(self.rei_dir):
            for fn in sorted(os.listdir(self.rei_dir)):
                if fn.endswith("-stageA.json"):
                    rec = _j(os.path.join(self.rei_dir, fn))
                    cid = rec.get("capability_id") or fn[:-len("-stageA.json")]
                    self.stage_a[cid] = rec
                elif fn.endswith("-disposition.json"):
                    rec = _j(os.path.join(self.rei_dir, fn))
                    cid = rec.get("capability_id") or fn[:-len("-disposition.json")]
                    self.dispo[cid] = rec

    # ---- per-capability lookups ----
    def name(self, cid: str) -> str:
        c = self.ledger_caps.get(cid) or self.matrix_caps.get(cid) or {}
        return c.get("name") or cid

    def subsystem(self, cid: str) -> str:
        return (self.ledger_caps.get(cid) or {}).get("subsystem") or ""

    def domain(self, cid: str) -> str:
        return (self.matrix_caps.get(cid) or {}).get("domain") or ""

    def maturity(self, cid: str) -> Any:
        return (self.ledger_caps.get(cid) or {}).get("maturity")

    def aliases(self, cid: str) -> List[str]:
        return list((self.ledger_caps.get(cid) or {}).get("aliases") or [])

    def evidence(self, cid: str) -> List[str]:
        out = []
        for e in (self.ledger_caps.get(cid) or {}).get("evidence") or []:
            if isinstance(e, dict):
                path = e.get("path", "")
                lines = e.get("lines")
                out.append(f"{path}:{lines}" if lines else path)
            else:
                out.append(str(e))
        return out

    def ziya_internal_name(self, cid: str) -> Optional[str]:
        rec = self.stage_a.get(cid)
        return (rec or {}).get("ziya_internal_name")

    def fraction_present(self, cid: str) -> Any:
        rec = self.stage_a.get(cid)
        return (rec or {}).get("fraction_present_if_partial")

    def corrected_maturity(self, cid: str) -> Any:
        """Critic A defensible score where present, else None."""
        x = self.inflated.get(cid)
        return x.get("defensible") if x else None

    def defect_text(self, cid: str) -> str:
        """Best available documentation-defect prose for a curated-class row.

        Prefers the disposition's documentation_defect (the most considered
        wording), then Stage A's, then the ledger's raw doc_discrepancy.
        """
        for src in (self.dispo.get(cid), self.stage_a.get(cid)):
            t = (src or {}).get("documentation_defect")
            if t and str(t).strip().lower() not in ("null", "none", ""):
                return str(t)
        return str((self.ledger_caps.get(cid) or {}).get("doc_discrepancy") or "")


# --------------------------------------------------------------------------
# row construction
# --------------------------------------------------------------------------

def _row(cx: DocDefectCorpus, cid: str, cls: str, text: str,
         auto: bool) -> Dict[str, Any]:
    docs, line = parse_target_docs(text)
    return {
        "capability_id": cid,
        "name": cx.name(cid),
        "subsystem": cx.subsystem(cid),
        "domain": cx.domain(cid),
        "class": cls,
        "ledger_maturity": cx.maturity(cid),
        "corrected_maturity": cx.corrected_maturity(cid),
        "target_docs": docs,
        "cited_line": line,
        "discrepancy_text": text,
        "aliases": cx.aliases(cid),
        "ziya_internal_name": cx.ziya_internal_name(cid),
        "fraction_present_if_partial": cx.fraction_present(cid),
        "evidence": cx.evidence(cid),
        "auto": auto,
        "reviewed": False,
        "closed": False,
        "closed_by": None,
    }


def build_rows(cx: DocDefectCorpus) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    # Curated classes -- explicit id lists, so auto=False.
    for cid in cx.terminology_ids:
        rows.append(_row(cx, cid, "terminology", cx.defect_text(cid), False))
    for cid in cx.nongoal_ids:
        rows.append(_row(cx, cid, "non-goal", cx.defect_text(cid), False))
    for cid, x in cx.inflated.items():
        text = (f"Critic A inflated score {x.get('claimed')}->"
                f"{x.get('defensible')}: {x.get('reasoning', '')}")
        rows.append(_row(cx, cid, "inflated", text, False))
    for cid, x in cx.overreach.items():
        text = f"Critic A reintegration overreach: {x.get('why_the_match_is_wrong', '')}"
        rows.append(_row(cx, cid, "partial", text, False))

    # Free-text keyword classes -- applied to every capability, so a curated
    # capability that also carries an under/over discrepancy yields both rows.
    for cid, cap in cx.ledger_caps.items():
        text = str(cap.get("doc_discrepancy") or "")
        cls = _classify_freetext(text)
        if cls is not None:
            rows.append(_row(cx, cid, cls, text, True))

    return rows


def build_reconciliation(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    per_class = collections.Counter(r["class"] for r in rows)
    classes = sorted(set(list(EXPECTED) + list(per_class)))
    counts = {}
    for c in classes:
        counts[c] = {
            "actual": per_class.get(c, 0),
            "expected": EXPECTED.get(c),
            "delta": (per_class.get(c, 0) - EXPECTED[c]) if c in EXPECTED else None,
        }
    doc_freq = collections.Counter()
    for r in rows:
        for d in r["target_docs"]:
            doc_freq[d] += 1
    return {
        "total_rows": len(rows),
        "capabilities_with_rows": len({r["capability_id"] for r in rows}),
        "auto_rows": sum(1 for r in rows if r["auto"]),
        "reviewed_rows": sum(1 for r in rows if r["reviewed"]),
        "per_class": counts,
        "target_docs_frequency": dict(
            sorted(doc_freq.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


# --------------------------------------------------------------------------
# markdown rendering
# --------------------------------------------------------------------------

CLASS_ORDER = ["over", "inflated", "partial", "under", "terminology",
               "non-goal", "other"]
CLASS_BLURB = {
    "over": "Over-claims: docs describe more than the code delivers. Fix first (correctness).",
    "inflated": "Critic A inflated scores the docs must not echo (corrected maturity shown).",
    "partial": "Critic A reintegration overreach: FOUND stretched from a partial match.",
    "under": "Under-claims: shipped, competitively relevant, absent from the docs.",
    "terminology": "Terminology artifacts: shipped under a non-marketing name.",
    "non-goal": "Deliberate non-goals: a decision, stated nowhere as one.",
    "other": "Has discrepancy text but matched no keyword -- needs human classification.",
}


def _esc(s: Any) -> str:
    return str("" if s is None else s).replace("|", "\\|").replace("\n", " ").strip()


def _clip(s: str, n: int = 160) -> str:
    s = _esc(s)
    return s if len(s) <= n else s[: n - 1].rstrip() + "\u2026"


def build_markdown(cx: DocDefectCorpus, rows: List[Dict[str, Any]],
                   recon: Dict[str, Any], runs: Dict[str, Any],
                   generated: str) -> str:
    out: List[str] = []
    out.append("# Documentation-defect worklist (WS0)")
    out.append("")
    out.append(f"Generated {generated} by `scripts/complandscape_docdefects.py`. "
               "Mechanical projection of the corpus -- extract and classify only, "
               "no judgment. Rows flagged `auto` came from keyword matching of the "
               "ledger `doc_discrepancy` free text and must be reviewed.")
    out.append("")
    out.append(f"Corpus runs: synthesis `{runs.get('synthesis')}`, "
               f"reintegration `{runs.get('reintegration')}`, "
               f"cells `{runs.get('cells')}`, depth `{runs.get('depth')}`.")
    out.append("")

    # reconciliation
    out.append("## Reconciliation")
    out.append("")
    out.append(f"**{recon['total_rows']} rows** across "
               f"{recon['capabilities_with_rows']} capabilities "
               f"({recon['auto_rows']} auto, {recon['reviewed_rows']} reviewed).")
    out.append("")
    out.append("| class | actual | expected | delta |")
    out.append("|---|---:|---:|---:|")
    for c in CLASS_ORDER + [c for c in recon["per_class"] if c not in CLASS_ORDER]:
        if c not in recon["per_class"]:
            continue
        e = recon["per_class"][c]
        exp = "" if e["expected"] is None else e["expected"]
        dlt = "" if e["delta"] is None else f"{e['delta']:+d}"
        out.append(f"| {c} | {e['actual']} | {exp} | {dlt} |")
    out.append("")
    out.append("### Target-doc frequency")
    out.append("")
    out.append("| doc | rows citing it |")
    out.append("|---|---:|")
    for doc, n in recon["target_docs_frequency"].items():
        out.append(f"| {_esc(doc)} | {n} |")
    out.append("")

    # rows grouped by class then subsystem
    by_class: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        by_class[r["class"]].append(r)
    for cls in CLASS_ORDER + [c for c in by_class if c not in CLASS_ORDER]:
        group = by_class.get(cls)
        if not group:
            continue
        out.append(f"## {cls} ({len(group)})")
        out.append("")
        if cls in CLASS_BLURB:
            out.append(f"*{CLASS_BLURB[cls]}*")
            out.append("")
        by_sub: Dict[str, List[dict]] = collections.defaultdict(list)
        for r in group:
            by_sub[r["subsystem"] or "(no subsystem)"].append(r)
        for sub in sorted(by_sub):
            sub_rows = sorted(by_sub[sub], key=lambda r: r["capability_id"])
            out.append(f"### {sub} ({len(sub_rows)})")
            out.append("")
            out.append("| capability | mat | corr | target docs | line | discrepancy |")
            out.append("|---|:---:|:---:|---|---:|---|")
            for r in sub_rows:
                docs = ", ".join(r["target_docs"]) or "\u2013"
                mat = "" if r["ledger_maturity"] is None else r["ledger_maturity"]
                corr = "" if r["corrected_maturity"] is None else r["corrected_maturity"]
                line = "" if r["cited_line"] is None else r["cited_line"]
                out.append(
                    f"| {_esc(r['name'])} `{r['capability_id']}` | {mat} | {corr} "
                    f"| {_esc(docs)} | {line} | {_clip(r['discrepancy_text'])} |")
            out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# glossary command (WS2 -- generated reader-vocabulary index)
# --------------------------------------------------------------------------
#
# WHY THIS EXISTS
# ---------------
# WS2 of the plan: a reader-vocabulary -> Ziya-name cross reference, GENERATED
# from the ledger ``aliases`` field so it cannot rot independently of the
# ledger.  For every capability at maturity >= 2 we emit one entry, grouped by
# matrix domain then alphabetically by name, carrying the capability name (as a
# link into ``Capabilities.md``), its Ziya internal id in code font, the
# maturity, and an ``also:`` list of alias vocabulary.  Maturity <= 1
# capabilities are parked, alias-free, in a final "Experimental or vestigial"
# subsection.  Output is deterministic: same corpus in -> identical bytes out,
# with no timestamp inside the generated region (WS6 regenerates and diffs it).
#
# The link target is the GitHub-style slug of the nearest heading in
# ``Capabilities.md`` that NAMES the capability (see ``find_anchor``).  Most
# mature capabilities have no such heading yet -- that is exactly the WS3
# under-claim backlog -- so those ids are collected into a "no anchor" list
# printed to stderr and returned for the caller to hand to WS3.

GLOSSARY_PATH = os.path.join("Docs", "CapabilityGlossary.md")
CAPABILITIES_DOC = "Capabilities.md"  # relative to Docs/, where the glossary lives
GEN_BEGIN = "<!-- GENERATED-BEGIN -->"
GEN_END = "<!-- GENERATED-END -->"
MAX_ALIASES = 8

#: The hand-authored scaffold written when the glossary does not yet exist.
#: ``--write`` only ever rewrites the region between the GENERATED markers, so
#: this preamble and the placeholder section are preserved across regenerations.
GLOSSARY_SCAFFOLD = f"""\
# Ziya Capability Glossary

This file is a **reader-vocabulary -> Ziya-name** cross reference. Ziya's own
docs describe capabilities by their internal mechanism name; this glossary lets
a reader who searches by the *vocabulary they already know* (a competitor's
term, a plain-English phrase) land on the right Ziya capability and its detail
in [Capabilities.md]({CAPABILITIES_DOC}).

The region between the two `GENERATED` markers below is produced mechanically
from the capability ledger (`19-ziya-ledger.json`) and the capability matrix
(`30-matrix.json`). **Do not hand-edit inside the markers** -- your changes
will be overwritten. To regenerate after the ledger changes:

```
python3 scripts/complandscape_docdefects.py glossary --write
```

Everything *outside* the markers (this preamble and the "Terms competitors use"
section) is hand-maintained and preserved by `--write`.

## Terms competitors use

<!-- Hand-maintained in a later stage (WS2): for each terminology artifact,
     the competitor whose word it is, mapped to the Ziya capability and its
     critic-corrected verdict. Left as a placeholder heading for now. -->

_To be written._

{GEN_BEGIN}
{GEN_END}
"""


def gh_slug(text: str) -> str:
    """GitHub-style heading slug: lowercase, drop punctuation, spaces->hyphens.

    Matches GitHub's algorithm closely enough for in-repo anchors: strip, lower,
    remove everything that is not a word char / whitespace / hyphen, then turn
    runs of whitespace into single hyphens.  Ampersands and slashes simply
    vanish (as they do on GitHub), which is why "Context & Projects" ->
    "context--projects".
    """
    s = (text or "").strip().lower()
    s = s.replace("&", "")
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    # GitHub replaces each whitespace char with a hyphen and does NOT collapse
    # runs, so "Context & Projects" -> "context--projects" (the & vanishes but
    # both surrounding spaces become hyphens).
    s = re.sub(r"\s", "-", s)
    return s


def parse_headings(path: str) -> List[Tuple[str, str]]:
    """Return [(heading_text, unique_slug)] for a markdown file, in doc order.

    Fenced code blocks are skipped so a ``# comment`` inside a shell example is
    not mistaken for a heading.  Duplicate slugs are disambiguated GitHub-style
    (``slug``, ``slug-1``, ``slug-2`` ...), so the returned slug is the one that
    actually resolves in a rendered document.
    """
    out: List[Tuple[str, str]] = []
    seen: Dict[str, int] = {}
    in_fence = False
    fence_re = re.compile(r"^\s*(```|~~~)")
    head_re = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if fence_re.match(line):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                m = head_re.match(line)
                if not m:
                    continue
                text = m.group(2).strip()
                if not text:
                    continue
                base = gh_slug(text)
                if base in seen:
                    seen[base] += 1
                    slug = f"{base}-{seen[base]}"
                else:
                    seen[base] = 0
                    slug = base
                out.append((text, slug))
    except FileNotFoundError:
        return []
    return out


def disp_name(name: Any) -> str:
    """Ledger names are almost always strings; a couple are lists of synonyms."""
    if isinstance(name, list):
        return " / ".join(str(x) for x in name)
    return str(name)


def find_anchor(name: str, cid: str,
                headings: List[Tuple[str, str]]) -> Optional[str]:
    """Slug of the heading that NAMES this capability, or None.

    A heading matches when it names the capability unambiguously -- one of:
      * the capability id is a substring of the heading text or its slug;
      * the full capability name (lowercased) is a substring of the heading;
      * the heading text (>= 5 chars, lowercased) is a substring of the name
        (the heading spells out a recognisable sub-phrase of the name).
    Fuzzy token overlap is deliberately NOT used: a wrong anchor sends the
    reader to the wrong section AND hides the capability from the WS3 backlog,
    so an unmatched capability is reported as 'no anchor' instead.  Among
    matches the most specific (longest heading text) wins; ties break to the
    earliest heading in the document, which is deterministic.
    """
    nl = disp_name(name).lower()
    il = cid.lower()
    best: Optional[Tuple[int, int, str]] = None  # (score, -index, slug)
    for idx, (text, slug) in enumerate(headings):
        hl = text.lower()
        matched = False
        if il and (il in hl or il in slug):
            matched = True
        elif nl and nl in hl:
            matched = True
        elif len(hl) >= 5 and hl in nl:
            matched = True
        if matched:
            key = (len(hl), -idx, slug)
            if best is None or key > best:
                best = key
    return best[2] if best else None


def _is_code_identifier(alias: str) -> bool:
    """True for code-shaped alias strings (function/attr/module names).

    Reader-facing phrases sort ahead of these.  A string is 'code' if it carries
    identifier punctuation (``_ . / :: ( )``) or interior camelCase, which
    reliably separates ``ProjectContextMiddleware._ensure_ast_indexed`` and
    ``initialize_ast_capabilities`` from ``codebase indexing``.
    """
    s = (alias or "").strip()
    if not s:
        return True
    if re.search(r"[_/().]", s) or "::" in s:
        return True
    if re.search(r"[a-z][A-Z]", s):  # camelCase
        return True
    return False


def select_aliases(aliases: List[str], cap: int = MAX_ALIASES) -> List[str]:
    """Dedupe case-insensitively, then deterministically keep the best ``cap``.

    Dedup keeps the first spelling seen (stable).  The survivors are ordered
    reader-facing-before-code, then shorter-before-longer, then alphabetically
    -- a total order, so the selection and its order are fully deterministic.
    """
    seen: set = set()
    deduped: List[str] = []
    for a in aliases:
        a = str(a).strip()
        if not a:
            continue
        low = a.lower()
        if low in seen:
            continue
        seen.add(low)
        deduped.append(a)
    deduped.sort(key=lambda a: (_is_code_identifier(a), len(a), a.lower()))
    return deduped[:cap]


def build_glossary(cx: DocDefectCorpus,
                   capabilities_path: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the generated region plus the no-anchor id list.

    Returns {region, entries, mature, vestigial, domains, no_anchor}.  ``region``
    is the exact text (no trailing newline) that lives between the markers.
    ``capabilities_path`` names the doc whose headings supply anchors; it
    defaults to the repo's ``Docs/Capabilities.md`` and is overridable for tests.
    """
    # ``Docs/Capabilities.md`` is a repo path (project root), not a
    # corpus-relative path; resolve it from the current working directory.
    if capabilities_path is None:
        capabilities_path = os.path.join("Docs", "Capabilities.md")
    headings = parse_headings(capabilities_path)

    domain_order = list(cx.matrix.get("domains") or [])
    mature: Dict[str, List[dict]] = collections.defaultdict(list)
    vestigial: List[dict] = []
    no_anchor: List[str] = []

    for cid in sorted(cx.ledger_caps):
        cap = cx.ledger_caps[cid]
        mat = cap.get("maturity")
        matv = mat if isinstance(mat, int) else 0
        name = disp_name(cap.get("name") or cid)
        anchor = find_anchor(name, cid, headings)
        if anchor is None:
            no_anchor.append(cid)
        entry = {
            "id": cid,
            "name": name,
            "maturity": mat,
            "domain": cx.domain(cid),
            "anchor": anchor,
            "aliases": select_aliases(cx.aliases(cid)),
        }
        if matv >= 2:
            mature[entry["domain"] or "(uncategorised)"].append(entry)
        else:
            vestigial.append(entry)

    # deterministic ordering
    for dom in mature:
        mature[dom].sort(key=lambda e: (e["name"].lower(), e["id"]))
    vestigial.sort(key=lambda e: (e["name"].lower(), e["id"]))
    ordered_domains = [d for d in domain_order if d in mature]
    ordered_domains += sorted(d for d in mature if d not in domain_order)

    def link(e: dict) -> str:
        if e["anchor"]:
            return f"[{e['name']}]({CAPABILITIES_DOC}#{e['anchor']})"
        return f"[{e['name']}]({CAPABILITIES_DOC})"

    lines: List[str] = [GEN_BEGIN]
    lines.append("<!-- Generated by `scripts/complandscape_docdefects.py "
                 "glossary --write`. Do not edit inside the markers. -->")
    lines.append("")
    lines.append("## Capabilities by domain")
    lines.append("")
    total_entries = 0
    for dom in ordered_domains:
        entries = mature[dom]
        lines.append(f"### {dom} ({len(entries)})")
        lines.append("")
        for e in entries:
            total_entries += 1
            mstr = "" if e["maturity"] is None else e["maturity"]
            row = f"- {link(e)} \u2014 `{e['id']}` \u00b7 maturity {mstr}"
            if e["aliases"]:
                row += " \u00b7 also: " + ", ".join(e["aliases"])
            lines.append(row)
        lines.append("")

    lines.append("## Experimental or vestigial")
    lines.append("")
    lines.append("Capabilities at maturity \u2264 1: present in the codebase but "
                 "experimental, vestigial or not wired at runtime. Listed without "
                 "alias expansion; not to be documented as shipped features.")
    lines.append("")
    for e in vestigial:
        total_entries += 1
        mstr = "" if e["maturity"] is None else e["maturity"]
        lines.append(f"- {link(e)} \u2014 `{e['id']}` \u00b7 maturity {mstr}")
    lines.append("")
    lines.append(GEN_END)

    return {
        "region": "\n".join(lines),
        "entry_count": total_entries,
        "mature_count": sum(len(v) for v in mature.values()),
        "vestigial_count": len(vestigial),
        "domain_count": len(ordered_domains),
        "no_anchor": no_anchor,
    }


def replace_region(existing: str, region: str) -> str:
    """Swap the text between the GENERATED markers, preserving everything else."""
    b = existing.find(GEN_BEGIN)
    e = existing.find(GEN_END)
    if b == -1 or e == -1 or e < b:
        raise ValueError(
            f"{GLOSSARY_PATH}: could not find '{GEN_BEGIN}' .. '{GEN_END}' "
            "markers to replace")
    head = existing[:b]
    tail = existing[e + len(GEN_END):]
    return head + region + tail


def glossary(root: str, write: bool, path: str = GLOSSARY_PATH,
             capabilities_path: Optional[str] = None) -> Dict[str, Any]:
    """Build the glossary region; --stdout prints it, --write splices it in.

    On --write, if the target file does not exist it is first created from the
    hand-authored scaffold (preamble + placeholder + empty marker region), then
    the region is filled.  Only the marker region is ever rewritten thereafter.
    ``capabilities_path`` is forwarded to :func:`build_glossary` (overridable
    for tests); ``path`` is the glossary file to write.
    """
    cx = DocDefectCorpus(root)
    res = build_glossary(cx, capabilities_path=capabilities_path)
    if write:
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(GLOSSARY_SCAFFOLD)
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
        updated = replace_region(existing, res["region"])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(updated)
        res["written"] = path
    return res


# --------------------------------------------------------------------------
# extract command
# --------------------------------------------------------------------------

def extract(root: str) -> Dict[str, Any]:
    cx = DocDefectCorpus(root)
    rows = build_rows(cx)
    recon = build_reconciliation(rows)
    generated = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    runs = {
        "synthesis": cx.synth_run,
        "reintegration": cx.rei_run,
        "cells": cx.phases["cells"]["run_id"],
        "depth": cx.phases["depth"]["run_id"],
    }
    doc = {
        "generated": generated,
        "runs": runs,
        "rows": rows,
        "reconciliation": recon,
    }
    json_path = os.path.join(cx.synth_dir, "DOC-DEFECTS.json")
    md_path = os.path.join(cx.synth_dir, "DOC-DEFECTS.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(build_markdown(cx, rows, recon, runs, generated))
        fh.write("\n")
    return {"json_path": json_path, "md_path": md_path, "doc": doc}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("extract", "glossary"))
    ap.add_argument("--root", default=".ziya/complandscape")
    ap.add_argument("--write", action="store_true",
                    help="glossary: splice the generated region into "
                         "Docs/CapabilityGlossary.md (creating it if absent)")
    ap.add_argument("--stdout", action="store_true",
                    help="glossary: print the generated region to stdout")
    args = ap.parse_args(argv)

    if args.command == "extract":
        res = extract(args.root)
        recon = res["doc"]["reconciliation"]
        print(f"wrote {res['json_path']}")
        print(f"wrote {res['md_path']}")
        print(f"  {recon['total_rows']} rows, "
              f"{recon['capabilities_with_rows']} capabilities, "
              f"{recon['auto_rows']} auto")
        for c, e in recon["per_class"].items():
            exp = "" if e["expected"] is None else f" (expected {e['expected']})"
            print(f"    {c:12s} {e['actual']:4d}{exp}")
        return 0

    if args.command == "glossary":
        res = glossary(args.root, write=args.write)
        if args.stdout:
            print(res["region"])
        else:
            if res.get("written"):
                print(f"wrote {res['written']}")
            print(f"  {res['entry_count']} entries "
                  f"({res['mature_count']} mature across {res['domain_count']} "
                  f"domains, {res['vestigial_count']} experimental/vestigial)")
            print(f"  {len(res['no_anchor'])} capabilities with no "
                  f"Capabilities.md anchor (WS3 backlog)")
        if res["no_anchor"]:
            sys.stderr.write(
                "no anchor (no matching Capabilities.md heading) for "
                f"{len(res['no_anchor'])} ids:\n")
            for cid in res["no_anchor"]:
                sys.stderr.write(f"  {cid}\n")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
