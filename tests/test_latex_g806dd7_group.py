"""Regression tests for gfx fix group G-806dd7 (latex_profiles / latex_color).

Covers two defects, each asserted in BOTH themes:

* D-358 (circuitikz-w4-07): a foreground THEME TOKEN (``currentColor`` /
  ``var(--ziya-text)`` / ``theme.foreground``) on a body with a detected dark
  author plate must resolve to the crisp plate ink (#EDEDED = 11.29:1 on the
  #16324A plate), not the nominal white-page black that the step-8 clamp could
  only lift to a muddy ~3:1 grey.

* D-353 (circuitikz-w3-01 solid ``\\fill``, circuitikz-w3-04 pale ``\\shade``):
  a DARK-theme body whose author drew a self-contained LIGHT card must be
  repainted to a white page with black ink (the mirror of the dark-plate
  treatment), so uncoloured labels and the light palette stay legible instead
  of collapsing under the baked dark page.

Both directions are pinned: the dark theme's token resolution and a plain dark
body's page bake must stay unchanged (byte-identical), so the light fix never
regresses the other theme.
"""
import re

from app.utils.latex_color import (
    normalize_colors,
    detect_light_plate,
    detect_dark_plate,
    _contrast_ratio,
)
from app.services.latex_profiles import get_profile

PLATE_RGB = (0x16, 0x32, 0x4A)
DARK_PAGE = (0x1F, 0x1F, 0x1F)
PLATE_INK = (0xED, 0xED, 0xED)

W407 = (
    "\\definecolor{plate}{HTML}{16324A}\n"
    "\\definecolor{plateink}{HTML}{F2F6FA}\n"
    "\\definecolor{plateaccent}{HTML}{5FD4E4}\n"
    "\\fill[plate] (-0.9,-1.5) rectangle (7.3,1.9);\n"
    "\\draw[color=var(--ziya-text)] (0,0) to[R=$R_1$] (2.4,0) node[ocirc]{};\n"
    "\\draw[color=theme.foreground] (0,-1.1) to[short] (2.4,-1.1);\n"
    "\\node[text=currentColor] at (2.4,1.4) {Theme tokens};"
)

W301 = (
    "\\definecolor{ink}{HTML}{333333}\n"
    "\\definecolor{palegrey}{HTML}{D8DCE0}\n"
    "\\fill[white] (-1,-1) rectangle (10,4.6);\n"
    "\\begin{scope}[color=palegrey, text=ink]\n"
    "\\draw[palegrey, thick] (0,3) to[V=$V_{in}$] (0,0);\n"
    "\\draw[palegrey, thick] (0,3) to[R=$R_1$] (3,3) to[R=$R_2$] (6,3);\n"
    "\\node[text=ink] at (4,4.2) {hardcoded light palette};\n"
    "\\end{scope}"
)

W304 = (
    "\\shade[left color=blue!60, right color=white] (-0.5,2.4) rectangle (9,3.4);\n"
    "\\shade[top color=gray!10, bottom color=gray!60] (-0.5,-1.4) rectangle (9,-0.4);\n"
    "\\draw[thick] (0,1.4) to[R=$R_1$] (3,1.4) to[L=$L_1$] (6,1.4) to[C=$C_1$] (8.5,1.4);\n"
    "\\node at (4.2,3.0) {axis shading, blue!60 to white};\n"
    "\\node at (4.2,-0.9) {axis shading, gray!10 to gray!60};"
)

# A genuine DARK card (D-357): a sole dark \fill plate -- must NOT be seen as a
# light card, and must still get the dark-plate page treatment on the light page.
W405 = (
    "\\definecolor{plate}{HTML}{16324A}\n"
    "\\fill[plate] (-0.9,-1.5) rectangle (7.3,1.9);\n"
    "\\draw[color=rgb(95,212,228)] (0,0) to[short] (0,-1.1) node[ground]{};\n"
    "\\node at (2.4,1.4) {rgba colours};"
)

