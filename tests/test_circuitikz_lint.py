"""
Regression tests for app/utils/circuitikz_lint.

The defect (confirmed live against the renderer): an unbraced ``=`` inside a
circuitikz option value is split by pgfkeys before TeX evaluates it and aborts
the whole compile with ``! Extra }, or forgotten $.`` -- no image at all.  The
lint braces such values so pgfkeys treats them as opaque.

These tests import the REAL module under test, never a re-implementation, so
they detect drift in the lint itself.  The byte-identity and idempotency guards
are mandatory: an over-eager rewrite of a TikZ option list would corrupt
working diagrams, which is far worse than the bug being fixed.
"""
import pytest

from app.utils.circuitikz_lint import autofix


#: The exact body that aborted the live renderer with
#: "! Extra }, or forgotten $." at the ``to[R, l=$R_C=\SI...$]`` line.
BROKEN = (
    r"\draw (0,0) to[V, l=$V_{in}$] (0,2)"
    "\n"
    r"  to[R, l=$R_C=\SI{2.2}{\kilo\ohm}$] (3,2)"
    "\n"
    r"  to[C, l=$C_1$] (3,0)"
    "\n"
    r"  to[short] (0,0)"
    "\n"
    r"  node[ground]{};"
)

#: Bodies that already compile cleanly.  The lint MUST leave every one of these
#: byte-identical -- this is the byte-identity guard against over-reach.  Seeds
#: the ledger's pass_corpus contract.
PASS_CORPUS = [
    r"\draw (0,0) to[R, l=$R_1$] (2,0) to[C, l=$C_1$] (2,-2);",
    # A single '=' per segment is the normal key=value form and must survive.
    r"\draw (0,0) to[R=1<\ohm>, i>_=$i_1$] (2,0);",
    r"\node[draw, fill=red, align=center] at (0,0) {A};",
    # Arrow tip: '>=Latex' is one '=', not hostile.
    r"\draw[->, >=Latex] (0,0) -- (2,0);",
    # \ctikzset uses {} not [], so it is not an option list and untouched.
    r"\ctikzset{bipoles/length=1.2cm} \draw (0,0) to[L, l=$L_1$] (2,0);",
    # Already braced: the inner '=' is at brace depth 1, so not flagged.
    r"\draw (0,0) to[R, l={$R_C=\SI{2.2}{\kilo\ohm}$}] (3,0);",
    r"\draw (0,0) to[sV, l=$v(t)$, invert] (0,2);",
    # Arrow-tip spec carrying its own '=' inside braces: protected.
    r"\draw[-{Latex[length=2mm]}] (0,0) -- (2,0);",
]


def test_hostile_equals_is_braced():
    """The confirmed-broken body's hostile value gets braced; nothing else."""
    fixed, fixes, warnings = autofix(BROKEN)
    assert fixed != BROKEN
    assert len(fixes) == 1
    # The one and only rewrite wraps the resistor label's value.
    assert r"l={$R_C=\SI{2.2}{\kilo\ohm}$}" in fixed
    # The other, already-valid labels are untouched.
    assert r"l=$V_{in}$" in fixed
    assert r"l=$C_1$" in fixed
    assert warnings == ()


def test_only_the_value_is_wrapped_not_the_whole_option():
    """The key and comma structure are preserved; only the value is braced."""
    fixed, _, _ = autofix(BROKEN)
    # Exactly one added open brace and one added close brace vs the original.
    assert fixed.count("{") == BROKEN.count("{") + 1
    assert fixed.count("}") == BROKEN.count("}") + 1


def test_idempotent():
    """Applying the fix twice equals applying it once."""
    once, _, _ = autofix(BROKEN)
    twice, fixes2, _ = autofix(once)
    assert twice == once
    assert fixes2 == ()


@pytest.mark.parametrize("spec", PASS_CORPUS)
def test_pass_corpus_byte_identical(spec):
    """Clean specs must be returned byte-for-byte unchanged (over-reach guard)."""
    out, fixes, warnings = autofix(spec)
    assert out == spec, f"lint mutated a clean spec: {spec!r} -> {out!r}"
    assert fixes == ()
    assert warnings == ()


def test_multiple_hostile_values_all_braced():
    """Two hostile values in one body are both repaired, offsets kept valid."""
    body = (
        r"\draw (0,0) to[R, l=$R=1$] (2,0) to[C, l=$C=2$] (2,-2);"
    )
    fixed, fixes, _ = autofix(body)
    assert r"l={$R=1$}" in fixed
    assert r"l={$C=2$}" in fixed
    assert len(fixes) == 2


