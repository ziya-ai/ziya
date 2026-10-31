"""
Tolerant JSON loading for hand-edited config files.

Python's ``json`` follows RFC 8259: only U+0020, TAB, LF and CR count as
whitespace between tokens.  Text copied out of a browser, chat client or word
processor often carries U+00A0 NO-BREAK SPACE (and relatives) in its
indentation, which is invisible in an editor and makes ``json.loads`` fail
with a misleading "Expecting property name enclosed in double quotes".

``loads_tolerant`` first tries a strict parse.  Only if that fails does it
normalize characters *between tokens*: any ``str.isspace()`` character becomes
U+0020 and zero-width U+200B / U+FEFF are deleted.  String literals are never
touched, so a value that legitimately contains a no-break space round-trips
unchanged.  The caller gets the list of changes so it can warn; the file on
disk is not rewritten.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_JSON_WHITESPACE = frozenset(" \t\n\r")
_ZERO_WIDTH = frozenset("\u200b\ufeff")


@dataclass(frozen=True)
class NormalizedChar:
    char: str
    line: int  # 1-based
    col: int   # 1-based, in the original text


def char_label(c: str) -> str:
    """'U+00A0 NO-BREAK SPACE' for a character."""
    return f"U+{ord(c):04X} {unicodedata.name(c, 'UNNAMED')}"


def _is_suspect(c: str) -> bool:
    return c in _ZERO_WIDTH or (c.isspace() and c not in _JSON_WHITESPACE)


def normalize_json_whitespace(text: str) -> Tuple[str, List[NormalizedChar]]:
    """Replace non-JSON whitespace between tokens; leave string literals alone."""
    out: List[str] = []
    changes: List[NormalizedChar] = []
    in_string = False
    escaped = False
    line, col = 1, 0
    for c in text:
        col += 1
        if in_string:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
            out.append(c)
        elif c in _ZERO_WIDTH:
            changes.append(NormalizedChar(c, line, col))
        elif c.isspace() and c not in _JSON_WHITESPACE:
            changes.append(NormalizedChar(c, line, col))
            out.append(" ")
        else:
            out.append(c)
        if c == "\n":
            line, col = line + 1, 0
    return "".join(out), changes


def summarize_changes(changes: List[NormalizedChar]) -> str:
    """'normalized 26 U+00A0 NO-BREAK SPACE on lines 57-66; ...'"""
    by_char: Dict[str, List[int]] = {}
    for ch in changes:
        by_char.setdefault(ch.char, []).append(ch.line)
    parts = []
    for c, lines in by_char.items():
        lo, hi = min(lines), max(lines)
        where = f"line {lo}" if lo == hi else f"lines {lo}-{hi}"
        verb = "removed" if c in _ZERO_WIDTH else "normalized"
        parts.append(f"{verb} {len(lines)} {char_label(c)} on {where}")
    return "; ".join(parts)


def whitespace_hint(text: str, err: json.JSONDecodeError) -> Optional[str]:
    """Name a non-ASCII whitespace / zero-width character at or next to the error."""
    pos = getattr(err, "pos", None)
    if pos is None:
        return None
    for p in (pos, pos - 1, pos + 1):
        if 0 <= p < len(text) and _is_suspect(text[p]):
            line = text.count("\n", 0, p) + 1
            col = p - (text.rfind("\n", 0, p) + 1) + 1
            what = "not a space" if text[p].isspace() else "an invisible character"
            return f"line {line} col {col} is {char_label(text[p])}, {what}"
    return None


@dataclass
class TolerantLoad:
    data: Any
    changes: List[NormalizedChar] = field(default_factory=list)

    @property
    def warning(self) -> Optional[str]:
        return summarize_changes(self.changes) if self.changes else None


def loads_tolerant(text: str) -> TolerantLoad:
    """json.loads, retried once with inter-token Unicode whitespace normalized.

    Raises the ORIGINAL JSONDecodeError when the normalized text still fails,
    so positions refer to the file as the user sees it.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        return TolerantLoad(json.loads(text))
    except json.JSONDecodeError as original:
        fixed, changes = normalize_json_whitespace(text)
        if not changes:
            raise
        try:
            data = json.loads(fixed)
        except json.JSONDecodeError:
            raise original from None
        return TolerantLoad(data, changes)
