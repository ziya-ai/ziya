"""D-029 (group G-4d3ace): chemfig-w3-02 aborted on the TS1 font tcrm1000.

Signature: ts1-textcomp-font-tcrm1000-missing-fatal.

The chemfig spec chemfig-w3-02 puts an explicit ``\\textmu`` macro inside a
``\\text{}`` atom label of an aromatic ring substituent
(``\\chemfig{*6(-=-(-\\text{\\textmu}m)=-=)}``).  On a minimal TeX Live tree
that macro is DEFINED (modern kernel) but typesetting it pulls the TS1 /
text-companion Type1 font ``tcrm1000``, which is absent -- so pdflatex aborts
with "Font tcrm1000 at 600 not found" and produces NO image in either theme.
The wave-3 control (the first structure: ``\\ss \\ae \\o \\AA \\c{c}``) renders
fine because those diacritics live in the always-present cm text fonts, which
isolated the fault to the TS1 encoding.

Real cause vs. triage: the triage listed installing cm-super / TS1 fonts OR a
preamble mapping as options.  The confirmed fix is the SECOND, already carried
by ``latex_unicode.transliterate`` (its ``_MACRO_TRANSLITERATIONS`` table,
introduced for the shared D-006 root cause): the explicit ``\\text...`` macro is
rewritten to ``\\ensuremath{...}`` so the glyph renders from the maths fonts
(cmmi/cmsy) every install ships, sidestepping the missing TS1 fonts entirely.
No texlive change and no per-spec special-casing is needed.

Both assertions below FAIL on an unpatched tree, where ``\\textmu`` is left
verbatim and re-triggers the fatal tcrm1000 lookup.  The theme dimension is
covered by construction: the rewrite is a source transform applied before the
compile and is theme-independent, so the same transliterated body is what the
renderer hands to pdflatex for BOTH light and dark; the later render stage
re-verifies chemfig-w3-02 in both themes.
"""
from app.utils.latex_unicode import transliterate


# The exact chemfig-w3-02 definition (both structures), verbatim.
_SPEC_BODY = (
    r"\chemfig{\text{\ss}-[:30]\text{\ae}-[:-30]\text{\o}-[:30]"
    r"\text{\AA}-[:-30]\text{\c{c}}}"
    "\n\n\\vspace{4mm}\n\n"
    r"\chemfig{*6(-=-(-\text{\textmu}m)=-=)}"
)


def test_d029_textmu_ts1_macro_is_defused():
    """The \\textmu that hit tcrm1000 must be rewritten to a maths-font glyph."""
    out, applied = transliterate(_SPEC_BODY)
    # The TS1-font-dependent macro is gone...
    assert r"\textmu" not in out
    # ...replaced by the maths-mode equivalent that needs no TS1 font.
    assert r"\text{\ensuremath{\mu}}" in out
    # And the rewrite is reported (advisory contract), naming the macro.
    assert any(r"\textmu" in note for note in applied)


def test_d029_diacritic_control_structure_is_untouched():
    """The fix is TARGETED: the wave-3 diacritic control atoms are unchanged.

    \\ss \\ae \\o \\AA \\c{c} render from the cm text fonts and were never the
    fault, so transliterate must leave that first structure byte-identical --
    it neither corrupts a working atom nor special-cases the whole spec.
    """
    control = (
        r"\chemfig{\text{\ss}-[:30]\text{\ae}-[:-30]\text{\o}-[:30]"
        r"\text{\AA}-[:-30]\text{\c{c}}}"
    )
    out, applied = transliterate(control)
    assert out == control
    assert applied == ()
