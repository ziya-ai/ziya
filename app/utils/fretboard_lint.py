r"""Normalisation pass for the ``fretboard`` LaTeX profile (chord diagrams).

The profile's ``\chord`` macro (see ``latex_profiles.PROFILES["fretboard"]``)
wants a comma-separated position list -- ``\chord{Am}{x,0,2,2,1,0}`` -- because
that is what pgffor's ``\foreach`` iterates.  Nobody writes chord shapes that
way: the notation every chord chart, tab site and guitarist uses is the
compact string ``x02210`` (one character per string, low to high).  This module
accepts what people actually write and rewrites it to what the macro needs, so
the macro itself stays a small, predictable piece of TikZ.

Three rewrites, each a no-op when the body already conforms:

1. **Shorthand lines -> ``\chord``.**  A body with no ``\chord`` at all whose
   lines read ``Am x02210`` / ``C: x,3,2,0,1,0`` / ``F 133211 barre=1
   fingers=134211`` is turned into one ``\chord`` call per line.  Only applied
   when NO line carries a backslash command, so real LaTeX is never mangled.
2. **Compact position strings -> comma lists.**  ``x02210`` and ``x 0 2 2 1 0``
   both become ``x,0,2,2,1,0``.  ``X``/``-`` are muted (``x``), ``o``/``O`` are
   open (``0``).  A compact string cannot express fret 10+, so a two-digit fret
   must already be comma-separated; such bodies are left alone.
3. **Accidentals in the chord name.**  A bare ``#`` is a TeX parameter
   character and aborts the compile ("You can't use macro parameter character
   # in horizontal mode"), so ``F#``, ``F\#``, ``F♯`` all become ``F\sharp``
   and ``B♭`` becomes ``B\flat``; the macro typesets both glyphs in text mode.

Every function returns ``(new_body, applied)`` where ``applied`` names the
rewrites performed, matching the other ``*_lint`` modules, and none raises:
the caller renders the body unchanged on any fault.
"""
from __future__ import annotations

import re

__all__ = ["normalize_fretboard", "normalize_positions", "normalize_chord_name"]

#: Characters permitted in a compact one-char-per-string position string.
_COMPACT_CHARS = set("xXoO0123456789-")

#: A shorthand chord line: name, separator, positions, optional key=value
#: options.  The name must start with a note letter so prose never matches.
_SHORTHAND_LINE_RE = re.compile(
    r"^\s*(?P<name>[A-Ga-g][^\s:=]*)\s*[:=]?\s+"
    r"(?P<pos>[xXoO0-9,\-]+(?:[ \t]+[xXoO0-9\-]+)*)"
    r"(?P<opts>(?:\s+(?:fret|barre|fingers|frets|scale)=\S+)*)\s*$"
)

#: Options whose value is itself a position-like string.
_LIST_OPTS = ("fingers",)


def normalize_positions(raw: str) -> str | None:
    """Return ``raw`` as a comma-separated position list, or None if it is not
    a position list this module understands (left for TeX to judge)."""
    s = raw.strip()
    if not s:
        return None
    if "," in s:
        parts = [p.strip() for p in s.split(",")]
    elif re.search(r"\s", s):
        parts = s.split()
    elif all(ch in _COMPACT_CHARS for ch in s):
        parts = list(s)
    else:
        return None
    out: list[str] = []
    for p in parts:
        if p in ("x", "X", "-"):
            out.append("x")
        elif p in ("o", "O"):
            out.append("0")
        elif p.isdigit():
            out.append(str(int(p)))
        else:
            return None
    return ",".join(out) if len(out) >= 2 else None


def normalize_fingers(raw: str) -> str | None:
    r"""Fingering list: digits only, anything non-numeric (x, -, T) -> 0.

    The macro tests ``\ifnum\f>0`` per entry, so every entry must be a
    number; a letter there would abort the compile.
    """
    s = raw.strip()
    if not s:
        return None
    if "," in s:
        parts = [p.strip() for p in s.split(",")]
    elif re.search(r"\s", s):
        parts = s.split()
    else:
        parts = list(s)
    return ",".join(p if p.isdigit() else "0" for p in parts)


def normalize_chord_name(name: str) -> str:
    """Make a chord name safe for the text-mode name node."""
    # Braced so a following letter ('F#m') is not swallowed into the control
    # word: 'F\sharpm' is an undefined command, 'F{\sharp}m' is F-sharp minor.
    out = name.replace("♯", r"{\sharp}").replace("♭", r"{\flat}")
    out = out.replace(r"\#", r"{\sharp}")
    # A bare '#' is a macro-parameter character in text and aborts the compile;
    # the replace above already covered the escaped form.
    out = re.sub(r"(?<!\\)#", r"{\\sharp}", out)
    return out


