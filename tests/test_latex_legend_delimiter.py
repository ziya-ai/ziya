r"""
pgfplots legend-entry text: the ``\to`` delimiter collision, and the
``\dfrac`` row-overlap advisory.

THE DEFECT
----------
pgfplots stores a legend entry through a macro whose argument is DELIMITED by
``\to`` (pgfplots.code.tex)::

    \long\def\pgfplots@addlegendentry@opts[#1]#2{%
        \pgfplotslistpushbackglobal[#1]#2\to\pgfplots@legend

A ``\to`` in the author's own entry text therefore terminates that argument
early; the following tokens are consumed as the list-macro NAME, and TeX aborts
with "Missing control sequence inserted / Please don't say ``\def cs{...}``".
The message names neither the legend nor ``\to``, so the cause is genuinely
hard to find from the log.

Established empirically against a live TeX install, by isolation:

  * ``\addlegendentry{$A\to B$}``            -> FATAL (``\to`` alone suffices)
  * ``legend entries={$A\to B$}``            -> FATAL, identical error
  * ``xlabel={$A\to B$}`` / ``\node{$x\to\infty$}`` -> renders fine
  * ``\addlegendentry{$f\colon g$ and $a:b$}``       -> renders fine

so the trigger is ``\to``, the scope is legend entry text only, and ``\colon``
/ ``:`` (initially suspected) are NOT involved.

WHY THE REWRITE IS SAFE
-----------------------
plain.tex line 899 reads ``\mathchardef\rightarrow="3221 \let\to=\rightarrow``,
and neither amsmath nor amssymb redefines either name.  ``\to`` and
``\rightarrow`` are literally the same mathchar, so substituting one for the
other is a token-level identity rather than a near-equivalent.

NOT CAUSED BY THE amsmath/amssymb PREAMBLE ADDITION
---------------------------------------------------
Worth stating because the two were found in the same session: ``\to`` is a
plain-TeX macro and the collision is with pgfplots' own delimiter, so this
defect predates -- and is independent of -- loading amsmath.  The
``test_colon_is_not_the_trigger`` case pins the misdiagnosis that cost the most
time, so nobody re-derives it.

Every assertion is written to fail against the unpatched module and pass with
the fix, and each rewrite has a paired direction check proving the lint leaves
legitimate input byte-for-byte alone.
"""
import pytest

from app.utils.tikz_lint import autofix


# ---------------------------------------------------------------------------
# The fix: \to inside legend entry text
# ---------------------------------------------------------------------------

class TestToDelimiterCollision:
    def test_addlegendentry_to_is_rewritten(self):
        body = r"\addplot {x};" "\n" r"\addlegendentry{$A\to B$}"
        out, applied, _ = autofix(body)
        assert r"\rightarrow" in out
        assert r"\to " not in out and r"\to}" not in out
        assert applied, "the rewrite must be reported, not applied silently"
        assert "legend" in applied[0].lower()

    def test_legend_entries_key_form_is_rewritten(self):
        """The axis-key spelling reaches the same delimiter and aborts
        identically, so it must be covered too."""
        body = r"\begin{axis}[legend entries={$A\to B$}]\addplot {x};\end{axis}"
        out, applied, _ = autofix(body)
        assert r"\rightarrow" in out
        assert applied

    def test_optional_argument_form_is_covered(self):
        body = r"\addlegendentry[]{$x\to\infty$}"
        out, applied, _ = autofix(body)
        assert r"\rightarrow\infty" in out
        assert applied

    def test_nested_braces_inside_the_entry_are_handled(self):
        r"""The entry commonly contains its own groups (``\frac{a}{b}``); a
        naive non-matching scan would stop at the first ``}`` and miss a later
        ``\to`` -- or worse, rewrite past the entry's end."""
        body = r"\addlegendentry{$\frac{\rho}{1-\rho}$ as $\rho\to 1$} \draw (0,0)--(1,1);"
        out, applied, _ = autofix(body)
        assert r"\rho\rightarrow 1" in out
        assert r"\draw (0,0)--(1,1);" in out, "text after the entry was damaged"
        assert applied

    def test_multiple_entries_and_multiple_arrows(self):
        body = (
            r"\addlegendentry{$A\to B$}" "\n"
            r"\addlegendentry{$C\to D\to E$}"
        )
        out, applied, _ = autofix(body)
        assert out.count(r"\rightarrow") == 3
        assert "3" in applied[0], "the report should count every rewrite"

    def test_rewrite_is_idempotent(self):
        body = r"\addlegendentry{$A\to B$}"
        once, _, _ = autofix(body)
        twice, applied2, _ = autofix(once)
        assert twice == once
        assert not applied2, "a clean body must report no rewrite"


