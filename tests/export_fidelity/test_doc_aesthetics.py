"""Fast unit tests for the doc-aesthetics metric functions (no browser)."""
import numpy as np

from tests.export_fidelity import doc_aesthetics as da


def _page(h=1000, w=700):
    return np.full((h, w, 3), 255, dtype=np.uint8)


def test_mostly_white_page_has_low_ink_and_large_band():
    p = _page()
    p[0:100, 50:650] = 0  # top 10% ink block
    assert da.ink_fraction(p) < 0.15
    assert da.largest_white_band_fraction(p) >= 0.85
    assert da.ink_height_fraction(p) <= 0.11
    # the trailing band is not INTERIOR
    assert da.largest_white_band_fraction(p, interior=True) == 0.0


def test_two_blocks_separated_by_30pct_band():
    p = _page()
    p[0:300, 50:650] = 30
    p[600:1000, 50:650] = 30
    assert da.largest_white_band_fraction(p) >= 0.3
    assert da.largest_white_band_fraction(p, interior=True) >= 0.3
    assert da.ink_height_fraction(p) == 1.0


def test_box_crop_excludes_margins():
    p = _page()
    p[980:1000, :] = 0  # footer in the margin
    box = (0, 0, 700, 950)
    assert da.ink_fraction(p, box) == 0.0
    assert da.ink_fraction(p) > 0.0


def test_edge_ink_and_bisection():
    p = _page()
    p[900:1000, 100:600] = 0
    assert da.edge_ink(p, edge="bottom")
    assert not da.edge_ink(p, edge="top")
    box = (40, 45, 555, 790)
    assert da.figure_bisected([60, 600, 500, 785], [60, 50, 500, 300], box)
    assert not da.figure_bisected([60, 400, 500, 600], [60, 50, 500, 300], box)
    assert not da.figure_bisected([60, 600, 500, 785], None, box)


def _w(text, x0, top, size=10.5):
    return {"text": text, "x0": x0, "x1": x0 + 6 * len(text), "top": top,
            "bottom": top + size, "size": size}


def test_heading_orphan_detection():
    words = [_w("Body", 50, 100), _w("text", 90, 100), _w("more", 50, 700),
             _w("Heading", 50, 770, size=13.5)]
    lines = da.group_lines(words)
    assert da.body_font_size(words) == 10.5
    assert da.heading_orphan(lines, 10.5, 45, 800) == "Heading"
    words.append(_w("follow", 50, 788))
    lines = da.group_lines(words)
    assert da.heading_orphan(lines, 10.5, 45, 800) is None


def test_cell_wrapped_lines_and_markup():
    cell = [_w("a", 10, t) for t in (0, 12, 24, 36, 48)]
    assert da.cell_wrapped_lines(cell) == 5
    hits = da.raw_markup_hits("x <!-- y ``` z \ue000 {{a}}")
    assert all(v == 1 for v in hits.values())
    assert sum(da.raw_markup_hits("clean prose").values()) == 0


def test_target_pages_and_ir_analysis():
    assert da.target_pages(1200, 2) == 2 + 1 + 1
    ir = "---\ntitle: T\n---\n\nhello world one two\n\n```mermaid\nflowchart LR\nA-->B\n```\n\n```python\nx=1\n```\n"
    a = da.analyze_ir(ir)
    assert a["figure_count"] == 1
    assert a["words"] == 4


def test_detect_rule_tables_from_cell_rules():
    cols = [(45, 115), (115, 235), (235, 551)]
    rects = []
    for top in (160, 230, 300):
        for x0, x1 in cols:
            rects.append({"x0": x0, "x1": x1, "top": top, "bottom": top + 1})
    # a full-width hr is a single rect -> not a table row
    rects.append({"x0": 45, "x1": 551, "top": 500, "bottom": 501})
    tabs = da.detect_rule_tables(rects)
    assert len(tabs) == 1
    assert tabs[0]["rules"] == [160, 230, 300]
    assert len(tabs[0]["cols"]) == 3