_RGB_TRIPLE = re.compile(
    r"rgb,255:red,(\d+);green,(\d+);blue,(\d+)")


def _resolved_colours(body: str):
    return [tuple(int(x) for x in m.groups())
            for m in _RGB_TRIPLE.finditer(body)]


def _dark_doc(body: str) -> str:
    prof = get_profile("circuitikz")
    norm, _ = normalize_colors(body, "dark")
    return prof.build_document(norm, standalone=True, fmt="png", theme="dark")


def _light_doc(body: str) -> str:
    prof = get_profile("circuitikz")
    norm, _ = normalize_colors(body, "light")
    return prof.build_document(norm, standalone=True, fmt="png", theme="light")


# --------------------------------------------------------------------------
# D-358: theme tokens resolve against the detected plate surface.
# --------------------------------------------------------------------------

def test_d358_theme_token_crisp_on_dark_plate_light():
    """LIGHT theme: every theme-token-derived ink clears a strong floor on the
    plate (fails before the fix, which only reached a ~3:1 grey)."""
    out, _ = normalize_colors(W407, "light")
    colours = _resolved_colours(out)
    assert colours, "expected resolved rgb colours"
    # The three theme-token inks must be the crisp plate ink, comfortably above
    # the 4.5:1 text floor on the #16324A plate.
    assert PLATE_INK in colours
    token_inks = [c for c in colours if c == PLATE_INK]
    assert len(token_inks) >= 3
    for rgb in token_inks:
        assert _contrast_ratio(rgb, PLATE_RGB) >= 7.0


def test_d358_theme_token_dark_theme_unchanged():
    """DARK theme is byte-identical: tokens still resolve to #EDEDED."""
    out, _ = normalize_colors(W407, "dark")
    for rgb in _resolved_colours(out):
        assert rgb == PLATE_INK


def test_d358_plain_light_body_unchanged():
    """No plate -> a light-theme foreground token is still #000000."""
    plain = r"\node[color=currentColor] at (0,0) {x};"
    out, _ = normalize_colors(plain, "light")
    assert (0, 0, 0) in _resolved_colours(out)


# --------------------------------------------------------------------------
# D-353: a dark-theme light card is repainted to a white page with black ink.
# --------------------------------------------------------------------------

def test_d353_detect_light_plate():
    assert detect_light_plate(W301) == (255, 255, 255)
    assert detect_light_plate(W304) == (255, 255, 255)
    # A dark card is NOT a light card.
    assert detect_light_plate(W405) is None
    # A plain body (no author background) is not a light card either.
    assert detect_light_plate(r"\draw (0,0) -- (1,1);") is None


def test_d353_light_card_repaints_dark_page_white():
    """DARK theme: fails before the fix, which baked #1F1F1F / #EDEDED."""
    for body in (W301, W304):
        doc = _dark_doc(body)
        assert "\\pagecolor[HTML]{FFFFFF}" in doc
        assert "\\color[HTML]{000000}" in doc
        assert "\\pagecolor[HTML]{1F1F1F}" not in doc


def test_d353_plain_dark_body_keeps_dark_page():
    """No light card -> the dark page bake is unchanged (byte-identical)."""
    doc = _dark_doc(r"\draw (0,0) -- (1,1);")
    assert "\\pagecolor[HTML]{1F1F1F}" in doc
    assert "\\color[HTML]{EDEDED}" in doc


def test_d353_dark_card_still_gets_dark_plate_on_light_page():
    """The mirror stays intact: a dark card on the LIGHT page is repainted to
    its plate (D-357), so this fix does not disturb detect_dark_plate."""
    assert detect_dark_plate(W405) == PLATE_RGB
    doc = _light_doc(W405)
    assert "\\pagecolor[RGB]{22,50,74}" in doc


def test_d353_light_theme_light_card_stays_white():
    """LIGHT theme render of a light card is the plain white page (unchanged)."""
    for body in (W301, W304):
        doc = _light_doc(body)
        assert "\\pagecolor[HTML]{FFFFFF}" in doc
        assert "\\color[HTML]{000000}" in doc
