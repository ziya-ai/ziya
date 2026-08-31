"""
Repair for chemfig ``\\charge`` / ``\\Charge`` argument syntax.

WHY A REPAIR RATHER THAN A DOC NOTE
-----------------------------------
chemfig declares (chemfig.tex:2240)::

    \\def\\charge_g#1:#2[#3]=#4,{% #1=angle, #2=offset, #3=tikz code, #4=charge

so the angle is separated from the charge symbol by ``=``, while ``:``
introduces the *optional radial offset*.  Colon-by-analogy is the natural wrong
guess, because chemfig spells BOND angles ``-[:30]`` -- and the resulting error
names the wrong thing entirely::

    ! Argument of \\charge_g has an extra }.

That message points at brace balance and never mentions a separator, so the
author edits braces that were already correct.  Two failure shapes recur, both
reproduced against a live chemfig install:

  1. ``\\charge{90:\\|}{O}``       -- ``:`` used where ``=`` is required.
  2. ``\\charge{90=\\ominus}{O}``  -- the charge argument is NOT math mode, so a
     math symbol dies with "Missing $ inserted", which likewise does not say
     which argument was at fault.

WHY THE MATH WRAP IS CONDITIONAL
--------------------------------
Blanket-wrapping every payload in ``$...$`` was measured to be UNSAFE.
Rendering ``\\charge{90=-}{O}`` bare and math-wrapped produces different bytes:
a text hyphen is not a math minus.  ``\\|``, ``+`` and ``2+`` are byte-identical
either way, but ``-`` is not, so wrapping unconditionally would silently alter
diagrams that already worked.  The wrap therefore fires only when the payload
contains a TeX control word (``\\ominus``, ``\\delta``, ``\\scriptstyle``), which
is exactly the set that cannot survive text mode.  Empirically:

    compiles bare : \\|  +  -  2+  2-  q
    needs math    : \\ominus  \\oplus  \\cdot  \\delta+  \\alpha  \\scriptstyle+

Rewriting a bare-angle form such as ``\\charge{90}`` or ``\\charge{90:2pt}`` is
safe for a reason worth stating: both are ALREADY invalid (verified -- chemfig
requires the ``=``), so the repair cannot turn working input into broken input.

Advisory, never fatal: like the ring lint, any defect here must degrade to
"render the body as written" rather than failing a render that would have
succeeded.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

#: Entry points.  ``\Charge`` (chemfig.tex:2156) shares ``\charge``'s argument
#: grammar, so both need the same repair.
_CHARGE_RE = re.compile(r"\\(charge|Charge)\s*\{")

#: Payload constructs that cannot survive text mode and therefore require the
#: math wrap.  Two shapes, both empirically confirmed to fail bare:
#:
#:   * a TeX control word (``\ominus``, ``\delta``, ``\scriptstyle``) -- the
#:     original trigger; and
#:   * a bare superscript/subscript (``^{2+}``, ``_3``) -- the natural way to
#:     write an ion such as ``Ca^{2+}``.  ``^`` and ``_`` are illegal outside
#:     math mode, so ``\charge{90=^{2+}}{Ca}`` dies with "Missing $ inserted"
#:     -- the same unhelpful message a stray ``\ominus`` produces, again not
#:     naming the charge argument as the culprit.  Wrapping is safe for exactly
#:     the reason the bare-angle rewrite is: a text-mode ``^``/``_`` is ALREADY
#:     invalid, so the repair cannot turn working input into broken input.  The
#:     text-mode payloads that must stay unwrapped (``-`` ``+`` ``2+`` ``2-``
#:     ``q`` ``\|``) contain neither character, so this cannot disturb them.
_NEEDS_MATH_RE = re.compile(r"\\[a-zA-Z@]+|[\^_]")

#: A charge item that is ONLY an angle ("90", "-45", "12.5") -- an angle with
#: the mandatory charge symbol missing.  chemfig aborts fatally on it
#: ("Argument of \charge_g has an extra }"), so it cannot be passed through
#: untouched.  Inventing a charge SYMBOL would guess at chemical intent, so the
#: repair instead supplies the mandatory '=' with an EMPTY symbol ("90" ->
#: "90="): syntax fixed, no glyph fabricated, angle preserved (see _repair_item
#: and test_bare_angle_with_no_symbol_gets_an_empty_symbol).
_BARE_ANGLE_RE = re.compile(r"\s*[-+]?\d+(?:\.\d+)?\s*\Z")

#: Angle supplied when a charge gives a SYMBOL but NO angle
#: (``\charge{\ominus}{O}``).  Here the chemically meaningful part is present
#: and only the mandatory placement angle is absent, so chemfig aborts with the
#: same misleading "Argument of \charge_g has an extra }" this module targets.
#: 90 (north) is chemfig's conventional charge position; the choice is
#: placement-only and so cannot alter chemical meaning, and the input was
#: already invalid, so the rewrite cannot break a working render.
_DEFAULT_CHARGE_ANGLE = "90"


#: A ``\chemfig`` / ``\Chemfig`` (or ``\chemleft``-style) command carrying TWO
#: consecutive optional brackets.  chemfig accepts exactly ONE optional
#: argument, so ``\chemfig[][draw=red]{...}`` -- the natural guess by analogy
#: with macros that take two optionals -- aborts fatally with the misleading
#: "Undefined control sequence \CF_currentstringangle", a message that names
#: nothing the author wrote and points at chemfig internals.  Verified against
#: a live chemfig install: single ``[X]{...}`` renders; ANY second bracket
#: (``[][X]``, ``[X][]``, ``[][]``, ``[X][Y]``) is fatal.
_CHEMFIG_DOUBLE_OPT_RE = re.compile(r"\\(?:chemfig|Chemfig)\s*\[")


def _collapse_double_optional(body: str) -> tuple[str, list[str]]:
    r"""Collapse ``\chemfig[][X]{...}`` -> ``\chemfig[X]{...}`` when safe.

    chemfig takes a SINGLE optional argument; a second ``[...]`` is a fatal
    ``\CF_currentstringangle`` error (verified on a live install).  The
    common author mistake is an EMPTY leading bracket followed by the real
    options -- ``\chemfig[][draw=red]{...}`` -- copied from macros that take
    two optionals.

    The rewrite fires ONLY when at least one of the two brackets is EMPTY, in
    which case the empty one carries no information and dropping it cannot
    change meaning: ``[][X]`` and ``[X][]`` both become ``[X]``, and ``[][]``
    becomes ``[]``.  When BOTH brackets are non-empty (``[X][Y]``) the intent
    is genuinely ambiguous -- which set of options did the author mean? -- so
    the body is left untouched for the renderer to reject with the TeX error,
    exactly as the ring lint leaves ambiguous rings alone.

    Safe for the same reason the other repairs here are: the double-bracket
    form is ALREADY invalid, so collapsing it cannot turn a working render
    into a broken one.  A single ``\chemfig[X]{...}`` (or bracket-free
    ``\chemfig{...}``) has no adjacent second bracket and never matches.
    """
    notes: list[str] = []
    out = body
    # Right-to-left so each edit leaves earlier match indices valid.
    matches = list(_CHEMFIG_DOUBLE_OPT_RE.finditer(body))
    for m in reversed(matches):
        first_open = m.end() - 1
        first_close = _match_bracket(body, first_open)
        if first_close is None:
            continue
        # A second bracket must follow immediately (only whitespace between).
        j = first_close + 1
        while j < len(body) and body[j] in " \t":
            j += 1
        if j >= len(body) or body[j] != "[":
            continue
        second_open = j
        second_close = _match_bracket(body, second_open)
        if second_close is None:
            continue
        first_inner = body[first_open + 1:first_close]
        second_inner = body[second_open + 1:second_close]
        # Only collapse when at least one bracket is empty (unambiguous).
        if first_inner.strip() and second_inner.strip():
            continue
        kept = first_inner if first_inner.strip() else second_inner
        replacement = f"[{kept}]"
        out = out[:first_open] + replacement + out[second_close + 1:]
        notes.append(
            "\\chemfig had two optional [] brackets (chemfig accepts one); "
            f"collapsed the empty one, keeping [{kept}]"
        )
    return out, list(reversed(notes))


def _match_bracket(body: str, open_idx: int) -> Optional[int]:
    """Index of the ``]`` matching the ``[`` at ``open_idx``, or None.

    Nesting-aware so a bracketed value inside the option list (rare, but a
    pgfkeys value can contain ``[...]``) cannot terminate the option early.
    """
    if open_idx >= len(body) or body[open_idx] != "[":
        return None
    depth = 0
    for i in range(open_idx, len(body)):
        if body[i] == "[":
            depth += 1
        elif body[i] == "]":
            depth -= 1
            if depth == 0:
                return i
    return None


def _match_brace(body: str, open_idx: int) -> Optional[int]:
    """Index of the ``}`` matching the ``{`` at ``open_idx``, or None.

    Nesting-aware so a braced payload cannot terminate the argument early.
    """
    if open_idx >= len(body) or body[open_idx] != "{":
        return None
    depth = 0
    for i in range(open_idx, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _top_level_index(spec: str, target: str, *, last: bool = False) -> int:
    """Index of ``target`` at brace/bracket depth 0, or -1.

    Depth tracking matters: a ``[...]`` TikZ option block or a braced payload
    can legitimately contain ``:`` or ``=``, and treating those as the
    separator would split the item in the wrong place.
    """
    depth = 0
    found = -1
    for i, ch in enumerate(spec):
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == target and depth == 0:
            if not last:
                return i
            found = i
    return found


def _split_items(spec: str) -> list[str]:
    """Split a charge spec on top-level commas.

    ``\\charge{90=\\|,180=\\|}{O}`` carries one item per charge position, and
    each is repaired independently.
    """
    items: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in spec:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    return items


def _repair_item(item: str) -> tuple[str, list[str]]:
    """Repair one ``angle[:offset][[tikz]]=symbol`` item."""
    notes: list[str] = []
    if not item.strip():
        return item, notes

    eq = _top_level_index(item, "=")
    if eq >= 0:
        angle, symbol = item[:eq], item[eq + 1:]
    else:
        # No '=' at all.  chemfig requires it, so this item cannot compile as
        # written; promoting the LAST top-level ':' is the repair.  Choosing
        # the last one keeps a genuine offset intact: in "45:2pt:\|" the first
        # colon introduces the offset and only the second stands in for '='.
        colon = _top_level_index(item, ":", last=True)
        if colon < 0:
            # No separator at all.  Two shapes hide here and must be told apart:
            #   * a bare ANGLE ("90") -- inventing a charge symbol would guess
            #     at chemical intent, so leave it (see _BARE_ANGLE_RE); but
            #   * a bare SYMBOL ("\ominus") -- the meaningful part is present
            #     and only the mandatory angle is missing, so chemfig aborts
            #     with "Argument of \charge_g has an extra }" exactly as the
            #     separator-mistake case does.  Supply the default angle.
            if _BARE_ANGLE_RE.match(item):
                # A bare ANGLE with no symbol ("90").  chemfig aborts fatally
                # on this ("Argument of \charge_g has an extra }") -- the SAME
                # error the separator/bare-symbol cases hit -- so passing it
                # through untouched guarantees the render dies.  We must NOT
                # invent a charge symbol (that would guess chemical intent),
                # but we CAN supply the mandatory '=' with an EMPTY symbol: the
                # angle is preserved, no glyph is fabricated, and the molecule
                # renders (the meaningless charge simply draws nothing).  The
                # form is already invalid, so the rewrite cannot turn a working
                # body into a broken one.
                stripped = item.strip()
                notes.append(
                    f"{stripped!r}: angle given with no charge symbol; "
                    f"inserted '=' with an empty symbol (chemfig requires "
                    f"angle=symbol; a bare angle aborts with 'Argument of "
                    f"\\charge_g has an extra }}')"
                )
                return f"{stripped}=", notes
            symbol = item
            notes.append(
                f"{item.strip()!r}: no angle given; inserted default "
                f"'{_DEFAULT_CHARGE_ANGLE}=' (chemfig requires angle=symbol)"
            )
            angle = _DEFAULT_CHARGE_ANGLE
        else:
            angle, symbol = item[:colon], item[colon + 1:]
            notes.append(
                f"{item.strip()!r}: separator ':' -> '=' (chemfig reserves ':' "
                f"for the radial offset)"
            )

    # Math wrap, conditional -- see the module docstring on why blanket
    # wrapping is unsafe.  '$' anywhere means the author already handled it.
    if "$" not in symbol and _NEEDS_MATH_RE.search(symbol):
        notes.append(
            f"{symbol.strip()!r}: wrapped in $...$ (the charge argument is "
            f"not math mode)"
        )
        symbol = f"${symbol}$"

    return f"{angle}={symbol}", notes


def _mask_comments(body: str) -> str:
    r"""Blank out LaTeX ``%`` comment spans, preserving length and newlines.

    TeX discards an unescaped ``%`` through the next newline, so a ``\charge``
    written in a comment (``% or use \charge{0:\oplus}{N} for a cation``) is
    never drawn -- yet ``repair`` scans the raw body with ``_CHARGE_RE`` and
    would rewrite that comment text AND emit false autofix notes for a charge
    that does not exist.  This is the exact blind spot already closed in the
    ring lint (``scan_rings``/``_count_bonds``) and the bond-script bracer
    (``_brace_bare_bond_scripts``); ``repair`` was the last of the four
    scanners that still saw comment text.

    Replacing each comment character (but not the terminating newline) with a
    space removes the macro from ``_CHARGE_RE`` detection while keeping every
    index identical, so the ``(open_idx, close_idx)`` offsets recorded for
    GENUINE occurrences still point into the ORIGINAL body and remain valid for
    editing.  An escaped ``\%`` is a literal percent, not a comment.
    """
    out = list(body)
    i = 0
    n = len(body)
    while i < n:
        if body[i] == "%" and (i == 0 or body[i - 1] != "\\"):
            while i < n and body[i] != "\n":
                out[i] = " "
                i += 1
            continue
        i += 1
    return "".join(out)


def repair(body: str) -> tuple[str, tuple[str, ...]]:
    """Repair every ``\\charge`` argument in ``body``.

    Returns ``(new_body, notes)``.  ``notes`` is empty when nothing changed.
    Rewrites right-to-left so each edit leaves the indices of the
    not-yet-processed occurrences untouched.
    """
    # Detect on comment-free text so a ``\charge`` mentioned only in a LaTeX
    # ``%`` comment (which TeX discards) is never rewritten.  Masking preserves
    # length, so an occurrence's indices still point into the ORIGINAL body,
    # from which the spec is extracted and into which the edit is spliced.
    scan = _mask_comments(body)
    occurrences: list[tuple[int, int, str]] = []   # open_idx, close_idx, spec
    for m in _CHARGE_RE.finditer(scan):
        open_idx = m.end() - 1
        close_idx = _match_brace(scan, open_idx)
        if close_idx is None:
            # Unbalanced braces are a different error; do not guess.
            logger.debug("chemfig charge: unbalanced brace at %d", m.start())
            continue
        occurrences.append((open_idx, close_idx, body[open_idx + 1:close_idx]))

    if not occurrences:
        return body, ()

    notes: list[str] = []
    out = body
    for open_idx, close_idx, spec in reversed(occurrences):
        repaired_items: list[str] = []
        item_notes: list[str] = []
        for item in _split_items(spec):
            fixed, ns = _repair_item(item)
            repaired_items.append(fixed)
            item_notes.extend(ns)
        if not item_notes:
            continue
        out = out[:open_idx + 1] + ",".join(repaired_items) + out[close_idx:]
        notes.extend(item_notes)

    return out, tuple(reversed(notes))


#: chemfig bond characters that are made ACTIVE inside ``\chemfig{...}``.
#: A bare superscript/subscript whose argument is one of these (``O^-``) makes
#: TeX's ``^``/``_`` primitive grab an active token and abort with "Missing {
#: inserted" -- see ``_brace_bare_bond_scripts``.  Kept in sync with the bond
#: set the ring lint recognises.
_BOND_SCRIPT_CHARS = "-=~<>"

#: mhchem entry points whose braced argument is NOT chemfig territory.  The
#: bond characters ``- = ~ < >`` are made active only inside ``\chemfig{...}``;
#: inside ``\ce{}`` / ``\pu{}`` they are ordinary mhchem syntax, and ``^`` /
#: ``_`` are mhchem's own charge/stoichiometry markup.  A mixed body such as
#: ``\ce{Cl^- + Na^+ -> NaCl}\chemfig{...}`` therefore contains a perfectly
#: legal ``Cl^-`` that must NOT be braced -- doing so both mangles the mhchem
#: output and emits a false "cannot be a superscript argument inside \chemfig"
#: note about a construct that was never in \chemfig at all.
_MHCHEM_RE = re.compile(r"\\(?:ce|pu)\s*\{")


def _brace_bare_bond_scripts(body: str) -> tuple[str, list[str]]:
    r"""Brace a bare ``^``/``_`` whose argument is an active chemfig bond char.

    Inside ``\chemfig`` the bond characters ``- = ~ < >`` are active, so a bare
    ``^-`` -- the natural way to write an anion such as ``O^-`` -- makes TeX's
    superscript primitive try to consume an active token and abort fatally with
    "Missing { inserted", a message that names nothing the author wrote.  The
    braced form ``O^{-}`` compiles and renders the intended charge (verified
    against a live chemfig install), so wrap the single bond character in
    braces.

    Safe for the same reason the charge repairs are: a bare ``^``/``_`` before a
    bond character is ALREADY invalid, so the rewrite cannot turn working input
    into broken input.  ``^+`` (ordinary ``+``), ``^1`` and ``^a`` all compile
    bare and are left untouched, as is an already-braced ``^{...}`` and anything
    inside a ``$...$`` math span (where the character is legal).  An escaped
    ``\^`` / ``\_`` is a text accent / literal underscore, not a script, so a
    preceding backslash suppresses the rewrite.

    Running before the ring lint additionally cures a latent miscount: the bare
    ``-`` in ``O^-`` would otherwise be tallied as a ring bond, whereas the
    braced ``{-}`` is skipped by the counter's brace-skip.

    A LaTeX ``%`` comment runs to end-of-line and TeX discards it, so a
    ``^-`` written inside a comment (``% an anion label like O^- ...``) is not
    a script at all and must NOT be braced -- doing so emits a spurious autofix
    note on a correct render, and reciprocally a comment could hide a real
    occurrence.  An unescaped ``%`` therefore switches to a copy-through state
    until the next newline, mirroring the ``$`` math-span skip.  An escaped
    ``\%`` is a literal percent, not a comment.

    Likewise a ``\ce{...}`` / ``\pu{...}`` mhchem span is NOT chemfig: the bond
    characters are not active there and ``^`` / ``_`` are mhchem's own charge
    markup, so ``\ce{Cl^- + Na^+ -> NaCl}`` contains a legitimate ``Cl^-`` that
    must be copied through untouched.  Bracing it both mangled the equation and
    emitted a false "cannot be a superscript argument inside \chemfig" note for
    a construct never inside ``\chemfig`` at all.  The mhchem argument is copied
    through verbatim, mirroring the comment and math-span skips.
    """
    notes: list[str] = []
    out: list[str] = []
    i = 0
    n = len(body)
    in_math = False
    while i < n:
        ch = body[i]
        if ch == "$":
            in_math = not in_math
            out.append(ch)
            i += 1
            continue
        if ch == "%" and (i == 0 or body[i - 1] != "\\"):
            # Copy the comment through verbatim to end-of-line; no bracing.
            while i < n and body[i] != "\n":
                out.append(body[i])
                i += 1
            continue
        # A \ce{...}/\pu{...} mhchem span is not chemfig; its ^/_ are legal
        # mhchem markup, so copy the whole call (macro + braced argument)
        # through verbatim.  _match_brace is nesting-aware, so a braced
        # sub-group inside the equation cannot end the span early.
        mh = _MHCHEM_RE.match(body, i)
        if mh:
            open_idx = mh.end() - 1
            close_idx = _match_brace(body, open_idx)
            if close_idx is not None:
                out.append(body[i:close_idx + 1])
                i = close_idx + 1
                continue
            # Unbalanced braces: fall through and treat as ordinary text
            # rather than guessing where the span ends.
        if (
            not in_math
            and ch in "^_"
            and (i == 0 or body[i - 1] != "\\")
            # A ``^``/``_`` that FOLLOWS a bond char is not an atom script at
            # all: chemfig spells the double-bond side-placement modifier
            # ``=^`` / ``=_`` (force the second stroke above/below), and the
            # single char after it is the NEXT bond, not a script argument.
            # Bracing it (``=^{-}``) absorbs that bond into a phantom label,
            # opens the ring and draws a different molecule -- verified: a
            # benzene ``*6(-=^-=^-=^-)`` collapsed to a 4-bond open chain.  The
            # genuine anion target (``O^-``) always has an ATOM before the
            # ``^``, never a bond, so this guard cannot suppress a real fix.
            and (i == 0 or body[i - 1] not in _BOND_SCRIPT_CHARS)
            and i + 1 < n
            and body[i + 1] in _BOND_SCRIPT_CHARS
        ):
            sign = body[i + 1]
            out.append(ch)
            out.append("{")
            out.append(sign)
            out.append("}")
            notes.append(
                f"{ch}{sign}: braced as {ch}{{{sign}}} (a bare bond character "
                f"cannot be a {'super' if ch == '^' else 'sub'}script argument "
                f"inside \\chemfig)"
            )
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out), notes


def autofix(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    r"""``(new_body, applied, warnings)``, matching ``chemfig_lint.autofix``.

    Every repair here is unambiguous, so nothing lands in ``warnings``; the
    triple exists so the renderer can treat both chemfig fixers alike.

    Runs the double-optional-bracket collapse first (``\chemfig[][X]{...}``
    -> ``\chemfig[X]{...}``), then the ``\charge`` argument repair, then braces
    any bare ``^``/``_`` bond-character script (``O^-`` -> ``O^{-}``).  The
    order is deliberate: the bracket collapse is a whole-command fix that must
    run before the argument-level repairs, and the charge repair may emit
    ``$...$`` spans that the bond-script pass must see so it does not re-touch a
    payload already handled.
    """
    fixed, bracket_notes = _collapse_double_optional(body)
    fixed, notes = repair(fixed)
    fixed, script_notes = _brace_bare_bond_scripts(fixed)
    return fixed, tuple(bracket_notes) + tuple(notes) + tuple(script_notes), ()
