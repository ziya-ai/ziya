r"""
Colour-form normaliser for model-authored LaTeX diagram bodies (D-004).

Why this exists
---------------
A model authoring a TikZ / CircuiTikZ / chemfig / tikz-cd diagram reaches for
the colour syntax it knows from CSS and the web, none of which stock LaTeX
accepts as-is:

  * 3-digit / hashed hex           ``#0af``, ``\textcolor{#36c}{...}``
  * a 3-digit ``HTML`` model value  ``\definecolor{acc}{HTML}{0AF}``   (HTML
                                     demands exactly 6 hex digits -> fatal)
  * ``rgb()`` / ``rgba()``          ``fill=rgba(220,20,60,0.8)``
  * lowercase CSS colour NAMES      ``fill=cornflowerblue`` (xcolor's svgnames
                                     are CamelCase -- ``CornflowerBlue`` -- so
                                     the lowercase spelling is an "Undefined
                                     color", even with svgnames loaded)
  * ``transparent`` as a fill/draw  ``fill=transparent`` (there is no
                                     ``transparent`` colour in plain xcolor ->
                                     fatal)

Every one of these is a FATAL "Undefined color"/"Missing number" abort (no
image at all) for a diagram that is otherwise valid.  Loading
``xcolor[svgnames,dvipsnames]`` (done in every profile) rescues the *correctly
spelled* CamelCase names; this normaliser closes the rest by rewriting each
recognised form into a shape xcolor already understands:

  * hex and ``rgb()``/``rgba()``  ->  an xcolor extended expression
        ``{rgb,255:red,R;green,G;blue,B}``  (alpha is dropped -- xcolor has no
        alpha channel; the geometry survives instead of the whole render dying)
  * a 3-digit ``HTML`` definecolor value -> its 6-digit expansion
  * a lowercase CSS name          ->  its canonical CamelCase svgnames spelling
  * ``transparent`` on fill/draw/text -> ``none``

Scope discipline (same contract as circuitikz_lint / chemfig_lint)
------------------------------------------------------------------
Advisory only: ``normalize_colors(body) -> (body, applied)`` must degrade to
"return the body unchanged" on any internal fault and must NEVER raise, so a
defect in the normaliser can never turn a render that would have worked into a
failure.

Deliberately conservative about WHERE it rewrites, because an over-eager colour
rewrite would corrupt working diagrams -- worse than the bug:

  * hex / ``rgb()`` / named-colour rewrites fire only in an unambiguous colour
    CONTEXT: the argument of ``\color`` / ``\textcolor`` / ``\pagecolor``, or
    the value of a ``fill=`` / ``draw=`` / ``color=`` / ``text=`` option.  A
    bare ``#36c`` or the word ``orange`` sitting in a label is left untouched.
  * ``rgb()`` / ``rgba()`` is the one form rewritten wherever it appears,
    because that token is never legitimate LaTeX prose -- it can only be a
    mis-spelled colour (this is what recovers chemfig's positional bond-colour
    field ``[:120,,,,rgba(...)]``).
  * a value already carrying an explicit model (``\color[rgb]{...}``) is left
    alone -- the author was already speaking xcolor.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# CSS / SVG colour names.  xcolor's ``svgnames`` option defines these in
# CamelCase, so the lowercase spelling a model habitually emits is an
# "Undefined color".  Map lowercase -> canonical so the author's intent
# survives.  The ~19 *base* xcolor names (red, blue, lightgray, ...) are valid
# lowercase already and are deliberately excluded below so they are never
# touched.
# --------------------------------------------------------------------------
_SVG_NAMES: tuple[str, ...] = (
    "AliceBlue", "AntiqueWhite", "Aqua", "Aquamarine", "Azure", "Beige",
    "Bisque", "BlanchedAlmond", "BlueViolet", "Brown", "BurlyWood",
    "CadetBlue", "Chartreuse", "Chocolate", "Coral", "CornflowerBlue",
    "Cornsilk", "Crimson", "DarkBlue", "DarkCyan", "DarkGoldenrod",
    "DarkGray", "DarkGreen", "DarkGrey", "DarkKhaki", "DarkMagenta",
    "DarkOliveGreen", "DarkOrange", "DarkOrchid", "DarkRed", "DarkSalmon",
    "DarkSeaGreen", "DarkSlateBlue", "DarkSlateGray", "DarkSlateGrey",
    "DarkTurquoise", "DarkViolet", "DeepPink", "DeepSkyBlue", "DimGray",
    "DimGrey", "DodgerBlue", "FireBrick", "FloralWhite", "ForestGreen",
    "Fuchsia", "Gainsboro", "GhostWhite", "Gold", "Goldenrod", "GreenYellow",
    "Honeydew", "HotPink", "IndianRed", "Indigo", "Ivory", "Khaki",
    "Lavender", "LavenderBlush", "LawnGreen", "LemonChiffon", "LightBlue",
    "LightCoral", "LightCyan", "LightGoldenrod", "LightGoldenrodYellow",
    "LightGray", "LightGreen", "LightGrey", "LightPink", "LightSalmon",
    "LightSeaGreen", "LightSkyBlue", "LightSlateBlue", "LightSlateGray",
    "LightSlateGrey", "LightSteelBlue", "LightYellow", "LimeGreen", "Linen",
    "Magenta", "Maroon", "MediumAquamarine", "MediumBlue", "MediumOrchid",
    "MediumPurple", "MediumSeaGreen", "MediumSlateBlue", "MediumSpringGreen",
    "MediumTurquoise", "MediumVioletRed", "MidnightBlue", "MintCream",
    "MistyRose", "Moccasin", "NavajoWhite", "Navy", "NavyBlue", "OldLace",
    "OliveDrab", "Orange", "OrangeRed", "Orchid", "PaleGoldenrod",
    "PaleGreen", "PaleTurquoise", "PaleVioletRed", "PapayaWhip", "PeachPuff",
    "Peru", "Pink", "Plum", "PowderBlue", "Purple", "RosyBrown", "RoyalBlue",
    "SaddleBrown", "Salmon", "SandyBrown", "SeaGreen", "Seashell", "Sienna",
    "Silver", "SkyBlue", "SlateBlue", "SlateGray", "SlateGrey", "Snow",
    "SpringGreen", "SteelBlue", "Tan", "Teal", "Thistle", "Tomato",
    "Turquoise", "Violet", "VioletRed", "Wheat", "WhiteSmoke", "YellowGreen",
)

#: Base xcolor names -- valid lowercase, never remapped.
_BASE_XCOLOR: frozenset = frozenset((
    "red", "green", "blue", "cyan", "magenta", "yellow", "black", "gray",
    "grey", "white", "darkgray", "lightgray", "brown", "lime", "olive",
    "orange", "pink", "purple", "teal", "violet",
))

#: lowercase spelling -> canonical CamelCase spelling (base names excluded).
_CSS_NAME_MAP: dict[str, str] = {
    n.lower(): n for n in _SVG_NAMES if n.lower() not in _BASE_XCOLOR
}

#: Option keys whose value is a colour.  A bare colour name (``\node[cornflowerblue]``)
#: is also accepted by TikZ as ``color=``, but is not remapped here -- only the
#: explicit ``key=value`` form is, which keeps the rewrite unambiguous.
_COLOUR_KEYS = ("fill", "draw", "color", "text")


def _hex_to_expr(hex6: str) -> str:
    """``'00aaff'`` -> ``'rgb,255:red,0;green,170;blue,255'`` (bare expression)."""
    r = int(hex6[0:2], 16)
    g = int(hex6[2:4], 16)
    b = int(hex6[4:6], 16)
    return f"rgb,255:red,{r};green,{g};blue,{b}"


def _expand_hex(h: str) -> str:
    """Expand a 3-digit hex to 6, else return unchanged."""
    return "".join(c * 2 for c in h) if len(h) == 3 else h


def _rgb_call_channels(inner: str) -> tuple[int, int, int] | None:
    """``'220,20,60,0.8'`` -> ``(220, 20, 60)`` (alpha dropped, channels clamped).

    Returns None when the first three channels cannot be read as numbers, so a
    malformed call is left untouched rather than mangled.
    """
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) < 3:
        return None
    try:
        chans = [max(0, min(255, int(round(float(p))))) for p in parts[:3]]
    except ValueError:
        return None
    return (chans[0], chans[1], chans[2])


def _rgb_call_to_expr(inner: str) -> str | None:
    """``'220,20,60,0.8'`` -> ``'rgb,255:red,220;green,20;blue,60'`` (alpha dropped).

    Returns None when the three channels cannot be read as integers, so a
    malformed call is left untouched rather than mangled.
    """
    chans = _rgb_call_channels(inner)
    if chans is None:
        return None
    return f"rgb,255:red,{chans[0]};green,{chans[1]};blue,{chans[2]}"


def _hsl_call_channels(inner: str) -> tuple[int, int, int] | None:
    """``'210,50%,40%'`` -> ``(51, 102, 153)`` (CSS hsl -> sRGB, alpha dropped).

    Hue in degrees, saturation/lightness as a fraction or a ``%`` value.  A ``%``
    is a TeX comment, so an unconverted ``hsl(210,50%,40%)`` would swallow the
    rest of the line -- this conversion removes it before it reaches TeX (D-482).
    Returns None when the first three fields cannot be read as numbers.
    """
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) < 3:
        return None

    def _frac(x: str) -> float:
        """A saturation/lightness field: ``40%`` -> 0.40, ``0.4`` -> 0.4."""
        x = x.strip()
        return float(x[:-1]) / 100.0 if x.endswith("%") else float(x)

    try:
        # Hue is an angle in degrees (a trailing ``deg`` or ``%`` is tolerated).
        htok = parts[0].strip().rstrip("%")
        if htok.lower().endswith("deg"):
            htok = htok[:-3]
        h = float(htok)
        s = _frac(parts[1])
        ll = _frac(parts[2])
    except ValueError:
        return None
    s = max(0.0, min(1.0, s))
    ll = max(0.0, min(1.0, ll))
    return _hsl_to_rgb(h, s, ll)


def _hsl_to_rgb(h: float, s: float, ll: float) -> tuple[int, int, int]:
    """CSS ``hsl(h, s, l)`` (h deg, s/l in 0..1) -> ``(r, g, b)`` 0..255."""
    h = h % 360.0
    c = (1.0 - abs(2.0 * ll - 1.0)) * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    mm = ll - c / 2.0
    if h < 60:
        rp, gp, bp = c, x, 0.0
    elif h < 120:
        rp, gp, bp = x, c, 0.0
    elif h < 180:
        rp, gp, bp = 0.0, c, x
    elif h < 240:
        rp, gp, bp = 0.0, x, c
    elif h < 300:
        rp, gp, bp = x, 0.0, c
    else:
        rp, gp, bp = c, 0.0, x
    return (
        max(0, min(255, int(round((rp + mm) * 255)))),
        max(0, min(255, int(round((gp + mm) * 255)))),
        max(0, min(255, int(round((bp + mm) * 255)))),
    )


def _hsl_call_to_expr(inner: str) -> str | None:
    """``'210,50%,40%'`` -> ``'rgb,255:red,51;green,102;blue,153'`` (alpha dropped)."""
    chans = _hsl_call_channels(inner)
    if chans is None:
        return None
    return f"rgb,255:red,{chans[0]};green,{chans[1]};blue,{chans[2]}"


def _convert_token(tok: str) -> str | None:
    """Convert a single colour token to an xcolor-valid BARE expression/name.

    Returns the replacement, or None if ``tok`` is not a recognised convertible
    form (leave it as the author wrote it).
    """
    t = tok.strip()
    # rgb() / rgba()
    m = re.fullmatch(r"rgba?\(([^)]*)\)", t, re.IGNORECASE)
    if m:
        return _rgb_call_to_expr(m.group(1))
    # hsl() / hsla() -- convert to an rgb expression so the ``%`` (a TeX comment)
    # never reaches TeX and swallows the line (D-482).
    m = re.fullmatch(r"hsla?\(([^)]*)\)", t, re.IGNORECASE)
    if m:
        return _hsl_call_to_expr(m.group(1))
    # hashed hex (#abc / #aabbcc)
    m = re.fullmatch(r"#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})", t)
    if m:
        return _hex_to_expr(_expand_hex(m.group(1)))
    # lowercase CSS name
    if t.lower() in _CSS_NAME_MAP:
        return _CSS_NAME_MAP[t.lower()]
    # bare 3/6-digit hex used as a colour NAME with no '#' (chemfig-w4-15's
    # ``\textcolor{336699}{...}``).  Only reached from the colour-macro pass --
    # the sole caller -- so this never rewrites a hex-looking token in prose.
    # A digit is REQUIRED so an all-[a-f] word (``beige``, ``face``) can never
    # be mistaken for hex; ``\textcolor[HTML]{336699}`` is untouched because the
    # macro regex excludes an explicit ``[model]``.
    if (re.fullmatch(r"[0-9A-Fa-f]{3}", t) or re.fullmatch(r"[0-9A-Fa-f]{6}", t)) \
            and any(c.isdigit() for c in t):
        return _hex_to_expr(_expand_hex(t))
    return None


# --- individual passes -----------------------------------------------------

_DEFINECOLOR_HTML_RE = re.compile(
    r"(\\definecolor\s*\{[^{}]*\}\s*\{HTML\}\s*\{)([0-9A-Fa-f]{3})(\})")

# \color{ARG} / \textcolor{ARG}{...} / \pagecolor{ARG} with NO explicit [model].
_COLOR_MACRO_RE = re.compile(
    r"(\\(?:color|textcolor|pagecolor))(?!\s*\[)\s*\{([^{}]*)\}")

# \definecolor{name}{rgb|RGB|HTML|cmyk}{rgba(...)/rgb(...)}  (D-004, tikz-w4-06).
# A model reaches for a CSS ``rgb()``/``rgba()`` call as the VALUE of a
# \definecolor whose model slot already says ``rgb`` -- a doubly-wrong form the
# generic rgb() pass below would SELF-CORRUPT into
# ``\definecolor{acc}{rgb}{{rgb,255:...}}`` (a brace-in-value the ``rgb`` model
# rejects).  Rewrite the whole definition to a valid ``{RGB}{r,g,b}`` (0..255
# model, alpha dropped) BEFORE the generic pass so the parens are gone by then.
_DEFINECOLOR_RGBCALL_RE = re.compile(
    r"(\\definecolor\s*\{[^{}]*\})\s*\{[A-Za-z]+\}\s*\{\s*(rgba?\(([^)]*)\))\s*\}",
    re.IGNORECASE)

# \colorbox{ARG}{...} background colour with NO explicit [model].  ``colorbox``/
# ``fcolorbox`` must precede ``color`` in the alternation, else ``color`` would
# claim the ``\color`` prefix of ``\colorbox`` and then fail the ``{`` that
# follows ``box``.  ``\colorbox{transparent}`` and a theme token as the box
# background are the D-004 (chemfig-w4-06) gap; the box background resolves to
# the theme SURFACE.
_COLORBOX_MACRO_RE = re.compile(
    r"(\\colorbox)(?!\s*\[)\s*\{([^{}]*)\}")

# rgb()/rgba() anywhere (unambiguous -- never legitimate prose).  Braced so the
# internal commas/semicolons survive a pgfkeys option list and a chemfig field.
_RGB_CALL_RE = re.compile(r"rgba?\(([^)]*)\)", re.IGNORECASE)

# rgb()/rgba() as a BARE, standalone positional option token -- inside a bare
# ``[...]`` option list, or (D-038, chemfig-w4-04) chemfig's POSITIONAL 5th
# bond-colour field ``-[:120,,,,rgba(220,20,60,0.8)]`` -- i.e. NOT preceded by a
# ``key=``.  The generic ``_RGB_CALL_RE`` pass below would rewrite this to a
# BARE ``{rgb,255:...}`` brace, which tikz/chemfig then parse as an unknown KEY
# (``I do not know the key '/tikz/rgb'``) -- a fatal abort, no image.  A bare
# colour EXPRESSION is not a valid option; only a colour NAME is (which is why
# the bare-name pass can leave names bare, but a bare rgb-expression cannot be).
# Wrapping it as ``color={rgb,...}`` gives tikz/chemfig an explicit colour
# assignment -- the very shape the contrast clamp already emits for a bare
# stroke colour.  Matched with the ``[``/``,`` boundary consumed so it fires
# only in an option position, never on an ``rgb()`` sitting after ``key=`` (that
# stays with the generic pass, which correctly yields ``fill={rgb,...}``).
_BARE_RGBCALL_RE = re.compile(
    r"([\[,]\s*)(rgba?\(([^)]*)\))(?=\s*[,\]])", re.IGNORECASE)

# hsl()/hsla() anywhere -- the CSS functional form xcolor cannot parse, and
# whose ``%`` is a TeX line comment that would swallow the rest of the line
# (D-482, tikz-cd-w4-10 ``color=hsl(210,50%,40%)``).  Converted to a braced
# ``{rgb,255:...}`` expression exactly like ``rgb()``; alpha (hsla) is dropped.
_HSL_CALL_RE = re.compile(r"hsla?\(([^)]*)\)", re.IGNORECASE)
# hsl()/hsla() as a BARE positional option token (mirror of _BARE_RGBCALL_RE):
# wrapped as ``color={rgb,...}`` so tikz/chemfig do not read a bare brace as a
# key.
_BARE_HSLCALL_RE = re.compile(
    r"([\[,]\s*)(hsla?\(([^)]*)\))(?=\s*[,\]])", re.IGNORECASE)

# key=value colour option: fill= / draw= / color= / text=  followed by a
# convertible value token.
_KEY = "|".join(_COLOUR_KEYS)
# key=#hex, optionally WRAPPED IN QUOTES (SVG/CSS attribute dialect
# ``fill="#3366CC"`` -- tikz-w4-13).  The quotes, if present, are consumed and
# dropped so the value becomes a bare xcolor expression.
_OPT_HEX_RE = re.compile(
    rf"(?<![A-Za-z])((?:{_KEY})\s*=\s*)[\"']?#([0-9A-Fa-f]{{6}}|[0-9A-Fa-f]{{3}})[\"']?")
_OPT_TRANSPARENT_RE = re.compile(
    rf"(?<![A-Za-z])((?:{_KEY})\s*=\s*)transparent(?:!\d+)?(?![A-Za-z])")
# ``opacity=`` (or ``fill/draw/text opacity=``) carrying a CSS keyword instead
# of a number (D-484: ``opacity=none`` / ``opacity=inherit`` abort pgfmath).
_OPT_OPACITY_KEYWORD_RE = re.compile(
    r"((?:fill\s+|draw\s+|text\s+)?opacity\s*=\s*)"
    r"(none|inherit|initial|unset|transparent)(?![A-Za-z])",
    re.IGNORECASE)
_OPT_NAME_RE = re.compile(
    rf"(?<![A-Za-z])((?:{_KEY})\s*=\s*)([A-Za-z]+)(?=[\s,\]}}])")

# A lowercase CSS colour name used as a BARE, standalone option token inside a
# ``[...]`` list -- ``\node[darkslategray]`` (circuitikz-w4-06) and chemfig's
# positional 5th bond-colour field ``-[:30,,,,navy]`` (chemfig-w4-05).  TikZ
# reads a bare colour option as ``color=NAME``, and chemfig passes the field to
# ``\color`` -- so the lowercase svgnames spelling is a fatal "Undefined color"
# in exactly the same way ``color=NAME`` was, only without the ``key=`` marker.
# Bounded by ``[`` / ``,`` before and ``,`` / ``]`` after so it fires only in an
# option position, never in a label; ``{3,}`` skips single/double-letter chemfig
# atom symbols (C, N, Cl, OH).  Every match is gated on _CSS_NAME_MAP membership
# in the handler, so a base xcolor name (``white``) or a TikZ keyword
# (``thick``, ``dashed``) is never rewritten.
_OPT_BARE_NAME_RE = re.compile(r"([\[,]\s*)([A-Za-z]{3,})(?=\s*[,\]])")

# A colour-macro argument that is a theme-reactive token, matched WHOLE:
# ``var(--fg)``, a bare CSS custom property ``--ziya-text-primary``
# (chemfig-w4-06), ``currentColor``, or ``theme-bg`` / ``theme.foreground``.
_THEME_TOKEN_FULL_RE = re.compile(
    r"var\(--[A-Za-z0-9_-]+\)|--[A-Za-z][A-Za-z0-9_-]*"
    r"|currentcolor|theme[-.][A-Za-z.-]+"
    r"|\$[A-Za-z][A-Za-z0-9_-]*",
    re.IGNORECASE)


def _is_theme_token(tok: str) -> bool:
    return bool(_THEME_TOKEN_FULL_RE.fullmatch(tok.strip()))

# Theme-reactive colour tokens a model emits from a web/CSS mindset:
# ``currentColor``, a CSS custom property ``var(--fg)`` / ``var(--surface)`` /
# ``var(--ziya-text)``, or a bare ``theme-bg`` / ``theme.foreground``.  None is
# a colour xcolor knows, so each is otherwise a FATAL "Undefined color" (no
# image) -- and unlike a hex literal or a CSS name it carries NO fixed value:
# it is a REQUEST for the active theme's ink / surface.  The renderer now
# threads ``theme`` all the way here, so a foreground-family token resolves to
# the theme foreground and a surface/background-family token to the theme
# surface.  This is the one both-theme-safe construction: a FIXED substitution
# would score 1.00:1 in the opposite theme, whereas resolving per theme gives,
# for a token used as ink, #000000-on-#FFFFFF = 21.00:1 in light and
# #EDEDED-on-#1F1F1F = 14.08:1 in dark -- the exact surface build_document
# bakes.  Restricted to the colour-key context (fill/draw/color/text=) like the
# other option passes, so a stray ``var(--x)`` in a label is left alone.
_OPT_THEME_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z])((?:{_KEY})\s*=\s*)"
    r"(var\(--[A-Za-z0-9_-]+\)|currentcolor|theme[-.][A-Za-z-]+"
    r"|\$[A-Za-z][A-Za-z0-9_-]*)",
    re.IGNORECASE,
)

#: The theme surface/ink build_document bakes (dark page #1F1F1F + #EDEDED ink,
#: light page #FFFFFF + #000000 ink), expressed as xcolor extended expressions
#: so a resolved token is a valid option value.  A theme not in this table
#: falls back to ``light`` (the safe default the rest of the pipeline uses).
_THEME_COLOURS: dict[str, dict[str, str]] = {
    "dark":  {"fg": _hex_to_expr("ededed"), "bg": _hex_to_expr("1f1f1f")},
    "light": {"fg": _hex_to_expr("000000"), "bg": _hex_to_expr("ffffff")},
}


def _classify_theme_token(tok: str) -> str:
    """``'bg'`` for a surface/background token, else ``'fg'``.

    Surface intent is signalled by ``surface`` / ``background`` / a ``bg``
    stem; everything else (``fg``, ``foreground``, ``text``, ``border``,
    ``currentColor``) is foreground-ish.  ``foreground`` deliberately does NOT
    contain the substring ``background``, so it classifies as ``fg``.
    """
    t = tok.lower()
    if "surface" in t or "background" in t or "-bg" in t or ".bg" in t or "(--bg" in t:
        return "bg"
    return "fg"


# --------------------------------------------------------------------------
# Theme contrast clamp (D-003).
#
# ``build_document`` bakes ONLY the default ink/page (dark #1F1F1F + #EDEDED,
# light #FFFFFF + #000000).  An author-EXPLICIT stroke/ink colour is left
# untouched, so a colour chosen for a white page vanishes on the dark page --
# ``blue!60!black`` (#000099) is 1.15:1 on #1F1F1F, ``Navy`` 1.03:1, a bare
# ``black`` stroke 1.27:1 -- the primary curve/arrow/label is lost while the
# themed default-ink scaffolding survives.  The mirror defect is a hardcoded
# LIGHT palette (``palegrey`` #D8DCE0 1.38:1, ``lightgray`` 1.50:1) that fails
# on the light page.
#
# The fix RESOLVES each author colour in a stroke/ink context to RGB, measures
# its WCAG contrast against the theme surface the renderer was actually given,
# and only when it is below the 3:1 graphical floor blends it toward the
# surface-opposite endpoint (white on the dark page, black on the light page)
# by the minimal amount that reaches the floor.  This is the both-theme-safe
# shape the repair contract demands, for two reasons:
#   * it is CONTRAST-GATED -- a colour already at/above the floor in the
#     current theme is never rewritten, so a value that is fine in light is
#     left exactly as authored in light (only its dark rendering is repaired,
#     and vice-versa).  A light-theme regression from the dark clamp is
#     therefore impossible: the two themes are clamped against their own
#     surfaces independently.
#   * blending toward the surface-opposite is MONOTONIC in contrast, so the
#     result is never over-corrected past what legibility requires and the hue
#     is preserved as far as the floor allows.
#
# Scope discipline (same contract as the passes above): only clear stroke/ink
# contexts are clamped -- ``draw=`` / ``color=`` option values, a bare colour
# option (equivalent to ``color=``, promoted to explicit ``color={...}`` so the
# result is unambiguous), and ``\color`` / ``\textcolor`` label arguments.
# ``fill=`` is deliberately NOT clamped (a fill defines its own region; its
# real problem is the label drawn ON it, which needs per-element ink selection
# the renderer cannot express here), and neither is ``text=`` (the one key that
# routinely carries intentional light-on-dark-fill label text, which a
# page-relative clamp would wrongly invert).  An unresolvable expression
# (a ``\definecolor`` name, a chemfig positional bond-colour field, a gradient
# or colormap) is left exactly as authored -- advisory, never fatal.
# --------------------------------------------------------------------------

#: Canonical lowercase CSS/SVG keyword -> hex (the svgnames xcolor loads).
_SVG_HEX: dict[str, str] = {
    "aliceblue": "F0F8FF", "antiquewhite": "FAEBD7", "aqua": "00FFFF",
    "aquamarine": "7FFFD4", "azure": "F0FFFF", "beige": "F5F5DC",
    "bisque": "FFE4C4", "blanchedalmond": "FFEBCD", "blueviolet": "8A2BE2",
    "brown": "A52A2A", "burlywood": "DEB887", "cadetblue": "5F9EA0",
    "chartreuse": "7FFF00", "chocolate": "D2691E", "coral": "FF7F50",
    "cornflowerblue": "6495ED", "cornsilk": "FFF8DC", "crimson": "DC143C",
    "darkblue": "00008B", "darkcyan": "008B8B", "darkgoldenrod": "B8860B",
    "darkgray": "A9A9A9", "darkgreen": "006400", "darkgrey": "A9A9A9",
    "darkkhaki": "BDB76B", "darkmagenta": "8B008B", "darkolivegreen": "556B2F",
    "darkorange": "FF8C00", "darkorchid": "9932CC", "darkred": "8B0000",
    "darksalmon": "E9967A", "darkseagreen": "8FBC8F", "darkslateblue": "483D8B",
    "darkslategray": "2F4F4F", "darkslategrey": "2F4F4F", "darkturquoise": "00CED1",
    "darkviolet": "9400D3", "deeppink": "FF1493", "deepskyblue": "00BFFF",
    "dimgray": "696969", "dimgrey": "696969", "dodgerblue": "1E90FF",
    "firebrick": "B22222", "floralwhite": "FFFAF0", "forestgreen": "228B22",
    "fuchsia": "FF00FF", "gainsboro": "DCDCDC", "ghostwhite": "F8F8FF",
    "gold": "FFD700", "goldenrod": "DAA520", "greenyellow": "ADFF2F",
    "honeydew": "F0FFF0", "hotpink": "FF69B4", "indianred": "CD5C5C",
    "indigo": "4B0082", "ivory": "FFFFF0", "khaki": "F0E68C",
    "lavender": "E6E6FA", "lavenderblush": "FFF0F5", "lawngreen": "7CFC00",
    "lemonchiffon": "FFFACD", "lightblue": "ADD8E6", "lightcoral": "F08080",
    "lightcyan": "E0FFFF", "lightgoldenrod": "EEDD82", "lightgoldenrodyellow": "FAFAD2",
    "lightgray": "D3D3D3", "lightgreen": "90EE90", "lightgrey": "D3D3D3",
    "lightpink": "FFB6C1", "lightsalmon": "FFA07A", "lightseagreen": "20B2AA",
    "lightskyblue": "87CEFA", "lightslateblue": "8470FF", "lightslategray": "778899",
    "lightslategrey": "778899", "lightsteelblue": "B0C4DE", "lightyellow": "FFFFE0",
    "limegreen": "32CD32", "linen": "FAF0E6", "magenta": "FF00FF",
    "maroon": "B03060", "mediumaquamarine": "66CDAA", "mediumblue": "0000CD",
    "mediumorchid": "BA55D3", "mediumpurple": "9370DB", "mediumseagreen": "3CB371",
    "mediumslateblue": "7B68EE", "mediumspringgreen": "00FA9A",
    "mediumturquoise": "48D1CC", "mediumvioletred": "C71585", "midnightblue": "191970",
    "mintcream": "F5FFFA", "mistyrose": "FFE4E1", "moccasin": "FFE4B5",
    "navajowhite": "FFDEAD", "navy": "000080", "navyblue": "000080",
    "oldlace": "FDF5E6", "olivedrab": "6B8E23", "orange": "FFA500",
    "orangered": "FF4500", "orchid": "DA70D6", "palegoldenrod": "EEE8AA",
    "palegreen": "98FB98", "paleturquoise": "AFEEEE", "palevioletred": "DB7093",
    "papayawhip": "FFEFD5", "peachpuff": "FFDAB9", "peru": "CD853F",
    "pink": "FFC0CB", "plum": "DDA0DD", "powderblue": "B0E0E6",
    "purple": "A020F0", "rosybrown": "BC8F8F", "royalblue": "4169E1",
    "saddlebrown": "8B4513", "salmon": "FA8072", "sandybrown": "F4A460",
    "seagreen": "2E8B57", "seashell": "FFF5EE", "sienna": "A0522D",
    "silver": "C0C0C0", "skyblue": "87CEEB", "slateblue": "6A5ACD",
    "slategray": "708090", "slategrey": "708090", "snow": "FFFAFA",
    "springgreen": "00FF7F", "steelblue": "4682B4", "tan": "D2B48C",
    "teal": "008080", "thistle": "D8BFD8", "tomato": "FF6347",
    "turquoise": "40E0D0", "violet": "EE82EE", "violetred": "D02090",
    "wheat": "F5DEB3", "whitesmoke": "F5F5F5", "yellowgreen": "9ACD32",
}

#: xcolor BASE model names (valid lowercase), as 0..255 RGB.  These take
#: precedence over ``_SVG_HEX`` because a lowercase ``green``/``blue``/``lime``
#: is the base colour in xcolor even with svgnames loaded.
_BASE_RGB: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "yellow": (255, 255, 0),
    "black": (0, 0, 0), "white": (255, 255, 255), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "darkgray": (64, 64, 64), "lightgray": (191, 191, 191),
    "brown": (191, 128, 64), "lime": (191, 255, 0), "olive": (128, 128, 0),
    "orange": (255, 128, 0), "pink": (255, 191, 191), "purple": (191, 0, 64),
    "teal": (0, 128, 128), "violet": (128, 0, 128),
}

#: The exact surface RGB ``build_document`` bakes, per theme (dark page
#: #1F1F1F, light page #FFFFFF).  A theme not in this table skips the clamp.
_THEME_SURFACE_RGB: dict[str, tuple[int, int, int]] = {
    "dark": (0x1F, 0x1F, 0x1F),
    "light": (0xFF, 0xFF, 0xFF),
}

#: WCAG graphical/large-text contrast floor.  Strokes, arrows, and diagram
#: text are graphical/large, so 3:1 (not the 4.5:1 body-text floor) is the
#: right threshold and matches the sweep's measured verdicts.
_CONTRAST_FLOOR = 3.0

#: WCAG body/small-text contrast floor.  A ``\color`` / ``\textcolor`` argument
#: is INK for text (chemfig atom labels, node captions), not a graphical stroke,
#: so it must clear the 4.5:1 small-text floor rather than the 3:1 graphical one
#: (D-037: a recovered ``#36c`` -> #3366CC scores 3.07:1 on the dark page --
#: above the 3:1 graphical floor, so the old clamp left it -- yet the O/H atom
#: labels it colours are text and need 4.5:1).  Applied ONLY to the text-ink
#: macro path below; ``draw=`` / ``color=`` / bare-stroke options stay on the
#: 3:1 graphical floor.
_TEXT_CONTRAST_FLOOR = 4.5


def _name_to_rgb(name: str,
                 defs: dict[str, tuple[int, int, int]] | None = None
                 ) -> tuple[int, int, int] | None:
    n = name.strip().lower()
    if n in _BASE_RGB:
        return _BASE_RGB[n]
    hexv = _SVG_HEX.get(n)
    if hexv is not None:
        return (int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16))
    # A body-level ``\definecolor`` name (D-467) becomes resolvable once the
    # caller threads the collected map in.  Base/svgnames names take precedence
    # so a rare author redefinition of a stock name never shifts the stock hue.
    if defs is not None and n in defs:
        return defs[n]
    return None


def _mix_rgb(a: tuple[int, int, int], b: tuple[int, int, int],
             pct: float) -> tuple[int, int, int]:
    """xcolor ``a!pct!b`` -- pct% of ``a`` linearly blended with (100-pct)% of ``b``."""
    p = max(0.0, min(100.0, pct)) / 100.0
    return tuple(int(round(a[i] * p + b[i] * (1 - p))) for i in range(3))  # type: ignore[return-value]


_EXPR_RE = re.compile(
    r"\{?\s*rgb\s*,\s*255\s*:\s*red\s*,\s*(\d+)\s*;\s*green\s*,\s*(\d+)\s*;"
    r"\s*blue\s*,\s*(\d+)\s*\}?")


def _resolve_xcolor_rgb(expr: str,
                        defs: dict[str, tuple[int, int, int]] | None = None
                        ) -> tuple[int, int, int] | None:
    """Resolve a subset of xcolor colour expressions to RGB, else None.

    Handles the ``{rgb,255:red,R;green,G;blue,B}`` extended expression the
    earlier passes emit, a base/svgnames colour NAME, ``NAME!P`` (blend with
    white) and ``NAME!P!NAME2`` (blend NAME with NAME2).  When ``defs`` (the
    body's ``\\definecolor`` map, D-467) is supplied, a definecolor name also
    resolves.  Any other form -- an unknown name, a nested/multi-step blend,
    ``none`` -- returns None so the author's text is left untouched.
    """
    t = expr.strip()
    m = _EXPR_RE.fullmatch(t)
    if m:
        vals = tuple(max(0, min(255, int(x))) for x in m.groups())
        return vals  # type: ignore[return-value]
    parts = t.split("!")
    base = _name_to_rgb(parts[0], defs)
    if base is None:
        return None
    if len(parts) == 1:
        return base
    try:
        pct = float(parts[1])
    except ValueError:
        return None
    if len(parts) == 2:
        return _mix_rgb(base, (255, 255, 255), pct)
    if len(parts) == 3:
        other = _name_to_rgb(parts[2], defs)
        if other is None:
            return None
        return _mix_rgb(base, other, pct)
    return None


def _rel_luminance(rgb: tuple[int, int, int]) -> float:
    def _chan(c: int) -> float:
        cs = c / 255.0
        return cs / 12.92 if cs <= 0.03928 else ((cs + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * _chan(r) + 0.7152 * _chan(g) + 0.0722 * _chan(b)


def _contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _rel_luminance(a), _rel_luminance(b)
    hi, lo = (la, lb) if la >= lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


def _clamp_rgb_to_surface(rgb: tuple[int, int, int],
                          surface: tuple[int, int, int],
                          floor: float = _CONTRAST_FLOOR
                          ) -> tuple[int, int, int] | None:
    """Return a contrast-clamped colour, or None if it already meets ``floor``.

    Blends toward the surface-opposite endpoint (white on a dark surface,
    black on a light one) by the minimal fraction that reaches ``floor``
    (the 3:1 graphical floor by default; the caller passes the 4.5:1 text
    floor for a text-ink macro -- D-037).  Contrast is monotonic in that
    fraction, so the search terminates at the least-changed legible colour.
    """
    if _contrast_ratio(rgb, surface) >= floor:
        return None
    # White endpoint if the surface is dark (luminance below mid), else black.
    endpoint = (255, 255, 255) if _rel_luminance(surface) < 0.18 else (0, 0, 0)
    steps = 50
    for i in range(1, steps + 1):
        t = i / steps
        cand = tuple(int(round(rgb[j] * (1 - t) + endpoint[j] * t))
                     for j in range(3))
        if _contrast_ratio(cand, surface) >= floor:  # type: ignore[arg-type]
            return cand  # type: ignore[return-value]
    return endpoint


def _rgb_to_expr(rgb: tuple[int, int, int]) -> str:
    return f"rgb,255:red,{rgb[0]};green,{rgb[1]};blue,{rgb[2]}"


# A colour VALUE token: an xcolor extended ``{...}`` expression, or a
# name/blend (``Navy``, ``blue!60!black``, ``black!15``).
_COLOUR_VALUE = r"(\{[^{}]*\}|[A-Za-z][A-Za-z0-9]*(?:!\d+(?:![A-Za-z][A-Za-z0-9]*)?)?)"
#: bare (name/blend only -- no ``{...}``) colour token, for a standalone option.
_COLOUR_BARE = r"[A-Za-z][A-Za-z0-9]*(?:!\d+(?:![A-Za-z][A-Za-z0-9]*)?)?"

# \color{ARG} / \textcolor{ARG}{...}  (no [model]; \pagecolor excluded -- it
# sets the surface, which is not clamped against itself).
_CLAMP_MACRO_RE = re.compile(r"(\\(?:color|textcolor))(?!\s*\[)\s*\{([^{}]*)\}")
# stroke/ink option value: draw= / color=  (NOT fill=, NOT text=).
_CLAMP_OPT_RE = re.compile(rf"(?<![A-Za-z])((?:draw|color)\s*=\s*){_COLOUR_VALUE}")
# a bare colour as a whole option inside a [...] list: promote to color={...}.
_CLAMP_BARE_RE = re.compile(rf"([\[,]\s*)({_COLOUR_BARE})(?=\s*[,\]])")
# a node ``text=<value>`` ink option.  Clamped ONLY when its enclosing option
# block carries no ``fill=`` (D-351): a ``text=`` paired with a ``fill=`` was
# chosen for that fill and is handled by the fill-aware label-ink helpers, but a
# node with no fill draws its label straight on the page/backdrop, so its
# ``text=`` ink must clear the 4.5:1 text floor like any other label
# (circuitikz-w3-09 ``\node[text=DarkGreen]`` = 2.27:1 on the dark page).
_CLAMP_TEXT_RE = re.compile(rf"(?<![A-Za-z])(text\s*=\s*){_COLOUR_VALUE}")

#: A node LABEL carrier inside a single statement: a ``node`` (``\node`` or a
#: path-attached ``node``) that terminates in a brace group with visible text.
#: When a coloured option belongs to such a statement the colour also paints
#: the label text, so it must clear the 4.5:1 TEXT floor, not the 3:1 graphical
#: floor (D-233: ``\draw[...,blue!60!black] ... node{thick blue}`` and
#: ``\node[blue]{$\sin x$}`` lifted only to 3.12/3.15:1 leave the LABEL under
#: the text floor while the stroke passes).
_NODE_LABEL_RE = re.compile(r"\bnode\b[^;]*?\{[^{}]*?\S[^{}]*?\}")
#: a circuitikz component LABEL: a ``to[...]`` whose option list carries an
#: uppercase-keyed annotation (``R=$R_1$``, ``C=$C_1$``, ``L=$L_1$``, ``V=``,
#: ``I=`` ...).  circuitikz draws that label in the path's ``color=``/bare ink,
#: so -- exactly like a node label (D-233) -- that ink paints text and must
#: clear the 4.5:1 text floor, not the 3:1 graphical floor (D-351: a
#: ``color=green!45!black`` tinting both the bipole and its ``R=$R_3$`` label
#: was lifted only to 3:1, leaving the label sub-legible).  tikz option keys
#: (color/fill/draw/out/in/bend...) are lowercase, so an UPPERCASE key inside a
#: ``to[...]`` is unambiguously a component label, not a styling key.
_CKT_LABEL_RE = re.compile(r"\bto\b\s*\[[^\]]*?(?<![A-Za-z])[A-Z][A-Za-z]*\s*=")


def _enclosing_is_bracket(text: str, pos: int) -> bool:
    """True iff the innermost still-open group enclosing ``pos`` is a ``[``.

    The bare-colour clamp keys off ``,``/``[`` delimiters, which also separate
    the items of a ``\\foreach ... in {red,blue,green}`` VALUE LIST -- a brace
    group, not an option list.  Rewriting ``blue`` there to ``color={rgb,...}``
    corrupts the loop value and aborts the whole render (D-233 tikz-w2-11: the
    lifted token becomes a bare option key pgfkeys rejects).  Distinguishing a
    ``[...]`` option list from a ``{...}`` value list needs the enclosing
    delimiter, which a lookbehind cannot see; this scan supplies it.  Normalised
    ``{rgb,...}`` values nested inside an option list balance out, so a genuine
    option-list colour still reports a bracket.
    """
    stack: list[str] = []
    for ch in text[:pos]:
        if ch in "[{":
            stack.append(ch)
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
    return bool(stack) and stack[-1] == "["


def _statement_has_label(text: str, pos: int) -> bool:
    """True iff the TikZ statement containing ``pos`` carries a node label.

    A statement runs between the surrounding ``;`` terminators.  When it holds
    a ``node ... {label}``, an option colour in that statement also paints the
    label glyphs, so it must satisfy the small-text floor rather than the
    graphical floor (D-233)."""
    start = text.rfind(";", 0, pos) + 1
    end = text.find(";", pos)
    if end == -1:
        end = len(text)
    seg = text[start:end]
    return (_NODE_LABEL_RE.search(seg) is not None
            or _CKT_LABEL_RE.search(seg) is not None)


def _statement_has_ckt_label(text: str, pos: int) -> bool:
    """True iff the statement containing ``pos`` carries a circuitikz component
    label (``to[R=..]`` / ``to[V=..]`` ...).

    Narrower than ``_statement_has_label``: it excludes general tikz ``node``
    labels.  circuitikz draws such a component annotation in the path's
    ``color=``/bare ink, so an illegible one is a bug on EITHER page -- the same
    carve-out d033 makes for ``\\color`` macros -- which is what licenses lifting
    it on the light bare page (D-352/D-354).  A general ``\\node`` stroke is NOT
    licensed there: the light-page stroke/colour passthrough for author nodes is
    the deliberate, g04/d234-protected contract."""
    start = text.rfind(";", 0, pos) + 1
    end = text.find(";", pos)
    if end == -1:
        end = len(text)
    return _CKT_LABEL_RE.search(text[start:end]) is not None


# --------------------------------------------------------------------------
# \definecolor resolution and effective backdrop (D-467 / D-051 / D-331).
#
# The clamp above measures every author colour against the theme PAGE.  Two
# real gaps follow from that:
#   * a colour introduced by ``\definecolor{palestroke}{HTML}{CCCCCC}`` and used
#     as ``draw=palestroke`` / ``\textcolor{ink333}`` is an opaque NAME the
#     resolver could not read, so it was neither measured nor lifted (D-467).
#   * text/strokes frequently sit on an author-drawn opaque BACKDROP -- a
#     ``\fill[plate] ... rectangle`` (D-051), a ``\fill[fill=white]`` card
#     (D-331), a ``\pagecolor``, or a tikz-cd ``cells={nodes={fill=...}}`` --
#     not on the page, so a page-relative verdict is simply measuring the wrong
#     backdrop (it under-lifts a colour that is invisible on the fill, and
#     OVER-lifts a colour that was legible on the fill, making it worse).
#
# ``_collect_definecolors`` reads the body's ``\definecolor`` declarations into
# a name->RGB map so the resolver can see them.  ``_effective_surface`` detects
# a single unambiguous author backdrop and returns it in place of the page.
# When no backdrop is found the page is returned, so a body without one clamps
# EXACTLY as before -- the existing contract (and every G-03 test) is untouched.
# --------------------------------------------------------------------------

_DEFINECOLOR_COLLECT_RE = re.compile(
    r"\\definecolor\s*\{([^{}]+)\}\s*\{(HTML|RGB|rgb|gray|Gray)\}\s*\{([^{}]*)\}")


def _definecolor_value_to_rgb(model: str, val: str) -> tuple[int, int, int] | None:
    """Resolve one ``\\definecolor`` value by model, else None."""
    model_l = model.lower()
    v = val.strip()
    try:
        if model_l == "html":
            h = _expand_hex(v)
            if len(h) != 6:
                return None
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        parts = [p.strip() for p in v.split(",")]
        if model == "RGB":
            if len(parts) < 3:
                return None
            return tuple(max(0, min(255, int(round(float(p))))) for p in parts[:3])  # type: ignore[return-value]
        if model_l == "rgb":
            if len(parts) < 3:
                return None
            return tuple(max(0, min(255, int(round(float(p) * 255)))) for p in parts[:3])  # type: ignore[return-value]
        if model_l == "gray":
            g = max(0, min(255, int(round(float(parts[0]) * 255))))
            return (g, g, g)
    except (ValueError, IndexError):
        return None
    return None


def _collect_definecolors(body: str) -> dict[str, tuple[int, int, int]]:
    """Map lowercase ``\\definecolor`` names -> RGB for the resolver (D-467)."""
    defs: dict[str, tuple[int, int, int]] = {}
    for m in _DEFINECOLOR_COLLECT_RE.finditer(body):
        rgb = _definecolor_value_to_rgb(m.group(2), m.group(3))
        if rgb is not None:
            defs[m.group(1).strip().lower()] = rgb
    return defs


_PAGECOLOR_RE = re.compile(r"\\pagecolor(?!\s*\[)\s*\{([^{}]*)\}")
#: a tikz-cd (or tikz) ``... nodes = { ... fill = C ... }`` cell fill.
_CELL_FILL_RE = re.compile(
    r"nodes\s*=\s*\{[^{}]*?(?<![A-Za-z])fill\s*=\s*(\{[^{}]*\}|[A-Za-z][\w!]*)")
#: a ``\fill[<opts>] (...) rectangle`` background rectangle.
_FILL_RECT_RE = re.compile(r"\\fill\s*\[([^\]]*)\][^;]*?\brectangle\b")


def _fill_opt_colour(opts: str) -> str | None:
    """The fill colour of a ``\\fill[...]`` option list: ``fill=C`` or bare C."""
    m = re.search(r"(?<![A-Za-z])fill\s*=\s*(\{[^{}]*\}|[A-Za-z][\w!]*)", opts)
    if m:
        return m.group(1)
    for tok in opts.split(","):
        tok = tok.strip()
        if re.fullmatch(r"[A-Za-z][\w!]*", tok):
            return tok
    return None


# --------------------------------------------------------------------------
# Alpha / fill-opacity compositing (D-470).
#
# The backdrop and fill-label helpers below measured an author fill as OPAQUE
# and ignored a ``fill opacity=``/``opacity=`` sitting in the same option list.
# A ``fill=blue, fill opacity=0.15`` cell is not blue -- it is 15% blue over the
# page (``#D9D9FF`` on white, a near-white surface), so a page-relative verdict
# that treats it as saturated blue picks the wrong ink endpoint: it injects
# WHITE labels (or lifts a text ink toward white) that then score ~1.4:1 on the
# actual pale composite (tikz-cd-w3-10).  ``_fill_alpha`` reads the effective
# fill alpha and ``_composite`` blends the colour over the surface, so the
# measured backdrop is the pixels a viewer actually sees.  A fill without an
# opacity key resolves to alpha 1.0 -> the colour is returned unchanged, so
# every opacity-free body is byte-identical.
# --------------------------------------------------------------------------

#: ``fill opacity=<a>`` (fill-only alpha); wins over a standalone ``opacity=``.
_FILL_OPACITY_RE = re.compile(r"fill\s+opacity\s*=\s*([0-9]*\.?[0-9]+)")
#: a STANDALONE ``opacity=<a>`` option (sets both fill and draw alpha).  Anchored
#: to a ``[``/``,`` delimiter so a qualified ``fill opacity``/``text opacity``/
#: ``draw opacity`` (a word + space before ``opacity``) never matches here.
_BARE_OPACITY_RE = re.compile(r"[\[,]\s*opacity\s*=\s*([0-9]*\.?[0-9]+)")


def _fill_alpha(opts: str) -> float:
    """Effective fill alpha from an option list (D-470): ``fill opacity`` wins,
    else a standalone ``opacity``, else fully opaque (1.0)."""
    m = _FILL_OPACITY_RE.search(opts) or _BARE_OPACITY_RE.search(opts)
    if m is None:
        return 1.0
    try:
        return max(0.0, min(1.0, float(m.group(1))))
    except ValueError:
        return 1.0


def _composite(fg: tuple[int, int, int], alpha: float,
               bg: tuple[int, int, int]) -> tuple[int, int, int]:
    """``fg`` painted at ``alpha`` over ``bg`` (straight alpha over)."""
    if alpha >= 1.0:
        return fg
    return tuple(int(round(fg[i] * alpha + bg[i] * (1 - alpha)))  # type: ignore[return-value]
                 for i in range(3))


#: the CONTENT of a tikz-cd/tikz ``nodes = { ... }`` cell-option block, so both
#: its ``fill=`` and any ``fill opacity=`` can be read together (D-470).
_CELL_NODES_RE = re.compile(r"nodes\s*=\s*\{([^{}]*)\}")


def _effective_surface(body: str, page: tuple[int, int, int],
                       defs: dict[str, tuple[int, int, int]]
                       ) -> tuple[int, int, int]:
    """A single unambiguous author backdrop, else the page (D-051/D-331/D-467).

    Priority: an explicit ``\\pagecolor``; a tikz-cd/tikz ``nodes={fill=...}``
    cell fill; or a SOLE ``\\fill[...] ... rectangle`` background.  Anything
    ambiguous (several fill rectangles, per-node style fills only) falls back to
    the page, so behaviour is unchanged for every body that lacks one.
    """
    m = _PAGECOLOR_RE.search(body)
    if m:
        rgb = _resolve_xcolor_rgb(m.group(1).strip(), defs)
        if rgb is not None:
            return rgb
    mnodes = _CELL_NODES_RE.search(body)
    if mnodes:
        blk = mnodes.group(1)
        fm = re.search(r"(?<![A-Za-z])fill\s*=\s*(\{[^{}]*\}|[A-Za-z][\w!]*)", blk)
        if fm:
            rgb = _resolve_xcolor_rgb(fm.group(1).strip(), defs)
            if rgb is not None:
                # Composite the cell fill over the page by its fill opacity so
                # the backdrop is the effective (visible) surface, not the
                # saturated colour (D-470: fill=blue at 0.15 -> #D9D9FF).
                return _composite(rgb, _fill_alpha(blk), page)
    rects = _FILL_RECT_RE.findall(body)
    if len(rects) == 1:
        col = _fill_opt_colour(rects[0])
        if col:
            rgb = _resolve_xcolor_rgb(col.strip(), defs)
            if rgb is not None:
                return _composite(rgb, _fill_alpha(rects[0]), page)
    return page


def detect_dark_plate(body: str) -> tuple[int, int, int] | None:
    """RGB of a SOLE author DARK background plate, else None (D-357).

    A model commonly emits a self-contained "card" whose background is one
    ``\\fill[<colour>] ... rectangle`` in a dark colour, with light ink drawn on
    top -- but the plate rectangle does not always cover the full drawing bbox,
    so leads/grounds spilling past it onto the WHITE light-page render are still
    in that light ink and vanish (circuitikz-w4-*: #5FD4E4 on #FFFFFF = 1.75:1).
    ``build_document`` uses this on the LIGHT page to match the page surface to
    the detected plate, so the whole cropped canvas is the plate and off-plate
    ink stays legible; None means the page stays white and the render is
    byte-identical.

    Deliberately the SAME ``sole \\fill[...] rectangle`` heuristic that
    ``_effective_surface`` already commits to as the ink backdrop -- extended to
    the page only when that backdrop is DARK (luminance below the mid point the
    clamp uses), which is the exact condition under which light ink is designed
    for the plate and illegible off it.  A body with no such plate, several
    plates, or a light plate returns None.  Advisory: any internal fault
    degrades to None (plain white page) rather than raising.
    """
    try:
        defs = _collect_definecolors(body)
        rects = _FILL_RECT_RE.findall(body)
        if len(rects) == 1:
            col = _fill_opt_colour(rects[0])
            if not col:
                return None
            rgb = _resolve_xcolor_rgb(col.strip(), defs)
            if rgb is None:
                return None
            if _rel_luminance(rgb) >= 0.18:  # a light/mid plate is not this case
                return None
            return rgb
        # No sole \fill rectangle.  A tikz-cd/tikz all-dark cell card (D-467)
        # is the same situation by a different construction: the cell fill
        # ``cells={nodes={fill=<dark>}}`` is the card and light ink (edge
        # arrows, edge labels) sits on it, but the arrows crossing BETWEEN
        # cells fall on the page -- invisible on white.  Extending the plate to
        # the whole page (as the sole-rectangle case does) keeps that off-cell
        # ink legible.  Guarded exactly as the rectangle case: fire ONLY when
        # the sole resolvable cell fill is DARK and there is no lighter fill
        # elsewhere, so a near-white-cell card (tikz-cd-w3-08) or a mixed body
        # keeps the plain white page and is byte-identical.
        if not rects:
            mnodes = _CELL_NODES_RE.search(body)
            if mnodes:
                blk = mnodes.group(1)
                fm = re.search(
                    r"(?<![A-Za-z])fill\s*=\s*(\{[^{}]*\}|[A-Za-z][\w!]*)", blk)
                if fm:
                    rgb = _resolve_xcolor_rgb(fm.group(1).strip(), defs)
                    if rgb is not None:
                        eff = _composite(rgb, _fill_alpha(blk), (255, 255, 255))
                        if _rel_luminance(eff) < 0.18:
                            return eff
        return None
    except Exception:                      # pragma: no cover - defensive
        return None


#: A ``\shade[...] ... rectangle`` background (an axis/radial gradient), used by
#: detect_light_plate to judge a light card that is painted with gradients
#: rather than a solid ``\fill`` (D-353, circuitikz-w3-04).
_SHADE_RECT_RE = re.compile(r"\\shade\s*\[([^\]]*)\][^;]*?\brectangle\b")
#: ``[<edge> ]color=`` inside a ``\shade`` option list -- the gradient endpoint
#: colours (left/right/top/bottom/inner/outer/middle color, or a bare color).
_SHADE_COLOUR_RE = re.compile(
    r"(?:[a-z]+\s+)?color\s*=\s*(\{[^{}]*\}|[A-Za-z][\w!.]*)")


def detect_light_plate(body: str) -> tuple[int, int, int] | None:
    """RGB of a SOLE author LIGHT background, else None (D-353).

    The MIRROR of ``detect_dark_plate``.  A model commonly emits a
    self-contained LIGHT "card": the whole background is a white/pale
    ``\\fill[...] ... rectangle`` (circuitikz-w3-01) or a set of pale
    ``\\shade[...] ... rectangle`` gradients (circuitikz-w3-04), with a light
    palette drawn on top.  On the DARK page ``build_document`` bakes a dark
    surface + light default ink, so the card renders as a jarring light patch
    and every uncoloured label collapses (#EDEDED on the pale region: 1.2:1
    w3-04) while a light author stroke stays faint.  ``build_document`` uses
    this on the DARK page to MATCH the page to the light card and bake DARK ink
    -- the exact symmetric treatment ``detect_dark_plate`` gives a dark card on
    the light page -- so the whole cropped canvas is the card the author built
    and the contrast clamp lifts the palette against it (the light render is
    already the white page, so it is byte-identical).

    Fires ONLY when the background is unambiguously a single light region:
      * no explicit ``\\pagecolor`` (the author did not choose the page);
      * not already a dark card (``detect_dark_plate`` is None);
      * EITHER exactly one ``\\fill[...] rectangle`` and no ``\\shade`` rectangle,
        OR one-or-more ``\\shade[...] rectangle`` and no ``\\fill`` rectangle;
      * every detected background colour resolves, none is genuinely dark
        (luminance >= 0.18), and their mean is clearly light (>= 0.5).
    Anything else returns None, so a mixed or dark body keeps the dark page and
    is byte-identical.  Advisory: any internal fault degrades to None.
    """
    try:
        if _PAGECOLOR_RE.search(body):
            return None
        if detect_dark_plate(body) is not None:
            return None
        defs = _collect_definecolors(body)
        fills = _FILL_RECT_RE.findall(body)
        shades = _SHADE_RECT_RE.findall(body)
        lums: list[float] = []
        if fills and not shades:
            if len(fills) != 1:
                return None
            col = _fill_opt_colour(fills[0])
            if not col:
                return None
            rgb = _resolve_xcolor_rgb(col.strip(), defs)
            if rgb is None:
                return None
            lums.append(_rel_luminance(rgb))
        elif shades and not fills:
            for opt in shades:
                got = False
                for m in _SHADE_COLOUR_RE.finditer(opt):
                    rgb = _resolve_xcolor_rgb(m.group(1).strip(), defs)
                    if rgb is not None:
                        lums.append(_rel_luminance(rgb))
                        got = True
                if not got:                # a shade whose endpoints we cannot
                    return None            # resolve -> bail (conservative)
        else:                              # no background rects, or a fill+shade
            return None                    # mix -> ambiguous, keep the dark page
        if not lums:
            return None
        if min(lums) < 0.18:               # a genuinely dark region present ->
            return None                    # not a light card
        if sum(lums) / len(lums) < 0.5:    # not predominantly light -> leave it
            return None
        return (255, 255, 255)
    except Exception:                      # pragma: no cover - defensive
        return None


def _enclosing_open_index(text: str, pos: int) -> int:
    """Index of the innermost still-open ``[`` enclosing ``pos``, else -1."""
    stack: list[tuple[str, int]] = []
    for i, ch in enumerate(text[:pos]):
        if ch in "[{":
            stack.append((ch, i))
        elif ch == "]":
            if stack and stack[-1][0] == "[":
                stack.pop()
        elif ch == "}":
            if stack and stack[-1][0] == "{":
                stack.pop()
    return stack[-1][1] if stack and stack[-1][0] == "[" else -1


def _enclosing_block_has_fill(text: str, pos: int) -> bool:
    """True iff the ``[...]`` option block enclosing ``pos`` carries a ``fill=``.

    Used to decide whether a ``text=`` ink is fill-paired (leave it to the
    fill-aware label-ink helpers) or a bare-page label that must be
    contrast-clamped (D-351)."""
    idx = _enclosing_open_index(text, pos)
    if idx < 0:
        return False
    depth = 0
    end = len(text)
    for i in range(idx, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    return re.search(r"(?<![A-Za-z])fill\s*=", text[idx:end]) is not None


_FILL_CMD_BEFORE_RE = re.compile(r"\\(filldraw|fill|shade|pagecolor)\s*$")


def _bare_option_is_fill(text: str, pos: int) -> bool:
    """True iff the ``[...]`` option enclosing ``pos`` belongs to ``\\fill``/``\\shade``.

    A ``\\fill[plate]`` bare colour is a region, not ink (the "fill is not
    clamped" contract), and is also the detected backdrop itself -- so the
    light-theme backdrop clamp must leave it alone rather than recolour the
    background.  The dark path keeps clamping it (circuitikz-w1-15's opaque
    plate relies on that lift), so this gate is applied only on the light path.
    """
    idx = _enclosing_open_index(text, pos)
    if idx < 0:
        return False
    return _FILL_CMD_BEFORE_RE.search(text[:idx]) is not None


#: a tikz-cd / tikz-matrix per-cell option prefix ``|[ ... ]|`` (D-471).  A
#: ``fill=`` inside it sets the fill of the cell node that follows, and the cell
#: runs until the next ``&`` (column) or ``\\`` (row) separator.
_CELL_PREFIX_RE = re.compile(r"\|\[([^\]]*)\]\|")
_CELL_FILL_IN_OPTS_RE = re.compile(
    r"(?<![A-Za-z])fill\s*=\s*(\{[^{}]*\}|[A-Za-z$][\w!.$-]*)")


def _enclosing_cell_fill(body: str, pos: int,
                         defs: dict[str, tuple[int, int, int]] | None
                         ) -> tuple[int, int, int] | None:
    """RGB of the ``|[fill=...]|`` cell fill a label at ``pos`` sits on, else None.

    A tikz-cd cell may be prefixed with ``|[fill=C]|`` (D-471), which paints the
    cell's node.  A ``\\textcolor{...}`` label inside that cell is drawn ON that
    fill, so its contrast must be measured against the fill -- not the page/plate
    the global clamp uses.  Returns the fill RGB when ``pos`` falls in a cell
    with a resolvable ``|[...fill=...]|`` prefix and no cell separator (``&`` or
    ``\\``) sits between that prefix and ``pos``; else None (fall back to the
    global surface, so a plain body is byte-identical)."""
    best: str | None = None
    for m in _CELL_PREFIX_RE.finditer(body):
        if m.end() > pos:
            break
        between = body[m.end():pos]
        if "&" in between or "\\\\" in between:
            continue
        fm = _CELL_FILL_IN_OPTS_RE.search(m.group(1))
        if fm:
            best = fm.group(1)
    if best is None:
        return None
    return _resolve_xcolor_rgb(best.strip("{} "), defs)


def _clamp_body_colours(body: str, theme: str,
                        applied: list[str]) -> str:
    """Contrast-clamp author stroke/ink colours to the themed surface (D-003)."""
    page = _THEME_SURFACE_RGB.get(theme)
    if page is None:
        return body
    # Resolve author \definecolor names (D-467) and the effective backdrop the
    # ink actually sits on (D-051/D-331).  When no backdrop is present, surface
    # == page and every branch below reduces to the original page-relative
    # behaviour, so existing renders are byte-identical.
    defs = _collect_definecolors(body)
    surface = _effective_surface(body, page, defs)
    backdrop = surface != page
    # Scope decision (D-003; re-affirmed for D-245/246/247; extended for
    # D-051/D-331/D-467).  Two independent gates below:
    #
    #   * the TEXT-INK macro clamp (\color / \textcolor) runs in BOTH themes
    #     against the 4.5:1 small-text floor (D-033): an illegible label is a
    #     bug on either page, and it is contrast-gated so an already-legible
    #     label (``\color{Navy}`` = 16:1 on white, g03) is byte-identical.
    #   * the STROKE/FILL-option clamp (draw= / color= / bare option) runs on
    #     the DARK page as before -- AND, now, on EITHER page when a resolvable
    #     author BACKDROP is detected (``\fill[plate] ... rectangle`` D-051,
    #     ``\pagecolor``, a tikz-cd cell fill).  On the bare page a symmetric
    #     light stroke clamp stays DELIBERATELY REJECTED (a page-relative light
    #     clamp cannot tell an illegible pale stroke from an intentional
    #     decorative one, and g02/g04 protect that passthrough); but on a
    #     KNOWN author backdrop that ambiguity is gone -- a dark stroke on a
    #     detected dark plate is unambiguously invisible -- so the clamp is
    #     safe there in both themes.
    #
    # Every measurement uses ``surface`` (the backdrop when detected, else the
    # page) so a colour legible on its real backdrop is never lifted -- this is
    # what stops the D-331 OVER-lift of ``\color{black!80}`` sitting on a white
    # card.  When no backdrop is present surface == page and behaviour is the
    # original page-relative clamp, so every G-03/G-04 body is byte-identical.
    dark_theme = _rel_luminance(page) < 0.18

    def _clamped_expr(value: str, floor: float = _CONTRAST_FLOOR) -> str | None:
        rgb = _resolve_xcolor_rgb(value, defs)
        if rgb is None:
            return None
        new = _clamp_rgb_to_surface(rgb, surface, floor)
        if new is None:
            return None
        return _rgb_to_expr(new)

    def _macro_sub(m: re.Match) -> str:
        # A \color / \textcolor argument is text ink -> the 4.5:1 small-text
        # floor, not the 3:1 graphical floor (D-037: #3366CC is 3.07:1 on the
        # dark page, above the graphical floor yet below the text floor its
        # atom labels need).
        macro, arg = m.group(1), m.group(2)
        rgb = _resolve_xcolor_rgb(arg, defs)
        if rgb is None:
            return m.group(0)
        # D-471: when the label sits inside a tikz-cd ``|[fill=C]|`` cell it is
        # drawn ON that fill, so measure/clamp against the ENCLOSING FILL, not
        # the page/plate the global clamp uses.  A page-relative verdict here
        # both misses an illegible on-fill pair (white on pale yellow) AND
        # WRONGLY lifts a correct one (black on a yellow fill scores 12.64:1 but
        # measured 1.27:1 on the dark page was greyed out).  No enclosing fill
        # -> local == surface, so a plain body is byte-identical.
        local = surface
        where = "backdrop" if backdrop else f"{theme} surface"
        cell_fill = _enclosing_cell_fill(body, m.start(), defs)
        if cell_fill is not None:
            local = cell_fill
            where = "enclosing fill"
        new = _clamp_rgb_to_surface(rgb, local, _TEXT_CONTRAST_FLOOR)
        if new is None:
            return m.group(0)
        expr = _rgb_to_expr(new)
        ratio = _contrast_ratio(rgb, local)
        applied.append(
            f"{macro}{{{arg}}} -> {macro}{{{expr}}} "
            f"(text ink {ratio:.2f}:1 below {_TEXT_CONTRAST_FLOOR:g}:1 on the "
            f"{where}; lifted to the text floor)")
        return f"{macro}{{{expr}}}"

    # Text-ink macro clamps run in BOTH themes (see note above).  Note that an
    # EXPLICIT-model form (``\textcolor[HTML]{336699}``) is deliberately NOT
    # clamped: test_latex_g04 encodes the contract that an author writing an
    # explicit xcolor model is "speaking xcolor" and must pass through verbatim.
    body = _CLAMP_MACRO_RE.sub(_macro_sub, body)

    # The stroke/fill-option clamps run FULLY on the dark page, or on either
    # page when a resolvable author backdrop was detected.  On the LIGHT bare
    # page (no backdrop) they run in a RESTRICTED "labels only" mode: a colour
    # is lifted only when its statement also paints LABEL TEXT -- the same
    # "an illegible label is a bug in either theme" rationale that already runs
    # the \color/\textcolor macro clamp above in both themes (D-033).  A purely
    # decorative stroke with NO label is left exactly as authored, preserving
    # the deliberate light-page stroke passthrough that g02/g04/d033 protect
    # (a page-relative clamp cannot tell an illegible pale stroke from an
    # intentional decorative one).  D-352/D-354: circuitikz LABEL inks --
    # ``color=``/bare/``text=`` that tint a ``to[R=..]`` component label or a
    # ``node{..}`` caption -- are the illegible-label case this mode reaches on
    # the light page (a 24-member ``color=cyan`` palette at 1.07:1, a
    # ``color=darkink`` #CCCCCC scope ink at 1.61:1), while a decorative pale
    # frame / ground symbol with no label stays as authored.
    labels_only = not (dark_theme or backdrop)

    def _opt_sub(m: re.Match) -> str:
        key, value = m.group(1), m.group(2)
        # A colour on a statement that also carries a node label paints the
        # label glyphs too, so it must clear the 4.5:1 text floor (D-233).
        texty = _statement_has_label(body, m.start(2))
        # Light bare page (labels_only): lift ONLY a circuitikz component-label
        # statement's ink -- the unambiguous illegible-label case (D-352/D-354);
        # a general tikz node/decorative stroke stays on the deliberate
        # light-page passthrough (g04/d234).
        if labels_only:
            if not _statement_has_ckt_label(body, m.start(2)):
                return m.group(0)
            texty = True
        floor = _TEXT_CONTRAST_FLOOR if texty else _CONTRAST_FLOOR
        expr = _clamped_expr(value, floor)
        if expr is None:
            return m.group(0)
        ratio = _contrast_ratio(_resolve_xcolor_rgb(value, defs), surface)  # type: ignore[arg-type]
        where = "backdrop" if backdrop else f"{theme} surface"
        applied.append(
            f"{key}{value} -> {key}{{{expr}}} "
            f"(contrast {ratio:.2f}:1 below {floor:g}:1 on the "
            f"{where}; lifted to the "
            f"{'text' if texty else 'graphical'} floor)")
        return f"{key}{{{expr}}}"

    body = _CLAMP_OPT_RE.sub(_opt_sub, body)

    def _bare_sub(m: re.Match) -> str:
        # The delimiters this regex keys off also separate a \foreach value
        # list; only lift when the token really sits inside a [...] option
        # list, never inside a {...} value list (D-233 tikz-w2-11 abort).
        if not _enclosing_is_bracket(body, m.start(2)):
            return m.group(0)
        # A bare colour that is the positional option of a ``\fill`` /
        # ``\filldraw`` / ``\shade`` command is a FILL region, not a stroke/ink
        # -- the "fill is not clamped" contract -- so it is left alone in BOTH
        # themes when the statement draws NO label (D-468).  The earlier gate
        # only skipped this when a DISTINCT backdrop was present, so an authored
        # dark plate ``\fill[black!88]`` whose colour EQUALS the dark page
        # (#1F1F1F -> backdrop=False) was misread as a bare stroke, measured
        # 1.00:1 against the page, and lifted to a mid-grey slab
        # (rgb(107,107,107)), destroying the author's deliberately dark plate.
        # The carve-out is conditioned on the statement having no node label: a
        # ``\fill[blue] ... node{..}`` draws that label glyph in the fill colour
        # (D-233 w1-04), so there the colour IS ink and must still be lifted to
        # the text floor -- only a label-less fill region (a backdrop plate,
        # a self-painted disc) is left untouched.
        if (_bare_option_is_fill(body, m.start(2))
                and not _statement_has_label(body, m.start(2))):
            return m.group(0)
        pre, value = m.group(1), m.group(2)
        texty = _statement_has_label(body, m.start(2))
        # Light bare page (labels_only): only a circuitikz component-label
        # statement is the unambiguous illegible-label case (see _opt_sub).
        if labels_only:
            if not _statement_has_ckt_label(body, m.start(2)):
                return m.group(0)
            texty = True
        floor = _TEXT_CONTRAST_FLOOR if texty else _CONTRAST_FLOOR
        expr = _clamped_expr(value, floor)
        if expr is None:
            return m.group(0)
        ratio = _contrast_ratio(_resolve_xcolor_rgb(value, defs), surface)  # type: ignore[arg-type]
        where = "backdrop" if backdrop else f"{theme} surface"
        applied.append(
            f"{value} -> color={{{expr}}} "
            f"(bare {'label' if texty else 'stroke'} colour, contrast "
            f"{ratio:.2f}:1 below {floor:g}:1 on the {where}; "
            f"lifted to the {'text' if texty else 'graphical'} floor)")
        return f"{pre}color={{{expr}}}"

    body = _CLAMP_BARE_RE.sub(_bare_sub, body)

    def _text_sub(m: re.Match) -> str:
        # A ``text=`` ink paired with a ``fill=`` was chosen for that fill and
        # is handled by the fill-aware label-ink helpers -- leave it (D-331: do
        # not over-lift a label picked for its chip).  A ``text=`` with no fill
        # in its block is a bare-page label and must clear the text floor.
        if _enclosing_block_has_fill(body, m.start(2)):
            return m.group(0)
        key, value = m.group(1), m.group(2)
        expr = _clamped_expr(value, _TEXT_CONTRAST_FLOOR)
        if expr is None:
            return m.group(0)
        ratio = _contrast_ratio(_resolve_xcolor_rgb(value, defs), surface)  # type: ignore[arg-type]
        where = "backdrop" if backdrop else f"{theme} surface"
        applied.append(
            f"{key}{value} -> {key}{{{expr}}} "
            f"(node text ink {ratio:.2f}:1 below {_TEXT_CONTRAST_FLOOR:g}:1 on "
            f"the {where}; lifted to the text floor)")
        return f"{key}{{{expr}}}"

    body = _CLAMP_TEXT_RE.sub(_text_sub, body)
    return body


# --------------------------------------------------------------------------
# Categorical \foreach palette clamp (D-238).
#
# A model builds a many-series legend by looping a literal colour list:
# ``\foreach \c in {red,blue,green,...,cyan!60,magenta!60,...}``.  The bare/opt
# clamps above DELIBERATELY skip a ``{...}`` value list (rewriting a token there
# to ``color={rgb,...}`` injects a comma and corrupts the loop -- the exact
# D-233/D-488 abort the _enclosing_is_bracket guard exists to prevent), so these
# series colours are never lifted and a whole categorical palette can sit under
# the floor: on the LIGHT page the recycled ``!60`` tints blend toward WHITE
# (``cyan!60`` = 60% cyan + 40% white = #66FFFF = 1.21:1) and the saturated
# primaries are pale too (``cyan`` #00FFFF = 1.25:1); on the DARK page the
# saturated author primaries fall below the floor instead.
#
# The correct engine behaviour (repair contract): a palette that recycles by
# tinting must tint toward the OPPOSITE of the surface -- ``NAME!p!black`` on
# the light page, ``NAME!p!white`` on the dark one -- which is MONOTONIC in
# contrast and legible on the surface the renderer was actually given.  This
# pass rewrites each below-floor item to that comma-free three-part blend, so
# the loop's item count and structure are byte-identical (no injected comma)
# and only the pixels change.  It fires ONLY on a list whose EVERY item already
# resolves to a colour, so a numeric/coordinate ``\foreach \x in {0,1,2}`` list
# is never touched, and only on items that FAIL the floor, so a palette already
# legible on the active surface is byte-identical.
_FOREACH_LIST_RE = re.compile(r"(\\foreach\b[^{]*?\bin\s*)\{([^{}]*)\}")

#: The default ink ``build_document`` bakes per theme (black on the white page,
#: #EDEDED on the dark page).  Used to tell whether uncoloured text/strokes are
#: legible on a detected author plate (D-358).
_THEME_DEFAULT_INK: dict[str, tuple[int, int, int]] = {
    "light": (0, 0, 0),
    "dark": (0xED, 0xED, 0xED),
}
#: A sole author plate ``\fill[...] ... rectangle ... ;`` captured through its
#: terminating ``;`` so a default-ink ``\color`` can be injected right after it.
_FILL_RECT_STMT_RE = re.compile(r"\\fill\s*\[([^\]]*)\][^;]*?\brectangle\b[^;]*;")


def _plate_default_ink(body: str, theme: str, applied: list[str]) -> str:
    """Set an on-plate default ink when the baked page ink is illegible on it.

    The step-8 clamp lifts author colour TOKENS against the effective backdrop,
    but an element with NO explicit colour draws in the page-relative default
    ink ``build_document`` bakes -- and on a whole-picture author plate that ink
    can be illegible (circuitikz-w4-05: the baked black light-page ink on the
    #16324A plate = 1.59:1, so an uncoloured ``\\node{...}`` label vanishes).
    When a SINGLE author ``\\fill[...] ... rectangle`` backdrop is detected and
    the theme's baked default ink fails the text floor against it, inject a
    plate-legible ``\\color`` right after that fill so subsequent uncoloured
    ink is chosen for the PLATE, not the page.  An explicit per-element colour
    still wins, and when the baked ink already clears the plate this is a no-op
    (byte-identical) -- so the dark render, where #EDEDED clears the dark plate,
    is untouched.
    """
    page = _THEME_SURFACE_RGB.get(theme)
    default_ink = _THEME_DEFAULT_INK.get(theme)
    if page is None or default_ink is None:
        return body
    defs = _collect_definecolors(body)
    surface = _effective_surface(body, page, defs)
    if surface == page:
        return body
    rects = _FILL_RECT_RE.findall(body)
    if len(rects) != 1 or _fill_opt_colour(rects[0]) is None:
        return body  # backdrop came from \pagecolor / cell fill, not a plate
    # Only a DARK plate: a light plate on which the baked dark-page ink fails is
    # the pale-fill case _pale_fill_label_ink already owns (D-234/D-331), and
    # blanket-injecting a default ink there would double-handle it and disturb a
    # node that carries its own explicit colour.  A dark plate under the black
    # light-page ink (w4-05) is the gap this pass exists to close.
    if _rel_luminance(surface) >= 0.18:
        return body
    if _contrast_ratio(default_ink, surface) >= _TEXT_CONTRAST_FLOOR:
        return body  # baked ink already legible on the plate
    ink = _clamp_rgb_to_surface(default_ink, surface, _TEXT_CONTRAST_FLOOR)
    if ink is None:
        return body
    expr = _rgb_to_expr(ink)

    injected = False

    def _sub(m: re.Match) -> str:
        nonlocal injected
        if injected:
            return m.group(0)
        injected = True
        applied.append(
            f"plate default ink -> \\color{{{expr}}} "
            f"(baked {theme} ink {_contrast_ratio(default_ink, surface):.2f}:1 "
            f"below {_TEXT_CONTRAST_FLOOR:g}:1 on the author plate; uncoloured "
            f"ink re-inked for the plate)")
        return m.group(0) + f"\n\\color{{{expr}}}"

    return _FILL_RECT_STMT_RE.sub(_sub, body, count=1)


def _clamp_foreach_palette(body: str, theme: str, applied: list[str]) -> str:
    surface = _THEME_SURFACE_RGB.get(theme)
    if surface is None:
        return body
    dark = _rel_luminance(surface) < 0.18
    endpoint = (255, 255, 255) if dark else (0, 0, 0)
    endpoint_name = "white" if dark else "black"
    defs = _collect_definecolors(body)

    def _fix_item(tok: str) -> str:
        raw = tok.strip()
        if not raw:
            return tok
        rgb = _resolve_xcolor_rgb(raw, defs)
        if rgb is None or _contrast_ratio(rgb, surface) >= _CONTRAST_FLOOR:
            return tok
        m = re.fullmatch(r"([A-Za-z][A-Za-z0-9]*)(?:!(\d+))?", raw)
        if not m:
            return tok
        base_name = m.group(1)
        base_rgb = _name_to_rgb(base_name, defs)
        if base_rgb is None:
            return tok
        author_p = int(m.group(2)) if m.group(2) else 100
        # Keep the author's saturation first (only flip the implicit blend
        # partner to the surface-opposite); if that still fails, search downward
        # for the strongest tint that clears the floor.
        for p in [author_p] + list(range(90, -1, -10)):
            if _contrast_ratio(_mix_rgb(base_rgb, endpoint, p), surface) >= _CONTRAST_FLOOR:
                new = f"{base_name}!{p}!{endpoint_name}"
                applied.append(
                    f"foreach palette {raw} -> {new} "
                    f"(series colour {_contrast_ratio(rgb, surface):.2f}:1 below "
                    f"{_CONTRAST_FLOOR:g}:1 on the {theme} surface; re-tinted "
                    f"toward {endpoint_name})")
                return tok.replace(raw, new, 1)
        return tok

    def _sub(m: re.Match) -> str:
        head, inner = m.group(1), m.group(2)
        items = inner.split(",")
        nonempty = [it.strip() for it in items if it.strip()]
        # Only a pure colour list -- never a numeric/coordinate foreach list.
        if not nonempty or any(_resolve_xcolor_rgb(it, defs) is None for it in nonempty):
            return m.group(0)
        return f"{head}{{{','.join(_fix_item(it) for it in items)}}}"

    return _FOREACH_LIST_RE.sub(_sub, body)


# --------------------------------------------------------------------------
# Dark-theme pale-fill label ink (D-234).
#
# ``build_document`` bakes a LIGHT default ink (#EDEDED) for the dark page so
# free-standing text is legible.  But a node the author gave an explicit PALE
# fill (``fill=green!20`` #ccffcc, ``fill=yellow!35``, ``fill=black!10`` ...)
# and NO explicit ``text=`` draws its LABEL in that same light default ink --
# light-on-pale, which washes the label out (#EDEDED on #ccffcc = 1.04:1) while
# the fill island stays visible.  This is the exact inverse of the old
# black-ink-on-dark-page bug and, per the repair contract, is fixed by choosing
# the label ink PER FILL LUMINANCE, not per page.
#
# For each TikZ option block that carries a resolvable pale fill and no explicit
# ``text=``, inject ``text=black``.  The gate is contrast-driven: we act ONLY
# when the light default ink FAILS the graphical floor against the fill, which
# (see _CONTRAST_FLOOR arithmetic) means the fill luminance is high enough that
# black scores >=6:1 on it -- so the injected ink is always comfortably legible
# and never marginal.  A fill on which #EDEDED already meets the floor (a dark
# or mid fill) is left untouched, so the light default ink still covers it.
#
# Both-theme safety: this fires ONLY in the dark theme.  The light theme's
# default ink is already #000000, which contrasts with a pale fill (13-20:1),
# so light renders are left byte-identical and cannot regress -- exactly the
# discipline the contrast clamp above uses.  An author-set ``text=`` is always
# respected (we skip the block), and an unresolvable fill (a \definecolor name,
# a gradient) is left as authored -- advisory, never fatal.
# --------------------------------------------------------------------------

#: The light default ink build_document bakes for the dark page (#EDEDED).
_DARK_DEFAULT_INK: tuple[int, int, int] = (0xED, 0xED, 0xED)
#: A TikZ ``[...]`` option block.  Braces (a normalised ``fill={rgb,...}``
#: value) are not brackets, so they survive inside the capture; nested option
#: brackets are vanishingly rare in an option list and simply skip the match.
_OPT_BLOCK_RE = re.compile(r"\[([^\[\]]*)\]")


class _BlockMatch:
    """A minimal ``re.Match`` stand-in exposing ``group(0)``/``group(1)`` so an
    existing ``_block_sub`` closure written for ``_OPT_BLOCK_RE.sub`` can be
    reused unchanged over balanced-scanned blocks (D-492)."""
    __slots__ = ("_full", "_content")

    def __init__(self, full: str, content: str) -> None:
        self._full = full
        self._content = content

    def group(self, idx: int = 0) -> str:
        return self._full if idx == 0 else self._content


def _sub_option_blocks(body: str, block_sub) -> str:
    """``re.sub``-style replacement over balanced top-level ``[...]`` option
    blocks -- the nesting-aware replacement for ``_OPT_BLOCK_RE.sub`` (D-492).

    The non-nesting ``_OPT_BLOCK_RE`` (``\\[([^\\[\\]]*)\\]``) cannot match an
    option block whose value braces themselves contain bracket groups -- e.g. a
    TikZ node ``[..., fill=orange!25, label={[red]left:..}, pin={[..]30:..}]``
    -- so a pale ``fill=`` inside such a block was skipped and its dark-page
    label kept the washed-out light default ink (tikz-w3-05).  This scan tracks
    brace depth (a ``[`` inside ``{...}`` does NOT open a nested option level)
    and bracket depth (a genuinely nested option ``[...]`` is balanced), so the
    WHOLE block is passed to ``block_sub``.  ``block_sub`` receives a
    ``_BlockMatch`` with ``.group(0) == '[...]'`` and ``.group(1) == content``
    and returns the replacement text, exactly as with ``_OPT_BLOCK_RE.sub``.
    An unbalanced ``[`` degrades to a literal copy of that character (advisory:
    never raises), so a malformed body is left as authored."""
    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        if body[i] == "[":
            depth = 0
            brace = 0
            j = i
            end = -1
            while j < n:
                c = body[j]
                if c == "{":
                    brace += 1
                elif c == "}":
                    if brace:
                        brace -= 1
                elif c == "[" and brace == 0:
                    depth += 1
                elif c == "]" and brace == 0:
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
                j += 1
            if end == -1:
                out.append(body[i])
                i += 1
                continue
            full = body[i:end + 1]
            content = body[i + 1:end]
            out.append(block_sub(_BlockMatch(full, content)))
            i = end + 1
        else:
            out.append(body[i])
            i += 1
    return "".join(out)
#: ``fill=<value>`` inside a block; value is a name/blend or a ``{...}`` expr.
_FILL_VALUE_RE = re.compile(rf"(?<![A-Za-z])fill\s*=\s*{_COLOUR_VALUE}")
#: an explicit ``text=`` key already present in the block (author intent).
_TEXT_KEY_PRESENT_RE = re.compile(r"(?<![A-Za-z])text\s*=")
#: a ``NAME/.style={...}`` definition inside a top-level options block.  A pale
#: fill inside ONE style must inject its label ink into THAT style only -- a
#: block-level injection leaks ``text=black`` onto every node that inherits an
#: UNfilled sibling style, blacking out its label on the dark page (D-234
#: tikz-w1-06: the ``base`` style has no fill, but the shared block-level
#: injection blacked out the ``monitor`` node that uses it).  The value capture
#: is simple-brace only; a style whose value itself nests braces (an arrow tip
#: ``-{Stealth}``) is left to the block path, which is harmless for the
#: no-fill styles that shape is used on.
_STYLE_DEF_RE = re.compile(r"([A-Za-z@][\w@ ]*/\.style\s*=\s*)\{([^{}]*)\}")


def _pale_fill_label_ink(body: str, theme: str, applied: list[str]) -> str:
    """Inject dark label ink for pale author fills on the dark page (D-234)."""
    if theme != "dark":
        return body

    page = _THEME_SURFACE_RGB["dark"]
    # D-234: resolve author \definecolor fills (``fill=motifA!12``) so a pale
    # chip defined via \definecolor is MEASURED, not skipped as unresolvable --
    # without the body's definecolor map ``motifA!12`` reads as None and the
    # label keeps the washed-out light default ink (1.04:1 on #E4EAEF).
    defs = _collect_definecolors(body)

    def _fill_needs_dark_ink(fill_tok: str, opts: str = ""):
        """(fill_rgb, black_ratio) when a pale fill washes out the light ink.

        ``opts`` is the surrounding option list; its ``fill opacity``/``opacity``
        is composited over the dark page so a low-opacity fill is measured as
        the pixels it actually shows (D-470), not as the saturated colour."""
        fill_rgb = _resolve_xcolor_rgb(fill_tok, defs)
        if fill_rgb is None:
            return None                    # \definecolor name / gradient: skip
        fill_rgb = _composite(fill_rgb, _fill_alpha(opts), page)
        if _contrast_ratio(_DARK_DEFAULT_INK, fill_rgb) >= _CONTRAST_FLOOR:
            return None                    # dark/mid fill: light default ink ok
        return fill_rgb, _contrast_ratio((0, 0, 0), fill_rgb)

    def _reink_style_value(head: str, val: str) -> str:
        """Inject ``text=black`` into ONE style value when it carries a pale
        fill and no explicit ``text=`` (per-style, never block-wide)."""
        if _TEXT_KEY_PRESENT_RE.search(val):
            return head + "{" + val + "}"  # this style already sets its ink
        fm = _FILL_VALUE_RE.search(val)
        if fm is None:
            return head + "{" + val + "}"  # unfilled style: keep light ink
        need = _fill_needs_dark_ink(fm.group(1), val)
        if need is None:
            return head + "{" + val + "}"
        _, black_ratio = need
        applied.append(
            f"{head.strip()} fill={fm.group(1)} + default light ink -> added "
            f"text=black to THIS style only (pale fill washes the light "
            f"default ink out; black label ink is {black_ratio:.2f}:1)")
        return head + "{" + val + ",text=black}"

    # D-234: named styles declared with \tikzset{...} (BRACE-delimited) or in a
    # \begin{tikzpicture}[...] option list are NOT reached by the [...] block
    # scan below, so a pale-fill style (``cell/.style={fill=motifA!12}``)
    # referenced later by ``\node[cell]`` kept the washed-out light default ink
    # (tikz-w3-02: $m_i$ labels at 1.04:1 on #E4EAEF).  Re-ink every
    # ``NAME/.style={...}`` in the body FIRST, wherever it is declared, so a
    # pale-fill style is fixed at its definition; per-style (never block-wide)
    # injection is preserved, so an unfilled sibling style keeps the light ink
    # (tikz-w1-06).
    #
    # The style VALUE routinely carries nested braces AT THIS POINT -- the step-8
    # clamp has already rewritten ``draw=motifA`` to ``draw={rgb,...}`` -- so a
    # simple ``\{[^{}]*\}`` capture can no longer find the closing brace.  Scan
    # with a balanced-brace walk instead, so the fill inside a clamped style is
    # still seen.  A style whose value itself opens a sub-group (an arrow tip
    # ``-{Stealth}``) is matched correctly by the balance walk; it carries no
    # fill, so re-inking is a no-op there.
    _STYLE_HEAD_RE = re.compile(r"([A-Za-z@][\w@ ]*/\.style\s*=\s*)\{")

    def _scan_style_defs(text: str) -> str:
        out: list[str] = []
        i = 0
        while True:
            m = _STYLE_HEAD_RE.search(text, i)
            if m is None:
                out.append(text[i:])
                break
            out.append(text[i:m.start()])
            open_brace = m.end() - 1        # index of the '{'
            depth = 0
            j = open_brace
            while j < len(text):
                c = text[j]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= len(text):              # unbalanced: leave the rest as-is
                out.append(text[m.start():])
                break
            val = text[open_brace + 1:j]
            out.append(_reink_style_value(m.group(1), val))
            i = j + 1
        return "".join(out)

    body = _scan_style_defs(body)

    def _block_sub(m: re.Match) -> str:
        block = m.group(1)
        # Style-definition blocks were already re-inked per style by the
        # whole-body pass above; leave them untouched here.
        if "/.style" in block:
            return m.group(0)
        if _TEXT_KEY_PRESENT_RE.search(block):
            return m.group(0)              # author chose the label ink already
        fm = _FILL_VALUE_RE.search(block)
        if fm is None:
            return m.group(0)
        need = _fill_needs_dark_ink(fm.group(1), block)
        if need is None:
            return m.group(0)
        _, black_ratio = need
        applied.append(
            f"fill={fm.group(1)} + default light ink -> added text=black "
            f"(dark-page default ink #EDEDED is below {_CONTRAST_FLOOR:g}:1 on "
            f"this pale fill; black label ink is {black_ratio:.2f}:1)")
        return "[" + block + ",text=black]"

    return _sub_option_blocks(body, _block_sub)


# --------------------------------------------------------------------------
# Dark-theme pale-fill SHAPE ink (D-356).
#
# ``_pale_fill_label_ink`` (step 9) re-inks the LABEL TEXT of a pale-filled
# node, but a circuitikz component shape (``\node[mixer, fill=blue!20]{}``)
# carries its meaning in the shape's INTERNAL GLYPH, drawn in the node's
# ``draw`` colour -- not its (here empty) text label.  On the dark page that
# glyph inherits the baked light default ink (#EDEDED) and collapses against the
# pale fill (blue!20 -> #CCCCFF -> 1.03:1, circuitikz-w3-14).  The label-ink
# pass never touches ``draw``, so shape-internal glyphs stayed invisible.
#
# Repair (mirrors the label pass, applied to the shape stroke): a node whose
# ONLY content is its shape graphic -- an EMPTY text label -- with a pale author
# fill and no explicit ``draw=``/``color=`` gets a fill-legible dark ``draw``
# ink injected.  Gating on the empty label is the STRUCTURAL signature of a
# shape-only node, so a plain text node (non-empty label, whose ink the label
# pass already handles) never has a border added and never special-cases a
# spec.  Dark theme only and contrast-gated on the node's OWN fill, so the light
# page is byte-identical and a dark/mid fill on which #EDEDED is already legible
# is left untouched.
# --------------------------------------------------------------------------
_NODE_KW_RE = re.compile(r"\\node\b")
#: an explicit ``draw=``/``color=`` already present (author set the shape ink).
_DRAW_OR_COLOR_KEY_RE = re.compile(r"(?<![A-Za-z])(?:draw|color)\s*=")


def _pale_fill_shape_ink(body: str, theme: str, applied: list[str]) -> str:
    """Inject a dark shape ink for empty-label pale-fill nodes on dark (D-356).

    Walks each ``\\node[...]{...}`` statement, balancing braces so a normalised
    ``fill={rgb,...}`` value inside the option block is handled.  When the node
    label is empty (a shape-only node), the block carries a resolvable pale fill
    and no explicit ``draw=``/``color=``, and the baked light default ink fails
    the graphical floor on that fill, a fill-legible ``draw=black`` is appended
    to the block so the shape's internal glyphs are legible on their own fill.
    """
    if theme != "dark":
        return body
    page = _THEME_SURFACE_RGB["dark"]
    defs = _collect_definecolors(body)
    out: list[str] = []
    i = 0
    n = len(body)
    while True:
        m = _NODE_KW_RE.search(body, i)
        if m is None:
            out.append(body[i:])
            break
        out.append(body[i:m.start()])
        # The option block must follow ``\node`` (after optional whitespace).
        k = m.end()
        while k < n and body[k] in " \t":
            k += 1
        if k >= n or body[k] != "[":
            out.append(body[m.start():m.end()])
            i = m.end()
            continue
        # Balanced ``[...]`` scan (braces of a normalised fill={rgb,...} value
        # survive inside the block, so bracket depth must ignore them).
        p = k
        depth = 0
        brace = 0
        while p < n:
            c = body[p]
            if c == "{":
                brace += 1
            elif c == "}":
                brace -= 1
            elif c == "[" and brace == 0:
                depth += 1
            elif c == "]" and brace == 0:
                depth -= 1
                if depth == 0:
                    break
            p += 1
        if p >= n:                          # unbalanced: leave as authored
            out.append(body[m.start():m.end()])
            i = m.end()
            continue
        options = body[k + 1:p]
        # The node label is the next brace group before the statement ends.
        q = p + 1
        while q < n and body[q] not in "{;":
            q += 1
        if q >= n or body[q] != "{":
            out.append(body[m.start():p + 1])
            i = p + 1
            continue
        r = q
        bdepth = 0
        while r < n:
            c = body[r]
            if c == "{":
                bdepth += 1
            elif c == "}":
                bdepth -= 1
                if bdepth == 0:
                    break
            r += 1
        if r >= n:
            out.append(body[m.start():q])
            i = q
            continue
        label = body[q + 1:r]
        replaced = None
        if label.strip() == "" and not _DRAW_OR_COLOR_KEY_RE.search(options):
            fm = _FILL_VALUE_RE.search(options)
            if fm is not None:
                fill_rgb = _resolve_xcolor_rgb(fm.group(1), defs)
                if fill_rgb is not None:
                    fill_rgb = _composite(fill_rgb, _fill_alpha(options), page)
                    if _contrast_ratio(_DARK_DEFAULT_INK, fill_rgb) < _CONTRAST_FLOOR:
                        black_ratio = _contrast_ratio((0, 0, 0), fill_rgb)
                        new_opts = "[" + options + ",draw=black]"
                        replaced = (body[m.start():k] + new_opts
                                    + body[p + 1:r + 1])
                        applied.append(
                            f"fill={fm.group(1)} shape-only node + default light "
                            f"ink -> added draw=black (dark-page default ink "
                            f"#EDEDED is below {_CONTRAST_FLOOR:g}:1 on this pale "
                            f"fill; the shape's internal glyphs are black at "
                            f"{black_ratio:.2f}:1)")
        if replaced is None:
            replaced = body[m.start():r + 1]
        out.append(replaced)
        i = r + 1
    return "".join(out)


# --------------------------------------------------------------------------
# Light-theme dark-fill label ink (D-048).
#
# The exact mirror of _pale_fill_label_ink above, for the LIGHT page.
# ``build_document`` bakes a DARK default ink (#000000) for the light page.  A
# node the author gave a saturated/dark fill (``fill=blue!70`` #4d4dff,
# ``fill=blue!85``, a dark hex ...) and NO explicit ``text=`` draws its LABEL in
# that black default ink -- black-on-dark -- which fails the text floor
# (#000000 on blue!70 = 3.82:1, on blue!85 = 2.80:1) while the fill island
# stays visible.  This is the light-page counterpart of the D-234 dark-page
# wash-out, and per the repair contract is fixed the same way: choose the label
# ink PER FILL LUMINANCE, against the node's OWN fill (a local, backdrop-aware
# decision -- NOT the page-relative clamp _clamp_body_colours deliberately
# declines).
#
# For each option block with a resolvable fill and no explicit ``text=``: when
# the light default ink (black) is below the 4.5:1 TEXT floor on the fill (node
# captions are text), inject the WHITE endpoint if it contrasts the fill better
# than black.  A pale fill -- on which black already clears the floor -- is left
# untouched, so a legible black-on-pale label is never changed; a fill on which
# black already passes is a no-op.
#
# Both-theme safety: this fires ONLY in the light theme, so the dark page is
# byte-identical and the verified D-234 behaviour above cannot regress; and the
# dark pass fires ONLY in the dark theme, so the two never both act.  An
# author-set ``text=`` is respected, and an unresolvable fill (a \definecolor
# name, a gradient, a ``fill=blue!\p`` whose percentage is a TeX loop variable)
# is left as authored -- advisory, never fatal.
# --------------------------------------------------------------------------

#: The dark default ink build_document bakes for the light page (#000000).
_LIGHT_DEFAULT_INK: tuple[int, int, int] = (0x00, 0x00, 0x00)


def _light_fill_label_ink(body: str, theme: str, applied: list[str]) -> str:
    """Inject light label ink for dark author fills on the light page (D-048)."""
    if theme != "light":
        return body

    white = (255, 255, 255)

    def _block_sub(m: re.Match) -> str:
        block = m.group(1)
        if _TEXT_KEY_PRESENT_RE.search(block):
            return m.group(0)              # author chose the label ink already
        fm = _FILL_VALUE_RE.search(block)
        if fm is None:
            return m.group(0)
        # A blend whose percentage is a TeX loop variable (``fill=blue!\p`` in a
        # \foreach, circuitikz-w3-07) is captured only up to ``blue`` -- the
        # ``!\d+`` branch of _COLOUR_VALUE needs a literal digit -- leaving a
        # dangling ``!`` right after the match.  Resolving the truncated base
        # name would pick an ink for the WRONG luminance (white for the pale end
        # of a blue!10..85 sweep), so treat the whole value as unresolvable.
        if block[fm.end():fm.end() + 1] == "!":
            return m.group(0)
        fill_rgb = _resolve_xcolor_rgb(fm.group(1))
        if fill_rgb is None:
            return m.group(0)              # \definecolor / gradient / loop var
        # Composite the fill over the white page by its opacity so a low-opacity
        # fill (fill=blue, fill opacity=0.15 -> #D9D9FF) is measured as the pale
        # pixels it shows, not as saturated blue -- otherwise a white label is
        # wrongly injected onto a near-white surface (D-470).
        fill_rgb = _composite(fill_rgb, _fill_alpha(block), (255, 255, 255))
        black_ratio = _contrast_ratio(_LIGHT_DEFAULT_INK, fill_rgb)
        if black_ratio >= _TEXT_CONTRAST_FLOOR:
            return m.group(0)              # pale/mid fill: black default ink ok
        white_ratio = _contrast_ratio(white, fill_rgb)
        if white_ratio <= black_ratio:
            return m.group(0)              # white no better; nothing to gain
        applied.append(
            f"fill={fm.group(1)} + default black ink -> added text=white "
            f"(light-page default ink #000000 is {black_ratio:.2f}:1 below "
            f"{_TEXT_CONTRAST_FLOOR:g}:1 on this dark fill; white label ink is "
            f"{white_ratio:.2f}:1)")
        return "[" + block + ",text=white]"

    return _sub_option_blocks(body, _block_sub)


# --------------------------------------------------------------------------
# Mismatched EXPLICIT fill-paired label ink (D-033).
#
# The two label-ink helpers above (_pale_fill_label_ink / _light_fill_label_ink)
# only INJECT a ``text=`` when the author gave a fill but NO explicit ink, and
# the ``text=`` branch of _clamp_body_colours deliberately SKIPS a ``text=``
# that is paired with a ``fill=`` (D-331: a label chosen for its chip must not
# be over-lifted against the page).  Between those two rules an EXPLICIT but
# WRONG fill-paired ink -- ``\node[fill=Navy, text=black]`` (black on #000080 =
# 1.31:1) or ``\node[fill=DarkOrange, text=white]`` (white on #ff8c00 = 2.33:1)
# -- is caught by NEITHER: the author set an ink, so nothing injects; it is
# fill-paired, so the page clamp skips it.  The label is then illegible on its
# own chip in BOTH themes, with no contrast guard at all.
#
# This pass closes that gap.  For an option block carrying a resolvable
# ``fill=`` AND an explicit resolvable ``text=``, it composites the fill over
# the theme page (by its fill opacity, D-470) and measures the author ink
# against that chip.  Because this OVERRIDES an EXPLICIT author choice, the
# threshold is the 3:1 GRAPHICAL floor, not the 4.5:1 small-text floor: it
# intervenes only when the pairing is EGREGIOUSLY illegible (black on #000080 =
# 1.31:1, white on #ff8c00 = 2.33:1 -- both below 3:1), and defers to the author
# on a merely-borderline small-text choice (white on SteelBlue = 3.8:1, black on
# Crimson = 4.2:1) that a reader can still make out and that the author clearly
# intended.  When it does act it flips the ink to whichever monochrome endpoint
# (black / white) contrasts the chip better, and only if that endpoint is
# strictly more legible than the author's.  A pairing at/above 3:1 is left
# byte-for-byte, so the D-331 "don't over-lift" contract and the g02 base-name
# passthrough both hold; the decision is against the node's OWN fill, not the
# page, so it is theme-invariant for an opaque chip and cannot break one theme
# to fix the other.  Advisory: an unresolvable fill or ink (a gradient, a
# loop-variable blend) is left exactly as authored.
# --------------------------------------------------------------------------

#: an explicit ``text=<value>`` ink inside an option block.
_TEXT_VALUE_RE = re.compile(rf"(?<![A-Za-z])text\s*=\s*{_COLOUR_VALUE}")


def _fix_mismatched_fill_ink(body: str, theme: str, applied: list[str]) -> str:
    """Correct an explicit fill-paired label ink illegible on its own fill (D-033)."""
    page = _THEME_SURFACE_RGB.get(theme)
    if page is None:
        return body
    defs = _collect_definecolors(body)

    def _block_sub(m: re.Match) -> str:
        block = m.group(1)
        tm = _TEXT_VALUE_RE.search(block)
        fm = _FILL_VALUE_RE.search(block)
        if tm is None or fm is None:
            return m.group(0)
        ink = _resolve_xcolor_rgb(tm.group(1).strip(), defs)
        fill = _resolve_xcolor_rgb(fm.group(1).strip(), defs)
        if ink is None or fill is None:
            return m.group(0)              # gradient / loop var / unknown name
        chip = _composite(fill, _fill_alpha(block), page)
        cur = _contrast_ratio(ink, chip)
        if cur >= _CONTRAST_FLOOR:
            return m.group(0)              # not egregiously illegible -> defer
        black_r = _contrast_ratio((0, 0, 0), chip)
        white_r = _contrast_ratio((255, 255, 255), chip)
        endpoint, best_r, name = (
            ((0, 0, 0), black_r, "black") if black_r >= white_r
            else ((255, 255, 255), white_r, "white"))
        if best_r <= cur:
            return m.group(0)              # no monochrome endpoint does better
        new_val = _rgb_to_expr(endpoint)
        # tm spans are relative to ``block``; rebuild the block, then re-wrap in
        # the ``[...]`` brackets _OPT_BLOCK_RE consumed.
        new_block = block[:tm.start(1)] + "{" + new_val + "}" + block[tm.end(1):]
        applied.append(
            f"text={tm.group(1)} on fill={fm.group(1)} -> text={name} "
            f"(explicit label ink {cur:.2f}:1 below the {_CONTRAST_FLOOR:g}:1 "
            f"graphical floor on its own chip; flipped to the legible endpoint "
            f"{best_r:.2f}:1)")
        return "[" + new_block + "]"

    return _sub_option_blocks(body, _block_sub)


def _effective_theme_colours(body: str, theme: str) -> dict[str, str]:
    """Theme fg/bg for token resolution, against the surface actually painted.

    Normally the nominal theme surface, but when a SOLE dark author plate is
    detected on the LIGHT page ``build_document`` repaints the page to that
    plate and bakes the light-on-dark ink -- so a foreground theme token
    (``currentColor`` / ``var(--fg)`` / ``theme.foreground``) must resolve to
    that plate ink (#EDEDED = 11.29:1 on #16324A) and a surface token to the
    plate, NOT to the nominal white-page black which the step-8 clamp could only
    lift to a muddy mid-grey (D-358, circuitikz-w4-07: black on the #16324A
    plate = 1.59:1, the token's whole point -- "the theme's ink" -- being the
    crisp light ink the plate is designed for).  A body without such a plate,
    and the dark theme, are unchanged (byte-identical).
    """
    base = _THEME_COLOURS.get(theme, _THEME_COLOURS["light"])
    if theme == "light":
        try:
            plate = detect_dark_plate(body)
        except Exception:                  # pragma: no cover - defensive
            plate = None
        if plate is not None:
            return {"fg": _hex_to_expr("ededed"), "bg": _rgb_to_expr(plate)}
    return base


def _normalize(body: str, theme: str = "light") -> tuple[str, tuple[str, ...]]:
    applied: list[str] = []

    # 1. \definecolor{..}{HTML}{abc} -> 6-digit.
    def _def_sub(m: re.Match) -> str:
        expanded = _expand_hex(m.group(2))
        applied.append(
            f"expanded 3-digit HTML colour {{{m.group(2)}}} -> {{{expanded}}} "
            f"(the HTML model requires 6 hex digits)")
        return m.group(1) + expanded + m.group(3)

    body = _DEFINECOLOR_HTML_RE.sub(_def_sub, body)

    # active-theme ink/surface, shared by every theme-token resolution below.
    # Resolved against the surface build_document actually paints, not the
    # nominal theme surface, so a foreground token on a detected dark plate
    # becomes the crisp plate ink rather than a mid-grey clamp result (D-358).
    resolved = _effective_theme_colours(body, theme)

    # 1b. \definecolor{name}{rgb}{rgba(...)} -> \definecolor{name}{RGB}{r,g,b}
    # (tikz-w4-06).  Runs BEFORE the generic rgb() pass so the value is
    # de-parenthesised first and pass 3 cannot self-corrupt it into a
    # ``{rgb}{{rgb,255:...}}`` the rgb model rejects.
    def _def_rgbcall_sub(m: re.Match) -> str:
        chans = _rgb_call_channels(m.group(3))
        if chans is None:
            return m.group(0)
        r, g, b = chans
        applied.append(
            f"{m.group(0)} -> {m.group(1)}{{RGB}}{{{r},{g},{b}}} "
            "(rgb()/rgba() in a \\definecolor value; RGB 0..255 model, alpha dropped)")
        return f"{m.group(1)}{{RGB}}{{{r},{g},{b}}}"

    body = _DEFINECOLOR_RGBCALL_RE.sub(_def_rgbcall_sub, body)

    # 2. \color / \textcolor / \pagecolor argument (no explicit model).  A theme
    # token (var(--fg) / --ziya-text-primary / currentColor / theme-bg) resolves
    # to the active-theme ink; otherwise a bare hex, rgb()/rgba() or a lowercase
    # CSS name is converted via _convert_token.
    def _macro_sub(m: re.Match) -> str:
        macro, arg = m.group(1), m.group(2)
        stripped = arg.strip()
        if _is_theme_token(stripped):
            role = _classify_theme_token(stripped)
            expr = resolved[role]
            applied.append(
                f"{macro}{{{arg}}} -> {macro}{{{expr}}} "
                f"(theme token resolved to the {theme} {role})")
            return f"{macro}{{{expr}}}"
        repl = _convert_token(arg)
        if repl is None:
            return m.group(0)
        applied.append(f"{macro}{{{arg}}} -> {macro}{{{repl}}}")
        return f"{macro}{{{repl}}}"

    body = _COLOR_MACRO_RE.sub(_macro_sub, body)

    # 2b. \colorbox{ARG} background (chemfig-w4-06).  ``transparent`` and a theme
    # token resolve to the theme SURFACE (a transparent box reveals the page =
    # the surface); a hex/name/rgb value is converted like any colour argument.
    def _colorbox_sub(m: re.Match) -> str:
        macro, arg = m.group(1), m.group(2)
        stripped = arg.strip()
        if _is_theme_token(stripped) or re.fullmatch(
                r"transparent(?:!\d+)?", stripped, re.IGNORECASE):
            expr = resolved["bg"]
            applied.append(
                f"{macro}{{{arg}}} -> {macro}{{{expr}}} "
                f"(box background resolved to the {theme} surface)")
            return f"{macro}{{{expr}}}"
        repl = _convert_token(arg)
        if repl is None:
            return m.group(0)
        applied.append(f"{macro}{{{arg}}} -> {macro}{{{repl}}}")
        return f"{macro}{{{repl}}}"

    body = _COLORBOX_MACRO_RE.sub(_colorbox_sub, body)

    # 2c. rgb()/rgba() as a BARE positional option token -- a bare ``[...]``
    # option or chemfig's positional 5th bond-colour field
    # ``-[:120,,,,rgba(...)]`` (D-038, chemfig-w4-04).  Runs BEFORE the generic
    # rgb() pass so the parens are consumed here as an explicit
    # ``color={rgb,...}`` assignment; otherwise pass 3 would leave a bare
    # ``{rgb,255:...}`` brace that tikz/chemfig reject as an unknown key.
    def _bare_rgbcall_sub(m: re.Match) -> str:
        expr = _rgb_call_to_expr(m.group(3))
        if expr is None:
            return m.group(0)
        applied.append(
            f"{m.group(2)} -> color={{{expr}}} "
            "(bare rgb()/rgba() in a positional colour field; wrapped as an "
            "explicit color= assignment so it is not parsed as a tikz key; "
            "alpha dropped)")
        return f"{m.group(1)}color={{{expr}}}"

    body = _BARE_RGBCALL_RE.sub(_bare_rgbcall_sub, body)

    # 3. rgb()/rgba() anywhere else (option values, chemfig positional field).
    def _rgb_sub(m: re.Match) -> str:
        expr = _rgb_call_to_expr(m.group(1))
        if expr is None:
            return m.group(0)
        applied.append(f"{m.group(0)} -> {{{expr}}} (alpha dropped; xcolor has no alpha)")
        return "{" + expr + "}"

    body = _RGB_CALL_RE.sub(_rgb_sub, body)

    # 3b. hsl()/hsla() as a BARE positional option token -> color={rgb,...}
    # (mirror of the bare rgb() pass; runs before the generic hsl() pass).
    def _bare_hslcall_sub(m: re.Match) -> str:
        expr = _hsl_call_to_expr(m.group(3))
        if expr is None:
            return m.group(0)
        applied.append(
            f"{m.group(2)} -> color={{{expr}}} "
            "(bare hsl()/hsla() in a positional colour field; wrapped as an "
            "explicit color= assignment so it is not parsed as a tikz key; "
            "alpha dropped)")
        return f"{m.group(1)}color={{{expr}}}"

    body = _BARE_HSLCALL_RE.sub(_bare_hslcall_sub, body)

    # 3c. hsl()/hsla() anywhere else (option values, e.g. ``color=hsl(210,50%,40%)``
    # -- tikz-cd-w4-10).  The ``%`` is a TeX comment, so converting to an rgb
    # expression here is what keeps it from swallowing the rest of the line (D-482).
    def _hsl_sub(m: re.Match) -> str:
        expr = _hsl_call_to_expr(m.group(1))
        if expr is None:
            return m.group(0)
        applied.append(
            f"{m.group(0)} -> {{{expr}}} (hsl() converted to rgb; the '%' would "
            "otherwise comment out the line; alpha dropped)")
        return "{" + expr + "}"

    body = _HSL_CALL_RE.sub(_hsl_sub, body)

    # 4. key=#hex  ->  key={rgb,255:...}
    def _opt_hex_sub(m: re.Match) -> str:
        expr = _hex_to_expr(_expand_hex(m.group(2)))
        applied.append(f"{m.group(1)}#{m.group(2)} -> {m.group(1)}{{{expr}}}")
        return m.group(1) + "{" + expr + "}"

    body = _OPT_HEX_RE.sub(_opt_hex_sub, body)

    # 5. key=transparent recovery, KEY-AWARE (D-484).  xcolor's ``none`` is
    # valid only on ``fill=`` / ``draw=`` (meaning "no fill"/"no stroke"); on
    # ``color=`` / ``text=`` a ``none`` colour is a fatal "Undefined color" that
    # aborts the compile (tikz-cd-w4-12 ``color=transparent``).  Mapping the ink
    # keys to the SURFACE would be structurally valid but invisible -- a failure
    # -- so resolve ``color=``/``text=`` transparent to the active-theme INK so
    # the element stays visible; keep the ``none`` rewrite for the region keys.
    def _opt_transp_sub(m: re.Match) -> str:
        # Scope discipline (D-499): ``(fill|draw|color|text)=transparent`` is a
        # colour OPTION and only ever appears inside a ``[...]`` option block.
        # The regex is otherwise unanchored, so a heading node whose literal
        # LABEL TEXT reads ``{fill=transparent}`` (tikz-w4-07) had that displayed
        # string rewritten to ``fill=none`` -- corrupting content, not colour.
        # Fire only when the match sits inside a bracket option context, never
        # inside a ``{...}`` label/value brace group.
        if not _enclosing_is_bracket(body, m.start()):
            return m.group(0)
        key_eq = m.group(1)
        key = key_eq.split("=")[0].strip().lower()
        if key in ("fill", "draw"):
            applied.append(f"{m.group(0)} -> {key_eq}none "
                           f"('transparent' is not an xcolor colour; "
                           f"'none' is valid on {key}=)")
            return key_eq + "none"
        expr = resolved["fg"]
        applied.append(
            f"{m.group(0)} -> {key_eq}{{{expr}}} "
            f"('transparent'/'none' is invalid on {key}= and mapping to the "
            f"surface would be invisible; resolved to the {theme} ink)")
        return f"{key_eq}{{{expr}}}"

    body = _OPT_TRANSPARENT_RE.sub(_opt_transp_sub, body)

    # 5b. CSS opacity keyword recovery (D-484).  ``opacity=none`` /
    # ``opacity=inherit`` (and initial/unset) are CSS keywords, not pgf numbers,
    # so pgfmath aborts on them (tikz-cd-w4-12 also carries ``opacity=none`` and
    # ``opacity=inherit``).  Treat them as fully opaque (1) -- the visible,
    # no-op-alpha reading -- so the element renders.  A numeric opacity is
    # untouched, so the fill-alpha compositing above is unaffected.
    def _opt_opacity_kw_sub(m: re.Match) -> str:
        applied.append(
            f"{m.group(0)} -> {m.group(1)}1 "
            f"(CSS opacity keyword '{m.group(2)}' is not a pgf number; "
            f"treated as fully opaque)")
        return m.group(1) + "1"

    body = _OPT_OPACITY_KEYWORD_RE.sub(_opt_opacity_kw_sub, body)

    # 6. key=lowercasecssname -> key=CamelCase
    def _opt_name_sub(m: re.Match) -> str:
        canon = _CSS_NAME_MAP.get(m.group(2).lower())
        if canon is None or m.group(2) == canon:
            return m.group(0)
        applied.append(f"{m.group(1)}{m.group(2)} -> {m.group(1)}{canon} "
                       "(xcolor svgnames are CamelCase)")
        return m.group(1) + canon

    body = _OPT_NAME_RE.sub(_opt_name_sub, body)

    # 6b. bare CSS colour name as a standalone [...] option, no key= (D-004):
    # circuitikz-w4-06 ``\node[darkslategray]`` and chemfig-w4-05's positional
    # 5th bond-colour field ``-[:30,,,,navy]``.  Gated on _CSS_NAME_MAP so only
    # a genuine svgnames colour word is remapped; base names and TikZ keywords
    # (``white``, ``thick``, ``dashed``) fall through untouched.  Idempotent:
    # a value already CamelCase equals its canonical form and is skipped.
    def _opt_bare_name_sub(m: re.Match) -> str:
        pre, name = m.group(1), m.group(2)
        canon = _CSS_NAME_MAP.get(name.lower())
        if canon is None or name == canon:
            return m.group(0)
        applied.append(
            f"{name} -> {canon} "
            "(bare CSS colour name option; xcolor svgnames are CamelCase)")
        return pre + canon

    body = _OPT_BARE_NAME_RE.sub(_opt_bare_name_sub, body)

    # 7. key=<theme token>  ->  key={theme fg/bg expression}, resolved from the
    # active theme so light stays correct while dark is fixed (see the note on
    # _OPT_THEME_TOKEN_RE).  Runs after pass 6: ``currentColor`` also matches
    # the bare-name pattern there, but is not an xcolor name so pass 6 leaves it
    # untouched for this pass to resolve.
    resolved = _effective_theme_colours(body, theme)

    def _opt_theme_sub(m: re.Match) -> str:
        role = _classify_theme_token(m.group(2))
        expr = resolved[role]
        applied.append(
            f"{m.group(1)}{m.group(2)} -> {m.group(1)}{{{expr}}} "
            f"(theme token resolved to the {theme} {role})")
        return m.group(1) + "{" + expr + "}"

    body = _OPT_THEME_TOKEN_RE.sub(_opt_theme_sub, body)

    # 8. Theme contrast clamp (D-003).  Runs LAST, after every syntax rewrite,
    # so it sees resolved names and ``{rgb,...}`` expressions.  Only stroke/ink
    # colours below the graphical floor against the baked surface are lifted;
    # a colour already legible in the active theme is left exactly as authored,
    # which is why the light and dark renders never regress each other.
    body = _clamp_body_colours(body, theme, applied)

    # 8b. Categorical \foreach palette clamp (D-238).  The step-8 clamp
    # deliberately leaves ``\foreach ... in {red,blue,...}`` value lists alone
    # (rewriting a token there would inject a comma and abort the loop).  This
    # pass re-tints only the below-floor items of an all-colour foreach list
    # toward the surface-opposite endpoint using a comma-free NAME!p!black /
    # NAME!p!white blend, so the list structure is preserved and a categorical
    # palette stays legible on the surface the renderer was given.  Runs in both
    # themes; a palette already legible on the active surface is byte-identical.
    body = _clamp_foreach_palette(body, theme, applied)

    # 8c. On-plate default ink (D-358).  The step-8 clamp lifts author colour
    # tokens against a detected plate, but an UNCOLOURED element still draws in
    # the page-relative baked ink, which can be illegible on the plate
    # (circuitikz-w4-05: black light-page ink on the #16324A plate = 1.59:1).
    # Inject a plate-legible default \color right after a sole author plate fill
    # so uncoloured ink is chosen for the plate.  No-op when the baked ink is
    # already legible (dark #EDEDED on the plate), so the dark render is
    # byte-identical.
    body = _plate_default_ink(body, theme, applied)

    # 9. Dark-theme pale-fill label ink (D-234).  Runs after the clamp so it
    # sees fills in resolved form (``fill={rgb,...}`` / ``fill=green!20``).
    # Fires only in the dark theme, and only where the baked light default ink
    # would wash the label out on a pale author fill -- injecting a black label
    # ink chosen per fill luminance.  Light theme is a no-op (byte-identical).
    body = _pale_fill_label_ink(body, theme, applied)

    # 9b. Dark-theme pale-fill SHAPE ink (D-356).  Step 9 re-inks a pale-filled
    # node's LABEL text; a circuitikz component shape carries its meaning in an
    # internal glyph drawn in the node's ``draw`` colour, so an empty-label
    # shape node on a pale fill needs a fill-legible ``draw`` ink too.  Runs
    # after the clamp/label passes (fills are resolved) and dark-only, so the
    # light page is byte-identical.
    body = _pale_fill_shape_ink(body, theme, applied)

    # 10. Light-theme dark-fill label ink (D-048).  The mirror of step 9 for the
    # light page: where the baked black default ink would be swallowed by a
    # SATURATED/dark author fill (``fill=blue!70`` -> black is 3.82:1, below the
    # 4.5 text floor), inject a white label ink chosen per fill luminance.
    # Fires only in the light theme, so the dark page (and step 9's verified
    # behaviour) is byte-identical.
    body = _light_fill_label_ink(body, theme, applied)

    # 11. Mismatched explicit fill-paired label ink (D-033).  Steps 9/10 only
    # inject an ink where the author gave NONE, and the step-8 text= clamp skips
    # a fill-paired text= (D-331).  An EXPLICIT but wrong fill-paired ink
    # (text=black on fill=Navy = 1.31:1, text=white on fill=DarkOrange = 2.33:1)
    # is therefore caught by nothing.  This pass flips such an ink to the
    # legible monochrome endpoint measured against the node's OWN chip -- both
    # themes, only when illegible on the chip, so a correct pairing is untouched.
    body = _fix_mismatched_fill_ink(body, theme, applied)

    return body, tuple(applied)


def normalize_colors(body: str, theme: str = "light") -> tuple[str, tuple[str, ...]]:
    """Rewrite web/CSS colour forms into xcolor-valid ones.

    ``theme`` resolves theme-reactive colour tokens (``currentColor``,
    ``var(--fg)``, ``theme-bg``, ...) to the active theme's ink/surface; every
    other rewrite is theme-independent.  Returns ``(new_body, applied)``.
    Advisory: any internal fault degrades to ``(body, ())`` so a normaliser
    defect can never break an otherwise-working render.
    """
    try:
        return _normalize(body, theme)
    except Exception:                      # pragma: no cover - defensive
        logger.exception("latex colour normalisation failed; body unchanged")
        return body, ()
