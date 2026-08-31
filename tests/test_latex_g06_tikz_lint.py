r"""
Regression tests for fix group G-06 (server-side TikZ / tikz-cd recovery).

Before this group the LaTeX renderer dispatched a structural lint for exactly
``chemfig`` and ``circuitikz``; ``tikz`` and ``tikz-cd`` had no preprocessor at
all.  ``app.utils.tikz_lint`` adds a small set of provably safe rewrites and the
renderer now routes both TikZ profiles through it.

Defects exercised here:

  * D-246 (recovery): a body whose newlines were serialised to the literal two
    characters ``\`` + ``n`` aborted with an undefined control sequence; the
    lint restores them.  (Also: the ``_extract_error`` undefined-command message
    no longer *asserts* a missing package as the cause.)

  * D-249 (structural): ``cos(\n*111)`` overflows pgfmath's dimen register once
    the loop-derived product crosses ~16384.  Wrapping the argument in
    ``mod(...,360)`` is exact for the 360-periodic trig functions, so the
    overflow is removed with an identical rendered value.

  * D-248 (structural): ``\pgfmathparse{E}`` followed by a ``\node`` that prints
    ``\pgfmathresult`` silently prints the node's y-coordinate, because the
    node coordinate re-runs pgfmath and clobbers the register.  The lint
    captures the value into a ``\pgfmathsetmacro`` macro.

Every assertion is written to FAIL against the unpatched tree (the module did
not exist and the tikz branch was absent) and pass with the fix.  Direction
checks are included: constant trig angles and already-correct pgfmath usage are
left byte-for-byte unchanged, so the tests certify the fix rather than the bug.
"""
import re

import pytest

from app.utils.tikz_lint import autofix
from app.services.latex_renderer import LatexRenderer


# --------------------------------------------------------------------------
# D-246: literal ``\n`` restoration
# --------------------------------------------------------------------------
def test_literal_backslash_n_restored_to_newline():
    # The corrupted body: real newlines arrived as the two chars '\' + 'n',
    # each sitting before the next command ('\n\node', '\n\draw').  The whole
    # body is a single physical line, the signature the restorer keys on.
    raw = (r"\fill[black!88] (0,0) rectangle (8,3);\n"
           r"\node[white] (a) at (1,1) {One};\n"
           r"\draw[white] (a)--(b);\n")
    # Direction check: the pre-fix body glues '\n' onto the following command,
    # which is an undefined control sequence and would abort the compile.
    assert r"\n\node" in raw and r"\n\draw" in raw

    fixed, applied, _ = autofix(raw)

    # The '\n' + command adjacencies are gone (became real newlines) ...
    assert r"\n\node" not in fixed
    assert r"\n\draw" not in fixed
    assert "\n" in fixed
    # ... and genuine control words beginning with 'n' are untouched.
    assert r"\node" in fixed
    assert r"\draw" in fixed
    assert applied and any("serialised" in a for a in applied)


def test_multiline_body_keeps_backslash_n_as_a_macro():
    # A body with real newlines uses '\n' as a loop counter (the tikz-w2-14
    # idiom) -- it must NOT be mistaken for a serialised newline.
    raw = ("\\foreach \\n in {0,...,19}{\n"
           "  \\node at (\\n,0) {N\\n};\n"
           "}")
    fixed, applied, _ = autofix(raw)
    assert r"\node at (\n,0)" in fixed
    assert r"{N\n}" in fixed
    # nothing was restored (no serialised-newline signature present)
    assert not any("serialised" in a for a in applied)


def test_node_and_nabla_not_corrupted_by_newline_pass():
    raw = r"\node (a) at (0,0) {$\nabla f$}; \newpage"
    fixed, applied, _ = autofix(raw)
    assert fixed == raw          # nothing to restore, byte-identical
    assert applied == ()


# --------------------------------------------------------------------------
# D-249: periodic clamp on loop-derived trig arguments
# --------------------------------------------------------------------------
def test_loop_derived_trig_argument_is_mod_clamped():
    raw = r"\pgfmathsetmacro{\dy}{0.09*cos(\n*111)}"
    fixed, applied, _ = autofix(raw)
    assert r"cos(mod(\n*111,360))" in fixed
    assert applied and any("mod(...,360)" in a for a in applied)


