"""D-338 (group G-92023d): adjacent top-level ``\\chemfig`` molecules must not abut.

Root cause (matches the triage hypothesis): the chemfig profile has no wrapping
environment, so a body containing two consecutive top-level ``\\chemfig{...}``
statements separated only by whitespace lands straight in the ``standalone``
crop box.  There the two molecule boxes are laid out with only the inter-token
space between them; their bonds reach the box edge, so the pair abuts and reads
as ONE bonded structure (chemfig-w4-09).

The fix (``separate_adjacent_chemfig``) inserts a ``\\par\\medskip`` between two
adjacent bare top-level molecules so they stack vertically as the distinct
structures the author wrote.  It is deliberately narrow: only depth-0
``\\chemfig`` calls with pure-whitespace between them are separated; any
intentional content between molecules, or a molecule nested in another group, is
left untouched, and a single-molecule body is a no-op.

These assertions FAIL against the pre-fix code (no separation pass -> the two
molecules stay adjacent) and pass with it.  A theme defect is only resolved when
both render, but this is a structural spacing fix in the shared preprocessor
that never reaches the rasteriser, so it is theme-independent -- the same
separated body is emitted on the light and dark paths.
"""
import json
import os

from app.utils.chemfig_lint import separate_adjacent_chemfig

_SPEC_DIR = os.path.join(".ziya", "gfx-sweep", "specs", "chemfig")


def _load(spec_id: str) -> str:
    with open(os.path.join(_SPEC_DIR, f"{spec_id}.json")) as fh:
        return json.load(fh)["definition"]


def test_two_adjacent_molecules_get_a_separator():
    body = "\\chemfig{*6(-=-=-=)}\n\\chemfig{A-[:30]B}"
    out, applied = separate_adjacent_chemfig(body)
    assert out != body, "adjacent molecules must be separated"
    assert "\\par" in out
    assert len(applied) == 1
    # Both molecules survive intact, in order.
    assert out.index("*6(-=-=-=)") < out.index("\\par") < out.index("A-[:30]B")


def test_w4_09_spec_after_unquote_is_separated():
    """The real recovery spec: quoted numerics are stripped first, then the two
    resulting top-level molecules are separated."""
    from app.utils.chemfig_lint import unquote_numeric_fields

    body = _load("chemfig-w4-09")
    body, _ = unquote_numeric_fields(body)
    # Two top-level \chemfig calls remain, abutting.
    assert body.count("\\chemfig{") == 2
    out, applied = separate_adjacent_chemfig(body)
    assert applied, "the two adjacent molecules must be separated"
    assert "\\par" in out
    # The \setchemfig setter preceding them is untouched.
    assert out.count("\\setchemfig") == body.count("\\setchemfig")


def test_single_molecule_is_a_noop():
    body = "\\chemfig{A-B-C}"
    out, applied = separate_adjacent_chemfig(body)
    assert out == body
    assert applied == ()


def test_intentional_content_between_is_kept():
    """Text (or an explicit spacer) between molecules means the author arranged
    the layout -- no break is inserted."""
    body = "\\chemfig{A-B} and \\chemfig{C-D}"
    out, applied = separate_adjacent_chemfig(body)
    assert out == body
    assert applied == ()


def test_already_separated_is_a_noop():
    body = "\\chemfig{A-B}\\par\n\\chemfig{C-D}"
    out, applied = separate_adjacent_chemfig(body)
    assert out == body
    assert applied == ()


def test_nested_chemfig_not_separated():
    """A \\chemfig nested in another group (e.g. a \\chemname argument) is not a
    top-level molecule and must not trigger separation."""
    body = "\\chemname{\\chemfig{A-B}}{Ethane}"
    out, applied = separate_adjacent_chemfig(body)
    assert out == body
    assert applied == ()


def test_three_adjacent_molecules_get_two_breaks():
    body = "\\chemfig{A}\n\\chemfig{B}\n\\chemfig{C}"
    out, applied = separate_adjacent_chemfig(body)
    assert out.count("\\par") == 2
    assert applied and "2 paragraph breaks" in applied[0]