def _metrics(pages, nbis=0, ratio=1.0, r1=True):
    return {
        "document": {"page_count": pages, "density_ratio": ratio, "outline_ok": True,
                     "fonts_embedded": True, "metadata_ok": True,
                     "rubric": {"R1": {"pass": r1}, "R6": {"pass": True}}},
        "pages": [{"page": i + 1, "ink_fraction": 0.1, "ink_height_fraction": 0.9,
                   "interior_white_band_fraction": 0.05, "has_figure": False,
                   "figure_bisected": i < nbis, "heading_orphan": None,
                   "table_narrow_cells": 0, "word_count": 300,
                   "raw_markup_hits": {"html_comment": 0}} for i in range(pages)],
    }


def test_compare_reports_regressions_and_improvements():
    a = _metrics(10, nbis=0, ratio=1.4)
    b = _metrics(12, nbis=1, ratio=1.6, r1=False)
    d = da.compare_metrics(a, b)
    joined = " ".join(d["regressions"])
    assert "page_count" in joined
    assert "figure_bisected" in joined
    assert "rubric R1" in joined
    back = da.compare_metrics(b, a)
    assert not back["regressions"]
    assert any("page_count" in s for s in back["improvements"])


def test_figure_title_orphan_detection():
    """A 'Figure N — …' title as the last text on a page, with the figure at the
    top of the next page, is an orphaned figure title (d002)."""
    words = [_w("Body", 50, 100), _w("text", 90, 100),
             _w("Figure", 50, 760), _w("1", 95, 760), _w("\u2014", 105, 760), _w("Title", 115, 760),
             _w("(artifact", 50, 774), _w("x).", 110, 774)]
    lines = da.group_lines(words)
    box = (45, 45, 555, 790)
    next_figs = [[60, 60, 500, 400, 120]]
    assert da.figure_title_orphan(lines, [], next_figs, box, box) == "Figure 1 \u2014 Title"
    # figure below the title on the SAME page -> not orphaned
    assert da.figure_title_orphan(lines, [[60, 780, 500, 789, 50]], next_figs, box, box) is None
    # next page's figure starts low (after prose) -> not this defect
    assert da.figure_title_orphan(lines, [], [[60, 600, 500, 780, 50]], box, box) is None
    # prose that merely mentions a figure is not a title
    lines2 = da.group_lines([_w("Body", 50, 100), _w("Figure", 50, 774), _w("3", 95, 774),
                             _w("shows", 105, 774), _w("that", 150, 774)])
    assert da.figure_title_orphan(lines2, [], next_figs, box, box) is None
    # the decorator's bare auto label under a drawing is not a title
    lines3 = da.group_lines([_w("Body", 50, 100), _w("Figure", 50, 774), _w("3", 95, 774)])
    assert da.figure_title_orphan(lines3, [], next_figs, box, box) is None


# ---------------------------------------------------------------------------
# d017: figure edge-clipping signal
# ---------------------------------------------------------------------------

_BOX = (45.35, 45.35, 550.57, 790.0)


def _curve(x0, top, x1, bottom, fill=(0.1, 0.1, 0.1), stroke=False):
    return {"x0": x0, "x1": x1, "top": top, "bottom": bottom,
            "non_stroking_color": fill, "stroke": stroke}


def test_whiteish_unstroked_curves_not_figure_ink():
    """d017: an unstroked near-white (#f3f4f6) curve hanging off the paper is
    a label background / frame corner, not ink — it must neither widen the
    figure cluster nor flag the figure as clipped at the paper edge."""
    from types import SimpleNamespace
    dark = [_curve(100 + i * 5, 100 + i * 4, 110 + i * 5, 110 + i * 4) for i in range(50)]
    pale = _curve(15.7, 59.2, 105.7, 80.0, fill=(0.9529, 0.9569, 0.9647))
    page = SimpleNamespace(curves=dark + [pale], rects=[], lines=[], images=[], chars=[])
    figs = da._figure_clusters(page, _BOX, [])
    assert len(figs) == 1
    assert figs[0][0] >= _BOX[0]
    assert da.figure_overflow_x(figs, [], _BOX) is False
    # a STROKED pale curve (mermaid subgraph fill) is still figure ink
    stroked = _curve(15.7, 59.2, 105.7, 80.0, fill=(0.9529, 0.9569, 0.9647), stroke=True)
    page2 = SimpleNamespace(curves=dark + [stroked], rects=[], lines=[], images=[], chars=[])
    figs2 = da._figure_clusters(page2, _BOX, [])
    assert figs2[0][0] < _BOX[0]


