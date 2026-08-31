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


def _rgb_call_to_expr(inner: str) -> str | None:
    """``'220,20,60,0.8'`` -> ``'rgb,255:red,220;green,20;blue,60'`` (alpha dropped).

    Returns None when the three channels cannot be read as integers, so a
    malformed call is left untouched rather than mangled.
    """
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) < 3:
        return None
    try:
        chans = [max(0, min(255, int(round(float(p))))) for p in parts[:3]]
    except ValueError:
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
    return None


# --- individual passes -----------------------------------------------------

_DEFINECOLOR_HTML_RE = re.compile(
    r"(\\definecolor\s*\{[^{}]*\}\s*\{HTML\}\s*\{)([0-9A-Fa-f]{3})(\})")

# \color{ARG} / \textcolor{ARG}{...} / \pagecolor{ARG} with NO explicit [model].
_COLOR_MACRO_RE = re.compile(
    r"(\\(?:color|textcolor|pagecolor))(?!\s*\[)\s*\{([^{}]*)\}")

# rgb()/rgba() anywhere (unambiguous -- never legitimate prose).  Braced so the
# internal commas/semicolons survive a pgfkeys option list and a chemfig field.
_RGB_CALL_RE = re.compile(r"rgba?\(([^)]*)\)", re.IGNORECASE)

# key=value colour option: fill= / draw= / color= / text=  followed by a
# convertible value token.
_KEY = "|".join(_COLOUR_KEYS)
_OPT_HEX_RE = re.compile(
    rf"(?<![A-Za-z])((?:{_KEY})\s*=\s*)#([0-9A-Fa-f]{{3}}|[0-9A-Fa-f]{{6}})")
_OPT_TRANSPARENT_RE = re.compile(
    rf"(?<![A-Za-z])((?:{_KEY})\s*=\s*)transparent(?:!\d+)?(?![A-Za-z])")
_OPT_NAME_RE = re.compile(
    rf"(?<![A-Za-z])((?:{_KEY})\s*=\s*)([A-Za-z]+)(?=[\s,\]}}])")

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

    # 2. \color / \textcolor / \pagecolor argument (no explicit model).
    def _macro_sub(m: re.Match) -> str:
        macro, arg = m.group(1), m.group(2)
        repl = _convert_token(arg)
        if repl is None:
            return m.group(0)
        applied.append(f"{macro}{{{arg}}} -> {macro}{{{repl}}}")
        return f"{macro}{{{repl}}}"

    body = _COLOR_MACRO_RE.sub(_macro_sub, body)

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
