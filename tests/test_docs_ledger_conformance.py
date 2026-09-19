"""Conformance between the shipped docs and the capability ledger (WS6).

THE DEFECT THIS EXISTS FOR
--------------------------
The competitive-landscape study found that Ziya's docs describe capabilities
by *mechanism name* while readers, competitors and the study's own first pass
search by *capability vocabulary*.  The measured consequence: 107 capabilities
at maturity >= 3 -- every one competitively relevant -- were shipped but
invisible in the docs, ~20 were over-claimed (described as fuller than the
code), and the generated glossary can rot independently of the ledger.

This test is the anti-recurrence guard from the remediation plan (WS6).  It
asserts three things, and NONE of them may be weakened to go green:

  (1) COVERAGE     every mature (>=3) ledger capability is findable in the
                   docs by its id, name or one of its recorded aliases;
  (2) OVER-CLAIM   every literally-false sentence WS1 removed stays removed;
  (3) FRESHNESS    Docs/CapabilityGlossary.md's generated region is byte-for-
                   byte identical to a fresh regeneration from the ledger.

The ledger and the synthesis run are LOCAL study data, not shipped with the
repo, so every corpus-backed test skips cleanly when they are absent.  A
COVERAGE failure is a FINDING about the docs, not a bug in this test: the
assertion lists every still-missing id so the gap can be closed, never hidden.
"""
import json
import os
import re
import subprocess
import sys

import pytest

# --------------------------------------------------------------------------
# Paths and corpus presence
# --------------------------------------------------------------------------
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
DOCS_DIR = os.path.join(ROOT, "Docs")
README = os.path.join(ROOT, "README.md")
LEDGER = os.path.join(ROOT, ".ziya", "complandscape", "19-ziya-ledger.json")
SYNTH_DIR = os.path.join(ROOT, ".ziya", "complandscape", "60-synthesis")
CURRENT_RUN = os.path.join(SYNTH_DIR, "CURRENT_RUN")
GLOSSARY = os.path.join(DOCS_DIR, "CapabilityGlossary.md")
DOCDEFECTS_SCRIPT = os.path.join(ROOT, "scripts", "complandscape_docdefects.py")

GEN_BEGIN = "<!-- GENERATED-BEGIN -->"
GEN_END = "<!-- GENERATED-END -->"
MATURE = 3  # docs must make every capability at this maturity or above findable

HAVE_LEDGER = os.path.exists(LEDGER)


def _run_dir():
    """RUN_DIR = 60-synthesis/<contents of CURRENT_RUN>, or None if absent."""
    if not os.path.exists(CURRENT_RUN):
        return None
    with open(CURRENT_RUN, encoding="utf-8") as fh:
        run = fh.read().strip()
    d = os.path.join(SYNTH_DIR, run)
    return d if os.path.isdir(d) else None


