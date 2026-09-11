"""D-227 (group G-29d67c): tikz-cd renders via the sibling-tree TEXINPUTS
backfill (D-215) when tikz-cd.sty is absent from the active TeX tree.

The tikz-cd profile was short-circuiting with the install advisory before any
compile, because ``tikz-cd.sty`` is absent from a "basic" scheme.  The D-215
backfill locates the package dirs in a co-installed sibling tree (or the
``ZIYA_LATEX_EXTRA_TEXINPUTS`` override) and prepends them to TEXINPUTS for
both the capability probe and the compile.  This locks that the probe no longer
reports the package missing and a minimal diagram renders in BOTH themes.
"""
import pytest

from app.services.latex_profiles import get_profile
from app.services.latex_renderer import LatexRenderer, latex_renderer


def test_profile_probe_finds_tikzcd():
    """missing_for_profile must be empty -- otherwise render() short-circuits."""
    r = LatexRenderer()
    cap = r.probe()
    if not cap.available:
        pytest.skip("no LaTeX toolchain available")
    profile = get_profile("tikz-cd")
    # If tikz-cd is genuinely unavailable in every tree, the environment cannot
    # exercise this defect; skip rather than fail spuriously.
    if not r._kpsewhich("tikz-cd.sty"):
        pytest.skip("tikz-cd.sty not resolvable in any tree on this host")
    assert r.missing_for_profile(profile) == ()


def test_tikzcd_renders_both_themes():
    cap = latex_renderer.probe()
    if not cap.available or not latex_renderer._kpsewhich("tikz-cd.sty"):
        pytest.skip("tikz-cd not available on this host")
    body = r"A \arrow[r] & B \arrow[d] \\ C & D"
    for theme in ("light", "dark"):
        res = latex_renderer.render("tikz-cd", body, fmt="png", theme=theme,
                                    use_cache=False)
        assert res.ok, f"{theme}: {res.error_kind}: {res.error}"
        assert res.content
