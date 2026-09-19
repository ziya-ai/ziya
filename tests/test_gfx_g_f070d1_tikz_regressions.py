r"""
Regression guards for fix group G-f070d1 (tikz), run d2c18548.

Three regressions that were verified once and reappeared, all surfacing through
the LaTeX theme/size pipeline:

* D-233 (author colour below floor on the dark plate) had two live failure
  modes the earlier fix did not cover:
    - tikz-w2-11: the bare-colour dark lift rewrote a colour NAME inside a
      ``\foreach ... in {red,blue,green}`` VALUE LIST into ``color={rgb,...}``,
      turning the loop value into a bad option key -> pgfkeys abort -> NO PDF.
      The lift must fire only inside a ``[...]`` option list, never a ``{...}``
      value list.
    - tikz-w1-02/04/09: a colour on a statement that also carries a node LABEL
      paints the label glyphs, so it must clear the 4.5:1 small-text floor, not
      the 3:1 graphical floor (``blue!60!black`` lifted to 3.12:1 leaves the
      label under the text floor while the stroke passes).

* D-234 (pale-fill label ink) leaked on tikz-w1-06: the ``text=black`` injection
  landed on the whole style-definition block, so a node using an UNfilled
  sibling style (``base`` -> ``monitor``) also got black label text and vanished
  on the dark page.  Injection must be per ``.style`` that carries the pale
  fill.

* D-239 (explicit width/height ignored) regressed because ``_pdf_media_box_points``
  reads ``/MediaBox`` from the PDF bytes with a plaintext regex; modern pdfTeX
  compresses that into an object stream, so the scan misses it and the size
  request silently degrades to the natural-size PNG.  A Ghostscript ``bbox``
  fallback restores the size contract.

Each test asserts the FIXED behaviour and would fail against the pre-fix code.
Theme fixes are checked in BOTH themes.
"""

import re

import pytest

from app.utils.latex_color import (
    normalize_colors, _contrast_ratio, _resolve_xcolor_rgb,
)

DARK = (0x1F, 0x1F, 0x1F)
WHITE = (0xFF, 0xFF, 0xFF)
GRAPH_FLOOR = 3.0
TEXT_FLOOR = 4.5


def _dark_rgbs(out: str):
    return [(int(r), int(g), int(b))
            for r, g, b in re.findall(
                r"rgb,255:red,(\d+);green,(\d+);blue,(\d+)", out)]


# --------------------------------------------------------------------------
# D-233a: \foreach value list must never be rewritten (render abort guard).
# --------------------------------------------------------------------------
def test_foreach_colour_value_list_is_left_intact_on_dark():
    body = (r"\foreach \c [count=\i from 0] in "
            r"{red,blue,green,orange,violet,purple,gray}{"
            r"\draw[\c, line width=0.5pt] (0,\i) -- (1,\i);}")
    out, _ = normalize_colors(body, theme="dark")
    # The value list between `in {` and `}{` must be byte-identical: no colour
    # name promoted to color={rgb,...} (that key inside the loop aborts LaTeX).
    seg = out[out.index("in {") + 4: out.index("}{")]
    assert "color={" not in seg and "rgb,255" not in seg, (
        f"\\foreach value list was corrupted by the dark lift: {seg!r}")
    assert "blue,green,orange,violet,purple,gray" in seg


def test_bare_colour_option_inside_brackets_is_still_lifted_on_dark():
    # The guard must NOT stop the legitimate case: a bare colour that really is
    # an option inside [...] is still promoted on the dark page.
    out, _ = normalize_colors(r"\draw[blue] (0,0) -- (1,1);", theme="dark")
    assert "color={" in out
    for rgb in _dark_rgbs(out):
        assert _contrast_ratio(rgb, DARK) >= GRAPH_FLOOR


# --------------------------------------------------------------------------
# D-233b: a colour that also paints a node label must clear the TEXT floor.
# --------------------------------------------------------------------------
LABEL_COLOUR_CASES = [
    ("w1-02 stroke+label",
     r"\draw[-{Stealth},line width=2pt,blue!60!black] (0,0) -- (4,0) "
     r"node[right] {thick blue};"),
    ("w1-04 fill+label",
     r"\fill[blue] (0,0) circle (2pt) node[right] {1/3 BC};"),
    ("w1-09 node label",
     r"\node[blue,right] at (4.6,1.2) {$\sin x$};"),
]


@pytest.mark.parametrize("label,body", LABEL_COLOUR_CASES, ids=[c[0] for c in LABEL_COLOUR_CASES])
def test_labelled_colour_is_lifted_to_text_floor_on_dark(label, body):
    out, _ = normalize_colors(body, theme="dark")
    rgbs = _dark_rgbs(out)
    assert rgbs, f"{label}: expected a lifted colour"
    for rgb in rgbs:
        assert _contrast_ratio(rgb, DARK) >= TEXT_FLOOR, (
            f"{label}: label colour {rgb} is "
            f"{_contrast_ratio(rgb, DARK):.2f}:1 on dark, below the "
            f"{TEXT_FLOOR} text floor")


