r"""
Structural lint / recovery pass for model-authored TikZ and tikz-cd bodies
(fix group G-06: D-246, D-248, D-249).

Why this exists
---------------
Before this module the LaTeX renderer dispatched a structural lint for exactly
two profiles (``chemfig`` and ``circuitikz``); ``tikz`` and ``tikz-cd`` matched
neither, so they had *no* preprocessor at all -- their entire recovery surface
was the wrapper stripping in ``_sanitize_input`` plus the shared colour/Unicode
normalisers.  This module fills that gap with a small set of rewrites that are
each provably safe (they either preserve the rendered output exactly or repair
a body that could not compile at all), because the renderer runs them
unconditionally on every TikZ diagram and an over-eager rewrite would corrupt a
working diagram -- worse than the bug.

What it does (and, deliberately, what it does NOT)
--------------------------------------------------
* **Literal ``\n`` restoration** (D-246, ``tikz-w4-15``).  A model that
  serialises a multi-line body through a JSON string and then loses the escape
  layer emits the two characters ``backslash`` + ``n`` where a real newline
  belonged.  In TeX ``\n`` (not followed by a letter) is an undefined control
  sequence and aborts the whole compile.  There is no legitimate standalone
  ``\n`` token in TikZ, so restoring it to a newline is strictly a repair.

* **Trig argument overflow-safe reduction** (D-249 / D-011, ``tikz-w2-14``).
  ``pgfmath`` routes every operation *result* through a TeX dimen register
  whose ceiling is 16383.99998pt, so the common idiom ``cos(\n*111)`` --
  deriving a per-element angle from a loop counter -- throws a fatal "Dimension
  too large" once the product crosses ~16384 (at ``\n = 148`` for ``*111``),
  naming a length the author never wrote.  A naive ``mod(inner,360)`` wrap does
  NOT help here: pgfmath still forms the inner product ``\n*111`` (up to 33189
  for ``\n<=299``) *before* ``mod`` runs, so it overflows first -- this is why
  ``tikz-w2-14`` remained fatal after the original clamp and re-surfaced as
  D-011.  ``sin``/``cos``/``tan``/``cot``/``sec``/``cosec`` are 360-periodic, so
  the value is preserved by reducing modulo 360; the fix reduces *before* the
  multiply, keeping every intermediate under the ceiling.  For a ``macro * K``
  argument it emits either the **exact** integer form
  ``mod(mod(macro,P)*K,360)`` where ``P = 360/gcd(K,360)`` is the true period of
  ``k -> K*k (mod 360)`` (chosen when ``(P-1)*K`` clears the ceiling, e.g. K=111
  -> P=120, product <=13209 -- pure integer, no rounding), or, when a
  coprime/large multiplier leaves the period too big to shrink the product
  (e.g. K=73, period 360, ``\n<=299`` unreduced -> 21827 overflow), the **frac**
  form ``360*frac((macro)/360*K)`` which divides the small counter by 360 first
  and is exact up to pgfmath's ~1e-5 fixed-point rounding (sub-pixel for any
  coordinate use).  Applied only when the argument contains a macro (a ``\``),
  which is precisely the loop-counter case, so constant-angle diagrams
  (``sin(30)``) are left byte-for-byte unchanged; a macro argument that is not a
  single ``term * number`` product falls back to the generic ``mod(...,360)``
  wrap (best effort, unchanged from before).

* **``\pgfmathparse`` -> ``\pgfmathsetmacro`` capture** (D-248,
  ``tikz-w3-05``).  ``\pgfmathparse{E}`` stores its result in the shared
  ``\pgfmathresult`` register; when the very next thing is a ``\node ... at
  (x,y) {... \pgfmathresult}`` the node's *coordinate* re-runs pgfmath and
  overwrites ``\pgfmathresult`` before the body typesets, so the node silently
  prints its own y-coordinate instead of the computed value -- a *wrong number*
  that passes casual review.  The ``\edef``/``\let`` snapshot workaround is
  blocked by the security prescan, leaving ``\pgfmathsetmacro`` as the only
  legal capture.  When a coordinate-bearing command (``\node``/``\path``/...)
  sits between a ``\pgfmathparse`` and its ``\pgfmathresult``, this rewrites the
  pair to a uniquely-named macro.  ``\pgfmathsetmacro{\m}{E}`` computes the
  identical value, so the output is unchanged where it was already correct and
  repaired where it was clobbered.

NOT attempted here (recorded as residual on D-246): inserting missing statement
semicolons (``tikz-w4-03``), balancing an unclosed label brace
(``tikz-w4-11``), and stripping SVG-attribute contamination / quoted hex
(``tikz-w4-13``).  Each of those needs a real TikZ tokeniser to do without a
serious risk of corrupting valid input, which is disproportionate for an
advisory pass that must never break a working render.

Contract (identical to circuitikz_lint / chemfig_lint)
------------------------------------------------------
``autofix(body) -> (new_body, applied, warnings)`` is advisory only: any
internal fault degrades to ``(body, (), ())`` and it must NEVER raise, so a
defect in this module can never turn a render that would have worked into a
failure.
"""
from __future__ import annotations

