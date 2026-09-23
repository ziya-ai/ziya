r"""
Regression tests for fix group G-5cdb01 (app.utils.latex_color): the contrast
clamp measured author colours against the WRONG surface in three ways.

Each test below FAILS against the pre-fix tree and passes with the fix, and
every theme-reactive assertion runs BOTH themes.

  * D-470  fill-opacity ignored.  A ``fill=blue, fill opacity=0.15`` cell is not
    blue -- it is 15% blue over the page (#D9D9FF, a near-white surface).  The
    old code measured the SATURATED colour, treated the cell as a dark backdrop,
    injected white labels / lifted a text ink toward white, and those inks then
    scored ~1.4:1 on the actual pale composite (tikz-cd-w3-10).  The fix
    composites fill/opacity over the page before measuring.

  * D-467  all-dark tikz-cd cell card.  ``cells={nodes={fill=<dark>}}`` with
    light ink is a dark card; the arrows crossing BETWEEN cells fall on the
    white page and vanish in light.  ``detect_dark_plate`` now extends the page
    to the dark cell fill (as it already does for a sole ``\fill`` rectangle),
    so off-cell ink stays legible.  A near-white-cell card is untouched.

  * D-351  circuitikz path ink tinting a component LABEL.  ``to[R=$R_1$,
    color=green!45!black]`` paints the ``R=$R_1$`` label in the path ink, but
    that ink was lifted only to the 3:1 graphical floor, leaving the label text
    sub-legible on the dark page.  A ``to[...]`` with an uppercase-keyed
    annotation is now recognised as a label carrier, so its ink clears the
    4.5:1 text floor.  A node ``text=`` with NO fill (a bare-page label) is
    clamped too, while a ``text=`` paired with a ``fill=`` stays untouched.
"""
import re

from app.utils.latex_color import (
    normalize_colors, detect_dark_plate,
    _collect_definecolors, _effective_surface, _rel_luminance,
    _contrast_ratio, _THEME_SURFACE_RGB, _TEXT_CONTRAST_FLOOR,
)

_EXPR = re.compile(
    r"rgb,255:red,(\d+);green,(\d+);blue,(\d+)")


def _rgbs(text):
    return [tuple(int(x) for x in m.groups()) for m in _EXPR.finditer(text)]


# --------------------------------------------------------------------------
# D-470  fill opacity folded into the effective surface (tikz-cd-w3-10 shape)
# --------------------------------------------------------------------------

_W3_10 = (
    r"\begin{tikzcd}[cells={nodes={draw=blue,fill=blue,fill opacity=0.15,"
    r"text opacity=1,inner sep=6pt}}]"
    "\n" r"A \rar[blue,opacity=0.25,""\textcolor{blue}{faint}""] & B"
    "\n" r"\end{tikzcd}"
)


def test_low_opacity_cell_fill_is_measured_as_the_pale_composite():
    """The 15%-blue cell is a near-WHITE surface on the white page and a dark
    surface on the dark page -- NOT saturated blue in both (pre-fix bug)."""
    defs = _collect_definecolors(_W3_10)
    light = _effective_surface(_W3_10, _THEME_SURFACE_RGB["light"], defs)
    dark = _effective_surface(_W3_10, _THEME_SURFACE_RGB["dark"], defs)
    # Pre-fix both were the opaque blue (luminance 0.0722 -> "dark").  Post-fix
    # the light-page composite is a light surface and differs from the dark one.
    assert _rel_luminance(light) > 0.18, light
    assert _rel_luminance(dark) < 0.18, dark
    assert light != dark


