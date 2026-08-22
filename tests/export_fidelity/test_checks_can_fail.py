"""
Prove every fidelity check CAN fail.

A harness that reports everything clean is almost certainly broken.  For each
analyzer we construct BOTH a passing input and a deliberately-broken input and
assert the check flips pass -> fail.  These are pure (synthetic numpy pages and
synthetic text) so they need no browser and run in milliseconds — the guard
that keeps the checks honest as they evolve.

Each raster check gets a synthetic :class:`RenderedDocument` built from numpy
arrays; each format-neutral check gets synthetic text.
"""
from __future__ import annotations

import numpy as np
import pytest

from tests.export_fidelity import fixture
from tests.export_fidelity import checks as C
from tests.export_fidelity.render_harness import RenderedDocument, RenderedPage


# ---------------------------------------------------------------------------
# Synthetic page builders
# ---------------------------------------------------------------------------

H, W = 300, 220  # small pages keep the flood-fill fast


def _white_page(idx=0, text="", words=None):
    rgb = np.full((H, W, 3), 255, dtype=np.uint8)
    return RenderedPage(idx, rgb, text, W, H, words or [])


def _doc(pages, text=None):
    full = text if text is not None else "\n".join(p.text for p in pages)
    return RenderedDocument(pages=pages, full_text=full, source_format="pdf", dpi=150.0)


def _paint(rgb, y0, y1, x0, x1, color):
    rgb[y0:y1, x0:x1] = color


# ---------------------------------------------------------------------------
# colorfulness
# ---------------------------------------------------------------------------

def test_colorfulness_can_pass_and_fail():
    # PASS: a page whose ink is substantially colored.
    ok = _white_page()
    _paint(ok.rgb, 10, 120, 10, 200, (10, 120, 230))   # big blue block = colored ink
    assert C.check_colorfulness(_doc([ok])).passed

    # FAIL: a page with plenty of ink but it's all pure grey (defect 1).
    bad = _white_page()
    _paint(bad.rgb, 10, 120, 10, 200, (80, 80, 80))     # grey ink only
    res = C.check_colorfulness(_doc([bad]))
    assert not res.passed
    assert res.measurements["colored_ink_fraction_per_page"][0] < 0.002


# ---------------------------------------------------------------------------
# background_whiteness
# ---------------------------------------------------------------------------

def test_background_whiteness_can_pass_and_fail():
    ok = _white_page()
    _paint(ok.rgb, 10, 40, 10, 200, (20, 120, 220))     # small colored mark on white
    assert C.check_background_whiteness(_doc([ok])).passed

    bad = _white_page()
    _paint(bad.rgb, 0, H, 0, W, (18, 18, 18))           # whole page dark (defect 6)
    res = C.check_background_whiteness(_doc([bad]))
    assert not res.passed
    assert res.measurements["dark_fraction_per_page"][0] > 0.15


# ---------------------------------------------------------------------------
# dark_theme_leak (PDF-02) — a SMALL but SOLID dark diagram block that a
# whole-page dark fraction would miss, but a contiguous dark-row run catches.
# ---------------------------------------------------------------------------

def test_dark_theme_leak_can_pass_and_fail():
    # PASS: a light diagram — thin dark strokes on white (few dark px per row,
    # no tall contiguous dark run) plus a mostly-white page.
    ok = _white_page()
    for y in range(20, 120, 12):                        # horizontal stroke lines
        _paint(ok.rgb, y, y + 1, 20, 200, (30, 30, 30))
    res_ok = C.check_dark_theme_leak(_doc([ok]))
    assert res_ok.passed, res_ok.failures

    # FAIL: a small SOLID dark rectangle (a dark-themed mermaid SVG leaking).
    # It is only ~20% of page height and ~50% width -> whole-page dark fraction
    # ~0.10 (would PASS background_whiteness' 0.15 cap), but it is a tall
    # contiguous dark-row run so dark_theme_leak flags it.
    bad = _white_page()
    _paint(bad.rgb, 40, 100, 30, 130, (35, 38, 40))     # 60 rows / 300 = 0.20
    res_bad = C.check_dark_theme_leak(_doc([bad]))
    assert not res_bad.passed
    assert res_bad.measurements["max_dark_row_run_fraction_per_page"][0] > 0.015
    # And confirm this same block does NOT trip the coarser whiteness cap,
    # proving dark_theme_leak is the sharper gate for a scaled-small dark figure.
    assert C.check_background_whiteness(_doc([bad])).passed


# ---------------------------------------------------------------------------
# image_presence
# ---------------------------------------------------------------------------

def test_image_presence_can_pass_and_fail():
    # PASS: three large solid blocks == three figures.
    ok = _white_page()
    _paint(ok.rgb, 10, 80, 10, 120, (0, 0, 0))
    _paint(ok.rgb, 100, 170, 10, 120, (0, 0, 0))
    _paint(ok.rgb, 190, 260, 10, 120, (0, 0, 0))
    assert C.check_image_presence(_doc([ok]), expected_figures=3).passed

    # FAIL: only text-thin marks, no figure-scale blob.
    bad = _white_page()
    _paint(bad.rgb, 10, 12, 10, 60, (0, 0, 0))          # a thin line, tiny area
    res = C.check_image_presence(_doc([bad]), expected_figures=3)
    assert not res.passed
    assert res.measurements["figure_scale_regions"] < 3