import logging
import math
import re

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _match_paren(body: str, open_idx: int) -> int | None:
    """Index of the ``)`` matching the ``(`` at ``open_idx``, or None.

    Skips over ``\\(`` escaped parens and respects nesting.  Returns None on an
    unbalanced run so the caller declines to guess a span.
    """
    depth = 0
    i = open_idx
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _macro_name(index: int) -> str:
    """Deterministic letters-only control sequence, e.g. 0->\\ziyapmva."""
    letters = ""
    n = index
    while True:
        letters = chr(ord("a") + (n % 26)) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return "\\ziyapmv" + letters


# --------------------------------------------------------------------------
# 1. Literal ``\n`` restoration (D-246)
# --------------------------------------------------------------------------
# CAUTION: ``\n`` is also a perfectly ordinary TikZ macro name -- ``\foreach \n
# in {...}`` and ``\pgfmathtruncatemacro{\n}{...}`` are extremely common, and
# ``cos(\n*111)`` references it -- so a naive "``\n`` -> newline" rewrite would
# corrupt more diagrams than it fixes.  Restore only the *exact* signature of a
# body whose newlines were serialised away: the whole body is a single physical
# line (no real newline survives) AND a ``\n`` is immediately followed by
# another command (``\n\node``, ``\n\draw``).  A ``\n`` macro reference is
# followed by an operator/brace/space (``\n*111``, ``(\n,0)``, ``{\n}``), never
# by a backslash, so this never touches a loop counter.
_SERIALISED_NEWLINE_RE = re.compile(r"(?<!\\)\\n(?=\\)")
# A serialised ``\n`` at the very END of the body (D-005, ``tikz-w4-15``).  A
# JSON-serialised multiline body routinely carries a trailing ``\n`` after its
# last statement, and ``_SERIALISED_NEWLINE_RE``'s ``(?=\\)`` lookahead cannot
# match it -- there is no following command -- so it survives as a stray
# undefined control sequence ``\n`` and aborts the whole compile.  This matches
# a ``\n`` that is the last non-whitespace token so it can be dropped.  Same
# ``(?<!\\)`` guard: a ``\node``/``\draw`` ending the body is never touched.
_TRAILING_SERIALISED_NEWLINE_RE = re.compile(r"(?<!\\)\\n(?=\s*$)")


def _restore_literal_newlines(body: str) -> tuple[str, tuple[str, ...]]:
    if "\n" in body:
        # A real newline survived -> the body was never single-line-serialised,
        # so any '\n' here is a macro reference.  Leave it entirely alone.
        return body, ()
    mid = len(_SERIALISED_NEWLINE_RE.findall(body))
    out = _SERIALISED_NEWLINE_RE.sub("\n", body)
    # Drop a trailing serialised '\n' the mid-body pass could not reach (its
    # lookahead needs a FOLLOWING command).  Done after the mid-body sub so the
    # real newlines it inserted make ``\s*$`` anchor at the true end of body.
    out, trail = _TRAILING_SERIALISED_NEWLINE_RE.subn("", out)
    count = mid + trail
    if not count:
        return body, ()
    return out, (
        f"restored {count} serialised '\\n' sequence(s) to newlines "
        "(a stray '\\n' before a command -- or trailing at end of body -- is an "
        "undefined control sequence and aborts the compile)",
    )


# --------------------------------------------------------------------------
# 1a2. SVG / CSS presentation-attribute dialect in option lists
#      (D-250, tikz-w4-13)
# --------------------------------------------------------------------------
# A model that thinks in SVG/CSS reaches for presentation attributes as if they
# were pgfkeys: ``stroke=white``, ``stroke-width=2``, ``font-size=12``,
# ``text-anchor=middle``.  None of these is a valid TikZ key, so the FIRST one
# aborts the whole compile with ``Package pgfkeys Error: I do not know the key
# '/tikz/stroke-width'`` -- no image, for a drawing that is otherwise fine (the
# quoted-hex ``fill="#hex"`` sibling of this dialect is already handled upstream
# by ``latex_color._OPT_HEX_RE``).
#
# Two of the four have an EXACT TikZ equivalent and are translated so the
# author's intent survives verbatim:
#   * ``stroke=<colour>``     -> ``draw=<colour>``   (SVG stroke IS the outline)
#   * ``stroke-width=<n>[px]`` -> ``line width=<n>pt`` (1px == 1pt at this DPI;
#                                 SVG's default user unit maps to a TeX point)
# The other two have no faithful TikZ key (``font-size`` in raw px would need a
# font-switch the size cannot be trusted to pick, and ``text-anchor`` is SVG
# horizontal justification, not TikZ node ``anchor=`` placement), so they are
# DROPPED rather than guessed -- dropping a decorative attribute keeps the
# geometry, whereas keeping it is fatal.
#
# Scope discipline: this fires ONLY inside a ``[...]`` option block and ONLY on
# these four never-valid keys, so a body with none of them is byte-identical
# and an option list that happens to be valid TikZ is untouched (none of the
# four is a real TikZ key).  Comma-splitting is brace/paren-aware so a
# normalised ``fill={rgb,255:...}`` value (whose internal commas are NOT entry
# separators) is preserved intact.
_SVG_DIALECT_KEY_RE = re.compile(
    r"(?<![A-Za-z-])(?:stroke-width|stroke|font-size|text-anchor)\s*=",
    re.IGNORECASE,
)
_SVG_OPT_BLOCK_RE = re.compile(r"\[([^\[\]]*)\]")


