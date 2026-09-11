r"""
Recovery regression tests for fix group G-05 (server-side TeX statement-structure
repair, defect D-005).

Each defect is a near-miss body a model routinely emits that aborts the LaTeX
compile (or ships debris) purely because a statement was not terminated the way
TeX expects.  The fixes are structural, theme-independent recoveries; where a
fix reaches the assembled document it is asserted for BOTH themes so a
theme-specific regression could not slip through.

Defects exercised:

  * tikz-w4-03 / circuitikz-w4-09 (missing statement semicolons): a ``\node`` /
    ``\draw`` path not terminated by ``;`` before the next statement aborts with
    "Did you forget a semicolon?".  ``_insert_missing_semicolons`` inserts them,
    keyed on statement-start control words at brace-depth zero.

  * tikz-w4-15 (trailing serialised ``\n``): a JSON-serialised multiline body
    carries a trailing literal ``\n`` after its last statement which the
    mid-body restorer's lookahead cannot reach, leaving a fatal stray control
    sequence.

  * circuitikz-w4-10 (orphan trailing ``\end{circuitikz}``): the body closes an
    environment it did not open; the profile then double-``\end``s and aborts.

  * tikz-w4-10 (inline ``\usetikzlibrary`` stripped): the sanitiser drops the
    body's ``\usetikzlibrary{shapes.geometric}``, leaving ``\node[diamond]`` an
    undefined key; the requested library is now merged into the preamble.

  * chemfig-w4-12 (stray statement ``;``): a semicolon borrowed from another
    dialect after ``\chemfig{...}`` is dropped (chemfig has no statement ``;``).

Every assertion is written to FAIL against the unpatched tree (new symbols,
untouched behaviour) and pass with the fix, and each includes a direction check
so a test that would pass against unpatched code certifies the bug, not the fix.

These import the REAL modules under test, never a re-implementation.
"""
import pytest

from app.utils.tikz_lint import autofix as tikz_autofix, _insert_missing_semicolons
from app.utils.circuitikz_lint import autofix as circuitikz_autofix
from app.utils.chemfig_lint import strip_statement_terminators
from app.services.latex_renderer import LatexRenderer
from app.services.latex_profiles import get_profile


# ---------------------------------------------------------------------------
# tikz-w4-03: missing statement-terminating semicolons
# ---------------------------------------------------------------------------
W4_03 = (
    r"\fill[black!88] (-0.5,-0.5) rectangle (8.00,3.20);" "\n"
    r"\node[white,anchor=west,font=\bfseries] at (-0.2,2.65) {Missing semicolons};" "\n"
    r"\node[draw=white,text=white,fill=blue!60!black] (a) at (1,1) {Read}" "\n"
    r"\node[draw=white,text=white,fill=green!50!black] (b) at (4,1) {Map};" "\n"
    r"\node[draw=white,text=white,fill=red!60!black] (c) at (7,1) {Write};" "\n"
    r"\draw[white,thick] (a)--(b)" "\n"
    r"\draw[white,thick] (b)--(c);" "\n"
)


def test_tikz_missing_semicolons_inserted():
    # Direction: two statements ({Read}, first \draw) run straight into the
    # next statement with no terminator -- the fatal case.
    assert "{Read}\n\\node" in W4_03
    assert "(a)--(b)\n\\draw" in W4_03

    fixed, applied, _ = tikz_autofix(W4_03)

    # The two unterminated statements now carry a ';' before the next command.
    assert "{Read};" in fixed or "{Read}\n;" in fixed or "{Read} ;" in fixed
    assert "(a)--(b);" in fixed or "(a)--(b)\n;" in fixed
    # Exactly the two missing terminators were added (the body had 5 ';', now 7).
    assert fixed.count(";") == W4_03.count(";") + 2
    assert any("semicolon" in note for note in applied)


def test_tikz_semicolon_inserter_is_noop_on_valid_body():
    # A well-formed body terminates every statement; the inserter must be
    # byte-identical (this is the safety contract that lets it run always).
    valid = (
        r"\draw (0,0) -- (1,0)" "\n"
        r"      -- (1,1) -- cycle;" "\n"
        r"\node (a) at (2,2) {A};" "\n"
        r"\node (b) at (3,3) {B};" "\n"
    )
    out, applied = _insert_missing_semicolons(valid)
    assert out == valid
    assert applied == ()


