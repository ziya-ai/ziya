"""
Tests for the chemfig ``\\charge`` argument repair.

Every case here traces to a failure reproduced against a live chemfig install,
and the file is organised around the two things that make this repair subtle:

  1. The separator is ``=``, not ``:``.  chemfig declares
     ``\\charge_g#1:#2[#3]=#4,`` so ``:`` is the radial OFFSET.  The wrong guess
     is natural (bond angles are ``-[:30]``) and the error message --
     "Argument of \\charge_g has an extra }" -- accuses brace balance instead.

  2. The math wrap MUST be conditional.  Measured on real renders:
     ``\\charge{90=-}`` and ``\\charge{90=$-$}`` produce DIFFERENT bytes (text
     hyphen vs. math minus), while ``\\|``, ``+`` and ``2+`` are byte-identical.
     So blanket wrapping would silently change diagrams that already worked.

The end-to-end tests assert something stronger than "the repair ran": they
assert the repaired body actually COMPILES, which is the only claim that
matters and the only one that would catch a repair producing plausible-looking
but still-invalid LaTeX.
"""

import pytest

from app.utils.chemfig_charge import autofix, repair
from app.services.latex_renderer import LatexRenderer


@pytest.fixture
def renderer(tmp_path):
    return LatexRenderer(cache_dir=tmp_path / "cache")


_cap = LatexRenderer().probe()
needs_tex = pytest.mark.skipif(not _cap.available, reason="no TeX toolchain")


# ---------------------------------------------------------------------------
# Separator promotion.  Pure string work -- no TeX required.
# ---------------------------------------------------------------------------

def test_colon_separator_is_promoted_to_equals():
    fixed, notes = repair(r"\chemfig{\charge{90:\|}{O}}")
    assert fixed == r"\chemfig{\charge{90=\|}{O}}"
    assert notes, "a silent rewrite would hide the correction from the caller"


def test_every_item_in_a_multi_charge_spec_is_repaired():
    fixed, _ = repair(r"\chemfig{\charge{90:\|,180:\|,270:\|}{O}}")
    assert fixed == r"\chemfig{\charge{90=\|,180=\|,270=\|}{O}}"


def test_a_genuine_offset_is_preserved():
    """The LAST top-level colon is the separator, not the first.

    In ``45:2pt:\\|`` the first colon really does introduce the offset; taking
    it as the separator would destroy the offset and produce ``45=2pt:\\|``.
    """
    fixed, _ = repair(r"\chemfig{\charge{45:2pt:\|}{S}}")
    assert fixed == r"\chemfig{\charge{45:2pt=\|}{S}}"


def test_a_correct_body_is_left_byte_identical():
    body = r"\chemfig{\charge{90=\|,180=\|}{O}}"
    fixed, notes = repair(body)
    assert fixed == body
    assert notes == ()


def test_a_colon_inside_a_tikz_option_block_is_not_the_separator():
    """Depth tracking: ``[...]`` can contain a colon of its own."""
    body = r"\chemfig{\charge{90[red]=\|}{O}}"
    fixed, notes = repair(body)
    assert fixed == body
    assert notes == ()


def test_bare_angle_with_no_symbol_gets_an_empty_symbol():
    r"""``\charge{90}`` -- an angle with the mandatory charge symbol missing.

    chemfig aborts fatally on the bare-angle form ("Argument of \charge_g has
    an extra }"), the SAME misleading message the colon/bare-symbol cases hit,
    so leaving it untouched guarantees the render dies (D-049 / chemfig-w4-13).
    The repair must NOT invent a charge symbol (that would guess chemical
    intent), so it supplies the mandatory ``=`` with an EMPTY symbol: the angle
    is preserved, no glyph is fabricated, and the molecule renders.

    Direction: the pre-fix code returned the body byte-identical, so the
    ``!=`` and non-empty-notes assertions below FAIL against unpatched code.
    """
    body = r"\chemfig{\charge{90}{O}}"
    fixed, notes = repair(body)
    assert fixed == r"\chemfig{\charge{90=}{O}}"
    assert fixed != body, "the fatal bare-angle form must not survive"
    assert notes, "a silent rewrite would hide the correction from the caller"
    # No phantom symbol invented: the '=' is present with nothing after it.
    assert "90=}" in fixed
    # And the known-fatal bare ``{90}`` argument is gone.
    assert "{90}" not in fixed


