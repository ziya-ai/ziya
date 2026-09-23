"""G-968f26 -- app/utils/latex_color.py tikz / tikz-cd colour-repair group.

Each test fails against the pre-fix normaliser and passes with the fix; theme
defects are asserted in BOTH themes.

Covered defects:
  * D-482  hsl()/hsla() converted to an rgb expression (the ``%`` in
    ``hsl(210,50%,40%)`` is a TeX comment that would swallow the line).
  * D-484  ``color=``/``text=`` transparent must NOT become the ``none`` colour
    (valid only on ``fill=``/``draw=``); it resolves to the theme ink instead.
    CSS opacity keywords (``opacity=none`` / ``inherit``) become a number.
  * D-485  a Sass ``$theme-fg`` colour token resolves to the theme ink.
  * D-468  a ``\\fill[black!88] ... rectangle`` positional fill colour is a
    region, not a stroke, and must NOT be lifted to a mid-grey slab -- even when
    the plate colour equals the dark page (backdrop == page).
  * D-471  a ``|[fill=C]|`` tikz-cd cell label is measured against the ENCLOSING
    FILL, not the page: a legible on-fill pair is kept and an illegible one is
    lifted.
  * D-491  a body-level ``\\definecolor`` custom colour is resolvable by the
    dark contrast clamp (draw=motifA is lifted, not left invisible on the dark
    page).
"""
import re

import pytest

from app.utils.latex_color import (
    normalize_colors,
    _contrast_ratio,
    _resolve_xcolor_rgb,
)

_EXPR = re.compile(r"\{?rgb,255:red,(\d+);green,(\d+);blue,(\d+)\}?")


def _channels(text):
    return [tuple(int(x) for x in m.groups()) for m in _EXPR.finditer(text)]


# ---------------------------------------------------------------------------
# D-482 -- hsl()/hsla()
# ---------------------------------------------------------------------------
_HSL = (
    r"\begin{tikzcd}" "\n"
    r"A \arrow[r, color=hsl(210,50%,40%)] & B" "\n"
    r"\end{tikzcd}"
)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_d482_hsl_converted_no_percent_comment(theme):
    # Direction: the raw body carries the fatal hsl() with a '%'.
    assert "hsl(" in _HSL and "%" in _HSL
    out, _ = normalize_colors(_HSL, theme=theme)
    # The CSS call and its line-eating '%' are gone in both themes.
    assert "hsl(" not in out, f"raw hsl() survived in {theme}"
    assert "%" not in out, f"'%' (TeX comment) survived in {theme}"
    # hsl(210,50%,40%) is #336699 = (51,102,153); the exact channels appear on
    # the light page (no clamp), and some rgb expression appears in dark.
    if theme == "light":
        assert (51, 102, 153) in _channels(out), out


# ---------------------------------------------------------------------------
# D-484 -- transparent / opacity keyword recovery
# ---------------------------------------------------------------------------
_TRANSP = (
    r"\begin{tikzcd}" "\n"
    r"A \arrow[r, color=transparent] \arrow[d, opacity=none] & "
    r"B \arrow[d, fill=transparent] \\" "\n"
    r"C \arrow[r, draw=none, opacity=inherit] & D" "\n"
    r"\end{tikzcd}"
)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_d484_color_transparent_not_none(theme):
    out, _ = normalize_colors(_TRANSP, theme=theme)
    # 'none' is invalid on color= (xcolor accepts it only on fill=/draw=): the
    # pre-fix rewrite produced the fatal ``color=none``.
    assert "color=none" not in out, f"color=none (fatal) emitted in {theme}"
    # color=transparent resolves to a visible theme ink instead.
    assert re.search(r"color=\{rgb,255:", out), f"color= not resolved in {theme}"
    # The region key keeps the valid ``none`` rewrite.
    assert "fill=none" in out, f"fill=transparent should map to none in {theme}"
    # CSS opacity keywords are neutralised to a pgf number (not left as words).
    assert "opacity=none" not in out and "opacity=inherit" not in out
    assert "opacity=1" in out


# ---------------------------------------------------------------------------
# D-485 -- Sass $theme-fg token
# ---------------------------------------------------------------------------
_SASS = (
    r"\begin{tikzcd}" "\n"
    r"A \arrow[r, color=$theme-fg] & B" "\n"
    r"\end{tikzcd}"
)


