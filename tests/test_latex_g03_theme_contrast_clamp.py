r"""
Regression test for D-003 (fix group G-03): theme contrast clamp of literal
author stroke/ink colours to the baked per-theme surface.

Backstory
---------
``build_document`` bakes only the DEFAULT ink/page (dark #1F1F1F + #EDEDED,
light #FFFFFF + #000000).  An author-EXPLICIT stroke/ink colour is passed
through untouched, so a colour chosen for a white page vanishes on the dark
page: ``blue!60!black`` (#000099) sits at 1.15:1 on #1F1F1F, ``Navy`` at
1.03:1, a bare ``black`` stroke at 1.27:1 -- the primary curve/arrow/label is
lost while the themed default-ink scaffolding survives (tikz-w1-02, w1-07,
w1-09, w2-06; circuitikz-w1-14; tikz-cd-w2-14; chemfig-w1-11).

``latex_color.normalize_colors`` now RESOLVES each author colour in a
stroke/ink context to RGB, measures its WCAG contrast against the surface the
renderer was given, and -- only when it is below the 3:1 graphical floor --
blends it toward the surface-opposite endpoint by the minimal amount that
reaches the floor, emitting an xcolor ``{rgb,255:...}`` expression.

Direction & both-theme discipline
----------------------------------
This is a THEME fix, verified in BOTH themes on every case:
  * the DARK assertion checks the previously-invisible colour is now lifted to
    >= 3:1 against #1F1F1F (the broken theme is now correct);
  * the LIGHT assertion checks the SAME author colour is left byte-for-byte
    unchanged, because it was already well above the floor on white (the other
    theme still correct -- this is the pairing that catches a "swap one
    hardcoded constant for another" fix that would quietly break light).

The clamp is CONTRAST-GATED, so a colour already legible in the active theme is
never rewritten; and it is scoped to the DARK surface, so light output is
identical to the unpatched tree.  Every "clamped" assertion here fails against
the unpatched tree, where ``normalize_colors`` left the author colour verbatim.

Measured contrast (author colour vs each baked surface):
  blue!60!black #000099   dark 1.15:1  ->  clamped to >=3:1   light 14.38:1 keep
  Navy          #000080   dark 1.03:1  ->  clamped to >=3:1   light 16.01:1 keep
  black                   dark 1.27:1  ->  clamped to >=3:1   light 21.00:1 keep
  Teal          #008080   dark 3.45:1  keep                    light  4.77:1 keep
"""

import re

from app.utils.latex_color import (
    normalize_colors,
    _resolve_xcolor_rgb,
    _contrast_ratio,
    _THEME_SURFACE_RGB,
)

_DARK = _THEME_SURFACE_RGB["dark"]
_LIGHT = _THEME_SURFACE_RGB["light"]
_FLOOR = 3.0

_EXPR = re.compile(r"\{rgb,255:red,\d+;green,\d+;blue,\d+\}")


def _emitted_rgbs(text: str) -> list[tuple[int, int, int]]:
    """Every ``{rgb,255:...}`` expression in ``text``, resolved to RGB."""
    return [_resolve_xcolor_rgb(m.group(0)) for m in _EXPR.finditer(text)]


def test_below_floor_author_stroke_is_lifted_on_dark_and_untouched_on_light():
    """blue!60!black: invisible on dark (1.15:1) -> clamped; fine on light -> kept."""
    body = r"\draw[-{Stealth},line width=2pt,blue!60!black] (0,0) -- (4,0);"

    # Precondition: the author colour really is below the floor on dark and
    # comfortably above it on light -- so this test targets a real defect.
    author = _resolve_xcolor_rgb("blue!60!black")
    assert _contrast_ratio(author, _DARK) < _FLOOR
    assert _contrast_ratio(author, _LIGHT) >= _FLOOR

    dark, dark_fixes = normalize_colors(body, theme="dark")
    light, _ = normalize_colors(body, theme="light")

    # DARK (broken theme now correct): raw token gone, replaced by a colour
    # that clears the floor on #1F1F1F.
    assert "blue!60!black" not in dark
    dark_rgbs = _emitted_rgbs(dark)
    assert dark_rgbs, "expected a clamped {rgb,...} expression on dark"
    assert all(_contrast_ratio(c, _DARK) >= _FLOOR for c in dark_rgbs)
    assert dark_fixes, "clamp should report an applied fix"

    # LIGHT (other theme still correct): byte-for-byte unchanged.
    assert light == body


