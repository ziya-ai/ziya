"""Group G-eccfe9: latex_profiles / latex_color surface + pattern fixes.

Covers three defects that share ``app/services/latex_profiles.py`` /
``app/utils/latex_color.py``:

* D-494 -- a pgf ``patterns`` fill tile draws in the pattern's own default
  colour (black); on the baked #1F1F1F dark page it is 1.27:1 (invisible).
  build_document must default the pattern colour to the theme ink on the dark
  path, and MUST NOT reference the ``pattern color`` key for a profile that
  never loads the ``patterns`` library, and MUST leave the light render alone.

* D-357 -- a model emits a self-contained "card" whose background is a SOLE
  author DARK ``\\fill[...] rectangle`` with light ink on top, but the plate
  does not cover the full bbox, so off-plate leads land on the white light
  page and vanish (#5FD4E4 on #FFFFFF = 1.75:1).  On the LIGHT page the surface
  must be matched to the detected plate; the dark render is untouched, and a
  body with a LIGHT plate (or no plate) keeps the plain white page.

* D-353 -- a hardcoded LIGHT palette (white plate, pale strokes) must stay
  legible on the dark page: the backdrop-aware clamp lifts the pale strokes
  against the detected white plate, and leaves the light render byte-identical.

Each assertion is written so it FAILS against the pre-fix behaviour (no
``pattern color`` default, plain white light page, unclamped pale strokes) and
passes with the fix.
"""
from __future__ import annotations

from app.services.latex_profiles import get_profile
from app.utils.latex_color import (
    detect_dark_plate,
    normalize_colors,
    _contrast_ratio,
    _resolve_xcolor_rgb,
)

_FLOOR = 3.0


# --------------------------------------------------------------------------
# D-494: pattern colour default on the dark page.
# --------------------------------------------------------------------------
_PATTERN_BODY = (
    r"\draw[pattern=crosshatch,draw=black,thick] (0,0) rectangle (1.3,1.3);"
)


def test_d494_pattern_colour_defaulted_on_dark():
    prof = get_profile("tikz")
    dark = prof.build_document(_PATTERN_BODY, standalone=True, fmt="png", theme="dark")
    assert "pattern color=ziyathemeink" in dark, (
        "dark page must default the pgf pattern colour to the theme ink so the "
        "tiles are not black-on-#1F1F1F")


def test_d494_pattern_colour_absent_on_light():
    prof = get_profile("tikz")
    light = prof.build_document(_PATTERN_BODY, standalone=True, fmt="png", theme="light")
    assert "pattern color" not in light, (
        "light render must be byte-identical to before -- no pattern remap")


def test_d494_pattern_colour_only_when_library_loaded():
    # circuitikz does NOT load the patterns library, so referencing the
    # ``pattern color`` key there would be an undefined-key abort.
    prof = get_profile("circuitikz")
    dark = prof.build_document(
        r"\draw (0,0) to[R=$R$] (2,0);", standalone=True, fmt="png", theme="dark")
    assert "pattern color" not in dark


def test_d494_pattern_colour_ink_clears_floor_on_dark():
    ink = _resolve_xcolor_rgb("rgb,255:red,237;green,237;blue,237")  # #EDEDED
    page = (0x1F, 0x1F, 0x1F)
    assert _contrast_ratio(ink, page) >= _FLOOR  # 14.08:1


# --------------------------------------------------------------------------
# D-357: dark author plate becomes the light-page surface.
# --------------------------------------------------------------------------
_PLATE_BODY = (
    "\\definecolor{plate}{HTML}{16324A}\n"
    "\\definecolor{plateink}{HTML}{F2F6FA}\n"
    "\\definecolor{plateaccent}{HTML}{5FD4E4}\n"
    "\\fill[plate] (-0.9,-1.5) rectangle (7.3,1.9);\n"
    "\\draw[color=plateink] (0,0) to[R=$R$] (2.6,0) node[ocirc]{};\n"
    "\\draw[color=plateaccent] (0,0) to[R] (0,-1.6) node[ground]{};\n"
)