def test_tikz_semicolon_inserter_ignores_semicolon_in_label_and_math():
    # A ';' inside a node label (brace depth >= 1) or inside $...$ must NOT be
    # treated as a terminator, so the following \node is still seen as needing
    # one.  Also proves a label ';' is never mistaken for a real terminator.
    body = (
        r"\node (a) at (0,0) {alpha; beta}" "\n"
        r"\node (b) at (1,1) {gamma};" "\n"
    )
    out, applied = _insert_missing_semicolons(body)
    # The label ';' (depth >= 1) is preserved AND did not terminate the first
    # \node, so a real terminator was inserted before the second \node: the
    # result carries three ';' (inner label, inserted, gamma terminator).
    assert out.count(";") == 3
    assert "{alpha; beta}" in out          # label punctuation preserved verbatim
    assert ";\\node (b)" in out            # a terminator was inserted, not the label ';'


# ---------------------------------------------------------------------------
# circuitikz-w4-09: missing semicolons on every \draw / \node
# ---------------------------------------------------------------------------
CK_W4_09 = (
    r"\definecolor{plate}{HTML}{16324A}" "\n"
    r"\definecolor{plateink}{HTML}{F2F6FA}" "\n"
    r"\definecolor{plateaccent}{HTML}{5FD4E4}" "\n"
    r"\fill[plate] (-0.9,-1.5) rectangle (7.3,1.9);" "\n"
    r"\draw[color=plateink] (0,0) to[R=$R_1$] (2.4,0) node[ocirc]{}" "\n"
    r"\draw[color=plateink] (0,-1.1) to[C=$C_1$] (2.4,-1.1) node[ocirc]{}" "\n"
    r"\draw[color=plateaccent] (0,0) to[short] (0,-1.1)" "\n"
    r"\node[plateink] at (2.4,1.4) {Missing semicolons}" "\n"
)


def test_circuitikz_missing_semicolons_inserted():
    # Direction: three \draw and the final \node are all unterminated.
    assert "node[ocirc]{}\n\\draw" in CK_W4_09
    fixed, applied, _ = circuitikz_autofix(CK_W4_09)
    # The \fill already had one ';'; the three draws + final node needed four
    # more (three before the next statement, one at end of body).
    assert fixed.count(";") == CK_W4_09.count(";") + 4
    assert any("semicolon" in note for note in applied)
    # The color \definecolor lines and the R/C labels are untouched.
    assert r"to[R=$R_1$]" in fixed and r"to[C=$C_1$]" in fixed


# ---------------------------------------------------------------------------
# tikz-w4-15: trailing serialised '\n'
# ---------------------------------------------------------------------------
# The whole body is a single physical line; every newline arrived as the two
# chars '\'+'n'.  The final one sits at end-of-body with no following command.
W4_15 = (
    r"\fill[black!88] (-0.5,-0.5) rectangle (8.00,3.20);\n"
    r"\node[white] (a) at (1,1) {One};\n"
    r"\draw[white,thick] (a)--(b);\n"
)


def test_tikz_trailing_serialised_newline_stripped():
    # Direction: the body ends with a stray literal '\n' after the last ';'.
    assert W4_15.rstrip().endswith(r";\n")
    assert "\n" not in W4_15               # no REAL newline: single-line serialised

    fixed, applied, _ = tikz_autofix(W4_15)

    # Internal '\n's became real newlines ...
    assert "\n" in fixed
    assert r"\n\node" not in fixed
    # ... and the trailing stray '\n' control sequence is gone (it would abort).
    assert not fixed.rstrip().endswith(r"\n")
    assert r"\draw" in fixed               # genuine command survived


# ---------------------------------------------------------------------------
# circuitikz-w4-10: orphan trailing \end{circuitikz}
# ---------------------------------------------------------------------------
CK_W4_10 = (
    r"\definecolor{plate}{HTML}{16324A}" "\n"
    r"\draw[color=plate] (0,0) to[R=$R_1$] (2.4,0)" "\n"
    r"  to[C=$C_1$] (4.8,0) node[ocirc]{};" "\n"
    r"\node[plate] at (2.4,1.4) {Off-by-one nesting};" "\n"
    r"\end{circuitikz}" "\n"
)


def test_orphan_trailing_end_environment_stripped():
    # Direction: the body carries an \end{circuitikz} with no matching \begin.
    assert r"\end{circuitikz}" in CK_W4_10
    assert r"\begin{circuitikz}" not in CK_W4_10

    sanitized = LatexRenderer._sanitize_input(CK_W4_10)
    assert r"\end{circuitikz}" not in sanitized
    # The real content is preserved.
    assert r"to[R=$R_1$]" in sanitized

    # And the profile's wrap then produces a balanced environment for BOTH
    # themes (the fix is theme-independent, but assert both so a theme-specific
    # regression is impossible).
    profile = get_profile("circuitikz")
    for theme in ("light", "dark"):
        doc = profile.build_document(sanitized, standalone=True, theme=theme)
        assert doc.count(r"\begin{circuitikz}") == doc.count(r"\end{circuitikz}")


