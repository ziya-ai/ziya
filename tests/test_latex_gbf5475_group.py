"""Group G-bf5475 (key: app/services/latex_profiles.py) — iteration 29.

Covers the defects fixed under this group.  Each assertion is written to FAIL
on the pre-fix source and pass on the post-fix source:

- D-466  _wrap must wrap a matrix body that only NESTS a drawing environment in
         a cell (tikz-cd-w2-06) instead of treating it as already-wrapped.
- D-465  tikz-cd dark must remap the library ``background color`` (equal-sign
         gap / crossing mask / label fill) to the page colour.
- D-350  circuitikz dark must remap the open-terminal pole fill to the page.
- D-330  chemfig dark must give the tikzpicture a theme-ink default so Lewis
         lone-pair dots (which do not inherit \\color) are visible.
- D-238  a categorical \\foreach colour palette must be re-tinted toward the
         surface-opposite endpoint so every series clears the contrast floor,
         in BOTH themes, without changing the loop's item count.
- D-358  an uncoloured label sitting on a DARK author plate must get an on-plate
         default ink on the light page (and be untouched on the dark page).
"""
import re

from app.services.latex_profiles import PROFILES
from app.utils.latex_color import (
    normalize_colors,
    _resolve_xcolor_rgb,
    _contrast_ratio,
    _THEME_SURFACE_RGB,
    _CONTRAST_FLOOR,
)

W2_06 = (r"L_{3} \arrow[r] & \begin{tikzcd}[ampersand replacement=\&, sep=tiny] "
         r"a \arrow[r] \& b \end{tikzcd}")

# 24-series categorical palette (tikz-w2-11): saturated primaries + !60 tints.
PALETTE_BODY = (
    r"\foreach \c [count=\i from 0] in "
    r"{red,blue,green,orange,violet,brown,cyan,magenta,teal,olive,purple,gray,"
    r"red!60,blue!60,green!60,orange!60,violet!60,brown!60,cyan!60,magenta!60,"
    r"teal!60,olive!60,purple!60,gray!60}{\draw[\c] (0,\i) -- (1,\i);}"
)

PLATE_BODY = (
    "\\definecolor{plate}{HTML}{16324A}\n"
    "\\fill[plate] (-0.9,-1.5) rectangle (7.3,1.9);\n"
    "\\node at (2.4,1.4) {label};"
)


# --------------------------------------------------------------------------
# D-466 — nested-matrix wrap
# --------------------------------------------------------------------------
def test_d466_nested_matrix_body_is_wrapped():
    wrapped = PROFILES["tikz-cd"]._wrap(W2_06)
    assert wrapped.startswith(r"\begin{tikzcd}")
    assert wrapped.rstrip().endswith(r"\end{tikzcd}")
    # the original outer row is now inside the wrap, not bare text
    assert wrapped.count(r"\begin{tikzcd}") == 2  # our outer + the nested one


def test_d466_genuinely_prewrapped_body_passes_through():
    body = r"\begin{tikzcd} A \arrow[r] & B \end{tikzcd}"
    assert PROFILES["tikz-cd"]._wrap(body) == body


def test_d466_definecolor_leading_body_still_passes_through():
    body = "\\definecolor{x}{HTML}{112233}\n\\begin{tikzcd} A & B \\end{tikzcd}"
    assert PROFILES["tikz-cd"]._wrap(body) == body


# --------------------------------------------------------------------------
# D-465 / D-350 / D-330 — per-engine dark remaps, light byte-identical
# --------------------------------------------------------------------------
def test_d465_tikzcd_background_color_dark_only():
    dark = PROFILES["tikz-cd"].build_document("A \\arrow[r] & B", standalone=True,
                                              fmt="png", theme="dark")
    light = PROFILES["tikz-cd"].build_document("A \\arrow[r] & B", standalone=True,
                                               fmt="png", theme="light")
    assert "\\tikzcdset{background color=ziyathemepage}" in dark
    assert "background color" not in light


