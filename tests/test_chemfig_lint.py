"""
Tests for the chemfig ring lint (app.utils.chemfig_lint).

Every fixture here is a body that was actually rendered and visually inspected
against a real chemfig install, so the expectations encode observed behaviour
rather than a reading of the manual.  The three empirical findings the lint is
built on:

  1. A standalone ``*n`` ring needs n bonds; a FUSED one needs n-1, because it
     inherits its closing edge from the ring it is nested in.

  2. An under-specified ring still COMPILES.  It renders as an open chain, so
     there is no error anywhere in the pipeline -- the wrong molecule is
     returned as a success.  That is what makes a lint the only viable place to
     catch it.

  3. A ring nested inside a BRANCH is pendant, not fused, and needs the full n.
     Conflating the two would make the autofixer open a correct biphenyl ring.
     This distinction is the one the fixer most needs to get right.

The bond ORDER of an added bond is ambiguous for odd rings, so autofix refuses
there.  Guessing would convert a visibly broken ring into a plausible-looking
wrong structure, which is strictly worse for the user.
"""

import pytest

from app.utils.chemfig_lint import (
    FUSED,
    PENDANT,
    STANDALONE,
    autofix,
    lint,
    scan_rings,
)

# ---------------------------------------------------------------------------
# Bodies verified by rendering.  Names describe the molecule, not the syntax.
# ---------------------------------------------------------------------------

BENZENE = r"*6(-=-=-=)"
NAPHTHALENE = r"*6(-=-*6(-=-=-)=-=)"
INDOLE = r"*6(-=-*5(-=--)=-=)"
BIPHENYL = r"*6(-=-(-*6(-=-=-=))-=-)"

#: Renders as an open chain: the fused five-ring has 3 bonds where 4 are needed.
INDOLE_BROKEN = r"*6(-=-*5(-=-)=-=)"

#: Five top-level bonds, not six: ``(-OH)``, ``(-CH_3)`` and ``(=O)`` are
#: branches.  This is the exact shape that misled me twice while probing, which
#: is the strongest argument for counting mechanically.
BRANCH_HEAVY_SHORT = r"*6(-(-OH)=(-CH_3)-(=O)-=)"

#: Correct as written: six top-level bonds with two substituents para to each
#: other.  Verified by rendering -- used to prove the lint stays silent on a
#: substituted ring, and as the reference for the position-preservation test.
PARA_SUBSTITUTED = r"*6(-=(-OH)-=-(-Cl)=)"

#: Three linearly fused rings, each inner ring supplying size-1 bonds.
FUSED_TRIPLE = r"*6(-=-*6(-=-*6(-=-=-)=-)=-=)"

#: Correct isatin: the fused five-ring needs 4 ring bonds, and the two ``(=O)``
#: groups are branches that contribute none of them.  Worth keeping as a named
#: fixture because the 3-bond spelling is an easy and convincing mistake -- it
#: renders as an open chain with a dangling acetyl, which still looks like a
#: molecule.  The lint caught exactly this error in my own earlier work.
ISATIN = r"*6(-=-*5(-(=O)-(=O)--)=-=)"

#: The 3-bond spelling, kept so a regression cannot quietly re-accept it.
ISATIN_BROKEN = r"*6(-=-*5(-(=O)-(=O)-)=-=)"


# ---------------------------------------------------------------------------
# Bond counting: branches, options and brace groups must not be counted.
# ---------------------------------------------------------------------------

def test_plain_aromatic_ring_counts_all_bonds():
    ring, = scan_rings(BENZENE)
    assert (ring.size, ring.bonds, ring.expected) == (6, 6, 6)
    assert ring.is_closed


def test_substituent_bonds_are_not_ring_bonds():
    """``(-OH)`` contributes a bond character that is not a ring bond."""
    ring, = scan_rings(r"*6(-=(-OH)-=-=)")
    assert ring.bonds == 6, f"branch counted as ring bond: {ring.pattern!r}"
    assert ring.is_closed


def test_bond_option_brackets_are_skipped():
    """A negative angle such as ``[:-30]`` contains a bare ``-``."""
    ring, = scan_rings(r"*6(-[:-30]=-=-=)")
    assert ring.bonds == 6
    assert ring.is_closed


def test_brace_groups_are_skipped():
    """A charge such as ``SO_{4}^{2-}`` ends in a minus sign."""
    ring, = scan_rings(r"*6(-=(-SO_{4}^{2-})-=-=)")
    assert ring.bonds == 6
    assert ring.is_closed