def _split_option_entries(block: str) -> list[str]:
    """Split a TikZ option-list body on its TOP-LEVEL commas.

    Commas inside ``{...}`` (an ``{rgb,255:...}`` colour value) or ``(...)`` (a
    coordinate) are not entry separators and are kept with their entry.  A
    backslash escapes the next character so ``\\{`` never opens a group.
    """
    entries: list[str] = []
    depth_brace = 0
    depth_paren = 0
    cur: list[str] = []
    i = 0
    n = len(block)
    while i < n:
        ch = block[i]
        if ch == "\\":
            cur.append(block[i:i + 2])
            i += 2
            continue
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            if depth_brace > 0:
                depth_brace -= 1
        elif ch == "(":
            depth_paren += 1
        elif ch == ")":
            if depth_paren > 0:
                depth_paren -= 1
        elif ch == "," and depth_brace == 0 and depth_paren == 0:
            entries.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    entries.append("".join(cur))
    return entries


def _translate_svg_entry(entry: str) -> str | None:
    """Map one SVG/CSS presentation attribute to its TikZ equivalent.

    Returns the translated entry, ``None`` when the attribute has no faithful
    equivalent and must be DROPPED, or the entry unchanged when it is not a
    recognised SVG-dialect key.
    """
    stripped = entry.strip()
    m = re.match(r"(?i)^stroke-width\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*(?:px|pt)?\s*$",
                 stripped)
    if m:
        return f"line width={m.group(1)}pt"
    if re.match(r"(?i)^stroke-width\s*=", stripped):
        return None                        # non-numeric width -> drop, don't guess
    m = re.match(r"(?i)^stroke\s*=\s*(.+)$", stripped)
    if m:
        return f"draw={m.group(1).strip()}"
    if re.match(r"(?i)^(?:font-size|text-anchor)\s*=", stripped):
        return None                        # no faithful TikZ key -> drop
    return entry


def _translate_svg_attributes(body: str) -> tuple[str, tuple[str, ...]]:
    if not _SVG_DIALECT_KEY_RE.search(body):
        return body, ()
    count = 0
    out_parts: list[str] = []
    last = 0
    for m in _SVG_OPT_BLOCK_RE.finditer(body):
        block = m.group(1)
        if not _SVG_DIALECT_KEY_RE.search(block):
            continue                       # no dialect key here -> leave as-is
        new_entries: list[str] = []
        changed = False
        for entry in _split_option_entries(block):
            translated = _translate_svg_entry(entry)
            if translated is None:
                changed = True
                count += 1
                continue                   # dropped attribute
            if translated != entry:
                changed = True
                count += 1
            new_entries.append(translated)
        if not changed:
            continue
        out_parts.append(body[last:m.start()])
        out_parts.append("[" + ",".join(new_entries) + "]")
        last = m.end()
    if not count:
        return body, ()
    out_parts.append(body[last:])
    out = "".join(out_parts)
    return out, (
        f"translated/removed {count} SVG-attribute-dialect option(s) "
        "(stroke->draw, stroke-width->line width; font-size/text-anchor have no "
        "faithful TikZ key and were dropped -- each is an unknown pgfkey that "
        "otherwise aborts the compile)",
    )


# --------------------------------------------------------------------------
# 1b. Missing statement-terminating semicolons (D-005, tikz-w4-03 /
#     circuitikz-w4-09)
# --------------------------------------------------------------------------
# A model very commonly omits the ``;`` that terminates a TikZ/circuitikz path
# statement, because most other diagram DSLs are newline-terminated:
#
#     \node (a) at (1,1) {Read}          <- missing ';'
#     \node (b) at (4,1) {Map};
#     \draw (a)--(b)                     <- missing ';'
#     \draw (b)--(c);
#
# ``! Package tikz Error: Giving up on this path.  Did you forget a
# semicolon?`` -- a fatal abort, no image, for an otherwise valid drawing.
#
# The inserter is deliberately conservative and provably safe on VALID input:
# it inserts ``;`` ONLY immediately before a statement-start control word (or
# at end of body) and ONLY when a prior statement-start has not yet been
# terminated by a ``;`` at brace-depth zero.  A well-formed body terminates
# every statement before the next one begins, so ``pending`` is always False at
# every trigger and NOTHING is inserted -- the pass is byte-identical on any
# compiling body and repairs only a body that could not compile at all, exactly
# the module's contract.
#
# Safety rests on three scoping rules, all tracked by a hand scanner:
#   * brace depth -- a ``\node`` in a tree ``child {...}`` or a matrix cell
#     ``{... & ...}`` sits at depth >= 1 and is ignored (it is the enclosing
#     statement's business), as is a ``;`` inside a label;
#   * ``$...$`` math -- an ``=``/``;`` inside math is skipped;
#   * ``%`` comments -- skipped to end of line.
# The path-operation spellings are ``node``/``coordinate``/``edge`` with NO
# backslash, so a backslashed ``\node`` is unambiguously a statement, never a
# mid-path operation.

