"""G-68 / D-048: quoted-numeric chemfig recovery.

chemfig has no notion of a quoted number.  A ring size ``*"6"(``, a bond angle
``[:"30"]`` and a setter dimension ``atom sep="2.4em"`` -- all common model
artefacts of treating the chemfig source like JSON -- are FATAL as written:
``*"6"(`` never matches the ring grammar (so no ring lint can even see it) and
aborts the compile with ``Missing number``.

``unquote_numeric_fields`` strips the quotes around a *purely numeric* body
while leaving a quoted verbatim TEXT atom untouched.

Direction: each recovery test first asserts the UNPATCHED behaviour (the ring
is invisible to ``scan_rings`` while quoted / the quotes survive), so the test
fails against code lacking the unquote step and passes with it.
"""
from __future__ import annotations

from app.utils.chemfig_lint import scan_rings, unquote_numeric_fields


def test_quoted_ring_size_is_invisible_before_and_recovered_after():
    """*"6"( is undetectable as a ring until the quotes are stripped."""
    quoted = r'*"6"(-=-=-=)'
    # UNPATCHED direction: the ring grammar requires a digit right after '*',
    # so a quoted size matches nothing -- the fatal 'Missing number' case.
    assert scan_rings(quoted) == ()

    fixed, applied = unquote_numeric_fields(quoted)
    assert fixed == r"*6(-=-=-=)"
    assert applied  # a fix was recorded
    rings = scan_rings(fixed)
    assert len(rings) == 1
    assert rings[0].size == 6
    # a 6-ring with six alternating bonds is fully closed
    assert rings[0].is_closed


def test_full_w4_09_spec_recovered():
    """The whole chemfig-w4-09 spec: ring size, angle and setter dimension."""
    spec = (
        '\\setchemfig{atom sep="2.4em"}\n'
        '\\chemfig{*"6"(-=-=-=)}\n'
        '\\chemfig{A-[:"30"]B}'
    )
    assert '"' in spec  # precondition: quotes present
    fixed, applied = unquote_numeric_fields(spec)
    assert '"' not in fixed
    assert fixed == (
        "\\setchemfig{atom sep=2.4em}\n"
        "\\chemfig{*6(-=-=-=)}\n"
        "\\chemfig{A-[:30]B}"
    )
    assert len(applied) == 3


def test_numeric_and_dimension_forms_unquoted():
    for quoted, bare in [
        (r'*"6"(', r"*6("),
        (r'[:"30"]', r"[:30]"),
        (r'[:"-30"]', r"[:-30]"),
        (r'="2.4em"', r"=2.4em"),
        (r'"12pt"', r"12pt"),
        (r'".5"', r".5"),
        (r'"3.14"', r"3.14"),
    ]:
        out, applied = unquote_numeric_fields(quoted)
        assert out == bare, f"{quoted!r} -> {out!r}, expected {bare!r}"
        assert applied


def test_text_literals_left_untouched():
    """A quoted verbatim TEXT atom is not numeric and must be preserved.

    This is the safety direction: an over-eager strip would corrupt a
    legitimate chemfig ``"..."`` verbatim node.
    """
    for literal in ['"cat"', '"H2O"', '"label 6"', '"6a"', '"a6"', '"e-"']:
        out, applied = unquote_numeric_fields(literal)
        assert out == literal, f"text literal {literal!r} was altered to {out!r}"
        assert applied == ()


def test_noop_on_unquoted_body():
    body = r"\chemfig{*6(-=-=-=)}"
    out, applied = unquote_numeric_fields(body)
    assert out == body
    assert applied == ()


def test_idempotent():
    spec = '\\chemfig{*"6"(-=-=-=)}'
    once, _ = unquote_numeric_fields(spec)
    twice, applied2 = unquote_numeric_fields(once)
    assert twice == once
    assert applied2 == ()
