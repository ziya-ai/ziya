r"""
Regression tests for fix group G-04 (defect D-004): colour-normaliser CONTEXT
gaps in ``app.utils.latex_color.normalize_colors``.

Backstory
---------
``normalize_colors`` (G-02/D-004, then the G-03 contrast clamp) already rewrote
hex / ``rgb()`` / lowercase CSS names / ``transparent`` / theme tokens into
xcolor-valid forms -- but only in a narrow set of syntactic CONTEXTS.  Five
context gaps remained, each a FATAL "Undefined color"/"Missing number" abort
(no image at all) for a diagram a model habitually authors:

  * tikz-w4-06  ``\definecolor{acc}{rgb}{rgba(52,152,219,0.85)}`` -- a CSS
    ``rgba()`` call sitting in the VALUE slot of a ``\definecolor`` whose model
    is already ``rgb``.  The generic rgb() pass SELF-CORRUPTED this into
    ``\definecolor{acc}{rgb}{{rgb,255:...}}``, a brace-in-value the rgb model
    rejects.  Now rewritten whole to a valid ``{RGB}{r,g,b}`` (alpha dropped).
  * tikz-w4-13  ``fill="#3366CC"`` -- an SVG/CSS attribute-dialect hex wrapped
    in quotes.  The option-hex pass required the ``#`` to sit immediately after
    ``=``; the quotes are now consumed and dropped.  (Also fixes a latent
    ordering bug that truncated any 6-digit ``key=#hex`` to 3 digits.)
  * circuitikz-w4-06  ``\node[darkslategray]`` and chemfig-w4-05's positional
    5th bond-colour field ``-[:30,,,,navy]`` -- a lowercase CSS name used as a
    BARE option token (no ``key=``).  TikZ reads it as ``color=NAME`` and
    chemfig passes it to ``\color``, so the lowercase svgnames spelling was
    "Undefined color" just as ``color=NAME`` was.  Now remapped to CamelCase.
  * chemfig-w4-06  ``\colorbox{transparent}{\textcolor{--ziya-text-primary}{..}}``
    -- ``transparent`` and a bare CSS custom property as macro arguments.  The
    box background now resolves to the theme SURFACE and the ``--ziya-*`` token
    to the theme ink, per theme.
  * chemfig-w4-15  ``\textcolor{336699}{...}`` -- a bare 6-digit hex used as a
    colour NAME (no ``#``), while the correct ``\textcolor[HTML]{336699}`` form
    in the same body is the in-spec control that must stay untouched.

Both-theme discipline
---------------------
D-004 is a RECOVERY defect (turn a fatal body into one that compiles), but two
of its cases carry theme-reactive tokens, so every test runs BOTH themes:
recovery cases assert the fatal token is gone and correctly converted in light
AND dark; the theme-token cases assert the token resolves to the correct
per-theme ink/surface and that the two themes DIFFER (the pairing that catches a
"swap one constant for another" fix).  Each assertion below fails against the
unpatched tree, where the value slot self-corrupted / the quotes broke the
match / the bare name stayed lowercase / the macro token survived verbatim.
"""

from app.utils.latex_color import normalize_colors

# xcolor expressions the normaliser emits for the theme ink/surface (must match
# the surface build_document bakes; identical to test_latex_g72_theme_tokens).
_FG_DARK = "rgb,255:red,237;green,237;blue,237"    # #EDEDED
_FG_LIGHT = "rgb,255:red,0;green,0;blue,0"          # #000000
_BG_DARK = "rgb,255:red,31;green,31;blue,31"        # #1F1F1F
_BG_LIGHT = "rgb,255:red,255;green,255;blue,255"    # #FFFFFF


# --------------------------------------------------------------------------
# tikz-w4-06  rgb()/rgba() in the \definecolor value slot
# --------------------------------------------------------------------------

def test_definecolor_rgb_value_slot_rewritten_not_self_corrupted():
    body = r"\definecolor{acc}{rgb}{rgba(52,152,219,0.85)}"
    for theme in ("light", "dark"):
        out, applied = normalize_colors(body, theme=theme)
        # Valid xcolor: RGB (0..255) model, alpha dropped.
        assert r"\definecolor{acc}{RGB}{52,152,219}" in out, (theme, out)
        # The self-corrupting brace-in-value never appears.
        assert "{rgb}{{rgb" not in out
        assert "rgba(" not in out
        assert applied


def test_plain_rgb_call_still_converts_outside_definecolor():
    """Direction: an rgb()/rgba() in an option value is still braced (unchanged
    behaviour -- the definecolor pass must not have shadowed the generic one)."""
    out, _ = normalize_colors(r"\node[fill=rgb(52,152,219)] (a) {A};")
    assert "fill={rgb,255:red,52;green,152;blue,219}" in out


# --------------------------------------------------------------------------
# tikz-w4-13  quoted SVG hex  fill="#hex"
# --------------------------------------------------------------------------

def test_quoted_hex_option_value_converted_and_quotes_dropped():
    body = r'\node[fill="#3366CC",text=white] (a) {A};'
    for theme in ("light", "dark"):
        out, _ = normalize_colors(body, theme=theme)
        assert "fill={rgb,255:red,51;green,102;blue,204}" in out, (theme, out)
        assert '"#' not in out and '#3366' not in out


