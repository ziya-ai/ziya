"""pgfplots profile: registry entry, document assembly, and lint routing.

Written alongside the profile itself; each test pins a seam the new type
crosses (registry -> document assembly -> lint dispatch).  All of these FAIL
before the "pgfplots" entry lands in app/services/latex_profiles.py, which is
the point: they certify the wiring, not just the halves.
"""
import re

from app.services.latex_profiles import (
    LATEX_DIAGRAM_TYPES,
    PROFILES,
    get_profile,
)

AXIS_BODY = (
    "\\begin{axis}[xlabel={$t$}, ylabel={$e^{-t}\\sin t$}]\n"
    "\\addplot[domain=0:10, samples=200, smooth] {exp(-x)*sin(deg(x))};\n"
    "\\end{axis}"
)


def test_pgfplots_profile_is_registered():
    assert "pgfplots" in PROFILES
    assert "pgfplots" in LATEX_DIAGRAM_TYPES
    assert get_profile(" PGFplots ") is PROFILES["pgfplots"]


def test_pgfplots_declares_install_metadata():
    # pgfplots is its own TeX Live package (it does NOT ship in pgf), so the
    # actionable not-installed message depends on this metadata being right.
    p = PROFILES["pgfplots"]
    assert "pgfplots" in p.tl_packages
    assert "pgfplots.sty" in p.probe_files


def test_axis_body_is_wrapped_in_tikzpicture():
    # A model usually emits a bare \begin{axis}...\end{axis}; that environment
    # only exists inside a tikzpicture, so the profile must supply the wrap.
    doc = PROFILES["pgfplots"].build_document(AXIS_BODY, standalone=True)
    assert doc.index("\\begin{tikzpicture}") < doc.index("\\begin{axis}")
    assert doc.index("\\end{axis}") < doc.index("\\end{tikzpicture}")


def test_full_tikzpicture_body_is_not_double_wrapped():
    # Bodies that already open tikzpicture must pass through: double-wrapping
    # is a fatal mismatched-environment error.
    body = "\\begin{tikzpicture}\n" + AXIS_BODY + "\n\\end{tikzpicture}"
    doc = PROFILES["pgfplots"].build_document(body, standalone=True)
    assert doc.count("\\begin{tikzpicture}") == 1


def test_preamble_sets_compat_and_loads_common_libraries():
    doc = PROFILES["pgfplots"].build_document(AXIS_BODY, standalone=True)
    # Without a compat level pgfplots keeps pre-1.3 axis-label placement and
    # warns on every compile; `newest` never names a version an older install
    # doesn't know.
    assert "\\pgfplotsset{compat=newest}" in doc
    for lib in ("fillbetween", "statistics", "polar", "dateplot", "groupplots"):
        assert lib in doc, f"pgfplots library {lib} not preloaded"


def test_xcolor_name_sets_load_before_pgfplots():
    # Same D-004 contract as every other profile: xcolor[svgnames,dvipsnames]
    # must win the once-only load so CSS/SVG colour names resolve.
    doc = PROFILES["pgfplots"].build_document(AXIS_BODY, standalone=True)
    assert doc.index("svgnames,dvipsnames") < doc.index("\\usepackage{pgfplots}")


def test_lint_routing_includes_pgfplots():
    # The tikz structural-recovery pass (literal \n restoration, pgfmath
    # fixes) applies to the whole TikZ family.  The dispatch is inline in
    # render(), so this checks the source the same way the frontend guard
    # test does -- a profile missing from the tuple silently skips lint.
    import inspect
    from app.services import latex_renderer
    src = inspect.getsource(latex_renderer)
    tuples = re.findall(r"profile\.key in \(([^)]*)\)", src)
    tikz_family = [t for t in tuples if "tikz" in t]
    assert tikz_family, "tikz-family lint dispatch tuple not found"
    assert any("pgfplots" in t for t in tikz_family), (
        "pgfplots missing from the tikz-family lint dispatch"
    )
