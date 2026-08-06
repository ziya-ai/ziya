"""
Structural lint for chemfig ring specifications.

Why this exists: an under-specified ring still COMPILES.  chemfig draws the
bonds it was given and simply does not close the ring, so every layer of the
pipeline reports success and the user receives a picture of a *different
molecule*.  There is no compile error, no missing package and nothing in the
TeX log -- the only signal is visual.  That makes it precisely the class of bug
a lint has to catch, because nothing downstream can.

The rule, established empirically against a real chemfig install:

    standalone ring    ``*n(...)``      needs n    top-level bonds
    fused ring         ``*n(...)``      needs n-1  top-level bonds
    pendant ring       ``(-*n(...))``   needs n    top-level bonds

A fused ring gets its closing edge from the ring it is nested in, so it must
supply one bond fewer.  The fused/pendant distinction is load-bearing rather
than pedantic, and is the part an autofixer must get right: both forms are
*nested inside another ring's parentheses*, but a pendant ring (the second ring
of a biphenyl, say) shares no edge and therefore needs the full n.  Classifying
it as fused would silently open a ring that was correct to begin with.

Branch contents, bond options and brace groups are skipped when counting, since
each can legitimately contain a bond character that is not a ring bond:
``(-OH)`` is a substituent, ``-[:-30]`` carries a negative angle, and
``SO_{4}^{2-}`` ends in a minus sign.  Counting raw characters instead
overcounts all three.

Autofix is deliberately narrow.  Appending the missing bond just inside the
ring's closing paren is *positionally* safe -- verified on rings carrying
substituents, which keep their relative placement (a para pair stays para) --
but the bond ORDER of the added bond is genuinely ambiguous, and guessing wrong
trades a visibly broken ring for a plausible-looking wrong structure, which is
worse.  So the fix is applied only where the intent is unambiguous: an even
ring whose bonds strictly alternate is a Kekule aromatic ring with exactly one
continuation.  Everything else is reported and left alone.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

#: chemfig bond tokens.  ``-`` single, ``=`` double, ``~`` triple, and the Cram
#: wedge/dash pair ``>`` / ``<``.  Modifiers that may follow a bond (``:``,
#: ``|``) are not bonds themselves and are not listed.
_BOND_CHARS = frozenset("-=~><")

#: Ring openers: ``*6(`` and the aromatic-circle form ``**6(``.
_RING_RE = re.compile(r"\*\*?(\d+)\(")

#: mhchem entry points whose braced argument is NOT chemfig territory.  Inside
#: ``\ce{}`` / ``\pu{}`` the ``*`` is mhchem's adduct/hydrate operator, so a
#: crystal-water formula such as ``\ce{CuSO4*5(H2O)}`` (CuSO4.5H2O) contains a
#: ``*5(...)`` that is NOT a chemfig ring -- yet ``_RING_RE`` matches it and the
#: lint reports a bogus "*5 ring will not close" on a formula that renders
#: perfectly.  The sibling charge module gained the same awareness in an
#: earlier pass (see ``chemfig_charge._MHCHEM_RE``); the ring lint was the
#: remaining scanner still blind to mhchem spans.
_MHCHEM_RE = re.compile(r"\\(?:ce|pu)\s*\{")

#: Spans skipped while counting a ring's own bonds.  Each can contain a bond
#: character that is not a ring bond.
_SKIP_PAIRS = {"(": ")", "[": "]", "{": "}"}

STANDALONE = "standalone"
FUSED = "fused"
PENDANT = "pendant"


def _mask_comments(body: str) -> str:
    r"""Blank out LaTeX comment spans, preserving length and newlines.

    TeX discards an unescaped ``%`` through the next newline, so a ``*n(...)``
    written in a comment (``% cf. *5(-=-=) cyclopentadiene``) is not a ring at
    all -- yet ``scan_rings`` matches on the raw body and would report it.
    Replacing each comment character (but not the terminating newline) with a
    space removes the text from every scan while keeping every index identical,
    so the ``Ring`` offsets remain valid for ``autofix`` insertion into the
    ORIGINAL body.  An escaped ``\%`` is a literal percent, not a comment.

    This complements the comment skip already in ``_count_bonds``: that guards
    the bond TALLY, this guards ring DETECTION and classification.
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


