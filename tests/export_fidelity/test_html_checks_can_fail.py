"""
Prove every HTML-specific fidelity check CAN fail (Card II).

Mirror of ``test_checks_can_fail.py`` for the HTML checks in ``checks.py``.
Each check reads a rendered-DOM/computed-style ``probe`` (plus static scans)
attached to ``doc.meta`` by ``render_harness.render_html``.  These tests
SYNTHESISE that meta dict (a passing shape and a deliberately-broken shape) so
they run in milliseconds with NO browser — the guard that keeps the HTML checks
honest as they evolve.

A synthetic HTML ``RenderedDocument`` is a single 1x1 white page (raster checks
are covered elsewhere) with ``source_format='html'`` and a hand-built ``meta``.
"""
from __future__ import annotations

import numpy as np

from tests.export_fidelity import checks as C
from tests.export_fidelity.render_harness import RenderedDocument, RenderedPage


def _html_doc(meta, *, text="", dark_rgb=None):
    rgb = np.full((4, 4, 3), 255, dtype=np.uint8)
    page = RenderedPage(0, rgb, text, 4, 4, [])
    m = dict(meta)
    if dark_rgb is not None:
        m["dark_rgb"] = dark_rgb
    return RenderedDocument(
        pages=[page], full_text=text, source_format="html", dpi=150.0, meta=m,
    )