def _norm_ws(s):
    """Collapse all runs of whitespace to a single space and strip the ends."""
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------
# Loaders (module scope -- built at most once)
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ledger_caps():
    if not HAVE_LEDGER:
        pytest.skip("complandscape ledger not present (local study data)")
    with open(LEDGER, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["capabilities"]


@pytest.fixture(scope="module")
def defect_rows():
    """Rows of RUN_DIR/DOC-DEFECTS.json, or [] when the worklist is absent."""
    if not HAVE_LEDGER:
        pytest.skip("complandscape ledger not present (local study data)")
    rd = _run_dir()
    if rd is None:
        return []
    path = os.path.join(rd, "DOC-DEFECTS.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("rows", [])


@pytest.fixture(scope="module")
def docs_text():
    """Lower-cased concatenation of every Docs/**/*.md plus README.md.

    Built exactly once for the module: coverage searches this single blob.
    """
    if not HAVE_LEDGER:
        pytest.skip("complandscape ledger not present (local study data)")
    parts = []
    for base, _dirs, files in os.walk(DOCS_DIR):
        for fn in sorted(files):
            if fn.endswith(".md"):
                with open(os.path.join(base, fn), encoding="utf-8") as fh:
                    parts.append(fh.read())
    if os.path.exists(README):
        with open(README, encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts).lower()


# --------------------------------------------------------------------------
# (1) COVERAGE
# --------------------------------------------------------------------------
# Dispositions on a DOC-DEFECTS row that mean "deliberately kept OUT of the
# docs because the capability is dead or experimental".  Such a capability is
# exempt from the coverage requirement -- but the exemption is printed, so it
# is a visible decision rather than a silent hole.
_EXCLUSION_KEYWORDS = ("dead", "experiment", "vestig", "exclud", "not-a-feature")


def _exclusion_reasons(defect_rows):
    """Map capability_id -> [disposition strings] that deliberately exclude it."""
    reasons = {}
    for r in defect_rows:
        disp = (r.get("disposition") or "").lower()
        if any(k in disp for k in _EXCLUSION_KEYWORDS):
            reasons.setdefault(r["capability_id"], [])
            if r["disposition"] not in reasons[r["capability_id"]]:
                reasons[r["capability_id"]].append(r["disposition"])
    return reasons


@pytest.mark.skipif(not HAVE_LEDGER, reason="complandscape ledger not present")
def test_every_mature_capability_is_findable_in_docs(ledger_caps, docs_text,
                                                     defect_rows):
    exempt = _exclusion_reasons(defect_rows)
    missing = []          # (id, subsystem, maturity) still absent
    exempted = []         # (id, [reasons]) absent but deliberately so

    for cap in ledger_caps:
        mat = cap.get("maturity")
        if not (isinstance(mat, int) and mat >= MATURE):
            continue
        cid = cap["id"]
        candidates = [cid, cap.get("name") or ""] + list(cap.get("aliases") or [])
        found = any(c and c.lower() in docs_text for c in candidates)
        if found:
            continue
        if cid in exempt:
            exempted.append((cid, exempt[cid]))
        else:
            missing.append((cid, cap.get("subsystem"), mat))

    if missing:
        lines = [
            f"{len(missing)} capabilities at maturity >= {MATURE} are not "
            "findable in Docs/**/*.md or README.md by id, name or alias.",
            "Close the gap (WS3) -- do not weaken this test:",
        ]
        for cid, sub, mat in sorted(missing):
            lines.append(f"  - {cid}  [subsystem={sub}, maturity={mat}]")
        if exempted:
            lines.append("")
            lines.append("Deliberately excluded (dead/experimental) and thus "
                         "exempt:")
            for cid, why in sorted(exempted):
                lines.append(f"  - {cid}  [{', '.join(why)}]")
        pytest.fail("\n".join(lines))


# --------------------------------------------------------------------------
# (2) OVER-CLAIM REGRESSION
# --------------------------------------------------------------------------
def _closed_overclaims(defect_rows):
    """WS1-closed 'over' rows that recorded the literally-false sentence removed."""
    return [
        r for r in defect_rows
        if r.get("class") == "over" and r.get("closed")
        and r.get("removed_sentence")
    ]


@pytest.mark.skipif(not HAVE_LEDGER, reason="complandscape ledger not present")
def test_removed_overclaims_do_not_reappear(defect_rows):
    rows = _closed_overclaims(defect_rows)
    if not rows:
        pytest.xfail("WS1 recorded no removed sentences")

    reappeared = []
    for r in rows:
        doc = r.get("target_doc")
        assert doc, (
            f"over-claim row for {r['capability_id']} has removed_sentence "
            "but no target_doc naming where it was removed from")
        path = os.path.join(ROOT, doc)
        if not os.path.exists(path):
            reappeared.append((r["capability_id"], doc, "TARGET DOC MISSING"))
            continue
        with open(path, encoding="utf-8") as fh:
            haystack = _norm_ws(fh.read())
        needle = _norm_ws(r["removed_sentence"])
        if needle in haystack:
            reappeared.append((r["capability_id"], doc, needle))

    if reappeared:
        lines = ["Removed over-claim sentence(s) reappeared in the docs:"]
        for cid, doc, needle in reappeared:
            lines.append(f"  - {cid} in {doc}: {needle!r}")
        pytest.fail("\n".join(lines))


# --------------------------------------------------------------------------
# (3) GLOSSARY FRESHNESS
# --------------------------------------------------------------------------
@pytest.mark.skipif(not HAVE_LEDGER, reason="complandscape ledger not present")
def test_glossary_region_matches_fresh_regeneration():
    assert os.path.exists(DOCDEFECTS_SCRIPT), (
        f"missing {DOCDEFECTS_SCRIPT}; WS0/WS2 own it")

    proc = subprocess.run(
        [sys.executable, DOCDEFECTS_SCRIPT, "glossary", "--stdout"],
        cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(
            "`python3 scripts/complandscape_docdefects.py glossary --stdout` "
            f"failed (rc={proc.returncode}). WS2 was to add the `glossary "
            "--stdout` subcommand.\nstderr:\n" + proc.stderr)

    generated = proc.stdout
    if generated.endswith("\n"):
        generated = generated[:-1]  # the trailing newline `print` adds

    assert os.path.exists(GLOSSARY), f"missing {GLOSSARY} (WS2 owns it)"
    with open(GLOSSARY, encoding="utf-8") as fh:
        doc = fh.read()

    b = doc.find(GEN_BEGIN)
    e = doc.find(GEN_END)
    assert b != -1 and e != -1 and e > b, (
        f"{GLOSSARY}: could not find the WS2 markers "
        f"{GEN_BEGIN!r} .. {GEN_END!r} bounding the generated region")
    region = doc[b:e + len(GEN_END)]

    if region != generated:
        # Characterise the drift without dumping ~500 identical lines.
        import difflib
        diff = list(difflib.unified_diff(
            region.splitlines(), generated.splitlines(),
            fromfile="Docs/CapabilityGlossary.md (region)",
            tofile="glossary --stdout", lineterm="", n=1))
        snippet = "\n".join(diff[:40])
        pytest.fail(
            "Docs/CapabilityGlossary.md's generated region is STALE relative "
            "to the ledger. Regenerate with `python3 scripts/"
            "complandscape_docdefects.py glossary --write`.\n" + snippet)
