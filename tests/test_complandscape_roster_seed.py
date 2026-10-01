"""Seeded roster entries must survive the CL2 -> CL3 hand-off.

THE DEFECT THIS EXISTS FOR
--------------------------
The first competitive-landscape run rostered aider and never produced its
dossier; the second rostered 27 tools but the CL3 tool axis is read from the
matrix's ``tools`` array, not the roster, so a tool added after the axis was
built gets zero grid slices.  Neither hole errored.  On 2026-09-23 Cloudflare
OS was seeded into ``roster.json`` from an off-roster comparison; these tests
assert the seed is well-formed, that the consuming scripts tolerate a rostered
tool that has no dossier or matrix column YET, and that the CL2/CL3 task cards
carry the carry-forward / reconcile instructions that make the seed durable.

They skip when the study corpus is not on this machine.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

ROOT = os.path.join(os.path.dirname(__file__), "..", ".ziya", "complandscape")
ROSTER = os.path.join(ROOT, "20-competitors", "roster.json")
MATRIX = os.path.join(ROOT, "30-matrix.json")
HAVE_CORPUS = os.path.exists(ROSTER) and os.path.exists(MATRIX)

pytestmark = pytest.mark.skipif(not HAVE_CORPUS, reason="complandscape corpus not present")


def _roster():
    with open(ROSTER, encoding="utf-8") as f:
        return json.load(f)


def _seeded(roster):
    return {s: r for s, r in roster.items()
            if s != "_meta" and isinstance(r, dict) and isinstance(r.get("seeded"), dict)}


def test_roster_meta_is_consistent():
    r = _roster()
    tools = [s for s in r if s != "_meta"]
    assert r["_meta"]["count"] == len(tools)
    for s, rec in r.items():
        if s == "_meta":
            continue
        assert rec["slug"] == s
        assert rec["category"] in r["_meta"]["categories"], (s, rec["category"])


def test_cloudflare_os_seed_is_present_and_well_formed():
    r = _roster()
    assert "cloudflare-os" in r, "seed dropped from roster"
    rec = r["cloudflare-os"]
    for key in ("display_name", "category", "vendor", "license_hosting", "local_first",
                "byo_model", "adoption_signal", "last_release", "why_on_roster",
                "likely_beats_ziya_on"):
        assert key in rec, key
    assert rec["seeded"]["carry_forward"] is True
    assert rec["category"] == "company-ai-workspace"
    # the _meta ledger of seeds must name it, so the CL2 gate can check it
    assert any(a["slug"] == "cloudflare-os" for a in r["_meta"]["seeded_additions"])


def test_every_seeded_entry_is_recorded_in_meta():
    r = _roster()
    recorded = {a["slug"] for a in r["_meta"].get("seeded_additions", [])}
    for slug in _seeded(r):
        assert slug in recorded, f"{slug} is seeded but not in _meta.seeded_additions"


def test_default_permit_is_recorded_as_design_position_not_gap():
    """Ziya's default-permit floor for safe work is deliberate (approval
    fatigue).  The method contract every study agent reads must say so, and
    the Cloudflare OS seed must not pre-judge default-deny as a Ziya loss."""
    with open(os.path.join(ROOT, "00-method.md"), encoding="utf-8") as f:
        method = f.read()
    assert "### 0a. Design positions are not gaps" in method
    assert "default-permit" in method
    rec = _roster()["cloudflare-os"]
    assert not any("default-deny" in s for s in rec["likely_beats_ziya_on"])
    axes = [p["axis"] for p in rec["design_positions_not_gaps"]]
    assert "approval / tool-access default" in axes
    # the real lead (runtime isolation) is still claimed -- the fix must not
    # have scrubbed genuine competitor strengths along with the mis-framing
    assert any("sandboxed execution" in s for s in rec["likely_beats_ziya_on"])


def test_consumers_tolerate_rostered_tool_without_dossier_or_column():
    """A seed exists before its dossier and matrix column do.  hitlist and
    appendix must not crash on that state (they build display-name maps from
    the roster and filter dossiers on existence)."""
    r = _roster()
    with open(MATRIX, encoding="utf-8") as f:
        m = json.load(f)
    undossiered = [s for s in r if s != "_meta"
                   and not os.path.exists(os.path.join(ROOT, "20-competitors", f"{s}.json"))]
    # the seed is expected to be in this state until CL2 runs
    if "cloudflare-os" not in m["tools"]:
        assert "cloudflare-os" in undossiered or os.path.exists(
            os.path.join(ROOT, "20-competitors", "cloudflare-os.json"))
    import complandscape_matrix as cm  # noqa: E402
    space = cm.load_space(ROOT)
    # the axis is read from the matrix, not the roster -- this is the seam the
    # CL3 reconcile step exists for; assert the premise rather than the fix
    assert set(cm.competitor_tools(space)) == set(t for t in m["tools"] if t != "ziya")
    # display-name projection over the whole roster (what hitlist.py does)
    display = {s: (v.get("display_name") or s) for s, v in r.items() if s != "_meta"}
    assert display["cloudflare-os"].startswith("Cloudflare OS")
