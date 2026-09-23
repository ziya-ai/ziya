"""D-234 (G-c3d6eb): pale-fill label ink for \\tikzset-defined + \\definecolor styles.

Regression guard for the still-broken tail of D-234 on tikz-w3-02.  Two real
gaps let a node whose pale fill comes from a *named* style wash its label out on
the dark page:

  1. The style is declared inside ``\\tikzset{...}`` (BRACE-delimited) or its
     value already carries nested braces from the step-8 clamp
     (``draw={rgb,...}``), so the old ``\\{[^{}]*\\}`` style capture -- which only
     scanned ``[...]`` option blocks -- never reached it.
  2. The fill is a ``\\definecolor`` colour (``fill=motifA!12``) that the
     label-ink helper could not resolve because the body's definecolor map was
     never threaded in, so it was skipped as "unresolvable".

The dark-page default ink (#EDEDED) then lands on the pale chip (#E4EAEF for
motifA!12) at ~1.04:1 and the ``$m_i$`` labels vanish.  The fix scans every
``NAME/.style={...}`` in the body with a balanced-brace walk and resolves
definecolor fills, injecting ``text=black`` (17.31:1 on that chip) per style.

Both themes are asserted: the dark page must gain the black label ink, and the
light page (whose black default ink is already legible on a pale fill) must be
left untouched -- no ``text=white`` and no spurious ``text=black``.
"""
from app.utils.latex_color import normalize_colors

# A \tikzset-defined style with a \definecolor pale fill, referenced by \node.
# This is the tikz-w3-02 mechanism reduced to its essentials.
TIKZSET_DEFINECOLOR_PALE = r"""
\definecolor{motifA}{HTML}{1F4E79}
\tikzset{
  cell/.style={draw=motifA, thick, fill=motifA!12, minimum size=8mm},
  ring/.style={draw=motifA, dashed},
}
\node[cell] (n0) at (0,0) {$m_0$};
\draw[ring] (0,0) circle (6.5mm);
"""


def test_dark_injects_black_ink_into_tikzset_definecolor_pale_fill_style():
    """DARK: the pale-fill named style must gain a dark label ink.

    Fails before the fix (the \tikzset style is never scanned and the
    definecolor fill never resolves, so no text= is added and #EDEDED washes the
    label out at ~1.04:1)."""
    out, applied = normalize_colors(TIKZSET_DEFINECOLOR_PALE, theme="dark")
    # the cell style carries a pale definecolor fill -> dark label ink injected
    assert "text=black" in out, out
    assert any("motifA!12" in a and "text=black" in a for a in applied), applied
    # per-style discipline: the UNFILLED ring style keeps the light default ink
    ring = out[out.index("ring/.style"):]
    ring = ring[:ring.index("}")]
    assert "text=" not in ring, ring


def test_light_leaves_tikzset_definecolor_pale_fill_untouched():
    """LIGHT: black default ink is already legible on a pale fill -> no change."""
    out, _ = normalize_colors(TIKZSET_DEFINECOLOR_PALE, theme="light")
    assert "text=white" not in out, out
    assert "text=black" not in out, out


def test_dark_style_with_explicit_text_is_respected():
    """An author-set ``text=`` on the style is never overridden (both gates)."""
    spec = r"""
\definecolor{motifA}{HTML}{1F4E79}
\tikzset{ cell/.style={fill=motifA!12, text=motifA} }
\node[cell] {x};
"""
    out, _ = normalize_colors(spec, theme="dark")
    assert "text=black" not in out, out