#: Control words that ALWAYS start a new top-level drawing statement (each
#: terminated by ``;``) and never continue a path.
_STMT_START_MACROS = frozenset({
    "draw", "fill", "filldraw", "path", "node", "coordinate", "clip",
    "shade", "shadedraw", "shadedpath", "useasboundingbox", "pattern",
    "matrix", "graph", "addplot", "addplot3",
})

#: Control words that TERMINATE any pending statement but do not start one that
#: needs a ``;`` (environment delimiters).  An unterminated path before one of
#: these is just as fatal, so a pending ``;`` is flushed before them.
_FLUSH_ONLY_MACROS = frozenset({"begin", "end"})


def _insert_missing_semicolons(body: str) -> tuple[str, tuple[str, ...]]:
    inserts: list[int] = []
    depth = 0
    math = False
    pending = False        # a statement-start seen since the last ';'
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":
            j = i + 1
            if j < n and body[j].isalpha():
                k = j
                while k < n and body[k].isalpha():
                    k += 1
                name = body[j:k]
                if depth == 0 and not math:
                    if name in _STMT_START_MACROS:
                        if pending:
                            inserts.append(i)
                        pending = True
                    elif name in _FLUSH_ONLY_MACROS:
                        if pending:
                            inserts.append(i)
                        pending = False
                i = k
                continue
            # Control symbol (\\, \{, \;, ...): copy the two chars over.
            i += 2
            continue
        if ch == "%":
            nl = body.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if ch == "$":
            math = not math
            i += 1
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            if depth > 0:
                depth -= 1
            i += 1
            continue
        if ch == ";" and depth == 0 and not math:
            pending = False
            i += 1
            continue
        i += 1

    if pending:
        inserts.append(n)          # last statement runs to end of body unterminated

    if not inserts:
        return body, ()
    out = body
    for pos in sorted(inserts, reverse=True):
        out = out[:pos] + ";" + out[pos:]
    count = len(inserts)
    plural = "" if count == 1 else "s"
    return out, (
        f"inserted {count} missing statement-terminating semicolon{plural} "
        "(a TikZ/circuitikz path must end with ';' before the next statement; "
        "the omission aborts the compile with 'Did you forget a semicolon?')",
    )


# --------------------------------------------------------------------------
# 2. Trig argument periodic clamp (D-249)
# --------------------------------------------------------------------------
_TRIG_RE = re.compile(r"(?<![A-Za-z@])(sin|cos|tan|cot|sec|cosec|csc)\(")

#: pgfmath routes every operation result through a TeX dimen register whose
#: magnitude ceiling is 16383.99998pt; an intermediate that reaches it aborts
#: the compile with "Dimension too large".  We reduce well below the raw
#: ceiling to leave headroom for pgfmath's own fixed-point rounding.
_TRIG_DIMEN_CEILING = 16000

_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def _split_top_level_product(expr: str) -> tuple[str, str] | None:
    """Split ``A*B`` when there is *exactly one* top-level ``*`` and no other
    top-level additive/multiplicative operator, so the two operands are an
    unambiguous single product.  Returns ``None`` for anything more complex
    (a sum, a chained product, a division) which is left to the generic
    ``mod(...,360)`` wrap.  Operators inside parentheses do not count.
    """
    depth = 0
    star = -1
    for i, ch in enumerate(expr):
        if ch == "\\":
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth > 0:
                depth -= 1
        elif depth == 0 and ch in "+*/":
            if ch == "*":
                if star != -1:
                    return None      # more than one top-level '*'
                star = i
            else:
                return None          # a top-level '+' or '/' -> not a bare product
        elif depth == 0 and ch == "-" and i > 0:
            # A binary '-' at depth 0 disqualifies; a leading unary '-' (i==0)
            # is tolerated so it can be carried inside an operand.
            return None
    if star == -1:
        return None
    return expr[:star], expr[star + 1:]


