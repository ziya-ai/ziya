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

#: Spans skipped while counting a ring's own bonds.  Each can contain a bond
#: character that is not a ring bond.
_SKIP_PAIRS = {"(": ")", "[": "]", "{": "}"}

STANDALONE = "standalone"
FUSED = "fused"
PENDANT = "pendant"


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