def _read_group(text: str, i: int) -> tuple[str, int] | None:
    """Read a balanced ``{...}`` group starting at ``text[i] == '{'``.

    Returns (inner, index_after_closing_brace), or None when unbalanced.
    """
    if i >= len(text) or text[i] != "{":
        return None
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return None


def _read_optional(text: str, i: int) -> tuple[str, int] | None:
    """Read a ``[...]`` option group at ``text[i]``, honouring nested braces."""
    if i >= len(text) or text[i] != "[":
        return None
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "]" and depth == 0:
            return text[i + 1:j], j + 1
        j += 1
    return None


def _normalize_opts(opts: str, applied: list[str]) -> str:
    """Rewrite list-valued options (``fingers=002310``) to comma lists."""
    def _repl(m: "re.Match[str]") -> str:
        key, val = m.group(1), m.group(2)
        inner = val[1:-1] if val.startswith("{") and val.endswith("}") else val
        fixed = normalize_fingers(inner)
        if fixed is None or fixed == inner:
            return m.group(0)
        applied.append(f"fretboard: {key}={inner!r} -> {{{fixed}}}")
        return f"{key}={{{fixed}}}"
    pattern = r"\b(" + "|".join(_LIST_OPTS) + r")\s*=\s*(\{[^{}]*\}|[^,\]\s]+)"
    return re.sub(pattern, _repl, opts)


def _normalize_chord_calls(body: str, applied: list[str]) -> str:
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        k = body.find(r"\chord", i)
        # Match the control word exactly (not \chordfoo).
        while k != -1 and k + 6 < n and body[k + 6].isalpha():
            k = body.find(r"\chord", k + 6)
        if k == -1:
            out.append(body[i:])
            break
        out.append(body[i:k])
        j = k + 6
        # skip whitespace between the macro and its arguments
        while j < n and body[j] in " \t":
            j += 1
        opts_txt = ""
        opt = _read_optional(body, j)
        if opt is not None:
            opts_txt, j = opt
            opts_txt = "[" + _normalize_opts(opts_txt, applied) + "]"
            while j < n and body[j] in " \t":
                j += 1
        name_grp = _read_group(body, j)
        if name_grp is None:
            out.append(body[k:j])
            i = j
            continue
        name, j = name_grp
        while j < n and body[j] in " \t":
            j += 1
        pos_grp = _read_group(body, j)
        if pos_grp is None:
            out.append(body[k:j])
            i = j
            continue
        pos, j = pos_grp
        new_name = normalize_chord_name(name)
        if new_name != name:
            applied.append(f"fretboard: chord name {name!r} -> {new_name!r}")
        fixed_pos = normalize_positions(pos)
        if fixed_pos is not None and fixed_pos != pos.strip():
            applied.append(f"fretboard: positions {pos!r} -> {fixed_pos!r}")
            pos = fixed_pos
        out.append(f"\\chord{opts_txt}{{{new_name}}}{{{pos}}}")
        i = j
    return "".join(out)


def _expand_shorthand(body: str, applied: list[str]) -> str:
    r"""Turn ``Am x02210`` lines into ``\chord`` calls (rule 1)."""
    if "\\" in body:
        return body            # real LaTeX present: leave it alone
    lines = body.splitlines()
    converted: list[str] = []
    hits = 0
    for line in lines:
        m = _SHORTHAND_LINE_RE.match(line)
        if not m:
            converted.append(line)
            continue
        pos = normalize_positions(m.group("pos"))
        if pos is None:
            converted.append(line)
            continue
        name = normalize_chord_name(m.group("name"))
        opts = []
        for tok in (m.group("opts") or "").split():
            key, _, val = tok.partition("=")
            if key in _LIST_OPTS:
                val = normalize_fingers(val) or val
                opts.append(f"{key}={{{val}}}")
            else:
                opts.append(f"{key}={val}")
        opt_txt = f"[{','.join(opts)}]" if opts else ""
        converted.append(f"\\chord{opt_txt}{{{name}}}{{{pos}}}")
        hits += 1
    if hits == 0:
        return body
    applied.append(f"fretboard: expanded {hits} shorthand chord line(s) to \\chord")
    return "\n".join(converted)


def normalize_fretboard(body: str) -> tuple[str, tuple[str, ...]]:
    """Apply every fretboard rewrite.  Never raises."""
    applied: list[str] = []
    try:
        body = _expand_shorthand(body, applied)
        body = _normalize_chord_calls(body, applied)
        return body, tuple(applied)
    except Exception:                      # pragma: no cover - defensive
        return body, ()