def _overflow_safe_trig_arg(inner: str) -> str | None:
    """Rewrite the loop-counter idiom ``\\n*K`` (a macro-bearing term times a
    numeric constant) into an overflow-free form whose value is the same modulo
    360, so the trig function is unchanged.

    Two shapes, both keeping every pgfmath intermediate well under the dimen
    ceiling:

    * **Exact** integer form ``mod(mod(macro,P)*K,360)`` where
      ``P = 360/gcd(K,360)`` is the true period of ``k -> K*k (mod 360)``.
      Chosen only when ``(P-1)*K`` clears the ceiling, so the reduced counter
      times ``K`` cannot overflow.  Pure integer arithmetic -> no rounding.

    * **Frac** form ``360*frac((macro)/360*K)`` for the residual case (a period
      that does not shrink the product enough -- e.g. a multiplier coprime to
      360 -- or a non-integer multiplier).  Dividing the small loop counter by
      360 *before* multiplying keeps the intermediate bounded regardless of
      ``K``; the reduction is exact up to pgfmath's ~1e-5 fixed-point rounding
      (sub-pixel for any coordinate use).

    Returns ``None`` when ``inner`` is not a single ``term * number`` product,
    leaving the caller to fall back to the generic ``mod(inner,360)`` wrap.
    """
    parts = _split_top_level_product(inner.strip())
    if parts is None:
        return None
    left, right = parts[0].strip(), parts[1].strip()
    if _NUMBER_RE.match(left) and "\\" in right and not _NUMBER_RE.match(right):
        num, macro = left, right
    elif _NUMBER_RE.match(right) and "\\" in left and not _NUMBER_RE.match(left):
        num, macro = right, left
    else:
        return None
    if not macro:
        return None
    try:
        fval = float(num)
    except ValueError:
        return None
    if fval == 0:
        return None
    if fval == int(fval):
        k = int(fval)
        period = 360 // math.gcd(abs(k), 360)
        if (period - 1) * abs(k) < _TRIG_DIMEN_CEILING:
            return f"mod(mod({macro},{period})*{k},360)"
    return f"360*frac(({macro})/360*{num})"


def _clamp_trig_arguments(body: str) -> tuple[str, tuple[str, ...]]:
    # Collect spans right-to-left so each rewrite keeps later offsets valid.
    edits: list[tuple[int, int, str]] = []   # (start, end, replacement)
    for m in _TRIG_RE.finditer(body):
        func = m.group(1)
        open_idx = m.end() - 1
        close_idx = _match_paren(body, open_idx)
        if close_idx is None:
            continue
        inner = body[open_idx + 1:close_idx]
        stripped = inner.strip()
        if "\\" not in inner:
            # Constant / literal angle: no overflow risk, leave byte-identical.
            continue
        if stripped.startswith("mod(") and stripped.endswith(",360)"):
            continue                        # already clamped (idempotent)
        if stripped.startswith("360*frac("):
            continue                        # already reduced (idempotent)
        safe = _overflow_safe_trig_arg(inner)
        if safe is not None:
            replacement = f"{func}({safe})"
        else:
            # Generic best-effort wrap: exact for the value, but the inner
            # product is still formed, so it only helps when the raw argument
            # itself stays under the dimen ceiling.
            replacement = f"{func}(mod({inner},360))"
        edits.append((m.start(), close_idx + 1, replacement))

    if not edits:
        return body, ()
    for start, end, replacement in reversed(edits):
        body = body[:start] + replacement + body[end:]
    return body, (
        f"reduced {len(edits)} loop-derived trig argument(s) to an "
        "overflow-safe periodic form (pgfmath trig is 360-periodic; the loop "
        "counter is period-reduced before the multiply, or divided by 360 "
        "first via 360*frac(...), so the rendered value is preserved while the "
        "'Dimension too large' dimen-register overflow at high loop indices is "
        "avoided)",
    )


# --------------------------------------------------------------------------
# 3. \pgfmathparse -> \pgfmathsetmacro capture (D-248)
# --------------------------------------------------------------------------
_PARSE_RE = re.compile(r"\\pgfmathparse\s*\{")
# Commands whose coordinate/option parsing re-runs pgfmath and clobbers
# \pgfmathresult before a following node body typesets it.
_COORD_CMD_RE = re.compile(r"\\(node|path|draw|fill|coordinate|matrix)(?![A-Za-z@])")
_RESULT_RE = re.compile(r"\\pgfmathresult(?![A-Za-z@])")


