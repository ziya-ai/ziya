r"""
Recovery regression tests for the circuitikz LaTeX path (fix group G-73).

Covers three circuitikz recovery defects from the graphics sweep backlog:

  * D-063(b) circuitikz-w4-12 (quoted-numeric option/coordinate values):
    ``right="2cm"`` / ``scale="0.9"`` / ``("0","0")`` are JSON-habit quoted
    numbers that pgfkeys/pgfmath cannot parse -- the second op-amp lands at an
    extreme distance and ink is pushed off the plate.  ``circuitikz_lint`` now
    strips quotes whose whole content is a number or number+TeX-unit, while
    leaving quoted label text / math untouched.

  * D-063(a) circuitikz-w4-11 (``\begin{tikzpicture}`` body double-wrapped):
    the profile ``_wrap`` guard passes a body through when it already opens any
    known drawing environment, so a ``tikzpicture`` body under the circuitikz
    profile is NOT nested inside a second ``circuitikz`` environment.

  * D-016 circuitikz-w4-02 (full standalone document rejected as a security
    violation): ``_sanitize_input`` strips the ``\documentclass`` /
    ``\usepackage`` / ``\begin{document}`` wrapper BEFORE the security prescan,
    so the redundant author preamble no longer trips the deny-list.

Each test asserts BOTH directions: the raw (unfixed) input is proven to exhibit
the defect, and the processed input is proven correct -- so a test that would
pass against unpatched code certifies the bug, not the fix.

These import the REAL modules under test, never a re-implementation.
"""
import pytest

from app.utils.circuitikz_lint import autofix
from app.services.latex_renderer import LatexRenderer
from app.services.latex_profiles import get_profile


# ---------------------------------------------------------------------------
# D-063(b): quoted numeric option / coordinate values
# ---------------------------------------------------------------------------

#: The exact circuitikz-w4-12 body (JSON-habit quoted numbers everywhere).
W4_12 = (
    r"\definecolor{plate}{HTML}{16324A}" "\n"
    r'\node[op amp, color=plateink] (oa) at ("0","0") {};' "\n"
    r'\node[op amp, color=plateink, right="2cm" of oa] (ob) {};' "\n"
    r'\draw[color=plateink, scale="0.9"] (oa.out) to[short] (ob.-);' "\n"
    r"\node[plateink] at (2.4,1.6) {Quoted numbers};" "\n"
)


def test_quoted_numeric_values_stripped():
    """right="2cm" / scale="0.9" / ("0","0") lose their quotes; label kept."""
    fixed, fixes, warnings = autofix(W4_12)

    # Direction: the raw body carries the quoted numbers that break pgfkeys.
    assert '"2cm"' in W4_12 and '"0.9"' in W4_12 and '"0"' in W4_12

    # After the fix each quoted number is a bare pgfkeys/pgfmath value.
    assert "right=2cm of oa" in fixed
    assert "scale=0.9" in fixed
    assert "at (0,0)" in fixed
    assert '"2cm"' not in fixed
    assert '"0.9"' not in fixed
    assert '"0"' not in fixed

    # The node label text is never a numeric-dimension, so it is untouched.
    assert "{Quoted numbers}" in fixed
    # One fix note per stripped quote pair (two coords + right + scale = 4).
    assert len(fixes) == 4
    assert warnings == ()


def test_quoted_numeric_values_safety():
    """Quotes carrying letters, or inside labels / math, are NOT stripped."""
    body = (
        r'\node[label="5V"] at (0,0) {He said "0" today};' "\n"
        r'\node {$k="2"$};' "\n"
    )
    fixed, fixes, _ = autofix(body)
    # "5V" has a non-TeX unit -> not a pure dimension -> left alone.
    assert '"5V"' in fixed
    # "0" inside a {...} label group -> the quotes are meant literally.
    assert '"0"' in fixed
    # "2" inside $...$ math -> untouched.
    assert '"2"' in fixed
    assert fixes == ()