@pytest.mark.parametrize("body,expected", [
    (r"\chemfig{\charge{90}{O}}", r"\chemfig{\charge{90=}{O}}"),
    (r"\chemfig{\charge{-45}{O}}", r"\chemfig{\charge{-45=}{O}}"),
    (r"\chemfig{\charge{12.5}{O}}", r"\chemfig{\charge{12.5=}{O}}"),
    (r"\chemfig{\charge{+90}{O}}", r"\chemfig{\charge{+90=}{O}}"),
])
def test_a_bare_angle_of_any_numeric_shape_gets_an_empty_symbol(body, expected):
    """Every numeric angle shape (signed, decimal) gets the ``=`` with an
    empty symbol -- the angle is kept verbatim and no charge glyph is
    attached, so a pure angle never gains a phantom symbol.

    Direction: unpatched code left each body byte-identical, so the equality
    to the ``=``-appended ``expected`` fails against it.
    """
    fixed, notes = repair(body)
    assert fixed == expected
    assert notes


def test_a_symbol_with_no_angle_gets_the_default_angle():
    r"""``\charge{\ominus}{O}`` -- a charge SYMBOL but no angle/separator.

    chemfig requires ``angle=symbol`` and aborts otherwise with the misleading
    "Argument of \charge_g has an extra }" -- the same message the colon-mistake
    case triggers.  Here the chemically meaningful part is already present and
    only the mandatory placement angle is missing, so the fix supplies the
    conventional 90 (north).  The symbol is a control word, so it is also
    math-wrapped.  Verified against a live chemfig install: the bare form fails,
    the repaired form renders.
    """
    fixed, notes = repair(r"\chemfig{\charge{\ominus}{O}}")
    assert fixed == r"\chemfig{\charge{90=$\ominus$}{O}}"
    assert any("no angle" in n for n in notes)


def test_a_text_mode_symbol_with_no_angle_gets_the_angle_but_no_wrap():
    """A plain ``+`` still needs the angle, but must NOT be math-wrapped."""
    fixed, notes = repair(r"\chemfig{\charge{+}{C}}")
    assert fixed == r"\chemfig{\charge{90=+}{C}}"
    assert any("no angle" in n for n in notes)
    assert not any("math mode" in n for n in notes)


def test_every_symbol_only_item_in_a_multi_charge_spec_gets_an_angle():
    fixed, _ = repair(r"\chemfig{\charge{\oplus,\ominus}{N}}")
    assert fixed == r"\chemfig{\charge{90=$\oplus$,90=$\ominus$}{N}}"


def test_Charge_variant_is_repaired_too():
    """``\\Charge`` (chemfig.tex:2156) shares the same argument grammar."""
    fixed, notes = repair(r"\chemfig{\Charge{90:\|}{O}}")
    assert fixed == r"\chemfig{\Charge{90=\|}{O}}"
    assert notes


# ---------------------------------------------------------------------------
# Conditional math wrap.  The load-bearing constraint.
# ---------------------------------------------------------------------------

def test_a_math_symbol_is_wrapped():
    fixed, notes = repair(r"\chemfig{\charge{90=\ominus}{O}}")
    assert fixed == r"\chemfig{\charge{90=$\ominus$}{O}}"
    assert any("math mode" in n for n in notes)


def test_both_repairs_apply_together():
    fixed, notes = repair(r"\chemfig{\charge{90:\ominus}{O}}")
    assert fixed == r"\chemfig{\charge{90=$\ominus$}{O}}"
    assert len(notes) == 2


@pytest.mark.parametrize("payload", ["-", "+", "2+", "2-", "q"])
def test_text_mode_payloads_are_never_wrapped(payload):
    """The regression guard for the whole design.

    ``-`` renders DIFFERENTLY in math mode (verified byte-for-byte: a text
    hyphen is not a math minus), so wrapping it would silently alter a diagram
    that already compiled correctly.
    """
    body = r"\chemfig{\charge{90=" + payload + r"}{O}}"
    fixed, notes = repair(body)
    assert fixed == body
    assert notes == ()


def test_an_already_wrapped_symbol_is_not_double_wrapped():
    body = r"\chemfig{\charge{90=$\ominus$}{O}}"
    fixed, notes = repair(body)
    assert fixed == body
    assert notes == ()