def _capture_pgfmath_results(body: str) -> tuple[str, tuple[str, ...]]:
    # Find every \pgfmathparse{...} and its brace-matched expression.
    parses: list[tuple[int, int, str]] = []   # (start, end, expr)
    for m in _PARSE_RE.finditer(body):
        brace_open = m.end() - 1
        # brace matcher (honours \{ escapes and nesting)
        depth = 0
        i = brace_open
        n = len(body)
        close = None
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
                    close = i
                    break
            i += 1
        if close is None:
            continue
        parses.append((m.start(), close + 1, body[brace_open + 1:close]))

    if not parses:
        return body, ()

    edits: list[tuple[int, int, str]] = []
    applied = 0
    for idx, (start, end, expr) in enumerate(parses):
        window_end = parses[idx + 1][0] if idx + 1 < len(parses) else len(body)
        window = body[end:window_end]
        result = _RESULT_RE.search(window)
        if result is None:
            continue
        # Only rewrite when a coordinate-bearing command intervenes between the
        # parse and the result usage -- that is exactly the clobber trap.  A
        # bare "\pgfmathparse{E}\pgfmathresult" was never clobbered.
        coord = _COORD_CMD_RE.search(window[:result.start()])
        if coord is None:
            continue
        macro = _macro_name(idx)
        # Replace the \pgfmathparse{E} with \pgfmathsetmacro{\m}{E} ...
        edits.append((start, end, f"\\pgfmathsetmacro{{{macro}}}{{{expr}}}"))
        # ... and every \pgfmathresult in this window with \m.
        for r in _RESULT_RE.finditer(window):
            abs_start = end + r.start()
            abs_end = end + r.end()
            edits.append((abs_start, abs_end, macro))
        applied += 1

    if not applied:
        return body, ()
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        body = body[:start] + replacement + body[end:]
    return body, (
        f"captured {applied} \\pgfmathparse result(s) into \\pgfmathsetmacro "
        "macros (a following node coordinate re-runs pgfmath and clobbers "
        "\\pgfmathresult before the body prints it, so it silently showed the "
        "node's own coordinate)",
    )


# --------------------------------------------------------------------------
# 4. Legend-entry text: pgfplots uses ``\to`` as its OWN delimiter
# --------------------------------------------------------------------------
# pgfplots stores a legend entry by handing the entry text to a macro whose
# argument is DELIMITED by ``\to`` (pgfplots.code.tex):
#
#     \long\def\pgfplots@addlegendentry@opts[#1]#2{%
#         \pgfplotslistpushbackglobal[#1]#2\to\pgfplots@legend
#
# So a ``\to`` in the author's own entry text terminates that delimited
# argument early, and the tokens after it are consumed as the list-macro NAME.
# TeX then executes ``\def`` on something that is not a control sequence and
# aborts with "Missing control sequence inserted / Please don't say `\def
# cs{...}'" -- an error naming neither the legend nor ``\to``, for a body that
# is otherwise perfectly valid.  Verified empirically: ``\addlegendentry{$A\to
# B$}`` is fatal on its own, while the same ``\to`` inside an ``xlabel``,
# ``ylabel`` or ``\node`` renders fine, and ``legend entries={$A\to B$}``
# reproduces the identical abort.
#
# The rewrite is a TOKEN-LEVEL IDENTITY, not an approximation: plain.tex line
# 899 reads ``\mathchardef\rightarrow="3221 \let\to=\rightarrow``, and neither
# amsmath nor amssymb redefines either name, so ``\to`` and ``\rightarrow`` are
# the same mathchar with the same Rel spacing.  Confirmed visually as well --
# ``\to``, ``\rightarrow`` and ``\mathrel{\to}`` render pixel-identically.
#
# Scoped strictly to legend entry text.  A ``\to`` anywhere else is legal and
# is left byte-for-byte alone, and a body with no legend at all (every ``tikz``
# / ``tikz-cd`` body) exits on the first guard without being examined.
_ADDLEGENDENTRY_RE = re.compile(r"\\addlegendentry\s*(?:\[[^\]]*\])?\s*\{")
_LEGENDENTRIES_KEY_RE = re.compile(r"legend\s+entries\s*=\s*\{")

# ``\to`` as a COMPLETE control sequence only.  The trailing guard keeps
# ``\top``/``\toprule``/``\totalheight`` untouched; the leading one keeps
# ``\\to`` (a TeX line break followed by the literal word "to") untouched --
# there ``to`` is text, not a macro, and rewriting it would invent an arrow the
# author never wrote.  Same guard shape as _SERIALISED_NEWLINE_RE above.
_BARE_TO_RE = re.compile(r"(?<!\\)\\to(?![a-zA-Z])")

# ``\dfrac`` forces a display-size fraction into a legend row, and the default
# legend row separation does not grow to accommodate it: the tall fraction
# overflows into the neighbouring row and the two collide into unreadable
# overlapping glyphs (the fraction bar visibly cuts through the next entry's
# letters).  Isolated: a legend using only ``\frac``/``\tfrac`` renders
# cleanly, so ``\frac`` is the VICTIM of an adjacent ``\dfrac``, not a cause,
# and only ``\dfrac`` is counted.
#
# The repair adds room instead of shrinking the maths, which preserves exactly
# what the author asked for.  Measured on this renderer with a two-row legend:
#
#     row sep   simple \dfrac   nested \dfrac
#     (default)   overlaps        overlaps
#     2pt         overlaps        overlaps
#     4pt         clear           -
#     6pt         clear           clear
#
# so 6pt is injected: the smallest measured value that also clears a nested
# ``\dfrac{\dfrac{1}{\mu}}{1-\rho}``.  A raised ``\rule`` strut was tried first
# and does NOT work -- pgfplots sizes the row from the legend layout, not from
# the cell's own height -- which is why this adjusts the axis option list.
#
# Appending a second ``legend style={...}`` is safe rather than clobbering:
# pgfplots' ``legend style`` APPENDS to ``every axis legend``, verified by
# rendering a body with ``legend style={draw=red,fill=yellow!20}`` followed by
# the injected block -- the red border and yellow fill both survived and the
# overlap was fixed.
_DFRAC_RE = re.compile(r"(?<!\\)\\dfrac(?![a-zA-Z])")

