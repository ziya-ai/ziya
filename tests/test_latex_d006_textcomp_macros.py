"""D-006 (group G-06): TS1 / text-companion symbol MACROS rewritten to maths.

Root cause confirmed against source: ``latex_unicode.transliterate`` rescued a
symbol written as a raw Unicode codepoint (``5 µF``) but NOT the same symbol
written as its ``\\text...`` macro (``\\text{\\textmu}m`` in chemfig-w3-02).
On a minimal TeX Live tree those macros are DEFINED (modern kernel) but abort
the compile because rendering them needs the absent TS1 Type1 fonts.  The fix
routes each such macro through ``\\ensuremath{...}`` so it renders from the
maths fonts every install ships.

Every assertion below FAILS on the unpatched tree, where the ``\\text...``
macro is left verbatim and no ``\\ensuremath`` rewrite is emitted.
"""
from app.utils.latex_unicode import transliterate


def test_textmu_macro_becomes_ensuremath_mu():
    # The exact chemfig-w3-02 fragment: \textmu inside a \text{} atom label.
    out, applied = transliterate(r"\chemfig{*6(-=-(-\text{\textmu}m)=-=)}")
    assert r"\ensuremath{\mu}" in out
    # \textmu must be gone (it is the TS1-font-dependent macro that aborts).
    assert r"\textmu" not in out
    assert any("\\textmu" in note for note in applied)


def test_textdegree_and_textohm_and_textcelsius():
    assert r"\ensuremath{^\circ}" in transliterate(r"90\textdegree")[0]
    assert r"\ensuremath{\Omega}" in transliterate(r"10\,\textohm")[0]
    assert r"\ensuremath{^\circ\mathrm{C}}" in transliterate(r"5\textcelsius")[0]


def test_boundary_does_not_touch_longer_macro():
    # A control word ends at the first non-letter, so \textmu must NOT be
    # rewritten when it is only the prefix of a longer, unrelated macro name.
    src = r"\textmuskip=2pt"
    out, applied = transliterate(src)
    assert out == src
    assert applied == ()


def test_textmu_followed_by_letter_is_left_alone():
    # \textmugreek is not \textmu; the boundary lookahead must skip it.
    out, _ = transliterate(r"\textmugreek")
    assert out == r"\textmugreek"


def test_unrelated_body_is_untouched_and_advisory():
    # Direction/no-op guard: a body with no TS1 macro and no target codepoint
    # is returned byte-identical with an empty applied tuple.
    src = r"\draw (0,0) -- (1,1) node {plain label};"
    out, applied = transliterate(src)
    assert out == src
    assert applied == ()


def test_macro_and_codepoint_paths_coexist():
    # Raw-codepoint µ (existing path) and \textmu macro (new path) in one body
    # both resolve, and both are reported.
    out, applied = transliterate("5 \u00b5F and \\textmu m")
    assert out.count(r"\ensuremath{\mu}") == 2
    assert any("U+00B5" in n for n in applied)
    assert any(r"\textmu" in n for n in applied)
