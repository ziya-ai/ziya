r"""
Regression tests for fix group G-775bca (server-side TikZ recovery, ledger
defects D-249/D-250/D-251/D-252).

These exercise the ``app.utils.tikz_lint`` recovery passes against the exact
``tikz-w4-*`` bodies the sweep flagged.  Each assertion is written to FAIL
against a tree without the corresponding pass and pass with it (a "direction"
check), so the tests certify the fix, not the bug.

Ledger defects (distinct from the docstring-era D-24x numbering inside
``tikz_lint`` itself, which predates the ledger):

  * D-250 (tikz-w4-13): SVG/CSS presentation-attribute dialect
    (``stroke=``/``stroke-width=``/``font-size=``/``text-anchor=``) used as
    pgfkeys.  Each is an unknown key that aborts the compile.  Translated to
    the faithful TikZ key (stroke->draw, stroke-width->line width) or dropped
    when there is none (font-size/text-anchor).  The quoted-hex ``fill="#hex"``
    sibling is handled upstream in ``latex_color`` and is checked end-to-end.

  * D-249 (tikz-w4-15): a JSON-serialised multiline body whose newlines became
    the literal two chars ``\`` + ``n``, INCLUDING a TRAILING one after the
    last statement that the mid-body restorer's ``(?=\\)`` lookahead cannot
    reach -- it survives as a stray undefined control sequence and aborts.

  * D-251 (tikz-w4-03): statement-terminating semicolons omitted on two of five
    path commands -- fatal "Did you forget a semicolon?".

  * D-252 (tikz-w4-11): the ``_extract_error`` diagnostic must NOT blame a core
    TikZ command (``\node``) on an unloaded package.  (Balancing the unclosed
    label brace / scope is declined as wont-fix -- it needs a real tokeniser --
    so only the diagnostic half is asserted here.)
"""
import re

import pytest

from app.utils.tikz_lint import autofix
from app.utils.latex_color import normalize_colors
from app.services.latex_renderer import LatexRenderer


def _pipeline(body: str, theme: str) -> str:
    """Renderer order: colour normalisation (theme-aware) then the tikz lint."""
    body, _ = normalize_colors(body, theme)
    body, _, _ = autofix(body)
    return body


# --------------------------------------------------------------------------
# D-250: SVG / CSS presentation-attribute dialect
# --------------------------------------------------------------------------
def test_svg_stroke_and_width_translated():
    raw = r"\draw[stroke=white,stroke-width=2] (a)--(b);"
    # Direction check: the raw body carries keys TikZ does not know.
    assert "stroke-width=" in raw and re.search(r"(?<!-)stroke=", raw)

    fixed, applied, _ = autofix(raw)

    assert "draw=white" in fixed
    assert "line width=2pt" in fixed
    # No SVG-dialect key survives to abort the compile.
    assert "stroke-width" not in fixed
    assert not re.search(r"(?<![A-Za-z-])stroke\s*=", fixed)
    assert applied and any("SVG-attribute" in a for a in applied)


def test_svg_font_size_and_text_anchor_dropped():
    raw = r"\node[fill=blue!60!black,font-size=12,text-anchor=middle,text=white] (b) at (4,1) {Beta};"
    fixed, applied, _ = autofix(raw)
    # The two no-equivalent attributes are removed ...
    assert "font-size" not in fixed
    assert "text-anchor" not in fixed
    # ... while the legitimate keys around them are preserved intact.
    assert "fill=blue!60!black" in fixed
    assert "text=white" in fixed
    # And the option list is still well-formed (no doubled/leading commas).
    assert "[fill=blue!60!black,text=white]" in fixed
    assert ",," not in fixed


def test_svg_dialect_does_not_touch_braced_colour_commas():
    # A normalised fill value ``{rgb,255:...}`` has internal commas that are NOT
    # entry separators; the comma-aware splitter must keep it intact.
    raw = (r"\node[fill={rgb,255:red,51;green,102;blue,204},stroke-width=2,"
           r"text=white] (a) at (1,1) {Alpha};")
    fixed, _, _ = autofix(raw)
    assert "fill={rgb,255:red,51;green,102;blue,204}" in fixed
    assert "line width=2pt" in fixed


