r"""
Regression tests for fix group G-09 (chemfig structural/recovery, iteration 41).

Backlog defect D-009, spec chemfig-w4-14:
``\chemname{\chemfig{H_2O}}{**water** &amp; ice &#8594; steam &lt;br/&gt;}``

The HTML entities (``&amp;``, ``&#8594;``, ``&lt;``/``&gt;``) were already
recovered by the earlier G-74 ``decode_entities`` work.  The remaining leak was
the MARKDOWN BOLD ``**water**``: nothing converted it, so the caption typeset
two literal asterisks flanking the word -- contradicting the author's obvious
intent and looking like broken output.  ``convert_markdown_bold`` now rewrites
``**text**`` to ``\textbf{text}`` on the chemfig path.

Direction is verified explicitly:

  * ``convert_markdown_bold`` did NOT exist before this change, so the test
    fails to import against HEAD -- it certifies the fix, not the input;
  * the aromatic-ring cases (``**6(...)``) that share the ``**`` marker are
    asserted UNCHANGED, so the recovery can never corrupt real chemfig syntax.

D-009 is a structural/recovery defect (theme-independent text preprocessing),
so the both-theme render obligation is discharged at the shared render stage;
no contrast ratios apply.
"""

import re

from app.utils.chemfig_lint import convert_markdown_bold, decode_entities


# --------------------------------------------------------------------------
# The fix: markdown bold -> \textbf on a chemfig caption (chemfig-w4-14)
# --------------------------------------------------------------------------

def test_markdown_bold_word_becomes_textbf():
    out, applied = convert_markdown_bold(r"\chemname{\chemfig{H_2O}}{**water**}")
    assert r"\textbf{water}" in out
    assert "**" not in out
    assert applied


def test_markdown_bold_with_hyphen_and_spaces_inside():
    out, _ = convert_markdown_bold("caption **well-known solvent** here")
    assert r"\textbf{well-known solvent}" in out
    assert "**" not in out


def test_full_w4_14_caption_recovers_end_to_end():
    """decode_entities then convert_markdown_bold on the real w4-14 caption."""
    body = r"\chemname{\chemfig{H_2O}}{**water** &amp; ice &#8594; steam &lt;br/&gt;}"
    body, _ = decode_entities(body)          # &amp; -> \&, &lt; -> \textless{}, ...
    out, applied = convert_markdown_bold(body)
    assert r"\textbf{water}" in out
    assert "**" not in out                   # the bold markers are gone
    assert r"\&" in out                       # entity recovery preserved
    assert applied


# --------------------------------------------------------------------------
# Safety: the shared ``**`` marker must never corrupt an aromatic ring
# --------------------------------------------------------------------------

def test_single_aromatic_ring_is_untouched():
    """``**6(...)`` is the aromatic-circle ring opener, not markdown bold."""
    src = r"\chemfig{**6(-=-=-=)}"
    out, applied = convert_markdown_bold(src)
    assert out == src
    assert not applied


def test_two_adjacent_aromatic_rings_not_spanned():
    """A greedy pass would fuse ``**6(...)**6(...)`` into \\textbf; a body-char
    guard (the ring body carries ``(``) prevents any match."""
    src = r"**6(-=-=-=)**6(-=-=-=)"
    out, applied = convert_markdown_bold(src)
    assert out == src
    assert not applied


def test_double_star_without_letters_left_alone():
    """No letters between the markers -> not a bold word, leave it verbatim."""
    src = "a ** -- ** b"
    out, applied = convert_markdown_bold(src)
    assert out == src
    assert not applied


def test_noop_on_plain_body():
    src = r"\chemfig{*6(-=-=-=)}"
    out, applied = convert_markdown_bold(src)
    assert out == src
    assert applied == ()