def _probe(**over):
    base = {
        "token_span_count": 0, "token_distinct_colors": [],
        "diff_insert_count": 0, "diff_insert_bgs": [],
        "diff_delete_count": 0, "diff_delete_bgs": [],
        "katex_count": 0, "mark_count": 0, "mark_bgs": [],
        "table_count": 0, "table_cell_count": 0, "table_row_count": 0,
        "literal_pipe_table_rows": 0,
        "xss_fired": False, "body_bg": "rgb(255, 255, 255)", "inner_text": "",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# self_containment
# ---------------------------------------------------------------------------

def test_self_containment_can_pass_and_fail():
    ok = _html_doc({"resource_refs": {"relative": [], "localhost": [],
                                      "blob": [], "external_http": []}})
    assert C.check_self_containment(ok).passed

    bad = _html_doc({"resource_refs": {
        "relative": ["./assets/logo.png"], "localhost": ["http://localhost:6969/x.js"],
        "blob": ["blob:abc"], "external_http": ["https://cdn.example/x.css"]}})
    res = C.check_self_containment(bad)
    assert not res.passed
    assert res.measurements["relative_count"] == 1
    assert res.measurements["blob_count"] == 1


# ---------------------------------------------------------------------------
# dark_mode_independence
# ---------------------------------------------------------------------------

def test_dark_mode_independence_can_pass_and_fail():
    white = np.full((40, 40, 3), 255, dtype=np.uint8)
    ok = _html_doc({"dark_probe": {"body_bg": "rgb(255, 255, 255)"}}, dark_rgb=white)
    assert C.check_dark_mode_independence(ok).passed

    dark = np.full((40, 40, 3), 13, dtype=np.uint8)   # #0d0d0d whole page
    bad = _html_doc({"dark_probe": {"body_bg": "rgb(13, 17, 23)"}}, dark_rgb=dark)
    res = C.check_dark_mode_independence(bad)
    assert not res.passed
    assert res.measurements["dark_scheme_dark_fraction"] > 0.15


# ---------------------------------------------------------------------------
# diff_coloring
# ---------------------------------------------------------------------------

def test_diff_coloring_can_pass_and_fail():
    ok = _html_doc({"probe": _probe(
        diff_insert_count=1, diff_insert_bgs=["rgb(230, 255, 236)"],
        diff_delete_count=1, diff_delete_bgs=["rgb(255, 235, 233)"])})
    assert C.check_diff_coloring(ok).passed

    # FAIL: no per-line elements (the Python regex exporter's uncolored <pre>).
    bad = _html_doc({"probe": _probe()})
    res = C.check_diff_coloring(bad)
    assert not res.passed
    assert res.measurements["insert_elements"] == 0

    # FAIL: elements exist but insert/delete share the same background.
    same = _html_doc({"probe": _probe(
        diff_insert_count=1, diff_insert_bgs=["rgb(240, 240, 240)"],
        diff_delete_count=1, diff_delete_bgs=["rgb(240, 240, 240)"])})
    res2 = C.check_diff_coloring(same)
    assert not res2.passed
    assert res2.measurements["distinguishable"] is False


# ---------------------------------------------------------------------------
# syntax_highlighting
# ---------------------------------------------------------------------------

def test_syntax_highlighting_can_pass_and_fail():
    ok = _html_doc({"probe": _probe(
        token_span_count=12,
        token_distinct_colors=["rgb(0,0,255)", "rgb(163,21,21)", "rgb(0,128,0)"])})
    assert C.check_syntax_highlighting(ok).passed

    # FAIL: zero token spans (no Prism).
    bad = _html_doc({"probe": _probe()})
    assert not C.check_syntax_highlighting(bad).passed

    # FAIL: spans exist but all one color (uniform == not highlighted).
    uniform = _html_doc({"probe": _probe(
        token_span_count=8, token_distinct_colors=["rgb(36,41,46)"])})
    res = C.check_syntax_highlighting(uniform)
    assert not res.passed
    assert res.measurements["distinct_color_count"] == 1


# ---------------------------------------------------------------------------
# math_rendering
# ---------------------------------------------------------------------------

def test_math_rendering_can_pass_and_fail():
    ok = _html_doc({"probe": _probe(katex_count=2)})
    assert C.check_math_rendering(ok).passed

    bad = _html_doc({"probe": _probe(katex_count=0)})
    res = C.check_math_rendering(bad)
    assert not res.passed
    assert res.measurements["katex_element_count"] == 0


# ---------------------------------------------------------------------------
# table_rendering (HTML-06)
# ---------------------------------------------------------------------------

def test_table_rendering_can_pass_and_fail():
    ok = _html_doc({"probe": _probe(table_count=1, table_cell_count=9,
                                    table_row_count=3, literal_pipe_table_rows=0)})
    assert C.check_table_rendering(ok).passed

    # broken: table left as literal pipe text (0 tables, a surviving delimiter row)
    bad = _html_doc({"probe": _probe(table_count=0, table_cell_count=0,
                                     literal_pipe_table_rows=1)})
    res = C.check_table_rendering(bad)
    assert not res.passed
    assert res.measurements["table_count"] == 0
    assert res.measurements["literal_pipe_table_rows"] == 1

    # broken: a <table> exists but a delimiter row still leaked into the text
    leak = _html_doc({"probe": _probe(table_count=1, table_cell_count=9,
                                      literal_pipe_table_rows=2)})
    assert not C.check_table_rendering(leak).passed


# ---------------------------------------------------------------------------
# highlight_preservation (vacuous pass when no mark; fails on lost bg)
# ---------------------------------------------------------------------------

def test_highlight_preservation_vacuous_pass_and_real_fail():
    # vacuous pass: no <mark> at all (canonical fixture — highlight non-feature)
    vac = _html_doc({"probe": _probe(mark_count=0, mark_bgs=[])})
    r = C.check_highlight_preservation(vac)
    assert r.passed and r.measurements["vacuous_pass"] is True

    # pass: a mark WITH a background
    kept = _html_doc({"probe": _probe(mark_count=1, mark_bgs=["rgb(255, 241, 118)"])})
    assert C.check_highlight_preservation(kept).passed

    # FAIL: a mark present but its background was stripped (transparent)
    lost = _html_doc({"probe": _probe(mark_count=1, mark_bgs=["transparent"])})
    res = C.check_highlight_preservation(lost)
    assert not res.passed


# ---------------------------------------------------------------------------
# structural_validity
# ---------------------------------------------------------------------------

def test_structural_validity_can_pass_and_fail():
    ok = _html_doc({"parse_errors": []})
    assert C.check_structural_validity(ok).passed

    # non-structural nits alone do NOT fail
    nits = _html_doc({"parse_errors": ["invalid-codepoint@(1, 2)",
                                       "named-entity-without-semicolon@(3, 4)"]})
    assert C.check_structural_validity(nits).passed

    # FAIL: a real unclosed-tag / mis-nesting recovery (e.g. <pre> inside <p>)
    bad = _html_doc({"parse_errors": ["unexpected-end-tag@(135, 17)",
                                      "unexpected-start-tag@(9, 1)"]})
    res = C.check_structural_validity(bad)
    assert not res.passed
    assert len(res.measurements["structural_errors"]) == 2


# ---------------------------------------------------------------------------
# xss_neutralized
# ---------------------------------------------------------------------------

def test_xss_neutralized_can_pass_and_fail():
    # PASS: payloads present only as ESCAPED text; canary did not fire.
    safe_html = (
        "<p>A script tag: &lt;script&gt;evil()&lt;/script&gt; end.</p>"
        "<p>An image: &lt;img src=x onerror=evil()&gt; end.</p>"
        "<p>A link: click me (javascript:evil())</p>"
    )
    ok = _html_doc({"probe": _probe(xss_fired=False), "html": safe_html})
    assert C.check_xss_neutralized(ok).passed

    # FAIL (canary): a script executed on open.
    fired = _html_doc({"probe": _probe(xss_fired=True), "html": safe_html})
    assert not C.check_xss_neutralized(fired).passed

    # FAIL (static): a real executable construct leaked into the HTML.
    leaked = _html_doc({"probe": _probe(xss_fired=False),
                        "html": "<p>x</p><script>evil()</script>"})
    res = C.check_xss_neutralized(leaked)
    assert not res.passed
    assert res.measurements["executable_constructs"]

    # FAIL (static): a live on* handler and a javascript: href.
    leaked2 = _html_doc({"probe": _probe(xss_fired=False),
                         "html": '<img src=x onerror="evil()"><a href="javascript:evil()">x</a>'})
    assert not C.check_xss_neutralized(leaked2).passed


# ---------------------------------------------------------------------------
# meta: run_all_checks includes HTML checks for an html document
# ---------------------------------------------------------------------------

def test_run_all_checks_includes_html_checks_for_html_doc():
    doc = _html_doc(
        {"probe": _probe(), "resource_refs": {"relative": [], "localhost": [],
                                              "blob": [], "external_http": []},
         "parse_errors": [], "dark_probe": {}},
        text=" ",
        dark_rgb=np.full((4, 4, 3), 255, dtype=np.uint8),
    )
    names = {r.name for r in C.run_all_checks(doc)}
    assert set(C.HTML_CHECKS) <= names
    assert set(C.FORMAT_NEUTRAL_CHECKS) <= names