# The axis-family environments whose option list can carry ``legend style``.
# pgfplots' own set; ``\begin{axis}`` is by far the common case, but a body
# using the log or polar variant hits the identical overlap.
_AXIS_BEGIN_RE = re.compile(
    r"\\begin\{(?:semilogxaxis|semilogyaxis|loglogaxis|polaraxis|axis)\}"
)

# Any author-supplied row separation, anywhere in the body.  When this matches
# the injection is declined and the situation is reported instead: overriding a
# value the author chose deliberately would be worse than leaving it, even when
# the value is too small to clear the fraction.
_ROW_SEP_RE = re.compile(r"row\s+sep\s*=")

_LEGEND_ROW_SEP = "legend style={row sep=6pt}"


def _match_brace(body: str, open_idx: int) -> int | None:
    r"""Index of the ``}`` matching the ``{`` at ``open_idx``, or None.

    Brace sibling of ``_match_paren``; kept beside its only caller because the
    legend-span scan is the sole thing that needs it.  Skipping two characters
    at a backslash is what makes ``\{`` / ``\}`` non-counting, and it is
    harmless over an ordinary macro name.  Returns None on an unbalanced run so
    the caller declines to guess a span rather than rewriting past its end.
    """
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


def _legend_entry_spans(body: str) -> list[tuple[int, int]]:
    r"""``(start, end)`` index pairs over the CONTENT of every legend entry.

    Covers both spellings that reach the colliding delimiter: the
    ``\addlegendentry{...}`` macro (with or without its optional argument) and
    the ``legend entries={...}`` axis key.  Sorted, so callers can walk the
    list in reverse and splice without invalidating earlier offsets.
    """
    spans: list[tuple[int, int]] = []
    for pat in (_ADDLEGENDENTRY_RE, _LEGENDENTRIES_KEY_RE):
        for m in pat.finditer(body):
            open_idx = m.end() - 1          # the '{' the pattern ends on
            close_idx = _match_brace(body, open_idx)
            if close_idx is None:
                continue                    # unbalanced -> decline to guess
            spans.append((open_idx + 1, close_idx))
    spans.sort()
    return spans


def _rewrite_to_in_legend_entries(body: str) -> tuple[str, tuple[str, ...]]:
    if "\\addlegendentry" not in body and "legend entries" not in body:
        return body, ()
    spans = _legend_entry_spans(body)
    if not spans:
        return body, ()
    out = body
    count = 0
    # Reverse order: splicing a later span cannot move an earlier one's
    # offsets.  Re-processing an overlapping span would be harmless anyway --
    # the rewrite leaves no ``\to`` behind, so it is idempotent.
    for start, end in reversed(spans):
        new_seg, n = _BARE_TO_RE.subn(r"\\rightarrow", out[start:end])
        if n:
            out = out[:start] + new_seg + out[end:]
            count += n
    if not count:
        return body, ()
    plural = "" if count == 1 else "s"
    return out, (
        f"rewrote {count} \\to -> \\rightarrow inside legend entry text: "
        "pgfplots uses \\to as its own argument delimiter, so a \\to there "
        f"aborts the compile. \\to is \\let to \\rightarrow, so the rendered "
        f"arrow{plural} {'is' if count == 1 else 'are'} unchanged.",
    )


def _match_bracket(body: str, open_idx: int) -> int | None:
    r"""Index of the ``]`` closing the ``[`` at ``open_idx``, or None.

    Distinct from ``_match_brace``: an axis option list routinely contains
    braced key values (``legend style={row sep=6pt}``, ``xlabel={$[m]$}``) and
    those braces may themselves hold a ``]``.  Square-bracket depth is
    therefore counted only OUTSIDE braces -- a naive scan stops at the first
    ``]`` it meets and would splice into the middle of a key value.  Returns
    None on an unbalanced run so the caller declines to guess.
    """
    depth_sq = 0
    depth_br = 0
    i = open_idx
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth_br += 1
        elif ch == "}":
            depth_br -= 1
        elif depth_br == 0:
            if ch == "[":
                depth_sq += 1
            elif ch == "]":
                depth_sq -= 1
                if depth_sq == 0:
                    return i
        i += 1
    return None


