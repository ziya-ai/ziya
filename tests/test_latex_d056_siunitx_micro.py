"""D-056 / G-6cc113 -- siunitx ``\\micro`` must not route through the absent
TS1 companion font (circuitikz-w1-03).

Root cause (confirmed against the profile code and the siunitx internals): a
circuitikz body such as ``\\SI{10}{\\micro\\farad}`` -- microfarads, the reason
the circuitikz profile loads siunitx at all -- typesets the micro sign via
siunitx's internal ``\\SIUnitSymbolMicro``, which expands to the TS1 /
text-companion glyph ``\\textmu``.  The Type1 font backing TS1 (``tcrm*``) is
absent from a minimal TeX Live tree, so the compile aborts with "Font tcrm1000
at 600 not found" and produces NO image, for an otherwise valid schematic.

The fix (``LatexProfile.build_document``): after siunitx is loaded, redefine
``\\SIUnitSymbolMicro`` so the micro sign renders from the MATHS fonts (cmmi's
``\\mu``, present in every install) instead of TS1 -- the same sidestep
``latex_unicode.transliterate`` already uses for a raw ``U+00B5``.  Guarded by
``\\ifdefined`` so it is a no-op when siunitx is not loaded.

D-056 is a STRUCTURAL defect (a fatal missing-font abort that never reaches the
rasteriser), so the output is theme-independent: the redefinition is emitted
identically on the light and dark document paths, which the both-theme test
below asserts explicitly.  These tests FAIL on the unpatched profile -- it emits
no ``\\SIUnitSymbolMicro`` redefinition at all.
"""
from app.services.latex_profiles import get_profile

_MICRO_BODY = (
    r"\draw (0,0) to[C=\SI{10}{\micro\farad}] (3,0);"
)
_REDEF = r"\RenewDocumentCommand{\SIUnitSymbolMicro}{}{\ensuremath{\mu}}"


def _doc(theme: str) -> str:
    profile = get_profile("circuitikz")
    assert profile is not None
    return profile.build_document(_MICRO_BODY, standalone=True, fmt="png", theme=theme)


def test_micro_redef_present_both_themes():
    # The heart of the fix: micro is rerouted to the maths font in BOTH themes.
    for theme in ("light", "dark"):
        doc = _doc(theme)
        assert _REDEF in doc, f"micro redefinition missing on {theme} path"
        # It must be guarded so it is safe when siunitx is absent, and it must
        # route through \ensuremath (maths font), never leave micro on TS1.
        assert r"\ifdefined\SIUnitSymbolMicro" in doc
        assert r"\ensuremath{\mu}" in doc


def test_micro_redef_after_siunitx_load():
    # Order matters: redefining \SIUnitSymbolMicro before \usepackage{siunitx}
    # would be clobbered (or undefined). The redef must follow the siunitx load.
    doc = _doc("light")
    assert "{siunitx}" in doc
    assert doc.index("{siunitx}") < doc.index(_REDEF)


def test_micro_redef_emitted_even_when_siunitx_optional():
    # A profile that gets siunitx only via the global optional load still emits
    # the guarded redefinition (harmless no-op if the package is absent), so the
    # micro rescue is uniform across engines rather than special-cased.
    profile = get_profile("pgfplots")
    assert profile is not None
    doc = profile.build_document(
        r"\begin{axis}\addplot coordinates {(0,0)};\end{axis}",
        standalone=True, fmt="png", theme="light",
    )
    assert r"\ifdefined\SIUnitSymbolMicro" in doc
    assert _REDEF in doc
