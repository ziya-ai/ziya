"""
Structural lint for CircuiTikZ / TikZ option lists.

Why this exists: a model authoring a circuit naturally writes a label whose
value contains a bare ``=``, comma, or bracket -- ``to[R, l=$R_C=\\SI{2.2}{\\kilo
\\ohm}$]`` -- because that is exactly how the value reads on paper.  But an
option list is parsed by *pgfkeys* before TeX ever sees the math, and pgfkeys
splits ``key=value`` on ``=``, splits the list on ``,``, and ends the list at
the first ``]``.  Any of those characters, unbraced, inside a value therefore
tears the value apart: circuitikz's label machinery receives a fragment with an
unbalanced ``$``/``{`` and aborts with ``! Extra }, or forgotten $.`` (or the
list ends early and a downstream ``! Package tikz Error: (, +, coordinate, ...
expected.`` fires) -- a fatal compile error, no image at all, naming a cause
the author never wrote.

The rule, established empirically against a real circuitikz install:

    to[R, l=$R_C=\\SI{2.2}{\\kilo\\ohm}$]        FAILS  (Extra }, or forgotten $)
    to[R, l={$R_C=\\SI{2.2}{\\kilo\\ohm}$}]      COMPILES
    to[R, a=$k\\in(0,1]$]                        FAILS  (the ']' ends to[ early)
    to[R, a={$k\\in(0,1]$}]                      COMPILES

Bracing the value hides its ``=``/``,``/``]`` from pgfkeys, which strips
exactly one brace level and hands the whole token list to the key's handler as
the label text -- which is what the author meant.  So the fix is: find any
top-level ``key=value`` segment inside a bracket option list whose *value*
carries a pgfkeys-hostile character (``=``, ``,`` or ``]``) at brace-depth
zero, and wrap that value in braces.

TWO NOTIONS OF STRUCTURE, deliberately different
------------------------------------------------
This is the subtle part.  The scanners that RECOVER the option list and split
it into segments must respect the author's intent, so they treat a ``$...$``
math span as opaque: a ``]`` or ``,`` inside ``$...$`` does NOT end the list or
split a segment, because the author plainly meant the whole math as one value.
But the HOSTILITY test on the recovered value is ``$``-blind -- because pgfkeys
itself is ``$``-blind, and that blindness is the entire bug.  A ``,`` inside
``$...$`` is invisible to the author's eye yet fatal to pgfkeys, so it must be
counted as hostile and the value braced.

Scope is deliberately narrow, because an over-eager rewrite of a TikZ option
list would corrupt working diagrams -- far worse than the bug being fixed:

  * Only ``[...]`` option-list brackets are scanned; ``{...}`` groups
    (``\\ctikzset{...}``) are not, and their contents are never rewritten.
  * A value is rewritten ONLY when it carries a hostile character at
    brace-depth zero (the exact pgfkeys pathology).  A value already fully
    braced has its inner hostile characters at depth >= 1, so it is not
    flagged -- which also makes the pass idempotent.
  * A hostile character reachable only inside ``$...$`` still counts (pgfkeys
    does not respect ``$``); only ``{}`` bracing hides it from pgfkeys.

Advisory only, following ``chemfig_lint``'s contract exactly: a pure
``autofix(body) -> (body, fixes, warnings)`` that must degrade to "compile the
body unchanged" on any internal fault and must never raise.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

#: Characters that pgfkeys treats as structural inside an option list and that
#: therefore tear a value apart when they appear in it unbraced: ``=`` is the
#: key/value separator, ``,`` is the list separator, ``]`` ends the list.
_HOSTILE_VALUE_CHARS = frozenset("=,]")

#: Quote characters that a model leaks around numeric/dimension values out of
#: JSON habit (``right="2cm"``, ``scale="0.9"``, coordinates ``("0","0")``).
#: Only DOUBLE quotes are handled -- straight ``"`` and the typographic pair
#: ``\u201c``/``\u201d`` -- mapping each opener to its closer.  The straight
#: single quote ``'`` is deliberately EXCLUDED: it is heavily overloaded in
#: TikZ (coordinate primes ``(A')``, ``to`` reversed paths), so pairing two
#: unrelated apostrophes would corrupt a valid body -- far worse than the bug.
_NUMERIC_QUOTE_OPENERS = {'"': '"', "\u201c": "\u201d"}

#: A quoted value is only unquoted when its ENTIRE content is a bare number or
#: a number followed by a TeX length/relative unit.  This is what makes the
#: rewrite safe: a quoted label carrying letters (``l="5V"``) or symbols never
#: matches, so it is left byte-for-byte alone.  pt/mm/cm/in/... are TeX lengths;
#: em/ex/mu are font-relative; a trailing unit is optional (``scale="0.9"``).
_NUMERIC_DIM_RE = re.compile(
    r"^\s*[+-]?\d+(?:\.\d+)?\s*"
    r"(?:pt|mm|cm|in|ex|em|bp|pc|dd|cc|sp|mu)?\s*$"
)


def _strip_numeric_quotes(body: str) -> tuple[str, list[str]]:
    r"""Remove quotes a model wrapped around numeric option / coordinate values.

    A model authoring from JSON habit emits ``right="2cm"``, ``scale="0.9"`` or
    quoted coordinates ``at ("0","0")``.  pgfkeys/pgfmath then receives the
    literal string ``"2cm"`` where a dimension is expected: at best the value
    is mis-parsed and the component lands at an extreme position (aspect ratio
    destroyed, ink pushed off the plate), at worst it is a fatal ``Dimension
    too large`` -- both from a body whose intent is obvious.  Dropping the
    quotes recovers the number.

    Scope is deliberately narrow so a valid body is never altered:

      * only a matched DOUBLE-quote pair is considered (see
        ``_NUMERIC_QUOTE_OPENERS``); the single quote is left alone entirely;
      * the pair is stripped ONLY when its whole content is a number or a
        number + TeX unit (``_NUMERIC_DIM_RE``), so a quoted label with any
        letters/symbols (``l="5V"``, ``"$R$"``) is untouched;
      * quotes inside a ``{...}`` group (node/label text, where the quote
        glyphs are meant literally) and inside ``$...$`` math are skipped --
        brace depth and math mode are tracked exactly as the brace-wrap pass
        does.

    Pure and best-effort; returns the (possibly rewritten) body and the list of
    applied-fix notes.
    """
    out: list[str] = []
    fixes: list[str] = []
    brace = 0
    math = False
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":                 # escaped char -- copy the pair verbatim
            out.append(body[i:i + 2])
            i += 2
            continue
        if ch == "$":
            math = not math
            out.append(ch)
            i += 1
            continue
        if ch == "{":
            brace += 1
            out.append(ch)
            i += 1
            continue
        if ch == "}":
            if brace > 0:
                brace -= 1
            out.append(ch)
            i += 1
            continue
        if brace == 0 and not math and ch in _NUMERIC_QUOTE_OPENERS:
            closer = _NUMERIC_QUOTE_OPENERS[ch]
            j = body.find(closer, i + 1)
            if j != -1:
                inner = body[i + 1:j]
                if "\n" not in inner and _NUMERIC_DIM_RE.match(inner):
                    trimmed = inner.strip()
                    out.append(trimmed)
                    fixes.append(
                        f"stripped quotes around numeric value "
                        f"{ch + inner + closer!r} -> {trimmed!r} (a quoted "
                        f"dimension is not a valid pgfkeys/pgfmath value)."
                    )
                    i = j + 1
                    continue
        out.append(ch)
        i += 1
    return "".join(out), fixes


def _match_bracket(body: str, open_idx: int) -> Optional[int]:
    r"""Index of the ``]`` matching the ``[`` at ``open_idx``, or None.

    Nesting-aware for ``[``, brace-aware, and ``$``-math-aware:

      * a ``]`` inside a ``{...}`` group (an arrow-tip spec such as
        ``-{Latex[length=2mm]}``) does not close the option list, and a ``[``
        inside braces does not open a nested one;
      * a ``]`` inside a ``$...$`` math span (interval notation such as
        ``a=$k\in(0,1]$``) does not close the option list either -- the author
        meant the whole math as the value.  This is what pgfkeys FAILS to do,
        so recovering the true end here is precisely what lets the value be
        braced before pgfkeys ever sees the stray ``]``.

    An unbalanced ``$`` (no closing delimiter before the buffer ends) means the
    real list end can no longer be located reliably; the scan then runs off the
    end and returns None, so a malformed body is left untouched rather than
    guessed at.
    """
    depth = 0
    brace = 0
    math = False
    i = open_idx
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":                 # skip an escaped char (\{, \}, \[, \], \$)
            i += 2
            continue
        if ch == "$":
            math = not math
            i += 1
            continue
        if math:                       # inside math, nothing is structural
            i += 1
            continue
        if ch == "{":
            brace += 1
        elif ch == "}":
            if brace > 0:
                brace -= 1
        elif brace == 0:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return None


def _split_top_level_commas(inner: str) -> list[tuple[int, int]]:
    r"""Spans of comma-separated segments at brace/bracket/math depth zero.

    Returns (start, end) offsets INTO ``inner`` for each segment, excluding the
    separating commas.  A comma inside ``{}``, ``[]`` or ``$...$`` is not a
    separator -- the ``$`` case keeps a math value such as
    ``a=$k=\frac{R_2}{R_1+R_2},\ k\in(0,1]$`` whole instead of tearing it at
    its internal comma, so the whole span can then be braced.
    """
    spans: list[tuple[int, int]] = []
    brace = 0
    bracket = 0
    math = False
    start = 0
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "$":
            math = not math
            i += 1
            continue
        if math:
            i += 1
            continue
        if ch == "{":
            brace += 1
        elif ch == "}":
            if brace > 0:
                brace -= 1
        elif ch == "[":
            bracket += 1
        elif ch == "]":
            if bracket > 0:
                bracket -= 1
        elif ch == "," and brace == 0 and bracket == 0:
            spans.append((start, i))
            start = i + 1
        i += 1
    spans.append((start, n))
    return spans


def _first_top_level_eq(segment: str) -> int:
    r"""Index of the first ``=`` at brace/math depth zero in ``segment``, or -1.

    This is the key/value separator pgfkeys uses.  ``=`` inside ``{}`` is a
    protected value character; ``=`` inside ``$...$`` is part of a math value
    (``a=$k=...$`` -- the key separator is the first ``=``, which precedes the
    ``$``), so math is skipped here too to avoid mistaking an in-math ``=`` for
    the separator.
    """
    brace = 0
    math = False
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "$":
            math = not math
            i += 1
            continue
        if math:
            i += 1
            continue
        if ch == "{":
            brace += 1
        elif ch == "}":
            if brace > 0:
                brace -= 1
        elif ch == "=" and brace == 0:
            return i
        i += 1
    return -1


def _value_is_hostile(value: str) -> bool:
    r"""True when ``value`` carries a pgfkeys-hostile char at brace-depth zero.

    The hostile characters are ``=`` (a second key/value split), ``,`` (a list
    split) and ``]`` (an early list end).  This test is deliberately
    ``$``-BLIND -- it does NOT skip math -- because pgfkeys is ``$``-blind, and
    a hostile character the author hid inside ``$...$`` is exactly the fatal
    case.  Only ``{}`` bracing (depth >= 1) hides a character from pgfkeys, so
    only brace depth suppresses the count.
    """
    brace = 0
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            brace += 1
        elif ch == "}":
            if brace > 0:
                brace -= 1
        elif brace == 0 and ch in _HOSTILE_VALUE_CHARS:
            return True
        i += 1
    return False


def autofix(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    r"""Brace pgfkeys-hostile option values; leave everything else untouched.

    Returns ``(new_body, applied, warnings)``.  Rewrites are applied
    right-to-left so each brace insertion leaves the offsets of the
    not-yet-processed sites valid.  ``warnings`` is currently always empty:
    every hostile value found is repaired, none are merely reported.

    Wrapped in a blanket except: this is a convenience check on model-authored
    input, so any defect must degrade to "render the body as written", never to
    a failed render.
    """
    try:
        return _autofix(body)
    except Exception:                      # pragma: no cover - defensive
        logger.exception("circuitikz lint failed; rendering body unchanged")
        return body, (), ()


def _autofix(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    # Strip JSON-habit quotes around numeric/dimension values FIRST, so the
    # brace-wrap pass below scans the recovered body.  Order is not
    # load-bearing (de-quoting never introduces a pgfkeys-hostile char), but
    # doing it first keeps both passes reasoning about the same text.
    body, quote_fixes = _strip_numeric_quotes(body)

    # (value_core_start, value_core_end) spans in the ORIGINAL body to wrap.
    wraps: list[tuple[int, int]] = []

    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if ch != "[":
            i += 1
            continue
        close = _match_bracket(body, i)
        if close is None:                  # unbalanced -- do not guess a span
            i += 1
            continue
        inner_start = i + 1
        inner = body[inner_start:close]
        for seg_start, seg_end in _split_top_level_commas(inner):
            segment = inner[seg_start:seg_end]
            eq = _first_top_level_eq(segment)
            if eq < 0:
                continue
            value = segment[eq + 1:]
            if not _value_is_hostile(value):
                continue
            # Wrap the value's non-whitespace core, keeping surrounding spaces
            # outside the braces so formatting is otherwise byte-identical.
            v_abs_start = inner_start + seg_start + eq + 1
            v_abs_end = inner_start + seg_end
            core_start = v_abs_start
            while core_start < v_abs_end and body[core_start] in " \t":
                core_start += 1
            core_end = v_abs_end
            while core_end > core_start and body[core_end - 1] in " \t":
                core_end -= 1
            if core_start >= core_end:
                continue
            # Already fully braced?  Then its hostile chars are at depth >= 1
            # and it would not have been flagged; this is belt-and-braces for
            # idempotency.
            if body[core_start] == "{" and _match_brace(body, core_start) == core_end - 1:
                continue
            wraps.append((core_start, core_end))
        i = close + 1

    if not wraps:
        return body, tuple(quote_fixes), ()

    applied: list[str] = []
    out = body
    for core_start, core_end in sorted(wraps, key=lambda t: t[0], reverse=True):
        original = out[core_start:core_end]
        out = out[:core_start] + "{" + original + "}" + out[core_end:]
        applied.append(
            f"braced option value {original!r} which contains a pgfkeys-hostile "
            f"character ('=', ',' or ']') (would otherwise abort the compile)."
        )

    return out, tuple(quote_fixes) + tuple(reversed(applied)), ()


def _match_brace(body: str, open_idx: int) -> Optional[int]:
    r"""Index of the ``}`` matching the ``{`` at ``open_idx``, or None."""
    if body[open_idx] != "{":
        return None
    depth = 0
    i = open_idx
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None
