"""
Stable-identifier detection for memory content and search queries.

A "stable identifier" is a token that names one thing unambiguously and
does not paraphrase: a UUID, a Kuiper terminal id (``ti-…``), a ticket or
review key (``CR-314341848``, ``NETREL-893``), or a long hex string.  Two
memory subsystems key on these:

* ``MemoryStorage.search`` pins memories that contain an identifier from
  the query ahead of rank fusion.  The keyword path already scores such a
  token highly (rare, so high IDF), but Reciprocal Rank Fusion can still
  let a semantic neighbour outrank it, which is how a query for a literal
  terminal id returned an unrelated memory first (2026-10-27).

* ``lifecycle._evaluate_promotion`` lets an explicitly proposed fact that
  names an identifier promote without corroboration (the "identifier fast
  track").  Such a fact is taught once and never independently re-derived,
  so the corroboration bar can never be met; it is also cheap to
  contradict, which is the property that makes skipping corroboration safe.

Deliberately narrow.  Hostnames, region names and version strings also mix
letters and digits but paraphrase freely and recur in unrelated contexts,
so they are not identifiers here.
"""
from __future__ import annotations

import re
from typing import List

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Kuiper terminal / device ids: ti- followed by a hex run.  The live fleet
# uses 17 hex chars; accept 12+ so shortened forms still count.
_TIID = r"ti-[0-9a-f]{12,}"
# Ticket, review and issue keys: an upper-case prefix, a dash, digits.
# Matched case-sensitively so prose like "co-2" does not qualify.
_KEY = r"\b[A-Z][A-Z0-9]{1,15}-\d{2,}\b"
# Long hex run with at least one digit and one letter (a pure number is an
# amount, a pure word is a word).  12+ chars so short hashes like a 7-char
# git abbreviation do not pin unrelated memories.
_HEX = r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b"

_CI = re.compile(f"({_UUID})|({_TIID})|({_HEX})", re.IGNORECASE)
_CS = re.compile(_KEY)

# Phrases a proposal uses to mark itself as a guess.  A fact the model
# itself hedges must not skip corroboration: the 2026-10-27 proposal
# "…is very likely ti-c6e49a8d711f4b326 (cell 775474) … dcohn has not
# confirmed it" named a real identifier and a wrong cell.
_HEDGE = re.compile(
    r"\b(very likely|likely|probably|possibly|presumably|apparently|"
    r"not confirmed|unconfirmed|has not confirmed|unverified|uncertain|"
    r"inferred|my guess|guess(?:ed|ing)?|tentative(?:ly)?|appears to be|"
    r"seems to be|may be|might be)\b",
    re.IGNORECASE,
)


def extract_identifiers(text: str) -> List[str]:
    """Return the stable identifiers in ``text``, lower-cased, in order of
    first appearance, without duplicates.

    A UUID is reported once (not again as its hex segments); the
    alternation is ordered so the longer pattern wins at each position.
    """
    if not text:
        return []
    found: List[str] = []
    seen = set()
    for m in _CI.finditer(text):
        tok = m.group(0).lower()
        if tok not in seen:
            seen.add(tok)
            found.append(tok)
    for m in _CS.finditer(text):
        tok = m.group(0).lower()
        if tok not in seen:
            seen.add(tok)
            found.append(tok)
    return found


def contains_identifier(text: str) -> bool:
    return bool(extract_identifiers(text))


def is_hedged(text: str) -> bool:
    """True if the text marks itself as uncertain (see ``_HEDGE``)."""
    return bool(text) and _HEDGE.search(text) is not None