def test_quote_strip_is_idempotent_and_byte_identical_when_clean():
    """A body with no quoted numbers is returned byte-for-byte unchanged."""
    clean = r"\draw (0,0) to[R=$R_1$] (2,0) node[ocirc]{};"
    fixed, fixes, _ = autofix(clean)
    assert fixed == clean
    assert fixes == ()
    # Idempotent: a second pass over the already-fixed w4-12 changes nothing.
    once, _, _ = autofix(W4_12)
    twice, twice_fixes, _ = autofix(once)
    assert twice == once
    assert twice_fixes == ()


# ---------------------------------------------------------------------------
# D-063(a): \begin{tikzpicture} body must not be double-wrapped
# ---------------------------------------------------------------------------

W4_11 = (
    r"\begin{tikzpicture}[scale=1.0]" "\n"
    r"\draw (0,0) to[R=$R_1$] (2.4,0) to[L=$L_1$] (4.8,0) node[ocirc]{};" "\n"
    r"\end{tikzpicture}" "\n"
)


def test_tikzpicture_body_not_double_wrapped():
    profile = get_profile("circuitikz")
    assert profile is not None and profile.wrap_env == "circuitikz"

    wrapped = profile._wrap(W4_11.strip())
    # Passed through: no second (circuitikz) environment nested around it.
    assert r"\begin{circuitikz}" not in wrapped
    assert wrapped.strip() == W4_11.strip()

    # Direction: a bare body (no drawing environment) IS wrapped, proving the
    # passthrough above is specific to the self-supplied environment, not a
    # blanket no-op.
    bare = r"\draw (0,0) to[R=$R$] (2,0);"
    bare_wrapped = profile._wrap(bare)
    assert bare_wrapped.startswith(r"\begin{circuitikz}")
    assert bare_wrapped.rstrip().endswith(r"\end{circuitikz}")


# ---------------------------------------------------------------------------
# D-016: full standalone document is recovered, not rejected as a security risk
# ---------------------------------------------------------------------------

W4_02 = (
    r"\documentclass[border=3pt]{standalone}" "\n"
    r"\usepackage[american]{circuitikz}" "\n"
    r"\begin{document}" "\n"
    r"\begin{circuitikz}" "\n"
    r"\draw (0,0) to[R=$R$] (2.6,0) to[C=$C$] (2.6,-1.4) node[ground]{};" "\n"
    r"\end{circuitikz}" "\n"
    r"\end{document}" "\n"
)


def test_full_document_wrapper_recovered_before_prescan():
    # Direction: the RAW document is rejected by the security prescan because
    # \documentclass / \usepackage are on the deny-list.
    assert LatexRenderer.prescan(W4_02) is not None

    # After stripping the wrapper the surviving body is only the circuit and
    # passes the prescan.
    sanitized = LatexRenderer._sanitize_input(W4_02)
    assert r"\documentclass" not in sanitized
    assert r"\usepackage" not in sanitized
    assert r"\begin{circuitikz}" in sanitized
    assert LatexRenderer.prescan(sanitized) is None


def test_prepended_usepackage_line_recovered():
    """A \\usepackage line prepended to a bare body is stripped, not rejected."""
    body = (
        r"\usepackage[american]{circuitikz}" "\n"
        r"\draw (0,0) to[R=$R$] (2,0) node[ocirc]{};" "\n"
    )
    assert LatexRenderer.prescan(body) is not None      # raw: rejected
    sanitized = LatexRenderer._sanitize_input(body)
    assert r"\usepackage" not in sanitized
    assert LatexRenderer.prescan(sanitized) is None      # recovered


def test_in_body_injection_still_rejected():
    """The recovery is subtractive: a real \\input in the body still fails."""
    malicious = r"\draw (0,0) -- (1,1); \input{/etc/passwd}"
    sanitized = LatexRenderer._sanitize_input(malicious)
    # \input is not a preamble line, so it survives sanitisation and the
    # prescan still rejects it -- the security posture is unchanged.
    assert LatexRenderer.prescan(sanitized) is not None