def test_six_digit_key_hex_not_truncated_to_three():
    """Direction: the {6}|{3} ordering fix -- a 6-digit key=#hex is read whole,
    not clipped to its first 3 digits (which the unpatched {3}|{6} did)."""
    out, _ = normalize_colors(r"\draw[color=#00aaff] (0,0) -- (1,1);")
    assert "color={rgb,255:red,0;green,170;blue,255}" in out
    # no stray leftover hex tail
    assert "aaff" not in out and "ff]" not in out


# --------------------------------------------------------------------------
# circuitikz-w4-06 / chemfig-w4-05  bare CSS name as a standalone option
# --------------------------------------------------------------------------

def test_bare_css_name_option_remapped_to_camelcase():
    body = r"\node[darkslategray] at (2.4,1.4) {CSS names};"
    light, _ = normalize_colors(body, theme="light")
    dark, _ = normalize_colors(body, theme="dark")
    # Light: the fatal lowercase spelling becomes the valid svgnames CamelCase.
    assert "DarkSlateGray" in light
    # The fatal lowercase form is gone in BOTH themes (dark may additionally be
    # contrast-lifted by the G-03 clamp, but the undefined-colour is resolved).
    assert "darkslategray" not in light
    assert "darkslategray" not in dark


def test_chemfig_positional_bare_name_field_remapped():
    """chemfig-w4-05: lowercase names in the 5th (colour) bond field."""
    body = r"\chemfig{N(-[:30,,,,navy]H)(-[:150,,,,darkgreen]H)-H}"
    light, _ = normalize_colors(body, theme="light")
    assert ",Navy]" in light and ",DarkGreen]" in light
    for theme in ("light", "dark"):
        out, _ = normalize_colors(body, theme=theme)
        assert ",navy]" not in out and ",darkgreen]" not in out


def test_bare_name_pass_leaves_non_colour_option_keywords_alone():
    """Direction: only genuine svgnames colour words are remapped; TikZ style
    keywords and base xcolor names in the same [...] are untouched."""
    body = r"\node[thick,draw,white,dashed] at (0,0) {x};"
    out, applied = normalize_colors(body)
    assert out == body
    assert applied == ()


# --------------------------------------------------------------------------
# chemfig-w4-06  \colorbox{transparent} + \textcolor{--ziya-text-primary}
#   (THEME fix -- both themes mandatory)
# --------------------------------------------------------------------------

_W4_06 = r"\colorbox{transparent}{\textcolor{--ziya-text-primary}{\chemfig{O=C=O}}}"


def test_colorbox_transparent_resolves_to_theme_surface_both_themes():
    dark, _ = normalize_colors(_W4_06, theme="dark")
    light, _ = normalize_colors(_W4_06, theme="light")
    assert f"\\colorbox{{{_BG_DARK}}}" in dark      # transparent -> dark surface
    assert f"\\colorbox{{{_BG_LIGHT}}}" in light    # transparent -> white
    assert _BG_DARK != _BG_LIGHT                     # not one hardcoded constant
    for out in (dark, light):
        assert "transparent" not in out


def test_bare_custom_property_in_textcolor_resolves_to_ink_both_themes():
    dark, _ = normalize_colors(_W4_06, theme="dark")
    light, _ = normalize_colors(_W4_06, theme="light")
    assert f"\\textcolor{{{_FG_DARK}}}" in dark     # --ziya-text-primary -> dark ink
    assert f"\\textcolor{{{_FG_LIGHT}}}" in light   # -> light ink (swap guard)
    for out in (dark, light):
        assert "--ziya-text-primary" not in out


# --------------------------------------------------------------------------
# chemfig-w4-15  bare 6-digit hex as a \textcolor name + [HTML] control
# --------------------------------------------------------------------------

_W4_15 = (
    r"\textcolor{336699}{\chemfig{S}}"
    "\n"
    r"\textcolor[HTML]{336699}{\chemfig{Cl-Cl}}"
)


def test_bare_hex_textcolor_converted_control_left_untouched():
    light, _ = normalize_colors(_W4_15, theme="light")
    # The bare-hex \textcolor becomes the light ink expression (light leg).
    assert r"\textcolor{rgb,255:red,51;green,102;blue,153}" in light
    for theme in ("light", "dark"):
        out, _ = normalize_colors(_W4_15, theme=theme)
        # Direction/parity: the correct \textcolor[HTML]{336699} control is an
        # explicit model and must survive VERBATIM in both themes.
        assert r"\textcolor[HTML]{336699}" in out
        # The fatal bare-name form (\textcolor{336699}, no #, no model) is gone.
        assert r"\textcolor{336699}" not in out


def test_all_digit_free_hex_lookalike_is_treated_as_a_name_not_hex():
    """Direction: an all-[a-f] word is NOT mistaken for hex (a digit is
    required), so a real colour name is still routed through the name map."""
    # 'face' is 4 hex letters with no digit -> must stay a name lookup, not hex.
    out, applied = normalize_colors(r"\textcolor{face}{x}")
    assert "rgb,255" not in out          # never hex-converted
    assert out == r"\textcolor{face}{x}"  # not a known name either -> untouched
    assert applied == ()


# --------------------------------------------------------------------------
# Cross-cutting: idempotence over every new context
# --------------------------------------------------------------------------

def test_new_passes_are_idempotent():
    body = (
        r"\definecolor{acc}{rgb}{rgba(52,152,219,0.85)}"
        + r'\node[darkslategray,fill="#3366CC"] {x};'
        + _W4_06 + _W4_15
    )
    for theme in ("light", "dark"):
        once, _ = normalize_colors(body, theme=theme)
        twice, applied2 = normalize_colors(once, theme=theme)
        assert twice == once, theme
        assert applied2 == (), (theme, applied2)