def test_figure_text_overflow_from_cut_glyphs():
    """d017: real clipping shows up as glyphs cut at the paper edge."""
    figs = [[60.0, 100.0, 540.0, 400.0, 120]]
    inside = {"x0": 41.0, "x1": 46.0, "top": 150.0, "bottom": 158.0, "text": "S"}
    outside = {"x0": 41.0, "x1": 46.0, "top": 600.0, "bottom": 608.0, "text": "S"}
    right = {"x0": 551.0, "x1": 556.0, "top": 150.0, "bottom": 158.0, "text": "e"}
    ok = {"x0": 60.0, "x1": 66.0, "top": 150.0, "bottom": 158.0, "text": "a"}
    assert da.figure_text_overflow_x([inside], figs, _BOX) is True
    assert da.figure_text_overflow_x([right], figs, _BOX) is True
    assert da.figure_text_overflow_x([outside], figs, _BOX) is False
    assert da.figure_text_overflow_x([ok], figs, _BOX) is False
    assert da.figure_overflow_x(figs, [inside], _BOX) is True


def test_overflow_regression_on_real_pdfs():
    """d017: iter_002 p6 (Fig 4 subtitle genuinely clipped) is flagged; the
    iter_002/iter_003 p5 false positive (pale curves) is not."""
    from pathlib import Path
    import pytest
    root = Path(__file__).resolve().parents[2] / ".ziya/doc-aesthetics/mir-shaper-3pager"
    ir, p2, p3 = root / "input.md", root / "iter_002/doc.pdf", root / "iter_003/doc.pdf"
    if not (ir.exists() and p2.exists() and p3.exists()):
        pytest.skip("mir-shaper-3pager iteration PDFs not present")
    irt = ir.read_text(encoding="utf-8")
    o2 = da.pdf_figure_overflow(p2.read_bytes(), irt)
    o3 = da.pdf_figure_overflow(p3.read_bytes(), irt)
    assert o2[5] is True and o2[4] is False
    assert o3[4] is False and o3[5] is False


# ---------------------------------------------------------------------------
# iter_005: d015 footer font metric, d023 true-colour rasters
# ---------------------------------------------------------------------------

def test_footer_font_min_pt_reported():
    box = (45.0, 45.0, 550.0, 790.0)
    words = [
        {"text": "body", "top": 100.0, "bottom": 110.0, "size": 11.0},
        {"text": "Title", "top": 800.0, "bottom": 807.0, "size": 7.05},
        {"text": "Ziya", "top": 808.0, "bottom": 815.0, "size": 6.6},
    ]
    assert da.footer_font_min_pt(words, box) == 6.6
    assert da.footer_font_min_pt(words[:1], box) is None