def test_no_white_label_injected_onto_the_pale_composite():
    """On the light page the effective cell is #D9D9FF, on which the default
    black label is legible -- so no ``text=white`` is injected (pre-fix it was),
    and the ``\\textcolor{blue}`` label is not force-lifted toward white."""
    out_light, _ = normalize_colors(_W3_10, theme="light")
    assert "text=white" not in out_light
    # The blue label sits at ~6:1 on #D9D9FF, so it is left as authored blue;
    # any lift would have pushed it toward white (the pre-fix >1.9-ratio bug).
    for rgb in _rgbs(out_light):
        # no near-white ink was emitted for the label/stroke inks
        assert not (rgb[0] > 200 and rgb[1] > 200 and rgb[2] > 250), (out_light, rgb)
    # Dark page still lifts the blue ink to a legible light blue.
    out_dark, _ = normalize_colors(_W3_10, theme="dark")
    assert out_dark != _W3_10


# --------------------------------------------------------------------------
# D-467  all-dark tikz-cd cell card extends the light page to the plate
# --------------------------------------------------------------------------

_DARK_CELLS = (
    r"\definecolor{darkfill}{HTML}{1E1E1E}"
    "\n" r"\definecolor{lightink}{HTML}{E6E6E6}"
    "\n" r"\begin{tikzcd}[cells={nodes={draw=lightink,fill=darkfill,inner sep=6pt}}]"
    "\n" r"\textcolor{lightink}{A} \rar[draw=lightink] & \textcolor{lightink}{B}"
    "\n" r"\end{tikzcd}"
)
_NEARWHITE_CELLS = (
    r"\definecolor{nearwhite}{HTML}{FDFDFD}"
    "\n" r"\begin{tikzcd}[cells={nodes={fill=nearwhite,inner sep=6pt}}]"
    "\n" r"A \rar & B"
    "\n" r"\end{tikzcd}"
)


def test_dark_cell_card_detected_as_page_plate():
    plate = detect_dark_plate(_DARK_CELLS)
    assert plate is not None, "dark tikz-cd cell card should extend the page"
    assert _rel_luminance(plate) < 0.18, plate


def test_near_white_cell_card_keeps_the_white_page():
    # A light-cell card must NOT darken the page (byte-identical light render).
    assert detect_dark_plate(_NEARWHITE_CELLS) is None


# --------------------------------------------------------------------------
# D-351  circuitikz path ink that tints a component label clears the text floor
# --------------------------------------------------------------------------

def test_circuitikz_path_ink_tinting_label_clears_text_floor():
    body = r"\draw (0,0) to[R=$R_1$, color=green!45!black] (3,0);"
    out, applied = normalize_colors(body, theme="dark")
    surf = _THEME_SURFACE_RGB["dark"]
    inks = _rgbs(out)
    assert inks, ("expected a lifted ink", out)
    # The lifted path ink also paints the R=$R_1$ label, so it must clear the
    # 4.5:1 TEXT floor (pre-fix it was lifted only to the 3:1 graphical floor).
    assert max(_contrast_ratio(rgb, surf) for rgb in inks) >= _TEXT_CONTRAST_FLOOR - 0.1, out
    assert any("text floor" in a for a in applied), applied


def test_bare_page_node_text_ink_is_clamped_but_fill_paired_text_is_not():
    # A node with NO fill draws its label on the page -> text= is clamped.
    no_fill = r"\node[text=DarkGreen] at (0,0) {label};"
    out, _ = normalize_colors(no_fill, theme="dark")
    assert "text=DarkGreen" not in out
    surf = _THEME_SURFACE_RGB["dark"]
    assert all(_contrast_ratio(rgb, surf) >= _TEXT_CONTRAST_FLOOR - 0.1
               for rgb in _rgbs(out)), out

    # A LEGIBLE text= chosen for a chip FILL is left to the fill-aware helpers,
    # untouched by the step-8 page clamp (white on Navy = 16:1).  (An EXPLICIT
    # but egregiously illegible fill-paired ink -- black on Navy = 1.31:1 --
    # IS corrected, but by the separate D-033 fill-paired-ink guard, not this
    # page clamp; see test_latex_d033_fill_paired_ink.)
    filled = r"\node[fill=Navy, text=white] at (0,0) {chip};"
    for theme in ("light", "dark"):
        out2, _ = normalize_colors(filled, theme=theme)
        assert "text=white" in out2, (theme, out2)
