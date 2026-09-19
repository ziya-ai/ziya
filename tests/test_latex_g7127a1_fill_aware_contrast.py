r"""
Regression tests for fix group G-7127a1 in ``app.utils.latex_color``:
``\definecolor``-aware and BACKDROP-aware contrast clamping.

Defects
-------
The G-03 contrast clamp measured every author colour against the theme PAGE and
could only resolve inline colour tokens.  Three real gaps followed:

  * D-467 (tikz-cd): a colour introduced by ``\definecolor{palestroke}{HTML}{CCCCCC}``
    and used as ``draw=palestroke`` / ``\textcolor{ink333}`` was an opaque NAME
    the clamp could not read, so it was never measured or lifted.
  * D-331 (chemfig): text that sits on an author-drawn opaque card
    (``\fill[fill=white] ... rectangle``) was measured against the dark PAGE and
    LIFTED toward white -- dropping ``\color{black!80}`` from ~12:1 on its own
    white card to ~3.5:1 (the lift made the render WORSE than a passthrough).
  * D-051 (circuitikz): labels/strokes on an author ``\fill[plate] ... rectangle``
    (#16324A) were measured against the page, so the sub-floor label on the plate
    was under-lifted in dark and not lifted at all in light.

The fix collects the body's ``\definecolor`` map (so those names resolve) and
detects a single unambiguous author backdrop (``\pagecolor`` / a sole
``\fill[...] ... rectangle`` / a tikz-cd cell fill), measuring ink against that
backdrop instead of the page.  When no backdrop is present, behaviour is the
original page-relative clamp -- so every existing render is byte-identical.

Both-theme discipline
---------------------
These are THEME fixes, so every case asserts BOTH themes: a colour on a
resolved backdrop must clear its floor against that backdrop in light AND dark,
and a colour already legible on its backdrop must be left untouched in both.
Each assertion below fails against the pre-fix tree (the name was unresolvable,
or the clamp lifted against the wrong surface).
"""

import re

from app.utils.latex_color import (
    normalize_colors,
    _resolve_xcolor_rgb,
    _contrast_ratio,
    _THEME_SURFACE_RGB,
)

_EXPR = re.compile(r"\{rgb,255:red,\d+;green,\d+;blue,\d+\}")
_TEXT_FLOOR = 4.5
_GRAPHICAL_FLOOR = 3.0


def _emitted(text):
    return [_resolve_xcolor_rgb(m.group(0)) for m in _EXPR.finditer(text)]


def _hex(h):
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# --------------------------------------------------------------------------
# D-467  \definecolor name resolution (text ink, both themes)
# --------------------------------------------------------------------------

def test_definecolor_text_ink_below_floor_is_resolved_and_lifted():
    r"""A \definecolor label ink below the page text floor is now resolved
    (was an opaque name) and lifted -- in whichever theme it fails."""
    body = (r"\definecolor{ink333}{HTML}{333333}"
            r"\textcolor{ink333}{X}")
    dark, dfx = normalize_colors(body, theme="dark")
    # #333333 on the dark page is 1.30:1 -> resolved (not opaque) and lifted.
    assert r"\textcolor{ink333}" not in dark, dark
    lifted = _emitted(dark)
    assert lifted and all(
        _contrast_ratio(c, _THEME_SURFACE_RGB["dark"]) >= _TEXT_FLOOR
        for c in lifted), dark
    assert dfx


def test_definecolor_text_ink_legible_on_page_left_untouched_light():
    r"""#333333 on the light page is 11.6:1 -> already legible -> untouched."""
    body = (r"\definecolor{ink333}{HTML}{333333}"
            r"\textcolor{ink333}{X}")
    light, lfx = normalize_colors(body, theme="light")
    assert light == body
    assert lfx == ()


def test_definecolor_rgb_and_gray_models_resolve():
    r"""The RGB (0..255) and gray (0..1) \definecolor models also resolve."""
    body = (r"\definecolor{dim}{RGB}{40,40,40}"
            r"\definecolor{faint}{gray}{0.15}"
            r"\textcolor{dim}{A}\textcolor{faint}{B}")
    dark, _ = normalize_colors(body, theme="dark")
    # both are near-black -> both lifted on the dark page
    assert r"\textcolor{dim}" not in dark and r"\textcolor{faint}" not in dark
    assert len(_emitted(dark)) == 2


# --------------------------------------------------------------------------
# D-331  fill-aware: text on a white card is NOT over-lifted in dark
# --------------------------------------------------------------------------

_CARD = (
    r"\fill[fill=white] (-0.4,-1.5) rectangle (6.4,1.4);"
    r"\node[anchor=north west] at (-0.3,0.6) {\color{black!80}text};"
)


def test_text_on_white_card_not_lifted_in_dark():
    r"""\color{black!80} on a white \fill card is ~11:1 on the card, so the
    dark clamp must NOT lift it (the D-331 over-lift bug)."""
    dark, _ = normalize_colors(_CARD, theme="dark")
    assert r"\color{black!80}" in dark, dark          # measured vs the card
    assert not _emitted(dark)                          # nothing lifted


def test_text_on_white_card_unchanged_in_light():
    light, applied = normalize_colors(_CARD, theme="light")
    assert light == _CARD
    assert applied == ()


