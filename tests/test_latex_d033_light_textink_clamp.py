r"""
Regression test for D-033: light-theme TEXT-INK contrast clamp.

Backstory
---------
The G-03 theme contrast clamp (``latex_color._clamp_body_colours``) was scoped
to the DARK surface only: a near-black author ink on the dark page was lifted
toward white, but the LIGHT page was left entirely untouched on the (correct)
grounds that a page-relative clamp of STROKE colours cannot tell an illegible
pale stroke from an intentional mid-tone decorative one without render
verification (test_latex_g04 even asserts a chemfig positional bond NAME such
as ``,Navy]`` survives verbatim in light).

That stroke argument does NOT extend to TEXT INK.  A ``\color`` / ``\textcolor``
ARGUMENT is a label colour whose only backdrop is the page, and an illegible
label is a bug in either theme -- there is no "intentional 2:1 body text".
chemfig-w1-11's ``\color{DarkOrange}{OH}`` sits at 2.19:1 on the white page
(below the 4.5:1 small-text floor) and the dark-only clamp left it there, so the
spec kept failing the light leg every sweep.

Fix
---
The text-ink macro clamp now runs in BOTH themes against the 4.5:1 text floor,
lifting a sub-floor label toward the surface-opposite endpoint (black on the
light page, white on the dark page).  It stays CONTRAST-GATED, so a label
already legible on the active surface is byte-for-byte untouched, and it only
touches ``\color``/``\textcolor`` ARGUMENTS -- never stroke/fill options or the
chemfig positional bond field the light path must preserve.

Every assertion below FAILS against the pre-fix (dark-only) clamp.
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
TEXT_FLOOR = 4.5

# chemfig-w1-11: per-bond colours in the positional 5th field plus \color atom
# labels.  DarkOrange as a label ink is the sub-floor case on the white page.
W1_11 = (r"\chemfig{\color{Crimson}{H_3C}-[,,,,DarkGreen]C(=[:60,,,,Navy]O)"
         r"-[:-60,,,,Teal]\color{DarkOrange}{OH}}")

_COLOR_EXPR = re.compile(r"\\(?:text)?color\{(rgb,255:red,\d+;green,\d+;blue,\d+)\}")


def _inks(text: str):
    return [_resolve_xcolor_rgb("{" + m.group(1) + "}")
            for m in _COLOR_EXPR.finditer(text)]


def test_darkorange_label_is_lifted_on_the_light_page():
    """DarkOrange text ink (2.19:1 on white) is lifted to >= 4.5:1 in light."""
    # Precondition: DarkOrange really is below the text floor on white.
    assert _contrast_ratio((0xFF, 0x8C, 0x00), WHITE) < TEXT_FLOOR

    light, applied = normalize_colors(W1_11, theme="light")
    assert "DarkOrange" not in light, "DarkOrange label ink was left unclamped in light"
    # Every emitted label ink clears the text floor on the white page.
    inks = _inks(light)
    assert inks, "no clamped label ink emitted in light"
    for rgb in inks:
        assert _contrast_ratio(rgb, WHITE) >= TEXT_FLOOR, (rgb, _contrast_ratio(rgb, WHITE))
    assert applied


def test_light_clamp_does_not_touch_positional_bond_names():
    """The light text clamp must not rewrite the chemfig positional bond field.

    (test_latex_g04 contract: a positional bond NAME stays as authored in light.)
    """
    light, _ = normalize_colors(W1_11, theme="light")
    assert ",Navy]" in light and ",DarkGreen]" in light and ",Teal]" in light


def test_already_legible_light_label_is_byte_identical():
    """A label already >= 4.5:1 on white (Navy = 16:1) is left untouched."""
    body = r"\chemfig{\color{Navy}{H_3C}-C}"
    light, _ = normalize_colors(body, theme="light")
    # Byte-identical is the real guard: the contrast clamp must not rewrite a
    # label already legible on white (the syntax pass may log a no-op entry).
    assert light == body


def test_darkorange_label_still_legible_on_dark():
    """Both-theme discipline: DarkOrange is >= 4.5:1 on the dark page already
    (6.7:1), so the dark leg keeps it -- the fix does not regress dark."""
    dark, _ = normalize_colors(W1_11, theme="dark")
    for rgb in _inks(dark):
        assert _contrast_ratio(rgb, DARK) >= TEXT_FLOOR