# ---------------------------------------------------------------------------
# Direction checks: legitimate input must be untouched
# ---------------------------------------------------------------------------

class TestToRewriteIsNarrowlyScoped:
    @pytest.mark.parametrize("body", [
        r"\begin{axis}[xlabel={$A\to B$}]\addplot {x};\end{axis}",
        r"\node at (0,0) {$x\to\infty$};",
        r"\begin{axis}[ylabel={map $f\to g$}]\addplot {x};\end{axis}",
    ])
    def test_to_outside_a_legend_entry_is_untouched(self, body):
        """Verified to render fine, so rewriting it would be pure churn."""
        out, applied, _ = autofix(body)
        assert out == body
        assert not applied

    def test_body_with_no_legend_is_byte_identical(self):
        body = r"\draw (0,0) -- (1,1); \node {$a\to b$};"
        out, applied, warnings = autofix(body)
        assert out == body
        assert not applied and not warnings

    @pytest.mark.parametrize("macro", [r"\top", r"\toprule", r"\totalheight"])
    def test_macros_merely_starting_with_to_are_untouched(self, macro):
        body = r"\addlegendentry{$" + macro + r"$}"
        out, applied, _ = autofix(body)
        assert out == body, f"{macro} must not be treated as \\to"
        assert not applied

    def test_line_break_followed_by_the_word_to_is_untouched(self):
        r"""``\\to`` is the line break ``\\`` plus the literal text ``to``.
        Rewriting it would invent an arrow the author never wrote."""
        body = r"\addlegendentry{first \\to second}"
        out, applied, _ = autofix(body)
        assert out == body
        assert not applied

    def test_unbalanced_brace_declines_rather_than_guessing(self):
        body = r"\addlegendentry{$A\to B$"        # never closed
        out, applied, _ = autofix(body)
        assert out == body
        assert not applied


# ---------------------------------------------------------------------------
# The advisory: \dfrac overlaps neighbouring legend rows
# ---------------------------------------------------------------------------

