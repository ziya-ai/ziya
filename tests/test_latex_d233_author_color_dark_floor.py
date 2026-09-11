r"""
Regression guard for D-233 (worked under fix group G-5f9c4e): author-explicit
dark-mix stroke/ink colours that sit below the graphical contrast floor on the
baked dark page must be lifted to the floor, while the light page is left
byte-for-byte unchanged.

Root cause (confirmed against the on-disk code)
-----------------------------------------------
D-233's triage lead pointed at ``D3Renderer.tsx:1194`` (a browser reference
surface), but that is not where the headless PNG surface is decided.  The real
cause is shared with D-003: ``latex_profiles.build_document`` bakes ONLY the
default ink/page (dark #1F1F1F + #EDEDED); an author-EXPLICIT colour chosen for
a white page (``blue!60!black`` #000099 -> 1.15:1, ``blue!70!black`` -> 1.29:1,
``red!70!black`` -> 2.29:1, a bare ``\draw[black]`` -> 1.27:1, pure ``blue`` ->
1.92:1 on #1F1F1F) is passed through untouched and vanishes on the dark plate.

The contrast clamp in ``latex_color.normalize_colors`` (added for D-003) already
covers every colour D-233 names -- across the ``[..., blue!60!black]`` bare
option, the ``[blue!70!black, ...]`` leading-option and the bare ``[black, ...]``
forms these specs use -- resolving each in a stroke/ink context, measuring WCAG
contrast against the surface the renderer was given, and blending toward the
surface-opposite endpoint only when below the 3:1 floor.  This test pins that
coverage against D-233's exact spec fragments (tikz-w1-02/w2-06/w2-13) and is a
strict no-op assertion on light.
"""

import pytest

from app.utils.latex_color import normalize_colors, _contrast_ratio, _resolve_xcolor_rgb

DARK = (0x1F, 0x1F, 0x1F)   # baked dark page
WHITE = (0xFF, 0xFF, 0xFF)  # baked light page
FLOOR = 3.0                 # WCAG graphical / large-text contrast floor

# (label, spec fragment, author colour token) — the below-floor author colours
# named in D-233, in the exact syntactic forms the failing specs use.
BELOW_FLOOR_CASES = [
    ("w1-02 bare trailing option",
     r"\draw[-{Stealth},line width=2pt,blue!60!black] (0,0) -- (4,0);", "blue!60!black"),
    ("w2-06 leading option (blue)",
     r"\draw[blue!70!black, line width=0.2pt] plot (\x,\y);", "blue!70!black"),
    ("w2-06 leading option (red)",
     r"\draw[red!70!black, line width=0.2pt] plot (\x,\y);", "red!70!black"),
    ("w2-13 bare black stroke",
     r"\draw[black, line width=0.06pt] (0,0) -- (1,1);", "black"),
    ("pure blue stroke",
     r"\draw[blue] (0,0) -- (1,1);", "blue"),
]


@pytest.mark.parametrize("label,body,token", BELOW_FLOOR_CASES, ids=[c[0] for c in BELOW_FLOOR_CASES])
def test_below_floor_author_colour_is_lifted_on_dark(label, body, token):
    # The author colour is genuinely below the floor on the dark plate.
    orig_rgb = _resolve_xcolor_rgb(token)
    assert orig_rgb is not None
    assert _contrast_ratio(orig_rgb, DARK) < FLOOR, f"{token} was expected below floor on dark"

    out_dark, _ = normalize_colors(body, theme="dark")
    # The clamp rewrote the author colour (verbatim pass-through would fail here).
    assert out_dark != body, f"{token} was left unclamped on dark ({label})"
    # Every rgb expression the clamp emitted meets the floor against the dark page.
    import re
    emitted = re.findall(r"rgb,255:red,(\d+);green,(\d+);blue,(\d+)", out_dark)
    assert emitted, f"no clamped rgb expression emitted for {label}"
    for r, g, b in emitted:
        rgb = (int(r), int(g), int(b))
        assert _contrast_ratio(rgb, DARK) >= FLOOR, (
            f"{label}: clamped {rgb} is {_contrast_ratio(rgb, DARK):.2f}:1 on dark, below {FLOOR}")


@pytest.mark.parametrize("label,body,token", BELOW_FLOOR_CASES, ids=[c[0] for c in BELOW_FLOOR_CASES])
def test_light_page_is_byte_identical(label, body, token):
    # The other theme must stay correct: these colours were chosen for white and
    # are already well above the floor there, so the light path is untouched.
    orig_rgb = _resolve_xcolor_rgb(token)
    assert _contrast_ratio(orig_rgb, WHITE) >= FLOOR
    out_light, _ = normalize_colors(body, theme="light")
    assert out_light == body, f"{label}: light output must be byte-identical"


def test_already_legible_dark_author_colour_is_not_rewritten():
    # green!50!black (#008000) and pure red are above the floor on dark already;
    # the contrast gate must leave them verbatim (no over-clamping).
    for body in (r"\draw[green!50!black, line width=0.2pt] plot (\x,\y);",
                 r"\fill[red] (0,0) circle (2pt);"):
        out_dark, _ = normalize_colors(body, theme="dark")
        assert out_dark == body, f"already-legible colour was needlessly rewritten: {body}"