def _count_dfrac_in_legends(body: str) -> int:
    r"""How many ``\dfrac`` sit inside legend entry text (0 = nothing to do).

    Shared by the row-sep autofix and its advisory counterpart so the two can
    never disagree about whether a body is affected.
    """
    if "\\dfrac" not in body:
        return 0
    if "\\addlegendentry" not in body and "legend entries" not in body:
        return 0
    return sum(
        len(_DFRAC_RE.findall(body[start:end]))
        for start, end in _legend_entry_spans(body)
    )


def _reserve_legend_row_sep(body: str) -> tuple[str, tuple[str, ...]]:
    count = _count_dfrac_in_legends(body)
    if not count:
        return body, ()
    if _ROW_SEP_RE.search(body):
        # The author chose a separation themselves -> respect it, even if it is
        # too small.  Surfaced by _warn_row_sep_may_be_insufficient instead.
        return body, ()
    matches = list(_AXIS_BEGIN_RE.finditer(body))
    if not matches:
        return body, ()
    out = body
    injected = 0
    # Reverse order: splicing a later axis cannot move an earlier one's offsets.
    for m in reversed(matches):
        i = m.end()
        while i < len(out) and out[i] in " \t\r\n":
            i += 1
        if i < len(out) and out[i] == "[":
            close = _match_bracket(out, i)
            if close is None:
                continue                # unbalanced -> leave this axis alone
            out = out[:close] + "," + _LEGEND_ROW_SEP + out[close:]
        else:
            # No option list at all.  Insert one immediately after the
            # environment name rather than after the skipped whitespace, so the
            # bracket stays adjacent to \begin{axis} as LaTeX expects.
            out = out[: m.end()] + "[" + _LEGEND_ROW_SEP + "]" + out[m.end():]
        injected += 1
    if not injected:
        return body, ()
    plural = "" if count == 1 else "s"
    return out, (
        f"added {_LEGEND_ROW_SEP} for {count} display-size fraction{plural} in "
        "legend entry text: the default legend row separation does not grow to "
        "fit a \\dfrac, so it overlapped the neighbouring entry. The fraction "
        "is left at the size you wrote.",
    )


def _warn_row_sep_may_be_insufficient(body: str) -> tuple[str, ...]:
    if not _ROW_SEP_RE.search(body):
        return ()                   # nothing author-set -> the autofix handled it
    count = _count_dfrac_in_legends(body)
    if not count:
        return ()
    plural = "" if count == 1 else "s"
    return (
        f"{count} \\dfrac in legend entry text alongside a row sep you set "
        f"explicitly, which was left untouched. If the fraction{plural} still "
        "overlap the next entry the value is too small: 2pt still overlaps "
        "here, 4pt clears a simple \\dfrac and 6pt clears a nested one.",
    )


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------
def _autofix(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    # Retained for the advisory pass below, which must judge what the AUTHOR
    # wrote rather than what this function produced.
    original = body
    applied: list[str] = []
    for step in (
        _restore_literal_newlines,
        # Before the semicolon inserter and colour-agnostic of it: an SVG
        # presentation attribute (stroke-width=, font-size=, ...) is an unknown
        # pgfkey that aborts before any missing semicolon would matter.
        _translate_svg_attributes,
        # After newline restoration so a serialised single-line body is split
        # into statements before the semicolon inserter scans it.
        _insert_missing_semicolons,
        _clamp_trig_arguments,
        _capture_pgfmath_results,
        # After _restore_literal_newlines: a body whose newlines were
        # serialised away must be repaired before its legend spans can be
        # located reliably.
        _rewrite_to_in_legend_entries,
        # Last: this one splices the AXIS option list rather than the legend
        # text, so running it after the \to rewrite keeps the legend spans that
        # step measured valid while it works.
        _reserve_legend_row_sep,
    ):
        body, notes = step(body)
        applied.extend(notes)
    # Advisory checks change nothing, and deliberately run on the ORIGINAL body
    # rather than the rewritten one.  Running them post-rewrite produced a real
    # false positive: _reserve_legend_row_sep injects ``row sep=6pt``, so the
    # row-sep advisory then matched its OWN injection, could not tell it from an
    # author-supplied value, and reported "a row sep you set explicitly" on a
    # body where the author had set nothing.  An advisory describes the input.
    warnings: list[str] = []
    for check in (_warn_row_sep_may_be_insufficient,):
        warnings.extend(check(original))
    return body, tuple(applied), tuple(warnings)


def autofix(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    r"""Apply the safe TikZ recovery rewrites.

    Returns ``(new_body, applied, warnings)``.  Advisory: any internal fault
    degrades to ``(body, (), ())`` so a lint defect can never break an
    otherwise-working render.
    """
    try:
        return _autofix(body)
    except Exception:                      # pragma: no cover - defensive
        logger.exception("tikz lint failed; rendering body unchanged")
        return body, (), ()