def _mask_mhchem(body: str) -> str:
    r"""Blank out ``\ce{...}`` / ``\pu{...}`` spans, preserving length.

    Inside an mhchem argument the ``*`` is the adduct/hydrate operator, so a
    crystal-water formula ``\ce{CuSO4*5(H2O)}`` contains a ``*5(...)`` that is
    NOT a chemfig ring.  Blanking the whole span (macro name, braces and body)
    with spaces keeps every index identical -- so the offsets stored on each
    ``Ring`` still point into the ORIGINAL body and remain valid for ``autofix``
    insertion -- while removing the phantom ring from detection.  A genuine
    ``\chemfig{...}`` ring elsewhere in the same body is untouched.

    Mirrors ``chemfig_charge._brace_bare_bond_scripts``'s mhchem skip, which
    copies ``\ce``/``\pu`` spans through verbatim for the same reason.  The
    brace match is nesting-aware; an unbalanced ``\ce{`` is left as-is (masked
    only from the ``\`` onward is avoided) so malformed input is never guessed
    at.  An escaped ``\\ce`` (backslash-backslash then ce) is not an mhchem
    call and ``_MHCHEM_RE`` will not match it as one because the regex anchors
    on the single control word ``\ce``.
    """
    out = list(body)
    for m in _MHCHEM_RE.finditer(body):
        open_idx = m.end() - 1
        close_idx = _match(body, open_idx)
        if close_idx is None:            # unbalanced -- do not guess a span
            continue
        for i in range(m.start(), close_idx + 1):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


@dataclass(frozen=True)
class Ring:
    """One ``*n(...)`` ring found in a body.

    Attributes:
        size:         the ``n`` in ``*n(``, i.e. the number of ring vertices.
        bonds:        top-level ring bonds actually written.
        expected:     bonds required to close, given ``kind``.
        kind:         standalone | fused | pendant.
        star:         index of the leading ``*``.
        inner_open:   index of the ring's ``(``.
        inner_close:  index of the matching ``)``.
        pattern:      the bond tokens, e.g. ``"-=-=-"``.
    """
    size: int
    bonds: int
    expected: int
    kind: str
    star: int
    inner_open: int
    inner_close: int
    pattern: str

    @property
    def deficit(self) -> int:
        """Bonds missing.  Negative when the ring is over-specified."""
        return self.expected - self.bonds

    @property
    def is_closed(self) -> bool:
        return self.deficit == 0

    def describe(self) -> str:
        """A one-line, actionable summary naming the count and the cause."""
        where = {
            FUSED: "fused ring (shares an edge, so needs size-1 bonds)",
            PENDANT: "pendant ring (attached via a branch, needs all size bonds)",
            STANDALONE: "standalone ring",
        }[self.kind]
        if self.deficit > 0:
            return (
                f"*{self.size} {where}: found {self.bonds} bond(s) "
                f"{self.pattern!r}, needs {self.expected}. The ring will not "
                f"close and the rendered structure will be wrong."
            )
        return (
            f"*{self.size} {where}: found {self.bonds} bond(s) "
            f"{self.pattern!r}, needs {self.expected}. The extra bond(s) "
            f"overshoot the ring."
        )


def _match(body: str, open_idx: int) -> Optional[int]:
    """Index of the delimiter matching the one at ``open_idx``, or None.

    Nesting-aware, so a branch inside a branch does not terminate the outer one.
    """
    opener = body[open_idx]
    closer = _SKIP_PAIRS.get(opener)
    if closer is None:
        return None
    depth = 0
    for i in range(open_idx, len(body)):
        ch = body[i]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


def _count_bonds(body: str, inner_open: int, inner_close: int) -> tuple[int, str]:
    """Count a ring's own bonds, ignoring anything nested inside it.

    Skips branches, bond options and brace groups wholesale: each can contain a
    bond character that is not a ring bond, and a naive character count
    therefore reports a ring as complete when it is short (or vice versa).
    """
    tokens: list[str] = []
    i = inner_open + 1
    while i < inner_close:
        ch = body[i]
        # A LaTeX comment runs from an unescaped '%' to end of line, and TeX
        # discards it entirely -- so any bond character inside it is not a ring
        # bond.  Counting the comment text was wrong in both directions: an
        # explanatory "% double = bond note --" after a CORRECT ring produced a
        # false overshoot warning, and comment bond chars could equally MASK a
        # genuine deficit.  An escaped "\%" is a literal percent, not a comment.
        if ch == "%" and (i == inner_open + 1 or body[i - 1] != "\\"):
            nl = body.find("\n", i)
            i = inner_close if nl == -1 or nl >= inner_close else nl + 1
            continue
        if ch in _SKIP_PAIRS:
            end = _match(body, i)
            # An unbalanced delimiter means malformed input; stop rather than
            # guessing, so the lint never invents a finding from broken syntax.
            if end is None or end > inner_close:
                break
            i = end + 1
            continue
        if ch in _BOND_CHARS:
            tokens.append(ch)
        i += 1
    return len(tokens), "".join(tokens)


