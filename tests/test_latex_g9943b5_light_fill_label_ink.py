r"""
Backlog guard for D-048 (fix group G-9943b5): a node given an explicit
SATURATED/dark author fill and NO explicit ``text=`` must get a LIGHT (white)
label ink on the baked light page, while the dark page is left byte-for-byte
unchanged.

Root cause (confirmed against the on-disk code)
-----------------------------------------------
``latex_profiles.build_document`` bakes a DARK default ink (#000000) for the
light page.  A node the author gave a dark fill (``fill=blue!70`` #4d4dff,
``fill=blue!85``) but no explicit ``text=`` draws its LABEL in that black
default ink -- black-on-dark -- which fails the 4.5:1 text floor (#000000 on
blue!70 = 3.82:1, on blue!85 = 2.80:1; circuitikz-w3-05 light) while the fill
island stays visible.  This is the light-page mirror of the D-234 dark-page
wash-out, and the fix is symmetric: ``latex_color.normalize_colors`` chooses
the label ink PER FILL LUMINANCE against the node's OWN fill.

Both themes are asserted (theme defect): the light page gains a legible white
ink, the dark page is untouched by this pass.  Direction is asserted
explicitly -- each dark-fill case proves black is below the text floor (the
defect) BEFORE asserting white is injected and legible, and each pale-fill /
mid-fill case proves black already clears the floor so nothing is injected.
"""

import pytest

from app.utils.latex_color import (
    normalize_colors, _contrast_ratio, _resolve_xcolor_rgb,
)

BLACK = (0, 0, 0)               # baked light-page default ink
WHITE = (255, 255, 255)
TEXT_FLOOR = 4.5                # WCAG small-text / label contrast floor

# The circuitikz-w3-05 fills whose LIGHT-page default (black) label fails the
# text floor -- exactly the illegible captions D-048 names.
DARK_FILL_CASES = [
    ("blue!70", r"\node[draw, fill=blue!70, minimum width=2.4cm] (a) {STAGE A};"),
    ("blue!85", r"\node[draw, fill=blue!85] {L};"),
]

# Fills on which black is already legible on the light page -- the pass must
# leave these byte-identical (no needless / regressive injection).
PALE_FILL_CASES = [
    ("red!60", r"\node[draw, fill=red!60] {STAGE B};"),
    ("green!50", r"\node[draw, fill=green!50] {STAGE C};"),
    ("yellow!80", r"\node[draw, fill=yellow!80] {BIAS};"),
    ("blue!20", r"\node[fill=blue!20] {L};"),
]


@pytest.mark.parametrize("fill_tok, body", DARK_FILL_CASES)
def test_dark_fill_gets_white_label_ink_on_light_page(fill_tok, body):
    fill_rgb = _resolve_xcolor_rgb(fill_tok)
    assert fill_rgb is not None
    # Direction: black (the baked light default ink) is genuinely below the
    # text floor on this fill -- the washed-out caption the defect describes.
    assert _contrast_ratio(BLACK, fill_rgb) < TEXT_FLOOR, (
        f"{fill_tok}: black default ink should be below the text floor (the bug)")
    # ...and white is a strict improvement.
    assert _contrast_ratio(WHITE, fill_rgb) > _contrast_ratio(BLACK, fill_rgb)

    light, light_notes = normalize_colors(body, theme="light")
    dark, _ = normalize_colors(body, theme="dark")

    # Light page: a white label ink is now injected.
    assert "text=white" in light, (
        f"light render should inject a light label ink for a dark fill: {light!r}")
    assert any("text=white" in n for n in light_notes)

    # Dark page: this light-only pass must NOT act (the dark default ink #EDEDED
    # already contrasts these mid/dark fills; step 9 owns the dark page).
    assert "text=white" not in dark


@pytest.mark.parametrize("fill_tok, body", PALE_FILL_CASES)
def test_pale_fill_keeps_black_default_ink_on_light_page(fill_tok, body):
    fill_rgb = _resolve_xcolor_rgb(fill_tok)
    assert fill_rgb is not None
    # black already clears the text floor here, so the pass must be a no-op.
    assert _contrast_ratio(BLACK, fill_rgb) >= TEXT_FLOOR
    light, _ = normalize_colors(body, theme="light")
    assert "text=white" not in light
    assert light == body


def test_author_text_colour_is_respected_on_light_page():
    # An explicit author text= wins even on a dark fill; the pass leaves it.
    body = r"\node[draw, fill=blue!70, text=yellow] {L};"
    light, _ = normalize_colors(body, theme="light")
    assert "text=yellow" in light
    assert "text=white" not in light


def test_unresolvable_loopvar_fill_is_left_alone_on_light_page():
    # circuitikz-w3-07 draws ``fill=blue!\p`` inside a \foreach: the percentage
    # is a TeX loop variable expanded at compile time, invisible to this
    # source-level pass, so the fill is unresolvable and must be left as
    # authored (advisory, never fatal).
    body = r"\node[draw, fill=blue!\p] {\p};"
    light, _ = normalize_colors(body, theme="light")
    assert "text=white" not in light
    assert light == body
