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
    r"|currentcolor|theme[-.][A-Za-z.-]+",
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
    r"(var\(--[A-Za-z0-9_-]+\)|currentcolor|theme[-.][A-Za-z-]+)",
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

#: A node LABEL carrier inside a single statement: a ``node`` (``\node`` or a
#: path-attached ``node``) that terminates in a brace group with visible text.
#: When a coloured option belongs to such a statement the colour also paints
#: the label text, so it must clear the 4.5:1 TEXT floor, not the 3:1 graphical
#: floor (D-233: ``\draw[...,blue!60!black] ... node{thick blue}`` and
#: ``\node[blue]{$\sin x$}`` lifted only to 3.12/3.15:1 leave the LABEL under
#: the text floor while the stroke passes).
_NODE_LABEL_RE = re.compile(r"\bnode\b[^;]*?\{[^{}]*?\S[^{}]*?\}")


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
    return _NODE_LABEL_RE.search(text[start:end]) is not None


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
    m = _CELL_FILL_RE.search(body)
    if m:
        rgb = _resolve_xcolor_rgb(m.group(1).strip(), defs)
        if rgb is not None:
            return rgb
    rects = _FILL_RECT_RE.findall(body)
    if len(rects) == 1:
        col = _fill_opt_colour(rects[0])
        if col:
            rgb = _resolve_xcolor_rgb(col.strip(), defs)
            if rgb is not None:
                return rgb
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
        if len(rects) != 1:
            return None
        col = _fill_opt_colour(rects[0])
        if not col:
            return None
        rgb = _resolve_xcolor_rgb(col.strip(), defs)
        if rgb is None:
            return None
        if _rel_luminance(rgb) >= 0.18:    # a light/mid plate is not this case
            return None
        return rgb
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


_FILL_CMD_BEFORE_RE = re.compile(r"\\(fill|shade|pagecolor)\s*$")


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
        expr = _clamped_expr(arg, _TEXT_CONTRAST_FLOOR)
        if expr is None:
            return m.group(0)
        ratio = _contrast_ratio(_resolve_xcolor_rgb(arg, defs), surface)  # type: ignore[arg-type]
        where = "backdrop" if backdrop else f"{theme} surface"
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

    # The stroke/fill-option clamps run on the dark page, or on either page when
    # a resolvable author backdrop was detected.
    if not (dark_theme or backdrop):
        return body

    def _opt_sub(m: re.Match) -> str:
        key, value = m.group(1), m.group(2)
        # A colour on a statement that also carries a node label paints the
        # label glyphs too, so it must clear the 4.5:1 text floor (D-233).
        texty = _statement_has_label(body, m.start(2))
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
        # When a DISTINCT author backdrop is detected, leave its ``\fill`` /
        # ``\shade`` bare colour alone in BOTH themes: it is a region (not ink)
        # and is the reference surface itself, so recolouring it would repaint
        # the background AND desync it from the surface the inks are measured
        # against (D-051: the #16324A plate must stay put while its labels are
        # lifted to clear it).  A backdrop that equals the page (circuitikz-w1-15
        # plate black!88 == #1F1F1F) sets backdrop=False, so that spec's
        # established plate lift is untouched.
        if backdrop and _bare_option_is_fill(body, m.start(2)):
            return m.group(0)
        pre, value = m.group(1), m.group(2)
        texty = _statement_has_label(body, m.start(2))
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

    def _fill_needs_dark_ink(fill_tok: str):
        """(fill_rgb, black_ratio) when a pale fill washes out the light ink."""
        fill_rgb = _resolve_xcolor_rgb(fill_tok)
        if fill_rgb is None:
            return None                    # \definecolor name / gradient: skip
        if _contrast_ratio(_DARK_DEFAULT_INK, fill_rgb) >= _CONTRAST_FLOOR:
            return None                    # dark/mid fill: light default ink ok
        return fill_rgb, _contrast_ratio((0, 0, 0), fill_rgb)

    def _style_sub(sm: re.Match) -> str:
        head, val = sm.group(1), sm.group(2)
        if _TEXT_KEY_PRESENT_RE.search(val):
            return sm.group(0)             # this style already sets its ink
        fm = _FILL_VALUE_RE.search(val)
        if fm is None:
            return sm.group(0)             # unfilled style: keep light ink
        need = _fill_needs_dark_ink(fm.group(1))
        if need is None:
            return sm.group(0)
        _, black_ratio = need
        applied.append(
            f"{head.strip()} fill={fm.group(1)} + default light ink -> added "
            f"text=black to THIS style only (pale fill washes the light "
            f"default ink out; black label ink is {black_ratio:.2f}:1)")
        return head + "{" + val + ",text=black}"

    def _block_sub(m: re.Match) -> str:
        block = m.group(1)
        # A style-DEFINITION block must inject per style, never block-wide: a
        # block-wide text=black leaks onto nodes that inherit an unfilled
        # sibling style (D-234 tikz-w1-06).
        if "/.style" in block:
            return "[" + _STYLE_DEF_RE.sub(_style_sub, block) + "]"
        if _TEXT_KEY_PRESENT_RE.search(block):
            return m.group(0)              # author chose the label ink already
        fm = _FILL_VALUE_RE.search(block)
        if fm is None:
            return m.group(0)
        need = _fill_needs_dark_ink(fm.group(1))
        if need is None:
            return m.group(0)
        _, black_ratio = need
        applied.append(
            f"fill={fm.group(1)} + default light ink -> added text=black "
            f"(dark-page default ink #EDEDED is below {_CONTRAST_FLOOR:g}:1 on "
            f"this pale fill; black label ink is {black_ratio:.2f}:1)")
        return "[" + block + ",text=black]"

    return _OPT_BLOCK_RE.sub(_block_sub, body)


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

    return _OPT_BLOCK_RE.sub(_block_sub, body)


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
    resolved = _THEME_COLOURS.get(theme, _THEME_COLOURS["light"])

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

    # 4. key=#hex  ->  key={rgb,255:...}
    def _opt_hex_sub(m: re.Match) -> str:
        expr = _hex_to_expr(_expand_hex(m.group(2)))
        applied.append(f"{m.group(1)}#{m.group(2)} -> {m.group(1)}{{{expr}}}")
        return m.group(1) + "{" + expr + "}"

    body = _OPT_HEX_RE.sub(_opt_hex_sub, body)

    # 5. fill=/draw=/text= transparent -> none (there is no 'transparent' colour).
    def _opt_transp_sub(m: re.Match) -> str:
        applied.append(f"{m.group(0)} -> {m.group(1)}none "
                       "('transparent' is not an xcolor colour)")
        return m.group(1) + "none"

    body = _OPT_TRANSPARENT_RE.sub(_opt_transp_sub, body)

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
    resolved = _THEME_COLOURS.get(theme, _THEME_COLOURS["light"])

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

    # 10. Light-theme dark-fill label ink (D-048).  The mirror of step 9 for the
    # light page: where the baked black default ink would be swallowed by a
    # SATURATED/dark author fill (``fill=blue!70`` -> black is 3.82:1, below the
    # 4.5 text floor), inject a white label ink chosen per fill luminance.
    # Fires only in the light theme, so the dark page (and step 9's verified
    # behaviour) is byte-identical.
    body = _light_fill_label_ink(body, theme, applied)

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