class TestDfracLegendRowSep:
    r"""The repair adds room rather than shrinking the maths.

    Measured on this renderer with a two-row legend: the default separation and
    2pt both overlap, 4pt clears a simple ``\dfrac`` and 6pt clears a nested
    one, so 6pt is injected.  An earlier revision only WARNED and advised
    ``\tfrac``; that was superseded once ``row sep`` was shown to fix the
    overlap while leaving the fraction at the size the author actually wrote.
    """

    AXIS = (
        "\\begin{axis}[width=8cm]\n"
        "\\addplot {x};\n"
        "\\addlegendentry{$L=\\dfrac{\\rho}{1-\\rho}$}\n"
        "\\end{axis}"
    )

    def test_dfrac_in_a_legend_entry_gets_row_sep(self):
        out, applied, warnings = autofix(self.AXIS)
        assert "row sep=6pt" in out, "no room was reserved"
        assert r"\dfrac" in out, "the fraction must keep the size it was written at"
        assert applied and "row sep" in applied[0]
        assert not warnings, "the injection is a fix, not something to warn about"

    def test_injection_lands_inside_the_axis_option_list(self):
        out, _, _ = autofix(self.AXIS)
        head = out.splitlines()[0]
        assert head.startswith("\\begin{axis}[") and head.endswith("]")
        assert "row sep=6pt" in head, "row sep must sit in the axis options"

    def test_existing_legend_style_is_appended_to_not_replaced(self):
        r"""pgfplots' ``legend style`` appends to ``every axis legend``, verified
        by render: a body styled ``draw=red,fill=yellow!20`` kept both after the
        injection.  So the author's styling must still be present."""
        body = (
            "\\begin{axis}[legend style={draw=red}]\n"
            "\\addlegendentry{$\\dfrac{a}{b}$}\n"
            "\\end{axis}"
        )
        out, applied, _ = autofix(body)
        assert "draw=red" in out, "author styling was clobbered"
        assert "row sep=6pt" in out
        assert applied

    def test_option_list_containing_a_bracketed_value_is_spliced_correctly(self):
        r"""A key value may itself hold a ``]`` (``xlabel={$[m]$}``).  Splicing
        at the first ``]`` would land inside that value and corrupt the body."""
        body = (
            "\\begin{axis}[xlabel={$[m]$},width=8cm]\n"
            "\\addlegendentry{$\\dfrac{a}{b}$}\n"
            "\\end{axis}"
        )
        out, _, _ = autofix(body)
        assert "xlabel={$[m]$}" in out, "the bracketed value was damaged"
        assert out.splitlines()[0].endswith(",legend style={row sep=6pt}]")

    def test_axis_with_no_option_list_gains_one(self):
        body = (
            "\\begin{axis}\n"
            "\\addlegendentry{$\\dfrac{a}{b}$}\n"
            "\\end{axis}"
        )
        out, applied, _ = autofix(body)
        assert out.splitlines()[0] == "\\begin{axis}[legend style={row sep=6pt}]"
        assert applied

    def test_log_axis_variant_is_handled(self):
        body = (
            "\\begin{semilogyaxis}[width=8cm]\n"
            "\\addlegendentry{$\\dfrac{a}{b}$}\n"
            "\\end{semilogyaxis}"
        )
        out, applied, _ = autofix(body)
        assert "row sep=6pt" in out
        assert applied

    def test_frac_is_not_warned_about(self):
        r"""Isolation showed a ``\frac``-only legend renders cleanly: ``\frac``
        is the victim of an adjacent ``\dfrac``, never the cause.  Warning on
        it would be a false positive on the common, correct spelling."""
        body = r"\addlegendentry{$L=\frac{\rho}{1-\rho}$}"
        _, _, warnings = autofix(body)
        assert not warnings

    def test_tfrac_is_not_warned_about(self):
        body = r"\addlegendentry{$L=\tfrac{\rho}{1-\rho}$}"
        _, _, warnings = autofix(body)
        assert not warnings

    def test_dfrac_outside_a_legend_entry_is_not_warned_about(self):
        """Display fractions are fine in labels and nodes -- there is no fixed
        row pitch to overflow."""
        body = r"\begin{axis}[xlabel={$\dfrac{a}{b}$}]\addplot {x};\end{axis}"
        _, _, warnings = autofix(body)
        assert not warnings

    def test_author_row_sep_is_respected_and_reported(self):
        r"""A value the author chose is never overridden -- even at 2pt, where
        the overlap persists -- but it IS reported, carrying the measured
        thresholds so the reader can pick a value that works."""
        body = (
            "\\begin{axis}[legend style={row sep=2pt}]\n"
            "\\addlegendentry{$\\dfrac{a}{b}$}\n"
            "\\end{axis}"
        )
        out, applied, warnings = autofix(body)
        assert out == body, "an author-set row sep must not be rewritten"
        assert not applied
        assert warnings and "4pt" in warnings[0] and "6pt" in warnings[0]

    def test_no_false_advisory_after_the_fix_injects(self):
        r"""Regression on a real seam bug: the advisory originally ran on the
        POST-rewrite body, so it matched the ``row sep`` this module had just
        injected and reported it as "a row sep you set explicitly" on a body
        where the author had set nothing.  Advisories judge the INPUT."""
        _, applied, warnings = autofix(self.AXIS)
        assert applied, "the fix should have fired"
        assert not warnings, "the advisory must not see its own injection"

    def test_both_legend_repairs_coexist(self):
        body = (
            "\\begin{axis}[width=8cm]\n"
            "\\addlegendentry{$L=\\dfrac{a}{b}$ as $x\\to\\infty$}\n"
            "\\end{axis}"
        )
        out, applied, warnings = autofix(body)
        assert r"\rightarrow" in out           # fatal repaired
        assert r"\dfrac" in out                # fraction size preserved
        assert "row sep=6pt" in out            # room reserved
        assert len(applied) == 2, applied
        assert not warnings

    def test_body_with_no_axis_environment_is_left_alone(self):
        r"""Nothing to splice into -> decline rather than guess.  Such a body
        cannot compile as pgfplots anyway: a legend requires an axis."""
        body = r"\addlegendentry{$\dfrac{a}{b}$}"
        out, applied, _ = autofix(body)
        assert out == body
        assert not applied


# ---------------------------------------------------------------------------
# The misdiagnosis, pinned so it is not re-derived
# ---------------------------------------------------------------------------

def test_colon_is_not_the_trigger():
    r"""``\colon`` and ``:`` were both suspected and both cleared: an entry
    containing them renders, and ``\protect\colon`` did NOT rescue the failing
    case.  amsmath defines ``\colon`` with ``\DeclareRobustCommand``, so it is
    robust, not fragile.  The lint must not touch either."""
    body = r"\addlegendentry{$f\colon g$ and $a:b$}"
    out, applied, warnings = autofix(body)
    assert out == body
    assert not applied and not warnings


# ---------------------------------------------------------------------------
# Contract shared with every other lint module
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    "", "{", "}", r"\addlegendentry", r"\addlegendentry{", r"legend entries={",
    r"\addlegendentry{}", r"legend entries={}", "\\" * 40,
    r"\addlegendentry{$\to$}",
])
def test_autofix_never_raises(body):
    """Advisory only: a lint fault must never turn a working render into a
    failure, so malformed input degrades instead of propagating."""
    out, applied, warnings = autofix(body)
    assert isinstance(out, str)
    assert isinstance(applied, tuple) and isinstance(warnings, tuple)
