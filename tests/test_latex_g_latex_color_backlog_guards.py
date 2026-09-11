r"""
Regression guards for the G-LATEX-COLOR backlog group (stage-2 fix pass).

Context
-------
The G-LATEX-COLOR consolidated backlog group covers the shared LaTeX colour
normaliser ``app/utils/latex_color.py`` across chemfig / circuitikz / tikz /
tikz-cd.  Its four RECOVERY defects were confirmed already-remediated in the
tree and are exercised by ``test_latex_g04_color_context.py`` /
``test_latex_g02_color_unicode.py`` -- with ONE backlog spec left without a
dedicated guard: D-030's headline case ``chemfig-w4-04``, an ``rgba()`` call in
chemfig's POSITIONAL 5th bond-colour field.  ``test_latex_g04`` covers the
positional NAME field (w4-05) and the generic option-value ``rgb()`` pass, but
not this exact positional-``rgba()`` combination.  These guards close that gap.

Direction / discriminating power
--------------------------------
Each assertion checks that the raw ``rgba(...)`` token (which is a FATAL
"I do not know the key /tikz/rgb"-class abort for chemfig when forwarded
verbatim) is GONE and replaced by a braced xcolor extended expression with the
author's exact RGB channels (alpha dropped -- xcolor has no alpha channel).
Against an unpatched normaliser that lacked the ``rgba()``-anywhere pass the
raw ``rgba(`` would survive and every assertion below would FAIL, so these
tests certify the fix, not the bug.  The rewrite is theme-independent, so it is
asserted identically in BOTH themes.
"""

import re

import pytest

from app.utils.latex_color import normalize_colors


# chemfig-w4-04: rgba() in the positional 5th bond-colour field
# ``-[angle,,,,COLOUR]`` -- the field chemfig forwards to ``\color``.
_W4_04 = r"\chemfig{A-[:30,,,,rgba(34,139,34,0.6)]B-[:-30,,,,rgba(220,20,60,0.8)]C}"

_EXPR = re.compile(r"\{rgb,255:red,(\d+);green,(\d+);blue,(\d+)\}")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chemfig_positional_rgba_field_converted_both_themes(theme):
    """D-030 (chemfig-w4-04): positional-field rgba() -> braced xcolor expr."""
    out, applied = normalize_colors(_W4_04, theme=theme)

    # The fatal raw CSS call is gone in both themes.
    assert "rgba(" not in out, f"raw rgba() survived in {theme}"

    # Both author colours survive as braced expressions with exact channels,
    # alpha dropped: forestgreen (34,139,34) and crimson (220,20,60).
    channels = [tuple(int(x) for x in m.groups()) for m in _EXPR.finditer(out)]
    assert (34, 139, 34) in channels, f"forestgreen channels lost in {theme}"
    assert (220, 20, 60) in channels, f"crimson channels lost in {theme}"

    # And the normaliser reported it acted (advisory contract).
    assert applied, f"no applied fix reported in {theme}"


def test_chemfig_positional_rgba_is_theme_independent():
    """The positional-field rewrite is pure syntax -- identical light vs dark."""
    light, _ = normalize_colors(_W4_04, theme="light")
    dark, _ = normalize_colors(_W4_04, theme="dark")
    assert light == dark
