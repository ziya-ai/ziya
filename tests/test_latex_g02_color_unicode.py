r"""
Regression tests for fix group G-02 (server-side LaTeX renderer, part 2).

Four defects, all repaired in the render/profile/preprocessor layer rather than
per spec:

  * D-004 (recovery): web/CSS colour forms a model habitually emits were each a
    FATAL "Undefined color"/"Missing number" -- 3-digit & hashed hex,
    ``rgb()``/``rgba()``, lowercase CSS names, a 3-digit ``HTML`` definecolor
    value, and ``transparent``.  ``xcolor[svgnames,dvipsnames]`` is now loaded
    by every TikZ-family profile (chemfig already did), and
    ``latex_color.normalize_colors`` rewrites the remaining forms into
    xcolor-valid ones before the compile.

  * D-005 (structural): Unicode technical symbols (micro, degree, ohm, arrows,
    the true minus) routed through the absent TS1 fonts and aborted.
    ``latex_unicode.transliterate`` now rewrites them to ``\ensuremath{...}``
    maths-font macros, which a minimal TeX install can typeset; genuinely
    unsupported scripts (CJK) are left in place for the existing font-error
    message.

  * D-042 (structural): chemfig ``\charge``/``\lewis`` satellite ink fell
    outside the 2pt standalone crop and was sliced at the edge; the chemfig
    profile now crops with a 6pt border while the other engines keep 2pt.

  * D-071: NOT fixed here -- see the module docstring note at the bottom and the
    backlog resolution_note (wont-fix, reason recorded).

Each assertion below fails against the unpatched tree (the normaliser modules
did not exist / the profiles lacked xcolor / the border was a hardcoded 2pt)
and passes with the fix.  The direction is verified explicitly where it matters
(a value already valid, or outside a colour context, is asserted UNCHANGED, so
the test certifies the fix and not merely the input).
"""

from app.services.latex_profiles import get_profile
from app.utils.latex_color import normalize_colors
from app.utils.latex_unicode import transliterate


# --------------------------------------------------------------------------
# D-004  colour-form normaliser
# --------------------------------------------------------------------------

def test_hashed_hex_in_option_value_becomes_rgb_expression():
    out, applied = normalize_colors(r"\draw[color=#0af] (0,0) -- (1,1);")
    assert "{rgb,255:red,0;green,170;blue,255}" in out
    assert "#0af" not in out
    assert applied


def test_hashed_hex_in_textcolor_argument():
    out, _ = normalize_colors(r"\textcolor{#36c}{\chemfig{H_3C}}")
    assert r"\textcolor{rgb,255:red,51;green,102;blue,204}" in out
    assert "#36c" not in out


def test_rgba_call_drops_alpha_and_survives_chemfig_field():
    body = r"\chemfig{C(-[:120,,,,rgba(220,20,60,0.8)]O)}"
    out, _ = normalize_colors(body)
    # braced so the internal commas/semicolons survive the chemfig field split
    assert "{rgb,255:red,220;green,20;blue,60}" in out
    assert "rgba(" not in out


def test_three_digit_html_definecolor_expanded_to_six():
    out, _ = normalize_colors(r"\definecolor{acc}{HTML}{0AF}")
    assert r"\definecolor{acc}{HTML}{00AAFF}" in out


def test_valid_six_digit_html_definecolor_left_unchanged():
    """Direction check: a well-formed value must NOT be rewritten."""
    body = r"\definecolor{plate}{HTML}{16324A}"
    out, applied = normalize_colors(body)
    assert out == body
    assert applied == ()


def test_lowercase_css_name_becomes_camelcase():
    out, _ = normalize_colors(
        r"\node[draw=darkslategray,text=white,fill=steelblue] (b) {Cache};")
    assert "draw=DarkSlateGray" in out
    assert "fill=SteelBlue" in out
    # a base xcolor name in the same option list is valid lowercase and stays
    assert "text=white" in out


def test_base_color_and_prose_word_left_untouched():
    """Direction check: base names and a colour WORD in label text are not touched."""
    body = r"\node[fill=blue] {ok}; \node {orange juice and salmon};"
    out, applied = normalize_colors(body)
    assert out == body
    assert applied == ()