def test_d350_circuitikz_open_poles_fill_dark_only():
    dark = PROFILES["circuitikz"].build_document("\\draw (0,0) to[R] (2,0);",
                                                 standalone=True, fmt="png", theme="dark")
    light = PROFILES["circuitikz"].build_document("\\draw (0,0) to[R] (2,0);",
                                                  standalone=True, fmt="png", theme="light")
    assert "\\ctikzset{open poles fill=ziyathemepage}" in dark
    assert "open poles fill" not in light


def test_d330_chemfig_lewis_ink_dark_only():
    dark = PROFILES["chemfig"].build_document("\\chemfig{\\lewis{2:6:,O}}",
                                              standalone=True, fmt="png", theme="dark")
    light = PROFILES["chemfig"].build_document("\\chemfig{\\lewis{2:6:,O}}",
                                               standalone=True, fmt="png", theme="light")
    assert "every picture/.append style={color=ziyathemeink}" in dark
    assert "ziyathemeink" not in light


def test_dark_remaps_absent_on_svg_path():
    sv = PROFILES["tikz-cd"].build_document("A & B", standalone=True, fmt="svg",
                                            theme="dark")
    assert "tikzcdset" not in sv and "ziyathemepage" not in sv


# --------------------------------------------------------------------------
# D-238 — categorical foreach palette clamp (both themes)
# --------------------------------------------------------------------------
def _foreach_items(body):
    return re.search(r"in\s*\{([^{}]*)\}", body).group(1).split(",")


def _all_items_clear_floor(body, theme):
    surf = _THEME_SURFACE_RGB[theme]
    for it in _foreach_items(body):
        rgb = _resolve_xcolor_rgb(it.strip())
        assert rgb is not None, it
        if _contrast_ratio(rgb, surf) < _CONTRAST_FLOOR:
            return False
    return True


def test_d238_palette_illegible_before_and_legible_after():
    for theme in ("light", "dark"):
        # sanity: the raw palette DOES contain below-floor series (pre-fix state)
        assert not _all_items_clear_floor(PALETTE_BODY, theme)
        new, applied = normalize_colors(PALETTE_BODY, theme)
        assert _all_items_clear_floor(new, theme)
        # item count preserved (no injected comma broke the loop)
        assert len(_foreach_items(new)) == len(_foreach_items(PALETTE_BODY))
        assert any("foreach palette" in a for a in applied)


def test_d238_numeric_foreach_untouched():
    body = r"\foreach \x in {0,1,2,3} {\draw (\x,0) circle (2pt);}"
    new, _ = normalize_colors(body, "light")
    assert "{0,1,2,3}" in new


def test_d238_light_option_list_navy_untouched():
    # a bare option-list colour (not a foreach) must still pass through in light
    body = r"\draw[draw=Navy] (0,0)--(1,1);"
    new, _ = normalize_colors(body, "light")
    assert "Navy" in new


# --------------------------------------------------------------------------
# D-358 — on-plate default ink for an uncoloured label on a dark plate
# --------------------------------------------------------------------------
def _injected_default_ink(body, theme):
    new, applied = normalize_colors(body, theme)
    m = re.search(r"\\fill\[plate\][^;]*;\s*\\color\{([^}]*)\}", new)
    return m.group(1) if m else None


def test_d358_dark_plate_gets_on_plate_ink_in_light():
    expr = _injected_default_ink(PLATE_BODY, "light")
    assert expr is not None, "no on-plate default ink injected in light"
    rgb = _resolve_xcolor_rgb(expr)
    plate = (0x16, 0x32, 0x4A)
    # the injected ink must be legible on the plate (text floor)
    assert _contrast_ratio(rgb, plate) >= 4.5


def test_d358_dark_theme_untouched_on_dark_plate():
    # #EDEDED baked dark ink already clears the dark plate -> no injection
    assert _injected_default_ink(PLATE_BODY, "dark") is None