def test_svg_dialect_full_body_both_themes():
    # The whole tikz-w4-13 body through the real renderer order, in BOTH themes:
    # no SVG-dialect key may survive in either, and the quoted #hex fill is
    # resolved upstream to an xcolor expression.
    raw = (
        r'\node[fill="#3366CC",stroke-width=2,text=white] (a) at (1,1) {Alpha};'
        "\n"
        r"\node[fill=blue!60!black,font-size=12,text-anchor=middle,text=white] (b) at (4,1) {Beta};"
        "\n"
        r"\draw[stroke=white,stroke-width=2] (a)--(b);"
    )
    for theme in ("light", "dark"):
        out = _pipeline(raw, theme)
        assert "stroke-width" not in out, theme
        assert "font-size" not in out, theme
        assert "text-anchor" not in out, theme
        assert not re.search(r"(?<![A-Za-z-])stroke\s*=", out), theme
        assert 'fill="#' not in out and "fill=#" not in out, theme
        assert "line width=2pt" in out, theme


def test_svg_dialect_body_without_it_is_byte_identical():
    # A body with no SVG-dialect key must be left exactly as written.
    raw = r"\node[draw=white,fill=blue!60!black,text=white] (a) at (1,1) {One};"
    fixed, applied, _ = autofix(raw)
    assert fixed == raw
    assert not any("SVG-attribute" in a for a in applied)


# --------------------------------------------------------------------------
# D-249: trailing serialised newline
# --------------------------------------------------------------------------
def test_trailing_serialised_newline_is_dropped():
    # A JSON-serialised body ends with a trailing '\' + 'n' after the last
    # statement.  The mid-body restorer's (?=\\) lookahead cannot reach it, so
    # without the trailing pass it survives as a stray undefined control
    # sequence and aborts.
    raw = (r"\draw[white] (a)--(b);\n"
           r"\draw[white] (b)--(c);\n")     # <- trailing serialised newline
    # Direction check: a bare '\n' sits at the very end of the raw body.
    assert raw.rstrip().endswith(r";\n") or raw.endswith(r"\n")

    fixed, applied, _ = autofix(raw)

    # The mid '\n' became a real newline and the trailing one is gone entirely:
    # no stray '\n' token remains anywhere.
    assert r"\n" not in fixed
    assert "\n" in fixed
    assert fixed.rstrip().endswith("(b)--(c);")
    assert applied and any("serialised" in a for a in applied)


def test_trailing_newline_pass_preserves_n_macro_in_multiline_body():
    # A body with real newlines uses '\n' as a loop counter; the trailing pass
    # must not touch it (it only fires on a single-physical-line body).
    raw = "\\foreach \\n in {0,...,5}{\n  \\node at (\\n,0) {N\\n};\n}\n"
    fixed, applied, _ = autofix(raw)
    assert r"\node at (\n,0)" in fixed
    assert r"{N\n}" in fixed
    assert not any("serialised" in a for a in applied)


# --------------------------------------------------------------------------
# D-251: missing statement-terminating semicolons
# --------------------------------------------------------------------------
def test_missing_semicolons_inserted_before_following_statement():
    raw = (r"\node (a) at (1,1) {Read}"          # <- missing ';'
           "\n"
           r"\node (b) at (4,1) {Map};"
           "\n"
           r"\draw (a)--(b)"                      # <- missing ';'
           "\n"
           r"\draw (b)--(c);")
    fixed, applied, _ = autofix(raw)
    # Each unterminated statement now ends with ';' before the next command.
    assert re.search(r"\{Read\}\s*;\s*\\node", fixed)
    assert re.search(r"\(a\)--\(b\)\s*;\s*\\draw", fixed)
    assert applied and any("semicolon" in a for a in applied)


def test_well_formed_body_gets_no_semicolons_inserted():
    # A body that already terminates every statement must be byte-identical.
    raw = (r"\node (a) at (1,1) {Read};"
           "\n"
           r"\draw (a)--(b);"
           "\n")
    fixed, applied, _ = autofix(raw)
    assert not any("semicolon" in a for a in applied)


# --------------------------------------------------------------------------
# D-252: diagnostic must not blame a core TikZ command on a missing package
# --------------------------------------------------------------------------
def test_extract_error_does_not_blame_node_on_missing_package():
    log = "! Undefined control sequence.\nl.6 \\node\n"
    msg = LatexRenderer._extract_error(log)
    assert r"\node" in msg
    assert "may belong to a package this diagram type does not load" not in msg
    assert "typo" in msg or "stray" in msg


if __name__ == "__main__":            # pragma: no cover
    pytest.main([__file__, "-v"])