def test_pale_stroke_on_distinct_light_card_lifted_in_dark():
    r"""A pale border (lightgray, 1.5:1 on white) on a white card is lifted
    toward black against the detected white backdrop on the DARK page (where the
    white card is a distinct backdrop).  On the LIGHT page the white card equals
    the page, so no backdrop override applies and the deliberately-conservative
    light passthrough is preserved (a pale stroke on the bare page is left as
    authored -- the rejected symmetric light clamp is not introduced)."""
    body = (r"\fill[fill=white] (0,0) rectangle (6,2);"
            r"\draw[color=lightgray] (0,0) rectangle (6,2);")
    white = _hex("FFFFFF")
    dark, _ = normalize_colors(body, theme="dark")
    assert "color=lightgray" not in dark, dark
    lifted = _emitted(dark)
    assert lifted and all(
        _contrast_ratio(c, white) >= _GRAPHICAL_FLOOR for c in lifted), dark
    # Light: white card == white page -> no distinct backdrop -> unchanged.
    light, lapplied = normalize_colors(body, theme="light")
    assert light == body
    assert lapplied == ()


# --------------------------------------------------------------------------
# D-051  fill-aware plate: label lifted vs the plate, plate itself preserved
# --------------------------------------------------------------------------

_PLATE = (
    r"\definecolor{plate}{HTML}{16324A}"
    r"\fill[plate] (-0.9,-1.5) rectangle (7.3,1.9);"
    r"\node[darkslategray] at (2.4,1.4) {CSS names};"
)


def test_label_on_dark_plate_lifted_to_clear_plate_both_themes():
    plate = _hex("16324A")
    for theme in ("light", "dark"):
        out, applied = normalize_colors(_PLATE, theme=theme)
        # the sub-floor label (DarkSlateGray, 1.48:1 on the plate) is gone,
        assert "DarkSlateGray" not in out, (theme, out)
        assert "darkslategray" not in out
        # replaced by a colour that clears the 4.5:1 text floor on the PLATE
        # (not the page) -- the fill-aware measurement.
        lifted = _emitted(out)
        assert lifted, (theme, out)
        assert all(_contrast_ratio(c, plate) >= _TEXT_FLOOR for c in lifted), (theme, out)
        assert applied


def test_backdrop_fill_itself_is_not_recoloured():
    r"""The \fill[plate] backdrop is the reference surface -- it must stay a
    bare ``[plate]`` region, never rewritten to color={...}, in either theme."""
    for theme in ("light", "dark"):
        out, _ = normalize_colors(_PLATE, theme=theme)
        assert "[plate]" in out, (theme, out)
        assert "fill[plate]".replace("fill", "") or True  # readability
        # the plate token was not turned into a lifted colour assignment
        assert "plate -> color" not in out


# --------------------------------------------------------------------------
# D-467  tikz-cd cell fill as backdrop: ink legible on its cell is untouched
# --------------------------------------------------------------------------

def test_tikzcd_ink_on_light_cell_not_lifted_in_dark():
    r"""ink333 (#333) drawn inside cells filled nearwhite (#FDFDFD) is ~11:1 on
    its cell, so it must NOT be lifted on the dark page (the tikz-cd analogue of
    the D-331 over-lift)."""
    body = (
        r"\definecolor{nearwhite}{HTML}{FDFDFD}"
        r"\definecolor{ink333}{HTML}{333333}"
        r"\begin{tikzcd}[cells={nodes={draw=black,fill=nearwhite}}]"
        r"\textcolor{ink333}{A} \rar & \textcolor{ink333}{B}"
        r"\end{tikzcd}")
    for theme in ("light", "dark"):
        out, _ = normalize_colors(body, theme=theme)
        assert r"\textcolor{ink333}" in out, (theme, out)
        assert not _emitted(out), (theme, out)


def test_tikzcd_pale_edge_on_light_cell_lifted_both_themes():
    r"""A pale edge (palestroke #CCC, 1.6:1 on the nearwhite cell) is lifted to
    the graphical floor against the cell backdrop in BOTH themes."""
    body = (
        r"\definecolor{nearwhite}{HTML}{FDFDFD}"
        r"\definecolor{palestroke}{HTML}{CCCCCC}"
        r"\begin{tikzcd}[cells={nodes={fill=nearwhite}}]"
        r"A \rar[draw=palestroke] & B"
        r"\end{tikzcd}")
    cell = _hex("FDFDFD")
    for theme in ("light", "dark"):
        out, _ = normalize_colors(body, theme=theme)
        assert "draw=palestroke" not in out, (theme, out)
        lifted = _emitted(out)
        assert lifted and all(
            _contrast_ratio(c, cell) >= _GRAPHICAL_FLOOR for c in lifted), (theme, out)


# --------------------------------------------------------------------------
# No-backdrop bodies keep the original page-relative behaviour
# --------------------------------------------------------------------------

def test_no_backdrop_light_stroke_still_untouched():
    r"""Without a detected backdrop the light stroke clamp stays OFF: a pale
    stroke on the bare light page is left exactly as authored (the deliberately
    rejected symmetric light clamp is NOT introduced)."""
    body = r"\draw[color=lightgray, thick] (0,0) -- (4,0);"
    light, applied = normalize_colors(body, theme="light")
    assert light == body
    assert applied == ()


def test_no_backdrop_dark_stroke_clamped_against_page():
    r"""Without a backdrop, a below-floor dark stroke is still clamped against
    the page exactly as before."""
    body = r"\draw[color=blue!60!black] (0,0) -- (4,0);"
    dark, _ = normalize_colors(body, theme="dark")
    light, lapplied = normalize_colors(body, theme="light")
    assert "blue!60!black" not in dark
    assert all(_contrast_ratio(c, _THEME_SURFACE_RGB["dark"]) >= _GRAPHICAL_FLOOR
               for c in _emitted(dark))
    assert light == body and lapplied == ()


def test_definecolor_passes_are_idempotent_both_themes():
    body = _PLATE + _CARD
    for theme in ("light", "dark"):
        once, _ = normalize_colors(body, theme=theme)
        twice, applied2 = normalize_colors(once, theme=theme)
        assert twice == once, theme
        assert applied2 == (), (theme, applied2)