def test_constant_trig_angle_left_byte_identical():
    # No macro in the argument -> no overflow risk -> must not be rewritten,
    # proving the clamp targets the loop idiom and does not touch valid input.
    raw = r"\draw (0,0) -- (sin(30), cos(60));"
    fixed, applied, _ = autofix(raw)
    assert fixed == raw
    assert applied == ()


def test_arcsin_boundary_not_mismatched():
    # 'sin(' inside 'arcsin(' must not be treated as a bare trig call.
    raw = r"\pgfmathsetmacro{\t}{arcsin(\x)}"
    fixed, _, _ = autofix(raw)
    assert r"arcsin(\x)" in fixed
    assert "mod(" not in fixed


def test_trig_clamp_is_idempotent():
    raw = r"\pgfmathsetmacro{\dx}{sin(\n*73)}"
    once, _, _ = autofix(raw)
    twice, _, _ = autofix(once)
    assert once == twice
    assert once.count("mod(") == 1


# --------------------------------------------------------------------------
# D-248: \pgfmathparse -> \pgfmathsetmacro capture
# --------------------------------------------------------------------------
def test_pgfmathparse_feeding_node_is_captured():
    raw = (r"\pgfmathparse{int(round(sqrt(144)))}"
           "\n"
           r"\node[font=\small] at (9,-3.6) {value = \pgfmathresult};")
    fixed, applied, _ = autofix(raw)

    # The parse became a macro capture ...
    assert r"\pgfmathsetmacro" in fixed
    assert r"\pgfmathparse" not in fixed
    # ... the expression is preserved verbatim ...
    assert r"{int(round(sqrt(144)))}" in fixed
    # ... and the node body now references the macro, not the clobbered register.
    assert r"\pgfmathresult" not in fixed
    assert applied and any("pgfmathsetmacro" in a for a in applied)


def test_bare_pgfmathresult_without_node_is_left_alone():
    # No coordinate-bearing command between parse and result -> never clobbered
    # -> must be left unchanged (do not rewrite correct input).
    raw = r"\pgfmathparse{2+2}\pgfmathresult"
    fixed, applied, _ = autofix(raw)
    assert fixed == raw
    assert applied == ()


# --------------------------------------------------------------------------
# Renderer wiring: the tikz / tikz-cd profiles now route through the lint.
# --------------------------------------------------------------------------
def test_renderer_dispatches_tikz_lint():
    raw = r"\node (a) at (0,0) {One};\n\node (b) at (2,0) {Two};\n"
    fixed, fixes, _ = LatexRenderer._lint_tikz(raw)
    assert r"\n\node" not in fixed    # the fatal adjacency was repaired
    assert "\n" in fixed
    assert fixes                      # a fix was recorded


def test_lint_never_raises_and_degrades():
    # Pathological / unbalanced input must degrade to "unchanged", never raise.
    for raw in (r"\pgfmathparse{unbalanced", r"cos(\n*111", "", r"\\n plain"):
        fixed, fixes, warnings = autofix(raw)
        assert isinstance(fixed, str)
        assert isinstance(fixes, tuple)
        assert isinstance(warnings, tuple)


# --------------------------------------------------------------------------
# D-246 sub-issue: the undefined-command diagnostic no longer over-claims.
# --------------------------------------------------------------------------
def test_extract_error_does_not_assert_missing_package():
    log = "! Undefined control sequence.\nl.4 \\node\n"
    msg = LatexRenderer._extract_error(log)
    assert r"\node" in msg
    # The old wording flatly claimed "it may belong to a package this diagram
    # type does not load"; the corrected message offers typo/stray-token first.
    assert "may belong to a package this diagram type does not load" not in msg
    assert "typo" in msg or "stray" in msg


if __name__ == "__main__":            # pragma: no cover
    pytest.main([__file__, "-v"])
