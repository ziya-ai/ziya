r"""
Regression guard for D-234 (fix group G-6d0f1f): a node given an explicit PALE
author fill and NO explicit ``text=`` must get a DARK label ink on the baked
dark page, while the light page is left byte-for-byte unchanged.

Root cause (confirmed against the on-disk code)
-----------------------------------------------
D-234 is the exact inverse of the old black-ink-on-dark bug, introduced by the
theme-ink flip itself.  ``latex_profiles.build_document`` bakes a LIGHT default
ink (#EDEDED) for the dark page so free-standing text is legible.  A node the
author gave a pale fill (``fill=green!20`` #ccffcc, ``fill=yellow!35``,
``fill=black!10`` ...) but no explicit ``text=`` draws its LABEL in that same
light default ink -- light-on-pale -- which washes the label out (#EDEDED on
#ccffcc = 1.04:1) while the fill island stays visible.

Fix: a dark-theme-only pass in ``latex_color.normalize_colors`` chooses the
label ink PER FILL LUMINANCE.  For an option block carrying a resolvable pale
fill and no author ``text=``, it injects ``text=black`` -- but ONLY when the
light default ink fails the 3:1 graphical floor against that fill, which
guarantees black scores comfortably (>=6:1) on it.  Dark/mid fills (where
#EDEDED already clears the floor) and author-set ``text=`` are left untouched,
and the light theme is a strict no-op, so no light regression is possible.

Direction is asserted explicitly: each case proves the light default ink is
below the floor on the fill (the defect) BEFORE asserting the injected ink is
present and legible, and BOTH themes are checked.
"""

import pytest

from app.utils.latex_color import (
    normalize_colors, _contrast_ratio, _resolve_xcolor_rgb,
)

DARK_INK = (0xED, 0xED, 0xED)   # baked dark-page default ink
BLACK = (0, 0, 0)
FLOOR = 3.0                     # WCAG graphical / large-text contrast floor

# Pale fills D-234 names (tikz-w1-06 / w3-02 / w3-03 / w4-14), in the option
# forms the specs use.  Each is a fill on which the light default ink washes out.
PALE_FILL_CASES = [
    ("green!20", r"\node[fill=green!20] {L};"),
    ("yellow!35", r"\node[draw=gray, fill=yellow!35, rounded corners] {L};"),
    ("red!25", r"\node[fill=red!25] at (0,0) {L};"),
    ("black!10", r"\node[fill=black!10] {L};"),
    ("blue!20", r"\node[fill=blue!20] {L};"),
    ("orange!20", r"\node[fill=orange!20] {L};"),
    # hex form that the earlier passes normalise to fill={rgb,...} first
    ("hex ccffcc", r"\node[fill=#ccffcc] {L};"),
]


@pytest.mark.parametrize("fill_tok, body", PALE_FILL_CASES)
def test_pale_fill_gets_dark_label_ink_on_dark_page(fill_tok, body):
    # Direction: the light default ink is genuinely below the floor on this
    # fill (that is the washed-out label the defect describes).  For the named
    # tokens we can resolve the fill RGB directly.
    if not fill_tok.startswith("hex"):
        fill_rgb = _resolve_xcolor_rgb(fill_tok)
        assert fill_rgb is not None
        assert _contrast_ratio(DARK_INK, fill_rgb) < FLOOR, (
            f"{fill_tok}: light default ink should be below the floor (the bug)")
        # And the ink we inject (black) is comfortably legible on the fill.
        assert _contrast_ratio(BLACK, fill_rgb) >= 6.0

    dark, dark_notes = normalize_colors(body, theme="dark")
    light, _ = normalize_colors(body, theme="light")

    # Post-fix on the dark page: a dark label ink is now present.
    assert "text=black" in dark, (
        f"dark render should inject a dark label ink for a pale fill: {dark!r}")
    assert any("text=black" in n for n in dark_notes)

    # Light page never gets a dark-ink injection: its black default ink already
    # contrasts with a pale fill, so injecting would be a needless change /
    # possible regression.  (The hex form is rewritten to {rgb,...} in BOTH
    # themes by a theme-independent syntax pass, so byte-identity is asserted
    # only for the tokens that carry no such rewrite.)
    assert "text=black" not in light
    if not fill_tok.startswith("hex"):
        assert light == body


def test_dark_fill_keeps_light_default_ink():
    # A dark fill: the baked light default ink already clears the floor, so the
    # pass must NOT touch it (that would invert a legible label).
    body = r"\node[fill=blue!80] {L};"
    fill_rgb = _resolve_xcolor_rgb("blue!80")
    assert _contrast_ratio(DARK_INK, fill_rgb) >= FLOOR
    dark, _ = normalize_colors(body, theme="dark")
    assert "text=" not in dark


def test_author_text_colour_is_respected():
    # An explicit author text= must win; the pass leaves the block alone.
    body = r"\node[fill=green!20, text=white] {L};"
    dark, _ = normalize_colors(body, theme="dark")
    assert "text=white" in dark
    assert "text=black" not in dark


def test_unresolvable_definecolor_fill_is_left_alone():
    # A \definecolor name cannot be resolved to a luminance here, so the pass
    # must leave it untouched rather than guess (advisory, never fatal).
    body = r"\node[fill=motifA!12] {L};"
    dark, _ = normalize_colors(body, theme="dark")
    assert "text=black" not in dark
