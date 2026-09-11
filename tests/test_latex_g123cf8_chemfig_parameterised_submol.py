"""G-123cf8 / D-030: chemfig parameterised ``\\definesubmol`` argument syntax.

chemfig declares a submol's argument COUNT as a bare digit after the name
(``\\definesubmol{arm}1{...}``) and passes the argument in BRACES at the call
site (``!{arm}{30}``).  Models trained on LaTeX ``\\newcommand{\\f}[1]{...}``
instead write the count in a bracket (``\\definesubmol{arm}[1]{...#1...}``) and
pass the argument in a bracket (``!{arm}[30]``).  chemfig then reads ``[1]`` as
a zero-arg optional display argument, never substitutes ``#1``, and the literal
``#`` reaches pgfmath during an angle evaluation -> FATAL
``Unknown operator `#'``.

``normalize_parameterised_submol`` rewrites both to chemfig's real syntax.

Direction: each test first asserts the UNPATCHED artefact is present (a bracket
count / a bracket-passed argument survive), so it fails against code lacking the
normalizer and passes with it.  A structural defect affects both themes, so the
w3-08 spec is exercised identically for light and dark (the recovery is purely
lexical and theme-independent -- there is no colour to resolve).
"""
from __future__ import annotations

import pytest

from app.utils.chemfig_lint import normalize_parameterised_submol


W3_08 = (
    "\\setchemfig{atom sep=2.4em, bond offset=1.5pt}\n"
    "\\definesubmol{arm}[1]{-[:#1]C(-[:#1+90]H)(-[:#1-90]H)-}\n"
    "\\chemfig{H!{arm}[30]O!{arm}[-30]H}"
)


def test_w3_08_bracket_syntax_is_the_unpatched_artefact():
    """Guard the fail direction: the raw spec carries the fatal bracket forms."""
    # Bracketed argument count -- chemfig wants a bare digit here.
    assert "\\definesubmol{arm}[1]" in W3_08
    # Bracket-passed call arguments -- chemfig wants braces here.
    assert "!{arm}[30]" in W3_08
    assert "!{arm}[-30]" in W3_08


def test_w3_08_definition_and_calls_are_normalised():
    fixed, applied = normalize_parameterised_submol(W3_08)
    # Count bracket -> bare digit.
    assert "\\definesubmol{arm}1{" in fixed
    assert "\\definesubmol{arm}[1]" not in fixed
    # Call arguments -> braces.
    assert "!{arm}{30}" in fixed
    assert "!{arm}{-30}" in fixed
    assert "!{arm}[30]" not in fixed
    assert "!{arm}[-30]" not in fixed
    # The angle-arithmetic body is preserved verbatim so #1+90 / #1-90 still
    # evaluate once #1 is substituted.
    assert "-[:#1]C(-[:#1+90]H)(-[:#1-90]H)-" in fixed
    assert applied  # fixes recorded


def test_bond_angle_brackets_are_never_touched():
    """A plain bond-angle bracket is not a submol argument and must survive."""
    body = "\\chemfig{A-[:30]B-[:-30]C}"
    fixed, applied = normalize_parameterised_submol(body)
    assert fixed == body
    assert applied == ()


def test_nonparameterised_submol_call_bracket_is_left_alone():
    """``!{name}`` with a non-parameterised def is not rewritten.

    The def has no ``#`` argument, so ``[1]`` there is a genuine optional
    display argument, not a count -- and its call bracket must be preserved.
    """
    body = (
        "\\definesubmol{x}[1]{-O-}\n"       # no '#': real optional arg, not count
        "\\chemfig{A!{x}[45]B}"
    )
    fixed, applied = normalize_parameterised_submol(body)
    assert fixed == body
    assert applied == ()


def test_real_definesubmol_with_bare_count_is_unchanged():
    """A correctly-written parameterised submol is a no-op."""
    body = (
        "\\definesubmol{arm}1{-[:#1]C-}\n"
        "\\chemfig{H!{arm}{30}O}"
    )
    fixed, applied = normalize_parameterised_submol(body)
    assert fixed == body
    assert applied == ()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_structural_fix_is_theme_independent(theme):
    """The recovery is lexical: identical output regardless of theme.

    A structural defect fails in BOTH themes, and this fix carries no colour,
    so both themes receive the same repaired body.
    """
    fixed, _ = normalize_parameterised_submol(W3_08)
    assert "!{arm}{30}" in fixed and "\\definesubmol{arm}1{" in fixed