# ---------------------------------------------------------------------------
# expected_color_presence
# ---------------------------------------------------------------------------

def test_expected_color_presence_can_pass_and_fail():
    ok = _white_page()
    _paint(ok.rgb, 10, 60, 10, 200, (230, 255, 236))    # diff-add green, lots
    _paint(ok.rgb, 70, 120, 10, 200, (255, 235, 233))   # diff-del red, lots
    assert C.check_expected_color_presence(_doc([ok])).passed

    bad = _white_page()                                  # no diff colors at all
    res = C.check_expected_color_presence(_doc([bad]))
    assert not res.passed
    assert res.measurements["color_pixel_counts"]["diff_insert_green"] == 0


# ---------------------------------------------------------------------------
# whitespace_waste
# ---------------------------------------------------------------------------

def test_whitespace_waste_can_pass_and_fail():
    # PASS: page evenly inked, no huge white band; make it NOT the last page.
    ok = _white_page(idx=0)
    for y in range(0, H, 20):
        _paint(ok.rgb, y, y + 8, 10, 200, (0, 0, 0))
    tail = _white_page(idx=1)  # last page exempt
    assert C.check_whitespace_waste(_doc([ok, tail])).passed

    # FAIL: a non-final page with a giant white band in the middle.
    bad = _white_page(idx=0)
    _paint(bad.rgb, 0, 10, 10, 200, (0, 0, 0))           # a little ink at top
    # rows 10..H remain white -> band fraction ~0.96 > 0.45
    tail2 = _white_page(idx=1)
    res = C.check_whitespace_waste(_doc([bad, tail2]))
    assert not res.passed
    assert res.measurements["largest_white_band_fraction_per_page"][0] > 0.45


# ---------------------------------------------------------------------------
# page_break_sanity
# ---------------------------------------------------------------------------

def _mostly_inked_page(idx, text="", words=None):
    p = _white_page(idx, text, words)
    for y in range(0, H, 6):
        _paint(p.rgb, y, y + 3, 5, W - 5, (0, 0, 0))     # dense ink -> not "empty"
    return p


def test_page_break_sanity_near_empty_can_fail():
    # FAIL: a non-final page that is almost entirely empty (absurd early break).
    empty = _white_page(idx=0, text="x")
    tail = _mostly_inked_page(1, text="tail")
    res = C.check_page_break_sanity(_doc([empty, tail]))
    assert not res.passed
    assert any("empty_fraction" in f for f in res.failures)


def test_page_break_sanity_early_break_top_band_can_fail():
    # FAIL: a non-final page whose little content sits only in a TOP band (the
    # classic nonsensical early break — content bumped, page left mostly empty).
    early = _white_page(idx=0, text="x")
    _paint(early.rgb, 5, 25, 5, W - 5, (0, 0, 0))  # thin ink band at the very top
    tail = _mostly_inked_page(1, text="tail")
    res = C.check_page_break_sanity(_doc([early, tail]))
    assert not res.passed
    # The failure names the low vertical span, not just empty_fraction.
    assert any("vertical_ink_span" in f for f in res.failures)
    assert res.measurements["vertical_ink_span_per_page"][0] < 0.60