def test_internal_fault_degrades_to_unchanged(monkeypatch):
    """A fault inside the pass returns the body unchanged, never raises."""
    import app.utils.circuitikz_lint as mod

    def boom(_body):
        raise RuntimeError("simulated lint fault")

    monkeypatch.setattr(mod, "_autofix", boom)
    out, fixes, warnings = mod.autofix(BROKEN)
    assert out == BROKEN
    assert fixes == ()
    assert warnings == ()


def test_empty_and_no_brackets():
    """Degenerate inputs are safe no-ops."""
    for body in ["", "\\draw (0,0) -- (2,0);", "plain text no brackets"]:
        out, fixes, warnings = autofix(body)
        assert out == body
        assert fixes == ()


# ---------------------------------------------------------------------------
# defect-6: the hostile-character class generalised beyond a bare '='.
#
# A model annotating a bipole naturally writes a math value that reads on paper
# as one unit but contains, unbraced, a pgfkeys-structural character other than
# '=' -- a top-level comma (a list of conditions) or a ']' (interval notation).
# pgfkeys is '$'-blind, so such a character tears the value apart or ends the
# option list early.  The confirmed-broken live body:
#
#   \draw (3,0) to[R, l=$R_2$,
#       a=$k=\frac{R_2}{R_1+R_2},\ k\in(0,1]$,
#       i=$\SI{3.3}{\milli\ampere\per\sqrt\hertz}$] (3,-2);
#
# The a= value carries an in-math ',' AND an in-math ']'.  Before this fix the
# lint's own bracket matcher stopped at that ']' (mistaking it for the option
# list terminator) and inserted a brace mid-math, producing
#   a={$k=\frac{R_2}{R_1+R_2}},\ k\in(0,1]$
# which aborts with "! Package tikz Error: (, +, coordinate, pic, or node
# expected." (verified in-process against the workspace renderer).  The fix
# makes the structural scanners '$'-math-aware so the whole math span is
# recovered as one value and braced intact.
# ---------------------------------------------------------------------------

#: The exact a= value from the confirmed-broken body.
DEFECT6_BODY = (
    r"\draw (3,0) to[R, l=$R_2$, "
    r"a=$k=\frac{R_2}{R_1+R_2},\ k\in(0,1]$, "
    r"i=$\SI{3.3}{\milli\ampere\per\sqrt\hertz}$] (3,-2);"
)


def test_math_value_with_comma_and_bracket_is_braced_whole():
    """A math value carrying an in-math ',' and ']' is braced as ONE span.

    Regression for the lint's own bracket matcher stopping at a ']' inside
    ``$...$``.  The whole math value must be wrapped intact -- not torn at its
    internal ']' -- so the brace count rises by exactly one pair.
    """
    fixed, fixes, warnings = autofix(DEFECT6_BODY)
    assert r"a={$k=\frac{R_2}{R_1+R_2},\ k\in(0,1]$}" in fixed
    # The neighbouring, already-valid values are untouched.
    assert r"l=$R_2$" in fixed
    assert r"i=$\SI{3.3}{\milli\ampere\per\sqrt\hertz}$" in fixed
    assert len(fixes) == 1
    assert warnings == ()
    # Exactly one brace pair added -- no mid-math brace insertion.
    assert fixed.count("{") == DEFECT6_BODY.count("{") + 1
    assert fixed.count("}") == DEFECT6_BODY.count("}") + 1
    # Idempotent on the widened class too.
    assert autofix(fixed)[0] == fixed


def test_math_value_bracket_only_no_equals_is_braced():
    """A value hostile ONLY via ']'/',' (no second '=') is still braced.

    This is the part the '='-only predicate could not catch: interval notation
    with no key/value '=' inside the value.
    """
    body = r"\draw (0,0) to[R, a=$x\in[0,1]$] (2,0);"
    fixed, fixes, _ = autofix(body)
    assert r"a={$x\in[0,1]$}" in fixed
    assert len(fixes) == 1
    assert autofix(fixed)[0] == fixed


def test_widened_predicate_still_handles_the_bare_equals():
    """Widening to ',' and ']' must not regress the original '=' case."""
    fixed, fixes, _ = autofix(BROKEN)
    assert r"l={$R_C=\SI{2.2}{\kilo\ohm}$}" in fixed
    assert len(fixes) == 1


def test_bracket_inside_braces_is_not_hostile():
    """A ']' already protected by '{}' (arrow-tip spec) is not flagged."""
    body = r"\draw[-{Latex[length=2mm]}] (0,0) -- (2,0);"
    out, fixes, _ = autofix(body)
    assert out == body
    assert fixes == ()