def test_a_style_command_is_wrapped():
    """``\\scriptstyle`` is a control word and fails in text mode like any other."""
    fixed, _ = repair(r"\chemfig{\charge{90=\scriptstyle\oplus}{N}}")
    assert fixed == r"\chemfig{\charge{90=$\scriptstyle\oplus$}{N}}"


@pytest.mark.parametrize("payload", ["^{2+}", "^-", "_3", "^{2-}"])
def test_a_bare_superscript_or_subscript_is_wrapped(payload):
    r"""A charge such as ``Ca^{2+}`` carries no control word but still needs math.

    ``^`` and ``_`` are illegal outside math mode, so ``\charge{90=^{2+}}{Ca}``
    dies with "Missing $ inserted" -- the same message a stray ``\ominus``
    produces, and just as silent about the charge argument being at fault.  The
    original guard fired only on control words and let this through; the fix is
    safe because a text-mode ``^``/``_`` cannot compile either way, so wrapping
    an already-invalid payload cannot break working input.
    """
    body = r"\chemfig{\charge{90=" + payload + r"}{Ca}}"
    fixed, notes = repair(body)
    assert fixed == r"\chemfig{\charge{90=$" + payload + r"$}{Ca}}"
    assert any("math mode" in n for n in notes)


# ---------------------------------------------------------------------------
# Robustness.  A repair must never be able to break a render.
# ---------------------------------------------------------------------------

def test_unbalanced_braces_are_left_alone():
    """Malformed input is a different error; guessing would invent structure."""
    body = r"\chemfig{\charge{90:\|"
    fixed, notes = repair(body)
    assert fixed == body
    assert notes == ()


def test_a_body_with_no_charge_is_untouched():
    body = r"\chemfig{*6(-=-=-=)}"
    assert repair(body) == (body, ())


def test_autofix_matches_the_ring_lint_signature():
    """The renderer treats both chemfig fixers alike, so the shapes must match."""
    fixed, applied, warnings = autofix(r"\chemfig{\charge{90:\|}{O}}")
    assert fixed == r"\chemfig{\charge{90=\|}{O}}"
    assert applied
    assert warnings == ()


# ---------------------------------------------------------------------------
# Bare superscript/subscript before an active bond character.
#
# Inside ``\chemfig`` the bond chars ``- = ~ < >`` are active, so a bare
# ``O^-`` -- the natural way to write an anion -- makes TeX's ``^`` primitive
# grab an active token and abort with "Missing { inserted".  ``O^{-}`` and
# ``N^+`` both compile (``+`` is ordinary), verified against a live install.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body,expected", [
    (r"\chemfig{O^-}", r"\chemfig{O^{-}}"),
    (r"\chemfig{O^=}", r"\chemfig{O^{=}}"),
    (r"\chemfig{X_-}", r"\chemfig{X_{-}}"),
    (r"\chemfig{*6(-N^+=-O^-=-=)}", r"\chemfig{*6(-N^+=-O^{-}=-=)}"),
])
def test_a_bare_bond_char_script_is_braced(body, expected):
    fixed, notes = autofix(body)[:2]
    assert fixed == expected
    assert any("bond character" in n for n in notes)


@pytest.mark.parametrize("body", [
    r"\chemfig{N^+}",          # + is ordinary, compiles bare
    r"\chemfig{O^{-}}",        # already braced
    r"\chemfig{C^1}",          # digit, compiles bare
    r"\chemfig{S_2}",          # digit subscript
    r"\chemfig{$O^-$}",        # inside a math span, - is legal
    r"\chemfig{*6(-=-=-=)}",   # no script at all
])
def test_a_safe_script_is_left_byte_identical(body):
    """The rewrite must not touch payloads that already compile."""
    fixed, notes, warnings = autofix(body)
    assert fixed == body
    assert notes == ()
    assert warnings == ()


@pytest.mark.parametrize("body", [
    r"\chemfig{A=^-B}",              # =^ forces the 2nd stroke above the axis
    r"\chemfig{A=_-B}",              # =_ forces it below
    r"\chemfig{*6(-=^-=^-=^-)}",    # benzene drawn with =^ side modifiers
    r"\chemfig{A-^=B}",             # any bond char before ^ is a modifier
])
def test_a_double_bond_side_modifier_is_not_braced(body):
    r"""``=^`` / ``=_`` place a double bond's second stroke above/below the axis;
    the ``^``/``_`` there is a chemfig BOND modifier, not an atom script, and the
    char after it is the NEXT bond.  Bracing it (``=^{-}``) would swallow that
    bond into a phantom label, open the ring and draw a different molecule
    (a benzene ``*6(-=^-=^-=^-)`` collapsed to a 4-bond open chain).  The
    discriminator is the char BEFORE the script: an atom precedes a real
    anion script (``O^-``), a bond precedes a side modifier."""
    fixed, notes, warnings = autofix(body)
    assert fixed == body
    assert notes == ()
    assert warnings == ()