def test_pure_stroke_without_label_keeps_graphical_floor_on_dark():
    # A curve stroke with no node in its statement is graphical: lifting only
    # to 3:1 (not 4.5) is correct and must be preserved (no over-brightening).
    body = r"\draw[blue!70!black, line width=0.2pt] plot (\x,\y);"
    out, _ = normalize_colors(body, theme="dark")
    rgbs = _dark_rgbs(out)
    assert rgbs
    top = max(_contrast_ratio(rgb, DARK) for rgb in rgbs)
    assert GRAPH_FLOOR <= top < TEXT_FLOOR, (
        f"pure stroke lifted to {top:.2f}:1; expected the graphical floor band")


def test_labelled_colour_light_page_is_byte_identical():
    for _, body in LABEL_COLOUR_CASES:
        out_light, _ = normalize_colors(body, theme="light")
        assert out_light == body, f"light page must stay identical: {body!r}"


# --------------------------------------------------------------------------
# D-234: pale-fill label ink must inject PER STYLE, not block-wide.
# --------------------------------------------------------------------------
W1_06 = (
    "[\n"
    "  base/.style={draw,thick,minimum width=22mm,minimum height=9mm,align=center},\n"
    "  ok/.style={base,fill=green!20,rounded corners},\n"
    "  warn/.style={base,fill=yellow!35,double},\n"
    "  bad/.style={base,fill=red!25,dashed},\n"
    "  flow/.style={-{Stealth},thick,gray!60!black}\n"
    "]\n"
    r"\node[ok] (a) at (0,2) {healthy};" "\n"
    r"\node[base] (d) at (4,0.8) {monitor};"
)


def test_pale_fill_ink_is_scoped_to_the_filled_style_on_dark():
    out, notes = normalize_colors(W1_06, theme="dark")

    def style_body(name):
        m = re.search(name + r"/\.style=\{([^{}]*)\}", out)
        assert m, f"{name}/.style not found in output"
        return m.group(1)

    # The filled styles get the dark label ink...
    assert "text=black" in style_body("ok")
    assert "text=black" in style_body("warn")
    assert "text=black" in style_body("bad")
    # ...the UNFILLED base style (inherited by the `monitor` node) does NOT,
    # so its label keeps the legible light default ink on the dark page.
    assert "text=black" not in style_body("base")
    # `monitor` uses `base`, so its label must NOT be forced black on dark.
    monitor_stmt = out[out.index(r"\node[base]"):]
    assert "text=black" not in monitor_stmt
    assert any("THIS style only" in n for n in notes)


def test_pale_fill_style_light_page_is_byte_identical():
    out_light, _ = normalize_colors(W1_06, theme="light")
    assert out_light == W1_06


def test_direct_node_fill_still_gets_block_level_ink_on_dark():
    # A plain \node[fill=green!20] (no .style) keeps the block-level injection.
    out, _ = normalize_colors(r"\node[fill=green!20] {L};", theme="dark")
    assert "text=black" in out


# --------------------------------------------------------------------------
# D-239: robust natural-size probe for compressed PDFs.
# --------------------------------------------------------------------------
from app.services.latex_renderer import LatexRenderer, PNG_DPI  # noqa: E402


def test_parse_gs_bbox_reads_hires_bounding_box():
    out = ("GPL Ghostscript 10.0\n"
           "%%BoundingBox: 0 0 132 97\n"
           "%%HiResBoundingBox: 0.000000 0.000000 131.520 96.375\n")
    assert LatexRenderer._parse_gs_bbox(out) == (131.520, 96.375)


def test_parse_gs_bbox_none_on_miss():
    assert LatexRenderer._parse_gs_bbox("") is None
    assert LatexRenderer._parse_gs_bbox("no bbox here") is None


def test_raster_dpi_uses_supplied_size_for_compressed_pdf(tmp_path):
    # A PDF whose /MediaBox is hidden (compressed) yields None from the byte
    # scan, so without a supplied size the request degrades to PNG_DPI...
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n<< compressed object streams, no plaintext MediaBox >>\n")
    assert LatexRenderer._raster_dpi(pdf, 2400, None) == float(PNG_DPI)
    # ...but with the gs-probed natural size threaded in, the width scales up.
    dpi = LatexRenderer._raster_dpi(pdf, 2400, None, size=(131.52, 96.0))
    assert dpi > float(PNG_DPI), (
        f"width=2400 on a ~131pt-wide crop should scale DPI up, got {dpi}")


def test_pdf_natural_size_falls_back_to_ghostscript(tmp_path, monkeypatch):
    from app.services import latex_renderer as lr

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n<< no plaintext MediaBox (compressed) >>\n")

    r = lr.latex_renderer
    # No plaintext MediaBox -> the byte scan returns None.
    assert r._pdf_media_box_points(pdf) is None

    cap = type("Cap", (), {"has_ghostscript": True})()
    monkeypatch.setattr(
        r, "_run",
        lambda argv, cwd, c: "%%HiResBoundingBox: 0 0 131.52 96.0\n")
    assert r._pdf_natural_size(pdf, cap) == (131.52, 96.0)

    # With no Ghostscript there is nothing to fall back to.
    cap_no_gs = type("Cap", (), {"has_ghostscript": False})()
    assert r._pdf_natural_size(pdf, cap_no_gs) is None