def _depth_between(body: str, start: int, stop: int) -> int:
    """Net branch depth accumulated over ``body[start:stop]``.

    Used to tell a fused ring (sitting at depth 0 of its parent, so sharing an
    edge) from a pendant one (nested inside a branch, so sharing nothing).
    Bracket and brace spans are skipped so an option like ``[:30]`` cannot be
    mistaken for a branch.
    """
    depth = 0
    i = start
    while i < stop:
        ch = body[i]
        if ch in "[{":
            end = _match(body, i)
            if end is None or end >= stop:
                break
            i = end + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    return depth


def scan_rings(body: str) -> tuple[Ring, ...]:
    """Find every ``*n(...)`` ring, with its bond count and classification."""
    # Scan comment-free text so a ``*n(...)`` mentioned in a LaTeX ``%``
    # comment is never mistaken for a real ring, and mask ``\ce``/``\pu``
    # mhchem spans so an adduct/hydrate ``*n(...)`` (e.g. ``\ce{CuSO4*5(H2O)}``)
    # is not either.  Both masks preserve length, so the indices stored on each
    # ``Ring`` still point into the ORIGINAL body and remain valid for
    # ``autofix`` insertion.
    body = _mask_mhchem(_mask_comments(body))
    raw: list[tuple[int, int, int, int]] = []   # size, star, open, close
    for m in _RING_RE.finditer(body):
        inner_open = m.end() - 1
        inner_close = _match(body, inner_open)
        if inner_close is None:
            logger.debug("chemfig lint: unbalanced ring at %d, skipping", m.start())
            continue
        raw.append((int(m.group(1)), m.start(), inner_open, inner_close))

    rings: list[Ring] = []
    for size, star, inner_open, inner_close in raw:
        # Innermost enclosing ring, if any -- that is this ring's parent.
        parent = None
        for p_size, p_star, p_open, p_close in raw:
            if p_star == star:
                continue
            if p_open < star and inner_close < p_close:
                if parent is None or p_open > parent[1]:
                    parent = (p_size, p_open, p_close)

        if parent is None:
            kind = STANDALONE
        else:
            # Fused when the ring sits directly on its parent's bond chain;
            # pendant when it hangs off a branch.
            kind = FUSED if _depth_between(body, parent[1] + 1, star) == 0 else PENDANT

        expected = size - 1 if kind == FUSED else size
        bonds, pattern = _count_bonds(body, inner_open, inner_close)
        rings.append(Ring(
            size=size, bonds=bonds, expected=expected, kind=kind,
            star=star, inner_open=inner_open, inner_close=inner_close,
            pattern=pattern,
        ))
    return tuple(rings)


def lint(body: str) -> tuple[Ring, ...]:
    """Rings whose bond count cannot close them, in document order."""
    return tuple(r for r in scan_rings(body) if not r.is_closed)


def _alternating_continuation(pattern: str, size: int, deficit: int) -> Optional[str]:
    """The unambiguous next bond for a Kekule ring, or None if ambiguous.

    Only an even ring whose bonds strictly alternate has exactly one sensible
    continuation.  An odd ring does not: a fused five-ring short one bond could
    be closed with either order, and picking the alternating one asserts a
    double bond that is often wrong (indole's pyrrole ring wants ``-=--``, not
    ``-=-=``).  Refusing there is what keeps the fix honest.
    """
    if deficit != 1 or size % 2 != 0 or not pattern:
        return None
    if any(c not in "-=" for c in pattern):
        return None
    if any(a == b for a, b in zip(pattern, pattern[1:])):
        return None
    return "-" if pattern[-1] == "=" else "="


def autofix(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Close unambiguous rings; report the rest.

    Returns ``(new_body, applied, warnings)``.  Fixes are applied right-to-left
    so that each insertion leaves the indices of the not-yet-processed rings
    untouched.
    """
    findings = lint(body)
    if not findings:
        return body, (), ()

    applied: list[str] = []
    warnings: list[str] = []
    out = body

    for ring in sorted(findings, key=lambda r: r.star, reverse=True):
        bond = _alternating_continuation(ring.pattern, ring.size, ring.deficit)
        if bond is None:
            warnings.append(ring.describe())
            continue
        out = out[:ring.inner_close] + bond + out[ring.inner_close:]
        applied.append(
            f"*{ring.size} {ring.kind} ring was {ring.bonds}/{ring.expected} "
            f"bonds ({ring.pattern!r}); appended {bond!r} to close it as an "
            f"alternating aromatic ring."
        )

    return out, tuple(reversed(applied)), tuple(reversed(warnings))