def test_an_escaped_caret_is_not_treated_as_a_script():
    r"""``\^`` is a text accent, not a superscript, so it must be left alone."""
    body = r"\chemfig{A\^-B}"
    fixed, notes, _ = autofix(body)
    assert fixed == body
    assert notes == ()


def test_a_bond_char_script_inside_a_comment_is_not_braced():
    r"""A ``^-`` written inside a LaTeX ``%`` comment is discarded by TeX and is
    not a script at all, so the bond-script bracer must leave it untouched --
    otherwise a correct render (e.g. a benzene whose trailing comment merely
    MENTIONS an anion label) gets a spurious autofix note, and reciprocally a
    comment could mask a real occurrence on another line."""
    # 1. comment-only mention: no rewrite, no note.
    body = r"*6(-=-=-=) % benzene; an anion label like O^- would need bracing"
    fixed, notes, warnings = autofix(body)
    assert fixed == body
    assert notes == ()
    assert warnings == ()

    # 2. reciprocal: a comment mention plus a REAL O^- on the next line --
    # only the real one is braced.
    body2 = "*6(-=-=-=) % note O^-\nO^-"
    fixed2, notes2, _ = autofix(body2)
    assert fixed2 == "*6(-=-=-=) % note O^-\nO^{-}"
    assert len(notes2) == 1

    # 3. an escaped \% is a literal percent, not a comment, so a following
    # O^- is a real script and is braced.
    body3 = r"50\% O^-"
    fixed3, notes3, _ = autofix(body3)
    assert fixed3 == r"50\% O^{-}"
    assert len(notes3) == 1


def test_a_bond_char_script_inside_mhchem_is_not_braced():
    r"""A ``^-`` inside a ``\ce{}`` / ``\pu{}`` mhchem span is legal mhchem
    charge markup -- the bond characters are active only inside ``\chemfig``,
    not in mhchem -- so the bond-script bracer must leave it untouched.

    Bracing it both mangles the equation and emits a false "cannot be a
    superscript argument inside \chemfig" note for a construct that was never
    in \chemfig at all.  Regression for the mixed-body bug where
    ``\ce{Cl^- + Na^+ -> NaCl}`` had its ``Cl^-`` rewritten to ``Cl^{-}``."""
    # 1. pure mhchem equation with a monoanion: no rewrite, no note.
    body = r"\ce{Cl^- + Na^+ -> NaCl}"
    fixed, notes, warnings = autofix(body)
    assert fixed == body
    assert notes == ()
    assert warnings == ()

    # 2. \pu{} (physical unit) span is likewise left alone.
    body_pu = r"\pu{1e-3 mol/L}"
    assert autofix(body_pu)[0] == body_pu
    assert autofix(body_pu)[1] == ()

    # 3. mixed body: the mhchem ion is preserved, the REAL chemfig anion is
    # still braced -- exactly one rewrite, and only inside \chemfig.
    body_mix = r"\ce{SO4^2-} + \chemfig{*6(-=-=-O^-)}"
    fixed_mix, notes_mix, _ = autofix(body_mix)
    assert fixed_mix == r"\ce{SO4^2-} + \chemfig{*6(-=-=-O^{-})}"
    assert len(notes_mix) == 1

    # 4. a bare \chemfig anion (no mhchem) is unaffected by the new skip.
    assert autofix(r"\chemfig{O^-}")[0] == r"\chemfig{O^{-}}"


