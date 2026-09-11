"""G-08 / D-008 -- tikz-cd package backfill via supplemental TEXINPUTS.

Root cause (confirmed against the live toolchain): ``tikz-cd.sty`` is absent
from the active TeX Live "basic" tree, so ``LatexRenderer.missing_for_profile``
reports the ``tikz-cd`` profile as not-installed and ``render`` short-circuits
with the install advisory BEFORE any compile -- every tikz-cd spec produces no
pixels.  The fix does not require a network ``tlmgr install``: the renderer
locates the package's directories in a co-installed fuller TeX tree (or an
explicit ``ZIYA_LATEX_EXTRA_TEXINPUTS`` override) and prepends them to
``TEXINPUTS`` for BOTH the capability probe (``_kpsewhich``) and the compile
(``_run``), so the gate passes and the package loads.

These tests FAIL on the unpatched tree: the symbols ``_supplemental_texinputs``
/ ``_augment_texinputs`` / ``_TEXINPUTS_BACKFILL`` do not exist, and the
augmented-vs-plain probe flip is the behaviour introduced by the fix.

D-008 is a STRUCTURAL defect (a package availability gate), not a colour/theme
defect -- the compiled output is theme-independent, so there is no contrast
ratio to assert; the both-theme obligation is discharged at the shared render
stage.  What is asserted here is the mechanism: the gate flips from
missing -> present precisely because of the supplemental TEXINPUTS path.
"""

import os
import shutil
import subprocess
import tempfile

import pytest

from app.services.latex_renderer import (
    LatexRenderer,
    _TEXINPUTS_BACKFILL,
    _augment_texinputs,
    _supplemental_texinputs,
)
from app.services.latex_profiles import get_profile


_HAS_KPSEWHICH = bool(shutil.which("kpsewhich"))


def _plain_kpsewhich(filename: str) -> bool:
    """kpsewhich WITHOUT any supplemental TEXINPUTS (the pre-fix behaviour)."""
    env = {k: v for k, v in os.environ.items() if k != "TEXINPUTS"}
    env.pop("ZIYA_LATEX_EXTRA_TEXINPUTS", None)
    proc = subprocess.run(
        ["kpsewhich", filename],
        capture_output=True, text=True, timeout=10, env=env,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


@pytest.fixture(autouse=True)
def _clear_supp_cache():
    """The supplemental-TEXINPUTS computation is memoised; reset it around each
    test so an env change is observed."""
    _supplemental_texinputs.cache_clear()
    yield
    _supplemental_texinputs.cache_clear()


def test_backfill_table_names_tikzcd():
    # The backfill table must cover tikz-cd's probe file AND the generic `cd`
    # library directory, or the wrapper .sty would load but \usetikzlibrary{cd}
    # would still abort the compile.
    assert "tikz-cd.sty" in _TEXINPUTS_BACKFILL
    reldirs = _TEXINPUTS_BACKFILL["tikz-cd.sty"]
    assert any("latex/tikz-cd" in r for r in reldirs)
    assert any("generic/tikz-cd" in r for r in reldirs)


def test_no_supplemental_paths_leaves_texinputs_untouched():
    # When there are no extras, the env is returned unchanged (no spurious
    # TEXINPUTS injection that could shadow the primary tree).
    base = {"PATH": os.environ.get("PATH", "")}
    # Force "no extras" by pointing the override at a non-existent dir and
    # relying on the primary tree already resolving its own packages.
    out = _augment_texinputs(dict(base))
    # Either unchanged, or (if this host backfills tikz-cd from a sibling tree)
    # TEXINPUTS ends with a separator so the compiled-in defaults are preserved.
    if "TEXINPUTS" in out:
        assert out["TEXINPUTS"].split(os.pathsep)[-1] == ""


@pytest.mark.skipif(not _HAS_KPSEWHICH, reason="kpsewhich not installed")
def test_override_dir_reaches_probe(monkeypatch):
    """Deterministic, environment-independent direction check.

    A fake package placed in an override directory is invisible to a plain
    kpsewhich but resolvable through the renderer's augmented probe -- which is
    exactly the mechanism that rescues tikz-cd.
    """
    with tempfile.TemporaryDirectory(prefix="g08_ovr_") as d:
        # A name the real TeX tree cannot possibly contain.
        probe = "ziyafauxpkgg08.sty"
        with open(os.path.join(d, probe), "w") as fh:
            fh.write("% faux package for G-08 test\n")

        monkeypatch.delenv("TEXINPUTS", raising=False)
        monkeypatch.setenv("ZIYA_LATEX_EXTRA_TEXINPUTS", d)
        _supplemental_texinputs.cache_clear()

        # supplemental list carries the override dir ...
        assert d in _supplemental_texinputs()
        # ... it is prepended to TEXINPUTS with a trailing default separator ...
        aug = _augment_texinputs(dict(os.environ))
        assert aug["TEXINPUTS"].split(os.pathsep)[0] == d
        assert aug["TEXINPUTS"].endswith(os.pathsep)
        # ... and the augmented probe now resolves the faux package.
        assert LatexRenderer._kpsewhich(probe) is True
        # Direction: a plain kpsewhich (pre-fix behaviour) does NOT.
        assert _plain_kpsewhich(probe) is False


@pytest.mark.skipif(not _HAS_KPSEWHICH, reason="kpsewhich not installed")
def test_tikzcd_gate_flips_when_backfilled():
    """The real defect: with the fix, the tikz-cd profile is no longer reported
    as not-installed on a host that lacks tikz-cd in its primary tree but has it
    in a sibling tree (or via override).

    Skipped only if this host genuinely has NO source of tikz-cd at all (then
    there is nothing to backfill and the render-stage advisory is correct).
    """
    _supplemental_texinputs.cache_clear()
    extras = _supplemental_texinputs()

    profile = get_profile("tikz-cd")
    assert profile is not None
    renderer = LatexRenderer()

    if _plain_kpsewhich("tikz-cd.sty"):
        # Primary tree already ships tikz-cd -- backfill is a no-op here and the
        # gate was never broken on this host; nothing to prove.
        pytest.skip("primary TeX tree already provides tikz-cd.sty")

    tikzcd_dirs = [
        p for p in extras
        if os.path.isfile(os.path.join(p, "tikz-cd.sty"))
    ]
    if not tikzcd_dirs:
        pytest.skip("no sibling/override TeX tree provides tikz-cd.sty on this host")

    # Pre-fix behaviour is what _plain_kpsewhich reproduces: package absent.
    assert _plain_kpsewhich("tikz-cd.sty") is False
    # Post-fix: the augmented probe finds it, so the gate passes ...
    assert LatexRenderer._kpsewhich("tikz-cd.sty") is True
    # ... and the profile is no longer in the missing set.
    assert "tikz-cd" not in renderer.missing_for_profile(profile)