def test_balanced_environment_body_is_untouched():
    # A body that opens AND closes its own environment must be left alone
    # (balanced counts): the orphan strip is guarded and subtractive-only.
    balanced = (
        r"\begin{circuitikz}" "\n"
        r"\draw (0,0) to[R] (2,0);" "\n"
        r"\end{circuitikz}" "\n"
    )
    assert LatexRenderer._sanitize_input(balanced).count(r"\end{circuitikz}") == 1


# ---------------------------------------------------------------------------
# tikz-w4-10: inline \usetikzlibrary preserved into the preamble
# ---------------------------------------------------------------------------
W4_10 = (
    r"\tikzstyle{box}=[draw=white,text=white,fill=blue!60!black,rounded corners]" "\n"
    r"\usetikzlibrary{shapes.geometric}" "\n"
    r"\fill[black!88] (-0.5,-0.5) rectangle (8.50,3.40);" "\n"
    r"\node[box] (a) at (1,1) {Parse};" "\n"
    r"\node[diamond,draw=white,text=white,fill=orange!70!black,aspect=2] (c) at (7.4,1) {Gate};" "\n"
    r"\draw[white,thick,->] (a)--(c);" "\n"
)


def test_inline_usetikzlibrary_extracted_and_merged():
    # Direction: the sanitiser strips the body's \usetikzlibrary line ...
    sanitized = LatexRenderer._sanitize_input(W4_10)
    assert r"\usetikzlibrary{shapes.geometric}" not in sanitized

    # ... but the requested library is captured from the raw body ...
    libs = LatexRenderer._extract_requested_libraries(W4_10)
    assert "shapes.geometric" in libs

    # ... and is merged into the profile preamble alongside the profile's own
    # libraries, so the `diamond` shape resolves.  Assert BOTH themes.
    profile = get_profile("tikz")
    for theme in ("light", "dark"):
        doc = profile.build_document(
            sanitized, standalone=True, theme=theme,
            extra_libraries=libs)
        # A single \usetikzlibrary line that contains BOTH a profile default
        # and the requested library.
        lib_lines = [ln for ln in doc.splitlines()
                     if ln.startswith(r"\usetikzlibrary{")]
        assert len(lib_lines) == 1
        assert "shapes.geometric" in lib_lines[0]
        assert "arrows.meta" in lib_lines[0]        # profile default retained


def test_extra_library_deduplicated():
    # A library the profile already declares must not be emitted twice.
    profile = get_profile("tikz")
    doc = profile.build_document(
        r"\draw (0,0);", standalone=True,
        extra_libraries=("calc", "shapes.geometric"))
    lib_lines = [ln for ln in doc.splitlines()
                 if ln.startswith(r"\usetikzlibrary{")]
    assert len(lib_lines) == 1
    assert lib_lines[0].count("calc") == 1


def test_build_document_extra_libraries_defaults_empty():
    # Backwards-compatible: omitting extra_libraries reproduces the prior
    # single profile-library line exactly.
    profile = get_profile("tikz")
    doc = profile.build_document(r"\draw (0,0);", standalone=True)
    lib_lines = [ln for ln in doc.splitlines()
                 if ln.startswith(r"\usetikzlibrary{")]
    assert len(lib_lines) == 1
    assert "shapes.geometric" not in lib_lines[0]


# ---------------------------------------------------------------------------
# chemfig-w4-12: stray statement ';' borrowed from another dialect
# ---------------------------------------------------------------------------
CHEM_W4_12 = "\\chemfig{H-C\n  -[:30]OH};\n\\chemfig{C=O};"


def test_chemfig_stray_semicolons_dropped():
    # Direction: each \chemfig{...} is followed by a stray statement ';'.
    assert CHEM_W4_12.count(";") == 2
    fixed, applied = strip_statement_terminators(CHEM_W4_12)
    # Both top-level terminators removed; the molecules themselves are intact.
    assert fixed.count(";") == 0
    assert r"\chemfig{H-C" in fixed
    assert r"\chemfig{C=O}" in fixed
    assert any("terminator" in note for note in applied)


def test_chemfig_semicolon_inside_brace_preserved():
    # A ';' inside a caption/label brace is legitimate punctuation and must be
    # kept; only a top-level (depth-0) terminator is dropped.
    body = r"\chemname{\chemfig{C=O}}{Formaldehyde; a gas};"
    fixed, applied = strip_statement_terminators(body)
    assert "{Formaldehyde; a gas}" in fixed     # inner ';' preserved
    assert fixed.rstrip().endswith("}")          # trailing top-level ';' dropped
    assert fixed.count(";") == 1                 # exactly the inner one remains