@pytest.mark.parametrize("theme,fg", [("light", (0, 0, 0)), ("dark", (237, 237, 237))])
def test_d485_sass_theme_token_resolves(theme, fg):
    assert "$theme-fg" in _SASS
    out, _ = normalize_colors(_SASS, theme=theme)
    assert "$theme-fg" not in out, f"$theme-fg survived in {theme}"
    assert fg in _channels(out), f"theme ink {fg} not resolved in {theme}: {out}"


# ---------------------------------------------------------------------------
# D-468 -- \fill[black!88] plate is a region, not a stroke
# ---------------------------------------------------------------------------
_PLATE = (
    r"\fill[black!88] (-1.6,-2.2) rectangle (6.2,2.4);" "\n"
    r"\node[draw=white,text=white] (i) at (0,1.4) {ingest};" "\n"
    r"\draw[white] (i) -- (0,0);"
)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_d468_fill_plate_not_lifted_to_grey(theme):
    out, _ = normalize_colors(_PLATE, theme=theme)
    # The fill positional colour must remain a fill region -- NOT rewritten to
    # ``\fill[color={rgb,107;107;107}]`` (the mid-grey slab the pre-fix dark
    # clamp produced when the plate colour equalled the dark page).
    fill_line = next(l for l in out.splitlines() if "rectangle" in l)
    assert "black!88" in fill_line, f"plate fill was rewritten in {theme}: {fill_line}"
    assert "107;green,107;blue,107" not in fill_line, \
        f"plate lifted to grey slab in {theme}: {fill_line}"


# ---------------------------------------------------------------------------
# D-471 -- on-fill label measured against the enclosing cell fill
# ---------------------------------------------------------------------------
_ONFILL = (
    r"\definecolor{onfillA}{HTML}{F1C40F}"  # yellow
    r"\definecolor{onfillD}{HTML}{F9E79F}"  # pale yellow
    r"\begin{tikzcd}" "\n"
    r"|[fill=onfillA]| \textcolor{black}{A} & "
    r"|[fill=onfillD]| \textcolor{white}{D}" "\n"
    r"\end{tikzcd}"
)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_d471_onfill_label_uses_enclosing_fill(theme):
    out, _ = normalize_colors(_ONFILL, theme=theme)
    yellow = (0xF1, 0xC4, 0x0F)
    pale = (0xF9, 0xE7, 0x9F)
    # black on the yellow fill is 12.64:1 -> a correct pair, LEFT as black.
    # (The pre-fix clamp measured black against the page and greyed it out.)
    assert r"\textcolor{black}{A}" in out, \
        f"correct on-fill black label was altered in {theme}: {out}"
    # white on the pale-yellow fill is 1.24:1 -> illegible, must be lifted away
    # from bare white and be legible ON THE FILL.
    m = re.search(r"\\textcolor\{(rgb,255:red,\d+;green,\d+;blue,\d+)\}\{D\}", out)
    assert m, f"illegible on-fill white label was not lifted in {theme}: {out}"
    rgb = _resolve_xcolor_rgb("{" + m.group(1) + "}")
    assert _contrast_ratio(rgb, pale) >= 4.5, \
        f"lifted D ink {rgb} still illegible on the pale fill in {theme}"
    # and the plain white was actually changed
    assert rgb != (255, 255, 255)


# ---------------------------------------------------------------------------
# D-491 -- body-level \definecolor resolvable by the dark clamp
# ---------------------------------------------------------------------------
_DEFCOLOR = (
    r"\definecolor{motifA}{HTML}{1F4E79}" "\n"       # dark blue #1F4E79
    r"\draw[draw=motifA] (0,0) -- (1,0);"
)


def test_d491_definecolor_lifted_on_dark_page():
    out, applied = normalize_colors(_DEFCOLOR, theme="dark")
    # motifA (#1F4E79) is 1.90:1 on the dark page -> the clamp must resolve the
    # \definecolor name and lift the stroke to a legible rgb expression.
    m = re.search(r"draw=\{(rgb,255:red,\d+;green,\d+;blue,\d+)\}", out)
    assert m, f"draw=motifA was left unresolved on the dark page: {out}"
    rgb = _resolve_xcolor_rgb("{" + m.group(1) + "}")
    assert _contrast_ratio(rgb, (0x1F, 0x1F, 0x1F)) >= 3.0, \
        f"lifted motifA stroke {rgb} still below the graphical floor"


def test_d491_definecolor_untouched_on_light_page():
    # #1F4E79 on white is high-contrast -> left exactly as authored in light.
    out, _ = normalize_colors(_DEFCOLOR, theme="light")
    assert "draw=motifA" in out, f"motifA wrongly lifted in light: {out}"
