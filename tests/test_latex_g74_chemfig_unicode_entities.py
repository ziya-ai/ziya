r"""
Regression tests for fix group G-74 (chemfig recovery, iteration 17).

Backlog defect D-059 (recovery, chemfig): a chat-pasted chemfig label carries
two model artefacts that were each FATAL or lossy on a minimal TeX install, and
neither had a recovery layer:

  * superscript / subscript / thin-space codepoints (``kJ·mol⁻¹``, ``m²``,
    ``H₂O``, a U+2009 thin space) route through the TS1 / text-companion fonts,
    which a BasicTeX / minimal TeX Live install does NOT ship (the missing
    ``tcrm*`` fonts), so the whole render aborted.  ``latex_unicode`` now maps
    each to an ``\ensuremath{^{...}}`` / ``\ensuremath{_{...}}`` maths macro,
    which the always-present maths fonts typeset -- exactly as the existing
    micro / degree / ohm entries do.

  * HTML entities (``&amp;``, ``&lt;``, ``&#8594;``) leaked from a rich-text
    source: a decoded ``&`` is a FATAL "Misplaced alignment tab" in a chemfig
    body, and a numeric entity for a symbol never renders.  ``chemfig_lint``
    now decodes them to safe LaTeX (named -> escaped/macro form, numeric ->
    character), and the renderer re-runs the Unicode transliteration so a
    decoded symbol routes through the maths fonts.  Wired ONLY on the chemfig
    path, because rewriting ``&`` would corrupt a tikz-cd matrix.

Direction is verified explicitly:
  * the new superscript/subscript/thin-space cases fail against the unpatched
    table (those codepoints were absent), and the em dash / smart quotes / CJK
    that are SAFE (or genuinely unsupported) are asserted UNCHANGED, so the test
    certifies the fix rather than the input;
  * ``decode_entities`` did not exist before this change, so the entity tests
    fail to import against HEAD.
"""

from app.utils.chemfig_lint import decode_entities
from app.utils.latex_unicode import transliterate


# --------------------------------------------------------------------------
# D-059  superscript / subscript / thin-space transliteration (chemfig-w4-08)
# --------------------------------------------------------------------------

def test_superscript_minus_and_one_become_math():
    """``mol⁻¹`` -> maths superscripts, off the absent TS1 fonts."""
    out, applied = transliterate("kJ\u00b7mol\u207b\u00b9")
    assert r"\ensuremath{^-}" in out          # U+207B SUPERSCRIPT MINUS
    assert r"\ensuremath{^1}" in out          # U+00B9 SUPERSCRIPT ONE
    assert "\u207b" not in out and "\u00b9" not in out
    assert applied


def test_superscript_two_three_and_subscripts():
    assert r"\ensuremath{^2}" in transliterate("m\u00b2")[0]      # ²
    assert r"\ensuremath{^3}" in transliterate("m\u00b3")[0]      # ³
    out, _ = transliterate("H\u2082O + CO\u2082")                 # ₂
    assert r"\ensuremath{_2}" in out
    assert "\u2082" not in out


def test_thin_space_becomes_latex_thin_space():
    out, applied = transliterate("5.5\u2009kJ")                   # U+2009 THIN SPACE
    assert r"\," in out
    assert "\u2009" not in out
    assert applied


def test_w4_08_full_label_clears_ts1_glyphs():
    """The real chemfig-w4-08 label: only default-safe glyphs may remain."""
    label = ("\u201cbenzene\u201d \u2014 mp 5.5\u00b0C,\u2009"
             "\u22125.5 kJ\u00b7mol\u207b\u00b9")
    out, _ = transliterate(label)
    # The TS1-routed / fatal codepoints are gone ...
    for cp in ("\u2009", "\u207b", "\u00b9", "\u00b0", "\u00b7", "\u2212"):
        assert cp not in out, cp
    # ... and the codepoints pdflatex's default UTF-8 map handles safely
    # (em dash, curly quotes) are deliberately LEFT so we do not over-rewrite.
    assert "\u2014" in out          # em dash -> \textemdash in OT1, safe
    assert "\u201c" in out and "\u201d" in out


def test_em_dash_and_smart_quotes_left_untouched():
    """Direction: safe default-UTF-8 glyphs must NOT be rewritten."""
    body = "a \u2014 b \u201cq\u201d"
    out, applied = transliterate(body)
    assert out == body
    assert applied == ()


def test_cjk_left_in_place_for_font_error():
    """Direction: unsupported scripts stay put for the existing font error."""
    out, applied = transliterate("\u82ef")     # 苯 (benzene, CJK)
    assert out == "\u82ef"
    assert applied == ()


# --------------------------------------------------------------------------
# D-059  HTML entity decode (chemfig-w4-14)
# --------------------------------------------------------------------------

def test_amp_entity_becomes_escaped_ampersand():
    """``&amp;`` -> ``\\&``: the fatal 'Misplaced alignment tab' is removed."""
    out, applied = decode_entities("water &amp; ice")
    assert r"\&" in out
    assert "&amp;" not in out
    assert "&" not in out.replace(r"\&", "")     # no bare & left
    assert applied


def test_numeric_entity_arrow_decodes_then_transliterates():
    """``&#8594;`` -> the arrow codepoint -> a maths macro on the next pass."""
    decoded, _ = decode_entities("A &#8594; B")
    assert "\u2192" in decoded                   # decoded to the arrow char
    assert "&#8594;" not in decoded
    final, _ = transliterate(decoded)
    assert r"\ensuremath{\rightarrow}" in final  # routed off TS1


def test_hex_numeric_entity_decodes():
    decoded, _ = decode_entities("A &#x2192; B")
    assert "\u2192" in decoded


def test_lt_gt_entities_become_text_macros():
    out, _ = decode_entities("&lt;br/&gt;")
    assert r"\textless{}" in out and r"\textgreater{}" in out
    assert "&lt;" not in out and "&gt;" not in out


def test_w4_14_full_label_recovers():
    """The real chemfig-w4-14 label: fatal & gone, arrow rendered."""
    label = "**water** &amp; ice &#8594; steam &lt;br/&gt;"
    decoded, _ = decode_entities(label)
    final, _ = transliterate(decoded)
    assert r"\&" in final
    assert r"\ensuremath{\rightarrow}" in final
    assert r"\textless{}" in final and r"\textgreater{}" in final
    # markdown ** is intentionally NOT stripped: ** is chemfig's aromatic-ring
    # operator, so stripping it would corrupt a valid **6(...) double ring.
    assert "**water**" in final


def test_unknown_named_entity_left_untouched():
    """Direction: an entity we do not recognise is not guessed at."""
    body = r"\chemfig{A} &frobnicate; label"
    out, applied = decode_entities(body)
    assert out == body
    assert applied == ()


def test_plain_body_untouched_by_entity_decode():
    """Direction: a chemfig body with no entities is byte-identical + no-op.

    In particular a ``**6(...)`` aromatic double ring must not be disturbed.
    """
    body = r"\chemfig{**6(-=-=-=)}"
    out, applied = decode_entities(body)
    assert out == body
    assert applied == ()


def test_bare_ampersand_without_entity_is_not_touched():
    """A raw '&' that is not part of an entity is left alone (it may be a
    tikz matrix separator inside a chemfig body); only ENTITIES are decoded."""
    body = r"\matrix{a & b\\}"
    out, applied = decode_entities(body)
    assert out == body
    assert applied == ()