def _one_page_red_pdf() -> bytes:
    content = b"1 0 0 rg 0 0 200 200 re f\n"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_rasterize_true_rgb_order():
    """d023: pypdfium2 renders BGR by default; doc_aesthetics PNGs must be RGB."""
    from tests.export_fidelity import render_harness
    doc = render_harness.load_pdf(_one_page_red_pdf(), dpi=72, rev_byteorder=True)
    p = doc.pages[0].rgb
    r, g, b = (int(v) for v in p[p.shape[0] // 2, p.shape[1] // 2])
    assert r > 240 and g < 15 and b < 15, (r, g, b)
    # default stays byte-identical for the conversation suite (BGR)
    legacy = render_harness.load_pdf(_one_page_red_pdf(), dpi=72).pages[0].rgb
    assert int(legacy[legacy.shape[0] // 2, legacy.shape[1] // 2][2]) > 240


def test_metadata_ok_rejects_conversation_subject_on_document():
    good = {"title": "T", "author": "A", "subject": "Ziya document export"}
    bad = dict(good, subject="Ziya conversation export")
    assert da.document_metadata_ok(True, good, {"author": "A"}) is True
    assert da.document_metadata_ok(True, bad, {"author": "A"}) is False
    assert da.document_metadata_ok(True, dict(good, author="B"), {"author": "A"}) is False
    assert da.document_metadata_ok(False, good, {}) is False


def test_runt_last_page_metric():
    def pg(n, words, ink, nxt=False):
        return {"page": n, "word_count": words, "ink_height_fraction": ink,
                "next_page_starts_with_heading": nxt}
    pages = [pg(1, 500, 0.95), pg(2, 500, 0.96), pg(3, 47, 0.08)]
    assert da.runt_last_page(pages, pagebreaks=0) is True
    assert da.runt_last_page(pages[:2] + [pg(3, 300, 0.5)], pagebreaks=0) is False
    # Last page begins a forced-pagebreak section: intentional, not a runt.
    forced = [pg(1, 500, 0.95), pg(2, 500, 0.96, nxt=True), pg(3, 47, 0.08)]
    assert da.runt_last_page(forced, pagebreaks=1) is False
    assert da.runt_last_page(pages[:1], pagebreaks=0) is False


# ---------------------------------------------------------------------------
# iter_009: d032 quarter-turn (rotated) glyphs count toward figure_min_text_pt
# ---------------------------------------------------------------------------

def _rotated_figure_pdf(rot_size=5.0, upright=True) -> bytes:
    from fpdf import FPDF
    pdf = FPDF(unit="pt", format=(595, 842))
    pdf.add_page()
    # a figure: >= 40 stroked rects spanning 100..400pt vertically
    for k in range(60):
        pdf.rect(80 + (k % 10) * 40, 100 + (k // 10) * 50, 30, 30)
    if upright:
        pdf.set_font("helvetica", size=9)
        pdf.text(90, 120, "upright label")
    pdf.set_font("helvetica", size=rot_size)
    with pdf.rotation(90, 300, 300):
        pdf.text(300, 300, "Rotated axis title")
    return bytes(pdf.output())


def test_glyph_size_pt_upright_and_rotated():
    up = {"size": 9.0, "upright": True, "matrix": (1, 0, 0, 1, 0, 0), "x0": 0, "x1": 5}
    rot = {"size": 2.78, "upright": False, "matrix": (0.0, 5.0, -5.0, 0.0, 0, 0),
           "x0": 10.0, "x1": 15.0}
    skew = {"size": 3.0, "upright": False, "matrix": (0.7, 0.7, -0.7, 0.7, 0, 0),
            "x0": 0, "x1": 4}
    assert da.glyph_size_pt(up) == 9.0
    assert abs(da.glyph_size_pt(rot) - 5.0) < 1e-6
    assert da.glyph_size_pt(skew) is None


def test_figure_min_text_counts_quarter_turn_glyphs():
    m = da.compute_metrics(_rotated_figure_pdf(), "---\ntitle: T\n---\n\nx\n", dpi=36)
    p = m["pages"][0]
    assert p["has_figure"], p["figure_bboxes_pts"]
    assert p["figure_min_text_pt"] is not None
    assert abs(p["figure_min_text_pt"] - 5.0) <= 0.3, p["figure_min_text_pt"]
    # rotated-only figure text is measured, not a vacuous None
    m2 = da.compute_metrics(_rotated_figure_pdf(upright=False), "---\ntitle: T\n---\n\nx\n", dpi=36)
    assert m2["pages"][0]["figure_min_text_pt"] is not None