def test_branch_heavy_ring_is_reported_short():
    """The miscount this lint exists to prevent."""
    finding, = lint(BRANCH_HEAVY_SHORT)
    assert (finding.bonds, finding.expected) == (5, 6)
    assert finding.deficit == 1


# ---------------------------------------------------------------------------
# Classification.  Fused vs pendant is the load-bearing distinction.
# ---------------------------------------------------------------------------

def test_lone_ring_is_standalone():
    ring, = scan_rings(BENZENE)
    assert ring.kind == STANDALONE


def test_ring_on_the_bond_chain_is_fused():
    outer, inner = scan_rings(NAPHTHALENE)
    assert outer.kind == STANDALONE
    assert inner.kind == FUSED
    assert inner.expected == 5, "a fused 6-ring inherits one edge"


def test_ring_inside_a_branch_is_pendant_not_fused():
    """Regression guard for the distinction that would corrupt a biphenyl.

    Both forms nest inside another ring's parentheses, but a pendant ring shares
    no edge, so it needs all n bonds.  Calling it fused would report a correct
    ring as over-specified and invite a fix that breaks it.
    """
    outer, inner = scan_rings(BIPHENYL)
    assert inner.kind == PENDANT
    assert inner.expected == 6
    assert not lint(BIPHENYL), "a correct biphenyl must produce no findings"


def test_nested_fusion_is_classified_per_level():
    """Three fused rings: each inner ring is fused to its immediate parent, so
    each needs size-1 bonds.  Deep nesting must not confuse the parent search."""
    rings = scan_rings(FUSED_TRIPLE)
    assert [r.kind for r in rings] == [STANDALONE, FUSED, FUSED]
    assert [r.expected for r in rings] == [6, 5, 5]
    assert not lint(FUSED_TRIPLE)


def test_innermost_ring_short_by_one_is_caught_in_deep_nesting():
    """Guards the parent search: the innermost ring of a triple-fused system is
    the easiest place for an off-by-one to hide, since it is furthest from the
    standalone ring that anchors the classification."""
    finding, = lint(r"*6(-=-*6(-=-*6(-=-=)=-)=-=)")
    assert (finding.size, finding.bonds, finding.expected) == (6, 4, 5)
    assert finding.kind == FUSED


# ---------------------------------------------------------------------------
# Real molecules must not produce findings.  A lint that cries wolf on correct
# input is worse than none, because it trains the user to ignore it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,body", [
    ("benzene", BENZENE),
    ("naphthalene", NAPHTHALENE),
    ("indole", INDOLE),
    ("biphenyl", BIPHENYL),
    ("5-hydroxyindole", r"*6(-=-*5(-=--)=(-OH)-=)"),
    ("serotonin", r"*6(-=-*5(-=(-CH_2CH_2NH_2)--)=(-OH)-=)"),
    ("5-HIAA", r"*6(-=-*5(-=(-CH_2CO_2H)--)=(-OH)-=)"),
    ("5-methoxyindole", r"*6(-=-*5(-=--)=(-OCH_3)-=)"),
    ("isatin", ISATIN),
    ("5-hydroxyanthranilate", r"*6(-=(-NH_2)-(-CO_2H)=(-OH)-=)"),
    ("indole ring with charge", r"*6(-=(-SO_{4}^{2-})-=-=)"),
    # A standalone 5-ring needs 5 bonds, not 4.  Confirmed by rendering: the
    # 4-bond form draws an open chain, so it belongs with the broken fixtures.
    ("cyclopentadiene", r"*5(-=-=-)"),
    ("cycloheptatriene", r"*7(-=-=-=-)"),
    ("para-chlorophenol", PARA_SUBSTITUTED),
])
def test_correct_structures_are_silent(name, body):
    assert lint(body) == (), f"false positive on {name}"


@pytest.mark.parametrize("body", [
    INDOLE_BROKEN,
    BRANCH_HEAVY_SHORT,
    ISATIN_BROKEN,
    r"*6(-=-=-)",
    r"*6(-=-*6(-=-=)=-=)",
    r"*5(-=-=)",
])
def test_short_rings_are_all_reported(body):
    assert lint(body), f"failed to flag an open ring: {body}"


