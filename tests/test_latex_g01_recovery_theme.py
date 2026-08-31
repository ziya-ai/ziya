"""
Regression tests for fix group G-01 (server-side LaTeX renderer).

Three defects, all repaired in the render/profile layer rather than per spec:

  * D-001 (theme): the LaTeX PNG path baked no theme in -- black ink on a
    transparent background composited ~1.27:1 on the dark panel.  The renderer
    now bakes a themed opaque surface (dark page + light ink / white page +
    dark ink) into the PNG, resolved FROM the theme, while leaving the SVG path
    transparent for the browser to recolour.

  * D-003 (recovery): the security prescan fired before any wrapper stripping,
    so a full ``\\documentclass`` document, a prepended ``\\usepackage`` line
    or a markdown fence was hard-rejected; and ``_wrap`` double-wrapped a body
    that already carried its own drawing environment.

  * D-050 (recovery): the removed ``\\setatomsep`` / ``\\setbondoffset`` /
    ``\\setdoublesep`` family aborted fatally; they are now rewritten to the
    modern ``\\setchemfig{key=value}`` form.

Each assertion below fails against the unpatched code (missing kwarg / missing
symbol / rejected body / double-wrap) and passes with the fix.
"""

from app.services.latex_profiles import get_profile
from app.services.latex_renderer import LatexRenderer
from app.utils.chemfig_lint import rewrite_deprecated_setters


# --------------------------------------------------------------------------
# D-001  theme-aware raster surface (BOTH themes asserted)
# --------------------------------------------------------------------------

def test_png_dark_theme_bakes_light_ink_on_dark_page():
    """Dark PNG: dark page + light ink (measured #EDEDED on #1F1F1F = 14.08:1)."""
    doc = get_profile("tikz").build_document(
        "\\draw (0,0) -- (1,1);", standalone=True, fmt="png", theme="dark")
    assert "\\pagecolor[HTML]{1F1F1F}" in doc
    assert "\\color[HTML]{EDEDED}" in doc
    # The dark surface must NOT be a white page (that is the light-regression
    # trap a naive constant-swap would fall into).
    assert "\\pagecolor[HTML]{FFFFFF}" not in doc


def test_png_light_theme_keeps_dark_ink_on_white_page():
    """Light PNG: white page + dark ink (measured #000000 on #FFFFFF = 21:1).

    Paired with the dark assertion above so the theme fix is proven on BOTH
    backgrounds, not just the one that was broken.
    """
    doc = get_profile("tikz").build_document(
        "\\draw (0,0) -- (1,1);", standalone=True, fmt="png", theme="light")
    assert "\\pagecolor[HTML]{FFFFFF}" in doc
    assert "\\color[HTML]{000000}" in doc
    assert "\\pagecolor[HTML]{1F1F1F}" not in doc


def test_svg_path_left_transparent_for_browser_recolour():
    """SVG carries no baked surface -- enhanceSVGVisibility recolours it live."""
    doc = get_profile("tikz").build_document(
        "\\draw (0,0) -- (1,1);", standalone=True, fmt="svg", theme="dark")
    assert "\\pagecolor" not in doc
    assert "\\color[HTML]" not in doc


# --------------------------------------------------------------------------
# D-003  recover wrapper shapes BEFORE the prescan; do not double-wrap
# --------------------------------------------------------------------------

def test_full_document_body_is_extracted_not_rejected():
    """A full \\documentclass document is stripped to its body, so the prescan
    (which denies \\documentclass/\\usepackage) no longer rejects it."""
    src = (
        "\\documentclass{standalone}\n"
        "\\usepackage{tikz}\n"
        "\\begin{document}\n"
        "\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}\n"
        "\\end{document}\n"
    )
    cleaned = LatexRenderer._sanitize_input(src)
    assert "\\documentclass" not in cleaned
    assert "\\usepackage" not in cleaned
    assert "\\begin{tikzpicture}" in cleaned
    assert LatexRenderer.prescan(cleaned) is None


def test_prepended_preamble_lines_are_dropped():
    """\\usepackage prepended before a bare body is removed, not rejected."""
    src = "\\usepackage{tikz}\n\\draw (0,0) -- (1,1);"
    cleaned = LatexRenderer._sanitize_input(src)
    assert "\\usepackage" not in cleaned
    assert "\\draw (0,0) -- (1,1);" in cleaned
    assert LatexRenderer.prescan(cleaned) is None


def test_markdown_fence_is_unwrapped():
    src = "```latex\n\\draw (0,0) -- (1,1);\n```"
    cleaned = LatexRenderer._sanitize_input(src)
    assert "```" not in cleaned
    assert cleaned == "\\draw (0,0) -- (1,1);"


def test_wrap_does_not_double_wrap_foreign_environment():
    """A tikzpicture supplied under the tikz-cd profile must pass through, not
    get a second \\begin{tikzcd} wrapped around it (which would abort with a
    mismatched-environment error)."""
    profile = get_profile("tikz-cd")
    body = "\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}"
    wrapped = profile._wrap(body)
    assert wrapped == body
    assert "\\begin{tikzcd}" not in wrapped


def test_wrap_still_wraps_a_bare_body():
    """Regression guard: a bare body with no environment is still wrapped."""
    profile = get_profile("tikz")
    wrapped = profile._wrap("\\draw (0,0) -- (1,1);")
    assert wrapped.startswith("\\begin{tikzpicture}")
    assert wrapped.rstrip().endswith("\\end{tikzpicture}")


# --------------------------------------------------------------------------
# D-050  rewrite removed chemfig setters
# --------------------------------------------------------------------------

def test_deprecated_setters_rewritten_to_setchemfig():
    body, applied = rewrite_deprecated_setters(
        "\\setatomsep{2em}\\setbondoffset{1pt}\\setdoublesep{2pt}\n"
        "\\chemfig{A-B}"
    )
    assert "\\setchemfig{atom sep=2em}" in body
    assert "\\setchemfig{bond offset=1pt}" in body
    assert "\\setchemfig{double bond sep=2pt}" in body
    assert "\\setatomsep" not in body
    assert len(applied) == 3
    # The molecule body is untouched.
    assert "\\chemfig{A-B}" in body


def test_bond_style_value_is_rebraced():
    """A comma-bearing style value is braced so it does not split the key list."""
    body, applied = rewrite_deprecated_setters("\\setbondstyle{line width=1pt}")
    assert body == "\\setchemfig{bond style={line width=1pt}}"
    assert len(applied) == 1


def test_no_deprecated_setters_is_a_noop():
    body, applied = rewrite_deprecated_setters("\\chemfig{A-B-C}")
    assert body == "\\chemfig{A-B-C}"
    assert applied == ()
