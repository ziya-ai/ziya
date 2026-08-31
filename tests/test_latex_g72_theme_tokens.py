r"""
Regression test for D-012 residual: theme-reactive colour tokens.

Backstory
---------
``latex_color.normalize_colors`` already rewrote hex / ``rgb()`` / lowercase
CSS names / ``transparent`` into xcolor-valid forms (fix group G-02, D-004).
It did NOT handle *theme tokens* -- ``currentColor``, a CSS custom property
``var(--fg)`` / ``var(--surface)``, or a bare ``theme-bg`` / ``theme.foreground``
(``tikz-w4-08`` and the cross-engine ``tikz-cd-w4-13`` / ``circuitikz-w4-07``).
Every one is a FATAL "Undefined color" in stock xcolor, and -- unlike a hex
literal -- carries NO fixed value: it is a *request* for the active theme's ink
or surface.  Because the renderer now threads ``theme`` all the way into the
normaliser, a foreground-family token can resolve to the theme foreground and a
surface/background-family token to the theme surface.

Direction & both-theme discipline
----------------------------------
This is a THEME fix, so it is verified in BOTH themes: the dark assertion checks
the token now resolves to the dark ink/surface, and the light assertion checks
it still resolves to the light ink/surface -- the pairing that catches the
swap-one-constant-for-another failure mode.  Every assertion fails against the
unpatched tree: ``normalize_colors`` took no ``theme`` argument and left the
tokens verbatim, so ``var(--fg)`` / ``currentColor`` / ``theme-bg`` survived
into the (fatal) compile.

Resolved values match the surface build_document bakes:
  fg  dark #EDEDED  light #000000      bg  dark #1F1F1F  light #FFFFFF
Contrast of a token used as ink on a token-resolved surface:
  dark  #EDEDED on #1F1F1F = 14.08:1   light #000000 on #FFFFFF = 21.00:1
(both clear the 4.5:1 text floor; the fg stroke clears the 3:1 stroke floor on
the black!88 plate at 14.08:1 dark and on #F6F8FA at 19.73:1 light).
"""

from app.utils.latex_color import normalize_colors

# xcolor extended expressions the normaliser emits for the theme surface/ink.
_FG_DARK = "rgb,255:red,237;green,237;blue,237"   # #EDEDED
_FG_LIGHT = "rgb,255:red,0;green,0;blue,0"         # #000000
_BG_DARK = "rgb,255:red,31;green,31;blue,31"       # #1F1F1F
_BG_LIGHT = "rgb,255:red,255;green,255;blue,255"   # #FFFFFF

# The heart of tikz-w4-08: var(--*) custom properties, currentColor and a bare
# theme-bg token, all in fill=/draw=/text= colour-key context.
_W4_08 = (
    r"\node[draw=var(--border),text=var(--fg),fill=var(--surface)] (a) at (1,1) {Panel};"
    "\n"
    r"\node[draw=white,text=currentColor,fill=theme-bg] (b) at (4,1) {Token};"
)


def test_foreground_token_resolves_to_theme_ink_dark_and_light():
    """text=var(--fg)/currentColor -> the theme FOREGROUND, per theme."""
    dark, _ = normalize_colors(_W4_08, theme="dark")
    light, _ = normalize_colors(_W4_08, theme="light")

    # Dark leg: fg token becomes the dark ink.
    assert f"text={{{_FG_DARK}}}" in dark
    # Light leg still correct: same token becomes the light ink (swap guard).
    assert f"text={{{_FG_LIGHT}}}" in light
    # The two themes must differ -- a single hardcoded constant would not.
    assert _FG_DARK != _FG_LIGHT


def test_surface_token_resolves_to_theme_surface_dark_and_light():
    """fill=var(--surface)/theme-bg -> the theme SURFACE, per theme."""
    dark, _ = normalize_colors(_W4_08, theme="dark")
    light, _ = normalize_colors(_W4_08, theme="light")

    assert f"fill={{{_BG_DARK}}}" in dark          # var(--surface) + theme-bg
    assert f"fill={{{_BG_LIGHT}}}" in light


def test_no_theme_token_survives_either_theme():
    """The compile-fatal raw tokens must be gone in BOTH themes (direction)."""
    for theme in ("dark", "light"):
        out, applied = normalize_colors(_W4_08, theme=theme)
        for tok in ("var(--fg)", "var(--surface)", "var(--border)",
                    "currentColor", "theme-bg"):
            assert tok not in out, f"{tok!r} left unresolved in {theme}"
        assert applied, "no fix reported"


def test_border_token_resolves_to_foreground():
    """var(--border) is foreground-ish (a border wants to be visible)."""
    dark, _ = normalize_colors(_W4_08, theme="dark")
    assert f"draw={{{_FG_DARK}}}" in dark


def test_theme_variants_of_a_token_classify_consistently():
    """A background/surface stem -> bg; everything else -> fg, per theme."""
    body = (
        r"\node[fill=theme.background,text=theme.foreground] (a) {A};"
        r"\node[fill=var(--bg),draw=var(--text-primary)] (b) {B};"
    )
    dark, _ = normalize_colors(body, theme="dark")
    # background/bg -> surface ink
    assert dark.count(f"{{{_BG_DARK}}}") == 2
    # foreground/text -> foreground ink
    assert dark.count(f"{{{_FG_DARK}}}") == 2


def test_non_colour_context_token_is_left_untouched():
    """A var(--x) outside a colour key is NOT a colour -- leave it alone."""
    body = r"\node at (1,1) {see var(--fg) in prose};"
    out, applied = normalize_colors(body, theme="dark")
    assert out == body
    assert applied == ()


def test_unknown_theme_falls_back_to_light():
    """An unrecognised theme resolves as light rather than raising/blanking."""
    out, _ = normalize_colors(_W4_08, theme="sepia")
    assert f"text={{{_FG_LIGHT}}}" in out
