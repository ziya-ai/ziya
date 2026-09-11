r"""
Fix group G-dbcebc (key file ``app/utils/latex_color.py``).

Covers three defects worked under this group:

* D-037 -- ``recovered-colour-not-theme-adapted-below-floor:dark``.  A recovered
  ``\textcolor{#36c}`` (-> #3366CC) scores 3.07:1 on the baked dark page
  (#1F1F1F).  That clears the 3:1 GRAPHICAL floor, so the old clamp left it --
  but the atom labels it colours are TEXT and need the 4.5:1 small-text floor.
  The fix: a ``\color`` / ``\textcolor`` argument is clamped against the 4.5:1
  text floor on the dark surface, while the light surface (5.37:1, already above
  the floor) is left byte-for-byte unchanged.

* D-051 -- ``bare-css-name-not-in-key-context-fatal`` (circuitikz-w4-06).  A
  bare CSS colour name used as a standalone option token ``\node[darkslategray]``
  (not ``key=value``) was a fatal "Undefined color".  Guarded here: the bare
  name is remapped to its CamelCase svgnames spelling, so the render is no
  longer fatal.

* D-247 -- ``rgba-rewrite-inside-definecolor-model-fatal`` (tikz-w4-06).  A
  ``\definecolor{acc}{rgb}{rgba(...)}`` was self-corrupted by the generic
  ``rgb()`` pass into a brace-in-value the ``rgb`` model rejects.  Guarded here:
  the ``\definecolor`` is rewritten to a valid ``{RGB}{r,g,b}`` (alpha dropped)
  before the generic pass runs, so no fatal brace survives.

Every theme assertion is checked in BOTH themes.
"""

import re

import pytest

from app.utils.latex_color import (
    normalize_colors,
    _contrast_ratio,
    _resolve_xcolor_rgb,
    _THEME_SURFACE_RGB,
    _TEXT_CONTRAST_FLOOR,
)

_DARK = _THEME_SURFACE_RGB["dark"]
_LIGHT = _THEME_SURFACE_RGB["light"]

_EXPR = re.compile(r"rgb,255:red,(\d+);green,(\d+);blue,(\d+)")


def _emitted(text: str) -> list[tuple[int, int, int]]:
    return [(int(r), int(g), int(b)) for r, g, b in _EXPR.findall(text)]


# --------------------------------------------------------------------------
# D-037: recovered text-ink colour must clear the 4.5:1 TEXT floor on dark.
# --------------------------------------------------------------------------
def test_d037_recovered_textcolor_meets_text_floor_on_dark():
    """#36c -> #3366CC (3.07:1 dark) is a text ink -> lifted to 4.5:1 on dark.

    This FAILS against the pre-fix tree, where the clamp used the 3:1 graphical
    floor for ``\\textcolor`` and 3.07:1 already passed, so the atom labels
    stayed below the 4.5:1 text floor.
    """
    body = r"\textcolor{#36c}{\chemfig{H_3C-[:30](-[:90]OH)-[:-30]CH_3}}"

    # Precondition: the recovered colour is between the graphical (3.0) and the
    # text (4.5) floor on the dark page -- the exact gap D-037 names.
    recovered = _resolve_xcolor_rgb("{rgb,255:red,51;green,102;blue,204}")
    assert 3.0 <= _contrast_ratio(recovered, _DARK) < _TEXT_CONTRAST_FLOOR

    dark, dark_applied = normalize_colors(body, theme="dark")
    dark_rgbs = _emitted(dark)
    assert dark_rgbs, "expected a clamped text-ink expression on dark"
    for c in dark_rgbs:
        assert _contrast_ratio(c, _DARK) >= _TEXT_CONTRAST_FLOOR, (
            f"dark text ink {c} is {_contrast_ratio(c, _DARK):.2f}:1, "
            f"below the {_TEXT_CONTRAST_FLOOR}:1 text floor")
    assert dark_applied, "clamp should report an applied fix on dark"
    assert r"\textcolor" in dark  # stays a \textcolor macro


def test_d037_light_leg_keeps_recovered_colour_above_text_floor():
    """On the light page #3366CC is 5.37:1 (>= 4.5) -> recovered but NOT clamped.

    Guards the swap direction: the fix must not quietly darken/alter the light
    render, which was already legible.
    """
    body = r"\textcolor{#36c}{\chemfig{H_3C-[:30](-[:90]OH)-[:-30]CH_3}}"
    light, _ = normalize_colors(body, theme="light")
    light_rgbs = _emitted(light)
    assert light_rgbs == [(51, 102, 204)], (
        "light leg should keep the recovered #3366CC verbatim (no clamp)")
    assert _contrast_ratio((51, 102, 204), _LIGHT) >= _TEXT_CONTRAST_FLOOR


def test_d037_stroke_option_still_uses_graphical_floor():
    """A stroke (draw=/color=/bare) keeps the 3:1 graphical floor, not 4.5.

    A colour at ~3.2:1 on dark as a STROKE is legible graphical output and must
    be left alone -- the text-floor tightening is scoped to \\color/\\textcolor.
    """
    # #3366CC at 3.07:1 on dark, used as a stroke option: below 3.0? it is 3.07,
    # just above the graphical floor, so a stroke keeps it.
    body = r"\draw[draw={rgb,255:red,51;green,102;blue,204}] (0,0) -- (1,1);"
    dark, _ = normalize_colors(body, theme="dark")
    assert _emitted(dark) == [(51, 102, 204)], (
        "a graphical stroke at 3.07:1 dark must not be clamped by the text floor")


# --------------------------------------------------------------------------
# D-051: bare CSS colour name as a standalone option is remapped (not fatal).
# --------------------------------------------------------------------------
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_d051_bare_css_name_option_remapped(theme):
    """``\\node[darkslategray]`` -> CamelCase svgnames spelling (no fatal).

    Against the pre-fix tree the lowercase bare name reached LaTeX verbatim and
    aborted with "I do not know the key /tikz/darkslategray".
    """
    body = r"\node[darkslategray] at (2.4,1.4) {CSS names};"
    out, _ = normalize_colors(body, theme=theme)
    assert "darkslategray" not in out, "bare lowercase CSS name left unremapped"
    # Remapped to the canonical svgnames spelling, or (on dark) further clamped
    # to a legible {rgb,...} -- either way it is no longer the fatal token.
    assert ("DarkSlateGray" in out) or ("rgb,255:" in out)


# --------------------------------------------------------------------------
# D-247: rgba() inside a \definecolor rgb model is de-parenthesised safely.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_d247_definecolor_rgba_not_self_corrupted(theme):
    r"""``\definecolor{acc}{rgb}{rgba(52,152,219,0.85)}`` -> ``{RGB}{52,152,219}``.

    Against the pre-fix tree the generic rgb() pass rewrote the value into
    ``\definecolor{acc}{rgb}{{rgb,255:...}}`` -- a brace-in-value the rgb model
    rejects with "Missing number, treated as zero" (fatal, no image).
    """
    body = r"\definecolor{acc}{rgb}{rgba(52,152,219,0.85)}"
    out, _ = normalize_colors(body, theme=theme)
    # No brace-in-value survives inside the \definecolor model slot.
    assert "{{" not in out, f"self-corrupted brace-in-value survived: {out}"
    # Rewritten to a valid RGB 0..255 model with alpha dropped.
    assert r"\definecolor{acc}{RGB}{52,152,219}" in out