def test_explicit_model_color_left_untouched():
    """An author already speaking xcolor (``\\color[rgb]{...}``) is not rewritten."""
    body = r"\color[rgb]{1,0,0} \draw (0,0) -- (1,1);"
    out, applied = normalize_colors(body)
    assert out == body
    assert applied == ()


def test_transparent_fill_becomes_none():
    out, _ = normalize_colors(
        r"\node[fill=transparent] (a) {Ghost}; \node[fill=transparent!50] (c) {Half};")
    assert "fill=none" in out
    assert "transparent" not in out


def test_normalizer_is_idempotent():
    body = r"\draw[color=#0af] (0,0) -- (1,1); \definecolor{a}{HTML}{0AF}"
    once, _ = normalize_colors(body)
    twice, applied2 = normalize_colors(once)
    assert twice == once
    assert applied2 == ()


# --------------------------------------------------------------------------
# D-004  xcolor[svgnames,dvipsnames] loaded by EVERY LaTeX-family profile
# --------------------------------------------------------------------------

def test_all_profiles_load_xcolor_svgnames():
    """Every profile must load xcolor with the extended name sets.

    Without this the CamelCase CSS names the normaliser targets are still
    "Undefined color".  chemfig already did; tikz / circuitikz / tikz-cd did
    NOT before this fix.
    """
    for key in ("tikz", "circuitikz", "chemfig", "tikz-cd"):
        doc = get_profile(key).build_document(
            "x", standalone=True, fmt="png", theme="light")
        assert "\\usepackage[svgnames,dvipsnames]{xcolor}" in doc, key


def test_xcolor_loaded_before_engine_package():
    """xcolor must precede the engine package (once-only package: first wins)."""
    doc = get_profile("circuitikz").build_document(
        "x", standalone=True, fmt="png", theme="light")
    assert doc.index("{xcolor}") < doc.index("{circuitikz}")


# --------------------------------------------------------------------------
# D-005  Unicode transliteration to maths-font macros
# --------------------------------------------------------------------------

def test_micro_sign_becomes_ensuremath_mu():
    out, applied = transliterate("5 \u00b5F")
    assert r"\ensuremath{\mu}" in out
    assert "\u00b5" not in out
    assert applied


def test_degree_ohm_and_true_minus():
    assert r"\ensuremath{^\circ}" in transliterate("90\u00b0")[0]
    assert r"\ensuremath{\Omega}" in transliterate("10 \u2126")[0]      # OHM SIGN
    assert r"\ensuremath{\Omega}" in transliterate("10 \u03a9")[0]      # capital omega
    # true minus collapses to ASCII hyphen
    assert transliterate("\u22125 V")[0] == "-5 V"


def test_arrow_and_greek_transliterated():
    out, _ = transliterate("A \u2192 B, \u0394t")
    assert r"\ensuremath{\rightarrow}" in out
    assert r"\ensuremath{\Delta}" in out


def test_cjk_left_in_place_for_font_error():
    """Direction check: unsupported scripts must NOT be transliterated.

    They have no maths-font equivalent, so they stay put and the renderer's
    existing 'no font for non-Latin scripts' message fires for them.
    """
    out, applied = transliterate("\u7535\u6d41")   # 电流
    assert out == "\u7535\u6d41"
    assert applied == ()


# --------------------------------------------------------------------------
# D-042  chemfig crops wider so \charge/\lewis satellite ink is not sliced
# --------------------------------------------------------------------------

def test_chemfig_uses_wider_standalone_border():
    doc = get_profile("chemfig").build_document(
        r"\chemfig{\chargeplus{C}}", standalone=True, fmt="png", theme="light")
    assert "\\documentclass[border=6pt]{standalone}" in doc


def test_other_engines_keep_tight_default_border():
    """Direction/parity check: the wider crop is scoped to chemfig only."""
    for key in ("tikz", "circuitikz", "tikz-cd"):
        doc = get_profile(key).build_document(
            "x", standalone=True, fmt="png", theme="light")
        assert "\\documentclass[border=2pt]{standalone}" in doc, key