def test_d357_detect_dark_plate():
    rgb = detect_dark_plate(_PLATE_BODY)
    assert rgb == (0x16, 0x32, 0x4A)


def test_d357_light_page_matches_plate():
    prof = get_profile("circuitikz")
    light = prof.build_document(_PLATE_BODY, standalone=True, fmt="png", theme="light")
    # The white default page would leave the off-plate accent lead at 1.75:1;
    # the plate surface lifts it well above the floor.
    assert "\\pagecolor[RGB]{22,50,74}" in light
    assert "\\pagecolor[HTML]{FFFFFF}" not in light
    acc = _resolve_xcolor_rgb("rgb,255:red,95;green,212;blue,228")  # #5FD4E4
    assert _contrast_ratio(acc, (0x16, 0x32, 0x4A)) >= _FLOOR  # 7.57:1
    assert _contrast_ratio(acc, (0xFF, 0xFF, 0xFF)) < _FLOOR    # 1.75:1 on white


def test_d357_dark_page_unchanged_by_plate():
    prof = get_profile("circuitikz")
    dark = prof.build_document(_PLATE_BODY, standalone=True, fmt="png", theme="dark")
    assert "\\pagecolor[HTML]{1F1F1F}" in dark
    assert "\\pagecolor[RGB]{22,50,74}" not in dark


def test_d357_light_plate_does_not_trigger():
    # A LIGHT plate (the D-353 white-card case) must NOT flip the page: the
    # figure lives on a white surface, and the page stays white.
    light_plate = (
        "\\definecolor{palegrey}{HTML}{D8DCE0}\n"
        "\\fill[white] (-1,-1) rectangle (10,4.6);\n"
        "\\draw[palegrey] (0,0) -- (2,0);\n"
    )
    assert detect_dark_plate(light_plate) is None
    prof = get_profile("circuitikz")
    light = prof.build_document(light_plate, standalone=True, fmt="png", theme="light")
    assert "\\pagecolor[HTML]{FFFFFF}" in light


def test_d357_no_plate_keeps_white_page():
    plain = r"\draw (0,0) to[R=$R$] (2,0);"
    assert detect_dark_plate(plain) is None
    prof = get_profile("circuitikz")
    light = prof.build_document(plain, standalone=True, fmt="png", theme="light")
    assert "\\pagecolor[HTML]{FFFFFF}" in light


# --------------------------------------------------------------------------
# D-353: hardcoded LIGHT palette clamped against the white plate on dark.
# --------------------------------------------------------------------------
_LIGHT_PALETTE_BODY = (
    "\\definecolor{ink}{HTML}{333333}\n"
    "\\definecolor{palegrey}{HTML}{D8DCE0}\n"
    "\\fill[white] (-1,-1) rectangle (10,4.6);\n"
    "\\draw[palegrey, thick] (0,3) -- (6,3);\n"
    "\\node[ground, palegrey] at (3,0) {};\n"
)


def test_d353_dark_lifts_pale_strokes_against_white_plate():
    out, applied = normalize_colors(_LIGHT_PALETTE_BODY, "dark")
    # The pale strokes must be lifted against the DETECTED white plate (not the
    # dark page), so every clamp note names the backdrop and the palegrey
    # #D8DCE0 (1.38:1 on white) is rewritten.
    assert any("backdrop" in a for a in applied), applied
    assert "palegrey" not in out.split("\\node")[0] or "color={rgb" in out
    # the lifted strokes clear the graphical floor on the white plate
    lifted = _resolve_xcolor_rgb("rgb,255:red,143;green,145;blue,148")
    assert _contrast_ratio(lifted, (0xFF, 0xFF, 0xFF)) >= _FLOOR


def test_d353_light_render_untouched():
    out, applied = normalize_colors(_LIGHT_PALETTE_BODY, "light")
    assert applied == ()
    assert out == _LIGHT_PALETTE_BODY
