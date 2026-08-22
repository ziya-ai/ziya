"""
NEW-1 (flow-aware figure shrinking) guard.

An embedded figure that FITS a page on its own but is too tall to also fit
alongside the prose that introduces it was bumped WHOLE onto its own page,
stranding it from its context and leaving a large empty band behind (user
defect NEW-1).  The pre-existing ``fitOversizedFigures`` in
``frontend/src/components/PrintRenderPage.tsx`` early-returned on ``scale >= 1``
— it only ever shrank figures that could not fit AT ALL, so it never touched a
figure that fits but wrecks flow.

The fix extends ``fitOversizedFigures`` with a FLOW-AWARE branch: a figure whose
box is nearly as tall as the whole printable page is shrunk just enough to leave
a companion band of prose on its page — but NEVER below the user's 0.75 floor
(``FLOW_SHRINK_FLOOR``).  Flow-driven and oversize shrinks are distinguished by
``data-print-fit-reason`` ('flow' | 'oversize') so a check can assert the 0.75
floor for the flow case only.  End-to-end proof lives in the audit
(``check_figure_flow_quality`` fail->pass on ``make_flow_figure_conversation``);
this is the browser-free STRUCTURAL guard that the shipped source carries the
mechanism (mutation-proven: removing the branch flips the assertions).

The change is in a git-tracked ``.tsx``; the /print route serves from the build,
so end-to-end verification additionally requires a frontend rebuild.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRINT_PAGE = REPO / "frontend" / "src" / "components" / "PrintRenderPage.tsx"


def _source_without_comments() -> str:
    text = PRINT_PAGE.read_text()
    # strip /* block */ and // line comments so we assert on real code, not prose
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def test_flow_shrink_floor_constant_present():
    """The 0.75 flow floor is codified as a constant, at exactly 0.75."""
    src = _source_without_comments()
    m = re.search(r"FLOW_SHRINK_FLOOR\s*=\s*([0-9.]+)", src)
    assert m, "FLOW_SHRINK_FLOOR constant missing from PrintRenderPage.tsx"
    assert float(m.group(1)) == 0.75, (
        f"flow shrink floor is {m.group(1)}, must be 0.75 per the user ruling"
    )


def test_flow_aware_branch_present():
    """fitOversizedFigures records a distinguishable 'flow' fit reason and does
    NOT unconditionally bail on scale>=1 (which is what blinded it to flow)."""
    src = _source_without_comments()
    assert "data-print-fit-reason" in src, (
        "the fit-reason attribute (flow vs oversize discriminator) is missing"
    )
    assert "'flow'" in src or '"flow"' in src, "no flow-reason shrink path present"
    assert "FLOW_COMPANION_BAND_FRACTION" in src, (
        "the companion-band reservation that drives flow shrinking is missing"
    )


def test_min_height_release_reused():
    """The flow shrink must reuse the mermaid wrapper min-height release — a
    shrunk <svg> inside a page-tall reserved box still forces the break, so the
    box collapse is essential to the fix landing visibly."""
    src = _source_without_comments()
    assert "min-height" in src, "wrapper min-height release absent"
    assert "applyFigureShrink" in src, (
        "shared shrink helper (applies size + releases wrapper height) missing"
    )