def test_bare_black_stroke_clamped_dark_only():
    """A bare ``black`` stroke (1.27:1 dark) is promoted to a legible color=.

    tikz treats a bare colour option as ``color=``; the clamp promotes it to an
    explicit ``color={rgb,...}`` so the result is unambiguous.
    """
    body = r"\draw[black, line width=0.06pt] (0,0) -- (1,1);"
    dark, _ = normalize_colors(body, theme="dark")
    light, _ = normalize_colors(body, theme="light")

    assert light == body                       # black on white is 21:1 -> keep
    assert "color={rgb,255:" in dark           # promoted + clamped
    for c in _emitted_rgbs(dark):
        assert _contrast_ratio(c, _DARK) >= _FLOOR


def test_color_key_value_below_floor_clamped_dark():
    """color=blue!60!black / color=red!70!black in circuitikz option context."""
    body = r"\draw (0,0) to[R=$R_1$, color=blue!60!black] (3,0) to[R=$R_2$, color=red!70!black] (6,0);"
    dark, _ = normalize_colors(body, theme="dark")
    light, _ = normalize_colors(body, theme="light")

    assert light == body
    assert "blue!60!black" not in dark and "red!70!black" not in dark
    rgbs = _emitted_rgbs(dark)
    assert len(rgbs) == 2
    for c in rgbs:
        assert _contrast_ratio(c, _DARK) >= _FLOOR


def test_color_macro_below_floor_clamped_dark():
    r"""\color{Navy} (1.03:1 on dark) is lifted; \color stays a \color macro."""
    body = r"\chemfig{\color{Navy}{H_3C}-C}"
    dark, _ = normalize_colors(body, theme="dark")
    light, _ = normalize_colors(body, theme="light")

    assert light == body
    assert r"\color{Navy}" not in dark
    m = re.search(r"\\color\{(rgb,255:red,\d+;green,\d+;blue,\d+)\}", dark)
    assert m, "Navy should be lifted inside a \\color macro on dark"
    assert _contrast_ratio(_resolve_xcolor_rgb("{" + m.group(1) + "}"), _DARK) >= _FLOOR


def test_already_legible_dark_colour_is_not_rewritten():
    """Teal (3.45:1 on dark) already clears the floor -> left exactly as authored."""
    body = r"\draw[Teal, thick] (0,0) -- (4,0);"
    assert _contrast_ratio(_resolve_xcolor_rgb("Teal"), _DARK) >= _FLOOR
    for theme in ("dark", "light"):
        out, applied = normalize_colors(body, theme=theme)
        assert out == body, f"legible colour rewritten in {theme}"
        assert applied == ()


def test_fill_is_not_clamped():
    """``fill=`` is a region, not ink -- excluded from the clamp in both themes.

    fill=green!20 (#CCFFCC) is a pale island at 14.7:1 on the dark page; its
    real issue is the label drawn ON it (per-element ink), not clamped here.
    """
    body = r"\node[draw,fill=green!20,rounded corners] (a) at (1,1) {Source};"
    for theme in ("dark", "light"):
        out, _ = normalize_colors(body, theme=theme)
        assert "fill=green!20" in out, f"fill wrongly clamped in {theme}"


def test_unresolvable_definecolor_name_left_untouched():
    """A \\definecolor name cannot be resolved -> author text is preserved."""
    body = (r"\definecolor{palegrey}{HTML}{D8DCE0}"
            r"\draw[draw=palegrey, thick] (0,3) -- (0,0);")
    for theme in ("dark", "light"):
        out, _ = normalize_colors(body, theme=theme)
        assert "draw=palegrey" in out, f"unresolvable name mangled in {theme}"


def test_light_surface_leaves_dark_author_colours_alone():
    """The clamp is dark-scoped: light output equals the syntax-only result.

    A near-black author stroke on the light page is high-contrast; the light
    surface must not be clamped (that would alter the authored-for surface).
    """
    body = r"\draw[blue!60!black] (0,0) -- (1,1); \draw[black] (0,0) -- (2,2);"
    light, applied = normalize_colors(body, theme="light")
    assert light == body
    assert applied == ()