def test_a_charge_inside_a_comment_is_not_repaired():
    r"""A ``\charge`` written inside a LaTeX ``%`` comment is discarded by TeX
    and never drawn, so ``repair`` must leave it untouched -- otherwise a
    correct body whose trailing comment merely MENTIONS the colon-form charge
    gets its comment source rewritten (``0:`` -> ``0=$...$``) and false autofix
    notes fire for a charge that does not exist.  This closes the last of the
    four scanner comment blind spots (the ring counter, ring detection and the
    bond-script bracer were fixed earlier; ``repair`` itself still saw comment
    text)."""
    # 1. comment-only mention on a correct benzene: no rewrite, no note.
    body = r"\chemfig{*6(-=-=-=)} % tip: \charge{0:\ominus}{O} shows an anion"
    out, notes = repair(body)
    assert out == body
    assert notes == ()

    # 2. reciprocal: a comment mention plus a REAL broken charge on the next
    # line -- only the real one is repaired, and the comment stays verbatim.
    body2 = "\\chemfig{*6(-=-=-=)} % see \\charge{0:\\oplus}{N}\n\\charge{90:\\ominus}{O}"
    out2, notes2 = repair(body2)
    assert out2 == (
        "\\chemfig{*6(-=-=-=)} % see \\charge{0:\\oplus}{N}\n"
        "\\charge{90=$\\ominus$}{O}"
    )
    # both notes describe the single real charge (separator + math wrap).
    assert any("separator" in n for n in notes2)
    assert "\\oplus" not in "".join(notes2)  # the comment charge never surfaced

    # 3. an escaped \% is a literal percent, not a comment, so a following
    # \charge is real and IS repaired.
    body3 = r"50\% yield \charge{90:\ominus}{O}"
    out3, notes3 = repair(body3)
    assert out3 == r"50\% yield \charge{90=$\ominus$}{O}"
    assert notes3

    # 4. autofix (the renderer entry point) agrees: comment-only body clean.
    fixed, applied, warnings = autofix(body)
    assert fixed == body
    assert applied == ()
    assert warnings == ()


def test_bond_script_bracing_does_not_disturb_ring_bond_counting():
    """The rewrite runs before the ring lint; the braced ``{-}`` must be skipped
    by the bond counter, not tallied as a ring bond."""
    from app.utils.chemfig_lint import scan_rings

    body = r"\chemfig{*6(-N^+=-O^-=-=)}"
    fixed, _, _ = autofix(body)
    # The fixed body has a correctly-closed 6-ring: the braced charge must not
    # add a phantom bond.
    rings = scan_rings(fixed)
    assert rings
    assert rings[0].bonds == rings[0].expected


@needs_tex
@pytest.mark.parametrize("body", [
    r"\chemfig{O^-}",
    r"\chemfig{*6(-N^+=-O^-=-=)}",
])
def test_bare_bond_char_script_renders_after_repair(renderer, body):
    """End-to-end: the autofixed body must actually COMPILE."""
    fixed, notes, _ = autofix(body)
    assert notes, "fixture is not actually broken; the test would be vacuous"
    result = renderer.render("chemfig", fixed, fmt="png", use_cache=False)
    assert result.ok, result.error


@needs_tex
def test_the_renderer_braces_bare_bond_scripts_itself(renderer):
    """The integration point: the BROKEN body passed straight to render() must
    succeed, which only happens if _lint_chemfig wired this repair in."""
    result = renderer.render(
        "chemfig", r"\chemfig{*6(-N^+=-O^-=-=)}", fmt="png", use_cache=False,
    )
    assert result.ok, result.error
    assert result.autofixes, "the correction must be reported, not silent"


def test_charge_repair_does_not_disturb_ring_bond_counting():
    """The two fixers must compose.

    A charge sits inside a ring here, so a repair that shifted indices or
    inserted a bond character would corrupt the ring lint's count.
    """
    from app.utils.chemfig_lint import scan_rings

    body = r"\chemfig{*6(-=-(-[:30]NO_2)-\charge{90:+}{C}-=)}"
    before = scan_rings(body)
    fixed, _ = repair(body)
    after = scan_rings(fixed)
    assert [r.bonds for r in before] == [r.bonds for r in after]
    assert [r.expected for r in after] == [r.expected for r in before]


# ---------------------------------------------------------------------------
# End-to-end: the repaired body must actually COMPILE.
# ---------------------------------------------------------------------------

