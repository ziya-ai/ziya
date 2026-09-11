r"""
Regression tests for fix group G-874348 (chemfig recovery, defect D-039).

Signature: ``chemname-long-label-overflows-crop-clipped``
Specs: chemfig-w4-08, chemfig-w4-14.

Two independent, confirmed failures made a ``\chemname`` caption render as a
"success" while silently DROPPING text -- content loss = structural failure:

1. CAPTION CLIP (both specs, the structural core).  chemfig stacks the name
   under the molecule via ``\CF_parsemolname``, which sets every caption line in
   ``\hbox to\CF_wdstuffbox{\hss#1\hss}`` -- a box FIXED to the molecule width.
   A caption WIDER than the molecule overflows that box symmetrically (the
   ``\hss`` glue), so the enclosing ``\vtop`` still reports only the molecule
   width and the standalone crop -- a UNIFORM ``border`` that cannot grow one
   side -- slices the caption at BOTH ends.  Fixed in the chemfig profile
   preamble (``latex_profiles.py``) by redefining ``\CF_parsemolname`` so a
   caption wider than the molecule is set at its NATURAL width (the ``\vtop``
   then grows to include it and the crop captures the whole caption), while the
   narrower-than-molecule case keeps the original centred behaviour verbatim.

2. MARKDOWN BOLD LEAK (chemfig-w4-14).  ``convert_markdown_bold`` was added and
   unit-tested under an earlier group but was NEVER wired into the renderer, so
   ``**water**`` still typeset two literal asterisks.  Now called on the chemfig
   path in ``latex_renderer._lint_chemfig``.

Note on the triage lead: the hypothesis also flagged smart quotes (U+201C/D)
and the em dash (U+2014) as un-transliterated.  An earlier deliberate decision
(see tests/test_latex_g74_chemfig_unicode_entities.py) leaves those UNTOUCHED
because pdflatex's default UTF-8 map typesets them safely (em dash ->
\textemdash in OT1); rewriting them would over-recover and regress G-74.  So
they are NOT the defect and are intentionally not changed here.

D-039 is a structural/recovery defect (theme-independent text preprocessing and
box geometry), so the both-theme render obligation is discharged at the shared
render stage; no contrast ratios apply.

Direction is verified explicitly: each assertion below fails against HEAD before
this change (the bold pass was unwired; the profile carried no
``\CF_parsemolname`` redefinition) and passes after it.
"""

from app.services.latex_profiles import PROFILES
from app.services.latex_renderer import LatexRenderer
from app.utils.latex_unicode import transliterate

_lint = LatexRenderer._lint_chemfig


def _pipeline(body: str) -> str:
    """Body as the renderer preprocesses a chemfig spec: transliterate + lint."""
    body, _ = transliterate(body)
    body, _applied, _warnings = _lint(body)
    return body


# --------------------------------------------------------------------------
# (2) markdown bold is now WIRED into the render pipeline (chemfig-w4-14)
# --------------------------------------------------------------------------

def test_markdown_bold_is_wired_into_lint_chemfig():
    """The renderer path -- not just the unit function -- must convert **bold**.

    Fails against HEAD: convert_markdown_bold existed but was never called by
    _lint_chemfig, so the caption kept its literal ``**`` markers.
    """
    out = _pipeline(r"\chemname{\chemfig{H_2O}}{**water** &amp; ice &#8594; steam &lt;br/&gt;}")
    assert r"\textbf{water}" in out          # bold recovered
    assert "**" not in out                    # no literal asterisks left
    # entity recovery from the earlier pass is preserved end-to-end
    assert r"\&" in out
    assert r"\rightarrow" in out


def test_aromatic_ring_star_marker_not_corrupted_by_bold_pass():
    """The shared ``**`` marker (aromatic-ring opener) must survive untouched."""
    out = _pipeline(r"\chemfig{**6(-=-=-=)}")
    assert "**6(" in out
    assert "textbf" not in out


# --------------------------------------------------------------------------
# (1) caption-clip fix lives in the chemfig profile preamble
# --------------------------------------------------------------------------

def _chemfig_preamble() -> str:
    return "\n".join(PROFILES["chemfig"].extra_preamble)


def test_chemfig_profile_redefines_parsemolname_for_wide_captions():
    """The profile must ship the \\CF_parsemolname redefinition that stops a
    wide \\chemname caption being cropped at both ends.

    Fails against HEAD: the chemfig profile had no such redefinition, so a
    caption wider than the molecule overflowed the molecule-width box and was
    clipped by the uniform crop.
    """
    pre = _chemfig_preamble()
    assert r"\CF_parsemolname" in pre         # the internal being repaired
    assert r"\CFZIYAnamebox" in pre           # the measuring box we allocate
    # wide branch: set the caption at its NATURAL width so the vtop grows
    assert r"\ifdim\wd\CFZIYAnamebox>\CF_wdstuffbox" in pre
    # narrow branch: original centred behaviour preserved verbatim
    assert r"\hbox to\CF_wdstuffbox{\hss#1\hss}" in pre
    # guarded + catcode-scoped so it degrades safely and matches chemfig's `_`
    assert r"\@ifundefined{CF_parsemolname}" in pre
    assert r"\catcode`\_=11" in pre


def test_chemfig_preamble_braces_balanced():
    """A malformed profile preamble would break EVERY chemfig render, so assert
    the redefinition block is brace-balanced."""
    pre = _chemfig_preamble()
    # count only unescaped braces
    import re
    opens = len(re.findall(r"(?<!\\)\{", pre))
    closes = len(re.findall(r"(?<!\\)\}", pre))
    assert opens == closes, f"unbalanced braces in chemfig preamble: {opens} vs {closes}"
