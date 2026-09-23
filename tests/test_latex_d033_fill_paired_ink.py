"""D-033: correct an EXPLICIT fill-paired label ink that is illegible on its
own chip (chemfig-w3-13).

Before this fix an explicit but wrong ``text=`` paired with a ``fill=`` fell
through every handler: the D-234/D-048 injectors only act when NO ink is set,
and the step-8 ``text=`` clamp deliberately skips a fill-paired ink (D-331).  So
``\\node[fill=Navy, text=black]`` (black on #000080 = 1.31:1) and
``\\node[fill=DarkOrange, text=white]`` (white on #ff8c00 = 2.33:1) stayed
illegible on their own chips in BOTH themes with no contrast guard at all.

The pass flips such an ink to the more-legible monochrome endpoint, measured
against the node's own (opacity-composited) chip -- and ONLY when the author's
ink is below the 4.5:1 small-text floor there, so a legible pairing is left
byte-for-byte.  Because the decision is against the opaque chip, not the page,
it is theme-invariant.
"""
import re

from app.utils.latex_color import (
    normalize_colors,
    _resolve_xcolor_rgb,
    _contrast_ratio,
)

FLOOR = 4.5


def _text_value(body: str) -> str:
    """The ``text=`` value token in a single-node body."""
    m = re.search(r"text\s*=\s*(\{[^{}]*\}|[A-Za-z][\w!]*)", body)
    assert m, f"no text= in {body!r}"
    return m.group(1)


def test_black_on_navy_flipped_to_white_both_themes():
    body = r"\node[fill=Navy, text=black]{lbl};"
    fill = _resolve_xcolor_rgb("Navy")
    assert _contrast_ratio((0, 0, 0), fill) < FLOOR          # authored ink fails
    for theme in ("light", "dark"):
        out, _ = normalize_colors(body, theme=theme)
        ink = _resolve_xcolor_rgb(_text_value(out))
        assert ink is not None
        assert _contrast_ratio(ink, fill) >= FLOOR, f"{theme}: still illegible"
        assert ink == (255, 255, 255), f"{theme}: expected white ink"


def test_white_on_darkorange_flipped_to_black_both_themes():
    body = r"\node[fill=DarkOrange, text=white]{lbl};"
    fill = _resolve_xcolor_rgb("DarkOrange")
    assert _contrast_ratio((255, 255, 255), fill) < FLOOR    # authored ink fails
    for theme in ("light", "dark"):
        out, _ = normalize_colors(body, theme=theme)
        ink = _resolve_xcolor_rgb(_text_value(out))
        assert ink is not None
        assert _contrast_ratio(ink, fill) >= FLOOR, f"{theme}: still illegible"
        assert ink == (0, 0, 0), f"{theme}: expected black ink"


def test_legible_pairing_is_left_untouched():
    # white on Teal (#008080) = 4.77:1, above the floor -> respect the author.
    body = r"\node[fill=Teal, text=white]{lbl};"
    for theme in ("light", "dark"):
        out, _ = normalize_colors(body, theme=theme)
        assert "text=white" in out, f"{theme}: legible author ink must survive"


def test_no_fill_is_not_touched_by_this_pass():
    # No fill -> this pass does nothing (a bare-page text= is the step-8 clamp's
    # job, not this fill-paired corrector).
    body = r"\node[text=black]{lbl};"
    out, _ = normalize_colors(body, theme="light")
    # light page: black on white is legible, so nothing changes here either.
    assert "text=black" in out


def test_unresolvable_fill_left_alone():
    # A \definecolor / gradient fill cannot be measured -> leave the ink as-is.
    body = r"\node[fill=motifA, text=black]{lbl};"
    out, _ = normalize_colors(body, theme="dark")
    assert "text=black" in out
