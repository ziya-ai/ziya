r"""
Regression test for fix group G-a3e74b / defect D-038 (recovery): a CSS
``rgba()`` / ``rgb()`` call in chemfig's POSITIONAL 5th bond-colour field.

The gap
-------
``chemfig-w4-04`` authors the bond colour as a CSS call in chemfig's positional
5th bond field::

    \chemfig{C(-[:120,,,,rgba(220,20,60,0.8)]O)...}

The generic ``_RGB_CALL_RE`` pass rewrites every ``rgba()`` to a BARE xcolor
expression ``{rgb,255:red,220;green,20;blue,60}``.  That braced form is correct
after ``fill=``/``draw=`` (``fill={rgb,...}``), but in a chemfig positional
field there is no ``key=`` marker, so chemfig forwards the braced expression to
TikZ as an option and TikZ parses it as an unknown KEY -- ``I do not know the
key '/tikz/rgb'`` -- a FATAL abort with no image.

A bare colour NAME survives there (TikZ falls back to ``color=NAME``), which is
why ``chemfig-w4-05``'s ``-[:30,,,,navy]`` already recovers; a bare colour
EXPRESSION does not.  The fix wraps a bare positional ``rgb()``/``rgba()`` as an
explicit ``color={rgb,...}`` assignment -- the same shape the contrast clamp
already emits for a bare stroke colour.

Both themes are asserted: D-038 is a RECOVERY defect (the field must stop being
a fatal bare-brace key in either theme), and the dark render additionally passes
the resolved colour through the contrast clamp, which must still leave a valid
``color={rgb,...}`` assignment (never a bare brace).

Each assertion below FAILS against the pre-fix tree (the positional field holds
a bare ``{rgb,...}`` brace) and PASSES with the fix.
"""

from app.utils.latex_color import normalize_colors

# chemfig-w4-04 verbatim.
_W4_04 = (
    r"\chemfig{C(-[:120,,,,rgba(220,20,60,0.8)]O)"
    r"(=[:60,,,,rgba(0,0,139,1)]O)"
    r"-[,,,,rgba(34,139,34,0.6)]OH}"
)


def _assert_no_bare_positional_brace(body: str) -> None:
    """A ``{rgb,...}`` expression must never sit directly after a positional
    comma/bracket -- that is the fatal ``/tikz/rgb`` key.  It must always be
    introduced by an explicit ``color=`` (or another ``key=``)."""
    assert ",{rgb," not in body, f"bare positional rgb-brace leaked: {body}"
    assert "[{rgb," not in body, f"bare positional rgb-brace leaked: {body}"
    # the specific chemfig-field shape ``,,,,{rgb`` must be gone
    assert ",,,,{rgb" not in body, f"bare chemfig-field rgb-brace leaked: {body}"


def test_chemfig_positional_rgba_wrapped_as_color_light():
    body, applied = normalize_colors(_W4_04, theme="light")
    # the crimson bond field is now an explicit color= assignment
    assert ",,,,color={rgb,255:red,220;green,20;blue,60}" in body, body
    _assert_no_bare_positional_brace(body)
    # all three rgba() calls were consumed
    assert "rgba(" not in body


def test_chemfig_positional_rgba_wrapped_as_color_dark():
    body, applied = normalize_colors(_W4_04, theme="dark")
    # crimson is legible on the dark page so it is left as authored...
    assert ",,,,color={rgb,255:red,220;green,20;blue,60}" in body, body
    # ...and every field remains an explicit color= assignment, never a bare
    # brace, even after the dark contrast clamp rewrites the dark-navy bond.
    _assert_no_bare_positional_brace(body)
    assert "rgba(" not in body
    assert "color={rgb,255:" in body


def test_key_value_rgba_context_not_double_wrapped():
    """A ``key=rgba(...)`` must still yield ``key={rgb,...}`` -- the new bare
    pass must NOT fire after a ``=`` and turn it into ``fill=color={rgb,...}``."""
    body, _ = normalize_colors(r"\node[fill=rgba(10,20,30,0.5)] {x};", theme="light")
    assert "fill={rgb,255:red,10;green,20;blue,30}" in body, body
    assert "color=" not in body


def test_bare_named_positional_field_still_bare():
    """chemfig-w4-05's ``,,,,navy`` must stay a bare (CamelCased) NAME -- the
    rgb-call pass must not touch a name, and a bare name is legal as an option."""
    body, _ = normalize_colors(
        r"\chemfig{N(-[:30,,,,navy]H)}", theme="light")
    assert ",,,,Navy]" in body, body
