r"""
Regression guards for fix group G-c5b87e (tikz), confirmed against the on-disk
``app/utils/latex_color.py``.

D-492 -- pale-fill label ink skips a NESTED-BRACKET option block (theme, dark)
----------------------------------------------------------------------------
``_pale_fill_label_ink`` scanned option blocks with the non-nesting
``_OPT_BLOCK_RE`` (``\[([^\[\]]*)\]``).  A node whose option block carries value
braces that themselves contain bracket groups --
``[..., fill=orange!25, label={[red]left:..}, pin={[pin distance=8mm]30:..}]``
(tikz-w3-05) -- cannot be matched by that regex, so the pale ``fill=orange!25``
was never seen and the node's label kept the washed-out light default ink on the
dark page (#EDEDED on orange!25 ~= 1.1:1).  Fix: a brace/bracket-depth-aware
scanner (``_sub_option_blocks``) passes the WHOLE block to the label-ink pass.
Both themes are asserted: dark injects a legible ``text=black``; light is a
strict byte-for-byte no-op.

D-499 -- colour rewrite lands inside LABEL TEXT (recovery, both themes)
----------------------------------------------------------------------
``_OPT_TRANSPARENT_RE`` matched ``(fill|draw|color|text)=transparent`` anywhere,
not only inside a ``[...]`` option block.  A heading node whose literal LABEL
TEXT reads ``{fill=transparent}`` (tikz-w4-07) had that DISPLAYED string
rewritten to ``fill=none`` -- corrupting content.  Fix: ``_opt_transp_sub`` fires
only when the match sits in a bracket option context, never inside a ``{...}``
label brace.  The genuine option sites in the same body must still be recovered.

Direction is asserted explicitly: each check would FAIL on the pre-fix code
(nested block skipped / label string rewritten) and passes with the fix, in
BOTH themes.
"""

from app.utils.latex_color import (
    normalize_colors, _contrast_ratio, _resolve_xcolor_rgb,
)

DARK_INK = (0xED, 0xED, 0xED)   # baked dark-page default ink
FLOOR = 3.0

# tikz-w3-05: a pale-fill node whose option block nests brackets inside its
# label={...} / pin={...} value braces.
_NESTED = (
    r"\node[draw, fill=orange!25, minimum size=9mm,"
    r" label={above:label above}, label={below:label below},"
    r" label={[red]left:red left}, pin={[pin distance=8mm]30:pinned}]"
    r" (z) at (5.5,-3) {z};"
)


def test_d492_nested_bracket_pale_fill_gets_dark_ink():
    # The washed-out condition the defect describes: the light default dark-page
    # ink is genuinely below the floor on the orange!25 fill.
    fill_rgb = _resolve_xcolor_rgb("orange!25")
    assert fill_rgb is not None
    assert _contrast_ratio(DARK_INK, fill_rgb) < FLOOR

    dark, _ = normalize_colors(_NESTED, "dark")
    # With the balanced-block scanner the pale fill is seen and a legible dark
    # label ink is injected (fails on the pre-fix non-nesting regex).
    assert "text=black" in dark
    assert _contrast_ratio((0, 0, 0), fill_rgb) >= FLOOR


def test_d492_light_theme_is_byte_identical():
    light, _ = normalize_colors(_NESTED, "light")
    # The dark-only pass must not touch the light render.
    assert light == _NESTED


# tikz-w4-07: a heading node whose LABEL TEXT is the literal string
# ``fill=transparent``, alongside three genuine option sites.
_W4_07 = (
    r"\fill[black!88] (-0.5,-0.5) rectangle (8.00,3.20);" "\n"
    r"\node[white,anchor=west,font=\bfseries] at (-0.2,2.65) {fill=transparent};" "\n"
    r"\node[draw=white,text=white,fill=transparent] (a) at (1,1) {Ghost};" "\n"
    r"\node[draw=transparent,text=white,fill=blue!60!black] (b) at (4,1) {Solid};" "\n"
    r"\node[draw=white,text=white,fill=transparent!50] (c) at (7,1) {Half};" "\n"
)


def test_d499_label_text_preserved_and_options_recovered_both_themes():
    for theme in ("light", "dark"):
        out, _ = normalize_colors(_W4_07, theme)
        # The displayed heading text must survive verbatim (fails pre-fix: it
        # was rewritten to ``{fill=none}``).
        assert "{fill=transparent}" in out, theme
        # The genuine option sites are still recovered: transparent fill/draw
        # -> none.
        assert "fill=none" in out, theme
        assert "draw=none" in out, theme
