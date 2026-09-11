r"""G-TIKZ-REPAIR backlog guards (iteration 8).

Every *repairable* defect in the G-TIKZ-REPAIR group was already remediated in
the baseline tree ahead of the (stale) triage hypotheses, and each is covered
by a dedicated test-first suite -- this module does NOT duplicate those:

  * D-201 explicit-width-height-ignored        -> test_latex_g66_size_forwarding
  * D-202 aspect-ratio-collapses-tick-labels   -> test_latex_g07_aspect_legibility_floor
  * D-203 pgfmath-dimen-overflow-at-high-index -> test_latex_g06_tikz_lint (trig clamp)
  * D-210 inline-usetikzlibrary-stripped       -> test_latex_g05_statement_structure
  * D-211 trailing-serialised-newline          -> test_latex_g05_statement_structure
  * D-213 missing-path-semicolon               -> test_latex_g05_statement_structure

What this module DOES pin is the two defects the group deliberately leaves
unfixed (wont-fix), so a future well-meaning edit cannot silently turn a
"declined, and honestly so" outcome into a corrupting one:

  * D-214 unbalanced-label-brace / unclosed-scope: the safe repair needs a real
    node-label tokeniser to know WHERE the missing ``}`` belongs; a blind
    append at end-of-body would make node (a)'s label swallow the rest of the
    picture -- a knowingly-wrong render worse than the error.  ``autofix`` must
    therefore NOT attempt brace/scope balancing (it still safely does the
    semicolon insertion it CAN prove), and must never raise.

  * D-204 non-latin-script-fatal: CJK / Arabic / Hebrew / Cyrillic have no
    maths-font equivalent, so ``transliterate`` leaves them untouched (a lossy
    romanisation would corrupt meaning and still would not rescue the
    same-picture CJK).  Rendering them needs CJK fonts or a Unicode engine
    (xelatex/lualatex) that this pdfLaTeX environment does not provide -- an
    environment/engine change out of the repair contract's "minimal, targeted"
    scope.  Pin that the transliterator does not pretend otherwise.

All defects here are structural/recovery and theme-independent (DPI selection,
brace scanning and script support are all colour-blind), so there is no
per-theme assertion to make -- the same behaviour holds for the light and dark
raster surfaces alike.
"""

from app.utils.tikz_lint import autofix
from app.utils.latex_unicode import transliterate


# --------------------------------------------------------------------------
# D-214 (wont-fix): brace/scope balancing is deliberately NOT performed.
# --------------------------------------------------------------------------
# tikz-w4-11: an unclosed brace in a node label ({Cache {hot}) plus an
# unbalanced \begin{scope}.
W4_11 = (
    r"\fill[black!88] (-0.5,-0.5) rectangle (8.00,3.40);" "\n"
    r"\node[white,anchor=west,font=\bfseries] at (-0.2,2.85) {Nesting off by one};" "\n"
    r"\begin{scope}[white,thick]" "\n"
    r"\node[draw=white,text=white,fill=blue!60!black] (a) at (1,1) {Cache {hot};" "\n"
    r"\node[draw=white,text=white,fill=green!50!black] (b) at (4,1) {Store};" "\n"
    r"\begin{scope}[xshift=10pt]" "\n"
    r"\node[draw=white,text=white,fill=red!60!black] (c) at (7,1) {Log};" "\n"
    r"\draw (a)--(b);" "\n"
    r"\draw (b)--(c);" "\n"
    r"\end{scope}" "\n"
)


def test_w4_11_autofix_never_raises_and_returns_str():
    out, applied, warnings = autofix(W4_11)
    assert isinstance(out, str)
    assert isinstance(applied, tuple)
    assert isinstance(warnings, tuple)


def test_w4_11_brace_imbalance_is_not_silently_balanced():
    # The declined decision: autofix must NOT add a phantom closing brace.  If
    # a future edit adds an unsafe blind brace-balancer, the net imbalance would
    # drop to 0 and this guard fires -- forcing that change to prove it places
    # the brace correctly (a tokeniser) rather than appending at end-of-body.
    out, _, _ = autofix(W4_11)
    imbalance = out.count("{") - out.count("}")
    assert imbalance == W4_11.count("{") - W4_11.count("}") == 1


def test_w4_11_scope_imbalance_is_not_silently_balanced():
    out, _, _ = autofix(W4_11)
    assert out.count(r"\begin{scope}") == 2
    assert out.count(r"\end{scope}") == 1  # NOT auto-closed


def test_w4_11_still_gets_the_semicolon_it_can_prove():
    # Declining brace balancing does not disable the orthogonal, provably-safe
    # semicolon insertion: the final \draw/\end run reaches end-of-body with a
    # pending statement, so a ';' is added.  (The body still will not compile --
    # the brace defect is fatal -- but the safe pass is not suppressed.)
    _, applied, _ = autofix(W4_11)
    assert any("semicolon" in note for note in applied)


# --------------------------------------------------------------------------
# D-204 (wont-fix): non-Latin scripts have no maths-font transliteration.
# --------------------------------------------------------------------------
# A romanisation would be lossy and would still not rescue the CJK/Arabic in the
# SAME picture, so the transliterator leaves these codepoints in place and the
# render stays a font-blocked fatal until CJK fonts / a Unicode engine exist.
_UNSUPPORTED_SCRIPT_CHARS = {
    "CJK-han": "\u65e5",       # 日
    "CJK-kana": "\u30e9",      # ラ
    "Arabic": "\u0645",        # م
    "Hebrew": "\u05e9",        # ש
    "Cyrillic": "\u041f",      # П
}


def test_non_latin_scripts_left_untouched_by_transliterate():
    for name, ch in _UNSUPPORTED_SCRIPT_CHARS.items():
        out, applied = transliterate(ch)
        assert out == ch, f"{name} was altered: {out!r}"
        assert applied == (), f"{name} claimed a transliteration: {applied}"


def test_transliterate_still_handles_supported_symbols_alongside():
    # Guard the boundary of the wont-fix: the DECLINE is specific to scripts
    # with no maths equivalent, not a blanket "leave unicode alone".  A degree
    # sign in the same body must still be rescued.
    body = "CJK \u65e5\u672c \u2014 90\u00b0"      # Han + em-dash-ish + degree
    out, applied = transliterate(body)
    assert "\u65e5" in out                          # Han untouched
    assert "\u00b0" not in out                       # degree transliterated away
    assert any("00B0" in note for note in applied)