@needs_tex
@pytest.mark.parametrize("body", [
    r"\chemfig{\charge{90:\|}{O}}",
    r"\chemfig{\charge{90:\|,180:\|,270:\|}{O}}",
    r"\chemfig{\charge{90=\ominus}{O}}",
    r"\chemfig{\charge{90:\ominus}{O}}",
    r"\chemfig{\charge{45:2pt:\|}{S}}",
    r"\chemfig{\charge{90=\scriptstyle\oplus}{N}}",
    r"\chemfig{\charge{90=^{2+}}{Ca}}",
    r"\chemfig{\charge{270=^-}{Cl}}",
    r"\chemfig{\charge{\ominus}{O}}",
    r"\chemfig{\charge{\oplus,\ominus}{N}}",
    r"\chemfig{\charge{90}{N}-H}",
])
def test_broken_charge_renders_after_repair(renderer, body):
    """Asserts the render succeeds, not merely that the text changed.

    Without this the repair could emit plausible-looking LaTeX that still does
    not compile, and every string assertion above would still pass.
    """
    fixed, notes = repair(body)
    assert notes, "fixture is not actually broken; the test would be vacuous"
    result = renderer.render("chemfig", fixed, fmt="png", use_cache=False)
    assert result.ok, result.error


@needs_tex
def test_the_renderer_applies_the_repair_itself(renderer):
    """The integration point, not just the module.

    Passing the BROKEN body straight to render() must succeed, which only
    happens if _lint_chemfig wired the charge repair in.
    """
    result = renderer.render(
        "chemfig", r"\chemfig{\charge{90:\|,180:\|}{O}}",
        fmt="png", use_cache=False,
    )
    assert result.ok, result.error
    assert result.autofixes, "the correction must be reported, not silent"


@needs_tex
def test_an_undefined_command_is_not_papered_over(renderer):
    """Scope guard.

    ``\\+`` is not a chemfig command at all.  That is an undefined control
    sequence, not a separator mistake, and guessing what was meant would trade
    a clear error for a wrong structure.  It must still fail.
    """
    result = renderer.render(
        "chemfig", r"\chemfig{\charge{90:\+}{C}}", fmt="png", use_cache=False,
    )
    assert not result.ok
    assert result.error_kind == "compile"


# ---------------------------------------------------------------------------
# Double optional-bracket collapse.  chemfig accepts ONE optional argument;
# the natural wrong guess ``\chemfig[][draw=red]{...}`` -- copied from macros
# that take two optionals -- aborts fatally with the misleading "Undefined
# control sequence \CF_currentstringangle".  Verified against a live install:
# single ``[X]{...}`` renders, ANY second bracket is fatal.  Collapsing an
# EMPTY bracket is unambiguous; two non-empty brackets stay untouched.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("body,expected", [
    (r"\chemfig[][draw=blue,line width=0.5pt]{*5(-S-=-=)}",
     r"\chemfig[draw=blue,line width=0.5pt]{*5(-S-=-=)}"),
    (r"\chemfig[][red]{*5(-S-=-=)}", r"\chemfig[red]{*5(-S-=-=)}"),
    (r"\chemfig[draw=blue][]{*5(-S-=-=)}", r"\chemfig[draw=blue]{*5(-S-=-=)}"),
    (r"\chemfig[][]{*5(-S-=-=)}", r"\chemfig[]{*5(-S-=-=)}"),
])
def test_empty_double_optional_bracket_is_collapsed(body, expected):
    fixed, applied, warnings = autofix(body)
    assert fixed == expected
    assert applied, "the collapse must be reported, not silent"
    assert warnings == ()


@pytest.mark.parametrize("body", [
    r"\chemfig[draw=red]{*5(-S-=-=)}",   # single optional
    r"\chemfig{*5(-S-=-=)}",             # no optional
    r"\chemfig[a][b]{X}",                # two non-empty -> ambiguous, untouched
])
def test_single_or_ambiguous_optional_is_untouched(body):
    fixed, applied, _ = autofix(body)
    assert fixed == body
    assert not any("two optional" in n for n in applied)


@needs_tex
def test_double_optional_bracket_renders_after_collapse(renderer):
    """End-to-end: the double-bracket form must compile after the collapse."""
    fixed, applied, _ = autofix(
        r"\chemfig[][draw=blue,line width=0.5pt]{*5(-S-=-=)}")
    assert applied
    result = renderer.render("chemfig", fixed, fmt="png", use_cache=False)
    assert result.ok, result.error


@needs_tex
def test_the_renderer_collapses_double_optional_itself(renderer):
    """The integration point: the BROKEN body straight to render() succeeds."""
    result = renderer.render(
        "chemfig", r"\chemfig[][draw=blue,line width=0.5pt]{*5(-S-=-=)}",
        fmt="png", use_cache=False,
    )
    assert result.ok, result.error
    assert result.autofixes, "the correction must be reported, not silent"