def test_page_break_sanity_tall_figure_page_can_pass():
    # PASS: a non-final page with LOW pixel coverage but whose (sparse) ink
    # spans nearly the full page height — a tall figure scaled to fit one page.
    # This must NOT be flagged as a nonsensical early break (PDF-03 fix: an
    # oversized figure is scaled to fill its own page rather than clipped).
    figure = _white_page(idx=0, text="figure")
    # A thin vertical spine + a few nodes: covers top-to-bottom, few pixels.
    _paint(figure.rgb, 10, H - 10, W // 2 - 2, W // 2 + 2, (0, 0, 0))  # spine
    for y in range(15, H - 15, 40):
        _paint(figure.rgb, y, y + 8, W // 2 - 20, W // 2 + 20, (0, 0, 0))  # nodes
    tail = _mostly_inked_page(1, text="tail")
    res = C.check_page_break_sanity(_doc([figure, tail]))
    # Coverage is low (mostly white) but vertical span is high -> exempt.
    assert res.measurements["ink_coverage_per_page"][0] < 0.10
    assert res.measurements["vertical_ink_span_per_page"][0] >= 0.60
    assert not any("empty_fraction" in f for f in res.failures)


def test_page_break_sanity_orphan_heading_can_pass_and_fail():
    marker = fixture.UNIQUE_TEXT_MARKERS["orphan_heading"]
    # FAIL: heading near page bottom (bottom=780pt on an ~800pt page) with
    # nothing below it.
    orphan_words = [
        {"text": "body", "x0": 10, "x1": 40, "top": 10, "bottom": 20},
        {"text": marker, "x0": 10, "x1": 90, "top": 770, "bottom": 785},
    ]
    p0 = _mostly_inked_page(0, text=marker, words=orphan_words)
    p1 = _mostly_inked_page(1, text="next")
    res_fail = C.check_page_break_sanity(_doc([p0, p1]))
    assert not res_fail.passed
    assert res_fail.measurements["orphan_heading"]["found"] is True

    # PASS: same heading but with body text below it on the page.
    ok_words = [
        {"text": marker, "x0": 10, "x1": 90, "top": 100, "bottom": 115},
        {"text": "figure", "x0": 10, "x1": 90, "top": 300, "bottom": 500},
    ]
    q0 = _mostly_inked_page(0, text=marker, words=ok_words)
    q1 = _mostly_inked_page(1, text="next")
    res_ok = C.check_page_break_sanity(_doc([q0, q1]))
    # orphan sub-signal should not contribute a failure here
    assert not any("orphaned" in f for f in res_ok.failures)


# ---------------------------------------------------------------------------
# text_extractability (format-neutral)
# ---------------------------------------------------------------------------

def test_text_extractability_can_pass_and_fail():
    good = " ".join(fixture.UNIQUE_TEXT_MARKERS.values())
    assert C.check_text_extractability(good).passed

    res = C.check_text_extractability("no markers here at all")
    assert not res.passed
    assert res.measurements["recovered_count"] == 0


# ---------------------------------------------------------------------------
# content_completeness (format-neutral)
# ---------------------------------------------------------------------------

def test_content_completeness_can_pass_and_fail():
    good = " ".join(fixture.UNIQUE_TEXT_MARKERS.values())
    assert C.check_content_completeness(good).passed

    # drop one marker (simulate a dropped message element) -> count 0
    markers = list(fixture.UNIQUE_TEXT_MARKERS.values())
    dropped = " ".join(markers[1:])
    res = C.check_content_completeness(dropped)
    assert not res.passed

    # duplicate one marker (simulate a message rendered twice) -> count 2
    dup = good + " " + markers[0]
    res2 = C.check_content_completeness(dup)
    assert not res2.passed
    assert 2 in res2.measurements["marker_counts"].values()


# ---------------------------------------------------------------------------
# MARKDOWN checks (Card III) — text-level, pass<->fail on synthetic strings.
# The passing input is the REAL exported markdown of the canonical fixture, so
# these also guard the live exporter, not just a hand-built happy string.
# ---------------------------------------------------------------------------

def _export_md(msgs):
    from app.utils.conversation_exporter import export_conversation_for_paste
    return export_conversation_for_paste(msgs, format_type="markdown", target="public")["content"]


def _canonical_md():
    return _export_md(fixture.make_fidelity_conversation())


def test_md_fence_integrity_can_pass_and_fail():
    assert C.check_md_fence_integrity(_canonical_md()).passed

    # FAIL: an unterminated fence (odd toggle) swallows the tail.
    broken = "intro\n\n```python\ncode goes here\nno closing fence and then EOF"
    res = C.check_md_fence_integrity(broken)
    assert not res.passed
    assert res.measurements["unterminated_fence"] == 1

    # FAIL: a diff not inside a diff fence (dropped diff fence) -> diff_fence_count 0.
    no_diff = "```python\nx=1\n```\n\n```mermaid\ngraph LR\nA-->B\n```\n"
    res2 = C.check_md_fence_integrity(no_diff)
    assert not res2.passed
    assert res2.measurements["diff_fence_count"] == 0


def test_md_tool_block_fence_integrity_can_pass_and_fail():
    B = "`"
    F3 = B * 3
    F4 = B * 4
    # PASS: a well-formed tool block whose wrapper fence (4 ticks) is longer than
    # the 3-tick run inside — the inner run is content, nothing leaks.
    good = (
        "<details>\n<summary>Tool Output</summary>\n\n"
        + F4 + "sh\nout before\n" + F3 + "\nnested\n" + F3 + "\nout after\n" + F4 + "\n\n"
        + "</details>\n"
    )
    assert C.check_md_tool_block_fence_integrity(good).passed

    # FAIL: the wrapper fence is only 3 ticks, so the inner 3-tick run closes it
    # early and 'nested' + 'out after' leak out of the block as prose.
    bad = (
        "<details>\n<summary>Tool Output</summary>\n\n"
        + F3 + "sh\nout before\n" + F3 + "\nnested\n" + F3 + "\nout after\n" + F3 + "\n\n"
        + "</details>\n"
    )
    res = C.check_md_tool_block_fence_integrity(bad)
    assert not res.passed
    assert res.measurements["leaking_tool_blocks"] == 1

    # PASS: no tool blocks at all -> vacuously clean.
    assert C.check_md_tool_block_fence_integrity("just prose\n\nmore prose").passed

    # PASS: the canonical export (its tool blocks, if any, are well-formed).
    assert C.check_md_tool_block_fence_integrity(_canonical_md()).passed


def test_md_diagram_embedding_can_pass_and_fail():
    assert C.check_md_diagram_embedding(_canonical_md()).passed

    # FAIL: a diagram label present as prose but NOT inside a fence and NO
    # embedded image -> silently vanished (source stripped, no picture).
    broken = "The diagram NodeAlphaMRK was here but the fence and image are gone."
    res = C.check_md_diagram_embedding(broken, presence_markers={"n": "NodeAlphaMRK"})
    assert not res.passed
    assert res.measurements["embedded_data_uri_count"] == 0

    # PASS via embedded image substitute (data URI present).
    embedded = "![d](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=) no source fence"
    assert C.check_md_diagram_embedding(embedded, presence_markers={"n": "NodeAlphaMRK"}).passed


def test_md_math_preservation_can_pass_and_fail():
    assert C.check_md_math_preservation(_canonical_md()).passed

    # FAIL: math delimiters stripped / rendered away.
    res = C.check_md_math_preservation("The integral equals one third, no delimiters.")
    assert not res.passed
    assert res.measurements["display_delim_count"] == 0

    # FAIL: odd number of $$ (unbalanced display block).
    res2 = C.check_md_math_preservation("start $$ \\int_0^1 x^2 and \\frac{1}{3} but never closed")
    assert not res2.passed


def test_md_table_integrity_can_pass_and_fail():
    assert C.check_md_table_integrity(_canonical_md()).passed

    # FAIL: header row but NO delimiter row (renderers won't treat it as a table).
    broken = "| Col1 | Col2 |\n| a | b |\n| c | d |\n"
    res = C.check_md_table_integrity(broken)
    assert not res.passed
    assert res.measurements["delimiter_rows"] == 0

    # FAIL: delimiter present but a body row has the wrong column count.
    malformed = "| A | B | C |\n| --- | --- | --- |\n| only | two |\n"
    res2 = C.check_md_table_integrity(malformed)
    assert not res2.passed


def test_md_structural_sanity_can_pass_and_fail():
    assert C.check_md_structural_sanity(_canonical_md()).passed

    # FAIL: no message headings and no rule separators.
    res = C.check_md_structural_sanity("just some prose with no headings or rules")
    assert not res.passed
    assert res.measurements["message_headings"] == 0


def test_md_roundtrip_legible_can_pass_and_fail():
    assert C.check_md_roundtrip_legible(_canonical_md()).passed

    # FAIL: a big document whose tail is swallowed by one unterminated fence.
    padding = "\n\n".join(f"paragraph number {i} with some words" for i in range(30))
    runaway = padding + "\n\n```python\nopened but never closed\n" + "\n".join(
        f"swallowed line {i}" for i in range(60))
    res = C.check_md_roundtrip_legible(runaway)
    assert not res.passed
    assert res.measurements.get("max_single_fence_lines", 0) > 0


# ---------------------------------------------------------------------------
# EXPORT-HYGIENE checks (Card III markdown + Card IV PDF).
# ---------------------------------------------------------------------------

def test_no_superseded_diffs_can_pass_and_fail():
    m = fixture.SUPERSESSION_MARKERS
    # PASS: a hygienic export keeps only the final diff's added line.
    good = f"{m['intro']}\n\n```diff\n+{m['final_add']} = 'final'\n```\n\n{m['closing']}"
    assert C.check_no_superseded_diffs(good).passed

    # FAIL: the stale (superseded) diff's added line is still present.
    bad = f"+{m['superseded_add']} = 'first'\n+{m['final_add']} = 'final'"
    res = C.check_no_superseded_diffs(bad)
    assert not res.passed
    assert res.measurements["superseded_add_count"] == 1

    # FAIL: the LIVE diff was dropped entirely (marker genuinely absent).
    dropped_live = "nothing but prose, the live diff's added line is gone"
    res2 = C.check_no_superseded_diffs(dropped_live)
    assert not res2.passed
    assert res2.measurements["final_add_count"] == 0


def test_no_ui_chrome_can_pass_and_fail():
    keep = fixture.UI_CHROME_MARKERS["answer"]
    # PASS: the real answer, no chrome.
    assert C.check_no_ui_chrome(f"{keep}: the function works now.").passed

    # FAIL: the auto-added-context banner leaked in.
    bad = ("Auto-added 3 file(s) to context (a.py) — available for subsequent "
           f"queries. Remove via the A button in the Files panel.\n\n{keep}: ok.")
    res = C.check_no_ui_chrome(bad)
    assert not res.passed
    assert len(res.measurements["leaked_substrings"]) >= 1

    # FAIL: the real answer was dropped.
    res2 = C.check_no_ui_chrome("clean but the answer marker is gone")
    assert not res2.passed
    assert res2.measurements["answer_kept"] is False


# ---------------------------------------------------------------------------
# FIGURE-FLOW QUALITY (NEW-1) — a figure placed WELL, not stranded on its own
# near-empty page; and flow-driven shrink never below the 0.75 floor.
# ---------------------------------------------------------------------------

def _figure_blob_page(idx, *, height_frac=0.25, ink_extra=False, text=""):
    """A page whose ink is dominated by ONE figure-scale solid block.

    ``height_frac`` controls how tall the block is (a small block on an
    otherwise-empty page == the lonely-figure symptom).  ``ink_extra`` adds
    prose lines around it so the page is NOT figure-only (the healthy case).
    """
    p = _white_page(idx, text)
    top = 10
    bot = int(H * height_frac) + top
    _paint(p.rgb, top, bot, 20, W - 20, (0, 0, 0))   # one big solid figure blob
    if ink_extra:
        # dense prose lines filling the rest of the page -> not figure-only
        for y in range(bot + 6, H - 6, 6):
            _paint(p.rgb, y, y + 3, 15, W - 15, (0, 0, 0))
    return p


def test_figure_flow_quality_can_pass_and_fail():
    # FAIL: a non-final page dominated by ONE small figure blob with almost no
    # other ink -> lonely-figure page (NEW-1 symptom: figure bumped whole to its
    # own near-empty page).  height_frac 0.10 keeps the blob figure-scale
    # (>5000px) while ink coverage (~0.08) stays below the lonely threshold —
    # a real diagram is sparse strokes, not a dense filled block.
    lonely = _figure_blob_page(0, height_frac=0.10, ink_extra=False, text="fig")
    tail = _mostly_inked_page(1, text="tail")
    res_bad = C.check_figure_flow_quality(_doc([lonely, tail]))
    assert not res_bad.passed, res_bad.measurements
    assert 0 in res_bad.measurements["lonely_figure_pages"]

    # PASS: the same figure sharing its page with surrounding prose (well placed).
    ok = _figure_blob_page(0, height_frac=0.10, ink_extra=True, text="fig+prose")
    tail2 = _mostly_inked_page(1, text="tail")
    res_ok = C.check_figure_flow_quality(_doc([ok, tail2]))
    assert res_ok.passed, res_ok.failures
    assert res_ok.measurements["lonely_figure_pages"] == []


def test_figure_flow_quality_shrink_floor_can_fail():
    # The 0.75 floor on flow-driven shrinking. A well-placed page (no lonely
    # figure) still FAILS if a flow shrink factor dips below 0.75.
    ok = _figure_blob_page(0, height_frac=0.10, ink_extra=True)
    tail = _mostly_inked_page(1)

    # PASS: shrink applied but never below the floor.
    res_ok = C.check_figure_flow_quality(
        _doc([ok, tail]), applied_shrink_factors=[1.0, 0.82, 0.75])
    assert res_ok.passed, res_ok.failures

    # FAIL: a flow shrink went below 0.75 (more aggressive than the user ruling).
    res_bad = C.check_figure_flow_quality(
        _doc([ok, tail]), applied_shrink_factors=[0.90, 0.60])
    assert not res_bad.passed
    assert res_bad.measurements["shrink"]["below_floor"] == [0.60]


def test_figure_flow_quality_companion_words_veto():
    """When word positions exist, companion PROSE beside a figure clears the
    lonely flag — the exact NEW-1 improvement (a figure that co-resides with
    its introducing/following text is well placed, even if it is the ink-
    dominant blob on the page).  This exercises the word-based path, not the
    raster proxy the other flow tests use.
    """
    # A single figure-scale blob near the page top (mimics a mermaid diagram).
    def _fig_page(idx, companion):
        p = _white_page(idx, text="fig")
        _paint(p.rgb, 10, int(H * 0.10) + 10, 20, W - 20, (0, 0, 0))
        # figure's OWN node labels sit inside the blob (top band) — not prose.
        inside = [{"text": f"Node{i}", "x0": 30, "x1": 60,
                   "top": 8, "bottom": 20} for i in range(4)]
        # companion prose sits BELOW the blob (outside its bbox), in points.
        below_top = (int(H * 0.10) + 40) / (150.0 / 72.0)
        prose = [{"text": f"prose{i}", "x0": 20, "x1": 90,
                  "top": below_top, "bottom": below_top + 8}
                 for i in range(companion)]
        p.words = inside + prose
        return p

    tail = _mostly_inked_page(1, text="tail")

    # FAIL: figure blob with NO companion prose outside it (only its own node
    # labels) -> lonely, divorced from context.
    lonely = _fig_page(0, companion=0)
    res_bad = C.check_figure_flow_quality(_doc([lonely, tail]))
    assert not res_bad.passed, res_bad.measurements
    assert 0 in res_bad.measurements["lonely_figure_pages"]
    assert res_bad.measurements["per_page"][0]["companion_words"] == 0

    # PASS: same figure now sharing its page with real companion prose.
    ok = _fig_page(0, companion=12)
    res_ok = C.check_figure_flow_quality(_doc([ok, tail]))
    assert res_ok.passed, res_ok.failures
    assert res_ok.measurements["lonely_figure_pages"] == []
    assert res_ok.measurements["per_page"][0]["companion_words"] >= 6


# ---------------------------------------------------------------------------
# DIFF HEADER BINDING (NEW-3) — 'Modify:' header sits with its body, no band.
# ---------------------------------------------------------------------------

def test_diff_header_binding_can_pass_and_fail():
    body_start = fixture.HEADER_BINDING_MARKERS["body_start"]

    # PASS: header and body-start on the SAME page, small gap between them.
    good_words = [
        {"text": "Modify:", "x0": 10, "x1": 60, "top": 100, "bottom": 115},
        {"text": body_start, "x0": 10, "x1": 200, "top": 130, "bottom": 145},
    ]
    p_ok = _mostly_inked_page(0, text=f"Modify: {body_start}", words=good_words)
    tail_ok = _mostly_inked_page(1, text="tail")
    res_ok = C.check_diff_header_binding(_doc([p_ok, tail_ok]))
    assert res_ok.passed, res_ok.failures
    assert res_ok.measurements["header_body_gap_pts"] == 15.0

    # FAIL (band): header high, body far below on the same page (large gap).
    band_words = [
        {"text": "Modify:", "x0": 10, "x1": 60, "top": 60, "bottom": 75},
        {"text": body_start, "x0": 10, "x1": 200, "top": 500, "bottom": 515},
    ]
    p_band = _mostly_inked_page(0, text=f"Modify: {body_start}", words=band_words)
    tail_band = _mostly_inked_page(1, text="tail")
    res_band = C.check_diff_header_binding(_doc([p_band, tail_band]))
    assert not res_band.passed
    assert res_band.measurements["header_body_gap_pts"] > 120.0

    # FAIL (split): header on page 0, body-start on page 1 (stranded header).
    p0 = _mostly_inked_page(0, text="Modify:",
                            words=[{"text": "Modify:", "x0": 10, "x1": 60,
                                    "top": 760, "bottom": 775}])
    p1 = _mostly_inked_page(1, text=body_start,
                            words=[{"text": body_start, "x0": 10, "x1": 200,
                                    "top": 40, "bottom": 55}])
    res_split = C.check_diff_header_binding(_doc([p0, p1]))
    assert not res_split.passed
    assert res_split.measurements["header_body_same_page"] is False


# ---------------------------------------------------------------------------
# WIDE TABLE COMPLETENESS (PDF-09b) — over-wide table keeps ALL its columns.
# ---------------------------------------------------------------------------

def test_wide_table_completeness_can_pass_and_fail():
    # PASS: both the leftmost and the rightmost cell marker survive (the fit-
    # scaled table reflowed every column within the margin).
    good = (
        "ADVWIDE_INTRO wide table:\n"
        "WCOL0 ... WCOL19  WIDECELL_0_xxxxxxxx ... WIDECELL_19_xxxxxxxx\n"
        "ADVWIDE_CLOSING."
    )
    res_ok = C.check_wide_table_completeness(_doc([], text=good))
    assert res_ok.passed, res_ok.failures
    assert res_ok.measurements["right_present"] is True

    # FAIL: the rightmost column was clipped off the content margin — its marker
    # never reaches the extracted PDF text (the exact PDF-09b symptom).
    clipped = (
        "ADVWIDE_INTRO wide table:\n"
        "WCOL0 ...  WIDECELL_0_xxxxxxxx WIDECELL_9_xxxxxxxx\n"   # right cols dropped
        "ADVWIDE_CLOSING."
    )
    res_bad = C.check_wide_table_completeness(_doc([], text=clipped))
    assert not res_bad.passed
    assert res_bad.measurements["right_present"] is False
    assert res_bad.measurements["left_present"] is True


# ---------------------------------------------------------------------------
# meta: run_all_checks wires everything and returns per-check results
# ---------------------------------------------------------------------------
# DOCUMENT-QUALITY checks (Card IV — QUAL family).
#
# These read PDF STRUCTURE via pypdf on ``doc.raw_bytes``, so the synthetic
# inputs are minimal hand-built PDFs (browser-free, milliseconds) with
# controllable fonts / outline / links / metadata / image-xobjects.  Each test
# builds a GOOD pdf that PASSES and a deliberately-broken one that FAILS.
# ---------------------------------------------------------------------------

def _build_pdf(*, embed_all_fonts=True, add_nonembedded_font=False,
               with_outline=True, with_link=True,
               title="Ziya Session Transcript — Fixture Chat",
               author="Ziya", subject="Conversation export", creator="Ziya PDF Exporter",
               set_creation_date=True,
               text="The office workflow is efficient and fluent.",
               n_pages=3, with_image_xobject=False, vector_ops=True) -> bytes:
    """Hand-build a minimal PDF with pypdf, controlling every QUAL dimension.

    Single-writer pass so metadata/outline/links all survive to the bytes.
    """
    import io
    from pypdf import PdfWriter
    from pypdf.generic import (DictionaryObject, NameObject, NumberObject,
                               DecodedStreamObject)

    w = PdfWriter()
    for _ in range(n_pages):
        w.add_blank_page(width=595, height=842)

    # --- font resource on page 0 (embedded subset; optional non-embedded) ---
    page = w.pages[0]
    res = page.get("/Resources")
    if res is None:
        res = DictionaryObject()
        page[NameObject("/Resources")] = res
    res = res.get_object()
    fonts = DictionaryObject()

    fd = DictionaryObject()
    fd[NameObject("/Type")] = NameObject("/FontDescriptor")
    fd[NameObject("/FontName")] = NameObject("/ABCDEF+DejaVuSans")
    if embed_all_fonts:
        ff = DecodedStreamObject()
        ff.set_data(b"\x00\x01\x02fake-embedded-font-program")
        ff[NameObject("/Subtype")] = NameObject("/Type1C")
        fd[NameObject("/FontFile3")] = w._add_object(ff)
    f1 = DictionaryObject()
    f1[NameObject("/Type")] = NameObject("/Font")
    f1[NameObject("/Subtype")] = NameObject("/Type1")
    f1[NameObject("/BaseFont")] = NameObject("/ABCDEF+DejaVuSans")
    f1[NameObject("/FontDescriptor")] = w._add_object(fd)
    fonts[NameObject("/F1")] = w._add_object(f1)

    if add_nonembedded_font:
        f2 = DictionaryObject()
        f2[NameObject("/Type")] = NameObject("/Font")
        f2[NameObject("/Subtype")] = NameObject("/Type1")
        f2[NameObject("/BaseFont")] = NameObject("/Helvetica")  # no descriptor/file
        fonts[NameObject("/F2")] = w._add_object(f2)
    res[NameObject("/Font")] = fonts

    # --- optional image xobject (raster diagram regression) ---
    if with_image_xobject:
        img = DecodedStreamObject()
        img.set_data(b"\xff" * 300)
        img[NameObject("/Type")] = NameObject("/XObject")
        img[NameObject("/Subtype")] = NameObject("/Image")
        img[NameObject("/Width")] = NumberObject(40)     # tiny -> low dpi at full width
        img[NameObject("/Height")] = NumberObject(40)
        img[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
        img[NameObject("/BitsPerComponent")] = NumberObject(8)
        xobj = DictionaryObject()
        xobj[NameObject("/Im1")] = w._add_object(img)
        res[NameObject("/XObject")] = xobj

    # --- content stream: text + optional vector ops + optional image Do ---
    esc = text.replace("(", r"\(").replace(")", r"\)")
    ops = [b"BT /F1 24 Tf 72 700 Td (%s) Tj ET" % esc.encode("latin-1", "replace")]
    if vector_ops:
        ops.append(b"100 100 200 50 re S 120 120 m 200 200 l S 150 150 30 30 re f")
    if with_image_xobject:
        ops.append(b"q 100 0 0 100 200 400 cm /Im1 Do Q")
    stream = DecodedStreamObject()
    stream.set_data(b"\n".join(ops))
    page[NameObject("/Contents")] = w._add_object(stream)

    # --- outline ---
    if with_outline:
        p0 = w.add_outline_item("Human: opening prompt", 0)
        w.add_outline_item("Assistant: reply", 1, parent=p0)
        w.add_outline_item("Closing", n_pages - 1)

    # --- link annotation ---
    if with_link:
        try:
            from pypdf.annotations import Link
            ln = Link(rect=(50, 50, 260, 70), url="https://github.com/ziya-ai/ziya")
            w.add_annotation(page_number=min(2, n_pages - 1), annotation=ln)
        except Exception:
            w.add_uri(page_number=min(2, n_pages - 1),
                      uri="https://github.com/ziya-ai/ziya", rect=(50, 50, 260, 70))

    # --- metadata ---
    md = {}
    if title is not None: md["/Title"] = title
    if author is not None: md["/Author"] = author
    if subject is not None: md["/Subject"] = subject
    if creator is not None: md["/Creator"] = creator
    if set_creation_date: md["/CreationDate"] = "D:20260812125550+00'00'"
    if md:
        w.add_metadata(md)

    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _pdf_doc(raw: bytes):
    """Wrap raw PDF bytes in a RenderedDocument WITHOUT rasterising (structure
    checks only need raw_bytes + full_text)."""
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    return RenderedDocument(pages=[], full_text=text, source_format="pdf",
                            raw_bytes=raw)


def test_font_embedding_can_pass_and_fail():
    ok = C.check_font_embedding(_pdf_doc(_build_pdf()))
    assert ok.passed, ok.failures
    assert ok.measurements["n_non_embedded"] == 0

    bad = C.check_font_embedding(_pdf_doc(_build_pdf(add_nonembedded_font=True)))
    assert not bad.passed
    assert bad.measurements["n_non_embedded"] >= 1
    assert "/Helvetica" in bad.measurements["non_embedded_fonts"]


def test_pdf_outline_can_pass_and_fail():
    ok = C.check_pdf_outline(_pdf_doc(_build_pdf(with_outline=True)))
    assert ok.passed, ok.failures
    assert ok.measurements["n_items"] >= 2
    assert ok.measurements["n_resolved"] >= 2

    bad = C.check_pdf_outline(_pdf_doc(_build_pdf(with_outline=False)))
    assert not bad.passed
    assert bad.measurements["n_items"] == 0


def test_link_annotations_can_pass_and_fail():
    # PASS: a URL in the text layer backed by a Link annotation.
    ok = C.check_link_annotations(_pdf_doc(_build_pdf(
        with_link=True, text="See https://github.com/ziya-ai/ziya for details.")))
    assert ok.passed, ok.failures
    assert ok.measurements["n_link_annotations"] >= 1

    # FAIL: the same URL text with NO Link annotation (dead blue text).
    bad = C.check_link_annotations(_pdf_doc(_build_pdf(
        with_link=False, text="See https://github.com/ziya-ai/ziya for details.")))
    assert not bad.passed
    assert bad.measurements["n_url_texts"] >= 1
    assert bad.measurements["n_link_annotations"] == 0


def test_document_metadata_can_pass_and_fail():
    ok = C.check_document_metadata(_pdf_doc(_build_pdf()))
    assert ok.passed, ok.failures
    assert ok.measurements["has_title"] and ok.measurements["has_author"]

    # FAIL: Chromium-style defaults — app-shell title, no author/subject,
    # Chromium creator.
    bad = C.check_document_metadata(_pdf_doc(_build_pdf(
        title="Ziya - Code Assistant", author=None, subject=None,
        creator="Chromium")))
    assert not bad.passed
    assert not bad.measurements["has_title"]
    assert not bad.measurements["has_author"]
    assert not bad.measurements["has_creator"]


def test_text_quality_can_pass_and_fail():
    phrases = ["The office workflow is efficient and fluent"]
    # PASS: clean text with the exact phrase and normal spacing.
    ok = C.check_text_quality(
        _pdf_doc(_build_pdf(text="The office workflow is efficient and fluent.")),
        expected_phrases=phrases)
    assert ok.passed, ok.failures
    assert ok.measurements["n_phrases_intact"] == 1

    # text_quality inspects the EXTRACTED TEXT LAYER (``doc.full_text``); these
    # sub-cases model a corrupt layer directly (valid PDF bytes carried for
    # completeness, corrupt text layer as the extractor would surface it).
    good_bytes = _build_pdf()

    def _doc_with_text(t):
        return RenderedDocument(pages=[], full_text=t, source_format="pdf",
                                raw_bytes=good_bytes)

    # FAIL (ligature corruption): the fi/fl clusters leak as ligature codepoints
    # and the phrase no longer matches.
    corrupt = "The o\ufb03ce work\ufb02ow is e\ufb03cient and \ufb02uent."
    bad = C.check_text_quality(_doc_with_text(corrupt), expected_phrases=phrases)
    assert not bad.passed
    assert bad.measurements["leaked_ligature_codepoints"]

    # FAIL (dropped spaces): words fuse -> phrase unrecoverable.
    fused = "Theofficeworkflowisefficientandfluent."
    bad2 = C.check_text_quality(_doc_with_text(fused), expected_phrases=phrases)
    assert not bad2.passed
    assert phrases[0] in bad2.measurements["phrases_missing"]

    # FAIL (soft hyphen): spurious mid-word hyphenation corrupts copy-paste.
    softhyp = "The office work\u00adflow is efficient and fluent."
    bad3 = C.check_text_quality(_doc_with_text(softhyp), expected_phrases=[])
    assert not bad3.passed
    assert bad3.measurements["soft_hyphen_count"] >= 1


def test_vector_preservation_can_pass_and_fail():
    # PASS: vector path ops, no image xobjects (the real pipeline's state).
    ok = C.check_vector_preservation(_pdf_doc(_build_pdf(vector_ops=True,
                                                         with_image_xobject=False)))
    assert ok.passed, ok.failures
    assert ok.measurements["is_vector"]
    assert ok.measurements["n_image_xobjects"] == 0

    # FAIL: diagrams rasterised to an image xobject where vector is expected
    # (also below print dpi at full-width placement).
    bad = C.check_vector_preservation(_pdf_doc(_build_pdf(vector_ops=False,
                                                          with_image_xobject=True)))
    assert not bad.passed
    assert bad.measurements["n_image_xobjects"] >= 1


# ---------------------------------------------------------------------------

def test_run_all_checks_returns_every_check():
    ok = _white_page()
    _paint(ok.rgb, 10, 40, 10, 200, (20, 120, 220))
    results = C.run_all_checks(_doc([ok], text=" ".join(fixture.UNIQUE_TEXT_MARKERS.values())))
    names = {r.name for r in results}
    assert set(C.RASTER_CHECKS) <= names
    assert set(C.FORMAT_NEUTRAL_CHECKS) <= names


def test_run_all_checks_includes_markdown_and_hygiene_for_markdown_doc():
    from tests.export_fidelity.render_harness import RenderedDocument
    from app.utils.conversation_exporter import export_conversation_for_paste
    md = export_conversation_for_paste(
        fixture.make_fidelity_conversation(), format_type="markdown", target="public")["content"]
    doc = RenderedDocument(pages=[], full_text=md, source_format="markdown")
    names = {r.name for r in C.run_all_checks(doc)}
    assert set(C.MARKDOWN_CHECKS) <= names
    assert set(C.HYGIENE_CHECKS) <= names
    assert set(C.FORMAT_NEUTRAL_CHECKS) <= names
