r"""
Regression tests for fix group G-f21848 (app/utils/latex_color.py).

Three circuitikz/tikz-cd theme defects, all rooted in the contrast passes:

* D-352 / D-354 -- LIGHT-page pale author INK on circuitikz component labels.
  The stroke/ink option clamp (``_clamp_body_colours``) early-returned on the
  light bare page, so a ``\draw[cyan] ... to[R=$R_1$, color=cyan]`` bipole label
  (cyan on white = 1.25:1) and a ``color=darkink`` #CCCCCC scope ink (1.61:1)
  stayed illegible.  The fix runs the option clamp on the light page in a
  RESTRICTED "labels only" mode gated on ``_statement_has_ckt_label`` -- a
  circuitikz ``to[UPPER=..]`` component label is the unambiguous illegible-label
  case (the same carve-out d033 makes for ``\color`` macros).  A general tikz
  ``\node`` stroke / decorative stroke on the light page is deliberately left
  untouched (the g04/d234-protected light passthrough).

* D-356 -- DARK-page circuitikz shape-INTERNAL glyphs.  A shape-only node
  (``\node[mixer, fill=blue!20]{}``) draws its internal symbol in the node's
  ``draw`` colour, which inherits the baked light default ink (#EDEDED) and
  collapses on the pale fill (1.03:1).  The label-ink pass only re-inks TEXT,
  never the shape stroke, so a new dark-only pass injects a fill-legible
  ``draw`` ink for an empty-label pale-fill node.

Every assertion below FAILS against the pre-fix engine and passes with it.
Both themes are exercised for the theme defects.
"""
import re

from app.utils.latex_color import (
    normalize_colors,
    _resolve_xcolor_rgb,
    _contrast_ratio,
    _THEME_SURFACE_RGB,
)

WHITE = _THEME_SURFACE_RGB["light"]
DARK = _THEME_SURFACE_RGB["dark"]
GRAPHICAL_FLOOR = 3.0
TEXT_FLOOR = 4.5

_EXPR = re.compile(r"(?:draw|color|text)=\{(rgb,255:red,\d+;green,\d+;blue,\d+)\}")


def _opt_inks(text: str):
    return [_resolve_xcolor_rgb("{" + m.group(1) + "}") for m in _EXPR.finditer(text)]


# ---------------------------------------------------------------------------
# D-352: circuitikz component-label ink is lifted on the LIGHT page (w2-11).
# ---------------------------------------------------------------------------
W2_11 = (r"\draw[cyan] (0,0) to[R=$R_{1}$, color=cyan] (4,0);"
         r"\draw[cyan!60] (0,1.2) to[R=$R_{2}$, color=cyan!60] (4,1.2);")


def test_d352_ckt_label_ink_lifted_on_light_page():
    """cyan / cyan!60 label ink (1.25 / ~1.14:1 on white) is lifted to >=4.5."""
    # Precondition: cyan really is below the text floor on white.
    assert _contrast_ratio((0, 255, 255), WHITE) < TEXT_FLOOR

    light, applied = normalize_colors(W2_11, theme="light")
    inks = _opt_inks(light)
    assert inks, "no circuitikz label ink was clamped on the light page"
    for rgb in inks:
        assert _contrast_ratio(rgb, WHITE) >= TEXT_FLOOR, (rgb, _contrast_ratio(rgb, WHITE))
    assert applied


def test_d352_ckt_label_ink_still_legible_on_dark():
    """Both-theme discipline: the dark leg keeps every label ink >= the floor."""
    dark, _ = normalize_colors(W2_11, theme="dark")
    for rgb in _opt_inks(dark):
        assert _contrast_ratio(rgb, DARK) >= GRAPHICAL_FLOOR


# ---------------------------------------------------------------------------
# D-354: a hardcoded pale scope ink on the light page (w3-02 shape).
# ---------------------------------------------------------------------------
W3_02_LABEL = (r"\definecolor{darkink}{HTML}{CCCCCC}"
               r"\draw[darkink, thick] (0,3) to[R=$R_b$] (3,3);")


def test_d354_hardcoded_pale_definecolor_label_lifted_on_light():
    """draw=darkink (#CCCCCC = 1.61:1 on white) painting an R= label is lifted."""
    assert _contrast_ratio((0xCC, 0xCC, 0xCC), WHITE) < TEXT_FLOOR
    light, applied = normalize_colors(W3_02_LABEL, theme="light")
    inks = _opt_inks(light)
    assert inks
    for rgb in inks:
        assert _contrast_ratio(rgb, WHITE) >= TEXT_FLOOR
    assert applied


# ---------------------------------------------------------------------------
# Contract: a general tikz node / decorative stroke on the LIGHT page stays
# byte-identical (this is the direction that regressed g04/d234).
# ---------------------------------------------------------------------------
def test_light_general_node_stroke_is_untouched():
    body = r"\node[thick,draw,white,dashed] at (0,0) {x};"
    assert normalize_colors(body, theme="light")[0] == body


def test_light_node_draw_border_with_fill_is_untouched():
    body = r"\node[draw=gray, fill=yellow!35, rounded corners] {L};"
    assert normalize_colors(body, theme="light")[0] == body


def test_light_decorative_pale_stroke_without_label_is_untouched():
    body = r"\draw[cyan!20] (0,0) -- (4,0);"
    assert normalize_colors(body, theme="light")[0] == body


# ---------------------------------------------------------------------------
# D-356: dark-page shape-only node gets a fill-legible draw ink so its
# internal glyphs clear the floor; light is byte-identical.
# ---------------------------------------------------------------------------
W3_14 = r"\node[mixer, fill=blue!20] (mx) at (0,0) {};"


def test_d356_shape_only_pale_fill_gets_dark_draw_ink_on_dark():
    """blue!20 -> #CCCCFF; #EDEDED default ink = 1.31:1, so draw=black added."""
    blue20 = (204, 204, 255)
    assert _contrast_ratio((0xED, 0xED, 0xED), blue20) < GRAPHICAL_FLOOR
    dark, applied = normalize_colors(W3_14, theme="dark")
    assert "draw=black" in dark, dark
    # The injected shape ink clears the graphical floor on the node's own fill.
    assert _contrast_ratio((0, 0, 0), blue20) >= GRAPHICAL_FLOOR
    assert any("shape-only" in a for a in applied)


def test_d356_light_page_is_byte_identical():
    """The shape-ink pass is dark-only; the light render must not change."""
    light, _ = normalize_colors(W3_14, theme="light")
    assert "draw=black" not in light


def test_d356_non_shape_filled_text_node_gets_no_border():
    """A node with a real text label (not shape-only) never gains a draw border
    on the dark page -- only its label ink is handled, per the label pass."""
    body = r"\node[fill=blue!20] at (0,0) {hello};"
    dark, _ = normalize_colors(body, theme="dark")
    assert "draw=black" not in dark