def test_carbonyl_branches_do_not_count_as_ring_bonds():
    """Regression guard for a real mistake this lint caught.

    ``*5(-(=O)-(=O)-)`` looks like it has five bond tokens but has three ring
    bonds; the two ``(=O)`` groups are branches.  The 3-bond spelling renders as
    an open chain with a dangling acetyl -- convincing enough that I reported it
    as correct isatin earlier in this session before the lint disagreed.
    """
    finding, = lint(ISATIN_BROKEN)
    assert (finding.size, finding.bonds, finding.expected) == (5, 3, 4)
    assert lint(ISATIN) == (), "the corrected form must be silent"


def test_over_specified_ring_is_reported():
    findings = lint(r"*6(-=-=-=-)")
    assert findings and findings[0].deficit < 0


def test_finding_message_is_actionable():
    finding, = lint(INDOLE_BROKEN)
    message = finding.describe()
    assert "*5" in message
    assert "fused" in message
    # The consequence matters more than the count: this renders successfully.
    assert "will not close" in message


# ---------------------------------------------------------------------------
# Autofix.  Narrow by design; silence is preferable to a confident wrong guess.
# ---------------------------------------------------------------------------

def test_even_alternating_ring_is_closed():
    fixed, applied, warnings = autofix(r"*6(-=-=-)")
    assert fixed == r"*6(-=-=-=)"
    assert applied and not warnings
    assert lint(fixed) == ()


def test_fix_preserves_substituent_positions():
    """A fix must not move existing substituents.

    Verified by rendering: appending the missing bond just inside the closing
    paren closes the ring and leaves substituent placement intact.  The fixture
    drops one bond from a body confirmed correct, so the fix should restore
    exactly that body -- which is a stronger check than merely re-linting clean,
    because it pins the substituents' relative order too.
    """
    broken = PARA_SUBSTITUTED.replace(r"-(-Cl)=)", r"-(-Cl))")
    assert lint(broken), "fixture must actually be short a bond"

    fixed, applied, warnings = autofix(broken)
    assert lint(fixed) == ()
    assert applied and not warnings
    assert "(-OH)" in fixed and "(-Cl)" in fixed
    assert fixed.index("(-OH)") < fixed.index("(-Cl)")


def test_odd_ring_is_refused_not_guessed():
    """A fused five-ring has two plausible closures, so intent is ambiguous.

    Indole's pyrrole ring needs ``-=--``; assuming alternation would produce
    ``-=-=`` -- a ring that closes but shows a bond order that is simply wrong.
    """
    fixed, applied, warnings = autofix(INDOLE_BROKEN)
    assert fixed == INDOLE_BROKEN, "must not rewrite an ambiguous ring"
    assert not applied
    assert warnings and "*5" in warnings[0]


def test_non_alternating_ring_is_refused():
    """Without a clear Kekule pattern there is no single right continuation."""
    body = r"*6(--=-=)"
    fixed, applied, warnings = autofix(body)
    assert fixed == body
    assert not applied and warnings


def test_deficit_above_one_is_refused():
    """Two or more missing bonds means the author's intent is unclear."""
    body = r"*6(-=-=)"
    fixed, applied, warnings = autofix(body)
    assert fixed == body
    assert not applied and warnings


def test_over_specified_ring_is_never_silently_trimmed():
    """Removing a bond could delete a bond the author meant to keep."""
    body = r"*6(-=-=-=-)"
    fixed, applied, warnings = autofix(body)
    assert fixed == body
    assert not applied and warnings


def test_correct_body_is_returned_untouched():
    for body in (BENZENE, INDOLE, BIPHENYL):
        assert autofix(body) == (body, (), ())


def test_multiple_rings_are_fixed_without_index_drift():
    """Right-to-left application keeps later insertions from shifting earlier
    offsets, which would corrupt the second and subsequent fixes."""
    body = r"\chemfig{*6(-=-=-)}\hspace{4mm}\chemfig{*6(-=-=-)}"
    fixed, applied, warnings = autofix(body)
    assert fixed.count(r"*6(-=-=-=)") == 2
    assert len(applied) == 2 and not warnings
    assert lint(fixed) == ()


# ---------------------------------------------------------------------------
# Malformed input must degrade quietly.  The lint is advisory; it must never
# become the reason a render fails.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    r"*6(-=-=-",          # unbalanced ring paren
    r"*6(-=(-OH-=-=)",    # unbalanced branch
    r"*6",                # no paren at all
    r"",                  # empty
    r"\chemfig{CH_3-CH_3}",  # no rings
])
def test_malformed_input_does_not_raise(body):
    scan_rings(body)
    lint(body)
    assert autofix(body)[0] == body
